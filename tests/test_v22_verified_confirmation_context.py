import json
from types import MappingProxyType

import pytest

from modules.verified_turn_context import VerifiedTurnContext


def _context(confirmation_pending=None):
    return VerifiedTurnContext.for_chat_reply(
        {"intent": "chat"}, confirmation_pending=confirmation_pending
    )


def test_confirmation_fact_is_redacted_and_does_not_imply_execution():
    raw = {
        "active": True,
        "capability": "complete_plan",
        "object_count": 3,
        "ids": ["private-task-uid"],
        "arguments": {"title": "private-title"},
        "path": "C:/private/memory.json",
        "system_prompt": "private-instruction",
    }
    context = _context(MappingProxyType(raw))
    expected = {"active": True, "capability": "complete_plan", "object_count": 3}

    assert context.confirmation_pending == expected
    assert context.to_dict()["confirmation_pending"] == expected
    assert context.to_dict()["execution"]["performed"] is False
    assert context.executed_tools == []
    assert context.verified_changes == []
    assert context.diagnostic_summary()["has_confirmation_pending"] is True
    assert context.diagnostic_summary()["confirmation_object_count"] == 3
    model_text = context.to_model_text()
    assert "尚未执行的真实确认操作" in model_text
    assert "不能当作已经执行或已经成功" in model_text
    safe_output = json.dumps(context.to_dict(), ensure_ascii=False) + model_text
    safe_output += json.dumps(context.diagnostic_summary(), ensure_ascii=False)
    for private_value in (
        "private-task-uid", "private-title", "C:/private/memory.json",
        "private-instruction", "arguments", "system_prompt",
    ):
        assert private_value not in safe_output
    assert raw["ids"] == ["private-task-uid"]


@pytest.mark.parametrize("raw", [None, [], "confirmation", 1, False, {}])
def test_missing_or_invalid_confirmation_shape_has_no_fact(raw):
    context = _context(raw)
    assert context.confirmation_pending == {}
    assert context.to_dict()["confirmation_pending"] == {}
    assert context.diagnostic_summary()["has_confirmation_pending"] is False
    assert context.diagnostic_summary()["confirmation_object_count"] == 0
    assert "程序没有建立真实待确认操作" in context.to_model_text()
    assert "不得要求用户确认" in context.to_model_text()


@pytest.mark.parametrize("active", [False, 1, "true", "True", [], {}, None])
def test_only_literal_true_can_represent_an_active_confirmation(active):
    assert _context({
        "active": active, "capability": "add_plan", "object_count": 1,
    }).confirmation_pending == {}


@pytest.mark.parametrize("count", [0, -1, True, False, 1.0, "1", None, [], {}])
def test_confirmation_object_count_is_a_positive_integer_not_a_boolean(count):
    assert _context({
        "active": True, "capability": "add_plan", "object_count": count,
    }).confirmation_pending == {}


@pytest.mark.parametrize("capability", [
    "", " ", " add_plan", "add_plan ", "ADD_PLAN", "add-plan",
    "C:/private/path", "delete_plan\nprivate-prompt", True, 1, None, {}, [],
])
def test_confirmation_capability_requires_a_canonical_name_shape(capability):
    assert _context({
        "active": True, "capability": capability, "object_count": 1,
    }).confirmation_pending == {}


def test_canonical_tool_name_is_projected_without_a_second_tool_catalog():
    context = _context({
        "active": True, "capability": "registered_runtime_action", "object_count": 1,
    })
    assert context.confirmation_pending == {
        "active": True, "capability": "registered_runtime_action", "object_count": 1,
    }


def test_direct_construction_and_later_dictionary_mutation_cannot_leak_extras():
    context = VerifiedTurnContext(
        "chat", "chat", False,
        confirmation_pending={
            "active": True, "capability": "delete_plan", "object_count": 1,
            "arguments": {"id": "private-uid"},
        },
    )
    assert set(context.confirmation_pending) == {
        "active", "capability", "object_count",
    }
    context.confirmation_pending["arguments"] = {"id": "private-uid"}
    assert "private-uid" not in context.to_model_text()
    assert "arguments" not in context.to_dict()["confirmation_pending"]
    context.confirmation_pending["active"] = "true"
    assert context.to_dict()["confirmation_pending"] == {}
    assert context.diagnostic_summary()["has_confirmation_pending"] is False
