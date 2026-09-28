import json
from datetime import datetime

import pytest

from modules.contracts import AgentResponse
from modules.intent_router import LLMIntentParser
from modules.memory_data_query_guard import MemoryDataQueryGuard
from modules.response_composer import ResponseComposer
from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from modules.suggestion_snapshot import SuggestionSnapshot
from v18_test_support import NoCallLLM, build_service


@pytest.mark.parametrize(
    "message",
    [
        "再加一条：做操作系统习题",
        "特征工程，一个小时",
        "添加计划：读一本小说",
        "今天安排两个小时阅读论文",
    ],
)
def test_add_plan_quantities_do_not_bind_suggestion_ordinals(tmp_path, message):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    runtime = service.conversation_service
    runtime.interaction_coordinator.remember_suggestion_snapshot(
        "quantity-reference",
        SuggestionSnapshot.for_options(
            conversation_id="quantity-reference",
            options=[{"id": "1", "title": "整理书桌"}],
        ),
    )
    arguments = {"title": "阅读论文", "duration_minutes": 60}
    semantic = SemanticParseResult(
        source="test_model",
        candidates=[ActionCandidate("plan", "add_plan", dict(arguments))],
        intent_result={"intent": "add_plan", "entities": dict(arguments)},
    )

    runtime._bind_suggestion_snapshot_reference(
        semantic,
        semantic.intent_result,
        conversation_id="quantity-reference",
        current_message=message,
    )

    assert len(semantic.candidates) == 1
    assert semantic.candidates[0].arguments == arguments
    assert semantic.needs_clarification is False
    assert growth.tasks() == []


@pytest.mark.parametrize(
    "message",
    [
        "不要告诉我关于我的记忆，我叫什么不是现在的问题",
        "我不是让你告诉我叫什么",
        "别告诉我喜欢吃什么",
        "告诉我你能干什么，不是问我的名字是什么",
    ],
)
def test_typed_memory_read_respects_refusals_and_capability_purpose(message):
    assert MemoryDataQueryGuard().route(message) is None


def test_latest_formal_name_wins_when_timestamps_are_equal(tmp_path):
    _service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.now_provider = lambda: datetime(2026, 9, 16, 12, 0, 0)
    memory.add_memory("我的名字是晨星", allow_conflict=True, allow_similar=True)
    memory.add_memory("以后叫我星海", allow_conflict=True, allow_similar=True)

    assert memory.preferred_name() == "星海"


@pytest.mark.parametrize(
    "attribute,topic,content,category,scope",
    [
        ("habit", "", "我通常早上阅读", "habit", "stable_identity"),
        ("goal", "", "我的长期目标是提高英语", "goal", "future_intent"),
        ("project", "work", "RoxyPlan 是我的长期开发项目", "project", "stable_identity"),
        ("current_state", "location", "我目前住在杭州", "other", "current_state"),
    ],
)
def test_typed_attributes_return_only_verified_matching_facts(
    tmp_path, attribute, topic, content, category, scope
):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    saved = memory.add_memory(content, category=category, scope=scope)["memory"]
    unrelated = memory.add_memory("我喜欢吃西瓜", category="preference")["memory"]

    result = service.memory_service.read_typed_memory(
        query_mode="attribute", attribute=attribute, topic=topic
    )

    assert result.success is True
    assert [item["memory_id"] for item in result.data["memory_read"]["facts"]] == [saved["id"]]
    assert memory.get(unrelated["id"])["use_count"] == 0
    assert memory.get(saved["id"])["use_count"] == 0


def test_existence_read_does_not_match_only_shared_question_framing(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    saved = memory.add_memory("我喜欢吃西瓜", category="preference")["memory"]

    absent = service.memory_service.read_typed_memory(
        query_mode="existence", attribute="preference", query="你记得我喜欢吃苹果吗"
    )
    present = service.memory_service.read_typed_memory(
        query_mode="existence", attribute="preference", query="你记得我喜欢吃西瓜吗"
    )

    assert absent.success is True and absent.data["memory_read"]["facts"] == []
    assert [item["memory_id"] for item in present.data["memory_read"]["facts"]] == [saved["id"]]


def test_provenance_distinguishes_formal_store_from_fact_origin(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    memory.add_memory("我喜欢吃西瓜", category="preference", source="explicit_user")
    registry = service.conversation_service.agent_core.executor.registry
    result = registry.get("list_memories").handler(
        query_mode="provenance", attribute="preference", query="西瓜"
    )

    response = ResponseComposer().compose(
        AgentResponse("completed", "", tool_results=[result]), user_text="这项记忆从哪里来的"
    )

    fact = result.data["memory_read"]["facts"][0]
    assert fact["source"] == "explicit_user"
    assert "正式长期记忆" in response.message
    assert "兼容用户资料" not in response.message


def test_semantic_prompt_parameter_docs_include_read_types_and_enums(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    registry = service.conversation_service.agent_core.executor.registry

    docs = registry.model_visible_parameter_docs()

    assert "list_memories:" in docs
    assert "query_mode(optional,string" in docs
    assert "overview" in docs and "provenance" in docs
    assert "preferred_name" in docs
    assert "delete_all_memories:" not in docs
    registry.get("list_memories").enabled = False
    assert "list_memories:" not in registry.model_visible_parameter_docs()


def test_local_suggestion_selection_keeps_origin_across_duration_clarification(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path)
    conversation_id = "suggestion-duration"
    runtime = service.conversation_service
    options = runtime._default_learning_options()
    # Seed a typed timed-operation choice without spending the two-round
    # clarification budget. Ordinary add_plan still requires only a title.
    runtime.interaction_coordinator.start(
        conversation_id,
        "advice_choice",
        "awaiting_choice",
        suggested_options=options,
        domain="plan",
        missing_fields=["duration_minutes"],
        known_fields={"time_slot": "下午"},
        immutable_arguments={"tool_name": "add_plan", "time_slot": "下午"},
    )
    runtime.interaction_coordinator.remember_suggestion_snapshot(
        conversation_id,
        SuggestionSnapshot.for_options(conversation_id=conversation_id, options=options),
    )

    selected = service.handle("第二个", conversation_id)
    assert selected.status == "clarification"
    assert growth.tasks() == []

    added = service.handle("一个小时", conversation_id)

    assert added.status == "completed"
    task = growth.tasks()[0]
    assert task["duration_minutes"] == 60
    assert task["time_slot"] == "下午"
    assert "主程序" in task["title"]
    snapshot = service.conversation_service.interaction_coordinator.current(
        conversation_id
    ).last_suggestion_snapshot
    assert [item["consumed"] for item in snapshot["objects"]] == [False, True, False]
    assert "suggestion_snapshot_selection" not in task


@pytest.mark.parametrize("title", ["读一个章节", "阅读第二个小节"])
def test_completion_explicit_title_is_not_rebound_to_read_snapshot_ordinal(tmp_path, title):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("复习英语")
    growth.add_task("整理代码")
    target = growth.add_task(title)
    conversation_id = "completion-title-quantity"
    shown = service.handle("今日计划", conversation_id)
    assert shown.status == "completed"

    response = service.handle("完成计划：" + title, conversation_id)

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["complete_plan"]
    tasks = growth.tasks()
    assert [item["done"] for item in tasks] == [False, False, True]
    assert tasks[2]["uid"] == target["uid"]


def test_grounded_read_snapshot_is_not_resolved_again_by_current_list_order(tmp_path):
    class OrdinalCompletionLLM:
        def chat(self, _messages):
            return json.dumps({
                "mode": "write", "intent": "complete_plan",
                "entities": {"task_ref": "wrong_model_target"},
                "proposed_tool": "complete_plan", "confidence": 0.99,
                "follow_up_target": None, "needs_confirmation": False,
                "warnings": [], "clarification_question": None,
                "candidate_actions": [], "subject": "self",
                "polarity": "positive", "modality": "commitment",
                "request_mode": "execute", "explicit_command": True,
            }, ensure_ascii=False)

    llm = OrdinalCompletionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    first = growth.add_task("复习英语")
    second = growth.add_task("整理代码")
    conversation_id = "grounded-snapshot-order"
    service.handle("今日计划", conversation_id)
    growth.delete_by_id(first["id"])
    added_later = growth.add_task("阅读论文")

    response = service.handle("第二个也搞定了", conversation_id)

    assert response.status == "completed"
    tasks = {item["uid"]: item for item in growth.tasks()}
    assert tasks[second["uid"]]["done"] is True
    assert tasks[added_later["uid"]]["done"] is False


@pytest.mark.parametrize("target_state", ["completed", "deleted"])
def test_invalid_snapshot_target_cannot_fall_back_to_another_live_ordinal(tmp_path, target_state):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("复习英语")
    growth.add_task("整理代码")
    target = growth.add_task("阅读第二个小节")
    conversation_id = "invalid-quantity-target"
    service.handle("今日计划", conversation_id)
    if target_state == "completed":
        growth.complete_by_id(target["id"])
    else:
        growth.delete_by_id(target["id"])

    response = service.handle("完成计划：阅读第二个小节", conversation_id)

    assert not any(item.tool == "complete_plan" and item.success for item in response.tool_results)
    assert [item["done"] for item in growth.tasks()[:2]] == [False, False]
    assert growth.records_for_date() == []
