"""Offline replay of the engineering gate's save/read/challenge failure.

The final denial is a captured engineering reply; intent annotations below
are controlled offline fixtures, not a verbatim replay of every API response.
Real shared services, handlers, snapshots and isolated history still execute.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from modules.intent_router import LLMIntentParser
from modules.memory_data_query_guard import MemoryDataQueryGuard
from modules.memory_read import MemoryReadRequest, explicit_memory_read_attributes
from modules.response_composer import _prior_memory_save_acknowledgement
from modules.verified_turn_context import VerifiedTurnContext
from v18_test_support import build_service


HABIT = "我习惯在周日晚上整理这一周的学习笔记"
CHALLENGE = "你确定吗？刚才不是这么说的。请核对已保存的目标和偏好，别把推测当记录。"
RAW_DENIAL = (
    "（轻轻点头）你核对得对，我重新说准确些。\n\n"
    "刚才那条“周日晚上整理学习笔记”是你这轮让我记住的，我按你的话复述了，"
    "但当时并没有真正写入长期记忆。\n\n"
    f"现在能核对的正式长期记忆只有一条，原文是：{HABIT}。"
    "它属于习惯类，不是你所说的目标，也没有别的偏好记录。"
    "除此之外，没有可核对的已保存目标。\n\n"
    "如果你希望把刚才那句正式保存，我可以按记忆操作流程帮你写入；"
    "在那之前，我不会说它已经记住了。"
)


class GateReplayLLM:
    def __init__(self):
        self.calls = []
        self.reply = RAW_DENIAL

    def chat(self, messages):
        self.calls.append(messages)
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            saving = "我想让你记住" in prompt.rsplit("User message: ", 1)[-1]
            return json.dumps({
                "mode": "write" if saving else "chat",
                "intent": "add_memory_request" if saving else "chat",
                "entities": {"content": HABIT} if saving else {},
                "proposed_tool": "save_formal_memory" if saving else None,
                "confidence": .95, "follow_up_target": None,
                "needs_confirmation": False, "warnings": [],
                "clarification_question": None, "candidate_actions": [],
                "subject": "self" if saving else "", "polarity": "positive" if saving else "",
                "modality": "commitment" if saving else "question",
                "request_mode": "execute" if saving else "discuss",
                "explicit_command": saving,
            }, ensure_ascii=False)
        return self.reply


def production_service(tmp_path):
    llm = GateReplayLLM()
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, memory, history, llm


def business_records(memory):
    # Read usage metadata is not a business edit.
    return [
        {k: v for k, v in record.items() if k not in {"use_count", "last_used", "last_used_at"}}
        for record in memory.memories()
    ]


def test_original_three_turn_replay_keeps_save_and_reads_only_requested_scope(tmp_path):
    service, memory, history, llm = production_service(tmp_path)
    first = service.handle("我想让你记住" + HABIT, "gate-replay")
    second = service.handle("展示一下我的长期记忆。", "gate-replay")
    before = business_records(memory)
    core = service.conversation_service
    prior_facts, prior_operation = core._verified_recent_memory_context(
        "gate-replay", core.state_manager.reference_context("gate-replay"), CHALLENGE,
    )
    response = service.handle(CHALLENGE, "gate-replay")
    evidence = {
        "fixture_source": "engineering gate 8923189 t01-t03; offline replay",
        "captured_raw_reply": RAW_DENIAL,
        "prior_facts": prior_facts, "prior_operation": prior_operation,
        "receipt_before": _prior_memory_save_acknowledgement(CHALLENGE, {
            "prior_memory_operation": VerifiedTurnContext._public_memory_operation(prior_operation),
        }),
        "first_tools": [r.to_dict() for r in first.tool_results],
        "second_tools": [r.to_dict() for r in second.tool_results],
        "third_tools": [r.to_dict() for r in response.tool_results],
        "final": response.message,
        "history": history.get_session("gate-replay"),
        "before": before, "after": business_records(memory),
        "offline_prompts": llm.calls,
    }
    (tmp_path / "gate-replay-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    assert [(r.tool, r.success) for r in first.tool_results] == [("save_formal_memory", True)]
    assert [(r.tool, r.success) for r in second.tool_results] == [("list_memories", True)]
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
    snapshot = response.tool_results[0].data["memory_read"]
    assert snapshot["snapshot_id"]
    # The existing semantic transport trims terminal punctuation, not scope.
    assert snapshot["request"]["query"].rstrip("。") == CHALLENGE.rstrip("。")
    assert snapshot["request"]["query_mode"] == "attribute"
    assert snapshot["facts"] == []  # The real saved habit is outside this read scope.
    assert business_records(memory) == before
    assert "目标" in response.message and "偏好" in response.message
    assert "并没有真正写入" not in response.message
    assert "已经保存" in response.message
    assert history.get_session("gate-replay")["messages"][-1]["content"] == response.message
    assert len(llm.calls) == 1  # Only the initial save's offline semantic fixture.


def test_nominal_saved_record_check_uses_verified_receipt():
    verified = {"prior_memory_operation": {
        "kind": "memory_saved", "status": "success", "content": HABIT,
        "observed_at": "2026-10-26T02:10:16+08:00",
    }}
    acknowledgement = _prior_memory_save_acknowledgement(
        "请核对刚才是否已经保存了这条记忆？", verified,
    )
    assert HABIT in acknowledgement
    assert "已经保存" in acknowledgement


@pytest.mark.parametrize("text", [
    "请核对已保存的目标和偏好。",
    "麻烦核实一下我的已经保存的偏好与目标。",
    "请查看正式记忆中的目标及偏好记忆。",
])
def test_union_roundtrips_existing_tool_arguments_and_filters_real_ids(tmp_path, text):
    service, memory, history, llm = production_service(tmp_path)
    goal = memory.add_memory("我的目标是完成合成课程", category="goal")
    preference = memory.add_memory("我喜欢拼图", category="preference")
    memory.add_memory(HABIT, category="habit")
    memory.add_memory("我在制作一个合成项目", category="project")
    before = business_records(memory)
    usage_before = {r["id"]: r.get("use_count", 0) for r in memory.memories()}
    response = service.handle(text, "union")
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
    snapshot = response.tool_results[0].data["memory_read"]
    request = MemoryReadRequest.from_arguments(snapshot["request"])
    assert set(request.to_tool_arguments()) <= {"query_mode", "attribute", "topic", "query"}
    assert set(request.requested_attributes) == {"goal", "preference"}
    assert {f["memory_id"] for f in snapshot["facts"]} == {goal["memory"]["id"], preference["memory"]["id"]}
    assert {f["category"] for f in snapshot["facts"]} == {"goal", "preference"}
    assert HABIT not in response.message
    assert "合成项目" not in response.message
    assert "目标" in response.message and "偏好" in response.message
    assert business_records(memory) == before
    used_ids = {goal["memory"]["id"], preference["memory"]["id"]}
    for record in memory.memories():
        assert record.get("use_count", 0) == usage_before[record["id"]] + (record["id"] in used_ids)
    assert history.get_session("union")["messages"][-1]["content"] == response.message
    assert llm.calls == []


@pytest.mark.parametrize("text", [
    "请保存我喜欢拼图", "请重新保存已保存的目标", "请帮我记住我喜欢拼图",
    "请核对已保存的目标和偏好，然后保存我喜欢拼图",
    "更正刚才的记忆，我现在不喜欢拼图", "取消刚才的保存", "不要核对已保存的目标和偏好",
    "我想讨论目标和偏好", "请核对明天的计划", "你喜欢什么",
    "请核对已保存的健康和关系",  # No newly visible private category.
])
def test_closed_read_scope_does_not_capture_mutations_refusals_or_other_domains(text):
    assert explicit_memory_read_attributes(text) == ()


@pytest.mark.parametrize("text", [
    "请核对刚才是否已经保存了这条记忆？",
    "你刚才已经保存了那条记忆吗？",
    "刚才保存了吗？",
])
def test_pure_source_challenge_has_real_save_receipt_no_repeat_write(tmp_path, text):
    service, memory, history, llm = production_service(tmp_path)
    service.handle("我想让你记住" + HABIT, "receipt")
    before = business_records(memory)
    llm.calls.clear()
    response = service.handle(text, "receipt")
    assert not response.tool_results
    assert HABIT in response.message
    assert "已经保存" in response.message
    assert "并没有真正写入" not in response.message
    assert business_records(memory) == before
    assert history.get_session("receipt")["messages"][-1]["content"] == response.message
    assert any("prior_memory_operation" in m["content"] for m in llm.calls[-1])


@pytest.mark.parametrize("text", [
    "请保存：我喜欢拼图", "请重新保存刚才那条记忆", "帮我记住：我喜欢拼图",
    "我现在不喜欢拼图了，刚才保存的情况变了", "更正刚才保存的记忆",
    "删除刚才保存的那条记忆", "取消刚才保存", "不要核对刚才保存的记忆",
])
def test_receipt_cannot_borrow_previous_success_for_new_request_or_correction(text):
    verified = {"prior_memory_operation": {
        "kind": "memory_saved", "status": "success", "content": HABIT,
        "observed_at": "2026-10-26T02:10:16+08:00",
    }}
    assert _prior_memory_save_acknowledgement(text, verified) == ""


@pytest.mark.parametrize("mutation", ["delete", "archive", "update", "expired_record", "expired_reference", "other_session"])
def test_live_invalidated_or_other_session_save_cannot_supply_receipt(tmp_path, mutation):
    service, memory, history, llm = production_service(tmp_path)
    core = service.conversation_service
    clock = [datetime(2026, 10, 26, 12)]
    core.now_provider = lambda: clock[0]
    core.state_manager.now_provider = lambda: clock[0]
    first = service.handle("我想让你记住" + HABIT, "live-check")
    memory_id = first.tool_results[0].data["memory_operation"]["memory_id"]
    if mutation == "delete":
        assert service.memory_service.delete_memory(memory_id).success
    elif mutation == "archive":
        assert service.memory_service.archive_memory(memory_id).success
    elif mutation == "update":
        assert service.memory_service.update_memory(memory_id, content="我现在习惯周六整理").success
    elif mutation == "expired_record":
        assert memory.update_memory(memory_id, valid_until="2000-01-01T00:00:00+00:00")
    elif mutation == "expired_reference":
        clock[0] += timedelta(seconds=core.state_manager.memory_reference_ttl_seconds + 1)
    conversation = "other" if mutation == "other_session" else "live-check"
    before = business_records(memory)
    response = service.handle(CHALLENGE, conversation)
    assert [(r.tool, r.success) for r in response.tool_results] == [("list_memories", True)]
    assert response.tool_results[0].data["memory_read"]["facts"] == []
    assert "刚才已经保存" not in response.message
    assert "目标" in response.message and "偏好" in response.message
    assert business_records(memory) == before


def test_explicit_single_attribute_keeps_precedence_over_query_union(tmp_path):
    service, memory, _history, _llm = production_service(tmp_path)
    goal = memory.add_memory("我的目标是完成合成课程", category="goal")
    memory.add_memory("我喜欢拼图", category="preference")
    result = service.memory_service.read_typed_memory(
        query_mode="attribute", attribute="goal", query=CHALLENGE,
    )
    assert result.success
    assert [f["memory_id"] for f in result.data["memory_read"]["facts"]] == [goal["memory"]["id"]]


@pytest.mark.parametrize("text", [
    "我现在不再周日晚上整理了，刚才保存的情况变了",
    "不要核对已保存的目标和偏好，我们继续聊",
    "取消",
])
def test_fresh_correction_refusal_and_cancellation_do_not_revive_save_receipt(tmp_path, text):
    service, memory, history, llm = production_service(tmp_path)
    service.handle("我想让你记住" + HABIT, "fresh-statement")
    before = business_records(memory)
    llm.reply = "明白，这次没有改写长期记忆。"
    response = service.handle(text, "fresh-statement")
    assert not response.tool_results
    assert "刚才已经保存" not in response.message
    assert business_records(memory) == before
    assert history.get_session("fresh-statement")["messages"][-1]["content"] == response.message


@pytest.mark.parametrize("text", [
    "请核对已保存的目标和偏好，然后把我的新目标保存",
    "核对已保存的目标和偏好，再帮我把新偏好记住",
    "核对已保存的目标和偏好，然后把" + "合成新目标" * 110 + "保存",
])
def test_compound_write_is_not_intercepted_by_deterministic_read_guard(text):
    assert MemoryDataQueryGuard().route(text) is None


def test_pure_read_with_separate_negated_new_save_keeps_its_scope():
    routed = MemoryDataQueryGuard().route("核对已保存的目标和偏好，不要保存新内容")
    assert routed is not None
    request = MemoryReadRequest.from_arguments(routed["entities"])
    assert set(request.requested_attributes) == {"goal", "preference"}
