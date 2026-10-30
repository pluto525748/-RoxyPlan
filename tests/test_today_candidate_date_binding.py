"""Captured 434e7a1 snapshot loss: historical dates must not date new candidates.

Uses provider transport replay through the isolated production service. No new
HTTP, GUI or invented suggestion snapshots. Original online evidence is untouched.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from scripts.run_seven_day_priority_acceptance import prepare_runtime, PRIOR, DAY
from test_seven_day_priority_regressions import runtime


CAPTURE = json.loads((Path(__file__).parent / "fixtures" /
                      "today_candidate_date_binding_20261027.json").read_text(encoding="utf-8"))


def setup_day(runtime):
    agent, transport, clock, calls = runtime
    clock["now"] = datetime.fromisoformat(PRIOR + "T10:00:00")
    prepare_runtime(agent, agent.project_root)
    clock["now"] = datetime.fromisoformat(DAY + "T10:00:00")
    calls.clear()
    transport.semantic = CAPTURE["semantic"]
    transport.reply = CAPTURE["raw_reply"]
    return agent, transport, clock, calls


def business(agent):
    names = {"memory.json", "today_plan.json", "action_log.json", "growth_log.json"}
    return {p.relative_to(agent.project_root).as_posix(): p.read_bytes()
            for p in agent.project_root.rglob("*.json") if p.name in names}


def write_decision(title):
    return {"mode": "write", "intent": "add_plan", "entities": {"title": title},
            "confidence": 0.95, "request_mode": "execute", "explicit_command": True}


@pytest.mark.parametrize("user", [
    CAPTURE["user"],
    "结合昨天的实际进展给我今天的两项学习候选，各12和8分钟，先只展示。",
    "参考昨天与前天的记录，请给两项今天的学习建议，分别12和8分钟。",
    "结合2026-11-23的记录给两项今日候选，分别12和8分钟。",
    "今天给我两项学习建议，参考昨天的记录，分别12和8分钟。",
    "今天只有20分钟，结合昨天的记录给两项候选，分别12和8分钟。",
    "昨天学习了20分钟，今天给我两项学习候选，分别12和8分钟。",
    "给我今天的两项学习建议，主题是《昨天与明天》，分别12和8分钟。",
])
def test_historical_context_keeps_fresh_today_snapshot_and_full_adoption(runtime, user):
    agent, transport, _, calls = setup_day(runtime)
    before = business(agent)
    reply = agent.handle(user, "candidate-date")
    assert reply.tool_results == [] and calls == []
    assert business(agent) == before
    assert reply.message == CAPTURE["raw_reply"]
    state = agent.conversation_service.interaction_coordinator.current("candidate-date")
    snapshot = state.last_suggestion_snapshot
    assert snapshot and snapshot["target_date"] == DAY
    assert snapshot["source_turn_id"] == reply.request_id
    assert [o["display_order"] for o in snapshot["objects"]] == [1, 2]
    assert not any(o["consumed"] for o in snapshot["objects"])
    transport.semantic = write_decision("第一项")
    first = agent.handle("第一项加入今天计划。", "candidate-date")
    transport.semantic = write_decision("第二项")
    second = agent.handle("第二项也加入今天计划。", "candidate-date")
    assert [c["tool"] for c in calls] == ["add_plan", "add_plan"]
    tasks = agent.growth_manager.tasks(DAY)
    assert [(t["title"], t["duration_minutes"], t["date"]) for t in tasks] == [
        ("理解过拟合：训练误差低但测试误差高的含义", 12, DAY),
        ("用一句话说明过拟合为什么在测试集上暴露", 8, DAY)]
    assert len({t["uid"] for t in tasks}) == 2
    for response, task in zip([first, second], tasks):
        assert response.tool_results[0].success
        assert response.tool_results[0].data["task"] == task
        assert task["title"] in response.message
    assert business(agent)["data/private/growth_log.json"] == before["data/private/growth_log.json"]
    state = agent.conversation_service.interaction_coordinator.current("candidate-date")
    assert state.last_suggestion_snapshot["snapshot_id"] == snapshot["snapshot_id"]
    assert all(o["consumed"] for o in state.last_suggestion_snapshot["objects"])


@pytest.mark.parametrize("user", [
    "结合昨天记录给两项明天的学习候选，分别12和8分钟。",
    "今天给我明天的两项学习建议，先只展示。",
    "参考昨天记录给我今天和明天各一项学习建议。",
    "结合昨天记录给2026-11-25的两项学习候选。",
    "明天只有20分钟，结合昨天的记录给两项学习候选。",
])
def test_future_or_mixed_target_never_becomes_today_adoption(runtime, user):
    agent, transport, _, calls = setup_day(runtime)
    before = business(agent)
    response = agent.handle(user, "other-day")
    assert response.tool_results == [] and calls == []
    assert not agent.conversation_service.interaction_coordinator.current("other-day").last_suggestion_snapshot
    transport.semantic = write_decision("第一项")
    response = agent.handle("第一项加入今天计划。", "other-day")
    assert response.tool_results == [] and calls == []
    assert business(agent) == before


@pytest.mark.parametrize("stale", ["expired", "next_day", "other_session"])
def test_repaired_snapshot_still_obeys_lifecycle(runtime, stale):
    agent, transport, clock, calls = setup_day(runtime)
    agent.handle(CAPTURE["user"], "candidate-date")
    assert agent.conversation_service.interaction_coordinator.current("candidate-date").last_suggestion_snapshot
    if stale == "expired":
        clock["now"] += timedelta(hours=1)
    if stale == "next_day":
        clock["now"] += timedelta(days=1)
    transport.semantic = write_decision("第一项")
    response = agent.handle("第一项加入今天计划。", "other" if stale == "other_session" else "candidate-date")
    assert response.tool_results == [] and calls == []
    assert agent.growth_manager.tasks(DAY) == []


def test_completed_learning_is_accepted_without_new_mastery_gate(runtime):
    agent, transport, _, calls = setup_day(runtime)
    transport.reply = "2026-11-23的复盘：你完成了训练集与测试集的区别学习，已掌握这项内容。"
    response = agent.handle("把昨天保存的复盘给我看看。", "completed-learning")
    assert [c["tool"] for c in calls] == ["generate_daily_review"]
    assert response.tool_results[0].data["review"]["done"] == 1
    assert response.message == transport.reply
    prompt = "\n".join(m["content"] for m in transport.requests[-1]["messages"])
    assert "按用户已掌握该项内容" in prompt
    assert "不追加测验或验证流程" in prompt
    assert "不证明已经掌握知识" not in prompt
