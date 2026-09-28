import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.interaction_state_coordinator import InteractionStateCoordinator
from v18_test_support import NoCallLLM, RecordingLLM, build_service


def test_plan_tool_without_task_cannot_reuse_an_older_task_title():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(
            Path(temp), NoCallLLM()
        )
        state = service.conversation_service.state_manager
        state.observe_tool_result(
            "paired-plan-result",
            {
                "success": True,
                "tool": "add_plan",
                "status": "completed",
                "data": {
                    "task": {
                        "id": 1,
                        "uid": "task-old",
                        "title": "旧计划",
                        "done": False,
                        "status": "pending",
                    }
                },
            },
        )
        state.observe_tool_result(
            "paired-plan-result",
            {
                "success": True,
                "tool": "update_plan",
                "status": "completed",
                "data": {},
            },
        )

        references = state.reference_context("paired-plan-result")

        assert references["last_tool_result"]["tool"] == "update_plan"
        assert references["last_task"] is None


def test_new_topic_cancels_plan_clarification_without_using_it_as_plan_content():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        service.interaction_coordinator.awaiting_clarification(
            "plan-topic",
            "missing_slots",
            domain="plan",
            known_fields={"date": "tomorrow"},
            missing_fields=["title", "duration_minutes"],
            immutable_arguments={"tool_name": "add_plan", "date": "tomorrow"},
            safe_summary="想学什么内容，大概安排多久？",
        )

        response = service.handle(
            "我的长期目标是成为AI经理和Agent全栈开发者",
            "plan-topic",
        )

        assert service.interaction_coordinator.current("plan-topic").state == "cancelled"
        assert growth.tasks() == []
        assert "尚未修改数据" not in response.message
        assert "AI经理" not in "\n".join(item["title"] for item in growth.tasks())


def test_new_topic_cancels_pending_confirmation_without_executing_it():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        service.confirmation_manager.create(
            "delete_plan", {"task_ref": "not-a-real-task"}, "删除计划", scope="confirm-topic"
        )
        service.interaction_coordinator.awaiting_confirmation(
            "confirm-topic",
            "dangerous_tool",
            immutable_arguments={"tool_name": "delete_plan", "task_ref": "not-a-real-task"},
        )

        response = service.handle("我的长期目标是成为AI经理", "confirm-topic")

        assert service.confirmation_manager.pending(scope="confirm-topic") is None
        assert service.interaction_coordinator.current("confirm-topic").state == "cancelled"
        assert growth.tasks() == []
        assert "尚未修改数据" not in response.message


def test_new_conversation_cannot_consume_old_confirmation_or_ordinal_reference():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.confirmation_manager.create(
            "delete_plan", {"task_ref": "old"}, "删除计划", scope="old-window"
        )
        service.interaction_coordinator.awaiting_confirmation(
            "old-window",
            "dangerous_tool",
            immutable_arguments={"tool_name": "delete_plan", "task_ref": "old"},
        )
        service.interaction_coordinator.record_candidates(
            "old-window", "object_selection", ["first", "second"]
        )

        confirmation = service.handle("确认吧", "new-window")
        ordinal = service.interaction_coordinator.handle_control("第二个", "new-window")

        assert service.confirmation_manager.pending(scope="new-window") is None
        assert service.confirmation_manager.pending(scope="old-window") is not None
        assert confirmation.tool_results == []
        assert ordinal.action == "none"
        assert ordinal.selected_object_ids == []


def test_expired_confirmation_never_executes():
    now = [datetime(2026, 8, 1, 12, 0, 0)]
    coordinator = InteractionStateCoordinator(now_provider=lambda: now[0], ttl_seconds=30)
    coordinator.awaiting_confirmation(
        "expired-window", "dangerous_tool", immutable_arguments={"tool_name": "delete_plan"}
    )
    now[0] += timedelta(seconds=31)

    decision = coordinator.handle_control("确认", "expired-window")

    assert decision.action == "expired"
    assert decision.interaction is not None and decision.interaction.consumed


def test_two_ambiguous_write_clauses_create_no_plan_or_candidate():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle(
            "把你说的这些先添加进今天的计划，然后把我说的长期目标放进长期记忆",
            "ambiguous-batch",
        )

        assert response.status == "clarification"
        assert response.tool_results == []
        assert growth.tasks() == []
        assert service.memory_service.list_candidates().data["candidates"] == []
        assert "没有修改" in response.message
        assert "已添加" not in response.message


def test_clear_multi_query_remains_read_only_and_executes_both_queries():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("复习随机森林")

        response = service.handle("查看今天计划，然后查看行动记录", "multi-query")

        tools = [item.tool if hasattr(item, "tool") else item["tool"] for item in response.tool_results]
        assert response.status == "completed"
        assert "show_plan" in tools
        assert "show_action_log" in tools
        assert len(growth.tasks()) == 1
