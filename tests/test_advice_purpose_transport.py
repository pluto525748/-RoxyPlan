"""Replay synthetic v4 advice-purpose errors through real offline composition.

No pending/snapshot is injected. Every selectable list must come from the
service's own first user turn, displayed reply and normal state transition.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from modules.suggestion_snapshot import SuggestionSnapshot
from test_beta_online_failure_replay import _build_service, _provider_body


CAPTURE = json.loads((Path(__file__).parent / "fixtures/advice_purpose_online_20261025.json").read_text(encoding="utf-8"))
CASES = {item["id"]: item for item in CAPTURE["cases"]}
SCOPE = "advice-purpose"
SYNTHETIC_REPLY = "没有历史记录，先给两项可选小任务：\n1. 阅读隔离示例，20分钟\n2. 整理隔离笔记，15分钟\n只展示候选，等你选择。"


class Transport:
    def __init__(self, case):
        self.semantic = deepcopy(case["responses"][0])
        # Two captures stopped after the mistaken read. Their new advice
        # response is explicitly synthetic; original responses stay immutable.
        self.reply = deepcopy(case["responses"][1]) if len(case["responses"]) > 1 else _provider_body({"content": SYNTHETIC_REPLY})
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert len(self.calls) <= 2, "unexpected additional provider call"
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            return deepcopy(self.semantic)
        return deepcopy(self.reply)


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    runs = []

    def build(case_id):
        transport = Transport(CASES[case_id])
        service = _build_service(tmp_path / str(len(runs)), transport)
        responses = []
        runs.append((service, transport, responses))
        return service, transport, responses

    yield build
    (tmp_path / "advice-purpose-evidence.json").write_text(json.dumps([
        {"requests": transport.calls, "semantic_response": transport.semantic,
         "responses": [response.to_dict() for response in responses],
         "plans": service.growth_manager.tasks(), "reviews": service.growth_manager.entries(),
         "history": service.chat_history_manager.messages(SCOPE),
         "state": service.interaction_coordinator.current(SCOPE).to_dict()}
        for service, transport, responses in runs
    ], ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("case_id", ["g2-d1-t02", "g4-d1-t02", "g2-d5-t02"])
def test_original_advice_request_is_not_replaced_by_business_read(replay, case_id):
    service, transport, responses = replay(case_id)
    if case_id == "g2-d5-t02":
        service.growth_manager.add_task("学习", time_slot="晚上")
        service.growth_manager.add_record("今天的计划外行动：给异常处理整理了一张概念卡，实际用了20分钟", duration_minutes=20)
    before = deepcopy(service.growth_manager.tasks())
    response = service.handle(CASES[case_id]["user"], SCOPE)
    responses.append(response)
    assert response.tool_results == []
    assert service.growth_manager.tasks() == before and service.growth_manager.entries() == []
    state = service.interaction_coordinator.current(SCOPE)
    assert state.last_suggestion_snapshot["list_purpose"] == "today_tasks"
    assert len(state.last_suggestion_snapshot["objects"]) == 2
    assert len(transport.calls) == 2
    advice_prompt = json.dumps(transport.calls[-1]["messages"], ensure_ascii=False)
    assert "本次请求绑定业务日期" in advice_prompt and "last_7_days" in advice_prompt
    assert "last_30_days" in advice_prompt


def test_original_goal_statement_framing_does_not_turn_tasks_into_directions(replay):
    service, transport, responses = replay("g3-d1-t02")
    response = service.handle(CASES["g3-d1-t02"]["user"], SCOPE)
    responses.append(response)
    assert response.tool_results == [] and not service.growth_manager.tasks()
    state = service.interaction_coordinator.current(SCOPE)
    assert state.interaction_kind != "discussion_choice"
    assert state.last_suggestion_snapshot["list_purpose"] == "today_tasks"
    assert len(state.last_suggestion_snapshot["objects"]) == 2
    added = service.handle("第一个加入今天计划", SCOPE)
    responses.append(added)
    assert [(r.tool, r.success) for r in added.tool_results] == [("add_plan", True)]
    assert len(service.growth_manager.tasks()) == 1
    assert "复习一个机器学习概念" in service.growth_manager.tasks()[0]["title"]
    assert service.memory_manager.memories() == [] and service.growth_manager.entries() == []
    assert len(transport.calls) == 2


@pytest.mark.parametrize("case_id,text,tool", [
    ("g3-d1-t02", "先查看今天的计划，再给我两个候选建议。", "show_plan"),
    ("g4-d1-t02", "查看今天的行动记录并给我两个可选行动。", "show_action_log"),
    ("g2-d5-t02", "查看今天的复盘并给我两条候选建议。", "generate_daily_review"),
    ("g2-d5-t02", "先查看昨天的复盘，再给我两条候选建议。", "generate_daily_review"),
    ("g4-d1-t02", "不要给我建议，只查看今天的行动记录。", "show_action_log"),
])
def test_explicit_display_request_is_preserved_in_composite_advice(replay, case_id, text, tool):
    service, transport, responses = replay(case_id)
    raw = json.loads(transport.semantic["choices"][0]["message"]["content"])
    raw["confidence"] = 0.99
    transport.semantic["choices"][0]["message"]["content"] = json.dumps(raw, ensure_ascii=False)
    transport.reply = _provider_body({"content": "今天没有已完成记录。"})
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [(tool, True)]
    assert service.growth_manager.tasks() == service.growth_manager.entries() == []
    assert service.memory_manager.memories() == []


def chat_decision():
    return {"mode": "chat", "intent": "chat", "entities": {}, "proposed_tool": None,
            "confidence": 0.99, "follow_up_target": None, "needs_confirmation": False,
            "warnings": [], "clarification_question": None, "candidate_actions": [],
            "subject": "", "polarity": "", "modality": "", "request_mode": "advice",
            "explicit_command": False}


@pytest.mark.parametrize("framing", [
    "按你的目标，两个候选可以这样安排：",
    "根据已经确认的目标和偏好，候选如下：",
    "你的目标是入门。下面给两个今日候选：",
    "按你的目标：\n两个候选可以这样安排：",
    "按你的学习目标，今天可先选择一个任务：",
])
def test_known_goal_statements_with_task_selection_create_real_task_snapshot(replay, framing):
    service, transport, responses = replay("g3-d1-t02")
    transport.semantic["choices"][0]["message"]["content"] = json.dumps(chat_decision(), ensure_ascii=False)
    transport.reply = _provider_body({"content": framing + "\n1. 阅读隔离示例，20分钟\n2. 整理隔离笔记，15分钟\n你想选一个，还是两个都加？"})
    response = service.handle("请给我两个今天的可执行候选，先只展示。", SCOPE)
    responses.append(response)
    state = service.interaction_coordinator.current(SCOPE)
    assert state.last_suggestion_snapshot["list_purpose"] == "today_tasks"
    assert response.tool_results == [] and not service.growth_manager.tasks()
    added = service.handle("第一个加入今天计划", SCOPE)
    responses.append(added)
    assert [(r.tool, r.success) for r in added.tool_results] == [("add_plan", True)]
    assert len(service.growth_manager.tasks()) == 1


@pytest.mark.parametrize("source,reply,purpose", [
    ("先讨论我的学习方向", "你希望达到什么目标？先选一个方向：\n1. 脚本应用\n2. 机器学习基础\n3. 考试或面试", "clarification_options"),
    ("先讨论我的学习方向", "请先告诉我你的基础：\n1. 没有基础\n2. 会写简单脚本", "clarification_options"),
    ("先讨论我的学习方向", "你的目标是什么？\n1. 脚本应用\n2. 考试或面试", "clarification_options"),
    ("先讨论我的学习方向", "你可以先选一个方向：\n1. 脚本应用\n2. 考试或面试", "clarification_options"),
    ("先讨论我的学习方向", "在这几个方向里先选一个：\n1. 脚本应用\n2. 考试或面试", "clarification_options"),
    ("先讨论我的学习方向", "我先确认一下你的基础：\n1. 没有基础\n2. 会写简单脚本", "clarification_options"),
    ("给我一个30天学习方案", "长期规划分阶段：\n1. 第1周：基础语法\n2. 第2周：数据结构", "long_term_outline"),
    ("请讲解列表推导式", "可以从两个概念理解：\n1. 输入序列：提供元素\n2. 表达式：变换元素", "explanation"),
])
def test_real_direction_outline_and_explanation_lists_never_authorize_writes(replay, source, reply, purpose):
    service, transport, responses = replay("g3-d1-t02")
    transport.semantic["choices"][0]["message"]["content"] = json.dumps(chat_decision(), ensure_ascii=False)
    transport.reply = _provider_body({"content": reply})
    response = service.handle(source, SCOPE)
    responses.append(response)
    state = service.interaction_coordinator.current(SCOPE)
    assert state.interaction_kind == "discussion_choice" and state.known_fields["list_purpose"] == purpose
    assert not state.last_suggestion_snapshot
    rejected = service.handle("第一个加入今天计划", SCOPE)
    responses.append(rejected)
    assert response.tool_results == rejected.tool_results == []
    assert service.growth_manager.tasks() == service.growth_manager.entries() == []
    assert service.memory_manager.memories() == []


def test_bare_today_wish_keeps_existing_choice_policy(replay):
    service, transport, responses = replay("g3-d1-t02")
    transport.semantic["choices"][0]["message"]["content"] = json.dumps(chat_decision(), ensure_ascii=False)
    response = service.handle("今天想学习Python", SCOPE)
    responses.append(response)
    assert service.interaction_coordinator.current(SCOPE).interaction_kind == "advice_or_action_choice"
    assert response.tool_results == [] and not service.growth_manager.tasks()


def test_goal_words_inside_task_items_do_not_change_framing_purpose():
    reply = "今天可以先做：\n1. 整理学习目标清单，20分钟\n2. 选择一个项目练习，15分钟"
    assert SuggestionSnapshot.displayed_list_purpose(reply, "给我今天的两个任务建议", "advice") == "today_tasks"
