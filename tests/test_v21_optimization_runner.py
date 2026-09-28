"""Runner for V2.1 optimization set — only the cases allowed for guided debugging."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.intent_router import IntentRouter, LLMIntentParser
from modules.semantic_action_parser import SemanticActionParser
from semantic_contract_fixtures import adapt_legacy_v21_semantic_payload

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MATRIX = json.loads((FIXTURES / "v21_chinese_context_matrix.json").read_text("utf-8"))
OPT_SET = json.loads((FIXTURES / "v21_optimization_set.json").read_text("utf-8"))
OPT_IDS = set(OPT_SET["case_ids"])
OPT_CASES = [c for c in MATRIX["cases"] if c["id"] in OPT_IDS]


class OneDecisionLLM:
    def __init__(self, case, *, reply="优化集隔离测试回复。"):
        self.case = dict(case)
        self.reply = str(reply)
        self.semantic_calls = []

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls.append(system_text)
            payload = adapt_legacy_v21_semantic_payload(self.case)
            return json.dumps(payload, ensure_ascii=False)
        return self.reply


@pytest.mark.parametrize("case", OPT_CASES, ids=[c["id"] for c in OPT_CASES])
def test_optimization_set_single_decision_contract(case):
    """Every optimization case obeys the single SemanticDecision contract."""
    llm = OneDecisionLLM(case)
    router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    parser = SemanticActionParser(router)

    result = parser.parse_unified(
        case["text"],
        {"conversation_id": "opt-scope"},
        allow_llm=True,
    )

    actual_tools = [item.tool_name for item in result.candidates]
    assert actual_tools == case["expected_candidates"], (
        f"candidates: expected={case['expected_candidates']} actual={actual_tools}"
    )
    assert not set(actual_tools).intersection(case.get("forbidden_tools", []))
    assert str(result.intent_result.get("intent")) == case["intent"], (
        f"intent: expected={case['intent']} actual={result.intent_result.get('intent')}"
    )
    assert len(llm.semantic_calls) <= 1, (
        f"semantic_calls: {len(llm.semantic_calls)}"
    )
    if "expect_semantic_calls" in case:
        assert len(llm.semantic_calls) == int(case["expect_semantic_calls"])
    if case["mode"] == "clarify":
        assert result.needs_clarification is True
        assert result.intent_result.get("clarification_question")


def test_optimization_set_coverage():
    """Verify optimization set covers all required categories."""
    required_phenomena = {
        "greeting", "emotion_with_money_keyword", "domain_word_not_request",
        "negated_action", "self_correction_cancel", "explicit_meta_advice",
        "quoted_command", "hypothetical_command", "rhetorical_opposition",
        "dance_mention_not_command", "capability_question",
        "plan_read_colloquial", "formal_memory_query",
        "action_log_query", "current_conversation_ellipsis",
        "polite_pet_request", "explicit_plan_add",
        "formal_memory_payload", "assistant_message_reference",
        "protected_dance_memory_payload", "goal_statement_with_trailing_memory_request",
        "plan_delete_confirmation",
        "missing_plan_content", "missing_delete_target",
        "explicit_read_write_multi_intent", "mixed_polarity_multi_intent",
        "condition_not_immediate_execution",
        "memory_guard_negative_contrast",
        "ordinary_progress_statement", "future_intention_not_instruction",
        "denial_of_past_action", "double_negation_read",
        "exact_pet_command",
    }
    opt_phenomena = {c["phenomenon"] for c in OPT_CASES}
    missing = required_phenomena - opt_phenomena
    assert not missing, f"Optimization set missing phenomena: {missing}"
    assert len(OPT_CASES) == len(OPT_IDS), f"Count mismatch: {len(OPT_CASES)} vs {len(OPT_IDS)}"
