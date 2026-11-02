"""Stdlib-only direct-corpus runner checks; no product/Qt/network execution."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import run_beta_corpus_acceptance as corpus


def snapshot(memory=None, plans=None):
    values = [memory or {"version": 2, "profile": {}, "memories": []}, plans or {"days": {}}, {"days": {}}, {"entries": {}}]
    result = {name: {"sha256": json.dumps(value, sort_keys=True), "data": copy.deepcopy(value)}
              for name, value in zip(corpus.week.BUSINESS, values)}
    result["auxiliary_business_files"] = {}
    return result


def case(cid="probe", tools=()):
    return {"case_id": cid, "execution": {"design_status": "ready_direct"}, "prelude": [],
            "turns": [{"role": "user", "text": "合成原句", "expect": {"tools": list(tools), "success": True}}],
            "current_contract_expectation": {"rules": ["current contract retained"]}}


def confirmation_preview():
    pending = {"confirmation_id": "synthetic-confirmation", "tool": "delete_memory", "arguments": {"memory_id": 3}}
    return {"status": "confirmation_required", "message": "请确认或取消", "pending_confirmation": copy.deepcopy(pending),
            "tool_results": [{"tool": "delete_memory", "success": False, "status": "confirmation_required",
                              "error": {"code": "confirmation_required"}, "data": {"confirmation": pending}}]}


class DirectRunnerChecks(unittest.TestCase):
    def test_limits_and_no_product_imports(self):
        self.assertEqual(corpus.LIMITS["group_seconds"], 90)
        self.assertEqual(corpus.LIMITS["parent_seconds"], 7200)
        self.assertEqual(corpus.LIMITS["http"], 1000)
        self.assertEqual(corpus.LIMITS["reported_tokens_stop"], 2000000)
        # Root pytest hooks may already import Qt; inspect the runner's own
        # clean import instead of attributing the test host's modules to it.
        probe = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; from scripts import run_beta_corpus_acceptance; "
             "assert 'PySide6' not in sys.modules; "
             "assert 'server.agent_service' not in sys.modules"],
            cwd=corpus.ROOT, capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_preludes_gates_events_are_not_blindly_executed(self):
        item = case()
        self.assertIsNone(corpus.ready_reason(item))
        item["prelude"] = [{"text": "真正的自然前置尚需适配"}]
        self.assertEqual(corpus.ready_reason(item), "pending_prelude_or_gate_adapter")
        item["prelude"] = []
        item["turns"][0]["require_before"] = {"pending": "must really exist"}
        self.assertEqual(corpus.ready_reason(item), "pending_event_or_gate_adapter")
        item["turns"][0].pop("require_before")
        item["turns"][0]["role"] = "event"
        self.assertEqual(corpus.ready_reason(item), "pending_event_or_gate_adapter")

    def test_seed_resolution_keeps_source_and_only_resolves_named_tokens(self):
        seed = {"memories": [{"created_at": "$business_day_T09:00:00"}], "business_date": "$run_business_date", "profile": {"nickname": "合成姓名"}}
        resolved = corpus.resolve_seed(seed, "2026-10-23")
        self.assertEqual(resolved["memories"][0]["created_at"], "2026-10-23T09:00:00")
        self.assertEqual(seed["business_date"], "$run_business_date")
        self.assertEqual(resolved["profile"], seed["profile"])

    def test_canonical_seed_uses_public_save_before_scoring_without_loosening_null_guard(self):
        class Memory:
            def __init__(self, data):
                self.data = data
                self.saves = 0
            def save(self):
                self.saves += 1
                return True
        source = {"profile": {}, "memories": [{"id": 3, "content": "A", "location": None}]}
        manager = Memory({"profile": {}, "memories": [{"id": 3, "content": "A", "location": None, "confidence": 1.0, "scope": "preference"}]})
        corpus.persist_canonical_seed(manager, source)
        self.assertEqual(manager.saves, 1)
        manager.data["memories"][0]["location"] = "None"
        with self.assertRaises(corpus.week.Halt):
            corpus.persist_canonical_seed(manager, source)

    def test_false_success_without_tool_or_persistence_is_failure_not_wrong_write(self):
        item = case(tools=["add_plan"])
        before = snapshot()
        result = corpus.evaluate(item, item["turns"][0], {"message": "已添加", "tool_results": []}, before, before, {}, "2026-10-23")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["unsafe_write"])
        self.assertIsNone(result["human_review"]["reply_truthfulness"])

    def test_confirmation_preview_retains_returned_handler_and_write_facts_separately(self):
        response = confirmation_preview()
        untouched = copy.deepcopy(response)
        before = snapshot()
        item = case()
        checked = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23", handler_results=[])
        self.assertEqual(checked["machine_failures"], [])
        self.assertEqual(checked["frozen_machine_tool_failures"], [])
        evidence = checked["execution_evidence"]
        self.assertEqual(evidence["returned_tool_results"][0]["tool"], "delete_memory")
        self.assertEqual(evidence["confirmation_preview_result_indexes"], [0])
        self.assertEqual(evidence["non_preview_result_tools"], [])
        self.assertEqual(evidence["registered_handler_tools"], [])
        self.assertEqual(evidence["changed_business_files"], [])
        self.assertTrue(evidence["handler_observation_available"])
        self.assertIsNone(checked["human_review"]["reply_truthfulness"])
        self.assertEqual(response, untouched)

    def test_preview_exemption_requires_matching_structured_pending_and_failure(self):
        valid = confirmation_preview()
        for field in ("pending", "id", "tool", "arguments", "success", "status", "error"):
            with self.subTest(field=field):
                response = copy.deepcopy(valid)
                if field == "pending":
                    response.pop("pending_confirmation")
                elif field in {"id", "tool", "arguments"}:
                    key = {"id": "confirmation_id", "tool": "tool", "arguments": "arguments"}[field]
                    response["pending_confirmation"][key] = {"memory_id": 4} if field == "arguments" else "different"
                elif field == "error":
                    response["tool_results"][0]["error"]["code"] = "write_failed"
                else:
                    response["tool_results"][0][field] = True if field == "success" else "failed"
                self.assertFalse(corpus.is_confirmation_preview(response["tool_results"][0], response))
                item = case()
                before = snapshot()
                checked = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23", handler_results=[])
                self.assertIn("handler_response_tool_sequence_mismatch", checked["machine_failures"])

    def test_real_handler_call_cannot_be_hidden_by_missing_result_or_preview(self):
        for response in ({"message": "聊天", "tool_results": []}, confirmation_preview()):
            with self.subTest(response_status=response.get("status")):
                item = case()
                before = snapshot()
                checked = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23",
                                          handler_results=[{"tool": "delete_memory", "success": True}])
                self.assertIn("handler_response_tool_sequence_mismatch", checked["machine_failures"])
                self.assertEqual(checked["status"], "failed")

    def test_real_results_still_need_matching_handlers_in_order_and_count(self):
        item = case(tools=["list_memories"])
        response = {"message": "读取", "tool_results": [{"tool": "list_memories", "success": True, "data": {"memories": []}}]}
        before = snapshot()
        for observed in ([], [{"tool": "delete_memory"}], [{"tool": "list_memories"}]*2):
            checked = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23", handler_results=observed)
            self.assertIn("handler_response_tool_sequence_mismatch", checked["machine_failures"])
        checked = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23",
                                  handler_results=[{"tool": "list_memories"}])
        self.assertEqual(checked["machine_failures"], [])
        unknown = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23")
        self.assertFalse(unknown["execution_evidence"]["handler_observation_available"])
        self.assertIsNone(unknown["execution_evidence"]["registered_handler_tools"])

    def test_preview_cannot_hide_business_write_or_satisfy_a_required_execution(self):
        before = snapshot(memory={"memories": [{"id": 3, "content": "A"}]})
        after = snapshot()
        item = case()
        checked = corpus.evaluate(item, item["turns"][0], confirmation_preview(), before, after, {}, "2026-10-23", handler_results=[])
        self.assertTrue(checked["unsafe_write"])
        required = case(tools=["delete_memory"])
        checked = corpus.evaluate(required, required["turns"][0], confirmation_preview(), before, before, {}, "2026-10-23", handler_results=[])
        self.assertIn("current_tool_sequence_mismatch", checked["machine_failures"])

    def test_read_cannot_turn_null_into_string_none(self):
        before = snapshot(memory={"memories": [{"id": 1, "content": "A", "location": None}]})
        after = snapshot(memory={"memories": [{"id": 1, "content": "A", "location": "None"}]})
        item = case()
        result = corpus.evaluate(item, item["turns"][0], {"message": "聊天", "tool_results": []}, before, after, {}, "2026-10-23")
        self.assertTrue(result["unsafe_write"])

    def test_memory_save_cannot_mutate_unrelated_existing_record(self):
        before = snapshot(memory={"memories": [{"id": 1, "content": "A", "location": None}]})
        after = snapshot(memory={"memories": [{"id": 1, "content": "A", "location": "None"}, {"id": 2, "content": "B"}]})
        item = case("bt_mem_13", ["save_memory"])
        response = {"message": "保存", "tool_results": [{"tool": "save_formal_memory", "success": True, "data": {}}]}
        result = corpus.evaluate(item, item["turns"][0], response, before, after, {"save_memory": "save_formal_memory"}, "2026-10-23")
        self.assertIn("memory_write_modified_unrelated_existing_record", result["machine_failures"])
        self.assertTrue(result["unsafe_write"])

    def test_current_natural_memory_overlay_does_not_overwrite_old_expectation(self):
        item = case("bt_mem_06")
        before = snapshot(memory={"memories": [{"id": 1, "content": "A", "status": "active"}]})
        response = {"message": "A", "tool_results": [{"tool": "list_memories", "success": True, "data": {"memories": [{"id": 1, "content": "A"}]}}]}
        result = corpus.evaluate(item, item["turns"][0], response, before, before, {}, "2026-10-23")
        self.assertEqual(result["machine_failures"], [])
        self.assertEqual(result["frozen_original_expectation"]["tools"], [])
        self.assertIn("frozen_tool_sequence_mismatch", result["frozen_machine_tool_failures"])

    def test_non_today_plan_write_and_unstored_tool_claim_are_detected(self):
        item = case(tools=["add_plan"])
        before = snapshot()
        task = {"uid": "u", "date": "2026-10-24", "title": "合成任务"}
        after = snapshot(plans={"days": {"2026-10-24": {"tasks": [task]}}})
        response = {"message": "保存", "tool_results": [{"tool": "add_plan", "success": True, "data": {"task": task, "postcondition_verified": True}}]}
        result = corpus.evaluate(item, item["turns"][0], response, before, after, {}, "2026-10-23")
        self.assertIn("non_today_plan_mutation", result["machine_failures"])
        self.assertIn("tool_write_wrong_business_date", result["machine_failures"])

    def test_reviewed_external_design_has_sixty_direct_and_rejects_rewrite(self):
        design_path = os.environ.get("ROXYPLAN_CORPUS_DESIGN")
        if not design_path:
            self.skipTest("optional reviewed external design not supplied")
        design = corpus.load_design(design_path)
        self.assertEqual(sum(corpus.ready_reason(c) is None for c in design["cases"]), 60)
        self.assertEqual(sum(c["execution"]["design_status"] in corpus.CONDITIONAL and corpus.execution_reason(c) is None for c in design["cases"]), 51)
        base = Path(os.environ.get("ROXYPLAN_TEST_WORKSPACE", str(corpus.ROOT / "test_workspace/runs/beta-corpus-stdlib-selfcheck"))) / "design-selfcheck"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path = Path(directory) / "design.json"
            bad = copy.deepcopy(design)
            bad["cases"][0]["turns"][0]["text"] += " altered"
            corpus.week.write_json(path, bad)
            with self.assertRaises(ValueError):
                corpus.load_design(path)
            bad = copy.deepcopy(design)
            bad["cases"][0]["seed"]["pending"] = {"invented": True}
            corpus.week.write_json(path, bad)
            with self.assertRaises(ValueError):
                corpus.load_design(path)

    def test_unknown_gate_and_event_fail_closed_without_changing_direct_selection(self):
        item = case()
        item["execution"]["design_status"] = "conditional_in_sequence"
        item["turns"][0]["require_before"] = {"valid_today_suggestion_snapshot": True}
        self.assertIsNone(corpus.execution_reason(item))
        self.assertIsNotNone(corpus.ready_reason(item))
        item["turns"][0]["require_before"]["unknown_future_condition"] = True
        self.assertIn("pending_gate_adapter", corpus.execution_reason(item))
        self.assertFalse(corpus.check_gates({"unknown_future_condition": True}, {"unknown_future_condition": True})["passed"])
        item["turns"][0]["role"] = "event"
        self.assertEqual(corpus.execution_reason(item), "pending_event_adapter")

    def test_suggestion_gate_requires_exact_count_title_order_and_real_provenance(self):
        requirement = {"suggestion_count": 2, "required_titles_in_order": ["A", "B"], "suggestion_source": "real_assistant_reply", "valid_today_session_snapshot": True}
        observation = {"suggestion_count": 2, "suggestion_titles": ["A", "B"], "suggestion_source": "real_assistant_reply", "valid_today_session_snapshot": True}
        self.assertTrue(corpus.check_gates(requirement, observation)["passed"])
        for changed in ({"suggestion_count": 3}, {"suggestion_titles": ["B", "A"]}, {"suggestion_source": None}, {"valid_today_session_snapshot": False}):
            self.assertFalse(corpus.check_gates(requirement, {**observation, **changed})["passed"])

    def test_pending_confirmation_and_read_gates_need_observed_fields(self):
        needed = {"pending_tool": "delete_memory", "confirmation_pending": True, "business_writes": 0}
        self.assertFalse(corpus.check_gates(needed, {})["passed"])
        self.assertTrue(corpus.check_gates(needed, {"pending_tool": "delete_memory", "confirmation_pending": True, "business_writes": 0})["passed"])
        self.assertFalse(corpus.check_gates({"confirmation_pending": False}, {})["passed"])
        read = {"valid_plan_read_snapshot": True, "display_order": 2, "read_snapshot_source": "real_tool_result"}
        observation = {"valid_plan_read_snapshot": True, "read_display_orders": [1, 2], "read_snapshot_source": "real_tool_result"}
        self.assertTrue(corpus.check_gates(read, observation)["passed"])
        self.assertFalse(corpus.check_gates(read, {**observation, "read_snapshot_source": None})["passed"])

    def test_prelude_write_is_failure_not_harmless_blocked_and_original_is_unchanged(self):
        item = case()
        item["execution"]["design_status"] = "conditional_natural_prelude"
        spec = {"role": "user", "text": "先建议，不要加入", "allowed_prelude_writes": "none", "require": {"business_writes": 0}}
        before = snapshot()
        after = snapshot(plans={"days": {"2026-10-23": {"tasks": [{"uid": "bad", "title": "不应写入", "date": "2026-10-23"}]}}})
        frozen = copy.deepcopy(item["turns"])
        checks = corpus.evaluate_prelude(item, spec, {"message": "建议", "tool_results": []}, before, after, {}, "2026-10-23")
        self.assertTrue(checks["unsafe_write"])
        self.assertEqual(checks["status"], "failed")
        self.assertEqual(item["turns"], frozen)
        self.assertNotIn("frozen_original_expectation", checks)

    def test_consumed_original_snapshot_and_previous_result_gates(self):
        requirement = {"original_snapshot_still_valid": True, "first_option_consumed": True, "previous_successful_tool": "add_plan", "previous_business_writes": 0}
        observation = {"original_snapshot_still_valid": True, "first_option_consumed": True, "successful_tools": ["add_plan"], "business_writes": 0}
        self.assertTrue(corpus.check_gates(requirement, observation)["passed"])
        self.assertFalse(corpus.check_gates(requirement, {**observation, "original_snapshot_still_valid": False})["passed"])

    def test_prelude_and_original_rounds_have_separate_counts(self):
        raw = {"group": "x", "status": "blocked_precondition", "completed_turns": {"x-pre01": "checked", "x-pre02": "blocked_gate", "x-t02": "checked"}, "completed_round_count": 3, "completed_message_count": 6}
        summary = corpus.phase_summary(raw)
        self.assertEqual(summary["completed_round_count"], 1)
        self.assertEqual(summary["original_completed_round_count"], 1)
        self.assertEqual(summary["completed_message_count"], 2)
        self.assertEqual(summary["prelude_completed_round_count"], 2)
        self.assertEqual(summary["supervisor_all_phase_completed_round_count"], 3)
        self.assertEqual(len(raw["completed_turns"]), 3)

    def test_all_records_keep_not_selected_and_excluded_reasons(self):
        items = [case("direct"), case("conditional"), case("historical")]
        items[1]["execution"]["design_status"] = "conditional_in_sequence"
        items[2]["execution"]["design_status"] = "excluded_historical"
        records = corpus.account_records({"cases": items}, {"pending_or_excluded": {"historical": "excluded_historical"}}, ["conditional"], [])
        self.assertEqual([r["status"] for r in records], ["not_selected_this_run", "not_started", "not_executable_in_this_adapter"])
        self.assertTrue(all(r["original_case_pass"] is None for r in records))

    def test_accounting_distinguishes_complete_partial_prelude_only_and_no_rounds(self):
        items = [case(cid) for cid in ("complete", "partial", "prelude", "empty")]
        items[1]["turns"] *= 3
        summaries = [
            {"group": cid, "status": "blocked_precondition", "completed_round_count": original,
             "completed_turns": {f"{cid}-t{i:02}": "checked" for i in range(1, original + 1)},
             "prelude_completed_round_count": prelude}
            for cid, original, prelude in (("complete", 1, 0), ("partial", 1, 2), ("prelude", 0, 1), ("empty", 0, 0))
        ]
        untouched = copy.deepcopy(summaries)
        records = corpus.account_records({"cases": items}, {"pending_or_excluded": {}}, [c["case_id"] for c in items], summaries)
        self.assertEqual([r["status"] for r in records], [
            "original_completed_review_required", "original_partially_completed_review_required",
            "prelude_only_original_not_executed", "not_started",
        ])
        self.assertEqual([r["original_expected_round_count"] for r in records], [1, 3, 1, 1])
        self.assertTrue(all(r["original_case_pass"] is None for r in records))
        self.assertEqual(summaries, untouched)
        explicit = {"group": "partial", "status": "failed", "original_completed_round_count": 1, "completed_round_count": 3}
        record = corpus.account_records({"cases": [items[1]]}, {"pending_or_excluded": {}}, ["partial"], [explicit])[0]
        self.assertEqual(record["status"], "original_partially_completed_review_required")
        self.assertEqual(record["original_completed_round_count"], 1)

    def test_equal_count_with_wrong_original_turn_id_is_not_complete(self):
        item = case("probe")
        summary = {"group": "probe", "status": "completed", "completed_round_count": 1,
                   "completed_turns": {"probe-t99": "checked"}}
        record = corpus.account_records({"cases": [item]}, {"pending_or_excluded": {}}, ["probe"], [summary])[0]
        self.assertEqual(record["status"], "original_partially_completed_review_required")
        self.assertEqual(record["missing_original_turn_ids"], ["probe-t01"])
        self.assertEqual(record["unexpected_original_turn_ids"], ["probe-t99"])

    def test_conditional_memory_delete_keeps_all_other_records_protected(self):
        item = case("btm_10", ["delete_memory"])
        item["execution"]["design_status"] = "conditional_in_sequence"
        a, b = {"id": 1, "content": "A"}, {"id": 2, "content": "B", "location": None}
        before = snapshot(memory={"memories": [a, b]})
        after = snapshot(memory={"memories": [b]})
        response = {"message": "删除A", "tool_results": [{"tool": "delete_memory", "success": True, "data": {"memory": a}}]}
        checked = corpus.evaluate(item, item["turns"][0], response, before, after, {}, "2026-10-23")
        self.assertFalse(checked["unsafe_write"])
        self.assertEqual(checked["machine_failures"], [])
        after = snapshot(memory={"memories": [{**b, "location": "None"}]})
        self.assertTrue(corpus.evaluate(item, item["turns"][0], response, before, after, {}, "2026-10-23")["unsafe_write"])

    def test_conditional_completion_cannot_change_unselected_uid(self):
        item = case("btm_15", ["complete_plan"])
        item["execution"]["design_status"] = "conditional_natural_prelude"
        a = {"uid": "a", "title": "A", "date": "2026-10-23", "done": False, "status": "pending"}
        b = {"uid": "b", "title": "B", "date": "2026-10-23", "done": False, "status": "pending"}
        completed = {**a, "done": True, "status": "completed"}
        before = snapshot(plans={"days": {"2026-10-23": {"tasks": [a, b]}}})
        after = snapshot(plans={"days": {"2026-10-23": {"tasks": [completed, b]}}})
        response = {"message": "完成A", "tool_results": [{"tool": "complete_plan", "success": True, "data": {"task": completed, "postcondition_verified": True}}]}
        self.assertEqual(corpus.evaluate(item, item["turns"][0], response, before, after, {}, "2026-10-23")["machine_failures"], [])
        after = snapshot(plans={"days": {"2026-10-23": {"tasks": [completed, {**b, "title": "擅改"}]}}})
        self.assertTrue(corpus.evaluate(item, item["turns"][0], response, before, after, {}, "2026-10-23")["unsafe_write"])


class CapturedConfirmationChecks(unittest.TestCase):
    """Optional immutable online capture replay; never sends a new request."""

    def capture_root(self):
        path = os.environ.get("ROXYPLAN_CORPUS_CAPTURE_ROOT")
        if not path:
            self.skipTest("optional original capture directory not supplied")
        return Path(path)

    def test_original_confirmation_previews_are_not_handler_execution(self):
        root = self.capture_root()
        design = json.loads((root / "frozen-design.json").read_text(encoding="utf-8"))
        by_id = {c["case_id"]: c for c in design["cases"]}
        ids = ("bt_mem_07-t01", "btm_10-t02", "btm_10-t04", "btm_11-t02", "bt_conf_05-pre01", "bt_neg_07-pre01")
        for tid in ids:
            with self.subTest(turn=tid):
                cid, suffix = tid.rsplit("-", 1)
                directory = root / cid / "turns" / tid
                read = lambda name: json.loads((directory / name).read_text(encoding="utf-8"))
                recorded, before, after = read("result.json"), read("before.json"), read("after.json")
                case_item = by_id[cid]
                index = int(suffix.removeprefix("pre").removeprefix("t")) - 1
                self.assertEqual(recorded["registered_handler_results"], [])
                self.assertEqual(recorded["checks"]["changed_business_files"], [])
                self.assertIn("handler_response_tool_sequence_mismatch", recorded["checks"]["machine_failures"])
                if suffix.startswith("pre"):
                    checked = corpus.evaluate_prelude(case_item, case_item["prelude"][index], recorded["final_response"], before, after, {}, recorded["business_date"], handler_results=recorded["registered_handler_results"])
                else:
                    checked = corpus.evaluate(case_item, case_item["turns"][index], recorded["final_response"], before, after, {}, recorded["business_date"], handler_results=recorded["registered_handler_results"])
                self.assertEqual(checked["machine_failures"], [])
                self.assertFalse(checked["unsafe_write"])
                self.assertEqual(checked["execution_evidence"]["confirmation_preview_result_indexes"], [0])
                self.assertEqual(checked["execution_evidence"]["registered_handler_tools"], [])

    def test_original_129_record_accounting_keeps_blocked_targets_unexecuted(self):
        root = self.capture_root()
        design = json.loads((root / "frozen-design.json").read_text(encoding="utf-8"))
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        original_bytes = (root / "summary.json").read_bytes()
        records = corpus.account_records(design, {"pending_or_excluded": summary["other_records"]}, summary["selected_case_ids"], summary["cases"])
        self.assertEqual(len(records), 129)
        self.assertEqual(sum(r["status"] == "prelude_only_original_not_executed" for r in records), 15)
        self.assertEqual(sum(r["original_completed_round_count"] > 0 for r in records), 96)
        self.assertEqual(sum(r["original_completed_round_count"] for r in records), 128)
        self.assertEqual(sum(r["prelude_completed_round_count"] for r in records), 42)
        self.assertEqual(sum(r["status"] == "not_executable_in_this_adapter" for r in records), 18)
        by_id = {r["case_id"]: r for r in records}
        for cid in ("bt_conf_05", "bt_neg_07"):
            self.assertEqual(by_id[cid]["original_completed_round_count"], 0)
            self.assertEqual(by_id[cid]["status"], "prelude_only_original_not_executed")
            self.assertTrue(by_id[cid]["blocked_original_turns"])
        self.assertTrue(all(r["original_case_pass"] is None for r in records))
        totals = corpus.account_totals(records)
        self.assertEqual(totals["records_denominator"], 129)
        self.assertEqual(totals["attempted_case_count"], 111)
        self.assertEqual(totals["original_started_case_count"], 96)
        expected_lengths = {c["case_id"]: len(c["turns"]) for c in design["cases"]}
        for record in records:
            count, expected = record["original_completed_round_count"], expected_lengths[record["case_id"]]
            if count:
                wanted = "original_completed_review_required" if count == expected else "original_partially_completed_review_required"
                self.assertEqual(record["status"], wanted)
        self.assertEqual((root / "summary.json").read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
