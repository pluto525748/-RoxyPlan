"""Bounded structural repair for model-produced tool calls.

The repairer receives precise validation errors and the target tool's
unique schema.  It can only fix JSON structure — it must not:
- change the intent or mode
- select a different tool
- add or remove tool calls
- resolve business ambiguity (time, duration, attribution)
- silently drop fields that carry potential business semantics
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from modules.schema_validator import SchemaValidator, ValidationError, ValidationResult
from modules.semantic_action_parser import SemanticDecision, SemanticToolCall
from modules.tool_registry import ToolRegistry


# Errors that can be safely fixed without a model call.
_PROGRAMMATIC_REPAIRABLE = {
    "unexpected_parameter",   # Drop only safe_ignorable_fields.
    "invalid_parameter_enum", # Resolve via enum_aliases.
}


# Errors that are eligible for model repair (structural, not semantic).
_MODEL_REPAIRABLE = {
    "missing_parameter",
    "invalid_parameter_type",
    "invalid_parameter_enum",
    "parameter_out_of_range",
}
# Note: unexpected_parameter is intentionally NOT in _MODEL_REPAIRABLE.
# The model must never be asked to guess where an unknown field belongs.


# Errors that must NEVER be repaired — they indicate semantic ambiguity.
_UNREPAIRABLE = {
    "tool_not_found",
    "tool_not_enabled",
    "tool_not_model_visible",
}


@dataclass
class RepairResult:
    decision: SemanticDecision
    repaired: bool
    repair_method: str = ""  # "programmatic", "model", or ""
    repair_diagnostic: str = ""
    errors_before: List[ValidationError] = field(default_factory=list)
    errors_after: List[ValidationError] = field(default_factory=list)


class StructureRepairer:
    """Attempts at most one model call to fix structural JSON issues.

    Programmatic repair (safe, deterministic) is always attempted first.
    Model repair only runs if programmatic repair is insufficient and the
    errors are purely structural.

    INVARIANTS (checked on every repair path):
    - intent is never changed
    - tool names are never changed
    - tool count is never changed
    - authorization scope is never widened
    - explicit business values are never altered
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        llm_callable: Optional[Callable[[str], str]] = None,
        require_model_visible: bool = True,
    ) -> None:
        self._registry = registry
        self._validator = SchemaValidator(
            registry,
            require_model_visible=require_model_visible,
        )
        self._llm = llm_callable

    def repair(
        self,
        decision: SemanticDecision,
        validation_result: ValidationResult,
    ) -> RepairResult:
        """Attempt repair.  Returns the (possibly) repaired decision.

        The caller MUST re-validate after this call.
        """
        if validation_result.valid:
            return RepairResult(
                decision=decision,
                repaired=False,
                repair_method="",
                repair_diagnostic="already_valid",
            )

        errors = validation_result.errors

        # -- Guard: unrecoverable errors -----------------------------------
        fatal = [e for e in errors if e.code in _UNREPAIRABLE]
        if fatal:
            return RepairResult(
                decision=decision,
                repaired=False,
                repair_method="",
                repair_diagnostic=(
                    f"unrepairable: {fatal[0].code} on "
                    f"{fatal[0].tool_name!r}"
                ),
                errors_before=errors,
                errors_after=errors,
            )

        # -- Guard: unknown business fields must not be silently dropped ----
        business_fields = self._find_business_unknown_fields(errors, decision)
        if business_fields:
            return RepairResult(
                decision=decision,
                repaired=False,
                repair_method="",
                repair_diagnostic=(
                    f"unrepairable_business_fields: {business_fields}"
                ),
                errors_before=errors,
                errors_after=errors,
            )

        # -- Step 1: programmatic repair -----------------------------------
        prog_result = self._programmatic_repair(decision, errors, validation_result)
        prog_validation = self._validator.validate(prog_result)
        if prog_validation.valid:
            return RepairResult(
                decision=prog_result,
                repaired=True,
                repair_method="programmatic",
                repair_diagnostic="programmatic_repair_succeeded",
                errors_before=errors,
                errors_after=[],
            )

        # -- Step 2: model repair (at most once) ---------------------------
        remaining = prog_validation.errors
        if not self._can_model_repair(remaining):
            return RepairResult(
                decision=decision,
                repaired=False,
                repair_method="",
                repair_diagnostic=(
                    f"model_repair_refused: "
                    f"{remaining[0].code if remaining else 'unknown'}"
                ),
                errors_before=errors,
                errors_after=remaining,
            )

        model_result = self._model_repair(
            decision, remaining, prog_validation.tool_schemas
        )
        # -- Boundary enforcement ------------------------------------------
        if not self._respects_boundaries(decision, model_result):
            return RepairResult(
                decision=decision,
                repaired=False,
                repair_method="model_rejected",
                repair_diagnostic="model_repair_violated_boundaries",
                errors_before=errors,
                errors_after=remaining,
            )

        model_validation = self._validator.validate(model_result)
        return RepairResult(
            decision=model_result,
            repaired=model_validation.valid,
            repair_method="model" if model_validation.valid else "model_failed",
            repair_diagnostic=(
                "model_repair_succeeded"
                if model_validation.valid
                else "model_repair_failed"
            ),
            errors_before=errors,
            errors_after=model_validation.errors,
        )

    # ------------------------------------------------------------------
    # Guard: business fields detection
    # ------------------------------------------------------------------

    def _find_business_unknown_fields(
        self,
        errors: List[ValidationError],
        decision: SemanticDecision,
    ) -> List[str]:
        """Return unknown field names that are NOT in safe_ignorable_fields.

        These fields carry potential business semantics and must trigger
        CLARIFY or REJECT, never silent drop.
        """
        business: List[str] = []
        for err in errors:
            if err.code != "unexpected_parameter":
                continue
            if not err.field_name:
                continue
            if err.tool_index >= len(decision.tool_calls):
                continue
            tool_name = decision.tool_calls[err.tool_index].name
            safe = set(self._registry.safe_ignorable_fields_for(tool_name))
            if err.field_name not in safe:
                business.append(
                    f"{tool_name}.{err.field_name}"
                )
        return business

    # ------------------------------------------------------------------
    # Programmatic repair
    # ------------------------------------------------------------------

    def _programmatic_repair(
        self,
        decision: SemanticDecision,
        errors: List[ValidationError],
        validation_result: ValidationResult,
    ) -> SemanticDecision:
        """Apply safe, deterministic fixes.

        Only two operations are allowed:
        1. Drop fields listed in the tool's safe_ignorable_fields.
        2. Resolve enum values via the tool's enum_aliases.
        """
        calls = list(decision.tool_calls)

        # Collect safe-to-drop fields per tool index.
        safe_drops: Dict[int, set] = {}
        for err in errors:
            if err.code != "unexpected_parameter" or not err.field_name:
                continue
            safe = set(
                self._registry.safe_ignorable_fields_for(
                    calls[err.tool_index].name if err.tool_index < len(calls) else ""
                )
            )
            if err.field_name in safe:
                safe_drops.setdefault(err.tool_index, set()).add(err.field_name)

        # Apply.
        repaired_calls = []
        for index, call in enumerate(calls):
            args = dict(call.arguments)
            # 1. Drop safe_ignorable_fields only.
            if index in safe_drops:
                args = {
                    k: v for k, v in args.items()
                    if k not in safe_drops[index]
                }
            # 2. Resolve enum aliases.
            args = self._apply_enum_aliases(call.name, args)
            repaired_calls.append(SemanticToolCall(name=call.name, arguments=args))

        return SemanticDecision(
            mode=decision.mode,
            intent=decision.intent,
            tool_calls=repaired_calls,
            confidence=decision.confidence,
            follow_up_target=decision.follow_up_target,
            reply_hint=decision.reply_hint,
            subject=decision.subject,
            polarity=decision.polarity,
            modality=decision.modality,
        )

    def _apply_enum_aliases(
        self,
        tool_name: str,
        arguments: Dict[str, object],
    ) -> Dict[str, object]:
        """Resolve enum value aliases using the registry's deterministic map."""
        enum_aliases = self._registry.enum_aliases_for(tool_name)
        if not enum_aliases:
            return arguments

        result = dict(arguments)
        for param_name, alias_map in enum_aliases.items():
            if param_name in result:
                value = str(result[param_name])
                if value in alias_map:
                    canonical = alias_map[value]
                    print(
                        f"[StructureRepair] enum_alias "
                        f"tool={tool_name!r} param={param_name!r} "
                        f"{value!r} -> {canonical!r}",
                        flush=True,
                    )
                    result[param_name] = canonical
        return result

    # ------------------------------------------------------------------
    # Model repair
    # ------------------------------------------------------------------

    def _can_model_repair(self, errors: List[ValidationError]) -> bool:
        """Model repair is only allowed for purely structural errors."""
        if self._llm is None:
            return False
        if not errors:
            return False
        return all(e.code in _MODEL_REPAIRABLE for e in errors)

    def _model_repair(
        self,
        decision: SemanticDecision,
        errors: List[ValidationError],
        schemas: Dict[int, Dict[str, Dict[str, object]]],
    ) -> SemanticDecision:
        """Call the model once with precise errors and the target schema."""
        prompt = self._build_repair_prompt(decision, errors, schemas)
        try:
            raw = str(self._llm(prompt)).strip()
        except Exception as exc:
            print(
                f"[StructureRepair] model_call_failed error={type(exc).__name__}",
                flush=True,
            )
            return decision

        if raw.startswith("```"):
            import re
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)

        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return decision

        if not isinstance(parsed, dict):
            return decision

        actions = parsed.get("tool_calls") or parsed.get("actions") or []
        if not isinstance(actions, list) or not actions:
            return decision

        original_names = [c.name for c in decision.tool_calls]
        repaired_calls = []
        for index, item in enumerate(actions[: len(original_names)]):
            if not isinstance(item, dict):
                if index < len(decision.tool_calls):
                    repaired_calls.append(decision.tool_calls[index])
                continue
            # Force canonical tool name — model must not change it.
            canonical_name = (
                original_names[index]
                if index < len(original_names)
                else ""
            )
            args = item.get("arguments", {})
            repaired_calls.append(
                SemanticToolCall(
                    name=canonical_name,
                    arguments=dict(args) if isinstance(args, dict) else {},
                )
            )

        # Pad with originals if model returned fewer calls.
        while len(repaired_calls) < len(decision.tool_calls):
            repaired_calls.append(decision.tool_calls[len(repaired_calls)])

        return SemanticDecision(
            mode=decision.mode,
            intent=decision.intent,
            tool_calls=repaired_calls[: len(decision.tool_calls)],
            confidence=decision.confidence,
            follow_up_target=decision.follow_up_target,
            reply_hint=decision.reply_hint,
            subject=decision.subject,
            polarity=decision.polarity,
            modality=decision.modality,
        )

    # ------------------------------------------------------------------
    # Boundary enforcement
    # ------------------------------------------------------------------

    def _respects_boundaries(
        self,
        original: SemanticDecision,
        repaired: SemanticDecision,
    ) -> bool:
        """Reject any repair that violates core invariants."""
        # 1. Intent must not change.
        if repaired.intent != original.intent:
            print(
                f"[StructureRepair] boundary_violation intent "
                f"{original.intent!r} -> {repaired.intent!r}",
                flush=True,
            )
            return False

        # 2. Tool count must not change.
        if len(repaired.tool_calls) != len(original.tool_calls):
            print(
                f"[StructureRepair] boundary_violation tool_count "
                f"{len(original.tool_calls)} -> {len(repaired.tool_calls)}",
                flush=True,
            )
            return False

        # 3. Tool names must not change.
        for i, (orig_call, rep_call) in enumerate(
            zip(original.tool_calls, repaired.tool_calls)
        ):
            if rep_call.name != orig_call.name:
                print(
                    f"[StructureRepair] boundary_violation tool_name[{i}] "
                    f"{orig_call.name!r} -> {rep_call.name!r}",
                    flush=True,
                )
                return False

        # 4. Mode must not change (authorization scope).
        if repaired.mode != original.mode:
            print(
                f"[StructureRepair] boundary_violation mode "
                f"{original.mode!r} -> {repaired.mode!r}",
                flush=True,
            )
            return False

        # 5. Explicit business values must not be altered.
        for i, (orig_call, rep_call) in enumerate(
            zip(original.tool_calls, repaired.tool_calls)
        ):
            for key, orig_val in orig_call.arguments.items():
                rep_val = rep_call.arguments.get(key)
                if rep_val is not None and rep_val != orig_val:
                    # Value changed — only allowed if it was an enum alias
                    # resolution or type coercion (handled elsewhere).
                    # Flag structural changes to explicit business values.
                    if isinstance(orig_val, str) and isinstance(rep_val, str):
                        if orig_val.strip() != rep_val.strip():
                            print(
                                f"[StructureRepair] boundary_violation "
                                f"business_value[{i}].{key} "
                                f"{orig_val!r} -> {rep_val!r}",
                                flush=True,
                            )
                            return False

        return True

    # ------------------------------------------------------------------
    # Repair prompt
    # ------------------------------------------------------------------

    def _build_repair_prompt(
        self,
        decision: SemanticDecision,
        errors: List[ValidationError],
        schemas: Dict[int, Dict[str, Dict[str, object]]],
    ) -> str:
        """Build a precise repair prompt from registry contracts."""
        error_lines = []
        for err in errors:
            error_lines.append(
                f"- tool[{err.tool_index}] {err.tool_name!r}: "
                f"{err.code} field={err.field_name!r} "
                f"expected={err.expected_type!r} detail={err.detail}"
            )

        schema_lines = []
        for index, call in enumerate(decision.tool_calls):
            schema = schemas.get(index, {})
            if not schema:
                schema_lines.append(
                    f"tool[{index}] {call.name!r}: schema unavailable"
                )
                continue
            fields = []
            for fname, frules in schema.items():
                req = "required" if frules.get("required") else "optional"
                ftype = frules.get("type", "string")
                enum_info = ""
                if isinstance(frules.get("enum"), list):
                    enum_info = f" enum={frules['enum']}"
                constraints = ""
                for ckey in ("minimum", "maximum", "minLength", "maxLength"):
                    if ckey in frules:
                        constraints += f" {ckey}={frules[ckey]}"
                fields.append(
                    f"    {fname} ({req}, {ftype}{enum_info}{constraints})"
                )
            schema_lines.append(
                f"tool[{index}] {call.name!r}:\n" + "\n".join(fields)
            )

        current_calls = []
        for index, call in enumerate(decision.tool_calls):
            current_calls.append(
                f"  tool[{index}]: name={call.name!r} "
                f"arguments={json.dumps(call.arguments, ensure_ascii=False)}"
            )

        return (
            "你是一个 JSON 结构修复器。你收到的工具调用参数通过了规范化但未通过"
            "严格 schema 校验。\n\n"
            "规则（违反任一条即视为失败）：\n"
            "1. 只能修正参数名和参数值的 JSON 结构，不得改变工具名或工具数量\n"
            "2. 不得重新选择 intent 或工具\n"
            "3. 不得增删工具调用\n"
            "4. 不得猜测缺失的业务数据（时间、时长、内容等）\n"
            "5. 不得添加新字段；只能修正已有字段的值使其符合 schema\n"
            "6. 如果确实无法修复，返回原始 JSON\n\n"
            "校验错误：\n"
            + "\n".join(error_lines)
            + "\n\n目标工具 Schema：\n"
            + "\n".join(schema_lines)
            + "\n\n当前工具调用：\n"
            + "\n".join(current_calls)
            + "\n\n只返回 JSON，不要输出解释或 Markdown。格式：\n"
            + '{"tool_calls": [{"tool_name": "原工具名", '
            + '"arguments": {修正后的参数}}]}'
        )
