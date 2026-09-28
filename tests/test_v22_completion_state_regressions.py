"""Current completion states must not be reclassified as new actions."""

import pytest

from modules.semantic_action_parser import ActionCandidate
from modules.plan_service import PlanService
from v18_test_support import NoCallLLM, build_service


def completion(reference, *, snapshot_bound=False):
    return ActionCandidate(
        domain="plan",
        tool_name="complete_plan",
        arguments={"match_text": reference},
        confidence=1.0,
        explicit_command=True,
        source_intent="read_snapshot_binding" if snapshot_bound else "complete_plan",
        clause_text="1.已经完成了" if snapshot_bound else "完成计划：" + reference,
    )


def store_bytes(growth):
    return {
        path: path.read_bytes() if path.exists() else None
        for path in (growth.plan_file, growth.action_file, growth.growth_file)
    }


@pytest.mark.parametrize("reference_kind", ["uid", "id", "title"])
def test_plan_lookup_distinguishes_completed_target_from_missing(tmp_path, reference_kind):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    done = growth.add_task("复习英语")
    growth.complete_by_id(done["id"])
    growth.add_task("整理代码")
    before = store_bytes(growth)

    found = service.plan_service.resolve(str(done[reference_kind]), pending_only=True)

    assert found.status == "already_completed"
    assert found.reason_code == "plan_already_completed"
    assert found.task["uid"] == done["uid"]
    assert store_bytes(growth) == before


@pytest.mark.parametrize("snapshot_bound", [False, True])
def test_resolver_completed_target_has_no_action_offer_or_write(tmp_path, snapshot_bound):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    done = growth.add_task("复习英语")
    growth.complete_by_id(done["id"])
    growth.add_task("整理代码")
    before = store_bytes(growth)

    result = service.business_resolver.resolve(
        completion(done["uid"], snapshot_bound=snapshot_bound), conversation_id="repeat-done"
    )

    assert result.status == "forbidden"
    assert result.reason_code == "plan_already_completed"
    assert result.resolved_action is None
    assert result.candidate_objects[0]["uid"] == done["uid"]
    assert "已经完成" in result.safe_prompt
    assert "行动" not in result.safe_prompt
    assert store_bytes(growth) == before


def test_deleted_snapshot_uid_cannot_rebind_reused_numeric_id(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    deleted = growth.add_task("复习英语")
    growth.delete_by_id(deleted["id"])
    replacement = growth.add_task("复习英语")
    assert replacement["id"] == deleted["id"]
    assert replacement["uid"] != deleted["uid"]
    before = store_bytes(growth)

    result = service.business_resolver.resolve(
        completion(deleted["uid"], snapshot_bound=True), conversation_id="deleted-snapshot"
    )

    assert result.status == "forbidden"
    assert result.reason_code == "target_deleted"
    assert result.resolved_action is None
    assert "行动" not in result.safe_prompt
    assert store_bytes(growth) == before
    assert growth.tasks()[0]["done"] is False


def test_genuine_unplanned_completion_keeps_action_record_offer(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("整理代码")
    before = store_bytes(growth)

    result = service.business_resolver.resolve(
        completion("学习烘焙"), conversation_id="unplanned-progress"
    )

    assert result.status == "not_found"
    assert result.reason_code == "plan_not_found"
    assert result.resolved_action is None
    assert "行动" in result.safe_prompt
    assert store_bytes(growth) == before


def test_unplanned_completion_can_be_recorded_only_after_confirmation(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    target = growth.add_task("整理代码")
    conversation_id = "unplanned-confirmation"

    offer = service.handle("完成计划：学习烘焙", conversation_id)

    assert offer.status == "clarification"
    assert "行动" in offer.message
    assert offer.tool_results == []
    assert growth.records_for_date() == []
    state = service.conversation_service.interaction_coordinator.current(conversation_id)
    assert state.interaction_kind == "action_log_offer"

    recorded = service.handle("确认", conversation_id)

    assert recorded.status == "completed"
    assert [result.tool for result in recorded.tool_results] == ["add_action_log"]
    assert len(growth.records_for_date()) == 1
    assert "学习烘焙" in growth.records_for_date()[0]["content"]
    assert growth.tasks()[0]["uid"] == target["uid"]
    assert growth.tasks()[0]["done"] is False


def test_pending_plan_still_resolves_verified_real_target(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    target = growth.add_task("学习烘焙")

    result = service.business_resolver.resolve(
        completion(target["uid"], snapshot_bound=True), conversation_id="pending-target"
    )

    assert result.status == "resolved"
    assert result.resolved_action.arguments == {"match_text": target["uid"]}
    assert growth.tasks()[0]["done"] is False


def test_snapshot_uid_stays_stable_through_resolve_and_executor(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    target = growth.add_task("整理代码")
    result = service.business_resolver.resolve(
        completion(target["uid"], snapshot_bound=True), conversation_id="deleted-after-resolve"
    )
    growth.delete_by_id(target["id"])
    replacement = growth.add_task("整理代码")
    assert replacement["id"] == target["id"]
    before = store_bytes(growth)

    registry = service.conversation_service.agent_core.executor.registry
    tool_result = registry.get("complete_plan").handler(**result.resolved_action.arguments)

    assert tool_result.success is False
    assert growth.tasks()[0]["done"] is False
    assert store_bytes(growth) == before


def test_pending_only_lookup_rejects_other_ineligible_statuses():
    class IneligiblePlanOwner:
        def tasks(self, _date=None):
            return [{"id": 1, "uid": "task_inactive", "title": "取消的学习", "done": False,
                     "status": "cancelled"}]

    found = PlanService(IneligiblePlanOwner()).resolve("task_inactive", pending_only=True)

    assert found.status == "unavailable"
    assert found.reason_code == "plan_unavailable"


def test_exact_completed_title_does_not_select_similar_pending_title(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    done = growth.add_task("英语阅读")
    growth.complete_by_id(done["id"])
    pending = growth.add_task("英语阅读练习")

    result = service.business_resolver.resolve(
        completion("英语阅读"), conversation_id="completed-title"
    )

    assert result.reason_code == "plan_already_completed"
    assert result.resolved_action is None
    assert {item["uid"]: item["done"] for item in growth.tasks()} == {
        done["uid"]: True, pending["uid"]: False
    }


@pytest.mark.parametrize("target_state", ["completed_when_shown", "completed_later", "deleted"])
def test_repeat_completion_snapshot_closes_without_new_pending_or_writes(tmp_path, target_state):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    target = growth.add_task("复习英语")
    other = growth.add_task("整理代码")
    if target_state == "completed_when_shown":
        growth.complete_by_id(target["id"])
    conversation_id = "completion-state-" + target_state
    shown = service.handle("今日计划", conversation_id)
    assert shown.status == "completed"
    if target_state == "completed_later":
        growth.complete_by_id(target["id"])
    elif target_state == "deleted":
        growth.delete_by_id(target["id"])
    before = store_bytes(growth)

    reply = service.handle("1.已经完成了", conversation_id)

    assert "行动" not in reply.message
    assert reply.tool_results == []
    assert reply.status == ("failed" if target_state == "deleted" else "completed")
    if target_state != "deleted":
        assert "已经完成" in reply.message
    state = service.conversation_service.interaction_coordinator.current(conversation_id)
    assert not state.pending
    assert store_bytes(growth) == before
    assert next(item for item in growth.tasks() if item["uid"] == other["uid"])["done"] is False
    assert growth.records_for_date() == []
