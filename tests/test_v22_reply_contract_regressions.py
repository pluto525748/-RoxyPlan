from __future__ import annotations

import pytest

from modules.contracts import AgentResponse, ToolResult
from modules.response_composer import ResponseComposer


def chat_reply(message, *, context=None, user_text="你有什么功能"):
    return ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text=user_text,
        verified_turn_context=context or {"semantic_intent": "chat"},
    ).message


def test_reminder_state_change_cannot_be_claimed_by_chat_without_tool_result():
    reply = chat_reply("好，那我不提醒你。", user_text="接下来先别提醒我")

    assert reply != "好，那我不提醒你。"
    assert "没有成功更改提醒状态" in reply


def test_pet_sleep_chat_fallback_cannot_claim_execution():
    reply = chat_reply("好，我先休息一会儿。", user_text="洛琪希，你休息一下")

    assert reply != "好，我先休息一会儿。"
    assert "没有成功让桌宠进入睡眠" in reply


@pytest.mark.parametrize(
    "message",
    [
        "我可以帮你记录进展，也能陪你聊天。",
        "我可以帮你添加今日计划、记录行动，并一起复盘。",
        "我能帮你整理今日计划，也可以保存你明确要求记住的信息。",
        "如果你明确让我记住，我可以保存长期偏好。",
    ],
)
def test_capability_introduction_is_not_an_execution_assertion(message):
    assert chat_reply(message) == message


@pytest.mark.parametrize(
    "message",
    [
        "好，我记住了。以后辅导你学习时会多鼓励你。",
        "我已经记住了你的鼓励偏好。",
        "我记下了，以后会按这个偏好来。",
    ],
)
def test_memory_success_requires_a_real_write_result(message):
    reply = chat_reply(message, user_text="学习时多鼓励我一些")

    assert reply != message
    assert "还没有保存长期记忆" in reply


@pytest.mark.parametrize(
    "message",
    [
        "好，这次对话里我会多鼓励你，先把眼前这一步做好。",
        "可以，我们这轮就用轻松一点的方式交流。",
        "我把这段经历记录在小说里了。",
    ],
)
def test_current_conversation_preference_and_non_app_narration_are_chat(message):
    assert chat_reply(message, user_text="学习时多鼓励我一些") == message


@pytest.mark.parametrize(
    "message",
    [
        "你确认后，我会删除今天的全部计划。",
        "确认一下，之后我再把今天所有计划删掉。",
        "你只需回复确认，我就删除这些计划。",
        "请确认删除全部计划，确认后执行。",
    ],
)
def test_chat_cannot_fabricate_a_confirmation_transaction(message):
    reply = chat_reply(message, user_text="删除全部计划")

    assert reply != message
    assert "没有建立可执行的确认操作" in reply
    assert "确认后" not in reply


@pytest.mark.parametrize(
    "pending",
    [
        {"active": False, "capability": "delete_plan", "object_count": 3},
        {"active": "true", "capability": "delete_plan", "object_count": 3},
        {"active": True, "capability": "delete_plan", "object_count": 0},
        {"active": True, "capability": "complete_plan", "object_count": 3},
    ],
)
def test_confirmation_authority_must_be_typed_and_match_the_operation(pending):
    reply = chat_reply(
        "你确认后，我会删除今天的全部计划。",
        context={"confirmation_pending": pending},
    )

    assert "没有建立可执行的确认操作" in reply


@pytest.mark.parametrize(
    ("capability", "message"),
    [
        ("add_plan", "确认后我会把这三项加入今日计划。"),
        ("complete_plan", "你确认后，我会把这三项计划标记完成。"),
        ("save_formal_memory", "你确认后，我会把这个偏好保存到长期记忆。"),
    ],
)
def test_real_matching_pending_can_explain_its_next_step(capability, message):
    context = {
        "confirmation_pending": {
            "active": True,
            "capability": capability,
            "object_count": 3,
        }
    }

    assert chat_reply(message, context=context) == message


def test_real_pending_does_not_authorize_a_completed_write_claim():
    reply = chat_reply(
        "已经帮你把三项加入今日计划了。请确认。",
        context={
            "confirmation_pending": {
                "active": True,
                "capability": "add_plan",
                "object_count": 3,
            }
        },
    )

    assert "加入今日计划了" not in reply


def test_same_mutation_verb_cannot_cross_pending_data_domains():
    reply = chat_reply(
        "你确认后，我会删除这些长期记忆。",
        context={
            "confirmation_pending": {
                "active": True,
                "capability": "delete_plan",
                "object_count": 3,
            }
        },
    )

    assert "没有建立可执行的确认操作" in reply


def test_non_operation_confirmation_question_is_normal_chat():
    message = "你确认明天下午有空吗？我们可以聊聊学习安排。"

    assert chat_reply(message) == message


def test_verified_formal_memory_save_still_has_deterministic_success():
    result = ToolResult(
        True,
        "save_formal_memory",
        "saved",
        {"memory": {"id": 9, "content": "辅导学习时多鼓励我"}},
        operation_kind="write",
    )
    response = ResponseComposer().compose(
        AgentResponse("completed", "saved", tool_results=[result]),
        user_text="记住辅导学习时多鼓励我",
    )

    assert "记住" in response.message or "保存" in response.message
    assert "还没有保存" not in response.message


def test_successful_plan_write_does_not_authorize_a_memory_save_assertion():
    result = ToolResult(
        True,
        "add_plan",
        "added",
        {"task": {"id": 5, "title": "英语阅读"}},
        operation_kind="write",
    )
    response = ResponseComposer().compose(
        AgentResponse("completed", "好，我记住了。", tool_results=[result]),
        user_text="把英语阅读加进今日计划",
    )

    assert response.status == "completed"
    assert "记住了" not in response.message
    assert "计划" in response.message


def test_successful_action_write_can_say_it_recorded_the_action():
    message = "我记下了，今天的行动记录已经更新。"
    result = ToolResult(
        True,
        "add_action_log",
        "added",
        {"record": {"id": 6, "content": "阅读一个章节"}},
        operation_kind="write",
    )
    response = ResponseComposer().compose(
        AgentResponse("completed", message, tool_results=[result]),
        user_text="记录今天阅读一个章节",
    )

    assert response.message == message
