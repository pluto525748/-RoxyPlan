import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.semantic_action_parser import (
    ActionCandidate,
    SemanticParseResult,
)


def test_parse_result_exports_one_tool_then_reply_decision():
    parsed = SemanticParseResult(
        source="stub",
        candidates=[
            ActionCandidate(
                domain="plan",
                tool_name="show_plan",
                arguments={"date": ""},
            )
        ],
        intent_result={
            "intent": "show_plan",
            "confidence": 0.93,
            "follow_up_target": "previous_read_result",
        },
    )

    decision = parsed.as_decision()

    assert decision.to_dict() == {
        "mode": "tool_then_reply",
        "intent": "show_plan",
        "tool_calls": [{"name": "show_plan", "arguments": {"date": ""}}],
        "confidence": 0.93,
        "follow_up_target": "previous_read_result",
        "reply_hint": None,
    }


def test_parse_result_exports_clarify_without_execution_authority():
    parsed = SemanticParseResult(
        source="stub",
        candidates=[
            ActionCandidate(
                domain="plan",
                tool_name="add_plan",
                arguments={"title": "cosplay"},
            )
        ],
        needs_clarification=True,
        intent_result={
            "intent": "add_plan",
            "confidence": 0.71,
            "clarification_question": "你想安排什么内容？",
        },
    )

    decision = parsed.as_decision()

    assert decision.mode == "clarify"
    assert decision.tool_calls == []
    assert decision.reply_hint == "你想安排什么内容？"
    assert parsed.candidates[0].arguments == {"title": "cosplay"}
