"""Semantic-combination tests for PlanAuthorizationPolicy.

These tests cover the structured field combinations (subject × polarity ×
modality) — not specific Chinese keywords like 一定要/想/必须.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.plan_authorization_policy import (
    PlanAuthorization,
    PlanAuthorizationPolicy,
)
from modules.semantic_action_parser import SemanticDecision, SemanticToolCall


def _decision(subject="", polarity="", modality="", tool="add_plan"):
    return SemanticDecision(
        mode="tool_then_reply",
        intent="add_plan",
        tool_calls=[SemanticToolCall(tool, {"title": "test"})],
        confidence=0.95,
        subject=subject,
        polarity=polarity,
        modality=modality,
    )


policy = PlanAuthorizationPolicy()


# ── Commitments (self + positive + commitment → EXECUTE) ─────────────

@pytest.mark.parametrize("subject,polarity,modality,expected", [
    ("self", "positive", "commitment", PlanAuthorization.EXECUTE),
    ("self", "positive", "desire", PlanAuthorization.PENDING),
    ("self", "positive", "hypothetical", PlanAuthorization.NO_ACTION),
    ("self", "positive", "question", PlanAuthorization.NO_ACTION),
    ("self", "negative", "commitment", PlanAuthorization.NO_ACTION),
    ("self", "negative", "desire", PlanAuthorization.NO_ACTION),
    ("self", "", "commitment", PlanAuthorization.EXECUTE),
    ("other", "positive", "commitment", PlanAuthorization.NO_ACTION),
    ("other", "positive", "desire", PlanAuthorization.NO_ACTION),
    ("", "positive", "desire", PlanAuthorization.EXECUTE),
    ("self", "", "", PlanAuthorization.EXECUTE),
    ("", "", "", PlanAuthorization.EXECUTE),
])
def test_plan_authorization_combinations(subject, polarity, modality, expected):
    decision = _decision(subject=subject, polarity=polarity, modality=modality)
    result = policy.evaluate(decision)
    assert result.authorization == expected, (
        f"({subject}, {polarity}, {modality}) → expected {expected.value}, "
        f"got {result.authorization.value}"
    )


# ── Non-plan tools bypass the policy ─────────────────────────────────

def test_non_plan_tool_bypasses_policy():
    decision = SemanticDecision(
        mode="tool_then_reply",
        intent="show_plan",
        tool_calls=[SemanticToolCall("show_plan", {})],
        confidence=0.95,
        subject="self",
        polarity="positive",
        modality="commitment",
    )
    assert policy.evaluate(decision).authorization == PlanAuthorization.EXECUTE


def test_explicit_desire_plan_command_executes_without_extra_confirmation():
    decision = _decision(
        subject="self",
        polarity="positive",
        modality="desire",
    )

    result = policy.evaluate(
        decision,
        request_mode="execute",
        explicit_command=True,
    )

    assert result.authorization == PlanAuthorization.EXECUTE


def test_possible_action_desire_still_requires_pending():
    decision = _decision(
        subject="self",
        polarity="positive",
        modality="desire",
    )

    result = policy.evaluate(
        decision,
        request_mode="possible_action",
        explicit_command=False,
    )

    assert result.authorization == PlanAuthorization.PENDING


def test_memory_tool_bypasses_policy():
    decision = SemanticDecision(
        mode="tool_then_reply",
        intent="add_memory_request",
        tool_calls=[SemanticToolCall("save_formal_memory", {"content": "test"})],
        confidence=0.95,
        subject="self",
        polarity="positive",
        modality="desire",
    )
    assert policy.evaluate(decision).authorization == PlanAuthorization.EXECUTE


# ── SemanticDecision contract: field validation ──────────────────────

def test_semantic_decision_rejects_invalid_subject():
    d = _decision(subject="invalid_subject")
    assert d.subject == ""


def test_semantic_decision_rejects_invalid_polarity():
    d = _decision(polarity="maybe")
    assert d.polarity == ""


def test_semantic_decision_rejects_invalid_modality():
    d = _decision(modality="wish")
    assert d.modality == ""


def test_semantic_decision_preserves_valid_fields():
    d = _decision(subject="self", polarity="positive", modality="commitment")
    assert d.subject == "self"
    assert d.polarity == "positive"
    assert d.modality == "commitment"


def test_semantic_decision_to_dict_includes_new_fields():
    d = _decision(subject="self", polarity="positive", modality="desire")
    result = d.to_dict()
    assert result["subject"] == "self"
    assert result["polarity"] == "positive"
    assert result["modality"] == "desire"


# ── Field passthrough: _with_legacy_slots → _result → as_decision ──

def test_with_legacy_slots_preserves_subject_polarity_modality():
    """Fields parsed from LLM JSON must survive _with_legacy_slots."""
    from modules.intent_router import IntentRouter, LLMIntentParser

    router = IntentRouter()
    parsed = {
        "intent": "add_plan",
        "confidence": 0.95,
        "entities": {"title": "早睡"},
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "mode": "write",
        "proposed_tool": "add_plan",
        "follow_up_target": None,
        "semantic_diagnostic": "",
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
    }
    result = router._with_legacy_slots(parsed)
    assert result["subject"] == "self", f"subject lost: {result.get('subject')}"
    assert result["polarity"] == "positive", f"polarity lost: {result.get('polarity')}"
    assert result["modality"] == "commitment", f"modality lost: {result.get('modality')}"


def test_parsed_to_as_decision_round_trip():
    """Full chain: parsed JSON → _with_legacy_slots → as_decision → fields intact."""
    from modules.intent_router import IntentRouter
    from modules.semantic_action_parser import (
        SemanticActionParser,
        SemanticParseResult,
    )

    router = IntentRouter()
    parsed = {
        "intent": "add_plan",
        "confidence": 0.95,
        "entities": {"title": "早睡", "time_slot": "晚上"},
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "mode": "write",
        "proposed_tool": "add_plan",
        "follow_up_target": None,
        "semantic_diagnostic": "",
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
    }
    intent_result = router._with_legacy_slots(parsed)

    # Build a minimal SemanticParseResult so as_decision can consume
    from modules.semantic_action_parser import ActionCandidate

    candidate = ActionCandidate(
        domain="plan",
        tool_name="add_plan",
        arguments={"title": "早睡", "time_slot": "晚上"},
    )
    parse_result = SemanticParseResult(
        source="llm",
        candidates=[candidate],
        intent_result=intent_result,
    )
    decision = parse_result.as_decision()
    assert decision.subject == "self"
    assert decision.polarity == "positive"
    assert decision.modality == "commitment"
    assert decision.intent == "add_plan"
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].name == "add_plan"
