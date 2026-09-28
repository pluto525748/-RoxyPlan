from __future__ import annotations

import json

from modules.intent_router import LLMIntentParser
from v18_test_support import NoCallLLM, build_service


class SemanticDecisionLLM:
    def __init__(self, decision: dict) -> None:
        self.decision = dict(decision)
        self.semantic_calls = 0

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            self.semantic_calls += 1
            return json.dumps(self.decision, ensure_ascii=False)
        return "不应进入普通聊天回复。"


def _decision(
    intent: str,
    *,
    entities: dict | None = None,
    proposed_tool: str | None = None,
    mode: str = "write",
    request_mode: str = "execute",
    explicit_command: bool = True,
    clarification_question: str | None = None,
    candidate_actions: list[dict] | None = None,
    modality: str = "commitment",
) -> dict:
    return {
        "mode": mode,
        "intent": intent,
        "entities": dict(entities or {}),
        "proposed_tool": proposed_tool,
        "confidence": 0.9,
        "follow_up_target": None,
        "needs_confirmation": bool(clarification_question),
        "warnings": [],
        "clarification_question": clarification_question,
        "candidate_actions": list(candidate_actions or []),
        "subject": "self",
        "polarity": "positive",
        "modality": modality,
        "request_mode": request_mode,
        "explicit_command": explicit_command,
    }


def _production_service(tmp_path, decision: dict):
    llm = SemanticDecisionLLM(decision)
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history, llm


def test_semantic_completion_ordinal_uses_recent_snapshot_for_new_synonym(tmp_path):
    service, growth, _memory, _history, _llm = _production_service(
        tmp_path,
        _decision(
            "complete_plan",
            proposed_tool="complete_plan",
            mode="clarify",
            clarification_question="请告诉我具体完成了哪项计划。",
        ),
    )
    first = growth.add_task("核对依赖版本")
    second = growth.add_task("校对封版说明")
    conversation_id = "v22-latest-snapshot-synonym"
    service.handle("查看今天计划", conversation_id)

    response = service.handle("第二个也搞定了", conversation_id)
    by_uid = {item["uid"]: item for item in growth.tasks()}

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["complete_plan"]
    assert by_uid[first["uid"]]["done"] is False
    assert by_uid[second["uid"]]["done"] is True


def test_pending_status_modifier_does_not_negate_snapshot_batch_completion(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first = growth.add_task("检查依赖版本")
    second = growth.add_task("校对封版说明")
    conversation_id = "v22-latest-pending-snapshot-batch"
    service.handle("查看今天计划", conversation_id)

    preview = service.handle("刚才展示的未完成计划我都完成了", conversation_id)

    assert preview.status == "clarification"
    assert "检查依赖版本" in preview.message
    assert "校对封版说明" in preview.message
    assert all(not item["done"] for item in growth.tasks())

    completed = service.handle("确认", conversation_id)
    by_uid = {item["uid"]: item for item in growth.tasks()}
    assert completed.status == "completed"
    assert by_uid[first["uid"]]["done"] is True
    assert by_uid[second["uid"]]["done"] is True
    assert completed.message.count("对应计划已标记完成") <= 1


def test_add_plan_command_shell_asks_only_for_missing_title(tmp_path):
    service, growth, _memory, _history, _llm = _production_service(
        tmp_path,
        _decision(
            "add_plan",
            entities={"title": "加入今日计划。\n-"},
            proposed_tool="add_plan",
            mode="clarify",
            request_mode="possible_action",
            explicit_command=True,
            clarification_question="你想加入什么计划？",
        ),
    )

    response = service.handle("加入今日计划。\n-", "v22-latest-empty-plan-title")
    state = service.interaction_coordinator.current("v22-latest-empty-plan-title")

    assert response.status == "clarification"
    assert response.message == "想加入什么具体事项？"
    assert "现在开始" not in response.message
    assert state.interaction_kind == "missing_slots"
    assert state.missing_fields == ["title"]
    assert state.immutable_arguments == {"tool_name": "add_plan"}
    assert growth.tasks() == []


def test_structured_commitment_promotes_follow_on_plan_add_to_execution(tmp_path):
    title = "V22验收-0914-核对发布清单"
    service, growth, _memory, _history, _llm = _production_service(
        tmp_path,
        _decision(
            "add_plan",
            entities={"title": title},
            proposed_tool="add_plan",
            request_mode="possible_action",
            explicit_command=False,
            modality="commitment",
        ),
    )

    response = service.handle(
        f"另外，今天还要做“{title}”。",
        "v22-latest-follow-on-commitment",
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == [title]


def test_hidden_complex_plan_change_degrades_before_pending_state(tmp_path):
    service, growth, _memory, _history, _llm = _production_service(
        tmp_path,
        _decision(
            "multi_action",
            mode="clarify",
            request_mode="possible_action",
            explicit_command=True,
            clarification_question="你具体想修改和合并哪两项计划？",
            candidate_actions=[
                {
                    "intent": "update_plan",
                    "entities": {
                        "task_ref": "整理发布资料",
                        "changes": {"duration_minutes": 60},
                    },
                },
                {
                    "intent": "merge_plan",
                    "entities": {
                        "target_ref": "整理发布资料",
                        "duplicate_refs": ["校对封版说明"],
                    },
                },
            ],
        ),
    )
    task = growth.add_task("整理发布资料", duration_minutes=20)
    conversation_id = "v22-latest-degraded-complex-plan-change"

    response = service.handle(
        "把整理发布资料改成一小时，再和另一个计划合并后挪到下周。",
        conversation_id,
    )
    saved = next(item for item in growth.tasks() if item["uid"] == task["uid"])
    state = service.interaction_coordinator.current(conversation_id)

    assert response.status == "failed"
    assert response.message.startswith(
        "抱歉，我当前这个功能还不完善，这次没有完成。你可以这样说："
    )
    assert response.tool_results == []
    assert saved["duration_minutes"] == 20
    assert state.pending is False


def test_model_proposed_hidden_duplicate_inspection_degrades_without_state(
    tmp_path,
):
    service, growth, _memory, _history, _llm = _production_service(
        tmp_path,
        _decision(
            "inspect_plan_duplicates",
            proposed_tool="inspect_plan_duplicates",
            mode="read",
            request_mode="query",
            explicit_command=False,
            modality="question",
        ),
    )
    task = growth.add_task("核对发布材料")
    conversation_id = "v22-hidden-duplicate-inspection"

    response = service.handle(
        "帮我核验它们有没有语义重叠",
        conversation_id,
    )
    state = service.interaction_coordinator.current(conversation_id)

    assert response.status == "failed"
    assert response.message.startswith(
        "抱歉，我当前这个功能还不完善，这次没有完成。你可以这样说："
    )
    assert response.tool_results == []
    assert [item["uid"] for item in growth.tasks()] == [task["uid"]]
    assert state.pending is False
