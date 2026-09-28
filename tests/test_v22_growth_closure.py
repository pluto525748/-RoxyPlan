from __future__ import annotations

import json
from datetime import datetime, timedelta

from PySide6.QtWidgets import QApplication, QMessageBox

from frontend.growth_dialog import GrowthDialog
from frontend.settings_dialog import SettingsDialog
from modules.growth_manager import GrowthManager
from modules.intent_router import LLMIntentParser
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from modules.review_backfill import ReviewBackfillRunner
from v18_test_support import NoCallLLM, build_service


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_review_snapshot_contains_stable_plan_fields_and_plan_extra_actions(tmp_path):
    clock = FakeClock(datetime(2026, 9, 10, 8, 30, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    day = "2026-09-09"
    first = manager.add_task(
        "核对封版清单",
        date=day,
        time_slot="上午",
        duration_minutes=25,
    )
    manager.add_task("补充兼容说明", date=day, time_slot="下午", duration_minutes=40)
    manager.complete_by_id(int(first["id"]), date=day)
    action = manager.add_record("修复了一个计划外问题", source="chat", date=day)

    review = manager.generate_review(day)

    assert [item["stable_id"] for item in review["plan_snapshot"]] == [
        item["uid"] for item in manager.tasks(day)
    ]
    assert review["plan_snapshot"][0] == {
        "stable_id": first["uid"],
        "title": "核对封版清单",
        "done": True,
        "status": "completed",
        "date": day,
        "time_slot": "上午",
        "duration_minutes": 25,
    }
    assert review["action_records"] == [action]
    assert review["actions"] == ["修复了一个计划外问题"]
    assert "计划外行动" in review["text"]


def test_ensure_review_is_idempotent_and_never_overwrites_existing_entry(tmp_path):
    manager = GrowthManager(
        tmp_path / "private",
        now_provider=lambda: datetime(2026, 9, 10, 9, 0, 0),
    )
    day = "2026-09-09"
    manager.add_task("昨天的计划", date=day)

    created = manager.ensure_review_for_date(day)
    frozen_entry = json.loads(json.dumps(created["entry"], ensure_ascii=False))
    manager.add_record("后来补记但不应自动覆盖", date=day)
    existing = manager.ensure_review_for_date(day)

    assert created["status"] == "created"
    assert created["created"] is True
    assert existing == {
        "status": "existing",
        "created": False,
        "entry": frozen_entry,
    }
    assert manager.entries() == [frozen_entry]


def test_ensure_review_does_not_create_an_empty_day(tmp_path):
    manager = GrowthManager(
        tmp_path / "private",
        now_provider=lambda: datetime(2026, 9, 10, 9, 0, 0),
    )

    result = manager.ensure_review_for_date("2026-09-09")

    assert result == {"status": "empty", "created": False, "entry": None}
    assert manager.entries() == []


def test_review_backfill_runner_checks_yesterday_only_and_never_calls_a_model(tmp_path):
    clock = FakeClock(datetime(2026, 9, 10, 10, 0, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    manager.add_task("前天事项", date="2026-09-08")
    manager.add_task("昨天事项", date="2026-09-09")
    runner = ReviewBackfillRunner(
        manager,
        now_provider=clock,
        diagnostics_dir=tmp_path / "diagnostics",
    )

    result = runner.run()

    assert result["status"] == "created"
    assert [item["date"] for item in manager.entries()] == ["2026-09-09"]


def test_review_backfill_failure_is_diagnostic_and_only_repeated_failure_notifies(
    tmp_path,
):
    class FailingGrowth:
        def ensure_review_for_date(self, _date):
            raise OSError("private detail must not be logged")

    notices = []
    kwargs = {
        "now_provider": lambda: datetime(2026, 9, 10, 10, 0, 0),
        "diagnostics_dir": tmp_path / "diagnostics",
        "notification_callback": notices.append,
    }

    first = ReviewBackfillRunner(FailingGrowth(), **kwargs).run()
    second = ReviewBackfillRunner(FailingGrowth(), **kwargs).run()
    records = [
        json.loads(line)
        for line in (tmp_path / "diagnostics" / "review_backfill.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert first["status"] == second["status"] == "failed"
    assert first["notified"] is False
    assert second["notified"] is True
    assert len(notices) == 1
    assert records[-1]["consecutive_failures"] == 2
    assert "private detail" not in json.dumps(records, ensure_ascii=False)


def test_review_backfill_detects_corrupt_growth_data_and_notifies_immediately(
    tmp_path,
):
    clock = FakeClock(datetime(2026, 9, 10, 10, 0, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    manager.add_task("不应在损坏状态下自动生成复盘", date="2026-09-09")
    manager.growth_file.write_text("{broken", encoding="utf-8")
    notices = []
    runner = ReviewBackfillRunner(
        manager,
        now_provider=clock,
        diagnostics_dir=tmp_path / "diagnostics",
        notification_callback=notices.append,
    )

    result = runner.run()
    diagnostic = json.loads(
        (tmp_path / "diagnostics" / "review_backfill.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )

    assert result["status"] == "failed"
    assert result["created"] is False
    assert result["notified"] is True
    assert result["error_code"] == "ReviewDataCorruptionError"
    assert diagnostic["data_corruption"] is True
    assert len(notices) == 1
    assert "broken" not in json.dumps(diagnostic, ensure_ascii=False)


def test_natural_month_statistics_do_not_leak_adjacent_months(tmp_path):
    manager = GrowthManager(
        tmp_path / "private",
        now_provider=lambda: datetime(2026, 8, 20, 20, 0, 0),
    )
    july = manager.add_task("七月事项", date="2026-07-31")
    august_done = manager.add_task("八月已完成", date="2026-08-01")
    manager.complete_by_id(int(august_done["id"]), date="2026-08-01")
    manager.add_task("八月待完成", date="2026-08-31")
    manager.add_record("八月计划外行动", date="2026-08-31")
    manager.add_task("九月事项", date="2026-09-01")
    for day in ("2026-07-31", "2026-08-01", "2026-08-31", "2026-09-01"):
        manager.save_today_review(day)

    entries = manager.entries_for_month("2026-08")
    stats = manager.monthly_statistics("2026-08")

    assert [item["date"] for item in entries] == ["2026-08-01", "2026-08-31"]
    assert stats == {
        "month": "2026-08",
        "logged_days": 2,
        "total_plans": 2,
        "completed_plans": 1,
        "pending_plans": 1,
        "action_count": 1,
        "completion_rate": 0.5,
    }
    assert july["uid"] not in {
        plan["stable_id"]
        for entry in entries
        for plan in entry["review"]["plan_snapshot"]
    }


def test_settings_default_and_saved_auto_yesterday_review_are_independent(tmp_path):
    app = QApplication.instance() or QApplication([])
    config_file = tmp_path / "pet_config.json"
    dialog = SettingsDialog(
        config_file=config_file,
        secret_store=SecretStore(tmp_path),
        usage_store=ModelUsageStore(tmp_path),
    )

    assert dialog.auto_complete_yesterday_review.isChecked() is True
    dialog.auto_complete_yesterday_review.setChecked(False)
    dialog.evening_review_enabled.setChecked(True)
    original_information = QMessageBox.information
    QMessageBox.information = lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    try:
        dialog._save()
    finally:
        QMessageBox.information = original_information
    saved = json.loads(config_file.read_text(encoding="utf-8"))

    assert saved["auto_complete_yesterday_review"] is False
    assert saved["evening_review_enabled"] is True
    dialog.close()
    app.processEvents()


def test_growth_dialog_displays_current_natural_month_statistics_and_each_day(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = GrowthManager(
        tmp_path / "private",
        now_provider=lambda: datetime(2026, 8, 20, 20, 0, 0),
    )
    for day in ("2026-08-01", "2026-08-18"):
        manager.add_task(f"{day} 事项", date=day)
        manager.save_today_review(day)
    manager.add_task("上月事项", date="2026-07-31")
    manager.save_today_review("2026-07-31")

    dialog = GrowthDialog(manager)
    dialog.refresh_growth_logs()

    assert dialog.month_selector.currentData() == "2026-08"
    assert "记录 2 天" in dialog.month_summary_label.text()
    assert dialog.growth_log_list.count() == 2
    assert "2026-08-01" in dialog.growth_log_list.item(0).text()
    assert "2026-08-18" in dialog.growth_log_list.item(1).text()
    dialog.close()
    app.processEvents()


def test_growth_tool_returns_verified_month_and_manual_yesterday_regeneration(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    today = datetime.now().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    growth.add_task("昨日核验计划", date=yesterday)
    growth.save_today_review(yesterday)
    revision = growth.entries()[0]["revision"]

    regenerated = service.handle("重新生成昨天的复盘", "growth-regenerate-yesterday")
    monthly = service.handle("查看本月成长日志", "growth-current-month")

    assert regenerated.status == "completed"
    assert regenerated.tool_results[0].tool == "save_daily_review"
    assert regenerated.tool_results[0].data["entry"]["date"] == yesterday
    assert growth.entries()[0]["revision"] == revision + 1
    assert monthly.status == "completed"
    assert monthly.tool_results[0].tool == "show_growth_log"
    assert monthly.tool_results[0].data["statistics"]["month"] == today.strftime(
        "%Y-%m"
    )
    assert "计划完成" in monthly.message
    assert "计划外行动" in monthly.message


def test_monthly_natural_summary_is_requested_only_from_verified_tool_data(tmp_path):
    class MonthlySummaryLLM:
        def __init__(self) -> None:
            self.summary_messages = []

        def chat(self, messages):
            first = str(messages[0].get("content", "")) if messages else ""
            if "You classify one Chinese user message" in first:
                return json.dumps(
                    {
                        "mode": "read",
                        "intent": "show_growth_log",
                        "entities": {},
                        "proposed_tool": "show_growth_log",
                        "confidence": 0.99,
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
            self.summary_messages = list(messages)
            return "本月有一项经过核验的计划进展。"

    llm = MonthlySummaryLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    title = "核验月度总结事实"
    task = growth.add_task(title)
    growth.complete_by_id(int(task["id"]))
    growth.save_today_review()

    response = service.handle("请总结本月成长记录", "growth-natural-month")

    assert response.status == "completed"
    assert response.tool_results[0].tool == "show_growth_log"
    assert response.message == "本月有一项经过核验的计划进展。"
    summary_prompt = "\n".join(
        str(item.get("content", "")) for item in llm.summary_messages
    )
    assert "已核验的自然月成长 JSON" in summary_prompt
    assert title in summary_prompt
