from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.run_online_chinese_semantic_acceptance import SELECTED_CASE_IDS
from tests.contract_taxonomy import (
    COMPATIBILITY_CONTRACT,
    HISTORICAL_BASELINE,
    PRODUCTION_CONTRACT,
    contract_lane_for,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CURRENT_STRUCTURED_FIELDS = {
    "subject",
    "polarity",
    "modality",
    "request_mode",
    "explicit_command",
}


def test_contract_taxonomy_separates_current_compatibility_and_history():
    assert contract_lane_for(
        "tests/test_candidate_flow_retirement.py::test_candidate_intent_from_llm_is_rejected_by_normal_main_chain"
    ) == PRODUCTION_CONTRACT
    assert contract_lane_for(
        "tests/test_memory_interaction_flow.py::test_candidate_compatibility_api_remains_without_normal_chat_entry"
    ) == COMPATIBILITY_CONTRACT
    assert contract_lane_for(
        "tests/test_v21_blind_runner.py::test_blind_set_single_decision_contract[write_009]"
    ) == HISTORICAL_BASELINE


def test_contract_taxonomy_is_path_separator_independent():
    assert contract_lane_for(
        r"tests\test_v21_optimization_runner.py::test_optimization_set_single_decision_contract[chat_001]"
    ) == HISTORICAL_BASELINE


def test_v21_model_driven_reschedule_evidence_is_historical():
    assert contract_lane_for(
        "tests/test_v21_replay_acceptance.py::test_optimization_v2_case[opt_v2_006]"
    ) == HISTORICAL_BASELINE


def test_v22_closure_regressions_and_local_duplicate_entry_are_production():
    production_nodes = (
        "tests/test_v22_latest_plan_state_regressions.py::test_semantic_completion_ordinal_uses_recent_snapshot_for_new_synonym",
        "tests/test_v22_latest_growth_regressions.py::test_explicit_generate_today_review_saves_and_refreshes_verified_snapshot",
        "tests/test_v22_delete_action_regressions.py::test_model_delete_write_binds_last_plan_then_confirms_once",
        "tests/test_v22_history_context.py::test_normal_chat_prompt_gets_only_relevant_evidence_with_implicit_use_policy",
        "tests/test_interaction_diagnostics.py::test_disabled_diagnostics_ignore_persist_path_and_do_not_write",
        "tests/test_duplicate_plan_resolution.py::test_inspection_lists_groups_and_confirmed_merge_is_conservative",
    )
    assert all(
        contract_lane_for(nodeid) == PRODUCTION_CONTRACT
        for nodeid in production_nodes
    )


def test_model_driven_duplicate_and_complex_merge_evidence_is_historical():
    historical_nodes = (
        "tests/test_duplicate_plan_resolution.py::test_merge_keeps_completed_status_only_when_every_source_is_completed",
        "tests/test_duplicate_plan_resolution.py::test_duplicate_inspection_reports_no_group_without_creating_pending",
        "tests/test_duplicate_plan_resolution.py::test_merge_conflict_asks_for_value_before_confirmation",
        "tests/test_plan_last_task_reference.py::test_model_last_task_reference_uses_successful_task_before_schema_validation",
    )
    assert all(
        contract_lane_for(nodeid) == HISTORICAL_BASELINE
        for nodeid in historical_nodes
    )
    assert contract_lane_for(
        "tests/test_v21_replay_acceptance.py::test_optimization_v2_summary"
    ) == HISTORICAL_BASELINE


def test_current_online_contract_has_explicit_v22_fields_for_every_selected_case():
    fixture = json.loads(
        (FIXTURES / "v22_online_semantic_contract.json").read_text(encoding="utf-8")
    )

    assert fixture["schema_version"] == "2.2"
    assert set(fixture["cases"]) == set(SELECTED_CASE_IDS)
    for case_id, fields in fixture["cases"].items():
        assert CURRENT_STRUCTURED_FIELDS.issubset(fields), case_id
        assert isinstance(fields["explicit_command"], bool), case_id


def test_frozen_v21_manifests_are_not_misreported_as_current_matrix_identity():
    blind = json.loads(
        (FIXTURES / "v21_blind_set.json").read_text(encoding="utf-8")
    )
    optimization = json.loads(
        (FIXTURES / "v21_optimization_set.json").read_text(encoding="utf-8")
    )
    current_bytes = (FIXTURES / "v21_chinese_context_matrix.json").read_bytes()
    current_sha = hashlib.sha256(current_bytes).hexdigest()

    assert blind["source_matrix_sha256"] == optimization["source_matrix_sha256"]
    assert current_sha != blind["source_matrix_sha256"]
