"""Explicit today-all completion must read/show, confirm, then use original tools.

For the first authorized red run, ROXYPLAN_COMPLETION_BASELINE=1 loads only
the preserved pre-fix ConversationService into this isolated service fixture.
No worktree rollback, provider or Qt is involved.
"""

from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

import server.agent_service as agent_service
from modules.intent_router import LLMIntentParser
from tests.semantic_contract_fixtures import build_current_semantic_payload
from v18_test_support import build_service


class OfflineDecision:
    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" not in prompt:
            return "这是隔离离线回复，不执行操作。"
        return json.dumps(build_current_semantic_payload({
            "mode": "write", "intent": "complete_plan",
            "entities": {"task_ref": "今天所有未完成计划"},
            "proposed_tool": "complete_plan", "confidence": 0.99,
            "follow_up_target": None, "needs_confirmation": True,
            "candidate_actions": [], "warnings": [], "clarification_question": None,
            "subject": "self", "polarity": "positive", "modality": "commitment",
            "request_mode": "execute", "explicit_command": True,
        }), ensure_ascii=False)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    if os.environ.get("ROXYPLAN_COMPLETION_BASELINE") == "1":
        path = Path(__file__).resolve().parents[1] / "test_workspace/runs/today-all-completion-20261014/artifacts/before/conversation_service.py"
        spec = importlib.util.spec_from_file_location("completion_before_fix", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        monkeypatch.setattr(agent_service, "ConversationService", module.ConversationService)
    llm = OfflineDecision()
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime = service.conversation_service
    runtime.semantic_decision_compatibility_enabled = False
    return service, runtime, growth, tmp_path


def business_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in (root / "data/private").glob("*.json")
            if p.name in {"today_plan.json", "action_log.json", "growth_log.json"}}


def seed(growth):
    return [growth.add_task(title) for title in ("阅读一本教材", "复习一组算法", "整理一页笔记")]


@pytest.mark.parametrize("user_text", [
    "把今天所有未完成计划标记完成。",
    "请把今日全部未完成任务标记为完成。",
    "完成今天所有计划",
])
def test_first_explicit_today_all_request_shows_every_uid_then_confirms(setup, user_text):
    service, runtime, growth, root = setup
    targets = seed(growth)
    already = growth.add_task("原来已经完成的项目")
    growth.complete_by_id(already["id"])
    yesterday = (growth.now_provider().date() - timedelta(days=1)).isoformat()
    previous = growth.add_task("昨天未完成的项目", date=yesterday)
    before = business_bytes(root)
    preview = service.handle(user_text, "fresh-today-all")
    assert preview.status == "clarification"
    assert "确认" in preview.message
    assert all(task["title"] in preview.message for task in targets)
    assert already["title"] not in preview.message and previous["title"] not in preview.message
    assert not preview.tool_results and business_bytes(root) == before
    state = runtime.interaction_coordinator.current("fresh-today-all")
    ids = [task["uid"] for task in targets]
    assert state.state == "awaiting_confirmation" and state.interaction_kind == "plan_batch_completion"
    assert state.selected_object_ids == ids
    assert [obj["stable_id"] for obj in state.last_read_snapshot["objects"]] == ids
    result = service.handle("确认", "fresh-today-all")
    assert result.status == "completed"
    assert len(result.tool_results) == 3 and all(r.success and r.tool == "complete_plan" for r in result.tool_results)
    saved = json.loads((root / "data/private/today_plan.json").read_text(encoding="utf-8"))
    disk_tasks = {task["uid"]: task for day in saved["days"].values() for task in day["tasks"]}
    assert all(disk_tasks[uid]["done"] for uid in ids)
    assert not disk_tasks[previous["uid"]]["done"]
    today = {task["uid"]: task for task in growth.tasks()}
    assert all(today[uid]["done"] for uid in ids)
    assert today[already["uid"]]["done"]
    assert not growth.tasks(yesterday)[0]["done"]
    after = business_bytes(root)
    repeated = service.handle("确认", "fresh-today-all")
    assert not repeated.tool_results and business_bytes(root) == after


@pytest.mark.parametrize("user_text", [
    "都完成了", "我都完成了", "我全都完成了", "全部", "所有计划都完成了", "不要把今天所有未完成计划标记完成",
    "今天所有未完成计划还没完成", "如果把今天所有计划完成会怎么样",
    "怎么把今天所有未完成计划标记完成？", "明天再完成今天所有计划",
    "长期方案里今天所有计划应该怎样完成", "长期方案里今天所有计划都会完成",
    "删除今天所有未完成计划",
])
def test_ambiguous_negative_conditional_or_other_operation_never_creates_batch(setup, user_text):
    service, runtime, growth, root = setup
    seed(growth)
    before = business_bytes(root)
    response = service.handle(user_text, "unsafe-scope")
    state = runtime.interaction_coordinator.current("unsafe-scope")
    # Existing method/condition questions may use a read-only fact preview.
    # They must never authorize completion or another mutation.
    assert all(result.operation_kind == "read" and result.tool != "complete_plan"
               for result in response.tool_results)
    assert business_bytes(root) == before
    assert not (state.state == "awaiting_confirmation" and state.interaction_kind == "plan_batch_completion")
    confirmation = service.handle("确认", "unsafe-scope")
    assert not confirmation.tool_results and business_bytes(root) == before


def test_empty_today_scope_has_no_fake_confirmation(setup):
    service, runtime, growth, root = setup
    before = business_bytes(root)
    response = service.handle("把今天所有未完成计划标记完成", "empty")
    assert "没有" in response.message and "待完成" in response.message
    assert not response.tool_results
    assert runtime.interaction_coordinator.current("empty").state != "awaiting_confirmation"
    assert not service.handle("确认", "empty").tool_results
    assert business_bytes(root) == before


@pytest.mark.parametrize("change", ["cancel", "replace", "complete_one", "cross_day", "other_session"])
@pytest.mark.parametrize("message", ["把今天所有未完成计划标记完成", "我都完成了"])
def test_existing_confirmation_protections_keep_zero_wrong_writes(setup, change, message):
    service, runtime, growth, root = setup
    targets = seed(growth)
    if message == "我都完成了":
        service.handle("查看今天计划", "protected")
    assert service.handle(message, "protected").status == "clarification"
    if change == "cancel":
        service.handle("取消", "protected")
    elif change == "replace":
        growth.delete_by_id(targets[1]["id"])
        growth.add_task("替换后新项目")
    elif change == "complete_one":
        growth.complete_by_id(targets[1]["id"])
    elif change == "cross_day":
        tomorrow = growth.now_provider() + timedelta(days=1)
        growth.now_provider = lambda: tomorrow
        runtime.business_now_provider = lambda: tomorrow
    before = business_bytes(root)
    response = service.handle("确认", "another" if change == "other_session" else "protected")
    assert not response.tool_results and business_bytes(root) == before


@pytest.mark.parametrize("change", ["other_session", "expired", "cross_day"])
def test_bare_completion_requires_a_current_same_session_list(setup, change):
    service, runtime, growth, root = setup
    seed(growth)
    service.handle("查看今天计划", "shown")
    if change == "expired":
        later = runtime.interaction_coordinator.now_provider() + timedelta(
            seconds=runtime.interaction_coordinator.continuation_ttl_seconds + 1,
        )
        runtime.interaction_coordinator.now_provider = lambda: later
    elif change == "cross_day":
        tomorrow = growth.now_provider() + timedelta(days=1)
        growth.now_provider = lambda: tomorrow
        runtime.business_now_provider = lambda: tomorrow
    before = business_bytes(root)
    scope = "another" if change == "other_session" else "shown"
    response = service.handle("我都完成了", scope)
    assert not response.tool_results and business_bytes(root) == before
    state = runtime.interaction_coordinator.current(scope)
    assert not (state.state == "awaiting_confirmation" and state.interaction_kind == "plan_batch_completion")
    assert not service.handle("确认", scope).tool_results
    assert business_bytes(root) == before


def test_bare_completion_never_expands_to_unshown_new_plans(setup):
    service, runtime, growth, root = setup
    targets = seed(growth)
    service.handle("查看今天计划", "shown-only")
    unshown = growth.add_task("查看列表之后新增的计划")
    before = business_bytes(root)
    preview = service.handle("我都完成了", "shown-only")
    assert preview.status == "clarification"
    assert all(task["title"] in preview.message for task in targets)
    assert unshown["title"] not in preview.message
    assert not preview.tool_results and business_bytes(root) == before
    response = service.handle("确认", "shown-only")
    assert response.status == "completed"
    assert {result.data["task"]["uid"] for result in response.tool_results} == {task["uid"] for task in targets}
    assert all(result.success and result.tool == "complete_plan" for result in response.tool_results)
    actual = {task["uid"]: task for task in growth.tasks()}
    assert all(actual[task["uid"]]["done"] for task in targets)
    assert not actual[unshown["uid"]]["done"]
    assert growth.records_for_date() == []


@pytest.mark.parametrize("bad_read", ["raises", "not_a_list", "missing_uid", "duplicate_uid", "wrong_date"])
def test_incomplete_scope_read_fails_without_narrowing(setup, monkeypatch, bad_read):
    service, runtime, growth, root = setup
    targets = seed(growth)
    def read(_date=None):
        if bad_read == "raises":
            raise OSError("synthetic complete-scope read failure")
        if bad_read == "not_a_list":
            return {"tasks": targets}
        if bad_read == "missing_uid":
            return [targets[0], {"title": "不完整项目", "status": "pending"}]
        if bad_read == "wrong_date":
            return [{**targets[0], "date": "2000-01-01"}]
        return [targets[0], targets[0]]
    monkeypatch.setattr(runtime.business_resolver.plan_service, "list_plans", read)
    before = business_bytes(root)
    response = service.handle("把今天所有未完成计划标记完成", "read-failure")
    assert response.status == "failed" and not response.tool_results
    assert runtime.interaction_coordinator.current("read-failure").state != "awaiting_confirmation"
    assert business_bytes(root) == before


def test_cross_day_old_list_cannot_authorize_an_ambiguous_all_reply(setup):
    service, runtime, growth, root = setup
    seed(growth)
    service.handle("查看今天计划", "stale-list")
    tomorrow = growth.now_provider() + timedelta(days=1)
    growth.now_provider = lambda: tomorrow
    runtime.business_now_provider = lambda: tomorrow
    before = business_bytes(root)
    response = service.handle("都完成了", "stale-list")
    assert not response.tool_results and business_bytes(root) == before
    assert runtime.interaction_coordinator.current("stale-list").state != "awaiting_confirmation"
    assert not service.handle("确认", "stale-list").tool_results
    assert business_bytes(root) == before


def test_old_read_ordinal_and_read_batch_paths_remain(setup):
    service, runtime, growth, root = setup
    targets = seed(growth)
    service.handle("查看今天计划", "old-single")
    single = service.handle("第二项完成了", "old-single")
    assert single.status == "completed" and len(single.tool_results) == 1
    assert [t["done"] for t in growth.tasks()] == [False, True, False]
    service.handle("查看今天计划", "old-batch")
    before = business_bytes(root)
    preview = service.handle("刚才展示的未完成计划我都完成了", "old-batch")
    assert preview.status == "clarification" and business_bytes(root) == before
    assert service.handle("确认", "old-batch").status == "completed"
    assert all(task["done"] for task in growth.tasks())
