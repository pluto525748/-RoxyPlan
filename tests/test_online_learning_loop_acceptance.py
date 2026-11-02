"""Opt-in, isolated real-model acceptance for growth facts and plan-list selection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from modules.llm.routed_client import RoutedLLMClient
from modules.llm.secret_store import SecretStore
from modules.development_log import configure_development_log
from v18_test_support import build_service


pytestmark = pytest.mark.skipif(
    os.environ.get("ROXYPLAN_ONLINE_ACCEPTANCE") != "1",
    reason="explicit opt-in required for real-model acceptance",
)


@pytest.mark.parametrize("repetition", range(3))
@pytest.mark.parametrize("wording_id,wording", [
    (1, "今天想兼顾项目文档、算法练习和短休息。先给我三条可执行建议，别改计划。"),
    (2, "今天我想写点项目文档、练一道算法题，也留几分钟休息。先给三条建议，暂时不要写进计划。"),
    (3, "帮我把今天的项目文档、算法练习和短休息想成三条可执行安排，先说建议，不要替我添加计划。"),
])
def test_online_learning_loop_plan_advice_and_selection(wording_id, wording, repetition):
    project_root = Path(__file__).resolve().parents[1]
    run_root = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]).resolve()
    assert run_root.parent.name == "runs"
    acceptance_root = run_root / "artifacts" / f"online_learning_loop_w{wording_id}_r{repetition + 1}"
    acceptance_root.mkdir(parents=True, exist_ok=False)
    development_log = configure_development_log(acceptance_root / "development_log")

    formal_settings_path = project_root / "data" / "pet_config.json"
    formal_settings = json.loads(formal_settings_path.read_text(encoding="utf-8"))
    public_model_keys = {
        "deepseek_base_url", "default_model", "complex_model", "model_mode",
        "enable_auto_escalation", "enable_thinking_for_complex_tasks",
        "request_timeout_seconds", "max_provider_retries", "max_output_tokens",
    }
    model_settings = {
        key: value for key, value in formal_settings.items() if key in public_model_keys
    }
    model_settings.update({"online_model_enabled": True, "offline_fallback_enabled": False})
    secret_store = SecretStore(acceptance_root)
    key, key_source = secret_store.get_deepseek_key()
    assert key, "DeepSeek credential unavailable; online acceptance cannot run"

    client = RoutedLLMClient(
        acceptance_root, settings=model_settings, secret_store=secret_store,
    )
    assert client.deepseek_provider.configured
    captured_requests = []
    original_chat = client.deepseek_provider.chat_with_tools

    def tracked_chat(messages, *args, **kwargs):
        captured_requests.append([str(item.get("content", "")) for item in messages])
        return original_chat(messages, *args, **kwargs)

    client.deepseek_provider.chat_with_tools = tracked_chat
    service, growth, _memory, _history = build_service(acceptance_root, client)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    growth.add_task("验收算法复习", yesterday.isoformat(), duration_minutes=90)
    growth.add_task("验收项目文档", yesterday.isoformat(), duration_minutes=60)
    growth.add_record("验收算法练习", yesterday.isoformat(), duration_minutes=20)
    saved_review = growth.save_today_review(yesterday.isoformat())
    before_titles = [item["title"] for item in growth.tasks(today.isoformat())]

    conversation_id = "online-learning-loop-" + today.isoformat()
    advice = service.handle(wording, conversation_id)
    advice_diagnostics = service.diagnostic_snapshot()
    after_advice_titles = [item["title"] for item in growth.tasks(today.isoformat())]
    advice_context_seen = any(
        "学习计划调整依据" in "\n".join(request)
        or "昨日已保存复盘" in "\n".join(request)
        for request in captured_requests
    )
    online_usage = client.usage_store.summary()
    online_requests = [
        item for item in online_usage["recent"]
        if item["provider"] == "deepseek" and item["success"]
    ]
    report = {
        "date": today.isoformat(),
        "wording_id": wording_id,
        "repetition": repetition + 1,
        "user_wording": wording,
        "key_source": key_source,
        "configured_model": client.settings.default_model,
        "provider": client.provider,
        "model": client.model,
        "route_reason": client.last_route.reason if client.last_route else "",
        "online_requests": len(online_requests),
        "advice_context_seen": advice_context_seen,
        "request_review_markers": [
            "昨日已保存复盘" in "\n".join(request) for request in captured_requests
        ],
        "yesterday_review_saved": bool(saved_review["review"]["text"]),
        "advice": advice.to_dict(),
        "advice_diagnostics": advice_diagnostics,
        "development_log_directory": str(development_log.run_directory or ""),
        "before_titles": before_titles,
        "after_advice_titles": after_advice_titles,
    }
    report_path = acceptance_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    assert online_requests, "no successful real DeepSeek request observed"
    assert not advice_context_seen, "historic growth evidence was injected into advice"
    assert before_titles == after_advice_titles, "advice unexpectedly wrote a plan"
    assert "调整依据：" not in advice.message, "history-adjustment explanation was inserted"
    assert all(f"{index}." in advice.message for index in range(1, 4)), "model omitted a requested candidate"
    assert all(topic in advice.message for topic in ("文档", "算法", "休息")), "model omitted a requested topic"

    selected = service.handle("就按这三条安排吧", conversation_id)
    final_tasks = growth.tasks(today.isoformat())
    final_titles = [item["title"] for item in final_tasks]
    report.update({
        "selection": selected.to_dict(),
        "selection_diagnostics": service.diagnostic_snapshot(),
        "final_titles": final_titles,
        "final_task_uids": [item["uid"] for item in final_tasks],
        "online_requests_after_selection": len([
            item for item in client.usage_store.summary()["recent"]
            if item["provider"] == "deepseek" and item["success"]
        ]),
    })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert selected.status == "completed"
    assert len(final_tasks) == 3, "the displayed list was not fully added"
    assert len({item["uid"] for item in final_tasks}) == 3
    assert [item.tool for item in selected.tool_results] == ["add_plan"] * 3
    assert all(item.success for item in selected.tool_results)


@pytest.mark.parametrize("repetition", range(3))
@pytest.mark.parametrize("wording_id,initial,first_change,second_change", [
    (1, "今天想学机器学习和 agent 课程，先给我今天的计划建议，不要直接加入。", "把学习强度拉高", "晚上不安排学习"),
    (2, "帮我建议今天怎么安排机器学习与 agent 课程，先只给候选。", "学习内容安排得紧凑一些", "晚上留给休息"),
    (3, "我今天要进入学习状态，机器学习和 agent 课程都想推进，请先列出计划建议。", "白天多安排一点学习", "十一点后再开始"),
])
def test_online_one_revision_boundary_and_fresh_advice(
    wording_id, initial, first_change, second_change, repetition,
):
    project_root = Path(__file__).resolve().parents[1]
    run_root = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]).resolve()
    assert run_root.parent.name == "runs"
    acceptance_root = run_root / "artifacts" / f"online_one_revision_w{wording_id}_r{repetition + 1}"
    acceptance_root.mkdir(parents=True, exist_ok=False)
    development_log = configure_development_log(acceptance_root / "development_log")

    settings = json.loads((project_root / "data" / "pet_config.json").read_text(encoding="utf-8"))
    public_model_keys = {
        "deepseek_base_url", "default_model", "complex_model", "model_mode",
        "enable_auto_escalation", "enable_thinking_for_complex_tasks",
        "request_timeout_seconds", "max_provider_retries", "max_output_tokens",
    }
    model_settings = {key: value for key, value in settings.items() if key in public_model_keys}
    model_settings.update({"online_model_enabled": True, "offline_fallback_enabled": False})
    secret_store = SecretStore(acceptance_root)
    key, key_source = secret_store.get_deepseek_key()
    assert key, "DeepSeek credential unavailable; online acceptance cannot run"
    client = RoutedLLMClient(acceptance_root, settings=model_settings, secret_store=secret_store)
    assert client.deepseek_provider.configured
    raw_model_replies = []
    original_chat = client.deepseek_provider.chat_with_tools

    def tracked_chat(messages, *args, **kwargs):
        result = original_chat(messages, *args, **kwargs)
        raw_model_replies.append(str(result.content or ""))
        return result

    client.deepseek_provider.chat_with_tools = tracked_chat
    service, growth, _memory, _history = build_service(acceptance_root, client)
    runtime = service.conversation_service
    today = datetime.now().date()
    overdue = growth.add_task("机器学习", date=(today - timedelta(days=1)).isoformat())
    conversation_id = f"online-one-revision-{wording_id}-{repetition + 1}"

    first = service.handle(initial, conversation_id)
    first_snapshot = runtime._recent_suggestion_snapshot(conversation_id)
    revised = service.handle(first_change, conversation_id)
    revised_snapshot = runtime._recent_suggestion_snapshot(conversation_id)
    online_before_second = len([
        item for item in client.usage_store.summary()["recent"]
        if item["provider"] == "deepseek" and item["success"]
    ])
    exceeded = service.handle(second_change, conversation_id)
    online_after_second = len([
        item for item in client.usage_store.summary()["recent"]
        if item["provider"] == "deepseek" and item["success"]
    ])
    stale_add = service.handle("全部加入", conversation_id)
    after_stale_titles = [task["title"] for task in growth.tasks(today.isoformat())]
    fresh = service.handle(
        "请重新给我今天的学习计划建议：机器学习和 agent 课程，强度高，晚上不安排学习。",
        conversation_id,
    )
    fresh_snapshot = runtime._recent_suggestion_snapshot(conversation_id)
    added = service.handle("全部加入", conversation_id) if fresh_snapshot else None
    final_tasks = growth.tasks(today.isoformat())
    report = {
        "date": today.isoformat(),
        "wording_id": wording_id,
        "repetition": repetition + 1,
        "input": [initial, first_change, second_change],
        "key_source": key_source,
        "provider": client.provider,
        "model": client.model,
        "raw_model_replies": raw_model_replies,
        "online_before_second": online_before_second,
        "online_after_second": online_after_second,
        "first": first.to_dict(),
        "first_snapshot_titles": [item.title for item in first_snapshot.objects] if first_snapshot else [],
        "revised": revised.to_dict(),
        "revised_snapshot_titles": [item.title for item in revised_snapshot.objects] if revised_snapshot else [],
        "exceeded": exceeded.to_dict(),
        "stale_add": stale_add.to_dict(),
        "after_stale_titles": after_stale_titles,
        "fresh": fresh.to_dict(),
        "fresh_snapshot_titles": [item.title for item in fresh_snapshot.objects] if fresh_snapshot else [],
        "added": added.to_dict() if added else None,
        "final_titles": [task["title"] for task in final_tasks],
        "final_uids": [task["uid"] for task in final_tasks],
        "overdue_uid": overdue["uid"],
        "development_log_directory": str(development_log.run_directory or ""),
    }
    (acceptance_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    assert first_snapshot and revised_snapshot, "a visible suggestion list was not captured"
    assert revised_snapshot.snapshot_id != first_snapshot.snapshot_id
    assert revised.tool_results == []
    assert "已经调整过一次" in exceeded.message
    assert exceeded.tool_results == []
    assert online_after_second == online_before_second
    assert stale_add.tool_results == []
    assert after_stale_titles == []
    assert fresh_snapshot, "the complete new advice request did not create a list"
    assert added and [result.tool for result in added.tool_results] == ["add_plan"] * len(fresh_snapshot.objects)
    assert all(result.success for result in added.tool_results)
    assert [task["title"] for task in final_tasks] == [item.title for item in fresh_snapshot.objects]
    assert growth.tasks((today - timedelta(days=1)).isoformat())[0]["uid"] == overdue["uid"]
