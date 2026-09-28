from datetime import datetime, timedelta, timezone

from frontend.client_action_dispatcher import DesktopClientActionDispatcher
from modules.contracts import ClientAction


class FakeManager:
    def __init__(self):
        self.current_state = "idle"
        self.calls = []
        self.next_result = (True, "running")

    def try_play_action(self, name):
        self.calls.append(name)
        if self.next_result[0] and name == "dance":
            self.current_state = "dancing"
        return self.next_result


class FakePet:
    def __init__(self):
        self.action_manager = FakeManager()
        self.bubbles = []

    def show_bubble(self, text, duration):
        self.bubbles.append((text, duration))


def test_dispatcher_executes_allowlisted_action_once():
    pet = FakePet()
    dispatcher = DesktopClientActionDispatcher(pet)
    action = ClientAction("play_dance", {"dance_id": None})

    first = dispatcher.dispatch(action)
    replay = dispatcher.dispatch(action)

    assert first.accepted and first.status == "running"
    assert replay.status == "skipped_duplicate"
    assert pet.action_manager.calls == ["dance"]


def test_dispatcher_allows_same_dance_with_new_identity():
    pet = FakePet()
    dispatcher = DesktopClientActionDispatcher(pet)
    first = dispatcher.dispatch(ClientAction("play_dance", {"dance_id": None}))
    pet.action_manager.current_state = "idle"
    second = dispatcher.dispatch(ClientAction("play_dance", {"dance_id": None}))
    assert first.accepted and second.accepted
    assert pet.action_manager.calls == ["dance", "dance"]


def test_dispatcher_reports_busy_expired_and_invalid():
    pet = FakePet()
    dispatcher = DesktopClientActionDispatcher(pet)
    pet.action_manager.next_result = (False, "busy")
    busy = dispatcher.dispatch(ClientAction("play_dance", {"dance_id": None}))
    expired = dispatcher.dispatch(
        ClientAction(
            "nod",
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
    )
    invalid = dispatcher.dispatch(ClientAction("play_dance", {"path": "x.png"}))

    assert busy.status == "skipped_busy"
    assert expired.status == "expired"
    assert invalid.status == "rejected"
