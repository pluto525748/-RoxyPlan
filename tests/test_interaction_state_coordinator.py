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


def test_low_risk_continuations_outlive_short_confirmation_window():
    clock = [datetime(2026, 8, 20, 9, 0, 0)]
    coordinator = InteractionStateCoordinator(
        now_provider=lambda: clock[0],
        ttl_seconds=300,
        continuation_ttl_seconds=1200,
        confirmation_ttl_seconds=180,
    )
    coordinator.awaiting_choice("choice", "advice_or_action_choice")
    coordinator.awaiting_clarification(
        "clarification", "missing_slots", missing_fields=["duration_minutes"]
    )
    coordinator.awaiting_confirmation("confirmation", "dangerous_tool")

    clock[0] += timedelta(seconds=181)

    assert coordinator.current("choice").state == "awaiting_choice"
    assert coordinator.current("clarification").state == "awaiting_clarification"
    assert coordinator.current("confirmation").state == "expired"

    clock[0] += timedelta(seconds=120)

    assert coordinator.current("choice").state == "awaiting_choice"
    assert coordinator.current("clarification").state == "awaiting_clarification"

    clock[0] += timedelta(seconds=900)

    assert coordinator.current("choice").state == "expired"
    assert coordinator.current("clarification").state == "expired"


def test_legacy_ttl_still_applies_to_every_pending_state_without_overrides():
    clock = [datetime(2026, 8, 20, 9, 0, 0)]
    coordinator = InteractionStateCoordinator(
        now_provider=lambda: clock[0], ttl_seconds=20
    )
    coordinator.awaiting_choice("choice", "advice_choice")
    coordinator.awaiting_confirmation("confirmation", "dangerous_tool")

    clock[0] += timedelta(seconds=21)

    assert coordinator.current("choice").state == "expired"
    assert coordinator.current("confirmation").state == "expired"


def test_pending_state_change_restarts_ttl_for_the_new_state_class():
    clock = [datetime(2026, 8, 20, 9, 0, 0)]
    coordinator = InteractionStateCoordinator(
        now_provider=lambda: clock[0],
        ttl_seconds=300,
        continuation_ttl_seconds=1200,
        confirmation_ttl_seconds=180,
    )
    coordinator.awaiting_choice("conversation", "advice_choice")
    clock[0] += timedelta(seconds=1100)

    updated = coordinator.update(
        "conversation",
        state="awaiting_clarification",
        interaction_kind="missing_slots",
        missing_fields=["duration_minutes"],
    )

    assert updated.expires_at == "2026-08-20T09:38:20"
    clock[0] += timedelta(seconds=101)
    assert coordinator.current("conversation").state == "awaiting_clarification"

    coordinator.mark_tool_pending("conversation", ["call_1"])
    assert coordinator.current("conversation").expires_at == "2026-08-20T09:25:01"
    clock[0] += timedelta(seconds=301)
    assert coordinator.current("conversation").state == "expired"


def test_candidate_objects_and_tool_call_ids_round_trip():
    coordinator = InteractionStateCoordinator()
    coordinator.start(
        "candidate-round-trip",
        "object_selection",
        "awaiting_choice",
        candidate_objects=[{"uid": "task_1", "title": "学习"}],
        tool_call_ids=["call_1"],
    )
    restored = coordinator.current("candidate-round-trip")
    assert restored.candidate_objects == [{"uid": "task_1", "title": "学习"}]
    assert restored.tool_call_ids == ["call_1"]


if __name__ == "__main__":
    test_state_round_trip_and_selection_persists()
    test_cancel_expire_single_consumption_and_conversation_isolation()
    test_plain_chat_is_not_consumed_as_control()
    print("interaction state coordinator tests passed")
