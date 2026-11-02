"""Original two-day engineering replies via the real shared offline transport.

d2-t03 never called a model originally. Its post-fix reply below is an explicit
synthetic fixture, not a captured answer or proof of model learning quality.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from modules.contracts import AgentResponse
from modules.response_composer import ResponseComposer, _claims_unverified_plan_change
from test_beta_online_failure_replay import _build_service, _provider_body


FIXTURE = json.loads((Path(__file__).parent / "fixtures/learning_loop_online_boundaries_20261026.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in FIXTURE["cases"]}
RAW_ADVICE = CASES["d1-t03"]["raw_visible_responses"][1]["choices"][0]["message"]["content"]
SYNTHETIC_REFINEMENT = (
    "按你这轮总共20分钟的限制，先巩固昨天没弄懂的过滤条件，再继续文件读取。\n"
    "1. 巩固列表推导式条件过滤，12分钟。先自己判断空列表，再看例子。\n"
    "2. 练习读取文本文件，8分钟。用小文件核对读出的内容。\n"
    "先只展示建议，没有加入计划。"
)


class Transport:
    def __init__(self, case):
        self.case = case
        self.requests = []
        self.generation_count = 0

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.requests.append(deepcopy(payload))
        responses = self.case["raw_visible_responses"]
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            return deepcopy(responses[0])
        self.generation_count += 1
        if self.generation_count == 1:
            return deepcopy(responses[1])
        assert self.generation_count == 2, "No second refinement/model retry is allowed"
        return _provider_body({"content": SYNTHETIC_REFINEMENT})


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    evidence = []

    def build(case_id):
        transport = Transport(CASES[case_id])
        service = _build_service(tmp_path / case_id, transport)
        day = "2026-11-02" if case_id.startswith("d1") else "2026-11-03"
        now = datetime.fromisoformat(day + "T10:00:00")
        service.growth_manager.now_provider = lambda: now
        service.conversation_service.business_now_provider = lambda: now
        for name in ("d1-t01", "d1-t02"):
            result = service.handle(CASES[name]["user"], "offline-loop")
            assert [tool.tool for tool in result.tool_results] == ["save_formal_memory"]
        if case_id.startswith("d2"):
            service.growth_manager.add_record("练习列表推导式，条件过滤仍没弄懂", date="2026-11-02", duration_minutes=12)
            service.growth_manager.save_today_review("2026-11-02")
        calls = []
        for name in service.tool_registry.names():
            definition = service.tool_registry.get(name)
            original = definition.handler
            def observed(*args, _original=original, _name=name, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)
            definition.handler = observed
        evidence.append((service, transport, calls))
        return service, transport, calls, day

    yield build
    (tmp_path / "boundary-replay-evidence.json").write_text(json.dumps([
        {"requests": transport.requests, "handler_calls": calls,
         "tasks": service.growth_manager.tasks(), "state": service.interaction_coordinator.current("offline-loop").to_dict(),
         "history": service.chat_history_manager.messages("offline-loop"),
         "diagnostics": service.conversation_service.diagnostic_snapshot()}
        for service, transport, calls in evidence], ensure_ascii=False, indent=2), encoding="utf-8")


def test_captured_no_arranged_record_is_not_a_positive_mutation():
    clause = RAW_ADVICE.split("，")[0]
    assert clause == "今天还没有已安排的学习记录"
    assert not _claims_unverified_plan_change(clause)
    assert not _claims_unverified_plan_change(RAW_ADVICE)
    result = ResponseComposer().compose(AgentResponse("chat", RAW_ADVICE),
        user_text=CASES["d1-t03"]["user"], verified_turn_context={"execution": {"performed": False}})
    assert result.message == RAW_ADVICE


def test_captured_first_day_keeps_legal_list_and_real_snapshot(replay):
    service, transport, calls, day = replay("d1-t03")
    result = service.handle(CASES["d1-t03"]["user"], "offline-loop")
    assert result.message == RAW_ADVICE
    assert calls == [] and result.tool_results == []
    snapshot = service.interaction_coordinator.current("offline-loop").last_suggestion_snapshot
    assert snapshot["target_date"] == day and len(snapshot["objects"]) == 2
    assert transport.generation_count == 1
    assert service.growth_manager.tasks(day) == []


def test_original_constraint_with_no_write_refines_once_then_adopts_new_durations(replay):
    service, transport, calls, day = replay("d2-t02")
    service.handle(CASES["d2-t02"]["user"], "offline-loop")
    revised = service.handle(CASES["d2-t03"]["user"], "offline-loop")
    assert revised.message == SYNTHETIC_REFINEMENT
    assert revised.tool_results == [] and calls == [] and service.growth_manager.tasks(day) == []
    assert transport.generation_count == 2
    prompt = json.dumps(transport.requests[-1]["messages"], ensure_ascii=False)
    assert "总共只有20分钟" in prompt and "条件过滤还没弄懂" in prompt
    state = service.interaction_coordinator.current("offline-loop")
    assert state.known_fields["suggestion_refinement_used"] is True
    assert state.interaction_kind == "assistant_plan_selection"
    second = service.handle("第二条加入今天计划", "offline-loop")
    first = service.handle("第一条也加入今天计划", "offline-loop")
    assert all(result.success for result in second.tool_results + first.tool_results)
    tasks = service.growth_manager.tasks(day)
    assert [task["duration_minutes"] for task in tasks] == [8, 12]
    assert len({task["uid"] for task in tasks}) == 2 and all(task["date"] == day for task in tasks)
    assert calls == ["add_plan", "add_plan"]
    assert transport.generation_count == 2


def test_long_second_constraint_cannot_bypass_one_refinement_limit(replay):
    service, transport, calls, day = replay("d2-t02")
    service.handle(CASES["d2-t02"]["user"], "offline-loop")
    service.handle(CASES["d2-t03"]["user"], "offline-loop")
    limited = service.handle("今天总共只有15分钟，条件过滤还要用空列表举例，仍给两条建议，先不要加入。", "offline-loop")
    assert "已经调整过一次" in limited.message
    assert calls == [] and limited.tool_results == [] and service.growth_manager.tasks(day) == []
    assert transport.generation_count == 2
    assert not service.interaction_coordinator.current("offline-loop").last_suggestion_snapshot


@pytest.mark.parametrize("cancel", ["先不要加入。", "这两条先不加入。", "不要再给建议，先不要加入。", "取消"])
def test_plain_cancellation_stays_zero_write_and_no_model(replay, cancel):
    service, transport, calls, day = replay("d2-t02")
    service.handle(CASES["d2-t02"]["user"], "offline-loop")
    before = len(transport.requests)
    response = service.handle(cancel, "offline-loop")
    assert response.tool_results == [] and calls == []
    assert service.growth_manager.tasks(day) == [] and len(transport.requests) == before
    assert service.interaction_coordinator.current("offline-loop").state == "cancelled"


def test_compound_dismissal_still_cancels_without_business_writes(replay):
    # Existing ordinary-chat handling can answer a compound dismissal with a
    # model. Its contract here is cancellation/zero write, not zero requests.
    service, transport, calls, day = replay("d2-t02")
    service.handle(CASES["d2-t02"]["user"], "offline-loop")
    response = service.handle("算了，都别加。", "offline-loop")
    assert response.tool_results == [] and calls == [] and service.growth_manager.tasks(day) == []
    assert service.interaction_coordinator.current("offline-loop").state == "cancelled"


@pytest.mark.parametrize("false_claim", [
    "今天已经安排了两项学习计划。", "我已经把练习加入今天计划。",
    "今天还没有已安排的学习记录；我已经把练习加入今天计划。",
    "今天还没有已安排的学习记录，但我已经更新了今天计划。",
    "今天没有已安排的记录但我已经安排好今日计划。",
])
def test_negative_record_clause_does_not_hide_separate_false_success(false_claim):
    result = ResponseComposer().compose(AgentResponse("chat", false_claim),
        user_text="先只给建议，不要加入计划。", verified_turn_context={"execution": {"performed": False}})
    assert result.message != false_claim
