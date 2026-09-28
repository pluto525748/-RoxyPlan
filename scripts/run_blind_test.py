"""Simple blind test runner — runs blind cases, saves raw results, reports by category.

Does NOT inspect individual blind case text or expected values.
Only aggregates pass/fail by mode category.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
import sys
for path in (str(ROOT), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from modules.intent_router import IntentRouter, LLMIntentParser
from modules.semantic_action_parser import SemanticActionParser


FIXTURES = TESTS / "fixtures"
MATRIX = json.loads((FIXTURES / "v21_chinese_context_matrix.json").read_text("utf-8"))
BLIND_SET = json.loads((FIXTURES / "v21_blind_set.json").read_text("utf-8"))
BLIND_IDS = set(BLIND_SET["case_ids"])
BLIND_CASES = [c for c in MATRIX["cases"] if c["id"] in BLIND_IDS]

RESULTS_DIR = Path("C:/Users/Public/RoxyPlan-blind-results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
RAW_PATH = RESULTS_DIR / f"blind_test_raw_{TIMESTAMP}.json"


class OneDecisionLLM:
    def __init__(self, case):
        self.case = dict(case)
        self.semantic_calls = []

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls.append(system_text)
            return json.dumps(
                {
                    "mode": self.case["mode"],
                    "intent": self.case["intent"],
                    "entities": dict(self.case.get("entities", {})),
                    "proposed_tool": self.case.get("proposed_tool"),
                    "confidence": 0.97,
                    "follow_up_target": self.case.get("follow_up_target"),
                    "needs_confirmation": bool(self.case.get("needs_confirmation", False)),
                    "warnings": [],
                    "clarification_question": self.case.get("clarification_question"),
                    "candidate_actions": list(self.case.get("candidate_actions", [])),
                },
                ensure_ascii=False,
            )
        return "盲测隔离回复。"


def run_blind_test():
    results = []
    category_pass = {}
    category_total = {}

    for case in BLIND_CASES:
        cid = case["id"]
        mode = case["mode"]
        phenomenon = case["phenomenon"]
        category_total[mode] = category_total.get(mode, 0) + 1
        category_pass[mode] = category_pass.get(mode, 0)

        try:
            llm = OneDecisionLLM(case)
            router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
            result = SemanticActionParser(router).parse_unified(
                case["text"],
                {"conversation_id": "blind-" + cid},
                allow_llm=True,
            )

            actual_tools = [item.tool_name for item in result.candidates]
            passed = True
            failure_reasons = []

            if actual_tools != case["expected_candidates"]:
                passed = False
                failure_reasons.append(
                    f"candidates: expected={case['expected_candidates']} actual={actual_tools}"
                )

            forbidden = set(case.get("forbidden_tools", []))
            if forbidden.intersection(actual_tools):
                passed = False
                failure_reasons.append(f"forbidden_tools: {forbidden.intersection(actual_tools)}")

            if str(result.intent_result.get("intent")) != case["intent"]:
                passed = False
                failure_reasons.append(
                    f"intent: expected={case['intent']} actual={result.intent_result.get('intent')}"
                )

            if len(llm.semantic_calls) > 1:
                passed = False
                failure_reasons.append(f"semantic_calls: {len(llm.semantic_calls)}")

            if "expect_semantic_calls" in case and len(llm.semantic_calls) != int(case["expect_semantic_calls"]):
                passed = False
                failure_reasons.append(
                    f"expect_semantic_calls: expected={case['expect_semantic_calls']} actual={len(llm.semantic_calls)}"
                )

            if case["mode"] == "clarify" and not result.needs_clarification:
                passed = False
                failure_reasons.append("expected clarification but none produced")

            if passed:
                category_pass[mode] = category_pass.get(mode, 0) + 1

            results.append({
                "case_id": cid,
                "mode": mode,
                "phenomenon": phenomenon,
                "passed": passed,
                "failure_reasons": failure_reasons,
                "semantic_calls": len(llm.semantic_calls),
            })

        except Exception as exc:
            results.append({
                "case_id": cid,
                "mode": mode,
                "phenomenon": phenomenon,
                "passed": False,
                "failure_reasons": [f"runner_exception:{type(exc).__name__}:{exc}"],
                "semantic_calls": 0,
            })

    # Build report
    total_pass = sum(1 for r in results if r["passed"])
    total_fail = len(results) - total_pass
    report = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "blind_set_sha256": "4b36220289a85ddec87ae332bf2a0c71c506253ef3339278f46d7ecee2be472b",
        "matrix_sha256": "d5cee7e9057c2dc812da48d2b4b178ac41eca77835012cf6edc2beafc5dd83d4",
        "total": len(BLIND_CASES),
        "passed": total_pass,
        "failed": total_fail,
        "pass_rate_by_mode": {
            mode: f"{category_pass.get(mode, 0)}/{category_total.get(mode, 0)}"
            for mode in sorted(category_total)
        },
        "results": results,
    }
    RAW_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print summary
    print(f"\n=== Blind Test Results ===")
    print(f"Total: {len(BLIND_CASES)}")
    print(f"Passed: {total_pass}")
    print(f"Failed: {total_fail}")
    print(f"Overall: {total_pass}/{len(BLIND_CASES)} = {total_pass/len(BLIND_CASES)*100:.1f}%")
    print(f"\nPass rate by mode:")
    for mode in sorted(category_total):
        p = category_pass.get(mode, 0)
        t = category_total[mode]
        print(f"  {mode}: {p}/{t} = {p/t*100:.1f}%")
    failed_modes = {}
    for r in results:
        if not r["passed"]:
            m = r["mode"]
            if m not in failed_modes:
                failed_modes[m] = []
            failed_modes[m].append(r["case_id"])
    if failed_modes:
        print(f"\nFailed cases by mode:")
        for mode, ids in sorted(failed_modes.items()):
            print(f"  {mode}: {ids}")
    print(f"\nRaw results: {RAW_PATH}")

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_blind_test())
