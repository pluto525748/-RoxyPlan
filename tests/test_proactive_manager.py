import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.growth_manager import GrowthManager
from modules.proactive_manager import ProactiveManager
from PySide6.QtWidgets import QApplication, QMessageBox

from frontend.settings_dialog import SettingsDialog


class TestClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def build_managers(temp_dir, hour=10, minute=0, **proactive_options):
    clock = TestClock(datetime(2026, 7, 15, hour, minute, 0))
    growth = GrowthManager(Path(temp_dir) / "private", now_provider=clock)
    proactive = ProactiveManager(
        growth,
        now_provider=clock,
        cooldown_seconds=60,
        **proactive_options,
    )
    return clock, growth, proactive


def test_pending_plan_generates_plan_pending():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, growth, proactive = build_managers(temp_dir)
        growth.add_task("学习机器学习30分钟")
        growth.add_task("整理 RoxyPlan 文档")

        reminder = proactive.check(seconds_since_interaction=121)

        assert reminder["type"] == "plan_pending"
        assert "2 个计划" in reminder["text"]


def test_plan_without_action_generates_no_action_log():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, growth, proactive = build_managers(temp_dir)
        growth.add_task("测试成长面板")

        reminder = proactive.check(
            seconds_since_interaction=121,
            allowed_types={"no_action_log"},
        )

        assert reminder["type"] == "no_action_log"
        assert "还没有行动记录" in reminder["text"]


def test_evening_generates_review_time_once_per_day():
    with tempfile.TemporaryDirectory() as temp_dir:
        clock, growth, proactive = build_managers(temp_dir, hour=21)
        growth.add_task("完成晚间复盘测试")
        growth.add_record("验证主动提醒", source="test")

        first = proactive.check(
            seconds_since_interaction=121,
            allowed_types={"review_time"},
        )
        clock.value += timedelta(minutes=2)
        second = proactive.check(
            seconds_since_interaction=121,
            allowed_types={"review_time"},
        )

        assert first["type"] == "review_time"
        assert second is None


def test_cooldown_prevents_repeated_reminder():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, growth, proactive = build_managers(temp_dir)
        growth.add_task("写测试")

        first = proactive.check(seconds_since_interaction=121)
        second = proactive.check(seconds_since_interaction=121)

        assert first is not None
        assert second is None


def test_disabled_and_paused_manager_do_not_remind():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, growth, proactive = build_managers(temp_dir, enabled=False)
        growth.add_task("不会弹出的计划")
        assert proactive.check(seconds_since_interaction=121) is None

        proactive.configure(enabled=True)
        proactive.pause()
        assert proactive.check(seconds_since_interaction=121) is None
        proactive.resume()
        assert proactive.check(seconds_since_interaction=121) is not None


def test_empty_growth_data_is_safe():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, _, proactive = build_managers(temp_dir)

        assert proactive.check(seconds_since_interaction=121) is None


def test_progress_encouragement_after_completion():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, _, proactive = build_managers(temp_dir)

        reminder = proactive.task_completed_reminder()

        assert reminder["type"] == "progress_encourage"
        assert "顺手记录" in reminder["text"]


def test_idle_nudge_requires_pending_plan_and_idle_time():
    with tempfile.TemporaryDirectory() as temp_dir:
        _, growth, proactive = build_managers(
            temp_dir,
            idle_threshold_seconds=300,
        )
        growth.add_task("继续推进项目")

        early = proactive.check(
            idle_seconds=299,
            seconds_since_interaction=121,
            allowed_types={"idle_nudge"},
        )
        ready = proactive.check(
            idle_seconds=300,
            seconds_since_interaction=300,
            allowed_types={"idle_nudge"},
        )

        assert early is None
        assert ready["type"] == "idle_nudge"


def test_settings_dialog_saves_proactive_options():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = Path(temp_dir) / "pet_config.json"
        dialog = SettingsDialog(config_file=config_file)
        dialog.proactive_enabled.setChecked(False)
        dialog.proactive_interval_minutes.setValue(18)
        dialog.evening_review_enabled.setChecked(False)
        dialog.idle_nudge_enabled.setChecked(True)

        original_information = QMessageBox.information
        QMessageBox.information = lambda *args, **kwargs: QMessageBox.StandardButton.Ok
        try:
            dialog._save()
        finally:
            QMessageBox.information = original_information

        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["proactive_enabled"] is False
        assert saved["proactive_interval_minutes"] == 18
        assert saved["evening_review_enabled"] is False
        assert saved["idle_nudge_enabled"] is True
        dialog.close()
        app.processEvents()


if __name__ == "__main__":
    test_pending_plan_generates_plan_pending()
    test_plan_without_action_generates_no_action_log()
    test_evening_generates_review_time_once_per_day()
    test_cooldown_prevents_repeated_reminder()
    test_disabled_and_paused_manager_do_not_remind()
    test_empty_growth_data_is_safe()
    test_progress_encouragement_after_completion()
    test_idle_nudge_requires_pending_plan_and_idle_time()
    test_settings_dialog_saves_proactive_options()
    print("proactive manager tests passed")
