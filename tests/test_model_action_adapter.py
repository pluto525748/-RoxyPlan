import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import ProposedAction
from modules.llm.contracts import ProviderResponse, ProviderToolCall
from modules.model_action_adapter import ModelActionAdapter
from modules.tool_registry import ToolDefinition, ToolRegistry


def build_registry():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "add_plan",
            "Add one plan",
            "medium",
            {
                "title": {
                    "type": "string",
                    "required": True,
                    "min_length": 1,
                    "max_length": 120,
                },
                "duration_minutes": {
                    "type": "integer",
                    "required": False,
                    "minimum": 1,
                    "maximum": 1440,
                },
            },
            lambda **_kwargs: None,
            model_visible=True,
        )
    )
    return registry


def test_native_tool_call_is_normalized_without_execution():
    adapter = ModelActionAdapter(build_registry())
    response = ProviderResponse(
        "deepseek",
        "fake",
        tool_calls=[
            ProviderToolCall(
                "add_plan",
                {"title": "machine learning", "duration_minutes": 50},
                "call_1",
            )
        ],
    )
    proposal = adapter.from_native_response(response)[0]
    assert proposal.kind == "action"
    assert proposal.call_id == "call_1"
    assert proposal.arguments["duration_minutes"] == 50


def test_unknown_native_tool_is_not_executable():
    adapter = ModelActionAdapter(build_registry())
    response = ProviderResponse(
        "deepseek",
        "fake",
        tool_calls=[ProviderToolCall("run_shell", {"command": "whoami"}, "bad")],
    )
    proposal = adapter.from_native_response(response)[0]
    assert proposal.kind == "clarification"
    assert proposal.needs_clarification is True
    assert "tool_not_allowed" in proposal.warnings


def test_missing_required_parameter_is_reported():
    adapter = ModelActionAdapter(build_registry())
    response = ProviderResponse(
        "deepseek",
        "fake",
        tool_calls=[ProviderToolCall("add_plan", {}, "missing")],
    )
    proposal = adapter.from_native_response(response)[0]
    assert proposal.needs_clarification is True
    assert proposal.missing_fields == ["title"]


def test_json_fallback_accepts_constrained_action():
    adapter = ModelActionAdapter(build_registry())
    raw = json.dumps(
        {
            "kind": "action",
            "actions": [
                {
                    "call_id": "json_1",
                    "tool_name": "add_plan",
                    "arguments": {"title": "read docs"},
                }
            ],
        }
    )
    proposal = adapter.from_json_text(raw, provider="deepseek")[0]
    assert proposal.kind == "action"
    assert proposal.source == "json_fallback"
    assert proposal.call_id == "json_1"


def test_json_fallback_rejects_invalid_json_and_unknown_fields():
    adapter = ModelActionAdapter(build_registry())
    invalid = adapter.from_json_text("not json")[0]
    assert invalid.needs_clarification is True
    proposal = adapter.from_json_text(
        '{"kind":"action","tool_name":"add_plan",'
        '"arguments":{"title":"x","path":"C:/private"}}'
    )[0]
    assert proposal.needs_clarification is True
    assert "unexpected_parameter" in proposal.warnings


def test_proposed_action_round_trip_is_json_safe():
    original = ProposedAction(
        "add_plan",
        {"title": "study", "duration_minutes": 50},
        confidence=0.93,
        source="native_tool_call",
    )
    restored = ProposedAction.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.tool_name == original.tool_name
    assert restored.arguments == original.arguments


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"model action adapter tests passed ({len(TESTS)} cases)")
