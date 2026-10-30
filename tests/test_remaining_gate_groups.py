"""Pure plan/selection checks; no product, network, ledger or runtime setup."""
import copy
import unittest

from scripts import run_resumed_online_acceptance as gate


class RemainingGateGroupsChecks(unittest.TestCase):
    def test_remaining_has_eighteen_counted_rounds_and_no_replay_of_first_four(self):
        plan = gate.build_remaining_gate("2026-10-26")
        rounds = [t for g in plan["groups"] for d in g["days"] for t in d["turns"]]
        self.assertEqual(len(rounds), 18)
        self.assertEqual(len({t["id"] for t in rounds}), 18)
        self.assertNotIn("gate-memory", [g["id"] for g in plan["groups"]])
        self.assertTrue(all(t["id"] not in {f"gate-memory-t{i:02}" for i in range(1, 5)} for t in rounds))
        mapping = plan["remaining_provenance"]["turn_mapping"]
        self.assertTrue(all(x["counted_user_round"] and x["user_text_unchanged"] and x["expected_unchanged"] for x in mapping))
        self.assertEqual(sum(x["natural_setup"] for x in mapping), 4)

    def test_controls_keep_original_text_and_expectation_after_real_natural_setup(self):
        original = gate.build_gate("2026-10-26")
        saved = copy.deepcopy(original)
        plan = gate.build_remaining_gate("2026-10-26")
        categories = next(g for g in plan["groups"] if g["id"] == "gate-categories")["days"][0]["turns"]
        self.assertEqual(len(categories), 7)
        self.assertTrue(all(t["expected"]["tools"] == ["save_formal_memory"] for t in categories[:3]))
        self.assertEqual(categories[3]["memory_attributes"], ["goal", "preference"])
        old = original["groups"][0]["days"][0]["turns"][4:]
        for before, moved in zip(old, categories[4:]):
            self.assertEqual(before["user"], moved["user"])
            self.assertEqual(before["expected"], moved["expected"])
            self.assertEqual(before["id"], moved["origin_turn_id"])
        self.assertEqual(original, saved)
        self.assertIn("not transparent continuation", plan["remaining_provenance"]["execution_context"])

    def test_explicit_group_selection_is_closed_unique_and_plan_ordered(self):
        plan = gate.build_remaining_gate("2026-10-26")
        self.assertEqual(gate.select_groups(plan, "gate-review,gate-source"), ["gate-source", "gate-review"])
        self.assertEqual(gate.select_groups(plan, "gate-categories"), ["gate-categories"])
        self.assertEqual(len(gate.select_groups(plan, "all")), 5)
        for bad in ("", None, "gate-memory", "gate-source,gate-source", "gate-source,", "all,gate-review"):
            with self.assertRaises(ValueError):
                gate.select_groups(plan, bad)

    def test_original_twenty_two_plan_is_still_separate(self):
        old = gate.build_gate("2026-10-26")
        self.assertEqual(sum(len(d["turns"]) for g in old["groups"] for d in g["days"]), 22)
        self.assertEqual(old["groups"][0]["days"][0]["turns"][2]["user"], gate.ORIGINAL_CHALLENGE)
        self.assertEqual(gate.select_groups(old, "gate-memory"), ["gate-memory"])
        self.assertNotIn("remaining_provenance", old)


if __name__ == "__main__":
    unittest.main()
