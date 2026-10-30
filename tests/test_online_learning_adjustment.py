"""One opt-in DeepSeek check for the isolated learning-adjustment flow."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from modules.llm.routed_client import RoutedLLMClient
from modules.llm.secret_store import SecretStore
from v18_test_support import build_service


pytestmark = pytest.mark.skipif(
    os.environ.get("ROXYPLAN_ONLINE_ACCEPTANCE") != "1",
    reason="explicit online acceptance opt-in required",
)


def test_one_online_adjustment_and_adoption():
    project = Path(__file__).resolve().parents[1]
    run_root = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]).resolve()
    assert run_root.parent.name == "runs"
    root = run_root / "artifacts" / "online_learning_adjustment"
    root.mkdir(parents=True, exist_ok=False)
    public_keys = {
        "deepseek_base_url", "default_model", "complex_model", "model_mode",
        "enable_auto_escalation", "enable_thinking_for_complex_tasks",
        "request_timeout_seconds", "max_provider_retries", "max_output_tokens",
    }
    settings = json.loads((project / "data" / "pet_config.json").read_text(encoding="utf-8"))
    settings = {key: value for key, value in settings.items() if key in public_keys}
    settings.update({
        "online_model_enabled": True,
        "offline_fallback_enabled": False,
        "model_mode": "economy",
        "enable_auto_escalation": False,
        "max_provider_retries": 0,
        "max_output_tokens": 700,
    })
    secrets = SecretStore(root)
    assert secrets.get_deepseek_key()[0], "DeepSeek credential unavailable"
    client = RoutedLLMClient(root, settings=settings, secret_store=secrets)
    assert client.deepseek_provider.configured
    captured = []
    raw_outputs = []
    original_chat = client.deepseek_provider.chat_with_tools

    def capture(messages, *args, **kwargs):
        captured.append("\n".join(str(item.get("content", "")) for item in messages))
        response = original_chat(messages, *args, **kwargs)
        raw_outputs.append(str(response.content or ""))
        return response

    client.deepseek_provider.chat_with_tools = capture
    service, growth, memory, history = build_service(root, client)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    memory.add_memory("长期目标是稳步掌握算法基础和错题复盘", category="goal")
    completed = growth.add_task("阅读算法入门", yesterday.isoformat(), duration_minutes=30)
    growth.complete_by_id(completed["id"], yesterday.isoformat())
    old = growth.add_task("算法练习", yesterday.isoformat(), duration_minutes=45)
    growth.add_record("做了两道算法题", date=yesterday.isoformat(), duration_minutes=25)
    growth.save_today_review(yesterday.isoformat())
    session = "online-adjustment-" + today.isoformat()
    report = {
        "business_date": today.isoformat(),
        "source_plan_uid": old["uid"],
        "stage": "before_advice",
    }
    path = root / "report.json"

    try:
        advice = service.handle(
            "请根据我的长期目标和昨天执行情况，建议今天两项学习计划：继续算法练习并整理错题，"
            "今天总共最多50分钟。先给建议，不要直接加入计划。",
            session,
        )
        report["stage"] = "after_advice"
        report["advice"] = advice.to_dict()
        report["plans_after_advice"] = growth.tasks(today.isoformat())
        advice_messages = [
            item for item in history.messages(session)
            if isinstance(item.get("metadata", {}), dict)
            and item["metadata"].get("planning_advice")
        ]
        report["advice_evidence"] = advice_messages[-1]["metadata"]["planning_advice"] if advice_messages else {}
        joined = "\n".join(captured)
        report["sent_fact_checks"] = {
            "goal": "长期目标是稳步掌握算法基础和错题复盘" in joined,
            "yesterday_plan": "算法练习" in joined,
            "yesterday_action": "做了两道算法题" in joined,
            "last_7_days": "last_7_days" in joined,
            "last_30_days": "last_30_days" in joined,
            "explicit_time": "最多50分钟" in joined,
        }
        assert all(report["sent_fact_checks"].values())
        assert report["advice_evidence"]["facts"]["yesterday"]["actions"] == ["做了两道算法题"]
        assert report["advice_evidence"]["facts"]["yesterday"]["actual_minutes"] == 25
        assert growth.tasks(today.isoformat()) == []
        assert len(report["advice_evidence"].get("displayed_candidates", [])) == 2

        adopted = service.handle("就按这两条安排吧", session)
        report["stage"] = "after_adoption"
        report["adoption"] = adopted.to_dict()
        report["final_plans"] = growth.tasks(today.isoformat())
        report["adoption_evidence"] = [
            item["metadata"]["suggestion_adoption"]
            for item in history.messages(session)
            if isinstance(item.get("metadata", {}), dict)
            and item["metadata"].get("suggestion_adoption")
        ]
        assert adopted.status == "completed"
        assert len(report["final_plans"]) == 2
        assert all(result.success for result in adopted.tool_results)
        assert len(report["adoption_evidence"][-1]["items"]) == 2
        assert growth.tasks(yesterday.isoformat())[-1]["uid"] == old["uid"]
    finally:
        recent = client.usage_store.summary()["recent"]
        online = [item for item in recent if item.get("provider") == "deepseek"]
        report["online_request_count"] = len(online)
        report["online_usage"] = [{
            key: item.get(key) for key in (
                "model", "success", "input_tokens", "output_tokens", "total_tokens",
                "latency_ms", "route_reason",
            )
        } for item in online]
        report["captured_request_count"] = len(captured)
        report["raw_model_outputs"] = raw_outputs
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
