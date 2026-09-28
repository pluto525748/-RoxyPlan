from __future__ import annotations

import json

import pytest

from modules.intent_router import LLMIntentParser
from semantic_contract_fixtures import build_current_semantic_payload
from v18_test_support import NoCallLLM, build_service


class DeleteDecisionLLM:
    def __init__(self, *, mode="write", entities=None, follow_up_target=None):
        self.semantic_calls = 0
        self.reply_calls = 0
        self.payload = build_current_semantic_payload({
            "mode": mode,
            "intent": "delete_plan",
            "entities": dict(entities or {}),
            "proposed_tool": "delete_plan",
            "confidence": 0.98,
            "follow_up_target": follow_up_target,
            "needs_confirmation": True,
            "warnings": [],
            "clarification_question": "确认后我会删除这些计划。" if mode == "clarify" else None,
            "candidate_actions": [],
            "subject": "self",
            "polarity": "positive",
            "modality": "commitment",
            "request_mode": "execute",
            "explicit_command": mode == "write",
        })

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            self.semantic_calls += 1
            return json.dumps(self.payload, ensure_ascii=False)
        self.reply_calls += 1
        return "请确认删除全部计划，确认后我会执行。"


def _production_service(tmp_path, llm):
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def _batch_pending(runtime, conversation_id, targets):
    return runtime.interaction_coordinator.awaiting_confirmation(
        conversation_id,
        "plan_batch_completion",
        selected_object_ids=[item["uid"] for item in targets],
        immutable_arguments={"tool_name": "complete_plan"},
        domain="plan",
        request_mode="execute",
    )


def test_no_pending_has_no_authoritative_confirmation_fact(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service

    assert runtime._verified_confirmation_pending("ordinary-chat") == {}


def test_typed_formal_memory_save_projects_three_fields_without_content(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "typed-memory-save"
    content = "仅用于隔离测试的稳定饮食偏好"
    runtime.interaction_coordinator.awaiting_confirmation(
        conversation_id,
        "formal_memory_save",
        immutable_arguments={"tool_name": "save_formal_memory", "content": content},
        domain="memory",
    )

    fact = runtime._verified_confirmation_pending(conversation_id)

    assert fact == {"active": True, "capability": "save_formal_memory", "object_count": 1}
    assert content not in json.dumps(fact, ensure_ascii=False)
    assert memory.memories() == []
    assert service.confirmation_manager.pending(scope=conversation_id) is None


@pytest.mark.parametrize("content", ["", "  "])
def test_empty_typed_memory_save_is_not_authoritative(tmp_path, content):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    runtime.interaction_coordinator.awaiting_confirmation(
        "empty-memory-save", "formal_memory_save",
        immutable_arguments={"tool_name": "save_formal_memory", "content": content},
        domain="memory",
    )
    assert runtime._verified_confirmation_pending("empty-memory-save") == {}


@pytest.mark.parametrize("state_kind", ["awaiting_clarification", "awaiting_confirmation"])
def test_missing_slots_does_not_authorize_a_confirmation(tmp_path, state_kind):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    runtime.interaction_coordinator.start(
        "missing-plan-title", "missing_slots", state_kind,
        immutable_arguments={"tool_name": "add_plan"},
        missing_fields=["title"], domain="plan", request_mode="execute",
    )
    assert runtime._verified_confirmation_pending("missing-plan-title") == {}


def test_real_batch_completion_projects_exact_live_count_and_not_titles(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    targets = [growth.add_task(title) for title in ("核对接口", "校验字段", "阅读说明")]
    _batch_pending(runtime, "batch-live-count", targets)

    fact = runtime._verified_confirmation_pending("batch-live-count")

    assert fact == {"active": True, "capability": "complete_plan", "object_count": 3}
    assert all(item["uid"] not in json.dumps(fact) for item in targets)
    assert all(not item["done"] for item in growth.tasks())
    assert growth.records_for_date() == []


@pytest.mark.parametrize("change", ["completed", "deleted"])
def test_batch_completion_with_invalid_live_target_has_no_fact(tmp_path, change):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    targets = [growth.add_task(title) for title in ("保留第一项", "变化第二项")]
    _batch_pending(runtime, "invalid-batch-target", targets)
    if change == "completed":
        growth.complete_by_id(targets[1]["id"])
    else:
        growth.delete_by_id(targets[1]["id"])
    before = growth.tasks()

    assert runtime._verified_confirmation_pending("invalid-batch-target") == {}
    assert growth.tasks() == before
    assert growth.records_for_date() == []


def test_corrupt_repeated_batch_ids_have_no_fact(tmp_path, monkeypatch):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    target = growth.add_task("重复 ID 不能视为两个对象")
    state = _batch_pending(runtime, "repeated-batch-ids", [target])
    # Public state creation already deduplicates IDs. Simulate corrupt input at
    # the verification boundary to retain its independent rejection contract.
    state.selected_object_ids = [target["uid"], target["uid"]]
    monkeypatch.setattr(runtime.interaction_coordinator, "current", lambda _scope: state)

    assert runtime._verified_confirmation_pending("repeated-batch-ids") == {}
    assert all(not item["done"] for item in growth.tasks())


def test_corrupt_batch_completion_cannot_authorize_a_delete_tool(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    target = growth.add_task("损坏操作类型不能授权删除")
    runtime.interaction_coordinator.awaiting_confirmation(
        "wrong-batch-tool", "plan_batch_completion",
        selected_object_ids=[target["uid"]],
        immutable_arguments={"tool_name": "delete_plan"},
        domain="plan", request_mode="execute",
    )

    assert runtime._verified_confirmation_pending("wrong-batch-tool") == {}
    assert service.confirmation_manager.pending(scope="wrong-batch-tool") is None
    assert [item["uid"] for item in growth.tasks()] == [target["uid"]]


def test_typed_delete_without_matching_confirmation_manager_has_no_fact(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    target = growth.add_task("无真实删除确认")
    runtime.interaction_coordinator.awaiting_confirmation(
        "typed-delete-only", "dangerous_tool",
        selected_object_ids=[target["uid"]],
        immutable_arguments={"tool_name": "delete_plan", "task_ref": target["uid"]},
        domain="plan",
    )

    assert runtime._verified_confirmation_pending("typed-delete-only") == {}
    assert service.confirmation_manager.pending(scope="typed-delete-only") is None
    assert [item["uid"] for item in growth.tasks()] == [target["uid"]]


def test_unique_existing_delete_still_previews_and_executes_once(tmp_path):
    llm = DeleteDecisionLLM(entities={"task_ref": "last_task"}, follow_up_target="last_task")
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    runtime = service.conversation_service
    conversation_id = "single-delete-preserved"
    added = service.handle("把验收说明加入今天计划", conversation_id)
    assert [item.tool for item in added.tool_results] == ["add_plan"]

    preview = service.handle("删除这个计划", conversation_id)
    fact = runtime._verified_confirmation_pending(conversation_id)

    assert preview.status == "clarification"
    assert preview.tool_results == []
    assert "验收说明" in preview.message and "确认" in preview.message
    assert service.confirmation_manager.pending(scope=conversation_id) is not None
    assert fact == {"active": True, "capability": "delete_plan", "object_count": 1}
    assert len(growth.tasks()) == 1
    confirmed = service.handle("确认", conversation_id)
    assert confirmed.status == "completed"
    assert len(confirmed.tool_results) == 1
    assert confirmed.tool_results[0].tool == "delete_plan"
    assert confirmed.tool_results[0].success is True
    assert growth.tasks() == []
    assert runtime._verified_confirmation_pending(conversation_id) == {}
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 0


def test_current_explicit_delete_title_wins_over_model_last_task(tmp_path):
    llm = DeleteDecisionLLM(entities={"task_ref": "last_task"}, follow_up_target="last_task")
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    runtime = service.conversation_service
    conversation_id = "explicit-title-over-stale-model-target"
    first = service.handle("把隔离计划甲加入今天计划", conversation_id)
    second = service.handle("把隔离计划乙加入今天计划", conversation_id)
    assert [item.tool for item in first.tool_results] == ["add_plan"]
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    first_target, retained_target = growth.tasks()

    preview = service.handle("删除隔离计划甲", conversation_id)

    assert preview.status == "clarification"
    assert preview.tool_results == []
    assert "隔离计划甲" in preview.message
    assert "隔离计划乙" not in preview.message
    assert service.confirmation_manager.pending(scope=conversation_id)["arguments"]["task_ref"] == first_target["uid"]
    assert runtime._verified_confirmation_pending(conversation_id) == {
        "active": True, "capability": "delete_plan", "object_count": 1,
    }
    assert len(growth.tasks()) == 2

    confirmed = service.handle("确认", conversation_id)

    assert confirmed.status == "completed"
    assert len(confirmed.tool_results) == 1
    assert confirmed.tool_results[0].tool == "delete_plan"
    assert confirmed.tool_results[0].success is True
    assert growth.tasks() == [retained_target]
    assert runtime._verified_confirmation_pending(conversation_id) == {}
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 0


@pytest.mark.parametrize("user_text", ["删除全部计划", "删除第一项和第二项"])
def test_multi_target_delete_cannot_be_shrunk_to_model_last_task(tmp_path, user_text):
    llm = DeleteDecisionLLM(entities={"task_ref": "last_task"}, follow_up_target="last_task")
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    runtime = service.conversation_service
    conversation_id = "multiple-delete-is-not-single-delete"
    first = service.handle("把核对说明加入今天计划", conversation_id)
    second = service.handle("把校验接口加入今天计划", conversation_id)
    assert [item.tool for item in first.tool_results] == ["add_plan"]
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    before = growth.tasks()

    response = service.handle(user_text, conversation_id)

    assert response.status == "failed"
    assert response.tool_results == []
    assert "没有完成" in response.message
    assert service.confirmation_manager.pending(scope=conversation_id) is None
    assert runtime.interaction_coordinator.current(conversation_id).pending is False
    assert runtime._verified_confirmation_pending(conversation_id) == {}
    assert growth.tasks() == before
    assert growth.records_for_date() == []
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 0


@pytest.mark.parametrize("mode,entities,user_text", [
    ("clarify", {}, "删除全部计划"),
    ("write", {"task_ref": "所有计划"}, "删除全部计划"),
    ("write", {}, "删除这个计划"),
    ("clarify", {"task_ref": "不存在的计划"}, "删除不存在的计划"),
])
def test_unbound_or_batch_model_delete_fails_without_fake_pending(
    tmp_path, mode, entities, user_text,
):
    llm = DeleteDecisionLLM(mode=mode, entities=entities)
    service, growth, memory, _history = _production_service(tmp_path, llm)
    runtime = service.conversation_service
    conversation_id = "unbound-delete"
    targets = [growth.add_task(title) for title in ("保留计划甲", "保留计划乙")]

    response = service.handle(user_text, conversation_id)

    assert response.status == "failed"
    assert response.tool_results == []
    assert "没有完成" in response.message
    assert runtime.interaction_coordinator.current(conversation_id).pending is False
    assert service.confirmation_manager.pending(scope=conversation_id) is None
    assert runtime._verified_confirmation_pending(conversation_id) == {}
    assert growth.tasks() == targets
    assert growth.records_for_date() == []
    assert memory.memories() == []
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 0
