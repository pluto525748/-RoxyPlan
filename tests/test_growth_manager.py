import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.growth_manager import GrowthManager
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from frontend.growth_dialog import GrowthDialog
from frontend.pet_app import ChatWindow


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_missing_files_are_created():
    with tempfile.TemporaryDirectory() as temp_dir:
        private_dir = Path(temp_dir) / "private"
        GrowthManager(private_dir, now_provider=lambda: datetime(2026, 7, 15, 9, 0, 0))

        for name in ("today_plan.json", "action_log.json", "growth_log.json"):
            path = private_dir / name
            assert path.exists()
            assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def test_plan_add_complete_and_delete():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = GrowthManager(
            Path(temp_dir) / "private",
            now_provider=lambda: datetime(2026, 7, 15, 10, 30, 0),
        )
        first = manager.add_task("学习机器学习30分钟")
        second = manager.add_task("整理RoxyPlan文档")

        completed, changed = manager.complete_by_id(int(first["id"]))
        removed = manager.delete_by_id(int(second["id"]))

        assert changed is True
        assert completed["done"] is True
        assert removed["title"] == "整理RoxyPlan文档"
        assert manager.review() == {"total": 1, "done": 1, "pending": 0}


def test_action_and_rule_review():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = GrowthManager(
            Path(temp_dir) / "private",
            now_provider=lambda: datetime(2026, 7, 15, 14, 20, 5),
        )
        done_task = manager.add_task("完成成长模块")
        manager.add_task("整理展示文档")
        manager.complete_by_id(int(done_task["id"]))
        action = manager.add_record("验证了成长面板", source="chat")

        review = manager.generate_review()

        assert action["date"] == "2026-07-15"
        assert action["time"] == "14:20:05"
        assert action["source"] == "chat"
        assert review["total"] == 2
        assert review["done"] == 1
        assert review["pending"] == 1
        assert review["action_count"] == 1
        assert review["completed_tasks"] == ["完成成长模块"]
        assert review["pending_tasks"] == ["整理展示文档"]
        assert "今天你计划了 2 件事" in review["text"]


def test_save_growth_log_overwrites_same_day():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = GrowthManager(
            Path(temp_dir) / "private",
            now_provider=lambda: datetime(2026, 7, 15, 20, 0, 0),
        )
        manager.save_today_review()
        manager.add_record("晚间补充记录")
        manager.save_today_review()

        entries = manager.entries()
        assert len(entries) == 1
        assert entries[0]["date"] == "2026-07-15"
        assert entries[0]["review"]["actions"] == ["晚间补充记录"]


def test_data_is_isolated_by_date():
    with tempfile.TemporaryDirectory() as temp_dir:
        clock = FakeClock(datetime(2026, 7, 15, 23, 50, 0))
        manager = GrowthManager(Path(temp_dir) / "private", now_provider=clock)
        manager.add_task("第一天计划")
        manager.add_record("第一天行动")

        clock.value = datetime(2026, 7, 16, 8, 0, 0)
        assert manager.tasks() == []
        assert manager.records_for_date() == []
        manager.add_task("第二天计划")

        assert manager.tasks("2026-07-15")[0]["title"] == "第一天计划"
        assert manager.records_for_date("2026-07-15")[0]["content"] == "第一天行动"
        assert manager.tasks("2026-07-16")[0]["title"] == "第二天计划"


def test_invalid_json_falls_back_without_crashing():
    with tempfile.TemporaryDirectory() as temp_dir:
        private_dir = Path(temp_dir) / "private"
        private_dir.mkdir(parents=True)
        (private_dir / "today_plan.json").write_text("not json", encoding="utf-8")

        manager = GrowthManager(
            private_dir,
            now_provider=lambda: datetime(2026, 7, 15, 9, 0, 0),
        )
        assert manager.tasks() == []
        assert manager.add_task("降级后仍可使用")["id"] == 1


def test_chat_commands_use_manager_without_llm():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = GrowthManager(
            Path(temp_dir) / "private",
            now_provider=lambda: datetime(2026, 7, 15, 16, 0, 0),
        )
        window = ChatWindow(growth_service=manager)
        startup_text = window.transcript.toPlainText()
        assert "欢迎回来" in startup_text or "今天也一起加油吧" in startup_text
        assert "我还记得" not in startup_text
        assert "已加载" not in startup_text
        window.transcript.clear()

        def fail_if_model_called(_user_text):
            raise AssertionError("Growth commands must not call the LLM")

        window.start_ai_reply = fail_if_model_called
        for command in (
            "今日计划：学习机器学习30分钟",
            "行动记录：完成了舞蹈模块测试",
            "完成计划1",
            "今日复盘",
            "保存今日复盘",
            "查看成长日志",
            "删除计划1",
            "确认删除计划1",
        ):
            window.input_box.setText(command)
            window.send_message()

        transcript = window.transcript.toPlainText()
        assert "学习机器学习30分钟" in transcript
        assert "完成了舞蹈模块测试" in transcript
        assert "今天你计划了 1 件事" in transcript
        assert len(manager.entries()) == 1
        assert manager.tasks() == []
        window.close()
        app.processEvents()


def test_growth_dialog_complete_workflow():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = GrowthManager(
            Path(temp_dir) / "private",
            now_provider=lambda: datetime(2026, 7, 15, 17, 0, 0),
        )
        dialog = GrowthDialog(manager)
        dialog.show()
        app.processEvents()

        dialog.plan_input.setText("完成成长面板验收")
        dialog.add_plan()
        dialog.plan_input.setText("临时计划")
        dialog.add_plan()
        dialog.plan_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.delete_plan(2)

        dialog.action_input.setText("完成了面板操作检查")
        dialog.add_action()
        dialog.generate_review()

        original_information = QMessageBox.information
        QMessageBox.information = lambda *args, **kwargs: QMessageBox.StandardButton.Ok
        try:
            dialog.save_review()
        finally:
            QMessageBox.information = original_information

        assert manager.tasks()[0]["done"] is True
        assert len(manager.tasks()) == 1
        assert len(manager.records_for_date()) == 1
        assert "完成了 1 件" in dialog.review_text.toPlainText()
        assert len(manager.entries()) == 1
        assert dialog.growth_log_list.count() == 1
        dialog.close()
        app.processEvents()


if __name__ == "__main__":
    test_missing_files_are_created()
    test_plan_add_complete_and_delete()
    test_action_and_rule_review()
    test_save_growth_log_overwrites_same_day()
    test_data_is_isolated_by_date()
    test_invalid_json_falls_back_without_crashing()
    test_chat_commands_use_manager_without_llm()
    test_growth_dialog_complete_workflow()
    print("growth manager tests passed")
