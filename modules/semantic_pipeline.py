"""Unified pipeline: SemanticDecision → Normalize → Validate → Repair → Execute.

This is the single upgrade point for the model-output reliability path.
Every tool call passes through the same contract-derived gates before
reaching ToolExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from modules.schema_validator import SchemaValidator, ValidationError, ValidationResult
from modules.semantic_action_parser import SemanticDecision, SemanticToolCall
from modules.semantic_normalizer import (
    DeterministicNormalizer,
    NormalizeDiagnostic,
    NormalizeResult,
)
from modules.structure_repair import RepairResult, StructureRepairer
from modules.tool_registry import ToolRegistry


class PipelineOutcome(Enum):
    EXECUTE = "execute"
    CLARIFY = "clarify"
    REJECT = "reject"


@dataclass
class PipelineDiagnostics:
    """Complete trace of the pipeline run for audit and debugging."""

    original_decision: Optional[Dict[str, object]] = None
    normalize_diagnostics: List[NormalizeDiagnostic] = field(default_factory=list)
    normalized_decision: Optional[Dict[str, object]] = None
    validation_errors_before_repair: List[ValidationError] = field(
        default_factory=list
    )
    repair_result: Optional[RepairResult] = None
    validation_errors_after_repair: List[ValidationError] = field(
        default_factory=list
    )
    outcome: str = ""
    outcome_reason: str = ""


@dataclass
class PipelineResult:
    """The pipeline's final decision about what to do with a SemanticDecision."""

    outcome: PipelineOutcome
    decision: Optional[SemanticDecision] = None
    clarification_message: str = ""
    reject_reason: str = ""
    diagnostics: PipelineDiagnostics = field(default_factory=PipelineDiagnostics)

    @property
    def should_execute(self) -> bool:
        return self.outcome == PipelineOutcome.EXECUTE and self.decision is not None

    @property
    def validated_tool_calls(self) -> List[SemanticToolCall]:
        if self.decision is None:
            return []
        return list(self.decision.tool_calls)


class SemanticPipeline:
    """Orchestrates the full Normalize → Validate → Repair → Validate chain.

    Usage::

        pipeline = SemanticPipeline(registry, llm_callable=...)
        result = pipeline.process(raw_decision)
        if result.should_execute:
            for call in result.validated_tool_calls:
                executor.execute(call.name, call.arguments)
        elif result.outcome == PipelineOutcome.CLARIFY:
            return result.clarification_message
        else:
            return result.reject_reason
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        llm_callable: Optional[Callable[[str], str]] = None,
        require_model_visible: bool = True,
    ) -> None:
        self._registry = registry
        self._normalizer = DeterministicNormalizer(registry)
        self._validator = SchemaValidator(
            registry,
            require_model_visible=require_model_visible,
        )
        self._repairer = StructureRepairer(
            registry,
            llm_callable=llm_callable,
            require_model_visible=require_model_visible,
        )

    def process(self, decision: SemanticDecision) -> PipelineResult:
        """Run the full pipeline and return an executable or terminal result."""
        diag = PipelineDiagnostics()
        diag.original_decision = decision.to_dict()

        # -- Step 0: Quick-reject empty / chat-only decisions ---------------
        if not decision.tool_calls:
            if decision.mode == "clarify" and decision.reply_hint:
                return PipelineResult(
                    outcome=PipelineOutcome.CLARIFY,
                    clarification_message=decision.reply_hint,
                    diagnostics=diag,
                )
            return PipelineResult(
                outcome=PipelineOutcome.REJECT,
                reject_reason="no_tool_calls",
                diagnostics=diag,
            )

        # -- Step 1: Deterministic normalization ----------------------------
        normalize_result = self._normalizer.normalize(decision)
        diag.normalize_diagnostics = normalize_result.diagnostics
        diag.normalized_decision = normalize_result.decision.to_dict()

        # -- Step 2: Strict schema validation -------------------------------
        validation = self._validator.validate(normalize_result.decision)
        diag.validation_errors_before_repair = list(validation.errors)

        if validation.valid:
            diag.outcome = PipelineOutcome.EXECUTE.value
            return PipelineResult(
                outcome=PipelineOutcome.EXECUTE,
                decision=normalize_result.decision,
                diagnostics=diag,
            )

        # -- Step 3: Check if errors are unrepairable -----------------------
        if not self._can_attempt_repair(validation.errors):
            return self._terminal_result(validation, diag)

        # -- Step 4: Structural repair (at most once) -----------------------
        repair_result = self._repairer.repair(
            normalize_result.decision, validation
        )
        diag.repair_result = repair_result

        if not repair_result.repaired:
            # Repair failed — terminal.
            diag.validation_errors_after_repair = repair_result.errors_after
            return self._terminal_result_from_repair(repair_result, diag)

        # -- Step 5: Re-validate after repair -------------------------------
        re_validation = self._validator.validate(repair_result.decision)
        diag.validation_errors_after_repair = list(re_validation.errors)

        if re_validation.valid:
            diag.outcome = PipelineOutcome.EXECUTE.value
            return PipelineResult(
                outcome=PipelineOutcome.EXECUTE,
                decision=repair_result.decision,
                diagnostics=diag,
            )

        # Still failing after repair — terminal.
        return self._terminal_result(re_validation, diag)

    # ------------------------------------------------------------------
    # Terminal outcomes
    # ------------------------------------------------------------------

    def _terminal_result(
        self,
        validation: ValidationResult,
        diag: PipelineDiagnostics,
    ) -> PipelineResult:
        """Decide between clarify and reject based on error types."""
        errors = validation.errors
        if not errors:
            diag.outcome = PipelineOutcome.REJECT.value
            diag.outcome_reason = "validation_failed_no_errors"
            return PipelineResult(
                outcome=PipelineOutcome.REJECT,
                reject_reason="校验失败但无具体错误信息",
                diagnostics=diag,
            )

        # Unexpected parameters that carry potential business semantics → clarify.
        unexpected = [e for e in errors if e.code == "unexpected_parameter"]
        if unexpected:
            fields = sorted({e.field_name for e in unexpected if e.field_name})
            public_labels = {
                "task_ref": "计划目标",
                "target_ref": "计划目标",
                "match_text": "计划目标",
                "changes": "修改内容",
            }
            labels = []
            for field in fields:
                normalized_field = str(field).strip().lower()
                looks_internal = (
                    normalized_field.startswith("_")
                    or normalized_field.endswith("_id")
                    or normalized_field
                    in {
                        "id",
                        "uid",
                        "scope",
                        "metadata",
                        "context",
                        "confirmation_scope",
                    }
                )
                label = public_labels.get(
                    field,
                    "其他信息" if looks_internal else field,
                )
                if label not in labels:
                    labels.append(label)
            message = (
                "这个请求里包含当前操作不支持的信息："
                + "、".join(labels or ["其他信息"])
                + "。请换一种更明确的说法后再试。"
            )
            diag.outcome = PipelineOutcome.CLARIFY.value
            diag.outcome_reason = "unexpected_business_parameters"
            return PipelineResult(
                outcome=PipelineOutcome.CLARIFY,
                clarification_message=message,
                diagnostics=diag,
            )

        # Missing required business parameters → clarify.
        missing = [e for e in errors if e.code == "missing_parameter"]
        if missing:
            fields = [e.field_name for e in missing if e.field_name]
            public_labels = {
                "task_ref": "要操作的计划（编号或标题）",
                "target_ref": "要保留的计划（编号或标题）",
                "match_text": "要完成的计划（编号或标题）",
                "changes": "需要修改的内容",
            }
            message = (
                "我还缺少这些信息："
                + "、".join(public_labels.get(field, field) for field in fields)
                + "。请补充后再执行。"
            )
            diag.outcome = PipelineOutcome.CLARIFY.value
            diag.outcome_reason = "missing_required_parameters"
            return PipelineResult(
                outcome=PipelineOutcome.CLARIFY,
                clarification_message=message,
                diagnostics=diag,
            )

        # Tool not found / not enabled → reject.
        fatal = [
            e
            for e in errors
            if e.code
            in (
                "tool_not_found",
                "tool_not_enabled",
                "tool_not_model_visible",
            )
        ]
        if fatal:
            diag.outcome = PipelineOutcome.REJECT.value
            diag.outcome_reason = fatal[0].code
            return PipelineResult(
                outcome=PipelineOutcome.REJECT,
                reject_reason=(
                    f"工具 {fatal[0].tool_name!r} 不可用，"
                    "本轮没有执行任何操作。"
                ),
                diagnostics=diag,
            )

        # Other structural errors after repair → clarify.
        diag.outcome = PipelineOutcome.CLARIFY.value
        diag.outcome_reason = "structural_errors_after_repair"
        return PipelineResult(
            outcome=PipelineOutcome.CLARIFY,
            clarification_message=(
                "我还不能安全确定要执行的工具或参数，请换一种更明确的说法。"
            ),
            diagnostics=diag,
        )

    def _terminal_result_from_repair(
        self,
        repair: RepairResult,
        diag: PipelineDiagnostics,
    ) -> PipelineResult:
        """Build terminal result from a failed repair attempt."""
        if repair.errors_after:
            return self._terminal_result(
                ValidationResult(
                    valid=False,
                    errors=repair.errors_after,
                ),
                diag,
            )
        diag.outcome = PipelineOutcome.REJECT.value
        diag.outcome_reason = repair.repair_diagnostic
        return PipelineResult(
            outcome=PipelineOutcome.REJECT,
            reject_reason="无法安全修复模型输出，本轮没有执行任何操作。",
            diagnostics=diag,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _can_attempt_repair(errors: List[ValidationError]) -> bool:
        """Repair is only possible for structural, non-semantic errors."""
        # Never repair if any tool is not found.
        if any(
            e.code
            in (
                "tool_not_found",
                "tool_not_enabled",
                "tool_not_model_visible",
            )
            for e in errors
        ):
            return False
        return True
