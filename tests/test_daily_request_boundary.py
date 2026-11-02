"""A delayed desktop-style turn keeps its submit-day and original session."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from modules.chinese_entity_parser import ChineseEntityParser
from modules.intent_router import LLMIntentParser
from modules.request_boundary import RequestBoundary, bind_request_boundary
from v18_test_support import build_service


class Clock:
    def __init__(self, value: str):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value


class DelayedSemanticClient:
    def __init__(self, clock, decision, *, on_return=None):
        self.clock = clock
        self.decision = decision
        self.on_return = on_return
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.on_return is not None:
            callback, self.on_return = self.on_return, None
            callback()
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(self.decision, ensure_ascii=False)
        return "隔离模型回复"


def _decision(intent, entities):
    return {
        "mode": "write", "intent": intent, "entities": entities,
        "proposed_tool": intent, "confidence": 0.98,
        "follow_up_target": None, "needs_confirmation": False,
        "warnings": [], "clarification_question": None,
        "candidate_actions": [], "subject": "self", "polarity": "positive",
        "modality": "commitment", "request_mode": "execute",
        "explicit_command": True,
    }


def _service(tmp_path, clock, decision, *, on_return=None):
    llm = DelayedSemanticClient(clock, decision, on_return=on_return)
    service, growth, _memory, history = build_service(tmp_path, llm)
    growth.now_provider = clock
    service.conversation_service.now_provider = clock
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service.conversation_service, growth, history, llm


def _submitted_turn(service, history, message, day="2026-10-09"):
    session = history.ensure_daily_learning_session(day)
    turn = service.prepare(
        message, session["session_id"], record_history=False,
        allow_llm_intent=False, business_date=day,
        require_existing_session=True,
    )
    assert turn.requires_llm
    assert turn.business_date == day
    assert turn.trace_context.session_id == session["session_id"]
    assert turn.request_id
    return turn


def test_delayed_today_plan_uses_submit_date_and_same_request_id(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "整理跨日笔记"}),
        on_return=lambda: setattr(clock, "value", datetime(2026, 10, 10, 0, 0, 2)),
    )
    turn = _submitted_turn(service, history, "今天加入计划：整理跨日笔记")
    response = service.complete(turn)
    assert response.status == "completed"
    assert response.request_id == turn.request_id
    assert response.conversation_id == turn.conversation_id
    assert "2026-10-09" in response.message
    assert [item["title"] for item in growth.tasks("2026-10-09")] == ["整理跨日笔记"]
    assert growth.tasks("2026-10-10") == []
    assert response.tool_results[0].data["task"]["date"] == "2026-10-09"


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        ("今天加入计划：读书", True),
        ("明天加入计划：读书", False),
        ("昨天加入计划：读书", False),
        ("2026年10月12日加入计划：读书", False),
        ("2026年10月9日加入计划：读书", True),
    ],
)
def test_plan_addition_only_accepts_submit_day(tmp_path, wording, expected):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "读书"}),
        on_return=lambda: setattr(clock, "value", datetime(2026, 10, 10, 0, 0, 2)),
    )
    turn = _submitted_turn(service, history, wording)
    response = service.complete(turn)
    assert (response.status == "completed") is expected
    if expected:
        assert response.tool_results[0].data["task"]["date"] == "2026-10-09"
        assert [item["title"] for item in growth.tasks("2026-10-09")] == ["读书"]
    else:
        assert "目前只支持安排今天的计划" in response.message
        assert response.tool_results == []
        assert growth.tasks("2026-10-09") == []


def test_parser_uses_request_date_without_changing_real_clock():
    clock = Clock("2026-10-10T00:00:02")
    boundary = RequestBoundary("origin", "2026-10-09", "明天查看计划", clock)
    parser = ChineseEntityParser(now_provider=clock)
    with bind_request_boundary(boundary):
        assert parser.parse("今天查看计划")["date"] == "2026-10-09"
        assert parser.parse("明天查看计划")["date"] == "2026-10-10"
        assert parser.parse("昨天查看计划")["date"] == "2026-10-08"
        assert parser.parse("2026-10-12查看计划")["date"] == "2026-10-12"
    assert parser.parse("今天查看计划")["date"] == "2026-10-10"


def test_mixed_relative_and_absolute_date_does_not_guess_write_target(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "复习报告"}),
    )
    turn = _submitted_turn(
        service, history, "今天加入计划：复习2026年10月12日的报告"
    )
    response = service.complete(turn)
    assert response.status != "completed"
    assert "目前只支持安排今天的计划" in response.message
    assert response.tool_results == []
    assert growth.tasks("2026-10-09") == []
    assert growth.tasks("2026-10-12") == []


@pytest.mark.parametrize(
    ("wording", "allowed"),
    [
        ("今晚安排读书", True),
        ("今天下午整理笔记", True),
        ("2026年10月9日安排读书", True),
        ("明天上午读书，帮我记到计划里", False),
        ("下周读书，帮我记到计划里", False),
        ("今天读书，明天练习，都加入计划", False),
        ("把《明天会更好》加入今天计划", True),
        ("为了明天的考试，今天复习算法，加入计划", True),
    ],
)
def test_plan_target_date_ignores_quoted_title_and_causal_background(wording, allowed):
    boundary = RequestBoundary("origin", "2026-10-09", wording, Clock("2026-10-09T15:00:00"))
    assert boundary.plan_add_date_allowed() is allowed


def test_model_date_cannot_override_user_today_or_hide_tomorrow():
    today = RequestBoundary("origin", "2026-10-09", "今天整理笔记", Clock("2026-10-09T15:00:00"))
    tomorrow = RequestBoundary("origin", "2026-10-09", "明天整理笔记", Clock("2026-10-09T15:00:00"))
    assert today.plan_add_date_allowed("2026-10-10") is False
    assert tomorrow.plan_add_date_allowed("") is False
    assert today.plan_add_date_allowed("2026-10-09") is True


@pytest.mark.parametrize(("wording", "expected"), [
    ("明天上午还想读一篇数据库索引的文章，预计半小时，也帮我记到计划里。", False),
    ("今天复习算法，明天读文档，都加到计划里。", False),
    ("把《明天会更好》加入今天计划。", True),
    ("为了明天的考试，今天复习算法，加入计划。", True),
])
def test_chat_add_plan_checks_original_target_even_when_model_omits_date(tmp_path, wording, expected):
    clock = Clock("2026-10-09T14:00:00")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "隔离新增测试"}),
    )
    turn = _submitted_turn(service, history, wording)
    response = service.complete(turn)
    assert bool(growth.tasks("2026-10-09")) is expected
    if not expected:
        assert response.status == "failed"
        assert "目前只支持安排今天的计划" in response.message
        if response.tool_results:
            assert response.tool_results[0].error == "unsupported_plan_date"


def test_deleted_origin_stops_delayed_write_without_recreation(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "不能写入"}),
        on_return=lambda: history.delete_session(turn.conversation_id),
    )
    turn = _submitted_turn(service, history, "今天加入计划：不能写入")
    response = service.complete(turn)
    assert response.status == "failed"
    assert history.get_session(turn.conversation_id) is None
    assert history.sessions() == []
    assert growth.tasks("2026-10-09") == []


def test_cross_day_unsafe_plan_write_rejects_without_touching_new_day(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("complete_plan", {"query": "隔离任务"}),
        on_return=lambda: setattr(clock, "value", datetime(2026, 10, 10, 0, 0, 2)),
    )
    previous = growth.add_task("隔离任务", date="2026-10-09")
    today = growth.add_task("隔离任务", date="2026-10-10")
    turn = _submitted_turn(service, history, "完成今天的隔离任务")
    response = service.complete(turn)
    assert response.status != "completed"
    assert "日期" in response.message
    assert not growth.tasks("2026-10-09")[0]["done"]
    assert not growth.tasks("2026-10-10")[0]["done"]
    assert previous["uid"] != today["uid"]


def test_same_day_request_and_action_record_keep_business_date(tmp_path):
    clock = Clock("2026-10-09T14:00:00")
    service, growth, history, _llm = _service(
        tmp_path, clock,
        _decision("add_action_log", {"content": "完成隔离练习", "duration_minutes": 25}),
    )
    turn = _submitted_turn(service, history, "今天记录行动：完成隔离练习，25分钟")
    response = service.complete(turn)
    assert response.status == "completed"
    assert response.request_id == turn.request_id
    record = growth.records_for_date("2026-10-09")[0]
    assert record["content"] == "完成隔离练习"
    assert record["duration_minutes"] == 25
    assert growth.records_for_date("2026-10-10") == []


def test_cross_day_action_and_review_bind_original_day(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "unused"})
    )
    session_id = history.ensure_daily_learning_session("2026-10-09")["session_id"]
    growth.add_task("隔离复盘事项", date="2026-10-09")
    clock.value = datetime(2026, 10, 10, 0, 0, 2)
    boundary = RequestBoundary(
        session_id, "2026-10-09", "今天记录行动：完成阅读", clock,
        session_exists=lambda value: history.get_session(value) is not None,
    )
    with bind_request_boundary(boundary):
        action = service.agent_core.executor.execute(
            "add_action_log", {"content": "完成阅读"},
            confidence=1.0, authorization_granted=True,
        )
    assert action.success
    assert action.data["record"]["date"] == "2026-10-09"
    assert action.data["record"]["time"] == "00:00:02"
    assert growth.records_for_date("2026-10-10") == []

    review_boundary = RequestBoundary(
        session_id, "2026-10-09", "今日复盘", clock,
        session_exists=lambda value: history.get_session(value) is not None,
    )
    with bind_request_boundary(review_boundary):
        review = service.agent_core.executor.execute(
            "save_daily_review", {}, confidence=1.0, authorization_granted=True,
        )
    assert review.success
    assert review.data["entry"]["date"] == "2026-10-09"
    assert growth.entries()[0]["date"] == "2026-10-09"


def test_cross_day_partial_execution_and_retry_report_real_outcome(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "unused"})
    )
    session_id = history.ensure_daily_learning_session("2026-10-09")["session_id"]
    boundary = RequestBoundary(
        session_id, "2026-10-09", "今天先加计划，再完成计划", clock,
        session_exists=lambda value: history.get_session(value) is not None,
    )
    executor = service.agent_core.executor
    with bind_request_boundary(boundary):
        first = executor.execute(
            "add_plan", {"title": "跨日批次第一项"},
            confidence=1.0, authorization_granted=True,
            tool_call_id="call_cross_day_first",
        )
        clock.value = datetime(2026, 10, 10, 0, 0, 2)
        second = executor.execute(
            "complete_plan", {"match_text": "跨日批次第一项"},
            confidence=1.0, authorization_granted=True,
            tool_call_id="call_cross_day_second",
        )
        retried = executor.execute(
            "add_plan", {"title": "跨日批次第一项"},
            confidence=1.0, authorization_granted=True,
            tool_call_id="call_cross_day_first",
        )
        message = service.agent_core._partial_message([first, second])
    assert first.success and retried.success
    assert not second.success and second.error == "request_date_changed"
    assert "跨日批次第一项" in message and "日期已变化" in message
    assert len(growth.tasks("2026-10-09")) == 1
    assert not growth.tasks("2026-10-09")[0]["done"]
    assert growth.tasks("2026-10-10") == []


def test_agent_batch_reports_success_before_cross_day_rejection(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, growth, history, _llm = _service(
        tmp_path, clock, _decision("add_plan", {"title": "unused"})
    )
    session_id = history.ensure_daily_learning_session("2026-10-09")["session_id"]
    boundary = RequestBoundary(
        session_id, "2026-10-09", "今天加入并完成隔离计划", clock,
        session_exists=lambda value: history.get_session(value) is not None,
    )
    definition = service.agent_core.executor.registry.get("add_plan")
    original_handler = definition.handler

    def add_then_cross_midnight(**kwargs):
        outcome = original_handler(**kwargs)
        clock.value = datetime(2026, 10, 10, 0, 0, 2)
        return outcome

    definition.handler = add_then_cross_midnight
    with bind_request_boundary(boundary):
        response = service.agent_core.execute_model_tool_calls(
            "今天加入并完成隔离计划",
            [
                {"name": "add_plan", "arguments": {"title": "隔离计划"}},
                {"name": "complete_plan", "arguments": {"match_text": "隔离计划"}},
            ],
            confidence=0.99,
            confirmation_scope=session_id,
        )
    assert response.status == "partial_success"
    assert [item.success for item in response.tool_results] == [True, False]
    assert "隔离计划" in response.message
    assert "日期已变化" in response.message
    assert len(growth.tasks("2026-10-09")) == 1
    assert not growth.tasks("2026-10-09")[0]["done"]


def test_month_boundary_uses_submit_month_for_implicit_growth_query(tmp_path):
    clock = Clock("2026-10-31T23:59:58")
    service, _growth, history, _llm = _service(
        tmp_path, clock, _decision("show_growth_log", {})
    )
    session_id = history.ensure_daily_learning_session("2026-10-31")["session_id"]
    clock.value = datetime(2026, 11, 1, 0, 0, 2)
    boundary = RequestBoundary(session_id, "2026-10-31", "查看本月成长日志", clock)
    with bind_request_boundary(boundary):
        response = service.agent_core.executor.execute("show_growth_log", {})
    assert response.success
    assert response.data["month"] == "2026-10"


def test_unbound_historical_query_is_rejected_after_day_change(tmp_path):
    clock = Clock("2026-10-09T23:59:58")
    service, _growth, history, _llm = _service(
        tmp_path, clock, _decision("show_conversation_history", {})
    )
    session_id = history.ensure_daily_learning_session("2026-10-09")["session_id"]
    clock.value = datetime(2026, 10, 10, 0, 0, 2)
    boundary = RequestBoundary(session_id, "2026-10-09", "查看昨天的对话", clock)
    with bind_request_boundary(boundary):
        response = service.agent_core.executor.execute(
            "show_conversation_history", {"query": "昨天"}
        )
    assert not response.success
    assert response.error == "request_date_changed"
