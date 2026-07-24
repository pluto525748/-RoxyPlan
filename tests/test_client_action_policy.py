import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.client_action_policy import ClientActionPolicy
from modules.contracts import AgentResponse, ClientAction


def test_allowlisted_actions_and_parameters_are_accepted():
    policy = ClientActionPolicy(now_provider=lambda: datetime(2026, 7, 16, 10, 0, 0))
    allowed, action, reason = policy.validate(
        ClientAction("show_bubble", {"text": "先做一小步。", "duration_ms": 5000})
    )
    assert allowed is True
    assert action.name == "show_bubble"
    assert reason == "allowed"


def test_shell_file_url_and_unregistered_actions_are_blocked():
    policy = ClientActionPolicy()
    for name in ("shell", "write_file", "open_url", "call_python", "qt_method"):
        allowed, _, reason = policy.validate(ClientAction(name))
        assert allowed is False
        assert reason == "action_not_allowed"


def test_action_schema_rejects_unknown_or_out_of_range_parameters():
    policy = ClientActionPolicy()
    assert policy.validate(ClientAction("nod", {"method": "close"}))[0] is False
    assert policy.validate(ClientAction("show_bubble", {"text": "x", "duration_ms": 50}))[0] is False
    assert policy.validate(ClientAction("scale", {"factor": 2.0}))[0] is False


def test_expired_action_is_not_executable():
    policy = ClientActionPolicy(now_provider=lambda: datetime(2026, 7, 16, 10, 0, 1))
    allowed, _, reason = policy.validate(
        ClientAction("nod", expires_at="2026-07-16T10:00:00")
    )
    assert allowed is False
    assert reason == "action_expired"


def test_agent_response_rejects_non_allowlisted_action():
    try:
        AgentResponse("completed", "", client_actions=[ClientAction("shell")])
    except ValueError as error:
        assert "action_not_allowed" in str(error)
    else:
        raise AssertionError("AgentResponse must not contain unsafe client actions")


if __name__ == "__main__":
    test_allowlisted_actions_and_parameters_are_accepted()
    test_shell_file_url_and_unregistered_actions_are_blocked()
    test_action_schema_rejects_unknown_or_out_of_range_parameters()
    test_expired_action_is_not_executable()
    test_agent_response_rejects_non_allowlisted_action()
    print("client action policy tests passed")
