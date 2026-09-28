from modules.action_preview import ActionPreview, build_action_preview
from modules.interaction_state_coordinator import InteractionStateCoordinator


def test_preview_uses_immutable_resolved_arguments():
    arguments = {
        "tool_name": "update_plan",
        "task_ref": "task_real_1",
        "duration_minutes": 60,
    }
    preview = build_action_preview(arguments, safe_summary="修改真实计划为60分钟")
    arguments["duration_minutes"] = 5

    assert preview.tool_name == "update_plan"
    assert preview.changes["task_ref"] == "task_real_1"
    assert preview.changes["duration_minutes"] == 60
    assert preview.affects_existing_data is True
    assert preview.reversible is True


def test_confirmation_state_preserves_preview_and_arguments():
    coordinator = InteractionStateCoordinator()
    immutable = {"tool_name": "delete_plan", "task_ref": "task_real_2"}
    preview = build_action_preview(immutable, safe_summary="删除计划").to_dict()
    coordinator.awaiting_confirmation(
        "conversation",
        "dangerous_tool",
        immutable_arguments=immutable,
        action_preview=preview,
    )
    state = coordinator.current("conversation")
    assert state.immutable_arguments == immutable
    assert state.action_preview["tool_name"] == "delete_plan"
    assert state.action_preview["reversible"] is False


def test_preview_has_natural_language_confirmation_prompt():
    preview = ActionPreview("add_plan", "添加计划", {"title": "学习"}, reversible=True)
    message = preview.message()
    assert "我理解你想做的是" in message
    assert "回复“确认”" in message
