from __future__ import annotations

import json
from datetime import datetime, timedelta

from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.intent_router import LLMIntentParser
from v18_test_support import NoCallLLM, build_service


class SemanticThenReplyLLM:
    def __init__(self, decision: dict, reply: str = "这是普通聊天回复。") -> None:
        self.decision = dict(decision)
        self.reply = str(reply)
        self.semantic_calls = 0
        self.reply_calls = []

    def chat(self, messages):
        first = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in first:
            self.semantic_calls += 1
            return json.dumps(self.decision, ensure_ascii=False)
        self.reply_calls.append(messages)
        return self.reply


class SequencedSemanticReplyLLM:
    def __init__(self, decisions: list[dict], replies: list[str] | None = None) -> None:
        self.decisions = [dict(item) for item in decisions]
        self.replies = list(replies or [])
        self.semantic_calls = 0
        self.reply_calls = []

    def chat(self, messages):
        first = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in first:
            self.semantic_calls += 1
            assert self.decisions, "unexpected extra semantic decision request"
            return json.dumps(self.decisions.pop(0), ensure_ascii=False)
        self.reply_calls.append(messages)
        assert self.replies, "unexpected extra reply request"
        return self.replies.pop(0)


def _decision(
    intent: str,
    *,
    entities: dict | None = None,
    proposed_tool: str | None = None,
    mode: str = "write",
    request_mode: str = "execute",
    explicit_command: bool = True,
    clarification_question: str | None = None,
    follow_up_target: str | None = None,
    modality: str | None = None,
) -> dict:
    return {
        "mode": mode,
        "intent": intent,
        "entities": dict(entities or {}),
        "proposed_tool": proposed_tool,
        "confidence": 0.98,
        "follow_up_target": follow_up_target,
        "needs_confirmation": bool(clarification_question),
        "warnings": [],
        "clarification_question": clarification_question,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": (
            modality
            if modality is not None
            else "commitment" if request_mode == "execute" else ""
        ),
        "request_mode": request_mode,
        "explicit_command": explicit_command,
    }


def _chat_decision() -> dict:
    return _decision(
        "chat",
        mode="chat",
        request_mode="discuss",
        explicit_command=False,
    )


def _production_service(tmp_path, llm):
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def test_new_user_capability_question_is_not_hijacked_by_memory_read(tmp_path):
    reply = "我是洛琪希，可以陪你聊天，也能帮你管理今日计划、行动记录和成长复盘。"
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, _growth, memory, _history = _production_service(tmp_path, llm)
    service.memory_service.save_formal_memory("隔离测试中的长期记忆")

    response = service.handle(
        "我是新用户，我还不太了解你，告诉我你能干什么",
        "v22-release-capability-not-memory",
    )

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert "隔离测试中的长期记忆" not in response.message
    assert llm.semantic_calls == 1


def test_negated_memory_correction_does_not_open_memory_scope_clarification(
    tmp_path,
):
    reply = "明白，你问的不是关于你的记忆。刚才是我理解错了。"
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, _growth, memory, _history = _production_service(tmp_path, llm)
    service.memory_service.save_formal_memory("隔离测试中的长期记忆")
    conversation_id = "v22-release-negated-memory-correction"

    response = service.handle(
        "我不是让你告诉我关于我的记忆",
        conversation_id,
    )

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert "正式长期记忆" not in response.message
    assert "待审核候选" not in response.message
    assert service.interaction_coordinator.current(conversation_id).pending is False


def test_candidate_scope_is_retired_without_formal_memory_alias(tmp_path):
    llm = SemanticThenReplyLLM(
        _decision(
            "show_memory_conflicts",
            proposed_tool="list_memory_conflicts",
            mode="read",
            request_mode="query",
            explicit_command=False,
        )
    )
    service, _growth, memory, _history = _production_service(tmp_path, llm)
    service.memory_service.save_formal_memory("我喜欢安静学习")

    response = service.handle(
        "待审核候选",
        "v22-release-candidate-scope-formal-read",
    )

    assert response.status == "failed"
    assert response.tool_results == []
    assert "候选记忆功能已经停用" in response.message
    assert "查看长期记忆" in response.message
    assert "喜欢安静学习" not in response.message
    assert llm.semantic_calls == 0
    assert [item["content"] for item in memory.memories()] == ["我喜欢安静学习"]


def test_chat_after_verified_add_cannot_retract_previous_success(tmp_path):
    false_retraction = (
        "西瓜确实很适合夏天。\n\n"
        "不过刚才那条我得更正一下：我其实没有真的执行添加操作，"
        "简历包装目前还没有被记录进今日计划。"
    )
    llm = SequencedSemanticReplyLLM(
        [_chat_decision()],
        [false_retraction],
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-prior-success-not-retracted"

    added = service.handle("把简历包装加入今天计划", conversation_id)
    chatted = service.handle("我喜欢吃西瓜", conversation_id)

    assert [item.tool for item in added.tool_results] == ["add_plan"]
    assert chatted.status == "chat"
    assert chatted.tool_results == []
    assert "西瓜确实很适合夏天" in chatted.message
    assert "没有真的执行" not in chatted.message
    assert "还没有被记录" not in chatted.message
    assert [item["title"] for item in growth.tasks()] == ["简历包装"]


def test_user_can_acknowledge_verified_prior_add_without_truth_guard_rewrite(
    tmp_path,
):
    llm = SequencedSemanticReplyLLM(
        [_chat_decision()],
        ["对，刚才已经把“简历包装”加入今天计划了。"],
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-prior-success-acknowledged"

    service.handle("把简历包装加入今天计划", conversation_id)
    response = service.handle("我看到已经加入了啊", conversation_id)

    assert response.status == "chat"
    assert response.tool_results == []
    assert "简历包装" in response.message
    assert "已经" in response.message
    assert "表达不够准确" not in response.message
    assert [item["title"] for item in growth.tasks()] == ["简历包装"]


def test_same_title_real_world_join_after_verified_add_stays_natural(tmp_path):
    reply = "这件事已经完成了，辛苦了。"
    llm = SequencedSemanticReplyLLM([_chat_decision()], [reply])
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-same-title-real-world-join"

    added = service.handle("把项目群加入今天计划", conversation_id)
    chatted = service.handle("我今天正式加入项目群了", conversation_id)

    assert [item.tool for item in added.tool_results] == ["add_plan"]
    assert chatted.status == "chat"
    assert chatted.message == reply
    assert chatted.tool_results == []
    assert [item["title"] for item in growth.tasks()] == ["项目群"]
    assert growth.tasks()[0]["done"] is False


def test_delete_last_plan_clarification_binds_real_confirmation_and_never_adds(
    tmp_path,
):
    decision = _decision(
        "delete_plan",
        entities={"task_ref": "last_task"},
        proposed_tool="delete_plan",
        mode="clarify",
        request_mode="execute",
        clarification_question=(
            "你是指删除最近提到的“英语阅读”这个计划吗？确认后我再执行删除。"
        ),
        follow_up_target="last_task",
    )
    llm = SequencedSemanticReplyLLM([decision])
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-delete-last-task-confirmation"

    added = service.handle("把简历包装加入今天计划", conversation_id)
    assert [item.tool for item in added.tool_results] == ["add_plan"]
    preview = service.handle("删除这个计划", conversation_id)
    state = service.interaction_coordinator.current(conversation_id)
    pending = service.confirmation_manager.pending(scope=conversation_id)

    assert preview.status == "clarification"
    assert preview.tool_results == []
    assert state.state == "awaiting_confirmation"
    assert pending is not None
    assert pending["tool"] == "delete_plan"
    assert "简历包装" in preview.message
    assert "英语阅读" not in preview.message
    assert "task_ref" not in preview.message

    confirmed = service.handle("确认", conversation_id)

    assert confirmed.status == "completed"
    assert [item.tool for item in confirmed.tool_results] == ["delete_plan"]
    assert confirmed.tool_results[0].success is True
    assert "task_ref" not in confirmed.message
    assert growth.tasks() == []


def test_negated_plan_display_with_advice_purpose_does_not_run_show_plan(tmp_path):
    reply = "如果只选一件，我建议先做最接近截止时间的那项。"
    llm = SemanticThenReplyLLM(
        _decision(
            "chat",
            mode="chat",
            request_mode="advice",
            explicit_command=False,
        ),
        reply,
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    growth.add_task("整理封版说明")

    response = service.handle(
        "不要展示今天的计划，我该先做什么？",
        "v22-release-negated-read",
    )

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert llm.semantic_calls == 1


def test_current_turn_explicit_date_overrides_stale_model_date(tmp_path):
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    llm = SemanticThenReplyLLM(
        _decision(
            "show_plan",
            entities={"date": yesterday.isoformat()},
            proposed_tool="show_plan",
            mode="read",
            request_mode="query",
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    growth.add_task("昨日核对项", date=yesterday.isoformat())
    growth.add_task("今日封版项", date=today.isoformat())
    conversation_id = "v22-release-date-priority"

    first = service.handle("昨天有哪些计划？", conversation_id)
    second = service.handle("今天的呢？", conversation_id)

    assert first.tool_results[0].data["date"] == yesterday.isoformat()
    assert second.tool_results[0].data["date"] == today.isoformat()
    assert [item["title"] for item in second.tool_results[0].data["tasks"]] == [
        "今日封版项"
    ]


def test_explicit_commitment_repairs_model_possible_action_mode_and_adds_plan(
    tmp_path,
):
    llm = SemanticThenReplyLLM(
        _decision(
            "add_plan",
            entities={"title": "简历包装"},
            proposed_tool="add_plan",
            request_mode="possible_action",
            explicit_command=True,
            modality="commitment",
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)

    response = service.handle(
        "我今天要完成简历包装",
        "v22-release-explicit-commitment-repair",
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["简历包装"]


def test_desire_is_not_promoted_to_plan_write_by_explicit_flag_alone(tmp_path):
    reply = "可以先聊聊怎么拆解，是否加入计划由你决定。"
    llm = SemanticThenReplyLLM(
        _decision(
            "add_plan",
            entities={"title": "学习摄影"},
            proposed_tool="add_plan",
            request_mode="possible_action",
            explicit_command=True,
            modality="desire",
        ),
        reply,
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)

    response = service.handle(
        "我以后想学摄影",
        "v22-release-desire-remains-non-writing",
    )

    assert response.tool_results == []
    assert growth.tasks() == []


def test_complete_plan_clarification_keeps_capability_and_never_asks_duration(
    tmp_path,
):
    llm = SemanticThenReplyLLM(
        _decision(
            "complete_plan",
            proposed_tool="complete_plan",
            mode="clarify",
            request_mode="execute",
            clarification_question="你完成的是哪一项计划？",
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    task = growth.add_task("整理封版文档")
    conversation_id = "v22-release-completion-clarification"

    first = service.handle("我完成了一项计划", conversation_id)
    second = service.handle("整理封版文档", conversation_id)

    assert first.status == "clarification"
    assert second.status == "completed"
    assert "准备学多久" not in second.message
    assert [item.tool for item in second.tool_results] == ["complete_plan"]
    saved = next(item for item in growth.tasks() if item["uid"] == task["uid"])
    assert saved["done"] is True


def test_show_plan_records_structured_snapshot_with_stable_ids_and_order(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first = growth.add_task("核对测试分类", time_slot="上午", duration_minutes=20)
    second = growth.add_task("同步封版文档", time_slot="下午", duration_minutes=35)
    conversation_id = "v22-release-read-snapshot"

    response = service.handle("查看今天计划", conversation_id)
    state = service.interaction_coordinator.current(conversation_id)
    snapshot = state.last_read_snapshot

    assert response.status == "completed"
    assert snapshot["tool_name"] == "show_plan"
    assert [item["stable_id"] for item in snapshot["objects"]] == [
        first["uid"],
        second["uid"],
    ]
    assert [item["display_order"] for item in snapshot["objects"]] == [1, 2]
    assert snapshot["objects"][1]["duration_minutes"] == 35


def test_display_ordinal_uses_snapshot_identity_not_changed_live_order(tmp_path):
    llm = SemanticThenReplyLLM(
        _decision(
            "complete_plan",
            entities={"query": "计划三"},
            proposed_tool="complete_plan",
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    first = growth.add_task("计划一")
    second = growth.add_task("计划二")
    third = growth.add_task("计划三")
    conversation_id = "v22-release-snapshot-ordinal"

    service.handle("查看今天计划", conversation_id)
    growth.delete_by_id(int(first["id"]))
    response = service.handle("第二个完成了", conversation_id)
    by_uid = {item["uid"]: item for item in growth.tasks()}

    assert response.status == "completed"
    assert by_uid[second["uid"]]["done"] is True
    assert by_uid[third["uid"]]["done"] is False


def test_all_recently_displayed_plans_require_one_confirmation_and_use_snapshot(
    tmp_path,
):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first = growth.add_task("计划甲")
    second = growth.add_task("计划乙")
    conversation_id = "v22-release-snapshot-all"

    service.handle("查看今天计划", conversation_id)
    later = growth.add_task("展示后新增计划")
    preview = service.handle("刚才展示的计划我都完成了", conversation_id)

    assert preview.status == "clarification"
    assert "计划甲" in preview.message
    assert "计划乙" in preview.message
    assert "展示后新增计划" not in preview.message
    assert all(not item["done"] for item in growth.tasks())

    completed = service.handle("确认", conversation_id)
    by_uid = {item["uid"]: item for item in growth.tasks()}

    assert completed.status == "completed"
    assert by_uid[first["uid"]]["done"] is True
    assert by_uid[second["uid"]]["done"] is True
    assert by_uid[later["uid"]]["done"] is False


def test_all_five_displayed_plans_complete_after_one_confirmation(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    tasks = [growth.add_task(f"批量计划{index}") for index in range(1, 6)]
    conversation_id = "v22-release-snapshot-five"

    service.handle("查看今天计划", conversation_id)
    preview = service.handle("刚才展示的计划我都完成了", conversation_id)
    before = {item["uid"]: item["done"] for item in growth.tasks()}
    completed = service.handle("确认", conversation_id)
    after = {item["uid"]: item["done"] for item in growth.tasks()}

    assert preview.status == "clarification"
    assert before == {item["uid"]: False for item in tasks}
    assert completed.status == "completed"
    assert len(completed.tool_results) == 5
    assert all(item.tool == "complete_plan" and item.success for item in completed.tool_results)
    assert after == {item["uid"]: True for item in tasks}


def test_negative_batch_completion_claim_never_opens_confirmation_or_writes(
    tmp_path,
):
    reply = "明白，还有计划没有完成。"
    llm = SemanticThenReplyLLM(
        _decision(
            "chat",
            mode="chat",
            request_mode="discuss",
            explicit_command=False,
        ),
        reply,
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    growth.add_task("核对发布清单")
    growth.add_task("整理测试结果")
    conversation_id = "v22-release-negative-batch"
    service.handle("查看今天计划", conversation_id)

    response = service.handle("计划我还没有都完成", conversation_id)

    assert response.status == "chat"
    assert response.message
    assert response.tool_results == []
    assert all(not item["done"] for item in growth.tasks())
    assert service.interaction_coordinator.current(conversation_id).pending is False


def test_batch_completion_status_question_reads_without_opening_confirmation(
    tmp_path,
):
    llm = SemanticThenReplyLLM(
        _decision(
            "show_plan",
            proposed_tool="show_plan",
            mode="read",
            request_mode="query",
            explicit_command=False,
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    growth.add_task("仍待完成的封版项")

    response = service.handle(
        "所有计划都完成了吗？",
        "v22-release-batch-question",
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["show_plan"]
    assert all(not item["done"] for item in growth.tasks())
    assert service.interaction_coordinator.current(
        "v22-release-batch-question"
    ).pending is False


def test_implicit_stable_memory_is_only_runtime_candidate_until_confirmation(
    tmp_path,
):
    llm = SemanticThenReplyLLM(
        _decision(
            "add_memory_request",
            entities={
                "content": "模型编造的内容",
                "category": "user_preference",
            },
            proposed_tool="save_formal_memory",
            explicit_command=False,
        )
    )
    service, _growth, memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-memory-offer"

    offered = service.handle(
        "我平时更喜欢先看原理再动手",
        conversation_id,
    )
    state = service.interaction_coordinator.current(conversation_id)

    assert offered.status == "clarification"
    assert offered.tool_results == []
    assert memory.memories() == []
    assert state.interaction_kind == "formal_memory_save"
    assert state.immutable_arguments["content"] == "先看原理再动手"

    saved = service.handle("确认", conversation_id)

    assert [item.tool for item in saved.tool_results] == ["save_formal_memory"]
    assert [item["content"] for item in memory.memories()] == ["先看原理再动手"]


def test_implicit_sensitive_memory_is_not_suggested_or_persisted(tmp_path):
    reply = "听起来饮食上需要更照顾自己的感受。"
    llm = SemanticThenReplyLLM(
        _decision(
            "add_memory_request",
            entities={
                "content": "我肠胃比较敏感，不适合吃太油",
                "category": "health_lifestyle",
            },
            proposed_tool="save_formal_memory",
            explicit_command=False,
        ),
        reply,
    )
    service, _growth, memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-release-sensitive-memory"

    response = service.handle(
        "我肠胃比较敏感，不适合吃太油",
        conversation_id,
    )
    state = service.interaction_coordinator.current(conversation_id)

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert memory.memories() == []
    assert state.pending is False


def test_v22_model_tool_scope_hides_degraded_and_candidate_management_tools(
    tmp_path,
):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    visible = {
        item["function"]["name"]
        for item in service.tool_registry.model_tool_contracts()
    }

    assert {
        "add_plan",
        "show_plan",
        "complete_plan",
        "add_action_log",
        "generate_daily_review",
        "show_growth_log",
        "save_formal_memory",
        "show_conversation_history",
    }.issubset(visible)
    assert visible.isdisjoint(
        {
            "inspect_plan_duplicates",
            "update_plan",
            "merge_plan",
            "reschedule_plan",
            "create_memory_candidate",
            "list_memory_candidates",
            "accept_memory_candidate",
            "accept_memory_candidates",
            "reject_memory_candidate",
            "reject_memory_candidates",
            "accept_all_memory_candidates",
        }
    )


def test_capability_catalog_is_the_only_tool_policy_and_visibility_authority(
    tmp_path,
):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    registry = service.tool_registry

    for name in registry.names():
        capability = DEFAULT_CAPABILITY_REGISTRY.for_tool(name)
        assert capability is not None, name
        tool = registry.get(name)
        assert tool.side_effect is capability.side_effect
        assert tool.risk_level == capability.risk_level
        assert tool.confirmation_policy == capability.confirmation_policy
        assert tool.reversible is capability.reversible

    visible = {
        item["function"]["name"]
        for item in registry.model_tool_contracts()
    }
    expected_visible = {
        capability.canonical_tool_name
        for capability in DEFAULT_CAPABILITY_REGISTRY.definitions()
        if capability.model_visible and capability.canonical_tool_name
    }
    assert visible == expected_visible


def test_model_request_for_degraded_plan_change_fails_honestly_without_write(
    tmp_path,
):
    llm = SemanticThenReplyLLM(
        _decision(
            "update_plan",
            entities={
                "task_ref": "1",
                "changes": {"duration_minutes": 60},
            },
            proposed_tool="update_plan",
        )
    )
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    task = growth.add_task("整理封版文档", duration_minutes=20)

    response = service.handle(
        "把整理封版文档改成一小时",
        "v22-release-degraded-plan-change",
    )
    saved = next(item for item in growth.tasks() if item["uid"] == task["uid"])

    assert response.status == "failed"
    assert response.tool_results == []
    assert response.message.startswith(
        "抱歉，我当前这个功能还不完善，这次没有完成。你可以这样说："
    )
    assert saved["duration_minutes"] == 20


def test_pending_operation_stops_after_two_clarification_rounds(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "v22-release-two-clarifications"
    service.interaction_coordinator.awaiting_clarification(
        conversation_id,
        "missing_slots",
        missing_fields=["duration_minutes"],
        immutable_arguments={"tool_name": "add_plan", "title": "复习概率论"},
        domain="plan",
        request_mode="execute",
        original_user_text="把复习概率论加入今天计划",
        known_fields={"title": "复习概率论"},
        safe_summary="准备学多久？",
    )

    second_round = service.handle("不知道", conversation_id)
    stopped = service.handle("不知道", conversation_id)
    state = service.interaction_coordinator.current(conversation_id)

    assert second_round.status == "clarification"
    assert stopped.status == "failed"
    assert stopped.message == (
        "抱歉，我当前这个功能还不完善，这次没有完成。"
        "你可以这样说：添加今天计划：具体事项，30分钟"
    )
    assert growth.tasks() == []
    assert state.state == "cancelled"
    assert state.pending is False


def test_history_fuzzy_query_returns_only_verified_old_summary(tmp_path):
    service, _growth, _memory, history = build_service(tmp_path, NoCallLLM())
    history.new_session(session_id="old-persona-topic")
    history.add_message(
        "old-persona-topic",
        "user",
        "我们讨论了人格包的导入格式和上下文预算。",
    )
    history.add_message(
        "old-persona-topic",
        "assistant",
        "可以先核对角色资料的字段边界。",
    )
    assert history.finalize_session("old-persona-topic") is True
    history.new_session(session_id="old-unrelated")
    history.add_message("old-unrelated", "user", "今天跑步三公里。")
    assert history.finalize_session("old-unrelated") is True

    response = service.handle(
        "以前聊过哪些关于人格包的事？",
        "current-history-query",
    )
    tool_result = response.tool_results[0]
    sessions = tool_result.data["sessions"]

    assert response.status == "completed"
    assert tool_result.tool == "show_conversation_history"
    assert tool_result.data["query"] == "人格包"
    assert [item["session_id"] for item in sessions] == ["old-persona-topic"]
    assert sessions[0]["source"] == "local_conversation_summary"
    assert sessions[0]["source_date"]
    assert "人格包" in sessions[0]["summary"]
    assert "current-history-query" not in json.dumps(sessions, ensure_ascii=False)
    assert "来源：本地会话摘要" in response.message


def test_history_fuzzy_query_reports_no_match_without_current_turn_invention(
    tmp_path,
):
    service, _growth, _memory, history = build_service(tmp_path, NoCallLLM())
    history.new_session(session_id="old-running-topic")
    history.add_message("old-running-topic", "user", "我们只讨论过周末跑步计划。")
    assert history.finalize_session("old-running-topic") is True

    response = service.handle(
        "以前聊过哪些关于海洋生物的事？",
        "current-no-history-match",
    )

    assert response.status == "completed"
    assert response.tool_results[0].data["sessions"] == []
    assert response.message == "没有找到与“海洋生物”匹配的旧会话。"
