from __future__ import annotations

import hashlib
import json
from threading import RLock
from typing import Dict, Optional, Tuple

from modules.confirmation_manager import ConfirmationManager
from modules.contracts import ToolError, ToolResult
from modules.safety_policy import SafetyPolicy
from modules.tool_registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        safety_policy: SafetyPolicy,
        confirmation_manager: Optional[ConfirmationManager] = None,
    ) -> None:
        self.registry = registry
        self.safety_policy = safety_policy
        self.confirmation_manager = confirmation_manager or ConfirmationManager()
        self._completed_calls: Dict[str, Dict[str, object]] = {}
        self._call_lock = RLock()

    def execute(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        *,
        confidence: float = 1.0,
        confirmed: bool = False,
        negated: bool = False,
        informational: bool = False,
        require_confirmation: bool = False,
        confirmation_scope: Optional[str] = None,
        tool_call_id: str = "",
    ) -> ToolResult:
        name = str(tool_name)
        args = dict(arguments or {})
        call_id = str(tool_call_id).strip()
        if call_id:
            with self._call_lock:
                cached = self._completed_calls.get(call_id)
            if cached is not None:
                print(f"[Tool] duplicate call skipped: {call_id}", flush=True)
                return ToolResult.from_dict(cached)
        print(f"[Tool] selected: {name}", flush=True)
        tool = self.registry.get(name)
        if tool is None:
            return self._bind_call_id(
                ToolResult(False, name, "工具不存在", error="tool_not_found"), call_id
            )
        if not tool.enabled:
            return self._bind_call_id(
                ToolResult(False, name, "工具未启用", error="tool_disabled"), call_id
            )

        valid, normalized, error = self._validate_arguments(tool.parameters_schema, args)
        if not valid:
            return self._bind_call_id(
                ToolResult(False, name, "参数不合法", error=error), call_id
            )

        decision = self.safety_policy.evaluate(
            risk_level=tool.risk_level,
            confidence=confidence,
            requires_confirmation=(
                False
                if tool.confirmation_policy == "never"
                else tool.requires_confirmation or require_confirmation
            ),
            confirmed=confirmed,
            negated=negated,
            informational=informational,
        )
        if decision.status == "confirmation_required":
            summary = (
                tool.confirmation_summary(normalized)
                if tool.confirmation_summary is not None
                else self._confirmation_summary(name, normalized)
            )
            pending = self.confirmation_manager.create(
                name,
                normalized,
                summary,
                scope=confirmation_scope,
                call_id=call_id,
                risk_level=tool.risk_level,
                state_fingerprint=self._state_fingerprint(tool, normalized, summary),
            )
            return self._bind_call_id(ToolResult(
                False,
                name,
                "confirmation_required",
                {"confirmation": pending},
                "confirmation_required",
                status="confirmation_required",
                message_code="confirmation_required",
                display_message=summary,
            ), call_id)
        if decision.status == "clarification":
            return self._bind_call_id(ToolResult(
                False,
                name,
                "clarification_required",
                error="clarification_required",
                status="clarification_required",
                message_code="clarification_required",
                display_message="我还不能确定要执行的内容。",
            ), call_id)
        if not decision.allowed:
            return self._bind_call_id(
                ToolResult(False, name, "操作未执行", error=decision.reason), call_id
            )

        print(f"[Tool] execute: {name}", flush=True)
        try:
            result = tool.handler(**normalized)
        except Exception as error:  # Tool boundary must protect the Qt event flow.
            print(f"[Tool] failed: {name}", flush=True)
            return self._bind_call_id(ToolResult(
                False,
                name,
                "工具执行失败",
                error=ToolError.from_exception(error),
            ), call_id)
        if not isinstance(result, ToolResult):
            print(f"[Tool] failed: {name}", flush=True)
            return self._bind_call_id(
                ToolResult(False, name, "工具返回格式错误", error="invalid_tool_result"),
                call_id,
            )
        print(f"[Tool] {'success' if result.success else 'failed'}: {name}", flush=True)
        self._bind_call_id(result, call_id)
        if call_id and result.success:
            with self._call_lock:
                self._completed_calls[call_id] = result.to_dict()
        return result

    def execute_confirmed(
        self,
        confirmation_id: Optional[str] = None,
        *,
        confirmation_scope: Optional[str] = None,
    ) -> ToolResult:
        pending = self.confirmation_manager.pending(scope=confirmation_scope)
        if pending is None:
            return ToolResult(False, "confirmation", "没有可执行的确认操作", error="confirmation_missing_or_expired")
        if confirmation_id and confirmation_id != pending.get("confirmation_id"):
            return ToolResult(False, "confirmation", "确认编号不匹配", error="confirmation_missing_or_expired")
        tool = self.registry.get(str(pending.get("tool", "")))
        if tool is None:
            self.confirmation_manager.cancel(scope=confirmation_scope)
            return ToolResult(False, "confirmation", "原工具已不可用", error="confirmation_state_changed")
        arguments = dict(pending.get("arguments", {}))
        summary = (
            tool.confirmation_summary(arguments)
            if tool.confirmation_summary is not None
            else self._confirmation_summary(tool.name, arguments)
        )
        expected = str(pending.get("state_fingerprint", ""))
        current = self._state_fingerprint(tool, arguments, summary)
        if expected and current != expected:
            self.confirmation_manager.cancel(scope=confirmation_scope)
            return ToolResult(
                False,
                tool.name,
                "目标状态已经变化，请重新发起并确认。",
                error="confirmation_state_changed",
            )
        pending = self.confirmation_manager.consume(
            confirmation_id, scope=confirmation_scope
        )
        if pending is None:
            return ToolResult(False, "confirmation", "确认已失效", error="confirmation_missing_or_expired")
        return self.execute(
            str(pending["tool"]),
            dict(pending["arguments"]),
            confidence=1.0,
            confirmed=True,
            tool_call_id=str(pending.get("call_id", "")),
        )

    @staticmethod
    def _validate_arguments(
        schema: Dict[str, Dict[str, object]], arguments: Dict[str, object]
    ) -> Tuple[bool, Dict[str, object], Optional[str]]:
        extras = set(arguments) - set(schema)
        if extras:
            return False, {}, "unexpected_parameter"
        normalized: Dict[str, object] = {}
        for name, rules in schema.items():
            if bool(rules.get("required")) and name not in arguments:
                return False, {}, "missing_parameter"
            if name not in arguments:
                continue
            value = arguments[name]
            expected = rules.get("type")
            if expected == "string":
                if not isinstance(value, str) or not value.strip():
                    return False, {}, "invalid_parameter_type"
                clean = value.strip()
                if int(rules.get("minLength", rules.get("min_length", 0)) or 0) > len(clean):
                    return False, {}, "parameter_out_of_range"
                maximum = int(rules.get("maxLength", rules.get("max_length", 0)) or 0)
                if maximum and len(clean) > maximum:
                    return False, {}, "parameter_out_of_range"
                if isinstance(rules.get("enum"), list) and clean not in rules["enum"]:
                    return False, {}, "invalid_parameter_enum"
                normalized[name] = clean
            elif expected == "integer":
                if isinstance(value, bool):
                    return False, {}, "invalid_parameter_type"
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    return False, {}, "invalid_parameter_type"
                if rules.get("minimum") is not None and parsed < int(rules["minimum"]):
                    return False, {}, "parameter_out_of_range"
                if rules.get("maximum") is not None and parsed > int(rules["maximum"]):
                    return False, {}, "parameter_out_of_range"
                normalized[name] = parsed
            elif expected == "object":
                if not isinstance(value, dict):
                    return False, {}, "invalid_parameter_type"
                normalized[name] = dict(value)
            elif expected == "boolean":
                if not isinstance(value, bool):
                    return False, {}, "invalid_parameter_type"
                normalized[name] = value
            elif expected == "array":
                if not isinstance(value, list):
                    return False, {}, "invalid_parameter_type"
                minimum_items = int(rules.get("minItems", 0) or 0)
                maximum_items = int(rules.get("maxItems", 0) or 0)
                if len(value) < minimum_items or (
                    maximum_items and len(value) > maximum_items
                ):
                    return False, {}, "parameter_out_of_range"
                item_rules = rules.get("items", {})
                if not isinstance(item_rules, dict):
                    return False, {}, "unsupported_schema_type"
                item_type = item_rules.get("type")
                normalized_items = []
                for raw_item in value:
                    if item_type == "integer":
                        if isinstance(raw_item, bool):
                            return False, {}, "invalid_parameter_type"
                        try:
                            parsed_item = int(raw_item)
                        except (TypeError, ValueError):
                            return False, {}, "invalid_parameter_type"
                        if (
                            item_rules.get("minimum") is not None
                            and parsed_item < int(item_rules["minimum"])
                        ):
                            return False, {}, "parameter_out_of_range"
                        if (
                            item_rules.get("maximum") is not None
                            and parsed_item > int(item_rules["maximum"])
                        ):
                            return False, {}, "parameter_out_of_range"
                        normalized_items.append(parsed_item)
                    elif item_type == "string":
                        if not isinstance(raw_item, str) or not raw_item.strip():
                            return False, {}, "invalid_parameter_type"
                        normalized_items.append(raw_item.strip())
                    else:
                        return False, {}, "unsupported_schema_type"
                if bool(rules.get("uniqueItems")):
                    deduplicated = []
                    for item_value in normalized_items:
                        if item_value not in deduplicated:
                            deduplicated.append(item_value)
                    normalized_items = deduplicated
                    if len(normalized_items) < minimum_items:
                        return False, {}, "parameter_out_of_range"
                normalized[name] = normalized_items
            else:
                return False, {}, "unsupported_schema_type"
        return True, normalized, None

    @staticmethod
    def _bind_call_id(result: ToolResult, call_id: str) -> ToolResult:
        if call_id:
            result.tool_call_id = call_id
        return result

    @staticmethod
    def _state_fingerprint(tool, arguments: Dict[str, object], summary: str) -> str:
        value = {
            "tool": str(tool.name),
            "arguments": arguments,
            "summary": str(summary),
        }
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _confirmation_summary(name: str, arguments: Dict[str, object]) -> str:
        identifiers = [
            str(arguments[key])
            for key in ("task_id", "task_ref", "memory_id", "scope")
            if key in arguments
        ]
        return f"{name} {' '.join(identifiers)}".strip()
