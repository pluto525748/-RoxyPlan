"""Small stdlib-only harness self-checks; no Qt/product/model imports.

Can be run with unittest for the explicitly authorized preparation self-check.
Future pytest runs must use scripts/run_isolated_tests.ps1 and a scheduled slot.
These checks validate the evaluator, not RoxyPlan or DeepSeek performance.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts import learning_week_plan as plan_module
from scripts import run_learning_week_acceptance as harness


def hung_worker(connection, _config):
    connection.send({"event": "turn_started", "turn": "g1-d1-t01"})
    connection.recv()
    time.sleep(60)


def permission_probe(connection, config):
    connection.send({"event": "http_request", "turn": "probe", "request": {"model": "synthetic_no_network"}})
    permit = connection.recv()
    evidence = Path(config["root"]) / "http-and-control.jsonl"
    stored = json.loads(evidence.read_text(encoding="utf-8").splitlines()[0])
    if not permit.get("permit") or stored["request_id"] != permit["request_id"]:
        connection.send({"event": "worker_error", "error_type": "request_not_durable_before_permission"})
        return
    connection.send({"event": "http_response", "response": {"usage": {"total_tokens": 7}}})
    connection.recv()
    connection.send({"event": "group_finished", "status": "synthetic_protocol_only_no_http"})


def blocked_then_failure_probe(connection, _config):
    connection.send({"event": "turn_blocked", "turn": "g1-d1-t01", "missing_dependency": "advice"})
    connection.send({"event": "turn_started", "turn": "g1-d1-t02"})
    connection.recv()
    connection.send({"event": "turn_completed", "turn": "g1-d1-t02", "status": "failed"})
    connection.send({"event": "group_finished", "status": "synthetic_safe_continuation"})


def snap(plan=None, memory=None):
    values = [memory or {"memories": []}, plan or {"days": {}}, {"days": {}}, {"entries": {}}]
    return {name: {"sha256": json.dumps(value, sort_keys=True), "data": copy.deepcopy(value)}
            for name, value in zip(harness.BUSINESS, values)}


class PlanChecks(unittest.TestCase):
    def test_exact_round_counts_and_profiles(self):
        plan = plan_module.build_plan()
        self.assertEqual(plan_module.validate_plan(plan), 630)
        self.assertEqual(len({g["goal"] for g in plan["groups"]}), 6)
        self.assertEqual(len({g["preference"] for g in plan["groups"]}), 6)
        self.assertTrue(all(len(d["turns"]) == 15 for g in plan["groups"] for d in g["days"]))
        self.assertEqual({d["trajectory"]["expected_core_completed"] for g in plan["groups"] for d in g["days"]}, {0, 1, 2})
        self.assertIn(0, {d["trajectory"]["expected_core_action_count"] for g in plan["groups"] for d in g["days"]})
        self.assertIn(None, {d["trajectory"]["action_minutes"] for g in plan["groups"] for d in g["days"]})

    def test_each_original_corpus_record_disposition_without_modification(self):
        before = plan_module.digest(plan_module.CORPUS)
        audit = plan_module.audit_corpus(plan_module.build_plan())
        self.assertEqual(len(audit["records"]), 129)
        self.assertEqual(len({r["case_id"] for r in audit["records"]}), 129)
        self.assertTrue(all(r["execution_status"] == "not_run" for r in audit["records"]))
        for cid in ("bt_today_add_14", "bt_cd_09", "bt_cd_10"):
            row = next(r for r in audit["records"] if r["case_id"] == cid)
            self.assertEqual(row["contract_difference"], "current_contract_overlay")
        self.assertEqual(before, plan_module.digest(plan_module.CORPUS))

    def test_plan_only_references_registered_tool_names(self):
        # AST reading avoids importing the product (and all constructors).
        tree = ast.parse((harness.ROOT / "modules/tool_registry.py").read_text(encoding="utf-8"))
        registered = {node.args[0].value for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == "ToolDefinition" and node.args and isinstance(node.args[0], ast.Constant)}
        used = {tool for g in plan_module.build_plan()["groups"] for d in g["days"] for t in d["turns"] for tool in t["expected"]["tools"]}
        self.assertFalse(used - registered)

    def test_reject_count_day_drift_and_unsafe_budget(self):
        plan = plan_module.build_plan()
        plan["groups"][0]["days"][0]["turns"].pop()
        with self.assertRaises(ValueError):
            plan_module.validate_plan(plan)
        plan = plan_module.build_plan()
        plan["groups"][0]["days"][1]["date"] = "2026-12-01"
        with self.assertRaises(ValueError):
            plan_module.validate_plan(plan)
        for key in plan["limits"]:
            plan = plan_module.build_plan()
            plan["limits"][key] += 1
            with self.assertRaises(ValueError):
                plan_module.validate_plan(plan)


class EvidenceChecks(unittest.TestCase):
    def test_confirmation_preview_and_negated_preflight_are_not_handlers(self):
        pending = {"confirmation_id": "preview", "tool": "delete_memory", "arguments": {"memory_id": 2}}
        preview = {"status": "confirmation_required", "message": "请确认", "pending_confirmation": pending,
                   "tool_results": [{"tool": "delete_memory", "success": False, "status": "confirmation_required",
                                     "error": {"code": "confirmation_required"}, "data": {"confirmation": pending}}]}
        state = snap()
        checked = harness.check_turn(plan_module.step("忘记：合成事实"), state, state, preview,
                                     [], [], [], [], "2026-11-02", handler_results=[])
        self.assertEqual(checked["failures"], [])
        self.assertEqual(checked["execution_evidence"]["confirmation_preview_result_indexes"], [0])
        negated = {"message": "本次未执行", "tool_results": [
            {"tool": "list_memories", "success": False, "status": "failed", "error": {"code": "negated_request"}}]}
        checked = harness.check_turn(plan_module.step("不要读取"), state, state, negated,
                                     [], [], [], [], "2026-11-02", handler_results=[])
        self.assertEqual(checked["failures"], [])
        # The same error label must never hide an actual handler invocation.
        checked = harness.check_turn(plan_module.step("不要读取"), state, state, negated,
                                     [], [], [], [], "2026-11-02", handler_results=[{"tool": "list_memories"}])
        self.assertTrue(checked["failures"])

    def test_memory_probe_checks_returned_fact_not_only_database(self):
        active = "我现在更适合晚上学习"
        state = snap(memory={"memories": [{"id": 3, "content": active, "status": "active"}]})
        turn = plan_module.step("我更适合什么时候学习", tools=["list_memories"],
                                memory_probe={"active": active, "inactive": "我更适合早上学习"})
        response = {"message": "没有相关事实", "tool_results": [{"tool": "list_memories", "success": True,
                    "data": {"memories": [], "memory_read": {"facts": []}}}]}
        checked = harness.check_turn(turn, state, state, response, [], [], [], [], "2026-11-02")
        self.assertIn("corrected_memory_read_omits_active_fact", checked["failures"])

    def test_only_visible_model_response_and_api_reported_usage(self):
        raw = {"model": "model", "headers": {"secret": "drop"}, "choices": [{"message": {
            "content": "<think>hidden</think>visible", "reasoning_content": "private"}}],
            "usage": {"total_tokens": 8, "prompt_tokens": -1, "arbitrary": "secret"}}
        visible = harness.public_response(raw)
        self.assertEqual(visible["choices"][0]["message"]["content"], "visible")
        self.assertEqual(visible["usage"], {"total_tokens": 8})
        self.assertNotIn("private", json.dumps(visible))
        self.assertNotIn("secret", json.dumps(visible))

    def test_request_drops_reasoning_headers_and_credentials_fields(self):
        safe = harness.public_payload({"model": "x", "Authorization": "private-key", "reasoning": "hidden",
            "messages": [{"role": "assistant", "content": "<think>hide", "reasoning_content": "private"}]})
        self.assertNotIn("private", json.dumps(safe))
        self.assertNotIn("hide", json.dumps(safe))

    def test_budget_reservation_and_unknown_usage_are_not_estimated(self):
        budget = harness.Budget({"http": 2, "reported_tokens_stop": 20})
        self.assertTrue(budget.reserve())
        budget.observe({"usage": {"total_tokens": 20}})
        self.assertFalse(budget.reserve())
        budget.observe({"usage": {"prompt_tokens": 9}})
        self.assertEqual(budget.report()["reported_total_tokens"], 20)
        self.assertEqual(budget.report()["responses_with_unknown_usage"], 1)

    def test_zero_write_contract_catches_actual_unauthorized_write(self):
        before, after = snap(), snap({"days": {"2026-11-02": {"tasks": [{"uid": "bad"}]}}})
        result = harness.check_turn(plan_module.step("不要保存"), before, after, {"message": "没保存", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertTrue(result["stop_group_for_wrong_write"])
        self.assertIn("unauthorized_business_change:" + harness.BUSINESS[1], result["failures"])

    def test_success_words_do_not_replace_missing_tool_or_write(self):
        before = snap()
        turn = plan_module.step("第一个加入今天计划", tools=["add_plan"], change="plan_add")
        checks = harness.check_turn(turn, before, before, {"message": "已成功添加", "tool_results": []}, [], [], [], [], "2026-11-02", "题目")
        self.assertEqual(checks["status"], "failed")
        self.assertIn("expected_write_missing", checks["failures"])
        self.assertFalse(checks["stop_group_for_wrong_write"], "safe business failure must not reset/stop the whole group")

    def test_ambiguous_authorization_remains_pending_but_action_write_forbidden(self):
        turn = plan_module.step("我准备晚上学一会儿", change="deferred_plan_authorization", adjudication="deferred", tool_sequences=[[], ["add_plan"]])
        before = snap()
        checks = harness.check_turn(turn, before, before, {"message": "讨论", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertEqual(checks["status"], "pending_adjudication")
        after = snap()
        after[harness.BUSINESS[2]] = {"sha256": "changed", "data": {"days": {"2026-11-02": {"records": [{"content": "已学"}]}}}}
        checks = harness.check_turn(turn, before, after, {"message": "完成", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertTrue(checks["stop_group_for_wrong_write"])

    def test_memory_usage_metadata_separate_from_content_change(self):
        before = snap(memory={"memories": [{"id": 1, "content": "目标", "use_count": 0}]})
        after = snap(memory={"memories": [{"id": 1, "content": "目标", "use_count": 1}]})
        args = (plan_module.step("聊一聊"), before, after, {"message": "好", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertEqual(harness.check_turn(*args)["failures"], [])
        after["memory.json"]["data"]["memories"][0]["content"] = "擅改"
        result = harness.check_turn(*args)
        self.assertTrue(result["stop_group_for_wrong_write"])

    def test_legitimate_completion_fields_are_not_false_failure(self):
        old = [{"uid": f"u{i}", "date": "2026-11-02", "title": str(i), "status": "pending", "done": False, "done_at": None, "updated_at": "a"} for i in range(2)]
        new = [dict(t, status="completed", done=True, done_at="b", updated_at="b") for t in old]
        before, after = snap({"days": {"2026-11-02": {"tasks": old}}}), snap({"days": {"2026-11-02": {"tasks": new}}})
        results = [{"success": True, "tool": "complete_plan", "data": {"task": t, "postcondition_verified": True}} for t in new]
        checked = harness.check_turn(plan_module.step("确认", tools=["complete_plan"]*2, change="plan_complete"), before, after, {"message": "完成", "tool_results": results}, old, new, [], [], "2026-11-02")
        self.assertEqual(checked["failures"], [])
        self.assertIsNone(checked["human_review"]["reply_truthfulness"])
        partial = [new[0], old[1]]
        after_partial = snap({"days": {"2026-11-02": {"tasks": partial}}})
        turn = plan_module.step("完成第一项", tools=["complete_plan"], change="plan_complete", completion_selection=1)
        turn["expected"]["complete_count"] = 1
        checked = harness.check_turn(turn, before, after_partial, {"message": "只完成第一项", "tool_results": results[:1]}, old, partial, [], [], "2026-11-02")
        self.assertEqual(checked["failures"], [])

    def test_allowed_today_write_cannot_hide_other_day_change(self):
        before = snap({"days": {"2026-11-01": {"tasks": []}}})
        after = snap({"days": {"2026-11-01": {"tasks": [{"uid": "foreign"}]}}})
        checked = harness.check_turn(plan_module.step("加入今天", tools=["add_plan"], change="plan_add"), before, after, {"message": "好", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertTrue(any(f.startswith("unauthorized_other_day_change:") for f in checked["failures"]))

    def test_hidden_auxiliary_candidate_write_is_detected(self):
        before, after = snap(), snap()
        after["auxiliary_business_files"] = {"data/private/memory_candidates.json": "unexpected"}
        checked = harness.check_turn(plan_module.step("只是闲聊"), before, after, {"message": "好", "tool_results": []}, [], [], [], [], "2026-11-02")
        self.assertTrue(checked["stop_group_for_wrong_write"])

    def test_explicit_memory_display_requires_all_ids_but_natural_answer_can_subset(self):
        state = snap(memory={"memories": [{"id": 1, "content": "目标", "status": "active"}, {"id": 2, "content": "偏好", "status": "active"}]})
        response = {"message": "列表", "tool_results": [{"tool": "list_memories", "success": True, "data": {"memories": []}}]}
        checked = harness.check_turn(plan_module.step("展示长期记忆", tools=["list_memories"]), state, state, response, [], [], [], [], "2026-11-02")
        self.assertIn("explicit_memory_display_incomplete_id_set", checked["failures"])
        response["tool_results"][0]["data"]["memories"] = [{"id": 1, "content": "目标"}]
        checked = harness.check_turn(plan_module.step("我的目标是什么？", tools=["list_memories"]), state, state, response, [], [], [], [], "2026-11-02")
        self.assertEqual(checked["failures"], [])

    def test_correction_requires_real_same_id_update_not_protection_of_old_fact(self):
        before = snap(memory={"memories": [{"id": 1, "content": "A", "status": "active"}]})
        after = snap(memory={"memories": [{"id": 1, "content": "B", "status": "active"}]})
        turn = plan_module.step("记住B", tools=["save_formal_memory"], change="memory_update", expected_old_memory="A", expected_new_memory="B")
        result = {"message": "更新", "tool_results": [{"tool": "save_formal_memory", "success": True, "data": {"memory_operation": {"data": {"operation": "updated"}}}}]}
        checked = harness.check_turn(turn, before, after, result, [], [], [], [], "2026-11-02")
        self.assertEqual(checked["failures"], [])
        after["memory.json"]["data"]["memories"][0]["id"] = 2
        checked = harness.check_turn(turn, before, after, result, [], [], [], [], "2026-11-02")
        self.assertIn("memory_correction_not_same_id_or_changed_other_records", checked["failures"])


class ParentChecks(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("ROXYPLAN_TEST_WORKSPACE", harness.ROOT / "test_workspace/runs/learning-week-stdlib-selfcheck"))
        base.mkdir(parents=True, exist_ok=True)
        self.workspace = tempfile.TemporaryDirectory(prefix="harness-", dir=base)
        self.addCleanup(self.workspace.cleanup)

    def config(self, name, turn_seconds):
        limits = dict(plan_module.build_plan()["limits"], turn_seconds=turn_seconds, group_seconds=5)
        return {"root": str(Path(self.workspace.name)/name), "limits": limits}

    def test_parent_stops_only_own_hung_worker_without_socket_timeout(self):
        config = self.config("hung", 0.6)
        started = time.monotonic()
        result = harness.supervise(plan_module.build_plan()["groups"][0], config, harness.Budget(config["limits"]), started+5, _worker_target=hung_worker)
        self.assertIn(result["status"], {"parent_turn_hard_deadline", "worker_startup_deadline"})
        self.assertFalse(result["worker_alive_after_cleanup"])
        self.assertEqual(len(result["completed_turns"]), 0)
        self.assertLess(time.monotonic()-started, 4)

    def test_parent_request_is_durable_before_network_permission(self):
        config = self.config("gate", 3)
        budget = harness.Budget(config["limits"])
        result = harness.supervise(plan_module.build_plan()["groups"][0], config, budget, time.monotonic()+5, _worker_target=permission_probe)
        self.assertEqual(result["status"], "synthetic_protocol_only_no_http")
        self.assertEqual(budget.http, 1)
        self.assertEqual(budget.tokens, 7)
        self.assertFalse(result["worker_alive_after_cleanup"])

    def test_blocked_input_does_not_gain_a_reply_or_completed_round(self):
        config = self.config("blocked", 3)
        result = harness.supervise(plan_module.build_plan()["groups"][0], config, harness.Budget(config["limits"]), time.monotonic()+5, _worker_target=blocked_then_failure_probe)
        self.assertEqual(result["completed_round_count"], 1)
        self.assertEqual(result["completed_message_count"], 2)
        self.assertEqual(result["blocked_turns_without_reply"], {"g1-d1-t01": "advice"})
        self.assertEqual(result["status_counts"], {"failed": 1})


if __name__ == "__main__":
    unittest.main()
