"""Focused chat presentation contracts, using complete isolated app stores."""

import os
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QApplication

from frontend.chat_history_dialog import ChatHistoryDialog
from scripts.preview_chat import build_preview_window, exercise_preview


def test_chat_sample_send_copy_resize_and_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    window, old_id = build_preview_window(tmp_path / "app")
    output = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts" / "chat-ui"
    try:
        window.show()
        app.processEvents()
        report = exercise_preview(window, old_id, output)
        assert report["offline_reply_calls"] == 2
        assert report["selection_copy"] and report["history_unchanged"]
        assert window.size().width() == 460 and window.size().height() == 560
        assert window.input_box.geometry().bottom() < window.send_button.geometry().top()
        assert window.header_decoration.geometry().bottom() < window.transcript.parentWidget().geometry().top()
        assert window.footer_decoration.geometry().top() > window.input_box.geometry().bottom()
        assert "    缩进的这一行也应保留。" in window.transcript.toPlainText()
    finally:
        window.close()
        app.processEvents()


def test_daily_history_display_keeps_stored_title_and_messages(tmp_path):
    app = QApplication.instance() or QApplication([])
    window, old_id = build_preview_window(tmp_path / "app")
    manager = window.chat_history_manager
    old = manager.get_session(old_id)
    original = manager.history_file.read_bytes()
    dialog = ChatHistoryDialog(manager, window.switch_chat_session, window)
    try:
        titles = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
        assert f"魔法修行日记 · {old['business_date']}" in titles
        assert manager.history_file.read_bytes() == original
        assert manager.get_session(old_id)["title"] == old["title"]
        window.switch_chat_session(old_id)
        assert "魔法修行日记" in window.session_title_label.text()
        assert "历史对话 · 只读" in window.session_title_label.text()
        avatar = window.transcript.document().resource(
            QTextDocument.ResourceType.ImageResource, QUrl("roxy-avatar")
        )
        assert isinstance(avatar, QImage) and not avatar.isNull()
        assert manager.history_file.read_bytes() == original
    finally:
        dialog.close()
        window.close()
        app.processEvents()


def test_growth_menu_delegates_to_both_existing_controller_entries(tmp_path):
    app = QApplication.instance() or QApplication([])
    window, _ = build_preview_window(tmp_path / "app")
    calls = []
    # Exercise the real lightweight panel and its signal, retaining the
    # controller-delegation contract that used to live in the growth menu.
    window.open_today_growth_dialog()
    panel = window.today_growth_dialog
    window.pet_controller = SimpleNamespace(
        record_interaction=lambda: None,
        open_today_growth_dialog=lambda: (calls.append("today"), panel.show()),
        open_growth_dialog=lambda: calls.append("detailed"),
    )
    try:
        assert window.growth_button.menu() is None
        window.growth_button.click()
        assert calls == ["today"]
        assert panel.growth_manager is window.growth_service
        panel.details_button.click()
        assert calls == ["today", "detailed"]
    finally:
        panel.close()
        window.close()
        app.processEvents()
