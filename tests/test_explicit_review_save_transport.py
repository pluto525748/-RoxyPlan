"""Explicit daily review saves use the existing typed handler, offline.

Original synthetic v4 input/raw is immutable in the fixture. Only the provider
transport is replaced; the service, router, managers and persistence are real.
"""
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from modules.intent_router import IntentRouter, is_fixed_command
from test_beta_online_failure_replay import _build_service, _provider_body


CAPTURE = json.loads((Path(__file__).parent / "fixtures/explicit_review_save_online_20261025.json").read_text(encoding="utf-8"))
CASES = {item["id"]: item for item in CAPTURE["cases"]}
SCOPE = "review-save-replay"


class Transport:
    def __init__(self, case, semantic=None):
        self.original = deepcopy(case["responses"][0])
        if semantic is not None:
            self.original["choices"][0]["message"]["content"] = json.dumps(semantic, ensure_ascii=False)
        self.reply = deepcopy(case["responses"][1])
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert len(self.calls) <= 2, "unexpected provider retry or repair"
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            return deepcopy(self.original)
        return deepcopy(self.reply)


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    runs = []

    def build(case_id="g1-d3-t15", semantic=None):
        transport = Transport(CASES[case_id], semantic)
        service = _build_service(tmp_path / str(len(runs)), transport)
        responses = []
        runs.append((service, transport, responses))
        return service, transport, responses

    yield build
    (tmp_path / "review-save-evidence.json").write_text(json.dumps([
        {"requests": transport.calls, "semantic_response": transport.original,
         "responses": [response.to_dict() for response in responses],
         "plans": service.growth_manager.tasks(), "reviews": service.growth_manager.entries(),
         "history": service.chat_history_manager.messages(SCOPE)}
        for service, transport, responses in runs
    ], ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("case_id", CASES)
def test_original_explicit_save_dispatches_without_model(replay, case_id):
    service, transport, responses = replay(case_id)
    if case_id == "g1-d7-t15":
        service.growth_manager.add_task("阅读隔离示例")
        service.growth_manager.add_task("整理隔离笔记")
    plans = deepcopy(service.growth_manager.tasks())
    memories = deepcopy(service.memory_manager.memories())
    response = service.handle(CASES[case_id]["user"], SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    entries = service.growth_manager.entries()
    assert len(entries) == 1
    review = entries[0]["review"]
    assert review["total"] == len(plans) and review["done"] == 0
    assert review["pending"] == len(plans) and review["action_count"] == 0
    assert entries[0]["date"] == service.growth_manager._today()
    assert service.growth_manager.tasks() == plans
    assert service.memory_manager.memories() == memories
    assert response.tool_results[0].data["entry"] == entries[0]
    assert "保存到成长日志" in response.message
    assert transport.calls == []


@pytest.mark.parametrize("text", [
    "今天先到这里，帮我生成并保存复盘。",  # Synthetic equivalent, not the captured day-7 input.
    "今天生成并保存复盘。",
    "请把今日的计划和行动整理成一份成长复盘然后保存。",
    "把今天实际完成与未完成情况生成复盘并且保存。",
    "生成并保存今天的复盘。",
    "重新生成并保存今天的复盘。",
    "保存今日复盘。",
])
def test_explicit_positive_variants_save_actual_plans_and_actions(replay, text):
    service, transport, responses = replay()
    growth = service.growth_manager
    task = growth.add_task("隔离复盘已完成事项", duration_minutes=20)
    growth.add_task("隔离复盘待完成事项", duration_minutes=15)
    growth.complete_by_id(task["id"])
    growth.add_record("隔离计划外阅读", duration_minutes=5)
    before = deepcopy(growth.tasks())
    actions = deepcopy(growth.records_for_date(growth._today()))
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    saved = growth.entries()[0]
    assert saved == response.tool_results[0].data["entry"]
    assert (saved["review"]["total"], saved["review"]["done"], saved["review"]["pending"], saved["review"]["action_count"]) == (2, 1, 1, 1)
    assert saved["review"]["completed_tasks"] == ["隔离复盘已完成事项"]
    assert saved["review"]["pending_tasks"] == ["隔离复盘待完成事项"]
    assert saved["review"]["actions"] == ["隔离计划外阅读"]
    assert growth.tasks() == before and growth.records_for_date(growth._today()) == actions
    assert service.memory_manager.memories() == []
    assert transport.calls == []
    assert is_fixed_command(text)


def chat_decision():
    raw = json.loads(CASES["g1-d3-t15"]["responses"][0]["choices"][0]["message"]["content"])
    raw.update(mode="chat", intent="chat", proposed_tool=None, subject="", modality="", polarity="",
               request_mode="discuss", explicit_command=False)
    return raw


@pytest.mark.parametrize("text", [
    "今天的计划与行动。",
    "请把今天的计划与行动整理成复盘。",
    "如果把今天的计划与行动整理成复盘并保存，会怎样？",
    "今天先到这里，先别生成并保存复盘。",
    "不要把今天的计划与行动整理成复盘并保存。",
    "我刚才说过‘请把今天的计划与行动整理成复盘并保存’，这只是引用。",
    "“请把今天的计划与行动整理成复盘并保存。”",
    "请把今天的计划与行动整理成复盘并保存？",
    "能不能把今天的计划与行动整理成复盘并保存？",
    "可不可以生成并保存今天的复盘？",
    "今天先到这里，帮我生成并保存复盘可以吗？",
    "如何生成并保存今日复盘？",
    "今日复盘？",
    "保存今日复盘可以吗？",
    "重新生成并保存今天的复盘可以吗？",
    "先别重新生成并保存今天的复盘。",
    "如果重新生成并保存今天的复盘会怎样？",
    "我刚才说过‘重新生成并保存今天的复盘’，这只是引用。",
    "“重新生成并保存今天的复盘。”",
    "今天先到这里，复盘。",
    "帮我整理一下复盘。",
])
def test_non_commands_never_enter_fixed_save_or_write(replay, text):
    service, transport, responses = replay(semantic=chat_decision())
    transport.reply = _provider_body({"content": "我们可以先聊聊复盘方法。"})
    original = service.growth_manager.add_task("隔离未完成事项")
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert not any(t.success and t.tool == "save_daily_review" for t in response.tool_results)
    assert service.growth_manager.entries() == []
    assert service.growth_manager.tasks() == [original]
    assert service.memory_manager.memories() == []
    assert not is_fixed_command(text)
    assert service.intent_router.route_exact_command(text) is None


@pytest.mark.parametrize("text", ["查看今日复盘。", "复盘一下。", "看看今天完成了什么。"])
def test_existing_review_reads_remain_read_only(replay, text):
    service, transport, responses = replay(semantic=chat_decision())
    transport.reply = _provider_body({"content": "今天没有已完成记录。"})
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("generate_daily_review", True)]
    assert service.growth_manager.entries() == []
    assert service.memory_manager.memories() == []


def test_existing_today_command_still_saves_and_shows_full_review(replay):
    service, transport, responses = replay()
    transport.reply = _provider_body({"content": "今天没有计划和行动记录，可以从小步开始。"})
    response = service.handle("今日复盘。", SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    saved = service.growth_manager.entries()[0]
    assert response.tool_results[0].data["entry"] == saved
    assert response.message == "今天没有计划和行动记录，可以从小步开始。"
    assert len(transport.calls) == 1


def test_existing_yesterday_regeneration_keeps_date_and_stable_uid(replay):
    service, transport, responses = replay()
    growth = service.growth_manager
    yesterday = (date.fromisoformat(growth._today()) - timedelta(days=1)).isoformat()
    old = growth.save_today_review(yesterday)
    response = service.handle("重新生成昨天的复盘。", SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    saved = growth.entries()[0]
    assert saved["date"] == yesterday and saved["uid"] == old["uid"]
    assert saved["revision"] == old["revision"] + 1
    assert len(growth.entries()) == 1 and service.memory_manager.memories() == []
    assert transport.calls == []


def test_today_regeneration_replaces_saved_review_with_stable_uid(replay):
    service, transport, responses = replay()
    growth = service.growth_manager
    growth.add_task("隔离今日已完成事项")
    old = growth.save_today_review()
    response = service.handle("重新生成并保存今天的复盘。", SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("save_daily_review", True)]
    saved = growth.entries()[0]
    assert saved["date"] == growth._today()
    assert saved["uid"] == old["uid"]
    assert saved["revision"] == old["revision"] + 1
    assert len(growth.entries()) == 1 and service.memory_manager.memories() == []
    assert transport.calls == []


def test_new_today_grammar_does_not_consume_yesterday(replay):
    service, _, responses = replay(semantic=chat_decision())
    text = "请把昨天的计划与行动整理成复盘并保存。"
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert service.intent_router.route_exact_command(text) is None
    assert response.tool_results == [] and service.growth_manager.entries() == []
