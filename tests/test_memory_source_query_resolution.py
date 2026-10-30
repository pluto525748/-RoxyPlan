"""Captured low-confidence source-read decision, with real isolated services.

The second semantic JSON is the engineering gate-source t02 capture. Save
setup is a controlled fixture. Neither fixture invokes an online provider.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from modules.intent_router import LLMIntentParser
from modules.memory_read import is_memory_save_reference_question
from modules.semantic_action_parser import SemanticActionParser
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.safety_policy import SafetyPolicy
from modules.memory_service import MemoryOperationResult
from v18_test_support import build_service


CONTENT = "我习惯在周日晚上整理这一周的学习笔记"
QUESTION = "刚才那条已经保存了吗？"
CAPTURED_READ = {
    "mode": "read", "intent": "show_memory",
    "entities": {"query_mode": "existence", "query": "刚才那条已经保存了吗"},
    "proposed_tool": "list_memories", "confidence": .62,
    "follow_up_target": "previous_user_message", "needs_confirmation": False,
    "warnings": [], "clarification_question": "", "candidate_actions": [],
    "subject": "self", "polarity": "", "modality": "question",
    "request_mode": "query", "explicit_command": False,
}


class SourceReadLLM:
    def __init__(self):
        self.calls = []
        self.semantic_override = None

    def chat(self, messages):
        self.calls.append(messages)
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            user = prompt.rsplit("User message: ", 1)[-1]
            if self.semantic_override is not None:
                return json.dumps(self.semantic_override, ensure_ascii=False)
            if user.startswith("我想让你记住"):
                return json.dumps({
                    "mode": "write", "intent": "add_memory_request",
                    "entities": {"content": CONTENT}, "proposed_tool": "save_formal_memory",
                    "confidence": .95, "follow_up_target": None, "needs_confirmation": False,
                    "warnings": [], "clarification_question": None, "candidate_actions": [],
                    "subject": "self", "polarity": "positive", "modality": "commitment",
                    "request_mode": "execute", "explicit_command": True,
                }, ensure_ascii=False)
            return json.dumps(CAPTURED_READ, ensure_ascii=False)
        return "刚才没有真正保存。"


def make_service(tmp_path):
    llm = SourceReadLLM()
    service, _growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, memory, history, llm


def business_records(memory):
    return [{key: value for key, value in record.items()
             if key not in {"use_count", "last_used", "last_used_at"}}
            for record in memory.memories()]


def test_captured_source_read_does_not_fall_into_generic_low_confidence_clarification(tmp_path, monkeypatch):
    service, memory, history, llm = make_service(tmp_path)
    core = service.conversation_service
    contexts = []
    compose = core.response_composer.compose

    def observed_compose(response, **kwargs):
        contexts.append({
            "request_id": response.request_id,
            "verified": kwargs.get("verified_turn_context", {}),
        })
        return compose(response, **kwargs)

    monkeypatch.setattr(core.response_composer, "compose", observed_compose)
    saved = service.handle("我想让你记住" + CONTENT, "source-query")
    before = business_records(memory)
    contexts.clear()
    response = service.handle(QUESTION, "source-query")
    evidence = {
        "fixture_source": "gate-source-9e88938-first/gate-source-t02; controlled save setup",
        "captured_semantic": CAPTURED_READ, "offline_prompts": llm.calls,
        "save_results": [r.to_dict() for r in saved.tool_results],
        "source_results": [r.to_dict() for r in response.tool_results],
        "composition_contexts": contexts,
        "status": response.status, "final": response.message,
        "history": history.get_session("source-query"),
        "business_before": before, "business_after": business_records(memory),
        "last_diagnostic": core.interaction_diagnostics.snapshot()[-1],
    }
    (tmp_path / "source-query-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    assert [(r.tool, r.success) for r in saved.tool_results] == [("save_formal_memory", True)]
    assert response.status != "clarification"
    assert "已经保存" in response.message and CONTENT in response.message
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
    snapshot = response.tool_results[0].data["memory_read"]
    assert [f["memory_id"] for f in snapshot["facts"]] == [before[0]["id"]]
    assert response.tool_results[0].data["memories"][0]["uid"] == before[0]["uid"]
    assert snapshot["facts"][0]["content"] == before[0]["content"]
    assert snapshot["request"]["query"] == CONTENT
    assert "没有找到相关" not in response.message
    assert business_records(memory) == before
    assert history.get_session("source-query")["messages"][-1]["content"] == response.message
    assert any(item["verified"].get("prior_memory_operation", {}).get("kind") == "memory_saved"
               for item in contexts)


@pytest.mark.parametrize("confidence,allowed", [(.59, False), (.60, True), (.62, True)])
def test_default_admission_boundary_for_legal_read_only(tmp_path, confidence, allowed):
    service, memory, _history, _llm = make_service(tmp_path)
    record = memory.add_memory(CONTENT, category="habit")["memory"]
    before = business_records(memory)
    response = service.agent_core.process("核对一条正式记录", {
        "intent": "show_memory", "source": "llm", "confidence": confidence,
        "entities": {"query_mode": "existence", "attribute": "fact", "query": CONTENT},
    }, confirmation_scope="threshold")
    if allowed:
        assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
        assert [f["memory_id"] for f in response.tool_results[0].data["memory_read"]["facts"]] == [record["id"]]
    else:
        assert response.status == "clarification" and not response.tool_results
    assert business_records(memory) == before


def test_lower_admission_does_not_relax_confirmation_arguments_or_explicit_safety_policy(tmp_path):
    service, memory, _history, _llm = make_service(tmp_path)
    record = memory.add_memory(CONTENT, category="habit")["memory"]
    before = business_records(memory)
    core = service.agent_core
    response = core.process("删除记忆1", {
        "intent": "delete_memory", "source": "llm", "confidence": .60,
        "entities": {"memory_id": record["id"]},
    }, confirmation_scope="safety")
    assert response.status == "confirmation_required"
    assert business_records(memory) == before
    invalid = core.executor.execute("list_memories", {"query_mode": "unsupported"}, confidence=.62)
    assert not invalid.success
    assert core.executor.safety_policy.medium_confidence_threshold == .82
    core.executor.safety_policy = SafetyPolicy(medium_confidence_threshold=.93)
    denied = core.executor.execute("save_formal_memory", {"content": "我喜欢合成练习"}, confidence=.62)
    assert not denied.success
    assert core.executor.safety_policy.medium_confidence_threshold == .93
    assert business_records(memory) == before


@pytest.mark.parametrize("tool", ["list_memories", "search_memories"])
def test_plural_memory_tool_domain_matches_canonical_catalog(tool):
    assert DEFAULT_CAPABILITY_REGISTRY.for_tool(tool).domain == "memory"
    assert SemanticActionParser._domain(tool) == "memory"


@pytest.mark.parametrize("mutation", ["delete", "archive", "update", "replace_uid", "expire_record", "expire_reference", "other_session"])
def test_invalid_saved_reference_never_turns_into_empty_library_claim(tmp_path, mutation):
    service, memory, history, _llm = make_service(tmp_path)
    core = service.conversation_service
    clock = [datetime(2026, 10, 26, 12)]
    core.now_provider = lambda: clock[0]
    core.state_manager.now_provider = lambda: clock[0]
    saved = service.handle("我想让你记住" + CONTENT, "identity")
    memory_id = saved.tool_results[0].data["memory_operation"]["memory_id"]
    if mutation == "delete":
        assert service.memory_service.delete_memory(memory_id).success
    elif mutation == "archive":
        assert service.memory_service.archive_memory(memory_id).success
    elif mutation == "update":
        assert service.memory_service.update_memory(memory_id, content="我现在周六整理").success
    elif mutation == "replace_uid":
        data = json.loads(memory.memory_file.read_text(encoding="utf-8"))
        data["memories"][0]["uid"] = "memory_replacement_engineering_fixture"
        memory.memory_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif mutation == "expire_record":
        assert memory.update_memory(memory_id, valid_until="2000-01-01T00:00:00+00:00")
    elif mutation == "expire_reference":
        clock[0] += timedelta(seconds=core.state_manager.memory_reference_ttl_seconds + 1)
    scope = "another" if mutation == "other_session" else "identity"
    before = business_records(memory)
    response = service.handle(QUESTION, scope)
    assert not response.tool_results
    assert "无法" in response.message and "唯一" in response.message
    assert "刚才已经保存" not in response.message
    assert business_records(memory) == before
    assert history.get_session(scope)["messages"][-1]["content"] == response.message


def decision(intent, tool, entities, *, mode="write"):
    return {**deepcopy(CAPTURED_READ), "mode": mode, "intent": intent, "proposed_tool": tool,
            "entities": entities, "confidence": .95, "follow_up_target": None,
            "subject": "self", "polarity": "positive", "modality": "commitment",
            "request_mode": "execute", "explicit_command": True}


@pytest.mark.parametrize("intent,tool,text,entities", [
    ("add_plan", "add_plan", "请把合成任务加入今天计划", {"title": "合成任务"}),
    ("add_action_log", "add_action_log", "记录今天的计划外行动：合成练习", {"content": "合成练习"}),
    ("save_review", "save_daily_review", "生成并保存今天的复盘。", {}),
])
def test_latest_business_write_prevents_older_memory_reference(tmp_path, intent, tool, text, entities):
    service, memory, _history, llm = make_service(tmp_path)
    service.handle("我想让你记住" + CONTENT, "latest")
    llm.semantic_override = decision(intent, tool, entities)
    intervening = service.handle(text, "latest")
    assert any(result.tool == tool and result.success for result in intervening.tool_results)
    llm.semantic_override = None
    before = business_records(memory)
    response = service.handle(QUESTION, "latest")
    assert not response.tool_results
    assert "无法" in response.message and "唯一" in response.message
    assert "刚才已经保存" not in response.message
    assert business_records(memory) == before


@pytest.mark.parametrize("text", ["换个话题，今天的云很好看", "我现在不再周日整理了，之前的习惯变了"])
def test_intervening_topic_or_correction_does_not_rebind_old_save(tmp_path, text):
    service, memory, _history, llm = make_service(tmp_path)
    service.handle("我想让你记住" + CONTENT, "topic")
    llm.semantic_override = {**deepcopy(CAPTURED_READ), "mode": "chat", "intent": "chat",
                             "entities": {}, "proposed_tool": None, "confidence": .95,
                             "request_mode": "discuss", "follow_up_target": None}
    service.handle(text, "topic")
    llm.semantic_override = None
    before = business_records(memory)
    response = service.handle(QUESTION, "topic")
    assert not response.tool_results
    assert "无法" in response.message and "唯一" in response.message
    assert "刚才已经保存" not in response.message
    assert business_records(memory) == before


@pytest.mark.parametrize("text", [
    "请保存那条记忆", "刚才那条保存了吗，然后保存新内容", "他刚才那条已经保存了吗？",
    "“刚才那条已经保存了吗？”", "别保存刚才那条", "我现在的情况变了，刚才那条已经保存了吗？",
])
def test_reference_purpose_is_not_a_new_write_quote_or_third_party(text):
    assert not is_memory_save_reference_question(text)


def test_single_memory_display_can_bind_same_record_but_not_multiple_display(tmp_path):
    service, memory, _history, _llm = make_service(tmp_path)
    service.handle("我想让你记住" + CONTENT, "display")
    service.handle("展示长期记忆", "display")
    first = service.handle(QUESTION, "display")
    assert [(r.tool, r.success) for r in first.tool_results] == [("list_memories", True)]
    memory.add_memory("我喜欢合成拼图", category="preference")
    service.handle("展示长期记忆", "display")
    second = service.handle(QUESTION, "display")
    assert not second.tool_results
    assert "刚才已经保存" not in second.message


def test_complete_body_read_does_not_include_similar_other_record(tmp_path):
    service, memory, _history, _llm = make_service(tmp_path)
    saved = service.handle("我想让你记住" + CONTENT, "exact")
    expected_id = saved.tool_results[0].data["memory_operation"]["memory_id"]
    memory.add_memory("我习惯在周一晚上整理这一周的学习笔记", category="habit")
    response = service.handle(QUESTION, "exact")
    assert [f["memory_id"] for f in response.tool_results[0].data["memory_read"]["facts"]] == [expected_id]


def test_hidden_capability_stays_hidden_at_new_admission_threshold(tmp_path):
    service, memory, _history, llm = make_service(tmp_path)
    llm.semantic_override = {**decision("show_memory_audit", "show_memory_audit", {}, mode="read"),
                             "confidence": .62, "request_mode": "query"}
    before = business_records(memory)
    response = service.handle("请查看我的记忆审计记录。", "hidden")
    assert not response.tool_results
    assert business_records(memory) == before


@pytest.mark.parametrize("outcome", ["failed", "confirmation_required", "low_confidence"])
def test_same_text_unsuccessful_repeat_cannot_borrow_previous_success(tmp_path, monkeypatch, outcome):
    service, memory, _history, llm = make_service(tmp_path)
    text = "我想让你记住" + CONTENT
    first = service.handle(text, "repeat")
    assert first.tool_results[0].success
    if outcome == "low_confidence":
        llm.semantic_override = {**decision("add_memory_request", "save_formal_memory", {"content": CONTENT}),
                                 "confidence": .59}
    else:
        monkeypatch.setattr(service.memory_service, "save_formal_memory", lambda *args, **kwargs:
                            MemoryOperationResult(False, outcome, "save_formal_memory", safe_message="合成未完成操作"))
    second = service.handle(text, "repeat")
    assert not any(r.success and r.tool == "save_formal_memory" for r in second.tool_results)
    llm.semantic_override = None
    before = business_records(memory)
    third = service.handle(QUESTION, "repeat")
    assert not third.tool_results
    assert "刚才已经保存" not in third.message
    assert "唯一" in third.message
    assert business_records(memory) == before


def test_duplicate_record_bodies_are_ambiguous_instead_of_a_profile_dump(tmp_path):
    service, memory, _history, _llm = make_service(tmp_path)
    service.handle("我想让你记住" + CONTENT, "duplicates")
    payload = json.loads(memory.memory_file.read_text(encoding="utf-8"))
    duplicate = {**payload["memories"][0], "id": 2, "uid": "memory_duplicate_fixture"}
    payload["memories"].append(duplicate)
    memory.memory_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = business_records(memory)
    response = service.handle(QUESTION, "duplicates")
    assert not response.tool_results
    assert "唯一" in response.message and "刚才已经保存" not in response.message
    assert business_records(memory) == before


def test_source_binding_does_not_exceed_existing_query_parameter_limit(tmp_path):
    service, memory, _history, llm = make_service(tmp_path)
    content = "我习惯" + "整理合成笔记" * 90
    llm.semantic_override = decision("add_memory_request", "save_formal_memory", {"content": content})
    saved = service.handle("我想让你记住" + content, "long-record")
    assert any(r.success and r.tool == "save_formal_memory" for r in saved.tool_results)
    llm.semantic_override = None
    before = business_records(memory)
    response = service.handle(QUESTION, "long-record")
    assert not response.tool_results
    assert "唯一" in response.message
    assert business_records(memory) == before
