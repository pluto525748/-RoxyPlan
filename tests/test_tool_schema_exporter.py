import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import ToolResult
from modules.tool_registry import ToolDefinition, ToolRegistry


def build_registry():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "set_timer",
            "Set a bounded timer",
            "medium",
            {
                "minutes": {
                    "type": "integer",
                    "required": True,
                    "minimum": 1,
                    "maximum": 180,
                },
                "mode": {
                    "type": "string",
                    "required": False,
                    "enum": ["study", "rest"],
                },
            },
            lambda **_kwargs: ToolResult(True, "set_timer", "ok"),
            model_visible=True,
            side_effect=True,
            sequential=True,
            confirmation_policy="conditional",
        )
    )
    registry.register(
        ToolDefinition(
            "private_debug",
            "Not exposed",
            "high",
            {},
            lambda: ToolResult(True, "private_debug", "ok"),
            model_visible=False,
        )
    )
    return registry


def test_export_contains_schema_and_policy_but_not_handler():
    contract = build_registry().model_tool_contracts()[0]
    assert contract["function"]["name"] == "set_timer"
    assert contract["risk_level"] == "medium"
    assert contract["side_effect"] is True
    assert contract["operation_kind"] == "write"
    assert contract["sequential"] is True
    assert "handler" not in contract
    json.dumps(contract)


def test_json_schema_is_closed_and_preserves_constraints():
    schema = build_registry().model_tool_schemas()[0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["minutes"]
    assert schema["properties"]["minutes"]["minimum"] == 1
    assert schema["properties"]["minutes"]["maximum"] == 180
    assert schema["properties"]["mode"]["enum"] == ["study", "rest"]


def test_non_visible_tool_is_not_exported():
    names = {
        item["function"]["name"] for item in build_registry().model_tool_contracts()
    }
    assert names == {"set_timer"}


def test_json_fallback_prompt_uses_only_registered_schema():
    prompt = build_registry().json_fallback_instruction()
    assert "set_timer" in prompt
    assert "private_debug" not in prompt
    assert "Never invent" in prompt


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"tool schema exporter tests passed ({len(TESTS)} cases)")
