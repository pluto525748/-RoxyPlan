"""Synthetic evaluator contracts only; no product/Qt/provider imports or calls.

Composer facts below are controlled fixtures, not new runtime observations.
The online worker must capture the real composer inputs without injecting them.
"""
import copy
import unittest

from scripts import run_resumed_online_acceptance as gate
from scripts import run_learning_week_acceptance as week


def record(id, content, category):
    return {"id": id, "uid": f"memory-{id}", "content": content,
            "category": category, "status": "active"}


HABIT = record(1, gate.MEMORY, "habit")
GOAL = record(2, gate.GOAL, "goal")
PREFERENCE = record(3, gate.PREFERENCE, "preference")


def snapshot(records):
    return {week.BUSINESS[0]: {"data": {"memories": copy.deepcopy(records)}}}


def prior_context():
    return {"prior_memory_operation": {"kind": "memory_saved", "status": "success",
            "content": gate.MEMORY, "source": "formal_memory_tool", "observed_at": "2026-10-26T09:00:00+08:00"}}


def typed_read(user, records, *, mode="attribute", attribute="fact"):
    return {"tool": "list_memories", "success": True, "data": {"memory_read": {
        "request": {"query_mode": mode, "attribute": attribute, "topic": "", "query": user},
        "facts": [{"memory_id": r["id"], "content": r["content"], "category": r["category"],
                   "authority": "formal_memory"} for r in records]}}}


class RevisedMemoryGateChecks(unittest.TestCase):
    def setUp(self):
        self.groups = {g["id"]: g for g in gate.build_gate("2026-10-26")["groups"]}

    def evaluate(self, turn, records, results=(), contexts=(), message="已按事实核对。", before=None):
        return gate.check_memory_contract(turn, snapshot(records if before is None else before),
            snapshot(records), {"message": message}, list(results), list(contexts))

    def test_original_three_inputs_preserved_and_contrasts_independent(self):
        original = self.groups["gate-memory"]["days"][0]["turns"]
        self.assertEqual([t["user"] for t in original[:3]], [
            "我想让你记住" + gate.MEMORY, "展示一下我的长期记忆。", gate.ORIGINAL_CHALLENGE])
        self.assertEqual(original[2]["memory_expected_contents"], [])
        self.assertNotIn("memory_content", original[2])  # habit is not a category match
        self.assertEqual(original[2]["historical_save_content"], gate.MEMORY)
        pure = self.groups["gate-source"]["days"][0]["turns"][1]
        self.assertIn([], pure["tool_sequences"])
        self.assertEqual(len(self.groups["gate-categories"]["days"][0]["turns"]), 4)

    def test_pure_history_accepts_observed_live_fact_without_new_handler(self):
        turn = self.groups["gate-source"]["days"][0]["turns"][1]
        good = self.evaluate(turn, [HABIT], contexts=[prior_context()])
        self.assertEqual(good["failures"], [])
        self.assertFalse(good["historical_save"]["requires_new_handler"])
        missing = self.evaluate(turn, [HABIT])
        self.assertIn("historical_save_has_no_live_verified_evidence", missing["failures"])

    def test_changed_identity_and_captured_false_denial_never_pass(self):
        turn = self.groups["gate-source"]["days"][0]["turns"][1]
        changed = dict(HABIT, uid="different")
        bad = self.evaluate(turn, [changed], contexts=[prior_context()], before=[HABIT])
        self.assertIn("historical_save_has_no_live_verified_evidence", bad["failures"])
        bad = self.evaluate(turn, [HABIT], contexts=[prior_context()],
                            message="刚才" + gate.CAPTURED_FALSE_DENIAL + "。")
        self.assertIn("captured_false_denial_of_prior_save_reappeared", bad["failures"])

    def test_habit_only_empty_categories_and_save_receipt_are_separate(self):
        turn = self.groups["gate-memory"]["days"][0]["turns"][2]
        good = self.evaluate(turn, [HABIT], [typed_read(turn["user"], [])], [prior_context()])
        self.assertEqual(good["failures"], [])
        self.assertEqual(good["category_scope"]["matching_record_count"], 0)
        missing = self.evaluate(turn, [HABIT], contexts=[prior_context()])
        self.assertIn("typed_category_scope_coverage_missing", missing["failures"])
        self.assertNotIn("historical_save_has_no_live_verified_evidence", missing["failures"])
        old_denial = self.evaluate(turn, [HABIT], [typed_read(turn["user"], [])],
            [prior_context()], message=gate.CAPTURED_FALSE_DENIAL)
        self.assertIn("captured_false_denial_of_prior_save_reappeared", old_denial["failures"])

    def test_goal_preference_match_with_habit_control_and_no_overview_widening(self):
        turn = self.groups["gate-categories"]["days"][0]["turns"][3]
        live = [HABIT, GOAL, PREFERENCE]
        good = self.evaluate(turn, live, [typed_read(turn["user"], [GOAL, PREFERENCE])])
        self.assertEqual(good["failures"], [])
        for returned, mode, failure in [
            ([HABIT, GOAL, PREFERENCE], "attribute", "category_results_do_not_match_known_saved_scope"),
            ([GOAL, PREFERENCE], "overview", "category_query_widened_or_wrong_mode"),
        ]:
            with self.subTest(mode=mode, returned=len(returned)):
                bad = self.evaluate(turn, live, [typed_read(turn["user"], returned, mode=mode)])
                self.assertIn(failure, bad["failures"])

    def test_category_coverage_is_scope_not_handler_count(self):
        turn = self.groups["gate-categories"]["days"][0]["turns"][3]
        reads = [typed_read(turn["user"], [GOAL], attribute="goal"),
                 typed_read(turn["user"], [PREFERENCE], attribute="preference")]
        self.assertEqual(self.evaluate(turn, [HABIT, GOAL, PREFERENCE], reads)["failures"], [])
        bad = self.evaluate(turn, [HABIT, GOAL, PREFERENCE], reads[:1])
        self.assertIn("typed_category_scope_coverage_missing", bad["failures"])

    def test_typed_fact_requires_live_identity_and_formal_authority(self):
        turn = self.groups["gate-categories"]["days"][0]["turns"][3]
        read = typed_read(turn["user"], [GOAL, PREFERENCE])
        read["data"]["memory_read"]["facts"][0]["memory_id"] = 99
        bad = self.evaluate(turn, [HABIT, GOAL, PREFERENCE], [read])
        self.assertIn("typed_category_fact_not_current_formal_record", bad["failures"])

    def test_composer_observer_records_actual_inputs_without_changing_call(self):
        calls, sink, result = [], [], object()
        class Composer:
            def compose(self, *args, **kwargs):
                calls.append((args, kwargs))
                return result
        composer = Composer()
        gate.observe_composer_context(composer, sink)
        context = prior_context()
        self.assertIs(composer.compose("same", verified_turn_context=context), result)
        self.assertIs(calls[0][1]["verified_turn_context"], context)
        context["prior_memory_operation"]["status"] = "changed_after_call"
        self.assertEqual(sink[0]["prior_memory_operation"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
