"""Synthetic stdlib-only accounting/IPC checks; never a provider request."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.online_acceptance_budget import SharedBudget
from scripts import run_learning_week_acceptance as week
from scripts import run_resumed_online_acceptance as gate


def failed_http_worker(connection, _config):
    connection.send({"event": "turn_started", "turn": "one"})
    connection.recv()
    connection.send({"event": "http_request", "request": {"max_tokens": 1}})
    permit = connection.recv()
    connection.send({"event": "http_error", "request_id": permit["request_id"]})
    connection.recv()
    # This second request must never receive network permission.
    connection.send({"event": "http_request", "request": {"max_tokens": 1}})
    connection.recv()


class SharedBudgetChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"
        self.limits = {"http": 20, "reported_tokens_stop": 100000,
                       "turn_seconds": 1, "group_seconds": 2}

    def open(self, **kw):
        args = dict(used_http=17, used_tokens=42836, remaining_http=1483,
                    remaining_tokens=2957164, stage="first", source_files={"a": "hash"},
                    source_check=lambda: True, token_reserve=20000)
        args.update(kw)
        return SharedBudget(self.path, self.limits, **args)

    def test_reservation_is_durable_and_next_stage_keeps_prior_consumption(self):
        b = self.open()
        self.assertTrue(b.reserve({"max_tokens": 1500}))
        s = json.loads(self.path.read_text("utf-8"))
        self.assertEqual(s["http_reserved"], 18)
        self.assertEqual(s["in_flight"], "http-00018")
        self.assertTrue(b.dispatched(b.request_id))
        self.assertTrue(b.observe({"usage": {"total_tokens": 1000}}, request_id=b.request_id))
        b.close()
        second = self.open(stage="second", used_http=18, used_tokens=43836,
                           remaining_http=1482, remaining_tokens=2956164)
        self.assertEqual(second.http, 0)  # local count is not the shared balance
        self.assertEqual(second.report()["shared_http_reserved"], 18)
        self.assertEqual(second.report()["shared_reported_total_tokens"], 43836)
        second.close()

    def test_stale_budget_and_caps_reset_fail_without_changing_ledger(self):
        b = self.open(); b.reserve(); b.observe({"usage": {"total_tokens": 2}}); b.close()
        before = self.path.read_bytes()
        for kw in ({}, {"used_http": 18, "remaining_http": 1483}):
            with self.assertRaises(ValueError):
                self.open(stage="second", **kw)
            self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_usage_latches_all_future_stages(self):
        b = self.open(); b.reserve()
        self.assertFalse(b.observe({"usage": {"prompt_tokens": 12}}))
        self.assertFalse(b.reserve())
        self.assertEqual(b.tokens, 0)
        self.assertEqual(b.report()["responses_with_unknown_usage"], 1)
        b.close()
        with self.assertRaises(ValueError):
            self.open(stage="second", used_http=18, remaining_http=1482)

    def test_http_error_keeps_possible_charge_and_no_completed_response(self):
        b = self.open(); b.reserve(); b.dispatched(b.request_id)
        self.assertFalse(b.observe({}, event="http_error", request_id=b.request_id))
        self.assertEqual(b.report()["requests_without_completed_response"], 1)
        self.assertEqual(b.report()["responses_with_unknown_usage"], 1)
        self.assertFalse(b.can_start_turn()); b.close()

    def test_timeout_and_close_never_refund_a_possible_request(self):
        b = self.open(); b.reserve(); b.halt("parent_turn_hard_deadline"); b.close()
        s = json.loads(self.path.read_text("utf-8"))
        self.assertEqual(s["http_reserved"], 18)
        self.assertEqual(s["unknown_usage"], 1)
        self.assertEqual(s["requests"][-1]["status"], "interrupted_unknown_charge")

    def test_reported_token_threshold_can_overshoot_final_response(self):
        b = self.open(); b.reserve()
        self.assertTrue(b.observe({"usage": {"total_tokens": 3000001}}))
        self.assertFalse(b.reserve())
        self.assertEqual(b.report()["remaining_reported_tokens"], 0)
        self.assertIn("not_absolute_cap", b.report()["token_limit_kind"]); b.close()

    def test_headroom_refuses_request_without_inventing_usage(self):
        b = self.open(used_tokens=2990000, remaining_tokens=10000)
        self.assertFalse(b.reserve())
        self.assertEqual(b.http, 0); self.assertEqual(b.tokens, 0); b.close()

    def test_lock_denies_second_executor_without_removing_first_lock(self):
        b = self.open()
        with self.assertRaises(FileExistsError):
            self.open(stage="second")
        self.assertTrue(b.lock_path.exists()); b.close()
        self.assertFalse(b.lock_path.exists())

    def test_source_drift_blocks_before_next_http(self):
        unchanged = [True]
        b = self.open(source_check=lambda: unchanged[0])
        unchanged[0] = False
        self.assertFalse(b.reserve())
        self.assertEqual(b.http, 0)
        self.assertEqual(b.report()["halt_reason"], "source_changed_during_stage"); b.close()

    def test_mismatched_response_stops_and_retains_unknown_inflight(self):
        b = self.open(); b.reserve()
        self.assertFalse(b.observe({"usage": {"total_tokens": 1}}, request_id="other"))
        self.assertEqual(b.tokens, 0)
        self.assertEqual(b.report()["responses_with_unknown_usage"], 1); b.close()

    def test_second_inflight_is_forbidden(self):
        b = self.open(); b.reserve()
        self.assertFalse(b.reserve())
        self.assertEqual(b.http, 1); self.assertEqual(b.unknown_usage, 1); b.close()

    def test_supervisor_http_error_never_grants_second_request(self):
        b = self.open()
        config = {"root": str(Path(self.temp.name) / "group"), "limits": self.limits}
        group = {"id": "synthetic", "days": [{"turns": [{"id": "one"}]}]}
        try:
            r = week.supervise(group, config, b, time.monotonic() + 5,
                               _worker_target=failed_http_worker)
            self.assertEqual(r["status"], "budget_failure_closed")
            self.assertEqual(b.http, 1)
            self.assertFalse(r["worker_alive_after_cleanup"])
        finally:
            b.close()

    def test_gate_explicit_and_unconstrained_durations_are_distinct(self):
        p = gate.build_gate("2026-10-26")
        self.assertEqual(sum(len(d["turns"]) for g in p["groups"] for d in g["days"]), 22)
        behind, ahead = p["groups"][-2:]
        self.assertEqual(behind["days"][0]["turns"][0]["expected_totals"], [15, 10])
        self.assertNotIn("expected_totals", ahead["days"][0]["turns"][0])
        self.assertEqual(gate.displayed_total("选两分钟视频作素材，15分钟。"), 15)
        self.assertEqual(gate.displayed_total("总共20分钟，先看两分钟视频。"), 20)
        self.assertIsNone(gate.displayed_total("看看一段视频"))

    def test_gate_catches_displayed_fifteen_written_as_two(self):
        title = "选两分钟视频作素材，15分钟。"
        saved = {"uid": "task-one", "title": title, "duration_minutes": 2}
        def snap(tasks):
            return {week.BUSINESS[1]: {"data": {"days": {"2026-11-05": {"tasks": tasks}}}}}
        result = {"tool": "add_plan", "data": {"task": saved}}
        anchors = {"advice": {"objects": [{"title": title, "display_order": 1}]}}
        calls = [{"tool": "add_plan", "arguments": {"title": title, "duration_minutes": 2}}]
        facts = gate.check_gate_turn({"selection": 1}, snap([]), snap([saved]),
                                    {"tool_results": [result]}, {}, anchors, calls, [result], "2026-11-05")
        self.assertIn("displayed_total_to_actual_write_duration_mismatch", facts["failures"])
        self.assertEqual(facts["duration_layers"][0]["displayed"], 15)

    def test_missing_review_action_is_retained_as_failure_not_evaluator_crash(self):
        result = {"tool": "generate_daily_review", "data": {"review": {
            "date": "2026-10-25", "action_count": 1, "action_records": []}}}
        facts = gate.check_gate_turn({"review_target_date": "2026-10-25"}, {}, {}, {}, {}, {},
                                    [], [result], "2026-10-26")
        self.assertIn("yesterday_review_date_or_saved_action_fact_mismatch", facts["failures"])


if __name__ == "__main__":
    unittest.main()
