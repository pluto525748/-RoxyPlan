"""W01/W03/W04: captured first responses and bounded offline service regressions.

The fixture preserves synthetic real-provider evidence from product 274fae7.
Transport replay is not a new model call. All writes use the isolated runner root.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from modules.contracts import AgentResponse, ToolResult
from modules.conversation_service import ConversationService
from modules.response_composer import ResponseComposer
from modules.suggestion_snapshot import SuggestionSnapshotObject
from test_beta_online_failure_replay import _build_service, _provider_body
from test_semantic_prompt_visibility_and_review_date import use_business_clock
from test_composer_learning_advice_context import NO_EXECUTION


CAPTURED = json.loads((Path(__file__).parent / "fixtures" /
                      "seven_day_priority_20261027.json").read_text(encoding="utf-8"))["cases"]
NOW = datetime(2026, 11, 17, 10)
DAY = "2026-11-17"
YESTERDAY = "2026-11-16"
ADVICE = "1. 练习列表推导式，12分钟。自己写两个例子。\n2. 练习读取文本文件，8分钟。核对输出。"
REQUEST = "给我两项今天的Python学习计划建议，先不要加入。"


class Replay:
    def __init__(self, reply=ADVICE):
        self.reply = reply
        self.requests = []
        self.semantic = {"mode": "chat", "intent": "chat", "entities": {},
                         "confidence": 0.95, "request_mode": "advice",
                         "explicit_command": False, "needs_confirmation": False}

    def __call__(self, method, url, headers, payload, timeout):
        self.requests.append(deepcopy(payload))
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            return _provider_body({"content": json.dumps(self.semantic, ensure_ascii=False)})
        return _provider_body({"content": self.reply})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Replay()
    agent = _build_service(tmp_path, transport)
    clock = use_business_clock(agent, NOW)
    agent.conversation_service.interaction_coordinator.now_provider = lambda: clock["now"]
    calls = []
    for name in agent.tool_registry.names():
        definition = agent.tool_registry.get(name)
        original = definition.handler

        def observe(*args, _name=definition.name, _original=original, **kwargs):
            calls.append({"tool": _name, "arguments": deepcopy(kwargs)})
            return _original(*args, **kwargs)

        definition.handler = observe
    yield agent, transport, clock, calls
    (tmp_path / "seven-day-regression-evidence.json").write_text(json.dumps({
        "requests": transport.requests, "handler_calls": calls,
        "plans": agent.growth_manager.tasks(DAY),
        "reviews": agent.growth_manager.entries(),
        "diagnostics": agent.conversation_service.diagnostic_snapshot(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def prime(runtime, reply=ADVICE, request=REQUEST):
    agent, transport, _, calls = runtime
    transport.reply = reply
    response = agent.handle(request, "w01")
    assert response.tool_results == []
    state = agent.conversation_service.interaction_coordinator.current("w01")
    assert len(state.last_suggestion_snapshot["objects"]) == 2
    assert calls == [] and agent.growth_manager.tasks(DAY) == []
    return state.last_suggestion_snapshot


@pytest.mark.parametrize("choice", [
    "先把编号1那项加到今天计划。", "请把编号一那项加入今天计划。",
    "编号1加入今天计划。", "把编号一加入今天计划。",
])
def test_w01_numbered_selection_and_followup_keep_real_objects(runtime, choice):
    agent, transport, _, calls = runtime
    source = prime(runtime)
    first = agent.handle(choice, "w01")
    transport.semantic = {"mode": "write", "intent": "add_plan", "entities": {"title": "编号二那项"},
                          "confidence": 0.95, "request_mode": "execute", "explicit_command": True}
    second = agent.handle("编号二那项也加入今天计划。", "w01")
    assert [c["tool"] for c in calls] == ["add_plan", "add_plan"]
    tasks = agent.growth_manager.tasks(DAY)
    assert [(t["title"], t["duration_minutes"], t["date"]) for t in tasks] == [
        ("练习列表推导式", 12, DAY), ("练习读取文本文件", 8, DAY)]
    assert len({t["uid"] for t in tasks}) == 2
    for response, task in zip([first, second], tasks):
        assert len(response.tool_results) == 1
        assert response.tool_results[0].success
        assert response.tool_results[0].data["task"] == task
        assert task["title"] in response.message
    state = agent.conversation_service.interaction_coordinator.current("w01")
    assert state.last_suggestion_snapshot["snapshot_id"] == source["snapshot_id"]
    assert all(o["consumed"] for o in state.last_suggestion_snapshot["objects"])


def test_w01_captured_list_and_exact_original_request(runtime):
    agent, transport, _, calls = runtime
    case = CAPTURED["W01"]
    prime(runtime, case["advice"], case["advice_request"])
    response = agent.handle(case["user"], "w01")
    assert [c["tool"] for c in calls] == ["add_plan"]
    task = response.tool_results[0].data["task"]
    assert task == agent.growth_manager.tasks(DAY)[0]
    assert task["title"] == "用列表推导式写 3 个带 if 过滤条件的例子"
    assert task["duration_minutes"] == 15


def test_w01_new_online_followup_preserves_action_heading_and_explicit_note(runtime):
    agent, transport, _, calls = runtime
    case = CAPTURED["W01_followup"]
    source = prime(runtime, case["advice"], case["advice_request"])
    agent.handle("编号1加入今天计划。", "w01")
    transport.semantic = json.loads(case["raw_contents"][0])
    response = agent.handle(case["user"], "w01")
    assert [c["tool"] for c in calls] == ["add_plan", "add_plan"]
    task = response.tool_results[0].data["task"]
    assert task["title"] == "写一小段代码，把两个变量做简单运算并打印结果"
    assert task["note"] == "只求跑通，不追求写得多。"
    assert task["duration_minutes"] == 8
    assert task == agent.growth_manager.tasks(DAY)[1]
    assert agent.conversation_service.interaction_coordinator.current("w01").last_suggestion_snapshot["snapshot_id"] == source["snapshot_id"]


@pytest.mark.parametrize("body,title,note", [
    ("用 8 分钟写一个例子。备注：核对输出。", "写一个例子", "核对输出。"),
    ("用八分钟整理概念卡。备注：只写一句。", "整理概念卡", "只写一句。"),
    ("用Python练习，8分钟：核对输出。", "用Python练习", "核对输出。"),
])
def test_explicit_note_label_and_duration_introducer_are_not_action_payload(body, title, note):
    arguments = SuggestionSnapshotObject("2", 2, body).plan_arguments()
    assert arguments == {"title": title, "duration_minutes": 8, "note": note}


@pytest.mark.parametrize("choice", ["编号0那项加入今天计划。", "编号3那项加入今天计划。",
    "不要把编号1加入今天计划。", "编号1那项能加入今天计划吗？"])
def test_w01_invalid_or_non_authorizing_selection_never_writes(runtime, choice):
    agent, _, _, calls = runtime
    prime(runtime)
    response = agent.handle(choice, "w01")
    assert calls == [] and response.tool_results == []
    assert agent.growth_manager.tasks(DAY) == []


@pytest.mark.parametrize("condition", ["missing", "expired", "other_session", "next_day"])
def test_w01_reference_requires_live_same_session_same_day(runtime, condition):
    agent, transport, clock, calls = runtime
    if condition != "missing":
        prime(runtime)
    if condition == "expired":
        clock["now"] += timedelta(hours=1)
    if condition == "next_day":
        clock["now"] += timedelta(days=1)
    transport.semantic = {"mode": "write", "intent": "add_plan", "entities": {"title": "编号1那项"},
                          "confidence": 0.99, "request_mode": "execute", "explicit_command": True}
    response = agent.handle("编号1那项加入今天计划。", "other" if condition == "other_session" else "w01")
    assert calls == [] and response.tool_results == []
    assert agent.growth_manager.tasks(DAY) == []


@pytest.mark.parametrize("text", ["再加一项今天计划", "学习1个小时", "复习编号1的题目",
    "阅读第1章关于编号2的说明", "把编号12的练习题加入今天计划"])
def test_w01_payload_numbers_are_not_selection(text):
    assert ConversationService._suggestion_reference_position(text) == 0


def test_w01_explicit_new_title_with_number_is_not_bound_to_old_advice(runtime):
    agent, transport, _, calls = runtime
    prime(runtime)
    transport.semantic = {"mode": "write", "intent": "add_plan", "entities": {"title": "复习编号1的题目"},
                          "confidence": 0.95, "request_mode": "execute", "explicit_command": True}
    response = agent.handle("把复习编号1的题目加入今天计划。", "w01")
    assert [c["tool"] for c in calls] == ["add_plan"]
    assert response.tool_results[0].data["task"]["title"] == "复习编号1的题目"


@pytest.mark.parametrize("text,date", [
    (CAPTURED["W03"]["user"], YESTERDAY),
    ("请给我看一下昨日已保存的复盘，不要重新保存。", YESTERDAY),
    ("看看昨天的复盘，完成任务不代表已经学会。", YESTERDAY),
    ("把2026-11-16保存的复盘给我看看。", YESTERDAY),
    ("查看前天的复盘。", "2026-11-15"),
])
def test_w03_dated_review_query_reads_exact_date_and_never_saves(runtime, text, date):
    agent, transport, _, calls = runtime
    g = agent.growth_manager
    task = g.add_task("过拟合例子", date=date, duration_minutes=12)
    g.complete_by_id(task["id"], date)
    g.add_record("整理概念卡", date=date, duration_minutes=8)
    saved = g.save_today_review(date)
    transport.semantic = json.loads(CAPTURED["W03"]["raw_contents"][0])
    transport.reply = "这是指定日期的记录：完成1项任务，已掌握该项内容，另记8分钟行动。"
    root = agent.project_root
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.json") if p.name in {"memory.json", "today_plan.json", "action_log.json", "growth_log.json", "pet_config.json"}}
    response = agent.handle(text, "w03")
    assert calls == [{"tool": "generate_daily_review", "arguments": {"date": date}}]
    result = response.tool_results[0]
    assert result.success and result.data["review"]["date"] == date
    assert result.data["review"]["done"] == 1
    assert result.data["review"]["action_records"][0]["duration_minutes"] == 8
    assert next(e for e in g.entries() if e["date"] == date) == saved
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.json") if p.name in {"memory.json", "today_plan.json", "action_log.json", "growth_log.json", "pet_config.json"}} == before
    assert date in response.message or (date == YESTERDAY and "昨天" in response.message)
    assert not any("You classify one Chinese user message" in r["messages"][0]["content"] for r in transport.requests)


@pytest.mark.parametrize("text", [
    "不要查看昨天保存的复盘。", "把昨天的复盘加入今天计划。",
    "重新生成并保存昨天的复盘。", "我在写一篇昨天复盘的文章。",
])
def test_w03_read_guard_does_not_hijack_negation_payload_or_save(runtime, text):
    agent, _, _, _ = runtime
    assert agent.intent_router._route_daily_review_query(text) is False


def test_w03_missing_day_read_does_not_create_empty_saved_review(runtime):
    agent, transport, _, calls = runtime
    transport.reply = "昨天没有已记录的计划或行动；这不证明你没有学习。"
    response = agent.handle("查看昨天的复盘。", "w03-missing")
    assert calls == [{"tool": "generate_daily_review", "arguments": {"date": YESTERDAY}}]
    assert response.tool_results[0].success
    assert agent.growth_manager.entries() == []
    assert agent.growth_manager.tasks(DAY) == []


def test_w04_captured_conditional_advice_keeps_snapshot_and_adoption(runtime):
    agent, transport, _, calls = runtime
    case = CAPTURED["W04"]
    source = prime(runtime, case["raw_contents"][-1], case["user"])
    assert source["source_turn_id"]
    assert agent.chat_history_manager.get_session("w01")["messages"][-1]["content"] == case["raw_contents"][-1]
    first = agent.handle("第一项加入今天计划。", "w01")
    transport.semantic = {"mode": "write", "intent": "add_plan", "entities": {"title": "第二项"},
                          "confidence": 0.95, "request_mode": "execute", "explicit_command": True}
    second = agent.handle("第二项也加入今天计划。", "w01")
    assert [c["tool"] for c in calls] == ["add_plan", "add_plan"]
    assert [t["duration_minutes"] for t in agent.growth_manager.tasks(DAY)] == [12, 8]
    assert first.tool_results[0].success and second.tool_results[0].success


@pytest.mark.parametrize("message", [
    "选好告诉我，我再帮你加入计划。", "如果你想采用，告诉我具体哪项，我再帮你加入今天计划。",
    "等你选好哪一项，再告诉我，我再帮你加入今天计划。",
    "我不会直接加入计划，选好告诉我，我再帮你加入计划。",
])
def test_w04_future_selection_offer_is_not_completed_or_committed_write(message):
    response = ResponseComposer().compose(AgentResponse("chat", message), user_text=REQUEST,
                                          verified_turn_context=NO_EXECUTION)
    assert response.message == message and response.tool_results == []


@pytest.mark.parametrize("message", [
    "选好告诉我，我已经帮你加入今天计划了。",
    "选好告诉我，我再帮你加入计划。我已经保存长期记忆了。",
    "我现在就帮你加入今天计划。",
    "你确认后我就删除长期记忆。",
    "我会先解释，不过我会加入今天计划。",
    "选好告诉我，我再帮你保存长期记忆。",
    "选好告诉我，我再帮你删除今天计划。",
])
def test_w04_offer_cannot_hide_false_success_or_unbound_transaction(message):
    response = ResponseComposer().compose(AgentResponse("chat", message), user_text=REQUEST,
                                          verified_turn_context=NO_EXECUTION)
    assert response.message != message and response.tool_results == []


def test_w04_unrelated_read_tool_cannot_authorize_plan_or_memory_success():
    claim = "我已经帮你加入今天计划并保存长期记忆了。"
    read = ToolResult(True, "show_plan", "listed", data={"tasks": [], "date": DAY})
    response = ResponseComposer().compose(AgentResponse("completed", claim, tool_results=[read]),
                                          user_text="查看今天计划", verified_turn_context=NO_EXECUTION)
    assert response.message != claim


def test_online_preparation_uses_real_handlers_without_provider_calls(runtime):
    from scripts.run_seven_day_priority_acceptance import prepare_runtime, PRIOR
    agent, transport, clock, calls = runtime
    clock["now"] = datetime.fromisoformat(PRIOR + "T10:00:00")
    receipt = prepare_runtime(agent, agent.project_root)
    assert transport.requests == []
    assert len(receipt["receipts"]) == 6
    assert all(r["result"]["success"] for r in receipt["receipts"])
    assert [t["done"] for t in agent.growth_manager.tasks(PRIOR)] == [True, False]
    assert agent.growth_manager.records_for_date(PRIOR)[0]["duration_minutes"] == 12
    assert agent.growth_manager.entries()[0]["review"]["done"] == 1


def test_online_plan_has_independent_preplanned_repeats_and_one_batch_budget():
    from scripts.run_seven_day_priority_acceptance import build_plan, LIMITS
    plan = build_plan()
    assert LIMITS["http"] == 90 and LIMITS["reported_tokens_stop"] == 450000
    assert len(plan["groups"]) == 27
    for family in ["W01", "W03", "W04"]:
        groups = [g for g in plan["groups"] if g["family"] == family]
        assert {(g["variant"], g["repetition"]) for g in groups} == {
            (v, r) for v in range(1, 4) for r in range(1, 4)}
    assert len({g["id"] for g in plan["groups"]}) == 27
