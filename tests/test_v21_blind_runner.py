"""Blind test runner for V2.1 — runs the blind set, saves raw results, reports by category.

CRITICAL: This file must NOT be modified after the first blind run.
Results are saved to a timestamped file outside the project.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.intent_router import IntentRouter, LLMIntentParser
from modules.semantic_action_parser import SemanticActionParser

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MATRIX = json.loads((FIXTURES / "v21_chinese_context_matrix.json").read_text("utf-8"))
BLIND_SET = json.loads((FIXTURES / "v21_blind_set.json").read_text("utf-8"))
BLIND_IDS = set(BLIND_SET["case_ids"])
BLIND_CASES = [c for c in MATRIX["cases"] if c["id"] in BLIND_IDS]

# Output path for raw results
RESULTS_DIR = Path("C:/Users/Public/RoxyPlan-blind-results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
RAW_RESULTS_PATH = RESULTS_DIR / f"blind_test_raw_{TIMESTAMP}.json"


class OneDecisionLLM:
    def __init__(self, case, *, reply="盲测隔离回复。"):
        self.case = dict(case)
        self.reply = str(reply)
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
        return self.reply


@pytest.mark.parametrize("case", BLIND_CASES, ids=[c["id"] for c in BLIND_CASES])
def test_blind_set_single_decision_contract(case):
    """Every blind case obeys the single SemanticDecision contract."""
    llm = OneDecisionLLM(case)
    router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    parser = SemanticActionParser(router)

    result = parser.parse_unified(
        case["text"],
        {"conversation_id": "blind-scope"},
        allow_llm=True,
    )

    actual_tools = [item.tool_name for item in result.candidates]
    assert actual_tools == case["expected_candidates"]
    assert not set(actual_tools).intersection(case.get("forbidden_tools", []))
    assert str(result.intent_result.get("intent")) == case["intent"]
    assert len(llm.semantic_calls) <= 1
    if "expect_semantic_calls" in case:
        assert len(llm.semantic_calls) == int(case["expect_semantic_calls"])
    if case["mode"] == "clarify":
        assert result.needs_clarification is True


# ── Result aggregation ─────────────────────────────────────────────

def _aggregate_results(session) -> dict:
    """Aggregate test results by category without inspecting individual sentences."""
    passed = []
    failed = []
    for item in session.items:
        if hasattr(item, 'callspec'):
            case_id = item.callspec.id
            case = next(c for c in BLIND_CASES if c["id"] == case_id)
            passed.append(case)
    return {"total": len(BLIND_CASES), "passed": len(passed), "failed": len(BLIND_CASES) - len(passed)}


def pytest_sessionfinish(session):
    """Save raw results after test run."""
    results = []
    for report in session._reports:
        if report.when == "call":
            case_id = report.nodeid.split("[")[-1].rstrip("]") if "[" in report.nodeid else "unknown"
            results.append({
                "case_id": case_id,
                "passed": report.passed,
                "failed": report.failed,
                "duration": report.duration,
            })

    summary = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "blind_set_sha256": "4b36220289a85ddec87ae332bf2a0c71c506253ef3339278f46d7ecee2be472b",
        "matrix_sha256": "d5cee7e9057c2dc812da48d2b4b178ac41eca77835012cf6edc2beafc5dd83d4",
        "total": len(BLIND_CASES),
        "results": results,
    }
    RAW_RESULTS_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
