"""Captured exact-title completion plus untouched-remainder scope, offline only."""
from copy import deepcopy
from datetime import datetime
import pytest
from modules.conversation_service import ConversationService
from test_beta_online_failure_replay import _build_service, _provider_body

CAPTURED_DECISION = '{"mode":"write","intent":"complete_plan","entities":{"match_text":"练习列表推导式"},"proposed_tool":"complete_plan","confidence":0.9,"follow_up_target":"","needs_confirmation":false,"warnings":[],"clarification_question":"","candidate_actions":[],"subject":"self","polarity":"positive","modality":"commitment","request_mode":"execute","explicit_command":true}'
DAY = "2026-11-02"


@pytest.mark.parametrize("user", [
    "把今天的练习列表推导式标记完成，其余先不动。",
    "其余先不动，把今天的练习列表推导式标记完成。",
    "把今天的练习列表推导式标记完成，其他计划不要动。",
    "把今天的练习列表推导式标记完成，其余 先不动。",
])
def test_exact_single_completion_without_read_snapshot(user, tmp_path, monkeypatch):
    # First input and semantic JSON were captured from online-56a74a9-second
    # d1-t06. Other inputs are declared synthetic exclusion-order variants.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-no-network")
    requests = []
    def transport(method, url, headers, payload, timeout):
        requests.append(deepcopy(payload))
        assert "You classify one Chinese user message" in payload["messages"][0]["content"]
        return _provider_body({"content": CAPTURED_DECISION})
    service = _build_service(tmp_path, transport)
    service.growth_manager.now_provider = lambda: datetime(2026, 11, 2, 10)
    service.conversation_service.business_now_provider = service.growth_manager.now_provider
    growth = service.growth_manager
    target = growth.add_task("练习列表推导式", date=DAY, duration_minutes=15)
    untouched = growth.add_task("练习读取文本文件", date=DAY, duration_minutes=10)
    old = growth.add_task("昨天的任务", date="2026-11-01")
    before = {task["uid"]: deepcopy(task) for task in growth.tasks(DAY)}
    result = service.handle(user, "exact-single-without-list")
    assert [tool.tool for tool in result.tool_results] == ["complete_plan"], result.message
    assert result.tool_results[0].success
    after = {task["uid"]: task for task in growth.tasks(DAY)}
    assert set(after) == set(before)
    assert after[target["uid"]]["done"] is True
    assert after[untouched["uid"]] == before[untouched["uid"]]
    assert growth.tasks("2026-11-01") == [old]
    assert result.tool_results[0].data["task"]["uid"] == target["uid"]
    assert result.tool_results[0].data["postcondition_verified"] is True
    assert len(requests) == 1


@pytest.mark.parametrize("message", [
    "其余先不动。",
    "其他计划不要动。",
    "不要完成任何任务，其余先不动。",
])
def test_negative_scope_alone_never_becomes_completion(message):
    assert ConversationService._completion_collection_scope_text(message) == message


def test_affirmative_all_scope_remains_a_batch():
    projected = ConversationService._completion_collection_scope_text("把所有计划标记完成，其余先不动。")
    assert "所有计划标记完成" in projected

