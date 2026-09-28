from __future__ import annotations

import pytest

from modules.intent_router import IntentRouter


@pytest.mark.parametrize(
    "text",
    [
        "帮我安排一下今日计划，我上午没什么时间，下午三点以后可以学习",
        "安排今日计划：上午没什么时间，下午三点后可以学习",
        "我不是让你查看今日计划，我是想让你帮我安排一下",
        "不是查看计划，是想讨论我该先做什么",
        "不要展示今天的计划，我该先做什么",
        "我想看看应该怎么安排今天的计划",
        "查看今天的计划，顺便建议我先做哪项",
        "什么计划最容易坚持",
    ],
)
def test_stable_business_read_guard_does_not_capture_planning_or_rejection(text):
    router = IntentRouter(enable_llm=False)

    decision = router.route_semantic_decision(text)

    assert decision["intent"] == "chat"
    assert decision["source"] != "business_read_guard"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("昨天有什么计划", "show_plan"),
        ("明天都有哪些任务", "show_plan"),
        ("计划里有什么", "show_plan"),
        ("查看今天安排了什么", "show_plan"),
        ("看看我的行动记录", "show_action_log"),
        ("行动记录里有什么", "show_action_log"),
        ("查看有没有重复的计划", "inspect_plan_duplicates"),
    ],
)
def test_stable_business_read_guard_keeps_explicit_and_structured_reads(text, intent):
    router = IntentRouter(enable_llm=False)

    decision = router.route_semantic_decision(text)

    assert decision["intent"] == intent
    assert decision["source"] == "business_read_guard"
