from __future__ import annotations

import json

import pytest

from modules.intent_router import LLMIntentParser
from modules.semantic_action_parser import ActionCandidate
from v18_test_support import build_service


class DeleteWriteLLM:
    """Return one model semantic decision; deterministic writes never call it."""

    def __init__(self) -> None:
        self.semantic_calls = 0

    def chat(self, messages):
        first = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" not in first:
            return "好，我不会删除这条计划。"
        self.semantic_calls += 1
        return json.dumps(
            {
                "mode": "write",
                "intent": "delete_plan",
                "entities": {"task_ref": "last_task"},
                "proposed_tool": "delete_plan",
                "confidence": 0.98,
                "follow_up_target": "last_task",
                "needs_confirmation": True,
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


def _production_service(tmp_path, llm):
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


@pytest.mark.parametrize("confirmation", ["确认", "确认‘", "“确认”"])
def test_model_delete_write_binds_last_plan_then_confirms_once(
    tmp_path,
    confirmation,
):
    llm = DeleteWriteLLM()
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-delete-write-hidden-bridge"

    added = service.handle("把简历包装加入今天计划", conversation_id)
    preview = service.handle("删除这个计划", conversation_id)
    pending = service.confirmation_manager.pending(scope=conversation_id)

    assert [item.tool for item in added.tool_results] == ["add_plan"]
    assert preview.status == "clarification"
    assert preview.tool_results == []
    assert "简历包装" in preview.message
    assert "确认" in preview.message
    assert "功能还不完善" not in preview.message
    assert "task_ref" not in preview.message
    assert pending is not None
    assert pending["tool"] == "delete_plan"
    assert len(growth.tasks()) == 1

    confirmed = service.handle(confirmation, conversation_id)

    assert confirmed.status == "completed"
    assert [item.tool for item in confirmed.tool_results] == ["delete_plan"]
    assert confirmed.tool_results[0].success is True
    assert growth.tasks() == []
    assert llm.semantic_calls == 1


def test_bulk_plan_delete_stays_zero_write_and_gives_a_real_single_item_path(tmp_path):
    llm = DeleteWriteLLM()
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-delete-all-guidance"
    growth.add_task("核对课程笔记")
    growth.add_task("整理练习结果")
    service.handle("查看今天计划", conversation_id)

    response = service.handle("删除所有计划", conversation_id)

    assert response.status == "failed"
    assert response.tool_results == []
    assert "一次只支持删除一条" in response.message
    assert "查看今天计划" in response.message
    assert "删除第2条" in response.message
    assert service.confirmation_manager.pending(scope=conversation_id) is None
    assert [item["title"] for item in growth.tasks()] == [
        "核对课程笔记",
        "整理练习结果",
    ]


def test_model_delete_proposal_cannot_override_current_negation(tmp_path):
    llm = DeleteWriteLLM()
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-delete-current-negation-wins"

    added = service.handle("把简历包装加入今天计划", conversation_id)
    response = service.handle("不要删除这个计划", conversation_id)

    assert [item.tool for item in added.tool_results] == ["add_plan"]
    assert response.tool_results == []
    assert service.confirmation_manager.pending(scope=conversation_id) is None
    assert service.interaction_coordinator.current(conversation_id).pending is False
    assert [item["title"] for item in growth.tasks()] == ["简历包装"]


def test_model_delete_ordinal_binds_the_recent_displayed_plan_before_confirmation(tmp_path):
    llm = DeleteWriteLLM()
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-delete-read-snapshot-ordinal"
    growth.add_task("核对项目目录")
    target = growth.add_task("修改简历描述")
    service.handle("查看今天计划", conversation_id)

    preview = service.handle("第二条不对，删除", conversation_id)

    assert preview.status == "clarification"
    assert preview.tool_results == []
    assert "修改简历描述" in preview.message
    pending = service.confirmation_manager.pending(scope=conversation_id)
    assert pending is not None
    assert pending["tool"] == "delete_plan"
    assert pending["arguments"]["task_ref"] == target["uid"]
    assert [item["title"] for item in growth.tasks()] == ["核对项目目录", "修改简历描述"]

    confirmed = service.handle("确认", conversation_id)

    assert confirmed.status == "completed"
    assert [item.tool for item in confirmed.tool_results] == ["delete_plan"]
    assert [item["title"] for item in growth.tasks()] == ["核对项目目录"]


def test_model_delete_out_of_range_ordinal_never_falls_back_to_an_unverified_target(tmp_path):
    llm = DeleteWriteLLM()
    service, growth, _memory, _history = _production_service(tmp_path, llm)
    conversation_id = "v22-delete-read-snapshot-invalid-ordinal"
    growth.add_task("唯一计划")
    service.handle("查看今天计划", conversation_id)

    response = service.handle("删除第二条", conversation_id)

    assert response.status in {"clarification", "failed"}
    assert response.tool_results == []
    assert service.confirmation_manager.pending(scope=conversation_id) is None
    assert [item["title"] for item in growth.tasks()] == ["唯一计划"]


def test_action_log_persists_content_without_command_punctuation_or_scope_note(
    tmp_path,
):
    service, growth, _memory, _history = build_service(tmp_path)

    response = service.handle(
        "记录一下：我今天学会了一道菜，这件事不在计划里",
        "v22-action-log-clean-content",
    )
    repeated = service.handle(
        "记录一下：我今天学会了一道菜",
        "v22-action-log-clean-content",
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["add_action_log"]
    assert repeated.status == "completed"
    assert [item["content"] for item in growth.records_for_date()] == [
        "我今天学会了一道菜"
    ]


def test_action_log_business_boundary_cleans_model_supplied_content(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path)
    candidate = ActionCandidate(
        "action_log",
        "add_action_log",
        {"content": "：我今天完成了阅读，不过这件事不属于今天的任务中。"},
        request_mode="execute",
        explicit_command=True,
    )

    resolution = service.business_resolver.resolve(
        candidate,
        conversation_id="v22-action-model-content-clean",
    )

    assert resolution.status == "resolved"
    assert resolution.resolved_action is not None
    assert resolution.resolved_action.arguments == {"content": "我今天完成了阅读"}
