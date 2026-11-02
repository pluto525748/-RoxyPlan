"""Synthetic live failures replayed through the real Qt-free service, offline.

Only the provider transport is injected. Captured model envelopes are kept in
the fixture; local comparisons and follow-ups do not change that original raw.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from modules.intent_router import LLMIntentParser
from test_beta_online_failure_replay import _build_service, _provider_body


CAPTURE = json.loads((Path(__file__).parent / "fixtures/action_envelope_online_20261024.json").read_text(encoding="utf-8"))
CASES = {item["id"]: item for item in CAPTURE["cases"]}
SCOPE = "envelope-replay"


class Transport:
    def __init__(self, case, decision=None):
        self.original = deepcopy(case["responses"][0])
        if decision is not None:
            self.original["choices"][0]["message"]["content"] = json.dumps(decision, ensure_ascii=False)
        self.fallback = deepcopy(case["responses"][1]) if len(case["responses"]) > 1 else _provider_body({"content": "这轮没有执行新的操作。"})
        self.calls = []
        self.classifications = 0

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert len(self.calls) <= 4, "unexpected additional provider call"
        system = payload["messages"][0]["content"]
        if "You classify one Chinese user message" in system:
            self.classifications += 1
            assert self.classifications == 1, "follow-up must use local bound state"
            return deepcopy(self.original)
        return deepcopy(self.fallback)


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    runs = []

    def build(case_id, decision=None):
        case = CASES[case_id]
        transport = Transport(case, decision)
        service = _build_service(tmp_path / str(len(runs)), transport)
        runs.append((service, transport))
        return service, transport

    yield build
    (tmp_path / "envelope-evidence.json").write_text(json.dumps([
        {"requests": transport.calls, "semantic_response": transport.original,
         "plans": service.growth_manager.tasks(),
         "history": service.chat_history_manager.messages(SCOPE),
         "state": service.interaction_coordinator.current(SCOPE).to_dict()}
        for service, transport in runs
    ], ensure_ascii=False, indent=2), encoding="utf-8")


def decision(case_id="bt_conf_02"):
    return json.loads(CASES[case_id]["responses"][0]["choices"][0]["message"]["content"])


def test_original_three_action_raw_writes_all_three_once(replay):
    service, transport = replay("bt_conf_02")
    response = service.handle(CASES["bt_conf_02"]["user"], SCOPE)
    assert [(r.tool, r.success) for r in response.tool_results] == [("add_plan", True)] * 3
    # Existing ChineseEntityParser removes internal spaces downstream; the
    # envelope-only preservation check below separately asserts the raw value.
    assert [p["title"] for p in service.growth_manager.tasks()] == ["英语阅读", "整理简历", "复习Python"]
    assert transport.classifications == 1
    before = deepcopy(service.growth_manager.tasks())
    repeated = service.handle("确认", SCOPE)
    assert repeated.tool_results == []
    assert service.growth_manager.tasks() == before


def canonical(payload):
    value = deepcopy(payload)
    value["candidate_actions"] = [{"intent": "add_plan", "entities": item["args"]}
                                  for item in value["candidate_actions"]]
    return value


@pytest.mark.parametrize("shape", ["canonical", "mixed", "matching_both"])
def test_equivalent_envelopes_preserve_complete_business_result(replay, shape):
    raw = decision()
    expected = canonical(raw)
    if shape == "canonical":
        raw = expected
    elif shape == "mixed":
        raw["candidate_actions"][1] = expected["candidate_actions"][1]
    else:
        for item in raw["candidate_actions"]:
            item.update(intent="add_plan", entities=deepcopy(item["args"]))
    service, transport = replay("bt_conf_02", raw)
    result = service.handle(CASES["bt_conf_02"]["user"], SCOPE)
    assert [(r.tool, r.success) for r in result.tool_results] == [("add_plan", True)] * 3
    assert [p["title"] for p in service.growth_manager.tasks()] == ["英语阅读", "整理简历", "复习Python"]
    assert transport.classifications == 1 and len(transport.calls) == 1
    prompt = transport.calls[0]["messages"][0]["content"]
    assert '"intent": <allowed single intent>' in prompt and '"entities": <object' in prompt


def test_existing_empty_optional_placeholders_are_not_a_new_gate(replay):
    raw = decision()
    for item in raw["candidate_actions"]:
        item["args"].update(note="", time_slot="", duration_minutes=None)
    service, _ = replay("bt_conf_02", raw)
    result = service.handle(CASES["bt_conf_02"]["user"], SCOPE)
    assert [(r.tool, r.success) for r in result.tool_results] == [("add_plan", True)] * 3
    assert len(service.growth_manager.tasks()) == 3


@pytest.mark.parametrize("bad", [
    None,
    {"tool": "execute_shell", "args": {"command": "ignored"}},
    {"tool": "list_memory_candidates", "args": {}},
    {"tool": "add_plan", "args": []},
    {"args": {"title": "第三项"}},
    {"tool": "add_plan"},
    {"intent": "complete_plan", "tool": "add_plan", "args": {"title": "第三项"}},
    {"intent": "add_plan", "entities": {"title": "原目标"}, "tool": "add_plan", "args": {"title": "另一目标"}},
    {"intent": "add_plan", "entities": None},
    {"intent": "chat", "entities": {}},
    {"intent": "add_plan", "entities": {"title": "第三项"}, "subject": "other"},
    {"tool": "add_plan", "args": {"title": False}},
    {"tool": "add_plan", "args": {"title": 0}},
    {"tool": "add_plan", "args": {"title": []}},
    {"tool": "add_plan", "args": {"title": "第三项", "content": False}},
    {"tool": "add_plan", "args": {"title": "第三项", "date": 0}},
], ids=["not_object", "unknown_tool", "retired_tool", "args_array", "orphan_args",
        "missing_args", "intent_conflict", "entities_conflict", "entities_null",
        "chat_child", "child_authorization", "invalid_parameter", "zero_title", "array_title",
        "invalid_alias_collision", "invalid_date_type"])
def test_bad_third_child_never_executes_first_two(replay, bad):
    raw = canonical(decision())
    raw["candidate_actions"][2] = deepcopy(bad)
    service, transport = replay("bt_conf_02", raw)
    result = service.handle(CASES["bt_conf_02"]["user"], SCOPE)
    assert result.tool_results == []
    assert service.growth_manager.tasks() == []
    assert service.interaction_coordinator.current(SCOPE).state not in {"awaiting_confirmation", "awaiting_tool_result"}
    assert transport.classifications == 1


@pytest.mark.parametrize("actions", [None, {}, [], [{"intent": "add_plan", "entities": {"title": "第一项"}}],
                                      [{"intent": "add_plan", "entities": {"title": str(i)}} for i in range(4)]],
                         ids=["null", "object", "empty", "one", "four"])
def test_invalid_batch_size_or_type_is_not_truncated(replay, actions):
    raw = decision()
    raw["candidate_actions"] = actions
    service, _ = replay("bt_conf_02", raw)
    result = service.handle(CASES["bt_conf_02"]["user"], SCOPE)
    assert result.tool_results == [] and service.growth_manager.tasks() == []


@pytest.mark.parametrize("annotations,text", [
    ({"subject": "other", "explicit_command": False}, "我同事今天安排三项：英语阅读、整理简历、复习 Python。"),
    ({"polarity": "negative", "explicit_command": False}, "今天别加入三项计划：英语阅读、整理简历、复习 Python。"),
    ({"modality": "desire", "request_mode": "possible_action", "explicit_command": False}, "也许今天可以英语阅读、整理简历、复习 Python。"),
])
def test_envelope_aliases_do_not_grant_write_authority(replay, annotations, text):
    raw = decision()
    raw.update(annotations)
    service, _ = replay("bt_conf_02", raw)
    result = service.handle(text, SCOPE)
    assert not any(t.success for t in result.tool_results)
    assert service.growth_manager.tasks() == []


def test_parser_preserves_parameter_values_order_and_top_annotations():
    raw = decision()
    raw.update(subject="other", polarity="negative", modality="hypothetical", request_mode="possible_action", explicit_command=False)
    dates = ["今天", "2026-10-24", "明天"]
    for item, date in zip(raw["candidate_actions"], dates):
        item["args"]["date"] = date
    parser = LLMIntentParser(lambda _: json.dumps(raw, ensure_ascii=False))
    result = parser.parse(CASES["bt_conf_02"]["user"])
    assert result is not None
    assert [item["entities"] for item in result["candidate_actions"]] == [item["args"] for item in raw["candidate_actions"]]
    for key in ["intent", "mode", "subject", "polarity", "modality", "request_mode", "explicit_command", "needs_confirmation"]:
        assert result[key] == raw[key]
    assert "candidate_action[2]:tool_args_to_intent_entities" in parser.last_diagnostic


def test_non_today_child_keeps_date_and_entire_batch_unwritten(replay):
    raw = decision()
    raw["candidate_actions"][2]["args"]["date"] = "明天"
    service, _ = replay("bt_conf_02", raw)
    response = service.handle("今天加入英语阅读、整理简历，明天加入复习Python。", SCOPE)
    assert not response.tool_results and not service.growth_manager.tasks()


@pytest.mark.parametrize("seed,duration,duplicate", [
    ("花25分钟看完一小节机器学习内容", 25, True),
    ("花 25 分钟看完一小节机器学习内容", 25, True),
    ("花半小时看完一小节机器学习内容", 30, True),
    ("花一小时半看完一小节机器学习内容", 90, True),
    ("看完一小节机器学习内容", 25, True),
    ("花25分钟看完一小节机器学习内容", 50, False),
    ("画花25分钟", 25, False),
    ("花艺25分钟", 25, False),
], ids=["original", "spaces", "half_hour", "chinese_hour_half", "concise", "different_duration", "embedded_flower", "flower_title"])
def test_duration_wrappers_match_only_same_scheduled_work(replay, seed, duration, duplicate):
    raw = decision("bt_conf_04")
    raw["entities"]["duration_minutes"] = duration
    service, _ = replay("bt_conf_04", raw)
    original = service.growth_manager.add_task(seed)
    result = service.handle(f"把看完一小节机器学习内容加入今天计划，{duration}分钟。", SCOPE)
    tasks = service.growth_manager.tasks()
    assert tasks[0] == original
    if duplicate:
        assert result.tool_results == [] and tasks == [original]
        assert service.interaction_coordinator.current(SCOPE).interaction_kind == "duplicate_plan_resolution"
    else:
        assert [(r.tool, r.success) for r in result.tool_results] == [("add_plan", True)]
        assert len(tasks) == 2


@pytest.mark.parametrize("seed,title", [
    ("画花25分钟", "画"),
    ("花艺25分钟", "艺"),
    ("先验收后发布25分钟", "发布"),
])
def test_meaningful_title_characters_are_not_deleted_to_force_duplicates(replay, seed, title):
    service, _ = replay("bt_conf_04")
    original = service.growth_manager.add_task(seed)
    assert service.plan_service.find_semantic_duplicates(title, duration_minutes=25) == []
    assert service.growth_manager.tasks() == [original]


def test_original_full_title_seed_requires_duplicate_resolution(replay):
    service, transport = replay("bt_conf_04")
    seed = CASES["bt_conf_04"]["seed"][0]
    original = service.growth_manager.add_task(seed["title"])
    response = service.handle(CASES["bt_conf_04"]["user"], SCOPE)
    assert response.tool_results == []
    assert service.growth_manager.tasks() == [original]
    assert service.interaction_coordinator.current(SCOPE).interaction_kind == "duplicate_plan_resolution"
    kept = service.handle("仍然添加", SCOPE)
    assert [(r.tool, r.success) for r in kept.tool_results] == [("add_plan", True)]
    tasks = service.growth_manager.tasks()
    assert tasks[0] == original
    assert tasks[1]["title"] == "看完一小节机器学习内容" and tasks[1]["duration_minutes"] == 25
    repeated = service.handle("确认", SCOPE)
    assert repeated.tool_results == [] and service.growth_manager.tasks() == tasks
    assert transport.classifications == 1
