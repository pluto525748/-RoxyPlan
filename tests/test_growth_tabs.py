"""Detailed growth UI contracts, using offline disposable business stores."""

from datetime import datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from frontend.growth_dialog import GrowthDialog
from modules.growth_manager import GrowthManager
from scripts.preview_growth import build_preview, business_files


def test_tabs_and_read_refresh_preserve_drafts_and_generated_review(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "private"
    manager = GrowthManager(root, now_provider=lambda: datetime(2026, 10, 12, 18))
    manager.add_task("尚未完成的计划")
    dialog = GrowthDialog(manager)
    try:
        dialog.show()
        app.processEvents()
        dialog.plan_input.setText("尚未提交的计划 <标题>")
        dialog.action_input.setText("尚未提交的行动 & 内容")
        dialog.action_minutes.setValue(37)
        dialog.generate_review()
        draft = dialog.review_text.toPlainText()
        assert draft and manager.entries() == []
        before = business_files(root)
        for tab in (1, 2, 0):
            dialog.tabs.setCurrentIndex(tab)
            dialog.refresh_all()
            app.processEvents()
        dialog.hide()
        dialog.show()
        app.processEvents()
        assert [dialog.tabs.tabText(i) for i in range(3)] == ["计划与行动", "今日复盘", "成长日志"]
        assert dialog.plan_input.text() == "尚未提交的计划 <标题>"
        assert dialog.action_input.text() == "尚未提交的行动 & 内容"
        assert dialog.action_minutes.value() == 37
        assert dialog.review_text.toPlainText() == draft
        assert "尚未保存" in dialog.review_status.text()
        assert business_files(root) == before
    finally:
        dialog.close()
        app.processEvents()


def test_existing_controls_submit_duration_and_save_latest_facts(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = GrowthManager(tmp_path / "private", now_provider=lambda: datetime(2026, 10, 12, 18))
    dialog = GrowthDialog(manager)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
    )
    try:
        dialog.show()
        app.processEvents()
        dialog.plan_input.setText("回车添加的计划")
        QTest.keyClick(dialog.plan_input, Qt.Key.Key_Return)
        assert len(manager.tasks()) == 1
        dialog.plan_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        assert manager.tasks()[0]["done"]
        dialog.generate_review()
        draft = dialog.review_text.toPlainText()
        dialog.action_input.setText("实际推进了学习")
        dialog.action_minutes.setValue(35)
        QTest.mouseClick(dialog.add_action_button, Qt.MouseButton.LeftButton)
        assert manager.records_for_date()[0]["duration_minutes"] == 35
        assert dialog.review_text.toPlainText() == draft
        dialog.tabs.setCurrentIndex(1)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        saved = manager.entries()[0]["review"]
        assert "实际推进了学习" in saved["text"]
        assert dialog.review_text.toPlainText() == saved["text"]
        assert "已保存复盘" in dialog.review_status.text()
        assert not dialog._review_is_preview
        dialog.tabs.setCurrentIndex(0)
        delete_button = dialog.plan_table.cellWidget(0, 2).findChild(QPushButton)
        QTest.mouseClick(delete_button, Qt.MouseButton.LeftButton)
        assert manager.tasks() == []
    finally:
        dialog.close()
        app.processEvents()


@pytest.mark.parametrize("scenario", ["populated", "empty", "long"])
def test_isolated_preview_scenarios_and_resizing(tmp_path, scenario):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / scenario
    dialog = build_preview(root, scenario)
    try:
        dialog.show()
        app.processEvents()
        before = business_files(root)
        if scenario == "empty":
            assert dialog.plan_table.rowCount() == 0
            assert dialog.action_list.count() == 0
            assert not dialog.review_text.toPlainText()
            assert "还没有" in dialog.growth_log_list.item(0).text()
        else:
            assert dialog.plan_table.rowCount() > 0
            assert dialog.action_list.count() == 2
            assert dialog.month_selector.count() == 2
            assert dialog.growth_log_list.count() >= 1
            assert "今天你计划了" in dialog.review_text.toPlainText()
        if scenario == "long":
            assert len(dialog.review_text.toPlainText()) > 3000
            assert '<标签> & “引号”' in dialog.review_text.toPlainText()
            wide_height = dialog.plan_table.rowHeight(0)
            dialog.resize(500, 520)
            app.processEvents()
            assert dialog.plan_table.rowHeight(0) >= wide_height
            dialog.tabs.setCurrentIndex(1)
            app.processEvents()
            assert dialog.review_text.verticalScrollBar().maximum() > 0
        dialog.tabs.setCurrentIndex(2)
        for index in range(dialog.month_selector.count()):
            dialog.month_selector.setCurrentIndex(index)
            app.processEvents()
        dialog.refresh_all()
        assert business_files(root) == before
    finally:
        dialog.close()
        app.processEvents()
