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

from frontend.growth_dialog import GrowthDialog
from frontend.pet_app import ChatWindow
from modules.growth import ActionLogStore, GrowthLogStore, GrowthService
from modules.today_plan import TodayPlanStore


class TestClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def make_service(directory: Path, clock: TestClock) -> GrowthService:
    return GrowthService(
        plan_store=TodayPlanStore(directory / "today_plan.json", now_provider=clock),
        action_store=ActionLogStore(directory / "action_log.json", now_provider=clock),
        growth_store=GrowthLogStore(directory / "growth_log.json", now_provider=clock),
        now_provider=clock,
    )


def test_missing_files_are_created():
    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        service = make_service(directory, TestClock(datetime(2026, 7, 15, 9, 0, 0)))

        assert (directory / "today_plan.json").exists()
        assert (directory / "action_log.json").exists()
        assert (directory / "growth_log.json").exists()
        assert service.plan_store.tasks() == []
        assert service.action_store.records_for_date() == []
        assert service.growth_store.entries() == []


def test_plan_add_complete_and_delete():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = make_service(
            Path(temp_dir), TestClock(datetime(2026, 7, 15, 10, 0, 0))
        )

        first = service.plan_store.add_task("学习机器学习30分钟")
        second = service.plan_store.add_task("整理RoxyPlan文档")
        completed, changed = service.plan_store.complete_by_id(int(first["id"]))
        removed = service.plan_store.delete_by_id(int(second["id"]))

        assert changed is True
        assert completed["done"] is True
        assert removed["title"] == "整理RoxyPlan文档"
        assert len(service.plan_store.tasks()) == 1


def test_action_record_and_review():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = make_service(
            Path(temp_dir), TestClock(datetime(2026, 7, 15, 14, 35, 20))
        )
        task = service.plan_store.add_task("测试成长闭环")
        service.plan_store.complete_by_id(int(task["id"]))
        record = service.action_store.add_record("完成了成长模块测试", source="chat")

        review = service.generate_review()

        assert record == {
            "date": "2026-07-15",
            "time": "14:35:20",
            "content": "完成了成长模块测试",
            "source": "chat",
        }
        assert review["total"] == 1
        assert review["done"] == 1
        assert review["pending"] == 0
        assert review["actions"] == ["完成了成长模块测试"]
        assert "计划 1 件，完成 1 件，未完成 0 件" in review["text"]


def test_growth_log_overwrites_same_day():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = make_service(
            Path(temp_dir), TestClock(datetime(2026, 7, 15, 20, 0, 0))
        )
        service.save_today_review()
        service.action_store.add_record("补充了一条行动", source="manual")
        second = service.save_today_review()

        entries = service.growth_store.entries()
        assert len(entries) == 1
        assert entries[0]["date"] == "2026-07-15"
        assert second["review"]["actions"] == ["补充了一条行动"]


def test_chat_growth_commands():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        service = make_service(
            Path(temp_dir), TestClock(datetime(2026, 7, 15, 16, 10, 0))
        )
        window = ChatWindow(growth_service=service)
        window.transcript.clear()

        for command in (
            "添加计划：整理测试文档",
            "记录：完成了第一轮验证",
            "完成计划1",
            "今日复盘",
            "保存今日复盘",
            "查看成长日志",
            "删除计划1",
        ):
            window.input_box.setText(command)
            window.send_message()

        transcript = window.transcript.toPlainText()
        assert "完成了第一轮验证" in transcript
        assert "计划 1 件，完成 1 件，未完成 0 件" in transcript
        assert "今天的复盘已经保存到成长日志了" in transcript
        assert "2026-07-15" in transcript
        assert service.plan_store.tasks() == []
        window.close()
        app.processEvents()


def test_growth_dialog_smoke():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        service = make_service(
            Path(temp_dir), TestClock(datetime(2026, 7, 15, 18, 0, 0))
        )
        dialog = GrowthDialog(service)
        dialog.plan_input.setText("完成成长面板检查")
        dialog.add_plan()
        dialog.action_input.setText("验证了面板输入")
        dialog.add_action()
        dialog.generate_review()

        assert dialog.plan_table.rowCount() == 1
        assert dialog.action_list.count() == 1
        assert "计划 1 件" in dialog.review_text.toPlainText()
        dialog.close()
        app.processEvents()


if __name__ == "__main__":
    test_missing_files_are_created()
    test_plan_add_complete_and_delete()
    test_action_record_and_review()
    test_growth_log_overwrites_same_day()
    test_chat_growth_commands()
    test_growth_dialog_smoke()
    print("growth logic tests passed")
