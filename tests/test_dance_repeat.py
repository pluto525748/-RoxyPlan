import tempfile
from pathlib import Path

from tests.v18_test_support import NoCallLLM, build_service


def test_repeat_dance_turns_create_new_client_actions():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        first = service.handle("跳舞", "dance_repeat")
        second = service.handle("再跳一次", "dance_repeat")
        third = service.handle("再跳一个舞", "dance_repeat")

        actions = [
            response.client_actions[0] for response in (first, second, third)
        ]
        assert all(action.name == "play_dance" for action in actions)
        assert len({action.action_id for action in actions}) == 3
        assert len({action.idempotency_key for action in actions}) == 3


def test_non_dance_phrases_do_not_create_actions():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp))
        assert service.handle("我不想跳舞", "no_dance").client_actions == []
        assert service.handle("真棒", "no_dance").client_actions == []
