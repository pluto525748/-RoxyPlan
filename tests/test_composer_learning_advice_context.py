"""Offline replay of v3 g1/g2 d2-t02; original API prose is preserved verbatim.

Source: learning-week-six-online-20261024-v3/{g1,g2}/turns/*-d2-t02/
result.json raw_visible_responses. No transport, application service, or Qt.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import AgentResponse, ToolResult
from modules.response_composer import ResponseComposer


RAW_G1 = (
    "昨天两项作用域练习都完成了，还额外整理了 5 分钟概念卡；今天计划还是空的。"
    "按你每天最多 45 分钟、用小例子检查理解的目标，可以这样安排：\n\n"
    "1. 用 for 循环遍历列表和字符串，分别打印迭代过程，20分钟。"
    "观察每次取到的值，确认你理解“迭代一次取一个”的感觉。\n\n"
    "2. 自己写一个最简单的迭代器类，实现 __iter__ 和 __next__，再用 next() 手动取几次值，20分钟。"
    "重点是看到 StopIteration 是怎么出现的。\n\n"
    "两条加起来约 40 分钟，留一点余量。先别急着记语法，跑起来看输出更直观。"
)
RAW_G2 = (
    "昨天完成了路径拼接与读取，计划外还整理概念卡 35 分钟；新建、改名与删除没做完。"
    "今天按文本读写主题，把候选控制在适合工作日晚上、能单独完成的小规模，先只展示，等你选：\n\n"
    "1. 用虚构文件练习文本读取与写入，30分钟。先写一段内容再读回来核对，正好接上昨天的读取练习。\n"
    "2. 用虚构文件练习追加写入与按行读取，25分钟。内容比上一项少一点，适合晚上精力有限时做。"
)
USER_G1 = "今天想练习Python迭代器。请参考已保存目标、昨日复盘和真实执行，给两条今天可做的具体建议，先别加入计划。"
USER_G2 = "昨天的执行留在复盘里了。今天的主题是文本读写，结合实际完成和用时给我两个可选行动；只展示，等我选。"

# Full composer context shape, corresponding to the recorded zero-write summary.
NO_EXECUTION = {
    "execution": {"performed": False, "tools": [], "verified_changes": []},
    "recorded_plan_state": {"pending_titles": [], "completed_titles": []},
    "user_reported_plan_progress": False,
    "confirmation_pending": {},
    "prior_verified_operation": {},
    "prior_memory_facts": [],
}


def chat(message, user_text=USER_G1):
    response = AgentResponse("chat", message)
    composed = ResponseComposer().compose(
        response, user_text=user_text, verified_turn_context=NO_EXECUTION,
    )
    assert composed.tool_results == []
    return composed.message


@pytest.mark.parametrize(
    "raw,user", [(RAW_G1, USER_G1), (RAW_G2, USER_G2)], ids=["g1-d2-t02", "g2-d2-t02"],
)
def test_captured_history_and_two_suggestions_survive(raw, user):
    assert chat(raw, user) == raw


@pytest.mark.parametrize("message", [
    "我会先讲解，不会加入今天计划。",
    "我会只展示候选，不会保存长期记忆。",
    "我会先说明，不会删除今天计划，也不会更新记录。",
])
def test_explicit_negative_is_not_a_committed_write(message):
    assert chat(message) == message


@pytest.mark.parametrize("message", [
    "等你确认学习方向，我再按它调整，暂时不需要加入计划。",
    "我再整理成计划，现在还不加入今天的列表。",
    "我会先讲清楚，不必添加今天的任务。",
    "我会陪你分析，暂时不用保存长期记忆。",
    "我会先讲解，不要加入今天计划。",
    "我会讲清楚，不打算保存正式记忆。",
])
def test_negative_write_auxiliary_preserves_read_only_learning_reply(message):
    # First two are the previously documented guard examples; remaining
    # sentences are declared synthetic controls, not captured model outputs.
    assert chat(message) == message


@pytest.mark.parametrize("claim", [
    "我会先讲清楚，不需要解释就加入今天计划。",
    "我会先说明，不必等你回复就保存长期记忆。",
    "我会先讲解，不要担心，我会加入今天计划。",
    "我会加入今天计划，暂时不需要保存长期记忆。",
    "我会讲解，不打算保存例子，但会加入今天计划。",
])
def test_negative_auxiliary_does_not_hide_a_separate_write_commitment(claim):
    assert chat(claim) != claim


@pytest.mark.parametrize("claim", [
    "我会把这两项加入今天计划。",
    "我会先讲解，然后加入今天计划。",
    "我会先不保存讲解，然后加入今天计划。",
    "我会先加入今天计划，但不会保存长期记忆。",
    "我会先讲解，不会加入今天计划。" + "先讲清楚每一步的目的。" * 5 + "我会保存长期记忆。",
    "我已经把今天的练习标记完成了。",
    "已经把今天的练习标记完成了。",
    "今日计划已完成。",
    "我已经保存这条长期记忆。",
])
def test_history_or_negation_cannot_hide_an_independent_false_write(claim):
    message = "昨天完成了练习。今天只展示建议，不会写入计划。" + claim
    assert chat(message) != message


@pytest.mark.parametrize("tool", ["pause_reminders", "resume_reminders"])
def test_unavailable_reminder_adapter_reports_failure_in_chinese(tool):
    result = ToolResult(False, tool, "unavailable", error="pet_unavailable")
    response = ResponseComposer().compose(
        AgentResponse("failed", "unavailable", tool_results=[result]),
    )
    assert "本次没有改变提醒状态" in response.message
    assert "unavailable" not in response.message
    assert not response.tool_results[0].success
    assert response.tool_results[0].error_code == "pet_unavailable"


@pytest.mark.parametrize("tool,message", [
    ("pause_reminders", "主动提醒已暂停。"),
    ("resume_reminders", "主动提醒已恢复。"),
])
def test_verified_reminder_success_keeps_its_actual_result(tool, message):
    result = ToolResult(True, tool, message)
    response = ResponseComposer().compose(
        AgentResponse("completed", "", tool_results=[result]),
    )
    assert response.message == message
    assert response.tool_results[0].success
