"""Central pytest contract-lane classification.

The default lane is the current production contract.  Only tests that
deliberately exercise a supported legacy surface or immutable evaluation
evidence are listed below.  Keeping this mapping in one place prevents old
expectations from being reported as current product regressions.
"""

from __future__ import annotations


PRODUCTION_CONTRACT = "production_contract"
COMPATIBILITY_CONTRACT = "compatibility_contract"
HISTORICAL_BASELINE = "historical_baseline"


_HISTORICAL_MODULES = frozenset(
    {
        "tests/test_v21_blind_runner.py",
        "tests/test_v21_optimization_runner.py",
    }
)

_HISTORICAL_TEST_PREFIXES = (
    # V2.2 deliberately retired conversational batch memory deletion.  The
    # supported chat surface is the exact-body flow: “忘记：完整记忆正文”.
    "tests/test_agent_reliability_v16.py::test_40_batch_delete_summary_has_scope_and_count",
    "tests/test_agent_reliability_v16.py::test_41_batch_delete_executes_original_saved_operation",
    # V2.1 model-driven complex plan edits. V2.2 deliberately hides these
    # tools from the model and retains these assertions only as old evidence.
    "tests/test_plan_last_task_reference.py::test_model_last_task_reference_uses_successful_task_before_schema_validation",
    "tests/test_plan_last_task_reference.py::test_last_task_reference_does_not_cross_conversations",
    "tests/test_plan_last_task_reference.py::test_explicit_plan_target_wins_over_last_task_marker",
    "tests/test_plan_last_task_reference.py::test_structured_pronoun_prefers_the_single_session_task_over_global_plans",
    "tests/test_plan_last_task_reference.py::test_malformed_model_changes_are_recovered_from_grounded_user_time_fields",
    "tests/test_plan_last_task_reference.py::test_malformed_model_changes_without_grounded_values_asks_for_clarification",
    "tests/test_duplicate_plan_resolution.py::test_merge_keeps_completed_status_only_when_every_source_is_completed",
    "tests/test_duplicate_plan_resolution.py::test_duplicate_inspection_reports_no_group_without_creating_pending",
    "tests/test_duplicate_plan_resolution.py::test_merge_conflict_asks_for_value_before_confirmation",
    # The V2.1 optimization fixture and its aggregate summary require the
    # semantic model to call reschedule_plan directly. V2.2 deliberately hides
    # that complex edit capability and reports an honest degradation instead.
    "tests/test_v21_replay_acceptance.py::test_optimization_v2_case[opt_v2_006]",
    "tests/test_v21_replay_acceptance.py::test_optimization_v2_summary",
)

_COMPATIBILITY_MODULES = frozenset(
    {
        "tests/eval/test_v18_real_dialogues.py",
        "tests/test_v19_final_interaction_matrix.py",
        "tests/test_v21_fullchain_assertions.py",
        "tests/test_v21_replay_acceptance.py",
    }
)

_COMPATIBILITY_TEST_PREFIXES = (
    "tests/test_chinese_context_matrix.py::test_research_driven_matrix_obeys_single_decision_contract",
    "tests/test_chinese_context_matrix.py::test_regression_cases_pass_single_decision_contract",
    "tests/test_memory_conversation_closure.py::test_candidate_batch_compatibility_api_uses_true_ids_and_reports_partial_results",
    "tests/test_memory_conversation_closure.py::test_candidate_compatibility_api_accepts_and_rejects_true_ids",
    "tests/test_memory_governance.py::test_candidate_compatibility_web_governance_api",
    "tests/test_memory_interaction_flow.py::test_candidate_compatibility_api_remains_without_normal_chat_entry",
)


def contract_lane_for(nodeid: str) -> str:
    """Return exactly one reporting lane for a collected pytest node id."""

    normalized = str(nodeid).replace("\\", "/")
    module = normalized.split("::", 1)[0]
    if module in _HISTORICAL_MODULES or normalized.startswith(
        _HISTORICAL_TEST_PREFIXES
    ):
        return HISTORICAL_BASELINE
    if module in _COMPATIBILITY_MODULES or normalized.startswith(
        _COMPATIBILITY_TEST_PREFIXES
    ):
        return COMPATIBILITY_CONTRACT
    return PRODUCTION_CONTRACT
