import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import (
    AgentResponse,
    AgentStep,
    AssistantToolCall,
    ClientAction,
    IntentResult,
    PendingConfirmation,
    ProposedAction,
    ToolError,
    ToolMessage,
    ToolResult,
)


def test_contracts_round_trip_as_json():
    intent = IntentResult("add_plan", 0.92, {"tasks": ["学习 30 分钟"]}, source="rule")
    error = ToolError("TASK_NOT_FOUND", "没有找到任务")
    result = ToolResult(False, "complete_plan", "not found", error=error)
    step = AgentStep("complete_plan", {"match_text": "学习"})
    pending = PendingConfirmation(
        "confirm_1",
        "delete_plan",
        {"task_ref": "1"},
        "2026-07-16T10:00:00",
        "2026-07-16T10:03:00",
        "delete plan 1",
    )
    action = ClientAction("nod")
    response = AgentResponse(
        "confirmation_required",
        "需要确认",
        [step],
        [result],
        pending,
        [action],
        request_id=intent.request_id,
    )

    encoded = json.dumps(response.to_dict(), ensure_ascii=False)
    restored = AgentResponse.from_dict(json.loads(encoded))

    assert restored.request_id == intent.request_id
    assert restored.steps[0].tool == "complete_plan"
    assert restored.tool_results[0].error == "TASK_NOT_FOUND"
    assert restored.pending_confirmation.confirmation_id == "confirm_1"
    assert restored.client_actions[0].name == "nod"
    assert restored.to_dict()["schema_version"] == "1.0"


def test_missing_required_field_is_rejected():
    try:
        ClientAction.from_dict({"arguments": {}})
    except ValueError as error:
        assert "name" in str(error)
    else:
        raise AssertionError("missing action name must be rejected")


def test_unknown_fields_are_ignored_for_forward_compatibility():
    restored = IntentResult.from_dict(
        {
            "intent": "chat",
            "confidence": 0.1,
            "future_field": {"not": "used"},
        }
    )
    assert restored.intent == "chat"
    assert "future_field" not in restored.to_dict()


def test_tool_error_redacts_traceback_and_local_paths():
    error = ToolError(
        "FAILED",
        "Traceback (most recent call last):\n  C:\\Users\\private\\secret.py line 3",
        details={"path": r"C:\Users\private\memory.json"},
    )
    serialized = json.dumps(error.to_dict(), ensure_ascii=False)
    assert "Traceback" not in serialized
    assert "C:\\Users" not in serialized
    assert "secret.py" not in serialized


def test_client_action_arguments_must_be_json_values():
    try:
        ClientAction("show_bubble", {"text": {"not-json"}})
    except TypeError as error:
        assert "non-JSON" in str(error)
    else:
        raise AssertionError("set values must not enter the API contract")


def test_model_tool_protocol_round_trip():
    proposal = ProposedAction(
        "add_plan",
        {"title": "study", "duration_minutes": 50},
        confidence=0.93,
        source="native_tool_call",
        call_id="call_1",
    )
    call = AssistantToolCall("call_1", proposal.tool_name, proposal.arguments)
    result = ToolResult(
        True,
        "add_plan",
        "added",
        {"task": {"id": 1, "uid": "task_1", "title": "study"}},
        tool_call_id="call_1",
    )
    message = ToolMessage.from_tool_result(result)
    encoded = json.loads(
        json.dumps(
            {
                "proposal": proposal.to_dict(),
                "call": call.to_dict(),
                "message": message.to_dict(),
            }
        )
    )
    assert ProposedAction.from_dict(encoded["proposal"]).call_id == "call_1"
    assert AssistantToolCall.from_dict(encoded["call"]).arguments["duration_minutes"] == 50
    restored = ToolMessage.from_dict(encoded["message"])
    assert restored.call_id == "call_1"
    assert restored.data_summary["task"]["uid"] == "task_1"


def test_tool_message_does_not_expose_private_path_or_full_payload():
    result = ToolResult(
        False,
        "add_plan",
        r"failed at C:\Users\private\data\today_plan.json",
        {"private_payload": "must not be forwarded"},
        error=ToolError("write_failed", r"C:\Users\private\secret.json"),
        tool_call_id="call_private",
    )
    message = ToolMessage.from_tool_result(result)
    serialized = json.dumps(message.to_dict())
    assert "C:\\Users" not in serialized
    assert "must not be forwarded" not in serialized


def test_pending_confirmation_accepts_canonical_v16_names():
    pending = PendingConfirmation.from_dict(
        {
            "confirmation_id": "confirm_2",
            "conversation_id": "session_a",
            "call_id": "call_2",
            "tool_name": "delete_plan",
            "immutable_arguments": {"task_ref": "task_1"},
            "safe_summary": "Delete task 1",
            "risk_level": "high",
            "created_at": "2026-07-23T10:00:00",
            "expires_at": "2026-07-23T10:03:00",
            "state_fingerprint": "abc",
        }
    )
    assert pending.tool == "delete_plan"
    assert pending.arguments == {"task_ref": "task_1"}
    assert pending.to_dict()["tool_name"] == "delete_plan"
    assert pending.to_dict()["immutable_arguments"] == {"task_ref": "task_1"}


if __name__ == "__main__":
    test_contracts_round_trip_as_json()
    test_missing_required_field_is_rejected()
    test_unknown_fields_are_ignored_for_forward_compatibility()
    test_tool_error_redacts_traceback_and_local_paths()
    test_client_action_arguments_must_be_json_values()
    test_model_tool_protocol_round_trip()
    test_tool_message_does_not_expose_private_path_or_full_payload()
    test_pending_confirmation_accepts_canonical_v16_names()
    print("contract tests passed")
