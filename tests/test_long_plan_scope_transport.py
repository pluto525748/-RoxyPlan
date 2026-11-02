"""Captured live decisions replay through the actual routed service, offline."""
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from modules.request_boundary import RequestBoundary
from test_beta_online_failure_replay import _build_service, _provider_body


FIXTURE = Path(__file__).parent / "fixtures/long_plan_scope_online_20261024.json"
CAPTURE = json.loads(FIXTURE.read_text(encoding="utf-8"))
GOALS = "先选目标方向：\n1. 脚本应用\n2. 机器学习基础\n3. 考试或面试"


class Transport:
    def __init__(self, responses):
        self.remaining = deepcopy(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert self.remaining, "unexpected model call"
        return self.remaining.pop(0)


def wire(content):
    return _provider_body({"content": content, "model": "offline-replay-model"})


def chat_decision(mode="discuss"):
    value = json.loads(CAPTURE["groups"][1]["turns"][0]["responses"][0]["choices"][0]["message"]["content"])
    value["request_mode"] = mode
    return value


def evidence(path, service, transport, responses):
    (path / "scope-evidence.json").write_text(json.dumps({
        "requests": transport.calls, "responses": [r.to_dict() for r in responses],
        "plans": service.growth_manager.tasks(),
        "state": service.interaction_coordinator.current("scope").to_dict(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("group", CAPTURE["groups"], ids=lambda g: g["id"])
def test_original_four_turn_live_sequence_cannot_save_whole_long_plan_today(tmp_path, monkeypatch, group):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Transport([response for turn in group["turns"] for response in turn["responses"]])
    service = _build_service(tmp_path, transport)
    responses = [service.handle(turn["user"], "scope") for turn in group["turns"]]
    evidence(tmp_path, service, transport, responses)
    assert not transport.remaining
    assert not service.growth_manager.tasks()
    assert all(not any(t.tool == "add_plan" and t.success for t in r.tool_results) for r in responses)


@pytest.mark.parametrize("text", [
    "我想讨论30天Python学习方案",
    "我想先讨论30天Python学习方案，先问我方向",
    "我想了解30天Python学习方案，列出方向供我选择",
])
def test_typed_discussion_cannot_be_promoted_to_bare_learning_wish(tmp_path, monkeypatch, text):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Transport([wire(json.dumps(chat_decision(), ensure_ascii=False)), wire(GOALS)])
    service = _build_service(tmp_path, transport)
    response = service.handle(text, "scope")
    state = service.interaction_coordinator.current("scope")
    evidence(tmp_path, service, transport, [response])
    assert state.interaction_kind != "advice_or_action_choice"
    assert state.interaction_kind == "discussion_choice"
    assert service.growth_manager.tasks() == []
    assert response.tool_results == []
    assert not transport.remaining


def test_bare_today_learning_wish_keeps_original_choice_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Transport([wire(json.dumps(chat_decision(), ensure_ascii=False))])
    service = _build_service(tmp_path, transport)
    response = service.handle("今天想学习Python", "scope")
    state = service.interaction_coordinator.current("scope")
    assert state.interaction_kind == "advice_or_action_choice"
    assert state.request_mode == "possible_action"
    assert response.tool_results == [] and service.growth_manager.tasks() == []


@pytest.mark.parametrize("text,allowed", [
    ("把30天规划加入今天的计划。", False),
    ("把整个30天学习计划加入今天的计划里", False),
    ("将一个月的学习方案放进今日任务", False),
    ("把“30天规划”加入今天计划", False),
    ("今天加入计划：30天Python学习方案", False),
    ("把制定30天学习方案加入今天计划", True),
    ("今天制定30天学习方案，预计20分钟", True),
    ("今天加入计划：制定30天Python学习方案", True),
    ("把第一天这一项加入今天计划", True),
    ("把30天方案中第一天的复习变量加入今天计划", True),
    ("把阅读《30天规划》第一章加入今天计划", True),
    ("把《明天会更好》加入今天计划", True),
])
def test_scope_distinguishes_whole_plan_from_creating_it_or_one_concrete_step(text, allowed):
    boundary = RequestBoundary("scope", "2026-10-24", text, lambda: datetime(2026, 10, 24, 12))
    assert boundary.plan_add_date_allowed() is allowed


@pytest.mark.parametrize("text,title", [
    ("把制定30天学习方案加入今天计划，预计20分钟", "制定30天学习方案"),
    ("把30天方案中第一天的复习变量加入今天计划，预计20分钟", "复习变量"),
])
def test_real_service_allows_today_preparation_or_explicit_first_day_step(tmp_path, monkeypatch, text, title):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    decision = json.loads(CAPTURE["groups"][0]["turns"][-1]["responses"][0]["choices"][0]["message"]["content"])
    decision.update(confidence=0.99, warnings=[])
    decision["entities"] = {"title": title, "duration_minutes": 20}
    transport = Transport([wire(json.dumps(decision, ensure_ascii=False))])
    service = _build_service(tmp_path, transport)
    response = service.handle(text, "scope")
    assert [(t.tool, t.success) for t in response.tool_results] == [("add_plan", True)]
    assert len(service.growth_manager.tasks()) == 1
    assert service.growth_manager.tasks()[0]["title"] == title


def test_unrelated_chat_and_explicit_long_term_memory_save_remain_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Transport([wire(json.dumps(chat_decision(), ensure_ascii=False)), wire("你好，今天风很轻。")])
    service = _build_service(tmp_path, transport)
    chat = service.handle("聊聊天气", "scope")
    assert chat.message == "你好，今天风很轻。" and chat.tool_results == []
    saved = service.handle("记住：我的长期目标是在30天内学完Python", "scope")
    assert [(t.tool, t.success) for t in saved.tool_results] == [("save_formal_memory", True)]
    assert service.growth_manager.tasks() == []
