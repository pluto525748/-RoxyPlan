"""Tests for the Normalize→Validate→Repair→Validate pipeline.

Covers:
- Tool name alias recovery
- Per-tool field alias recovery
- Cross-tool field substitution prevention
- Repair success (programmatic)
- Repair double-fail safety exit
- Repair must not change intent or tool count
- No false success without ToolResult
- Ambiguous time must not be auto-normalized
- Diagnostic completeness
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.schema_validator import SchemaValidator, ValidationError
from modules.semantic_action_parser import SemanticDecision, SemanticToolCall
from modules.semantic_normalizer import DeterministicNormalizer, NormalizeDiagnostic
from modules.semantic_pipeline import (
    PipelineDiagnostics,
    PipelineOutcome,
    PipelineResult,
    SemanticPipeline,
)
from modules.structure_repair import RepairResult, StructureRepairer
from modules.tool_registry import ToolRegistry

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_minimal_registry() -> ToolRegistry:
    """Build a ToolRegistry with a few tools for testing aliases and validation."""
    from modules.tool_registry import ToolDefinition

    registry = ToolRegistry()

    def _noop(**kwargs):
        from modules.contracts import ToolResult
        return ToolResult(True, "test", "ok")

    registry.register(
        ToolDefinition(
            "add_plan",
            "add a plan",
            "medium",
            {
                "title": {"type": "string", "required": True, "minLength": 1, "maxLength": 240},
                "time_slot": {"type": "string", "required": False, "enum": ["上午", "下午", "晚上"]},
                "duration_minutes": {"type": "integer", "required": False, "minimum": 1, "maximum": 1440},
            },
            _noop,
            model_visible=True,
            side_effect=True,
            aliases=["add_plan_form", "create_plan"],
            field_aliases={"plan_name": "title", "task": "title", "name": "title"},
            safe_ignorable_fields=["call_id", "_index", "_order"],
            enum_aliases={"time_slot": {"今晚": "晚上", "今夜": "晚上"}},
        )
    )
    registry.register(
        ToolDefinition(
            "show_plan",
            "show plans",
            "low",
            {"date": {"type": "string", "required": False, "maxLength": 10}},
            _noop,
            model_visible=True,
            side_effect=False,
            aliases=["list_plans", "get_plans"],
        )
    )
    registry.register(
        ToolDefinition(
            "add_action_log",
            "log an action",
            "medium",
            {"content": {"type": "string", "required": True}},
            _noop,
            model_visible=True,
            side_effect=True,
            aliases=["log_action", "record_action"],
            field_aliases={"text": "content", "message": "content"},
        )
    )
    registry.register(
        ToolDefinition(
            "complete_plan",
            "complete a plan",
            "medium",
            {"match_text": {"type": "string", "required": True}},
            _noop,
            model_visible=True,
            side_effect=True,
            aliases=["mark_plan_complete"],
            field_aliases={"task_id": "match_text", "id": "match_text"},
        )
    )
    return registry


def _make_decision(
    mode: str = "tool_then_reply",
    intent: str = "add_plan",
    tool_calls: List[Dict] | None = None,
) -> SemanticDecision:
    """Quickly create a SemanticDecision for testing."""
    calls = []
    if tool_calls:
        for tc in tool_calls:
            calls.append(SemanticToolCall(name=tc["name"], arguments=tc.get("arguments", {})))
    return SemanticDecision(
        mode=mode,
        intent=intent,
        tool_calls=calls,
        confidence=0.9,
    )


def test_hidden_registered_tool_is_strict_for_model_and_valid_for_internal_pipeline():
    from modules.tool_registry import ToolDefinition

    registry = _make_minimal_registry()
    registry.register(
        ToolDefinition(
            "internal_read",
            "internal deterministic read",
            "low",
            {},
            lambda: None,
            model_visible=False,
            side_effect=False,
        )
    )
    decision = _make_decision(
        intent="internal_read",
        tool_calls=[{"name": "internal_read", "arguments": {}}],
    )

    strict = SemanticPipeline(registry).process(decision)
    internal = SemanticPipeline(
        registry,
        require_model_visible=False,
    ).process(decision)

    assert strict.outcome == PipelineOutcome.REJECT
    assert strict.diagnostics.outcome_reason == "tool_not_model_visible"
    assert internal.outcome == PipelineOutcome.EXECUTE


def test_structured_semantics_survive_normalization_and_repair():
    registry = _make_minimal_registry()
    decision = SemanticDecision(
        mode="tool_then_reply",
        intent="add_plan",
        tool_calls=[
            SemanticToolCall(
                name="create_plan",
                arguments={"plan_name": "cosplay", "call_id": "trace-1"},
            )
        ],
        confidence=0.9,
        subject="self",
        polarity="positive",
        modality="desire",
    )

    result = SemanticPipeline(registry).process(decision)

    assert result.outcome == PipelineOutcome.EXECUTE
    assert result.decision.subject == "self"
    assert result.decision.polarity == "positive"
    assert result.decision.modality == "desire"
    assert result.decision.to_dict()["modality"] == "desire"


# ---------------------------------------------------------------------------
# 1. Tool name alias recovery
# ---------------------------------------------------------------------------


class TestToolNameAliasRecovery:
    def test_alias_resolved_to_canonical(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan_form", "arguments": {"title": "学习Rust"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].name == "add_plan"
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].tool_name_changed is True
        assert result.diagnostics[0].original_tool_name == "add_plan_form"
        assert result.diagnostics[0].resolved_tool_name == "add_plan"

    def test_create_plan_alias_resolved(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "create_plan", "arguments": {"title": "test"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].name == "add_plan"

    def test_log_action_alias_resolved(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="add_action_log",
            tool_calls=[{"name": "log_action", "arguments": {"content": "完成了复习"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].name == "add_action_log"

    def test_mark_plan_complete_alias_resolved(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="complete_plan",
            tool_calls=[{"name": "mark_plan_complete", "arguments": {"match_text": "task_1"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].name == "complete_plan"


# ---------------------------------------------------------------------------
# 2. Per-tool field alias recovery
# ---------------------------------------------------------------------------


class TestFieldAliasRecovery:
    def test_plan_name_maps_to_title_for_add_plan(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"plan_name": "学习Python"}}]
        )
        result = normalizer.normalize(decision)

        args = result.decision.tool_calls[0].arguments
        assert "plan_name" not in args
        assert args["title"] == "学习Python"
        assert len(result.diagnostics[0].field_mappings) == 1
        assert result.diagnostics[0].field_mappings[0][0] == "plan_name"
        assert result.diagnostics[0].field_mappings[0][1] == "title"

    def test_task_maps_to_title_for_add_plan(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"task": "复习数学"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].arguments["title"] == "复习数学"

    def test_text_maps_to_content_for_add_action_log(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="add_action_log",
            tool_calls=[{"name": "add_action_log", "arguments": {"text": "完成了测试"}}]
        )
        result = normalizer.normalize(decision)

        assert "text" not in result.decision.tool_calls[0].arguments
        assert result.decision.tool_calls[0].arguments["content"] == "完成了测试"

    def test_task_id_maps_to_match_text_for_complete_plan(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="complete_plan",
            tool_calls=[{"name": "complete_plan", "arguments": {"task_id": "task_3"}}]
        )
        result = normalizer.normalize(decision)

        assert "task_id" not in result.decision.tool_calls[0].arguments
        assert result.decision.tool_calls[0].arguments["match_text"] == "task_3"

    def test_alias_and_tool_alias_combined(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "create_plan", "arguments": {"plan_name": "学Go"}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.tool_calls[0].name == "add_plan"
        assert result.decision.tool_calls[0].arguments["title"] == "学Go"


# ---------------------------------------------------------------------------
# 3. Cross-tool field substitution prevention
# ---------------------------------------------------------------------------


class TestCrossToolFieldPrevention:
    def test_plan_name_not_mapped_for_non_add_plan(self):
        """plan_name → title only applies to add_plan, not add_action_log."""
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="add_action_log",
            tool_calls=[{"name": "add_action_log", "arguments": {"plan_name": "should not map"}}]
        )
        result = normalizer.normalize(decision)

        # plan_name should NOT be mapped to title for add_action_log
        # It should remain as an unresolved field
        args = result.decision.tool_calls[0].arguments
        assert "plan_name" in args
        assert "title" not in args
        # Should be flagged as unresolved
        assert 0 in result.unresolved_fields
        assert "plan_name" in result.unresolved_fields[0]

    def test_task_field_not_cross_mapped(self):
        """task → title for add_plan only, not for complete_plan."""
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="complete_plan",
            tool_calls=[{"name": "complete_plan", "arguments": {"task": "should remain task"}}]
        )
        result = normalizer.normalize(decision)

        # complete_plan doesn't have task→??? alias; task survives as unresolved
        args = result.decision.tool_calls[0].arguments
        assert "task" in args  # unresolved — will be flagged by validator


# ---------------------------------------------------------------------------
# 4. Repair success (programmatic)
# ---------------------------------------------------------------------------


class TestRepairSuccess:
    def test_programmatic_repair_drops_safe_ignorable_fields(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)
        repairer = StructureRepairer(registry)

        # "call_id" and "_index" are in safe_ignorable_fields for add_plan
        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {
                    "title": "学习Rust",
                    "call_id": "call_1",
                    "_index": 0,
                }
            }]
        )
        validation = validator.validate(decision)
        assert not validation.valid
        assert any(e.code == "unexpected_parameter" for e in validation.errors)

        result = repairer.repair(decision, validation)

        assert result.repaired
        assert result.repair_method == "programmatic"
        re_validation = validator.validate(result.decision)
        assert re_validation.valid
        # safe_ignorable fields gone, title preserved
        args = result.decision.tool_calls[0].arguments
        assert args["title"] == "学习Rust"
        assert "call_id" not in args
        assert "_index" not in args

    def test_repair_preserves_valid_fields(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)
        repairer = StructureRepairer(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {
                    "title": "学习Rust",
                    "time_slot": "下午",
                    "_order": 1,  # safe_ignorable
                }
            }]
        )
        validation = validator.validate(decision)
        result = repairer.repair(decision, validation)

        assert result.repaired
        args = result.decision.tool_calls[0].arguments
        assert args["title"] == "学习Rust"
        assert args["time_slot"] == "下午"
        assert "_order" not in args

    def test_enum_alias_resolved_deterministically(self):
        """Enum aliases from ToolRegistry must be resolved before model repair."""
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)
        repairer = StructureRepairer(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "time_slot": "今晚"}
            }]
        )
        validation = validator.validate(decision)
        assert not validation.valid  # "今晚" not in ["上午","下午","晚上"]

        result = repairer.repair(decision, validation)
        assert result.repaired
        assert result.repair_method == "programmatic"
        assert result.decision.tool_calls[0].arguments["time_slot"] == "晚上"


# ---------------------------------------------------------------------------
# 5. Repair double-fail safety exit
# ---------------------------------------------------------------------------


class TestRepairDoubleFail:
    def test_missing_required_field_not_programmatically_repairable(self):
        """Missing required fields can't be fixed programmatically — needs model."""
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)
        repairer = StructureRepairer(registry)  # No llm_callable

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"time_slot": "上午"}}]
            # Missing required "title"
        )
        validation = validator.validate(decision)
        assert not validation.valid

        result = repairer.repair(decision, validation)

        # Without an LLM, repair should fail
        assert not result.repaired
        assert result.errors_after  # still has errors

    def test_pipeline_clarifies_on_missing_params(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)  # No LLM

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"time_slot": "上午"}}]
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.CLARIFY
        assert "title" in result.clarification_message

    def test_pipeline_rejects_on_tool_not_found(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "nonexistent_tool", "arguments": {}}]
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.REJECT
        assert "没有执行" in result.reject_reason

    def test_unexpected_internal_parameter_is_never_exposed_to_user(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)
        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "简历包装", "task_ref": "private-id"},
            }]
        )

        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.CLARIFY
        assert "task_ref" not in result.clarification_message
        assert "private-id" not in result.clarification_message
        assert "计划目标" in result.clarification_message

    def test_unknown_runtime_parameter_names_are_not_exposed_to_user(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)
        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {
                    "title": "简历包装",
                    "tool_call_id": "private-call-id",
                    "scope": "private-scope",
                },
            }]
        )

        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.CLARIFY
        assert "tool_call_id" not in result.clarification_message
        assert "scope" not in result.clarification_message
        assert "private-call-id" not in result.clarification_message
        assert "private-scope" not in result.clarification_message
        assert "其他信息" in result.clarification_message


# ---------------------------------------------------------------------------
# 6. Repair must not change intent or tool count
# ---------------------------------------------------------------------------


class TestIntentAndToolCountPreservation:
    def test_normalizer_preserves_intent(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            intent="show_plan",
            tool_calls=[{"name": "list_plans", "arguments": {}}]
        )
        result = normalizer.normalize(decision)

        assert result.decision.intent == "show_plan"
        assert result.decision.mode == "tool_then_reply"

    def test_normalizer_preserves_tool_count(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[
                {"name": "add_plan_form", "arguments": {"title": "task 1"}},
                {"name": "log_action", "arguments": {"content": "done"}},
            ]
        )
        result = normalizer.normalize(decision)

        assert len(result.decision.tool_calls) == 2
        assert result.decision.tool_calls[0].name == "add_plan"
        assert result.decision.tool_calls[1].name == "add_action_log"

    def test_repair_preserves_tool_count(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)
        repairer = StructureRepairer(registry)

        # Use safe_ignorable fields so repair succeeds
        decision = _make_decision(
            tool_calls=[
                {"name": "add_plan", "arguments": {"title": "task 1", "call_id": "x"}},
                {"name": "add_plan", "arguments": {"title": "task 2", "_index": 0}},
            ]
        )
        validation = validator.validate(decision)
        result = repairer.repair(decision, validation)

        assert len(result.decision.tool_calls) == 2
        assert result.decision.tool_calls[0].name == "add_plan"
        assert result.decision.tool_calls[1].name == "add_plan"


# ---------------------------------------------------------------------------
# 7. No false success without ToolResult
# ---------------------------------------------------------------------------


class TestNoFalseSuccess:
    def test_reject_outcome_has_no_decision(self):
        """When the pipeline rejects, it must not provide a decision to execute."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "nonexistent_tool", "arguments": {}}]
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.REJECT
        assert not result.should_execute

    def test_clarify_outcome_has_no_decision(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {}}]  # missing title
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.CLARIFY
        assert not result.should_execute

    def test_execute_outcome_has_valid_decision(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "valid task"}}]
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.EXECUTE
        assert result.should_execute
        assert result.decision is not None
        assert len(result.validated_tool_calls) == 1


# ---------------------------------------------------------------------------
# 8. Ambiguous time must not be auto-normalized
# ---------------------------------------------------------------------------


class TestAmbiguousTimeNotNormalized:
    def test_vague_duration_not_resolved(self):
        """The normalizer must not guess or fill in ambiguous time/duration."""
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        # Model output with a vague field that isn't an alias
        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {
                    "title": "学习",
                    "vague_time": "下午晚一点",  # ambiguous — not a known alias
                }
            }]
        )
        result = normalizer.normalize(decision)

        # vague_time should NOT be mapped to time_slot since it's not an alias
        args = result.decision.tool_calls[0].arguments
        assert "time_slot" not in args
        # It survives as unresolved — validator will flag it
        assert "vague_time" in args

    def test_ambiguous_clock_time_not_normalized(self):
        """Clock times like "3点" can't be deterministically mapped to time_slot."""
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {
                    "title": "开会",
                    "time": "3点",  # ambiguous clock time, not a field alias for add_plan
                }
            }]
        )
        result = normalizer.normalize(decision)

        # "time" is not a field alias for add_plan (only "plan_name", "task", "name" are)
        # It should remain as unresolved
        args = result.decision.tool_calls[0].arguments
        assert "time" in args  # unresolved
        assert "time_slot" not in args  # NOT mapped


# ---------------------------------------------------------------------------
# 9. Diagnostic completeness
# ---------------------------------------------------------------------------


class TestDiagnosticCompleteness:
    def test_normalize_diagnostic_records_original_and_target(self):
        registry = _make_minimal_registry()
        normalizer = DeterministicNormalizer(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan_form", "arguments": {"plan_name": "test"}}]
        )
        result = normalizer.normalize(decision)

        diag = result.diagnostics[0]
        assert diag.original_tool_name == "add_plan_form"
        assert diag.resolved_tool_name == "add_plan"
        assert diag.tool_name_changed
        assert any(m[0] == "plan_name" and m[1] == "title" for m in diag.field_mappings)

    def test_pipeline_diagnostics_has_full_trace(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        # "call_id" is in safe_ignorable_fields — programmatic repair drops it
        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "test", "call_id": "x"}}]
        )
        result = pipeline.process(decision)

        diag = result.diagnostics
        assert diag.original_decision is not None
        assert diag.normalized_decision is not None
        # Programmatic repair should have dropped "call_id" (safe_ignorable)
        assert diag.repair_result is not None
        assert diag.repair_result.repaired
        assert diag.outcome == PipelineOutcome.EXECUTE.value

    def test_diagnostic_records_repair_failure(self):
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {}}]  # missing title
        )
        result = pipeline.process(decision)

        diag = result.diagnostics
        assert len(diag.validation_errors_before_repair) > 0
        assert diag.outcome == PipelineOutcome.CLARIFY.value


# ---------------------------------------------------------------------------
# 10. Validator edge cases
# ---------------------------------------------------------------------------


class TestValidatorEdgeCases:
    def test_valid_decision_passes(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "valid"}}]
        )
        result = validator.validate(decision)

        assert result.valid
        assert len(result.errors) == 0

    def test_missing_required_param_detected(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"time_slot": "上午"}}]
        )
        result = validator.validate(decision)

        assert not result.valid
        assert any(e.code == "missing_parameter" and e.field_name == "title" for e in result.errors)

    def test_invalid_enum_detected(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "time_slot": "午夜"}
            }]
        )
        result = validator.validate(decision)

        assert not result.valid
        assert any(e.code == "invalid_parameter_enum" for e in result.errors)

    def test_integer_out_of_range_detected(self):
        registry = _make_minimal_registry()
        validator = SchemaValidator(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "duration_minutes": 0}
            }]
        )
        result = validator.validate(decision)

        assert not result.valid
        assert any(e.code == "parameter_out_of_range" for e in result.errors)


# ---------------------------------------------------------------------------
# 11. Pipeline integration: alias + validate + repair
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    def test_alias_then_validate_success(self):
        """Alias resolution should happen before validation, so aliased fields pass."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{"name": "add_plan_form", "arguments": {"plan_name": "learn Rust"}}]
        )
        result = pipeline.process(decision)

        assert result.outcome == PipelineOutcome.EXECUTE
        assert result.decision.tool_calls[0].name == "add_plan"
        assert result.decision.tool_calls[0].arguments["title"] == "learn Rust"

    def test_multiple_tools_with_mixed_validity(self):
        """One invalid tool call should cause clarify/reject for the whole batch."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[
                {"name": "add_plan", "arguments": {"title": "valid task"}},
                {"name": "add_plan", "arguments": {}},  # missing title
            ]
        )
        result = pipeline.process(decision)

        # The whole batch fails validation — pipeline clarifies
        assert result.outcome == PipelineOutcome.CLARIFY


# ---------------------------------------------------------------------------
# 12. ToolRegistry alias contract tests
# ---------------------------------------------------------------------------


class TestToolRegistryAliases:
    def test_alias_maps_are_exported(self):
        registry = _make_minimal_registry()
        aliases = registry.tool_name_aliases()

        assert aliases["add_plan_form"] == "add_plan"
        assert aliases["create_plan"] == "add_plan"
        assert aliases["log_action"] == "add_action_log"

    def test_field_aliases_per_tool(self):
        registry = _make_minimal_registry()
        add_plan_aliases = registry.field_aliases_for("add_plan")

        assert add_plan_aliases["plan_name"] == "title"
        assert add_plan_aliases["task"] == "title"

        action_log_aliases = registry.field_aliases_for("add_action_log")
        assert action_log_aliases["text"] == "content"
        # "plan_name" should NOT be an alias for add_action_log
        assert "plan_name" not in action_log_aliases

    def test_field_aliases_for_unknown_tool_returns_empty(self):
        registry = _make_minimal_registry()
        assert registry.field_aliases_for("nonexistent") == {}

    def test_safe_ignorable_fields_exported(self):
        registry = _make_minimal_registry()
        safe = registry.safe_ignorable_fields_for("add_plan")
        assert "call_id" in safe
        assert "_index" in safe

    def test_enum_aliases_exported(self):
        registry = _make_minimal_registry()
        aliases = registry.enum_aliases_for("add_plan")
        assert "time_slot" in aliases
        assert aliases["time_slot"]["今晚"] == "晚上"


# ---------------------------------------------------------------------------
# 13. Audit: unknown business fields must not be silently dropped
# ---------------------------------------------------------------------------


class TestBusinessFieldPreservation:
    """Fields that carry potential business semantics must trigger CLARIFY,
    never silent drop."""

    def test_repeat_field_not_silently_dropped(self):
        """'repeat' is a business field — model might mean recurrence."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "daily task", "repeat": "daily"}
            }]
        )
        result = pipeline.process(decision)
        # "repeat" is NOT in safe_ignorable_fields → must CLARIFY, not EXECUTE
        assert result.outcome == PipelineOutcome.CLARIFY
        assert "repeat" in result.clarification_message

    def test_date_field_not_in_test_registry_triggers_clarify(self):
        """In this test registry add_plan has no 'date' field, so it triggers CLARIFY."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "date": "2026-08-06"}
            }]
        )
        result = pipeline.process(decision)
        # "date" is not in the minimal test registry's add_plan schema → CLARIFY
        assert result.outcome == PipelineOutcome.CLARIFY
        assert "date" in result.clarification_message

    def test_duration_field_not_silently_dropped(self):
        """'duration' not in schema nor safe_ignorable → CLARIFY."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "duration": "30min"}
            }]
        )
        result = pipeline.process(decision)
        # "duration" is a business field, not safe_ignorable → CLARIFY
        assert result.outcome == PipelineOutcome.CLARIFY
        assert "duration" in result.clarification_message

    def test_reminder_field_not_silently_dropped(self):
        """'reminder' carries clear business semantics."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "reminder": "5min before"}
            }]
        )
        result = pipeline.process(decision)
        assert result.outcome == PipelineOutcome.CLARIFY
        assert "reminder" in result.clarification_message

    def test_safe_ignorable_call_id_is_silently_dropped(self):
        """call_id is in safe_ignorable_fields — safe to drop."""
        registry = _make_minimal_registry()
        pipeline = SemanticPipeline(registry)

        decision = _make_decision(
            tool_calls=[{
                "name": "add_plan",
                "arguments": {"title": "test", "call_id": "call_abc"}
            }]
        )
        result = pipeline.process(decision)
        # call_id is safe_ignorable → programmatic repair drops it → EXECUTE
        assert result.outcome == PipelineOutcome.EXECUTE
        assert "call_id" not in result.decision.tool_calls[0].arguments


# ---------------------------------------------------------------------------
# 14. Audit: repair boundary enforcement
# ---------------------------------------------------------------------------


class TestRepairBoundaryEnforcement:
    """Repair must never change intent, tool names, tool count, mode,
    or explicit business values."""

    def test_repair_rejects_intent_change(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            intent="add_plan",
            tool_calls=[{"name": "add_plan", "arguments": {"title": "test"}}]
        )
        # Simulate a repaired decision with changed intent
        altered = SemanticDecision(
            mode="tool_then_reply",
            intent="complete_plan",  # changed!
            tool_calls=[SemanticToolCall(name="add_plan", arguments={"title": "test"})],
        )
        assert not repairer._respects_boundaries(original, altered)

    def test_repair_rejects_tool_name_change(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "test"}}]
        )
        altered = SemanticDecision(
            mode="tool_then_reply",
            intent="add_plan",
            tool_calls=[SemanticToolCall(name="show_plan", arguments={"title": "test"})],
        )
        assert not repairer._respects_boundaries(original, altered)

    def test_repair_rejects_tool_count_change(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            tool_calls=[
                {"name": "add_plan", "arguments": {"title": "task 1"}},
                {"name": "add_plan", "arguments": {"title": "task 2"}},
            ]
        )
        altered = SemanticDecision(
            mode="tool_then_reply",
            intent="add_plan",
            tool_calls=[SemanticToolCall(name="add_plan", arguments={"title": "task 1"})],
        )
        assert not repairer._respects_boundaries(original, altered)

    def test_repair_rejects_mode_change(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            mode="tool_then_reply",
            tool_calls=[{"name": "add_plan", "arguments": {"title": "test"}}]
        )
        altered = SemanticDecision(
            mode="chat_only",  # changed!
            intent="add_plan",
            tool_calls=[SemanticToolCall(name="add_plan", arguments={"title": "test"})],
        )
        assert not repairer._respects_boundaries(original, altered)

    def test_repair_rejects_business_value_change(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "学习Rust"}}]
        )
        altered = SemanticDecision(
            mode="tool_then_reply",
            intent="add_plan",
            tool_calls=[SemanticToolCall(name="add_plan", arguments={"title": "学习Go"})],
        )
        assert not repairer._respects_boundaries(original, altered)

    def test_repair_accepts_unchanged_decision(self):
        registry = _make_minimal_registry()
        repairer = StructureRepairer(registry)

        original = _make_decision(
            tool_calls=[{"name": "add_plan", "arguments": {"title": "学习Rust"}}]
        )
        # Same decision — boundaries respected
        assert repairer._respects_boundaries(original, original)
