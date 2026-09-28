"""Strict schema validator that consumes ToolRegistry contracts exclusively.

Every validation error carries enough detail for the StructureRepairer to
attempt a targeted fix and for the pipeline to decide whether to clarify or
reject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from modules.semantic_action_parser import SemanticDecision, SemanticToolCall
from modules.tool_registry import ToolRegistry


@dataclass
class ValidationError:
    """One precise, machine-readable validation failure."""

    tool_index: int
    tool_name: str
    code: str = ""
    # One of: tool_not_found, tool_not_enabled, tool_not_model_visible,
    #         missing_parameter, unexpected_parameter,
    #         invalid_parameter_type, invalid_parameter_enum,
    #         parameter_out_of_range, unsupported_schema_type
    detail: str = ""
    field_name: str = ""
    expected_type: str = ""
    actual_value_repr: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "tool_index": self.tool_index,
            "tool_name": self.tool_name,
            "code": self.code,
            "detail": self.detail,
            "field_name": self.field_name,
            "expected_type": self.expected_type,
            "actual_value_repr": self.actual_value_repr,
        }


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    # The tool definitions for each call index, so the repairer can
    # reference the schema without re-looking-up.
    tool_schemas: Dict[int, Dict[str, Dict[str, object]]] = field(
        default_factory=dict
    )


class SchemaValidator:
    """Validates every tool call in a SemanticDecision against the registry.

    The registry's ``parameters_schema`` is the sole contract source.
    This validator does NOT modify the decision — it only reports errors.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        require_model_visible: bool = True,
    ) -> None:
        self._registry = registry
        self._require_model_visible = bool(require_model_visible)

    def validate(self, decision: SemanticDecision) -> ValidationResult:
        """Return structured validation result for every tool call."""
        errors: List[ValidationError] = []
        schemas: Dict[int, Dict[str, Dict[str, object]]] = {}

        for index, call in enumerate(decision.tool_calls):
            tool = self._registry.get(call.name)
            if tool is None:
                errors.append(
                    ValidationError(
                        tool_index=index,
                        tool_name=call.name or "<empty>",
                        code="tool_not_found",
                        detail=f"工具 {call.name!r} 未在注册表中找到",
                    )
                )
                continue
            if not tool.enabled:
                errors.append(
                    ValidationError(
                        tool_index=index,
                        tool_name=call.name,
                        code="tool_not_enabled",
                        detail=f"工具 {call.name!r} 已禁用",
                    )
                )
                continue
            if self._require_model_visible and not tool.model_visible:
                errors.append(
                    ValidationError(
                        tool_index=index,
                        tool_name=call.name,
                        code="tool_not_model_visible",
                        detail=f"工具 {call.name!r} 未向模型开放",
                    )
                )
                continue

            schema = tool.parameters_schema
            schemas[index] = dict(schema)

            # Check for unexpected parameters.
            known = set(schema.keys())
            provided = set(call.arguments.keys())
            extras = provided - known
            for extra in sorted(extras):
                errors.append(
                    ValidationError(
                        tool_index=index,
                        tool_name=call.name,
                        code="unexpected_parameter",
                        field_name=extra,
                        detail=(
                            f"工具 {call.name!r} 不接受参数 {extra!r}。"
                            f"允许的参数：{sorted(known)}"
                        ),
                    )
                )

            # Validate each parameter.
            for param_name, rules in schema.items():
                is_required = bool(rules.get("required"))
                if param_name not in call.arguments:
                    if is_required:
                        errors.append(
                            ValidationError(
                                tool_index=index,
                                tool_name=call.name,
                                code="missing_parameter",
                                field_name=param_name,
                                expected_type=str(rules.get("type", "string")),
                                detail=(
                                    f"工具 {call.name!r} 缺少必填参数 "
                                    f"{param_name!r}"
                                ),
                            )
                        )
                    continue

                value = call.arguments[param_name]
                expected_type = str(rules.get("type", "string"))
                type_error = self._check_type(
                    value, expected_type, param_name, index, call.name
                )
                if type_error is not None:
                    errors.append(type_error)
                    continue

                # Type-specific constraint checks.
                constraint_error = self._check_constraints(
                    value, rules, param_name, index, call.name
                )
                if constraint_error is not None:
                    errors.append(constraint_error)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            tool_schemas=schemas,
        )

    # ------------------------------------------------------------------
    # Type checking
    # ------------------------------------------------------------------

    def _check_type(
        self,
        value: object,
        expected: str,
        param_name: str,
        tool_index: int,
        tool_name: str,
    ) -> Optional[ValidationError]:
        base = ValidationError(
            tool_index=tool_index,
            tool_name=tool_name,
            field_name=param_name,
            expected_type=expected,
            actual_value_repr=_safe_repr(value),
        )

        if expected == "string":
            if not isinstance(value, str) or not str(value).strip():
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为非空字符串，"
                    f"实际类型：{type(value).__name__}"
                )
                return base
            return None

        if expected == "integer":
            if isinstance(value, bool):
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为整数，实际为布尔值"
                )
                return base
            try:
                int(value)
            except (TypeError, ValueError):
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为整数，"
                    f"实际类型：{type(value).__name__}，值：{_safe_repr(value)}"
                )
                return base
            return None

        if expected == "boolean":
            if not isinstance(value, bool):
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为布尔值，"
                    f"实际类型：{type(value).__name__}"
                )
                return base
            return None

        if expected == "object":
            if not isinstance(value, dict):
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为对象，"
                    f"实际类型：{type(value).__name__}"
                )
                return base
            return None

        if expected == "array":
            if not isinstance(value, list):
                base.code = "invalid_parameter_type"
                base.detail = (
                    f"参数 {param_name!r} 应为数组，"
                    f"实际类型：{type(value).__name__}"
                )
                return base
            return None

        base.code = "unsupported_schema_type"
        base.detail = f"不支持的 schema 类型：{expected}"
        return base

    def _check_constraints(
        self,
        value: object,
        rules: Dict[str, object],
        param_name: str,
        tool_index: int,
        tool_name: str,
    ) -> Optional[ValidationError]:
        expected = str(rules.get("type", "string"))
        base = ValidationError(
            tool_index=tool_index,
            tool_name=tool_name,
            field_name=param_name,
            expected_type=expected,
            actual_value_repr=_safe_repr(value),
        )

        if expected == "string":
            assert isinstance(value, str)
            clean = value.strip()
            min_len = int(rules.get("minLength", rules.get("min_length", 0)) or 0)
            max_len = int(rules.get("maxLength", rules.get("max_length", 0)) or 0)
            if min_len and len(clean) < min_len:
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 长度 {len(clean)} 小于最小值 {min_len}"
                )
                return base
            if max_len and len(clean) > max_len:
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 长度 {len(clean)} 超过最大值 {max_len}"
                )
                return base
            if isinstance(rules.get("enum"), list) and clean not in rules["enum"]:
                base.code = "invalid_parameter_enum"
                base.detail = (
                    f"参数 {param_name!r} 的值 {clean!r} "
                    f"不在允许的枚举中：{rules['enum']}"
                )
                return base
            return None

        if expected == "integer":
            assert not isinstance(value, bool)
            parsed = int(value)
            if rules.get("minimum") is not None and parsed < int(rules["minimum"]):
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 的值 {parsed} 小于最小值 "
                    f"{rules['minimum']}"
                )
                return base
            if rules.get("maximum") is not None and parsed > int(rules["maximum"]):
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 的值 {parsed} 超过最大值 "
                    f"{rules['maximum']}"
                )
                return base
            return None

        if expected == "array":
            assert isinstance(value, list)
            min_items = int(rules.get("minItems", 0) or 0)
            max_items = int(rules.get("maxItems", 0) or 0)
            if len(value) < min_items:
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 的数组长度 {len(value)} "
                    f"小于最小值 {min_items}"
                )
                return base
            if max_items and len(value) > max_items:
                base.code = "parameter_out_of_range"
                base.detail = (
                    f"参数 {param_name!r} 的数组长度 {len(value)} "
                    f"超过最大值 {max_items}"
                )
                return base
            return None

        return None


def _safe_repr(value: object, max_len: int = 80) -> str:
    """Truncated repr that never leaks private data."""
    text = repr(value)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text
