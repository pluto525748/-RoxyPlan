"""回复守卫的语境边界：两处已捕获案例的最小修正与必须保留的负例。

证据来源（只读）：``test_workspace/runs/20261014-deepseek-expanded/``

- G01-3：用户“先把第一周的学习思路讲清楚，不用加入今天计划。”。模型给出了完整的
  第一周讲解，末句“等你确认了是哪种考试或面试……”是目标方向澄清，却被当成授权
  执行的确认；它与首句“不动你的计划记录”被跨句拼成一次交易，整段讲解被替换成
  “没有建立可执行的确认操作”。
- G26-2：用户“我只是想和你聊聊，不用安排任务。”。模型回“今天没有计划，也不需要
  有。”，没有本轮读取工具；命中空数据声明后被整段替换成“需要先读取实际记录”。

本文件固定修正后的边界：确认词必须是被授权执行的形状（请确认／你确认后／回复确认
指向即将执行的写操作），目标方向澄清与讲解不算授权；没有读取依据的空数据声明只
移除该句、保留其余可独立成立的聊天。防假执行、防假确认、防跨域确认和真实
ToolResult 的成功反馈都不变。
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import AgentResponse, ToolResult
from modules.response_composer import ResponseComposer


# 捕获的原始回答原文（逐字保留，不做改写）。
RAW_G01_3 = (
    "好，只讲第一周思路，不动你的计划记录。\n\n"
    "第一周的目标不是“学会 Python”，而是建立三样东西：能跑代码的环境、能读懂报错、"
    "能自己写出十几行的小程序。考试和面试里，卡住人的往往不是难题，而是基础语法"
    "不熟、看代码反应慢。\n\n"
    "七天可以这样分：\n\n"
    "第1天，环境。装好 Python，跑通 print，学会在终端或编辑器里运行脚本。重点是别在"
    "这一步耗太久，装不上就换在线环境先写。\n\n"
    "第2天，变量和类型。整数、浮点、字符串、布尔，以及 type() 查看类型。面试常问的"
    "就是类型转换和字符串与数字的区别。\n\n"
    "第3天，条件判断。if/elif/else，比较运算符，and/or/not。练到能一眼看懂嵌套判断"
    "的执行顺序。\n\n"
    "第4天，循环。for 和 while，range 的用法，break 和 continue。这是考试读程序题的"
    "重灾区，值得多花时间。\n\n"
    "第5天，列表和元组。索引、切片、增删改查、遍历。切片规则要练熟，面试很爱问。\n\n"
    "第6天，字典和集合。键值对、遍历、常用方法。字典几乎是 Python 面试必考。\n\n"
    "第7天，综合。写一个小程序，比如统计一段文字里每个词出现的次数。这一天的作用是"
    "把前六天的东西连起来。\n\n"
    "几个提醒：第一周不要碰面向对象和第三方库，太早会乱；每天的重点是动手写，不是"
    "看视频；遇到报错先自己读一遍错误信息，这是面试里很值钱的能力。\n\n"
    "等你确认了是哪种考试或面试，我再把这七天按它的侧重调整。"
)
RAW_G26_2 = (
    "好，那就不安排。今天没有计划，也不需要有。\n\n"
    "想聊什么都可以，哪怕只是说说今天为什么提不起劲。我在这儿听着。"
)

G01_3_USER_TEXT = "先把第一周的学习思路讲清楚，不用加入今天计划。"
G26_2_USER_TEXT = "我只是想和你聊聊，不用安排任务。"

# 最终 compose 阶段实际传出的上下文形状（conversation_service.complete）：
# 只有 response_preferences 与 confirmation_pending，没有本轮执行事实。
FINAL_PASS_CONTEXT = {"response_preferences": {}, "confirmation_pending": {}}


def _chat(message, *, user_text="随便聊聊", context=None):
    return ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text=user_text,
        verified_turn_context=(
            dict(FINAL_PASS_CONTEXT) if context is None else context
        ),
    ).message


# ---------------------------------------------------------------- 捕获案例回放


def test_captured_week_plan_talk_is_not_replaced_as_a_transaction():
    """G01-3：方向澄清不是授权执行，讲解保持原文。"""
    reply = _chat(RAW_G01_3, user_text=G01_3_USER_TEXT)

    assert reply == RAW_G01_3
    assert "没有建立可执行的确认操作" not in reply
    assert "等你确认了是哪种考试或面试" in reply


def test_captured_chat_keeps_conversation_and_drops_the_unread_claim():
    """G26-2：没有读取依据的空数据声明被移除，其余闲聊保留。"""
    reply = _chat(RAW_G26_2, user_text=G26_2_USER_TEXT)

    assert "好，那就不安排。" in reply
    assert "我在这儿听着" in reply
    assert "今天没有计划" not in reply
    assert "读取实际记录" not in reply


# ------------------------------------------------------------ 空数据声明最小纠正


def test_absence_claim_alone_still_requires_a_real_read():
    reply = _chat("今天还没有计划。", user_text="我今天有计划吗")

    assert reply != "今天还没有计划。"
    assert "读取实际记录" in reply


def test_absence_claim_mixed_with_independent_chat_keeps_that_chat():
    # 当前边界：只移除声明所在句。声明句本身被整句移除，不留下残缺分句。
    reply = _chat("今天还没有计划。你可以先休息一下。", user_text="我今天有计划吗")

    assert reply == "你可以先休息一下。"


def test_any_reply_without_a_read_loses_its_empty_data_claim():
    messages = (
        "今天没有计划。",
        "现在没有任务，不过可以先聊聊。",
        "还没有任何行动记录。",
        "目前没有长期记忆。",
    )

    for message in messages:
        reply = _chat(message, user_text="随便聊聊")
        for claim in ("没有计划", "没有任务", "没有行动记录", "没有长期记忆"):
            assert claim not in reply, (message, reply)


# ------------------------------------------------------------ 真实交易仍被拦截


def test_fabricated_confirmation_is_blocked_even_across_two_sentences():
    messages = (
        "你确认后，我会删除今天的全部计划。",
        "确认一下，之后我再把今天所有计划删掉。",
        "你只需回复确认，我就删除这些计划。",
        "请确认删除全部计划，确认后执行。",
        "我可以帮你把「复习算法」加入今天的计划。你确认后我就执行。",
        "目前计划还没有建立，请回复确认，我就把这三项加进清单。",
        "把我今天的计划全部删除。你确认后我就执行。",
        "要我把今天的计划全部删除吗？你确认一下我就删。",
    )

    for message in messages:
        reply = _chat(message, user_text="删除全部计划")
        assert reply != message, message
        assert "没有建立可执行的确认操作" in reply, message


def test_direction_clarification_is_not_an_execution_authorization():
    messages = (
        "这段只讲第一周思路，不会动你的计划记录。\n\n等你确认了方向，我再按它的侧重调整。",
        "好，我先只讲思路，不动你的计划记录，你确认方向后我再调整。",
        "这段讲解不会写入计划，等你确认目标后我再细化。",
        "第5天练习索引、切片、增删改查。这些内容都不会写入记录，放松看就行。",
        "你确认明天下午有空吗？我们可以聊聊学习安排。",
        "我知道你现在不想被安排，那就不安排。",
    )

    for message in messages:
        reply = _chat(message, user_text=G01_3_USER_TEXT)
        assert reply == message, message


def test_real_pending_still_explains_the_next_step_and_cross_domain_still_blocks():
    pending = {
        "confirmation_pending": {
            "active": True,
            "capability": "add_plan",
            "object_count": 3,
        }
    }
    allowed = "你确认后，我会把这三项加入今日计划。"
    assert _chat(allowed, context=pending) == allowed

    cross_domain = "你确认后，我会删除这些长期记忆。"
    assert "没有建立可执行的确认操作" in _chat(cross_domain, context=pending)


def test_false_execution_claims_are_still_corrected_next_to_absence_claims():
    cases = (
        "我已经把今天的计划全部删除了。",
        "好，我帮你把“简历包装”加入今天计划了。",
        "我现在帮你把它记进长期记忆。",
    )

    for message in cases:
        reply = _chat(message, user_text="加进今日计划")
        assert reply != message, message
        assert "删除" not in reply and "加入今天计划了" not in reply


# ------------------------------------------------------ 真实结果与成功反馈不变


def test_verified_read_and_write_results_are_not_downgraded():
    empty_read = ResponseComposer().compose(
        AgentResponse(
            "completed",
            "今天还没有计划。",
            tool_results=[
                ToolResult(
                    True,
                    "show_plan",
                    "listed",
                    {"tasks": []},
                    operation_kind="read",
                )
            ],
        ),
        user_text="我今天有计划吗",
    )
    assert empty_read.message == "今天还没有计划。"

    added = ResponseComposer().compose(
        AgentResponse(
            "completed",
            "",
            tool_results=[
                ToolResult(
                    True,
                    "add_plan",
                    "added",
                    {"task": {"id": 5, "title": "英语阅读"}},
                    operation_kind="write",
                )
            ],
        ),
        user_text="把英语阅读加进今日计划",
    )
    assert "加入今天的清单" in added.message
    assert "需要先读取" not in added.message
