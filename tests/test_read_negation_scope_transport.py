"""Synthetic v4 read failures, real service/managers, offline transport only.

Captured responses contain semantic JSON only. Any post-fix natural answer is
explicitly synthetic and constructed solely from facts in the actual request.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from test_beta_online_failure_replay import _build_service, _provider_body


CAPTURE = json.loads((Path(__file__).parent / "fixtures/read_negation_online_20261025.json").read_text(encoding="utf-8"))
CASES = {item["id"]: item for item in CAPTURE["cases"]}
SCOPE = "read-negation-scope"
RECORDS = ["我的目标是完成隔离阅读练习", "我偏好先看隔离示例", "我习惯先独立整理隔离笔记"]


class Transport:
    def __init__(self, case):
        self.semantic = deepcopy(case["responses"][0])
        self.calls = []
        self.answers = []
        self.allow_chat = False

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert len(self.calls) <= 2, "unexpected provider retry or additional request"
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            return deepcopy(self.semantic)
        marker = "已核验正式记忆 JSON："
        facts = next((json.loads(m["content"].split(marker, 1)[1])["facts"]
                      for m in payload["messages"] if marker in m.get("content", "")), None)
        if facts is None:
            assert self.allow_chat, "only a verified memory answer is expected"
            return _provider_body({"content": "好的，本轮没有读取或修改记录。"})
        # Synthetic answer, not a captured live response or a fresh model call.
        answer = {"answer": "当前有效记录：\n" + "\n".join(f"记录编号 {f['memory_id']}：{f['content']}" for f in facts),
                  "used_memory_ids": [f["memory_id"] for f in facts]}
        self.answers.append(deepcopy(answer))
        return _provider_body({"content": json.dumps(answer, ensure_ascii=False)})


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    runs = []

    def build(case_id="g1-d7-t14"):
        transport = Transport(CASES[case_id])
        service = _build_service(tmp_path / str(len(runs)), transport)
        responses = []
        runs.append((service, transport, responses))
        # Actual public chat commands, including the real confirmation flow.
        for text in RECORDS:
            saved = service.handle("记住：" + text, SCOPE)
            responses.append(saved)
            assert [(r.tool, r.success) for r in saved.tool_results] == [("save_formal_memory", True)]
        assert len(service.memory_manager.memories()) == 3
        preview = service.handle("忘记：" + RECORDS[1], SCOPE)
        responses.append(preview)
        assert preview.status == "confirmation_required"
        assert len(service.memory_manager.memories()) == 3
        deleted = service.handle("确认", SCOPE)
        responses.append(deleted)
        assert [(r.tool, r.success) for r in deleted.tool_results] == [("delete_memory", True)]
        assert [m["content"] for m in service.memory_manager.memories()] == [RECORDS[0], RECORDS[2]]
        assert transport.calls == []
        return service, transport, responses

    yield build
    (tmp_path / "read-negation-evidence.json").write_text(json.dumps([
        {"requests": transport.calls, "semantic_response": transport.semantic,
         "synthetic_answers": transport.answers,
         "responses": [response.to_dict() for response in responses],
         "memories": service.memory_manager.memories(),
         "plans": service.growth_manager.tasks(), "reviews": service.growth_manager.entries(),
         "history": service.chat_history_manager.messages(SCOPE)}
        for service, transport, responses in runs
    ], ensure_ascii=False, indent=2), encoding="utf-8")


def assert_remaining_facts(service, response):
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
    facts = response.tool_results[0].data["memory_read"]["facts"]
    remaining = service.memory_manager.memories()
    assert {(f["memory_id"], f["content"]) for f in facts} == {(m["id"], m["content"]) for m in remaining}
    assert {m["content"] for m in remaining} == {RECORDS[0], RECORDS[2]}
    assert RECORDS[1] not in {f["content"] for f in facts}
    assert service.growth_manager.tasks() == service.growth_manager.entries() == []


@pytest.mark.parametrize("case_id", CASES)
def test_original_semantic_read_survives_separate_write_or_presentation_ban(replay, case_id):
    service, transport, responses = replay(case_id)
    response = service.handle(CASES[case_id]["user"], SCOPE)
    responses.append(response)
    assert_remaining_facts(service, response)
    assert len(transport.calls) == 2 and len(transport.answers) == 1
    assert RECORDS[0] in response.message and RECORDS[2] in response.message and RECORDS[1] not in response.message
    for memory in service.memory_manager.memories():
        assert f"记录编号 {memory['id']}" in response.message


@pytest.mark.parametrize("case_id", ["g1-d7-t14", "g2-d5-t10"])
def test_model_call_entry_uses_same_real_read_boundary(replay, case_id):
    service, transport, responses = replay(case_id)
    # A synthetic tool proposal for the existing alternate entry, not a
    # captured provider tool_call (v4 captured only semantic JSON).
    response = service.agent_core.execute_model_tool_calls(
        CASES[case_id]["user"], [{"name": "list_memories", "arguments": {"query_mode": "overview"}}],
        confidence=0.99, confirmation_scope=SCOPE,
    )
    responses.append(response)
    assert_remaining_facts(service, response)
    assert transport.calls == []


def observe_reads(service):
    """Count actual handler entry without replacing any policy or result."""
    calls = []
    definition = service.agent_core.executor.registry.get("list_memories")
    original = definition.handler

    def handler(**kwargs):
        calls.append(deepcopy(kwargs))
        return original(**kwargs)

    definition.handler = handler
    return calls


def core_entry(service, entry, text, calls):
    if entry == "model":
        return service.agent_core.execute_model_tool_calls(text, calls, confidence=0.99, confirmation_scope=SCOPE)
    return service.agent_core.process(text, {
        "intent": "show_memory" if len(calls) == 1 else "multi_action",
        "source": "llm", "confidence": 0.99, "semantic_authoritative": True,
        "resolved_actions": [{"tool_name": call["name"], "arguments": call["arguments"]} for call in calls],
    }, confirmation_scope=SCOPE)


@pytest.mark.parametrize("entry", ["process", "model"])
@pytest.mark.parametrize("text", [
    "不要查看现有长期记忆。",
    "别展示现有长期记忆，也不要删除记录。",
    "不用读取长期记忆，不要保存任何内容。",
    "请展示长期记忆，但先不要展示长期记忆。",
    "不要把长期记忆展示出来。",
])
def test_explicit_read_prohibition_never_enters_handler(replay, entry, text):
    service, transport, responses = replay()
    entered = observe_reads(service)
    response = core_entry(service, entry, text, [{"name": "list_memories", "arguments": {"query_mode": "overview"}}])
    responses.append(response)
    assert entered == [] and not any(r.success for r in response.tool_results)
    assert [m["content"] for m in service.memory_manager.memories()] == [RECORDS[0], RECORDS[2]]
    assert response.to_dict()["pending_confirmation"] is None
    assert transport.calls == []


@pytest.mark.parametrize("text", ["不要查看现有长期记忆。", "请展示长期记忆，但不要展示长期记忆。"])
def test_semantic_entry_cannot_override_user_read_prohibition(replay, text):
    service, transport, responses = replay()
    transport.allow_chat = True
    entered = observe_reads(service)
    response = service.handle(text, SCOPE)
    responses.append(response)
    assert entered == [] and not any(r.success for r in response.tool_results)
    assert service.memory_manager.memories()[0]["content"] == RECORDS[0]


@pytest.mark.parametrize("entry", ["process", "model"])
@pytest.mark.parametrize("text", [
    "请展示现有长期记忆，不要删除任何记录。",
    "请核对已保存目标和偏好，别保存这次聊天。",
])
def test_read_does_not_consume_separate_save_or_delete_ban(replay, entry, text):
    service, transport, responses = replay()
    entered = observe_reads(service)
    response = core_entry(service, entry, text, [{"name": "list_memories", "arguments": {"query_mode": "overview"}}])
    responses.append(response)
    assert_remaining_facts(service, response)
    assert len(entered) == 1 and transport.calls == []


@pytest.mark.parametrize("entry", ["process", "model"])
@pytest.mark.parametrize("extra", [
    {"name": "save_formal_memory", "arguments": {"content": "未经授权的新隔离记录"}},
    {"name": "unregistered_read_like_name", "arguments": {}},
])
def test_mixed_write_or_unknown_steps_never_get_read_exception(replay, entry, extra):
    service, transport, responses = replay()
    entered = observe_reads(service)
    response = core_entry(service, entry, "展示现有长期记忆，但不要保存任何记录。", [
        {"name": "list_memories", "arguments": {"query_mode": "overview"}}, deepcopy(extra),
    ])
    responses.append(response)
    assert entered == [] and not any(r.success for r in response.tool_results)
    assert [m["content"] for m in service.memory_manager.memories()] == [RECORDS[0], RECORDS[2]]
    assert response.to_dict()["pending_confirmation"] is None
    assert transport.calls == []


@pytest.mark.parametrize("entry", ["process", "model"])
def test_all_registered_read_steps_share_positive_read_scope(replay, entry):
    service, transport, responses = replay()
    entered = observe_reads(service)
    response = core_entry(service, entry, "请展示现有长期记忆和今天计划，不要保存任何记录。", [
        {"name": "list_memories", "arguments": {"query_mode": "overview"}},
        {"name": "show_plan", "arguments": {}},
    ])
    responses.append(response)
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True), ("show_plan", True)]
    assert len(entered) == 1 and not service.growth_manager.tasks()
    assert [m["content"] for m in service.memory_manager.memories()] == [RECORDS[0], RECORDS[2]]
    assert transport.calls == []


@pytest.mark.parametrize("metadata", [{"operation_kind": "write"}, {"side_effect": True}])
def test_tool_name_alone_cannot_establish_read_permission(replay, metadata):
    service, transport, responses = replay()
    entered = observe_reads(service)
    definition = service.agent_core.executor.registry.get("list_memories")
    for key, value in metadata.items():
        setattr(definition, key, value)
    response = core_entry(service, "model", "展示现有长期记忆，别删除记录。", [
        {"name": "list_memories", "arguments": {"query_mode": "overview"}},
    ])
    responses.append(response)
    assert entered == [] and not any(r.success for r in response.tool_results)
    assert transport.calls == []


@pytest.mark.parametrize("entry", ["process", "model"])
def test_positive_read_clause_never_authorizes_a_write_only_proposal(replay, entry):
    service, transport, responses = replay()
    response = core_entry(service, entry, "请展示现有长期记忆，不要保存任何新记录。", [
        {"name": "save_formal_memory", "arguments": {"content": "不应被保存的隔离记录"}},
    ])
    responses.append(response)
    assert not any(r.success for r in response.tool_results)
    assert [m["content"] for m in service.memory_manager.memories()] == [RECORDS[0], RECORDS[2]]
    assert response.to_dict()["pending_confirmation"] is None and transport.calls == []


@pytest.mark.parametrize("entry", ["process", "model"])
@pytest.mark.parametrize("text", [
    "“展示现有长期记忆”，不要删除记录。",
    "例句是“前置说明。\n展示现有长期记忆”。不要删除记录。",
])
def test_quoted_read_example_cannot_cancel_write_negation(replay, entry, text):
    service, transport, responses = replay()
    entered = observe_reads(service)
    response = core_entry(service, entry, text, [
        {"name": "list_memories", "arguments": {"query_mode": "overview"}},
    ])
    responses.append(response)
    assert entered == [] and not any(r.success for r in response.tool_results)
    assert transport.calls == []
