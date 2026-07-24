import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.interaction_state import InteractionState
from modules.interaction_state_coordinator import InteractionStateCoordinator


def test_state_round_trip_and_selection_persists():
    clock = [datetime(2026, 7, 24, 9, 0, 0)]
    coordinator = InteractionStateCoordinator(
        now_provider=lambda: clock[0], ttl_seconds=30
    )
    coordinator.record_candidates(
        "a", "memory_candidate_review", ["11", "12", "13"]
    )

    selected = coordinator.handle_control("第二个", "a")
    assert selected.handled and selected.action == "select"
    assert coordinator.current("a").selected_object_ids == ["12"]

    confirmed = coordinator.handle_control("确认", "a")
    assert confirmed.action == "confirm"
    assert confirmed.selected_object_ids == ["12"]

    restored = InteractionState.from_dict(coordinator.current("a").to_dict())
    assert restored.conversation_id == "a"
    assert restored.listed_object_ids == ["11", "12", "13"]


def test_cancel_expire_single_consumption_and_conversation_isolation():
    clock = [datetime(2026, 7, 24, 9, 0, 0)]
    coordinator = InteractionStateCoordinator(
        now_provider=lambda: clock[0], ttl_seconds=20
    )
    coordinator.awaiting_confirmation(
        "a",
        "dangerous_tool",
        immutable_arguments={"tool_name": "delete_plan", "task_ref": "task_1"},
    )
    assert coordinator.handle_control("确认", "b").action == "none"
    assert coordinator.handle_control("取消", "a").action == "cancel"
    assert coordinator.handle_control("确认", "a").action == "none"

    coordinator.awaiting_confirmation("a", "dangerous_tool")
    clock[0] += timedelta(seconds=21)
    assert coordinator.handle_control("确认", "a").action == "expired"

    coordinator.awaiting_confirmation("a", "dangerous_tool")
    coordinator.clear_conversation("a")
    assert coordinator.current("a").state == "idle"


def test_plain_chat_is_not_consumed_as_control():
    coordinator = InteractionStateCoordinator()
    coordinator.awaiting_clarification(
        "a", "missing_slot", missing_fields=["duration_minutes"]
    )
    decision = coordinator.handle_control("我想学四十分钟", "a")
    assert decision.handled is False
    assert coordinator.current("a").state == "awaiting_clarification"


if __name__ == "__main__":
    test_state_round_trip_and_selection_persists()
    test_cancel_expire_single_consumption_and_conversation_isolation()
    test_plain_chat_is_not_consumed_as_control()
    print("interaction state coordinator tests passed")
