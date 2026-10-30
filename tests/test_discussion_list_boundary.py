"""Offline reproduction of goal choices versus adoptable daily tasks.

The reported conversation proves a reply deviation only. These controlled
fixtures prove this implementation's state/tool/data chain, not the original
user's persistence or the accuracy of a generated Python course.
"""
from datetime import datetime
import json

import pytest

from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from v18_test_support import RecordingLLM, build_service


REQUEST = "给我安排一个30天的python语言学习计划"
GOALS = (
    "你希望学完后达到什么目标？先选一个方向：\n"
    "1. 能独立写小脚本，处理文件、爬点数据这种\n"
    "2. 打好基础，之后接机器学习\n"
    "3. 应付考试或面试"
)
LONG_OUTLINE = "按考试或面试方向，可以分阶段练习：\n1. 第1周：基础语法\n2. 第2周：数据结构\n3. 第3至4周：模拟题与复习"
DAILY = "今天可以先做：\n1. 复习变量，20分钟\n2. 练习列表，30分钟\n3. 整理错题，15分钟"


def runtime(tmp_path, monkeypatch, replies):
    llm = RecordingLLM()
    queue = iter(replies)

    def chat(messages):
        llm.calls.append(messages)
        return next(queue)

    llm.chat = chat
    agent, growth, memory, history = build_service(tmp_path, llm)
    service = agent.conversation_service
    now = lambda: datetime(2026, 10, 14, 16, 0)
    service.now_provider = service.business_now_provider = now
    service.interaction_coordinator.now_provider = now
    growth.now_provider = now
    # Fixed identifiable preconditions through the existing public manager.
    for title in ("边界回归-英语阅读", "边界回归-整理笔记", "边界回归-复习数学"):
        growth.add_task(title)
    session = history.ensure_session(restore_latest=False)["session_id"]
    parser = service.semantic_action_parser
    original = parser.parse

    def parse(text, *args, **kwargs):
        if text == "总计划在哪里能输入":
            # Controlled wrong model proposal, not evidence of the original
            # model JSON: the method question must override this read action.
            return SemanticParseResult(
                source="controlled_model", request_mode="query", plain_chat_probability=0.0,
                candidates=[ActionCandidate(domain="plan", tool_name="show_plan", arguments={},
                                            request_mode="query", confidence=1.0)],
                intent_result={"intent": "show_plan", "mode": "read", "request_mode": "query",
                               "confidence": 1.0, "entities": {}, "source": "controlled_model",
                               "subject": "self", "polarity": "positive", "modality": "question",
                               "request_id": "req_help_misread"},
            )
        if text in {REQUEST, "请给我今天的学习计划建议", "请给我今天的学习计划建议：先不要加入计划"}:
            return SemanticParseResult(
                source="controlled_model", candidates=[], request_mode="advice", plain_chat_probability=1.0,
                intent_result={"intent": "chat", "mode": "chat", "request_mode": "advice",
                               "confidence": 1.0, "entities": {}, "source": "controlled_model",
                               "subject": "self", "polarity": "positive", "modality": "statement",
                               "request_id": "req_discussion_boundary"},
            )
        return original(text, *args, **kwargs)

    monkeypatch.setattr(parser, "parse", parse)
    monkeypatch.setattr(parser, "parse_unified", parse)
    tools = []
    execute = agent.tool_executor.execute

    def observe(*args, **kwargs):
        tools.append(args[0] if args else kwargs.get("tool_name"))
        return execute(*args, **kwargs)

    monkeypatch.setattr(agent.tool_executor, "execute", observe)
    return service, growth, memory, llm, session, tools


def evidence(tmp_path, service, growth, session, before_state, before, response, tools):
    (tmp_path / "responsibility-chain.json").write_text(json.dumps({
        "state_before_choice": before_state,
        "state_after_choice": service.interaction_coordinator.current(session).to_dict(),
        "tools": tools, "results": [result.to_dict() for result in response.tool_results],
        "plans_before": before, "plans_after": growth.tasks(), "reply": response.message,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("choice", ["3", "第三项", "考试或面试", "3. 应付考试或面试"])
def test_goal_choice_continues_discussion_with_zero_daily_writes(tmp_path, monkeypatch, choice):
    service, growth, memory, llm, session, tools = runtime(tmp_path, monkeypatch, [GOALS, LONG_OUTLINE])
    before = growth.tasks()
    plan_bytes = growth.plan_file.read_bytes()
    memory_bytes = (tmp_path / "memory.json").read_bytes()
    offered = service.handle(REQUEST, session)
    state = service.interaction_coordinator.current(session).to_dict()
    assert offered.tool_results == []
    response = service.handle(choice, session)
    evidence(tmp_path, service, growth, session, state, before, response, tools)
    assert tools == []
    assert growth.tasks() == before
    assert growth.plan_file.read_bytes() == plan_bytes
    assert (tmp_path / "memory.json").read_bytes() == memory_bytes
    assert response.tool_results == []
    assert "已添加" not in response.message and "已加入" not in response.message
    assert "模拟题" in response.message
    assert memory.memories() == []
    assert len(llm.calls) == 2
    assert REQUEST in llm.calls[-1][-1]["content"]
    assert "应付考试或面试" in llm.calls[-1][-1]["content"]
    assert not service.interaction_coordinator.current(session).last_suggestion_snapshot


@pytest.mark.parametrize("reply", [
    GOALS,
    "先确认目标方向再安排今天的学习，选择哪个？\n1. 考试准备\n2. 项目练习\n3. 兴趣探索",
    LONG_OUTLINE,
])
def test_non_task_lists_never_bind_add_plan_even_in_advice_mode(tmp_path, monkeypatch, reply):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [reply])
    before = growth.tasks()
    response = service.handle("请给我今天的学习计划建议", session)
    state = service.interaction_coordinator.current(session)
    assert state.immutable_arguments.get("tool_name") != "add_plan"
    assert not state.last_suggestion_snapshot
    assert tools == [] and response.tool_results == [] and growth.tasks() == before


@pytest.mark.parametrize("text", ["总计划在哪里能输入", "告诉我怎么添加计划"])
def test_plan_operation_help_uses_current_rules_without_model_claims(tmp_path, monkeypatch, text):
    service, growth, _memory, llm, session, tools = runtime(
        tmp_path, monkeypatch, ["你可以添加任意日期，例如明天也可以加入计划。"])
    before = growth.tasks()
    response = service.handle(text, session)
    evidence(tmp_path, service, growth, session, {}, before, response, tools)
    assert tools == [] and response.tool_results == [] and growth.tasks() == before
    assert "今天" in response.message and "只" in response.message
    assert "任意日期" not in response.message
    assert "尚未" in response.message or "不支持" in response.message
    assert llm.calls == []


def test_today_suggestions_keep_single_then_remaining_adoption(tmp_path, monkeypatch):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [DAILY])
    service.handle("请给我今天的学习计划建议", session)
    first = service.handle("第二项", session)
    remaining = service.handle("剩下的都加入今天计划", session)
    assert [result.tool for result in first.tool_results] == ["add_plan"]
    assert first.tool_results[0].success
    assert [task["title"] for task in growth.tasks()][3:] == ["练习列表", "复习变量", "整理错题"]
    assert all(task["date"] == "2026-10-14" for task in growth.tasks())
    assert len(remaining.tool_results) == 2 and all(result.success for result in remaining.tool_results)
    assert tools == ["add_plan"] * 3


@pytest.mark.parametrize("control", ["取消", "不要把这些加入计划"])
def test_daily_suggestion_cancellation_writes_nothing(tmp_path, monkeypatch, control):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [DAILY])
    before = growth.tasks()
    service.handle("请给我今天的学习计划建议：先不要加入计划", session)
    response = service.handle(control, session)
    assert tools == [] and response.tool_results == [] and growth.tasks() == before


DAY_ACTIONS = "先讨论30天方案中的两天示例：\n1. 第1天：复习变量，20分钟\n2. 第2天：练习列表，30分钟"


def test_first_day_requires_explicit_single_today_add_after_goal_choice(tmp_path, monkeypatch):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [GOALS, DAY_ACTIONS])
    service.handle(REQUEST, session)
    discussion = service.handle("第三项", session)
    assert tools == [] and discussion.tool_results == []
    response = service.handle("把第一天这一项加入今天计划", session)
    assert tools == ["add_plan"]
    assert response.tool_results[0].success
    task = growth.tasks()[-1]
    assert task["title"] == "复习变量" and task["duration_minutes"] == 20
    assert task["date"] == "2026-10-14" and len(growth.tasks()) == 4


def test_first_day_cannot_be_saved_to_tomorrow(tmp_path, monkeypatch):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [DAY_ACTIONS])
    before = growth.plan_file.read_bytes()
    service.handle(REQUEST, session)
    response = service.handle("把第一天这一项加入明天计划", session)
    assert tools == [] and response.tool_results == []
    assert growth.plan_file.read_bytes() == before
    assert "今天" in response.message


def test_direction_list_replaces_an_old_adoptable_daily_snapshot(tmp_path, monkeypatch):
    service, growth, _memory, _llm, session, tools = runtime(tmp_path, monkeypatch, [DAILY, GOALS, LONG_OUTLINE])
    service.handle("请给我今天的学习计划建议", session)
    assert service.interaction_coordinator.current(session).last_suggestion_snapshot
    before = growth.plan_file.read_bytes()
    service.handle(REQUEST, session)
    service.handle("3", session)
    assert tools == [] and growth.plan_file.read_bytes() == before
    assert not service.interaction_coordinator.current(session).last_suggestion_snapshot
