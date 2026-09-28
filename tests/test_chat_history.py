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

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from PySide6.QtWidgets import QApplication
from frontend import pet_app
from frontend.pet_app import ChatWindow


def make_manager(root: Path, enabled: bool = True) -> ChatHistoryManager:
    return ChatHistoryManager(
        root / "private",
        now_provider=lambda: datetime(2026, 7, 15, 10, 30, 0),
        enabled=enabled,
    )


def test_missing_files_are_created_and_messages_are_saved():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        assert manager.history_file.exists()
        assert manager.summary_file.exists()

        session = manager.new_session()
        manager.add_message(session["session_id"], "user", "你好", intent="chat")
        manager.add_message(session["session_id"], "assistant", "你好。")

        restored = make_manager(Path(temp_dir))
        latest = restored.latest_session()
        assert latest is not None
        assert latest["message_count"] == 2
        assert [item["role"] for item in latest["messages"]] == ["user", "assistant"]


def test_new_switch_delete_and_title_generation():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        first = manager.new_session()
        manager.add_message(first["session_id"], "user", "讨论 RoxyPlan 聊天历史设计")
        manager.add_message(first["session_id"], "assistant", "好。")
        manager.add_message(first["session_id"], "user", "继续")
        second = manager.new_session()

        titled = manager.get_session(first["session_id"])
        assert titled["title"].startswith("讨论 RoxyPlan")
        assert manager.switch_session(first["session_id"])["session_id"] == first["session_id"]
        assert manager.delete_session(second["session_id"]) is True
        assert manager.get_session(second["session_id"]) is None


def test_clear_requires_confirmation_and_disabled_storage_does_not_write_messages():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = make_manager(root)
        session = manager.new_session()
        manager.add_message(session["session_id"], "user", "保留")
        assert manager.clear_history(confirmed=False) is False
        assert len(manager.sessions()) == 1

        manager.set_enabled(False)
        assert manager.add_message(session["session_id"], "user", "不写入") is None
        disk_data = json.loads(manager.history_file.read_text(encoding="utf-8"))
        assert len(disk_data["sessions"][0]["messages"]) == 1

        assert manager.clear_history(confirmed=True) is True
        assert manager.sessions() == []


def test_summary_is_separate_and_rule_fallback_works():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        session = manager.new_session()
        session_id = session["session_id"]
        manager.add_message(session_id, "user", "我决定继续完善聊天历史")
        manager.add_message(session_id, "assistant", "好，我们分步推进。")
        manager.add_message(session_id, "user", "下次继续测试会话切换")

        summary = manager.generate_rule_summary(session_id)
        assert "主要讨论" in summary
        assert "下次可继续" in summary
        assert manager.save_summary(session_id, summary) is True
        assert manager.get_summary(session_id) == summary

        history_data = json.loads(manager.history_file.read_text(encoding="utf-8"))
        summary_data = json.loads(manager.summary_file.read_text(encoding="utf-8"))
        assert summary_data["summaries"][session_id]["summary"] == summary
        assert "memories" not in history_data


def test_summary_threshold_uses_message_count_or_character_count():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        session = manager.new_session()
        session_id = session["session_id"]
        for index in range(31):
            manager.add_message(session_id, "user", f"消息 {index}")
        assert manager.should_summarize(session_id, message_threshold=30) is True

        summary = manager.generate_rule_summary(session_id)
        manager.save_summary(session_id, summary)
        assert manager.should_summarize(session_id, message_threshold=30) is False

        long_session = manager.new_session()
        manager.add_message(long_session["session_id"], "user", "内容" * 100)
        assert manager.should_summarize(
            long_session["session_id"],
            message_threshold=30,
            character_threshold=100,
        ) is True


def test_chat_window_restores_sessions_and_clear_needs_second_command():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = make_manager(root)
        original_memory_file = pet_app.MEMORY_FILE
        original_config_file = pet_app.CONFIG_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        pet_app.CONFIG_FILE = root / "config.json"
        growth = GrowthManager(root / "growth")
        try:
            first = ChatWindow(
                chat_history_manager=manager,
                growth_service=growth,
            )
            first.add_message("You", "讨论本地会话恢复")
            first.add_message("Roxy", "好，我们继续。")
            session_id = first.current_session_id
            first.close()

            second = ChatWindow(
                chat_history_manager=manager,
                growth_service=growth,
            )
            assert second.current_session_id == session_id
            assert "讨论本地会话恢复" in second.transcript.toPlainText()

            second.input_box.setText("新建对话")
            second.send_message()
            assert second.current_session_id != session_id
            assert len(manager.sessions()) == 2

            second.input_box.setText("清空聊天记录")
            second.send_message()
            assert len(manager.sessions()) == 2
            second.input_box.setText("确认清空聊天记录")
            second.send_message()
            assert len(manager.sessions()) == 1
            assert "已经清空" in second.transcript.toPlainText()
            second.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.CONFIG_FILE = original_config_file


if __name__ == "__main__":
    test_missing_files_are_created_and_messages_are_saved()
    test_new_switch_delete_and_title_generation()
    test_clear_requires_confirmation_and_disabled_storage_does_not_write_messages()
    test_summary_is_separate_and_rule_fallback_works()
    test_summary_threshold_uses_message_count_or_character_count()
    test_chat_window_restores_sessions_and_clear_needs_second_command()
    print("chat history tests passed")
