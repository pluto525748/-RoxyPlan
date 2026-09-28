import tempfile
from pathlib import Path

from tests.v18_test_support import NoCallLLM, build_service


def test_agent_response_preserves_client_action_through_serialization():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("跳个舞看看", "agent_action")
        restored = type(response).from_dict(response.to_dict())

        assert response.tool_results[0].tool == "play_dance"
        assert response.client_actions[0].name == "play_dance"
        assert restored.client_actions[0].action_id == response.client_actions[0].action_id
