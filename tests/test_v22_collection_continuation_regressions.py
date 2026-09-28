from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from modules.intent_router import LLMIntentParser
from modules.contracts import AgentResponse, ToolResult
from modules.suggestion_snapshot import SuggestionSnapshot
from v18_test_support import NoCallLLM, build_service


class CollectionDecisionLLM:
    def __init__(self, intent):
        self.intent = intent

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "clarify" if self.intent == "complete_plan" else "write",
                    "intent": self.intent,
                    "entities": {} if self.intent == "complete_plan" else {"title": "所有建议"},
                    "proposed_tool": self.intent,
                    "confidence": 0.95,
                    "follow_up_target": None,
                    "needs_confirmation": self.intent == "complete_plan",
                    "warnings": [],
                    "clarification_question": "请选择具体计划。" if self.intent == "complete_plan" else None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "commitment",
                    "request_mode": "execute",
                    "explicit_command": True,
                }, ensure_ascii=False,
            )
        return "没有执行操作。"


class NumberedPlanChatLLM:
    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "chat",
                    "intent": "chat",
                    "entities": {},
                    "proposed_tool": None,
                    "confidence": 0.98,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "",
                    "request_mode": "discuss",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return (
            "1. 阅读一个课程章节\n"
            "2. 整理三条概念笔记\n"
            "3. 练习一道配套题目\n"
            "4. 复习一条核心规则"
        )


class RefinementPlanChatLLM(NumberedPlanChatLLM):
    def __init__(self):
        self.refinement_prompt = ""

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return super().chat(messages)
        self.refinement_prompt = "\n".join(
            str(item.get("content", "")) for item in messages
        )
        return (
            "1. 复习一个机器学习概念并写三句解释\n"
            "2. 运行一个最小训练示例并观察一个参数\n"
            "3. 整理一个机器学习疑问"
        )


class MisreadPlanSuggestionLLM(NumberedPlanChatLLM):
    """Reproduce the real provider mistake observed in desktop acceptance."""

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "read",
                    "intent": "show_plan",
                    "entities": {"date": datetime.now().date().isoformat()},
                    "proposed_tool": "show_plan",
                    "confidence": 0.94,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "question",
                    "request_mode": "query",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return super().chat(messages)


class ThreePlanDecisionLLM:
    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "write",
                    "intent": "multi_action",
                    "entities": {},
                    "proposed_tool": None,
                    "confidence": 0.98,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [
                        {"intent": "add_plan", "entities": {"title": "查车票或机票"}},
                        {"intent": "add_plan", "entities": {"title": "列要带回家的东西"}},
                        {"intent": "add_plan", "entities": {"title": "确定返程日期"}},
                    ],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "commitment",
                    "request_mode": "execute",
                    "explicit_command": True,
                },
                ensure_ascii=False,
            )
        return "没有执行操作。"


class SnapshotReferenceLLM:
    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "write",
                    "intent": "add_plan",
                    "entities": {"title": "你说的"},
                    "proposed_tool": "add_plan",
                    "confidence": 0.98,
                    "follow_up_target": "previous_assistant_message",
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "commitment",
                    "request_mode": "execute",
                    "explicit_command": True,
                },
                ensure_ascii=False,
            )
        raise AssertionError("a verified suggestion snapshot must not be re-extracted")


def _production_service(tmp_path, intent):
    llm = CollectionDecisionLLM(intent)
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def _seed_suggestions(runtime, conversation_id, *, pending=True, options=None):
    options = options or [
        {"id": "1", "title": "阅读一个课程章节"},
        {"id": "2", "title": "整理三条概念笔记"},
        {"id": "3", "title": "练习一道配套题目"},
    ]
    if pending:
        runtime.interaction_coordinator.awaiting_choice(
            conversation_id, "assistant_plan_selection", options=options,
            listed_object_ids=[item["id"] for item in options],
            immutable_arguments={"tool_name": "add_plan"}, domain="plan",
            request_mode="execute",
        )
    runtime.interaction_coordinator.remember_suggestion_snapshot(
        conversation_id,
        SuggestionSnapshot.for_options(conversation_id=conversation_id, options=options),
    )
    return options


def test_three_explicit_plan_lines_execute_all_three_tools(tmp_path):
    llm = ThreePlanDecisionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False

    response = service.handle(
        "2. 查车票或机票\n3. 列要带回家的东西\n4. 确定返程日期，把这些加入今日计划",
        "three-explicit-plan-lines",
    )

    assert response.status == "completed"
    assert len(response.tool_results) == 3
    assert all(item.success and item.tool == "add_plan" for item in response.tool_results)
    assert [item["title"] for item in growth.tasks()] == [
        "查车票或机票",
        "列要带回家的东西",
        "确定返程日期",
    ]


def test_online_observed_suffix_batch_reaches_model_and_executes_all_three(tmp_path):
    llm = ThreePlanDecisionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False

    response = service.handle(
        "我今天要做三件事：查车票或机票、列要带回家的东西、确定返程日期，请都加进计划",
        "online-observed-three-suffix-plans",
    )

    assert response.status == "completed"
    assert len(response.tool_results) == 3
    assert all(item.success and item.tool == "add_plan" for item in response.tool_results)
    assert len(growth.tasks()) == 3


def test_online_observed_advice_suffix_negation_never_writes_and_keeps_snapshot(tmp_path):
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False

    response = service.handle(
        "请给我四项今天可以做的机器学习计划建议，先只给建议，不要加入计划",
        "online-observed-advice-negation",
    )

    assert response.status == "chat"
    assert response.tool_results == []
    assert growth.tasks() == []
    snapshot = runtime.interaction_coordinator.current(
        "online-observed-advice-negation"
    ).last_suggestion_snapshot
    assert len(snapshot["objects"]) == 4


def test_assistant_reference_reuses_full_verified_snapshot_without_shrinking(tmp_path):
    llm = SnapshotReferenceLLM()
    service, _growth, _memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "full-snapshot-reference"
    options = [
        {"id": str(index), "title": title}
        for index, title in enumerate(
            ("认识矩阵形状", "练习矩阵加减", "理解矩阵乘法", "观察线性变换", "整理一个疑问"),
            1,
        )
    ]
    history.new_session(session_id=conversation_id)
    displayed = "\n".join(
        f"{index}. {item['title']}" for index, item in enumerate(options, 1)
    )
    history.add_message(conversation_id, "assistant", displayed, intent="chat")
    _seed_suggestions(runtime, conversation_id, pending=False, options=options)

    response = service.handle("能把你说的加入今日计划吗", conversation_id)

    assert response.status == "clarification"
    assert all(item["title"] in response.message for item in options)
    state = runtime.interaction_coordinator.current(conversation_id)
    assert len(state.suggested_options) == 5
    assert len(state.last_suggestion_snapshot["objects"]) == 5


def test_polite_question_in_live_selection_repeats_full_verified_list(tmp_path):
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "polite-live-snapshot-reference"

    listed = service.handle(
        "请给我四项今天可以做的机器学习计划建议，先只给建议，不要加入计划",
        conversation_id,
    )
    response = service.handle("能把你说的加入今日计划吗", conversation_id)

    assert listed.status == "chat"
    assert response.status == "clarification"
    assert response.tool_results == []
    assert growth.tasks() == []
    for title in (
        "阅读一个课程章节",
        "整理三条概念笔记",
        "练习一道配套题目",
        "复习一条核心规则",
    ):
        assert title in response.message
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_selection"
    assert len(state.last_suggestion_snapshot["objects"]) == 4


def test_semantic_all_completion_binds_recent_read_and_confirms_once(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "complete_plan")
    targets = [growth.add_task(title) for title in ("核对目录", "整理说明", "检查依赖")]
    conversation_id = "collection-completion"
    service.handle("查看今天计划", conversation_id)

    preview = service.handle("我都完成了", conversation_id)

    assert preview.status == "clarification"
    assert all(item["title"] in preview.message for item in targets)
    assert all(not item["done"] for item in growth.tasks())
    assert service.conversation_service.interaction_coordinator.current(conversation_id).interaction_kind == "plan_batch_completion"
    completed = service.handle("确认", conversation_id)
    assert completed.status == "completed"
    assert len(completed.tool_results) == 3
    assert all(item.tool == "complete_plan" for item in completed.tool_results)
    assert all(item["done"] for item in growth.tasks())
    assert len(growth.tasks()) == 3
    assert growth.records_for_date() == []


@pytest.mark.parametrize("reply", ["全部", "全部三条"])
def test_missing_completion_target_all_is_collection_not_title(tmp_path, reply):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    targets = [growth.add_task(title) for title in ("核对目录", "整理说明", "检查依赖")]
    conversation_id = "typed-missing-completion"
    service.handle("查看今天计划", conversation_id)
    runtime = service.conversation_service
    runtime.interaction_coordinator.awaiting_clarification(
        conversation_id, "missing_slots", domain="plan", missing_fields=["task_ref"],
        immutable_arguments={"tool_name": "complete_plan"}, request_mode="execute",
    )

    preview = service.handle(reply, conversation_id)

    assert preview.status == "clarification"
    assert all(item["title"] in preview.message for item in targets)
    assert runtime.interaction_coordinator.current(conversation_id).interaction_kind == "plan_batch_completion"
    assert all(not item["done"] for item in growth.tasks())
    assert growth.records_for_date() == []


def test_all_suggestions_use_the_displayed_collection_and_consume_each_success(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "all-suggestions"
    options = _seed_suggestions(runtime, conversation_id)

    response = service.handle("全部都加入", conversation_id)

    assert response.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [item["title"] for item in options]
    assert len(response.tool_results) == 3
    assert all(item.tool == "add_plan" for item in response.tool_results)
    raw_snapshot = runtime.interaction_coordinator.current(conversation_id).last_suggestion_snapshot
    assert all(item["consumed"] for item in raw_snapshot["objects"])


@pytest.mark.parametrize("user_text", ["把刚才四项全部加入今日计划", "把1234全加入今日计划"])
def test_all_snapshot_suggestions_over_model_action_cap_execute_in_one_turn(tmp_path, user_text):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = f"all-four-{user_text}"
    options = [
        {"id": "1", "title": "阅读一个课程章节"},
        {"id": "2", "title": "整理三条概念笔记"},
        {"id": "3", "title": "练习一道配套题目"},
        {"id": "4", "title": "复习一条核心规则"},
    ]
    _seed_suggestions(runtime, conversation_id, options=options)

    response = service.handle(user_text, conversation_id)

    assert response.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [item["title"] for item in options]
    assert len(response.tool_results) == 4
    assert all(item.tool == "add_plan" and item.success for item in response.tool_results)
    snapshot = runtime.interaction_coordinator.current(conversation_id).last_suggestion_snapshot
    assert all(item["consumed"] for item in snapshot["objects"])


def test_snapshot_batch_duplicate_is_one_atomic_choice_then_adds_only_new_items(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "snapshot-batch-duplicate"
    options = [
        {"id": "1", "title": "阅读一个课程章节"},
        {"id": "2", "title": "整理三条概念笔记"},
        {"id": "3", "title": "练习一道配套题目"},
        {"id": "4", "title": "复习一条核心规则"},
    ]
    growth.add_task(options[1]["title"])
    _seed_suggestions(runtime, conversation_id, options=options)

    preview = service.handle("全部加入今日计划", conversation_id)

    assert preview.status == "clarification"
    assert [item["title"] for item in growth.tasks()] == [options[1]["title"]]
    assert "整理三条概念笔记" in preview.message
    assert "添加未重复项" in preview.message
    assert "仍然全部添加" in preview.message
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "suggestion_batch_duplicate_resolution"

    added = service.handle("添加未重复项", conversation_id)

    assert added.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [
        options[1]["title"], options[0]["title"], options[2]["title"], options[3]["title"],
    ]
    assert len(added.tool_results) == 3
    assert all(item.success and item.tool == "add_plan" for item in added.tool_results)
    snapshot = runtime.interaction_coordinator.current(conversation_id).last_suggestion_snapshot
    assert all(item["consumed"] for item in snapshot["objects"])


def test_snapshot_batch_duplicate_can_still_add_the_whole_verified_list(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "snapshot-batch-force-duplicate"
    options = [
        {"id": "1", "title": "阅读一个课程章节"},
        {"id": "2", "title": "整理三条概念笔记"},
        {"id": "3", "title": "练习一道配套题目"},
        {"id": "4", "title": "复习一条核心规则"},
    ]
    growth.add_task(options[1]["title"])
    _seed_suggestions(runtime, conversation_id, options=options)
    service.handle("全部加入今日计划", conversation_id)

    added = service.handle("仍然全部添加", conversation_id)

    assert added.status == "completed"
    assert len(added.tool_results) == 4
    assert all(item.success and item.tool == "add_plan" for item in added.tool_results)
    assert [item["title"] for item in growth.tasks()] == [
        options[1]["title"],
        options[0]["title"],
        options[1]["title"],
        options[2]["title"],
        options[3]["title"],
    ]
    snapshot = runtime.interaction_coordinator.current(conversation_id).last_suggestion_snapshot
    assert all(item["consumed"] for item in snapshot["objects"])


def test_cancelled_plan_choice_is_not_revived_by_an_unbound_duration_reply(tmp_path):
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "numbered-plan-after-duration"
    runtime.interaction_coordinator.awaiting_choice(
        conversation_id,
        "advice_or_action_choice",
        domain="plan",
        request_mode="advice",
        known_fields={"title": "机器学习"},
        immutable_arguments={"tool_name": "add_plan", "title": "机器学习"},
    )
    runtime.interaction_coordinator.cancel(conversation_id)

    listed = service.handle("我能学习三个小时", conversation_id)

    assert listed.status == "chat"
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.state == "cancelled"
    assert not state.pending
    assert not state.last_suggestion_snapshot
    assert growth.tasks() == []


def test_advice_with_explicit_no_write_still_keeps_the_displayed_list_for_later_adds(
    tmp_path,
):
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "advice-now-write-later"

    listed = service.handle(
        "根据下面四件事给我今日计划建议，先不要加入计划："
        "阅读课程一章、整理三条笔记、练习一道题、复习一条规则",
        conversation_id,
    )

    assert listed.status == "chat"
    assert growth.tasks() == []
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_offer"
    assert [item["title"] for item in state.last_suggestion_snapshot["objects"]] == [
        "阅读一个课程章节",
        "整理三条概念笔记",
        "练习一道配套题目",
        "复习一条核心规则",
    ]

    ambiguous = service.handle("添加进今日计划", conversation_id)
    assert ambiguous.status == "clarification"
    assert growth.tasks() == []
    assert "第一个" in ambiguous.message and "第二个" in ambiguous.message

    first = service.handle("第一个", conversation_id)
    remaining = service.handle("剩下的也加入", conversation_id)

    assert first.status == "completed"
    assert remaining.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [
        "阅读一个课程章节",
        "整理三条概念笔记",
        "练习一道配套题目",
        "复习一条核心规则",
    ]


def test_actual_advice_cancellation_does_not_create_a_suggestion_snapshot(tmp_path):
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "cancel-advice-capture"

    service.handle("先不要给我计划建议了", conversation_id)

    state = runtime.interaction_coordinator.current(conversation_id)
    assert not state.last_suggestion_snapshot
    assert growth.tasks() == []


@pytest.mark.parametrize(
    ("advice_request", "collection_reply"),
    [
        ("那你帮我建议几条计划吧", "都要"),
        ("给我推荐几项今天能做的计划", "全部都要"),
        ("帮我列几条今日计划建议", "这些都加入"),
    ],
)
def test_natural_plan_advice_from_idle_keeps_displayed_list_for_batch_add(
    tmp_path,
    advice_request,
    collection_reply,
):
    """Exercise the full user-visible chain without seeding pending state."""
    llm = NumberedPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = f"natural-advice-{collection_reply}"

    listed = service.handle(advice_request, conversation_id)

    assert listed.status == "chat"
    assert listed.message == (
        "1. 阅读一个课程章节\n"
        "2. 整理三条概念笔记\n"
        "3. 练习一道配套题目\n"
        "4. 复习一条核心规则"
    )
    assert growth.tasks() == []
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_offer"
    assert [item["title"] for item in state.last_suggestion_snapshot["objects"]] == [
        "阅读一个课程章节",
        "整理三条概念笔记",
        "练习一道配套题目",
        "复习一条核心规则",
    ]

    added = service.handle(collection_reply, conversation_id)

    assert added.status == "completed"
    assert len(added.tool_results) == 4
    assert all(item.tool == "add_plan" and item.success for item in added.tool_results)
    assert added.message.startswith("已加入 4 项今日计划：")
    assert added.message.count("已加入") == 1
    assert [item["title"] for item in growth.tasks()] == [
        "阅读一个课程章节",
        "整理三条概念笔记",
        "练习一道配套题目",
        "复习一条核心规则",
    ]
    snapshot = runtime.interaction_coordinator.current(
        conversation_id
    ).last_suggestion_snapshot
    assert all(item["consumed"] for item in snapshot["objects"])


def test_plan_suggestion_prompt_requires_program_compatible_numbered_candidates(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())

    messages = service.conversation_service.build_llm_messages(
        "那你帮我建议几条计划吧",
        "numbered-suggestion-contract",
    )

    system_text = "\n".join(
        str(item.get("content", ""))
        for item in messages
        if str(item.get("role", "")) == "system"
    )
    assert "必须先直接给出二至四项单层编号候选" in system_text
    assert "不要只用澄清问题作答" in system_text
    assert "用户信息已经足够时，不要强行追问" in system_text
    assert "不要复读固定模板" in system_text
    assert "不要把补充当成执行前置条件" in system_text
    assert "不代表已经加入今日计划" in system_text


def test_answer_to_assistant_suggestion_question_refines_without_writing(tmp_path):
    llm = RefinementPlanChatLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    runtime = service.conversation_service
    conversation_id = "suggestion-refinement"
    options = _seed_suggestions(runtime, conversation_id)
    runtime.interaction_coordinator.awaiting_choice(
        conversation_id,
        "assistant_plan_offer",
        options=options,
        listed_object_ids=[item["id"] for item in options],
        immutable_arguments={"tool_name": "add_plan"},
        known_fields={"suggestion_refinement_used": False},
        domain="plan",
        request_mode="execute",
        original_user_text="给我安排晚上十一点到十二点的计划",
    )

    turn = runtime.prepare("学习一点", conversation_id)
    in_flight = runtime.interaction_coordinator.current(conversation_id)

    assert turn.requires_llm
    assert in_flight.interaction_kind == "assistant_plan_refinement"
    assert in_flight.known_fields["suggestion_refinement_used"] is True

    response = runtime.complete(turn)

    assert response.status == "chat"
    assert response.tool_results == []
    assert growth.tasks() == []
    assert "给我安排晚上十一点到十二点的计划" in llm.refinement_prompt
    assert "用户补充：学习一点" in llm.refinement_prompt
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_selection"
    assert state.known_fields["suggestion_refinement_used"] is True
    assert "补充要求：学习一点" in state.original_user_text
    assert [item["title"] for item in state.suggested_options] == [
        "复习一个机器学习概念并写三句解释",
        "运行一个最小训练示例并观察一个参数",
        "整理一个机器学习疑问",
    ]


def test_leading_dismissal_then_explicit_add_uses_positive_correction(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "suggestion-positive-correction"
    options = _seed_suggestions(runtime, conversation_id)

    response = service.handle("不用了把你说的加入今日计划", conversation_id)

    assert response.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [
        item["title"] for item in options
    ]
    assert len(response.tool_results) == len(options)


def test_contextual_suggestion_help_keeps_snapshot_and_does_not_write(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    conversation_id = "suggestion-contextual-help"
    _seed_suggestions(runtime, conversation_id)

    response = service.handle("告诉我我应该怎么表达才行", conversation_id)

    assert response.tool_results == []
    assert growth.tasks() == []
    assert "把你说的全部加入今日计划" in response.message
    assert "把第二项加入今日计划" in response.message
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_selection"


def test_model_show_plan_misread_is_repaired_to_advice_before_snapshot_and_batch(tmp_path):
    llm = MisreadPlanSuggestionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    conversation_id = "repair-plan-suggestion-read"

    listed = service.handle("帮我列几条今日计划建议", conversation_id)

    assert listed.status == "chat"
    assert listed.tool_results == []
    assert growth.tasks() == []
    state = runtime.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "assistant_plan_offer"
    assert len(state.last_suggestion_snapshot["objects"]) == 4

    added = service.handle("这些都加入", conversation_id)

    assert added.status == "completed"
    assert len(added.tool_results) == 4
    assert all(item.tool == "add_plan" and item.success for item in added.tool_results)
    assert len(growth.tasks()) == 4


def test_remaining_suggestions_survive_first_add_and_do_not_readd_consumed_item(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "add_plan")
    runtime = service.conversation_service
    conversation_id = "remaining-suggestions"
    options = _seed_suggestions(runtime, conversation_id)
    first = service.handle("第一个", conversation_id)
    assert first.status == "completed"

    remaining = service.handle("剩下的都加入", conversation_id)

    assert remaining.status == "completed"
    assert [item["title"] for item in growth.tasks()] == [item["title"] for item in options]
    assert len(remaining.tool_results) == 2
    assert all(item["consumed"] for item in runtime.interaction_coordinator.current(conversation_id).last_suggestion_snapshot["objects"])


def test_all_suggestion_reference_without_same_conversation_evidence_does_not_write(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "add_plan")
    _seed_suggestions(service.conversation_service, "different-conversation")

    response = service.handle("全部都加入", "no-suggestion-evidence")

    assert response.status in {"clarification", "failed"}
    assert growth.tasks() == []
    assert not any(item.success and item.operation_kind == "write" for item in response.tool_results)


def test_collective_completion_without_recent_read_does_not_guess_global_set(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "complete_plan")
    growth.add_task("不要猜测这个未展示目标")

    response = service.handle("我都完成了", "without-read-evidence")

    assert response.status in {"clarification", "failed"}
    assert all(not item["done"] for item in growth.tasks())
    assert growth.records_for_date() == []


def test_counted_demonstrative_matching_all_pending_plans_uses_one_confirmation(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first = growth.add_task("真实桌面验收甲")
    second = growth.add_task("真实桌面验收乙")
    conversation_id = "counted-live-pending-set"

    preview = service.handle("这两项我都完成了", conversation_id)

    assert preview.status == "clarification"
    assert "真实桌面验收甲" in preview.message
    assert "真实桌面验收乙" in preview.message
    assert all(not item["done"] for item in growth.tasks())
    state = service.conversation_service.interaction_coordinator.current(
        conversation_id
    )
    assert state.interaction_kind == "plan_batch_completion"
    assert state.selected_object_ids == [first["uid"], second["uid"]]

    completed = service.handle("确认", conversation_id)

    assert completed.status == "completed"
    assert [item.tool for item in completed.tool_results] == [
        "complete_plan",
        "complete_plan",
    ]
    assert all(item["done"] for item in growth.tasks())


def test_counted_demonstrative_does_not_guess_subset_when_more_plans_exist(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    for title in ("计划甲", "计划乙", "计划丙"):
        growth.add_task(title)

    response = service.handle("这两项我都完成了", "counted-subset-ambiguous")

    assert response.status in {"clarification", "failed"}
    assert response.tool_results == []
    assert all(not item["done"] for item in growth.tasks())


@pytest.mark.parametrize("question", ["全部都加入？", "全部?"])
def test_suggestion_collection_question_is_not_an_executable_short_answer(tmp_path, question):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    _seed_suggestions(runtime, "collection-question")
    response = service.handle(question, "collection-question")
    assert growth.tasks() == []
    assert not any(item.success and item.tool == "add_plan" for item in response.tool_results)
    assert not any(item["consumed"] for item in runtime.interaction_coordinator.current("collection-question").last_suggestion_snapshot["objects"])


@pytest.mark.parametrize("reply", ["全部都不加入", "不要全部加入"])
def test_negated_suggestion_collection_never_executes(tmp_path, reply):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    _seed_suggestions(runtime, "collection-negation")

    response = service.handle(reply, "collection-negation")

    assert growth.tasks() == []
    assert not any(item.success and item.tool == "add_plan" for item in response.tool_results)
    assert not any(
        item["consumed"]
        for item in runtime.interaction_coordinator.current(
            "collection-negation"
        ).last_suggestion_snapshot["objects"]
    )


@pytest.mark.parametrize("user_text", ["全部两条加入", "全部四条加入"])
def test_suggestion_collection_wrong_count_exits_without_pending_or_consumption(tmp_path, user_text):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    _seed_suggestions(runtime, "count-mismatch")
    response = service.handle(user_text, "count-mismatch")
    assert response.status == "failed"
    assert growth.tasks() == []
    state = runtime.interaction_coordinator.current("count-mismatch")
    assert not state.pending
    assert not any(item["consumed"] for item in state.last_suggestion_snapshot["objects"])


def test_expired_suggestion_collection_does_not_create_a_placeholder_plan(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "add_plan")
    runtime = service.conversation_service
    options = _seed_suggestions(runtime, "expired-collection", pending=False)
    runtime.interaction_coordinator.remember_suggestion_snapshot(
        "expired-collection", SuggestionSnapshot.for_options(
            conversation_id="expired-collection", options=options,
            captured_at=datetime.now() - timedelta(hours=1),
        ),
    )
    response = service.handle("全部都加入", "expired-collection")
    assert response.status == "failed"
    assert growth.tasks() == []
    assert not runtime.interaction_coordinator.current("expired-collection").pending


def test_batch_consumption_requires_one_success_for_each_same_title_option(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    options = [{"id": "first", "title": "阅读练习"}, {"id": "second", "title": "阅读练习"}]
    snapshot = SuggestionSnapshot.for_options(conversation_id="same-titles", options=options)
    runtime.interaction_coordinator.remember_suggestion_snapshot("same-titles", snapshot)
    actual = growth.add_task("阅读练习")
    response = AgentResponse("partial_success", "", tool_results=[
        ToolResult(True, "add_plan", "added", data={"task": actual}),
        ToolResult(False, "add_plan", "failed"),
    ])
    runtime._consume_suggestion_selection(response, "same-titles", {
        "suggestion_snapshot_selections": [
            {"snapshot_id": snapshot.snapshot_id, "object_id": item["id"], "title": item["title"]}
            for item in options
        ],
    })
    state = runtime.interaction_coordinator.current("same-titles")
    assert [item["consumed"] for item in state.last_suggestion_snapshot["objects"]] == [True, False]


def test_expired_pending_collection_is_terminal(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    options = _seed_suggestions(runtime, "terminal-collection")
    snapshot = SuggestionSnapshot.for_options(
        conversation_id="terminal-collection", options=options,
        captured_at=datetime.now() - timedelta(hours=1),
    )
    runtime.interaction_coordinator.remember_suggestion_snapshot("terminal-collection", snapshot)
    response = service.handle("全部都加入", "terminal-collection")
    assert response.status == "failed"
    assert growth.tasks() == []
    state = runtime.interaction_coordinator.current("terminal-collection")
    assert not state.pending
    assert not any(item["consumed"] for item in state.last_suggestion_snapshot["objects"])


def test_yesterday_collection_does_not_complete_today(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "complete_plan")
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    growth.add_task("昨天的核对事项", date=yesterday)
    growth.add_task("今天的核对事项")
    service.handle("查看昨天计划", "yesterday-collection")
    response = service.handle("我都完成了", "yesterday-collection")
    assert response.status == "failed"
    assert all(not item["done"] for item in growth.tasks())
    assert all(not item["done"] for item in growth.tasks(yesterday))
    assert not service.conversation_service.interaction_coordinator.current("yesterday-collection").pending


def test_repeated_completed_collection_is_truthful_noop(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "complete_plan")
    targets = [growth.add_task(title) for title in ("核对第一项", "核对第二项")]
    for target in targets:
        growth.complete_by_id(target["id"])
    service.handle("查看今天计划", "done-collection")
    response = service.handle("我都完成了", "done-collection")
    assert response.status == "completed"
    assert "已经全部完成" in response.message
    assert response.tool_results == []
    assert growth.records_for_date() == []
    assert not service.conversation_service.interaction_coordinator.current("done-collection").pending


def test_deleted_member_stops_whole_displayed_completion_collection(tmp_path):
    service, growth, _memory, _history = _production_service(tmp_path, "complete_plan")
    deleted = growth.add_task("原集合第一项")
    remaining = growth.add_task("原集合第二项")
    service.handle("查看今天计划", "deleted-collection")
    growth.delete_by_id(deleted["id"])
    response = service.handle("我都完成了", "deleted-collection")
    assert response.status == "failed"
    assert response.tool_results == []
    assert growth.tasks()[0]["uid"] == remaining["uid"]
    assert not growth.tasks()[0]["done"]
    assert not service.conversation_service.interaction_coordinator.current("deleted-collection").pending
