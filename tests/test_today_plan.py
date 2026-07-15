import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from frontend.pet_app import ChatWindow
from modules.growth import ActionLogStore, GrowthLogStore, GrowthService
from modules.today_plan import TodayPlanStore


class TestClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_missing_file_is_created():
    with tempfile.TemporaryDirectory() as temp_dir:
        plan_file = Path(temp_dir) / "today_plan.json"
        store = TodayPlanStore(plan_file, now_provider=lambda: datetime(2026, 7, 15, 9, 0, 0))

        assert plan_file.exists()
        assert store.data["date"] == "2026-07-15"
        assert store.tasks() == []


def test_add_complete_and_review():
    with tempfile.TemporaryDirectory() as temp_dir:
        plan_file = Path(temp_dir) / "today_plan.json"
        store = TodayPlanStore(plan_file, now_provider=lambda: datetime(2026, 7, 15, 10, 30, 0))

        first = store.add_task("学习机器学习30分钟")
        second = store.add_task("整理RoxyPlan文档")
        assert first["id"] == 1
        assert second["id"] == 2
        assert len(store.tasks()) == 2

        completed, changed = store.complete_by_id(1)
        assert changed is True
        assert completed["done"] is True
        assert completed["done_at"] is not None

        completed_by_title, changed_by_title = store.complete_by_title("整理RoxyPlan文档")
        assert changed_by_title is True
        assert completed_by_title["id"] == 2
        assert store.review() == {"total": 2, "done": 2, "pending": 0}


def test_chat_commands_add_view_complete_and_review():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        store = TodayPlanStore(
            Path(temp_dir) / "today_plan.json",
            now_provider=lambda: datetime(2026, 7, 15, 14, 0, 0),
        )
        clock = lambda: datetime(2026, 7, 15, 14, 0, 0)
        growth_service = GrowthService(
            plan_store=store,
            action_store=ActionLogStore(Path(temp_dir) / "action_log.json", clock),
            growth_store=GrowthLogStore(Path(temp_dir) / "growth_log.json", clock),
            now_provider=clock,
        )
        window = ChatWindow(growth_service=growth_service)
        window.transcript.clear()

        window.input_box.setText("今日计划：学习机器学习30分钟")
        window.send_message()
        window.input_box.setText("添加计划：整理RoxyPlan文档")
        window.send_message()
        window.input_box.setText("查看计划")
        window.send_message()
        transcript = window.transcript.toPlainText()
        assert "1. [待完成] 学习机器学习30分钟" in transcript
        assert "2. [待完成] 整理RoxyPlan文档" in transcript

        window.input_box.setText("完成计划1")
        window.send_message()
        assert store.tasks()[0]["done"] is True
        window.input_box.setText("我完成了整理RoxyPlan文档")
        window.send_message()
        assert store.tasks()[1]["done"] is True

        window.input_box.setText("今日复盘")
        window.send_message()
        review_text = window.transcript.toPlainText()
        assert "计划 2 件，完成 2 件，未完成 0 件" in review_text
        window.close()
        app.processEvents()


def test_date_change_archives_previous_day():
    with tempfile.TemporaryDirectory() as temp_dir:
        clock = TestClock(datetime(2026, 7, 15, 23, 50, 0))
        store = TodayPlanStore(Path(temp_dir) / "today_plan.json", now_provider=clock)
        store.add_task("整理今日笔记")

        clock.value = datetime(2026, 7, 16, 0, 10, 0)
        assert store.tasks() == []
        assert store.data["date"] == "2026-07-16"
        assert len(store.data["history"]) == 1
        assert store.data["history"][0]["date"] == "2026-07-15"
        assert store.data["history"][0]["tasks"][0]["title"] == "整理今日笔记"


if __name__ == "__main__":
    test_missing_file_is_created()
    test_add_complete_and_review()
    test_chat_commands_add_view_complete_and_review()
    test_date_change_archives_previous_day()
    print("today plan tests passed")
