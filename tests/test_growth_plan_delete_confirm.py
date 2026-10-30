"""Detailed growth panel: plan deletion confirmation stays on the confirmed target.

All cases run offline with disposable stores; no visible window and no model call.
"""

import json
import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from frontend.growth_dialog import GrowthDialog
from modules.growth import ActionLogStore, GrowthLogStore, GrowthService
from modules.growth_manager import GrowthManager
from modules.today_plan import TodayPlanStore
from scripts.preview_growth import business_files


NOW = datetime(2026, 10, 13, 18, 0, 0)


def _clock() -> datetime:
    return NOW


def _manager(root: Path) -> GrowthManager:
    return GrowthManager(root, now_provider=_clock)


def _open_dialog(app, service) -> GrowthDialog:
    dialog = GrowthDialog(service)
    dialog.show()
    app.processEvents()
    return dialog


def _click_delete(dialog: GrowthDialog, row: int = 0) -> None:
    button = dialog.plan_table.cellWidget(row, 2).findChild(QPushButton)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def _titles(service) -> list:
    return [str(task.get("title", "")) for task in service.tasks()]


def _row_of(dialog: GrowthDialog, title: str) -> int:
    for row in range(dialog.plan_table.rowCount()):
        if title in dialog.plan_table.item(row, 1).text():
            return row
    raise AssertionError(f"Plan row not found: {title}")


def _in_table(dialog: GrowthDialog, title: str) -> bool:
    return any(title in dialog.plan_table.item(row, 1).text()
               for row in range(dialog.plan_table.rowCount()))


def _answer_question(prompts, answer=QMessageBox.StandardButton.Yes, on_ask=None):
    def question(_parent, _title, text, *_args, **_kwargs):
        prompts.append(text)
        if on_ask is not None:
            on_ask()
        return answer
    return question


def _record_information(notices):
    def information(_parent, _title, text, *_args, **_kwargs):
        notices.append(text)
        return QMessageBox.StandardButton.Ok
    return information


def test_cancel_shows_real_title_and_writes_nothing(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "private"
    manager = _manager(root)
    manager.add_task("整理删除确认")
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question",
        _answer_question(prompts, answer=QMessageBox.StandardButton.No),
    )
    dialog = _open_dialog(app, manager)
    try:
        before = business_files(root)
        _click_delete(dialog)
        assert len(prompts) == 1
        assert "整理删除确认" in prompts[0]
        assert _titles(manager) == ["整理删除确认"]
        assert _in_table(dialog, "整理删除确认")
        assert business_files(root) == before
    finally:
        dialog.close()
        app.processEvents()


def test_confirm_deletes_only_the_confirmed_plan(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path / "private")
    manager.add_task("计划甲")
    manager.add_task("计划乙")
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", _answer_question(prompts))
    dialog = _open_dialog(app, manager)
    try:
        _click_delete(dialog, _row_of(dialog, "计划甲"))
        assert "计划甲" in prompts[0]
        assert _titles(manager) == ["计划乙"]
        assert dialog.plan_table.rowCount() == 1
        assert _in_table(dialog, "计划乙")
    finally:
        dialog.close()
        app.processEvents()


def test_replacement_during_confirmation_is_not_deleted(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path / "private")
    original = manager.add_task("原来的计划")
    notices = []

    def replace_while_asking():
        # The plan changes after the prompt appeared but before the user answers.
        manager.plan_store.delete_by_id(int(original["id"]))
        replacement = manager.plan_store.add_task("替代的计划")
        assert int(replacement["id"]) == int(original["id"])

    monkeypatch.setattr(
        QMessageBox, "question", _answer_question([], on_ask=replace_while_asking)
    )
    monkeypatch.setattr(QMessageBox, "information", _record_information(notices))
    dialog = _open_dialog(app, manager)
    try:
        _click_delete(dialog)
        assert _titles(manager) == ["替代的计划"]
        assert _in_table(dialog, "替代的计划")
        assert notices and "没有删除任何内容" in notices[0]
    finally:
        dialog.close()
        app.processEvents()


def test_same_title_timestamp_replacement_is_not_deleted(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path / "private")
    original = manager.add_task("同名计划")
    notices = []

    def replace():
        manager.delete_by_id(int(original["id"]))
        new = manager.add_task("同名计划")
        assert new["created_at"] == original["created_at"]
        assert new["uid"] != original["uid"]

    monkeypatch.setattr(QMessageBox, "question", _answer_question([], on_ask=replace))
    monkeypatch.setattr(QMessageBox, "information", _record_information(notices))
    dialog = _open_dialog(app, manager)
    try:
        _click_delete(dialog)
        assert _titles(manager) == ["同名计划"] and notices
    finally:
        dialog.close()
        app.processEvents()


def test_stale_row_is_reported_honestly_without_asking(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path / "private")
    original = manager.add_task("刷新前的计划")
    asked = []
    notices = []
    monkeypatch.setattr(QMessageBox, "question", _answer_question(asked))
    monkeypatch.setattr(QMessageBox, "information", _record_information(notices))
    dialog = _open_dialog(app, manager)
    try:
        manager.plan_store.delete_by_id(int(original["id"]))
        manager.plan_store.add_task("刷新后的计划")
        _click_delete(dialog)
        assert asked == []
        assert _titles(manager) == ["刷新后的计划"]
        assert _in_table(dialog, "刷新后的计划")
        assert notices and "没有删除任何内容" in notices[0]
    finally:
        dialog.close()
        app.processEvents()


def test_legacy_plan_file_without_uid_falls_back_to_stable_identity(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = _manager(tmp_path / "private")
    task = manager.add_task("旧文件计划")
    data = json.loads(manager.plan_file.read_text(encoding="utf-8"))
    for day in data["days"].values():
        for item in day["tasks"]:
            if item.get("uid") == task["uid"]:
                del item["uid"]
    manager.plan_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    prompts = []
    monkeypatch.setattr(QMessageBox, "question", _answer_question(prompts))
    dialog = _open_dialog(app, manager)
    try:
        _click_delete(dialog)
        assert "旧文件计划" in prompts[0]
        assert _titles(manager) == []
    finally:
        dialog.close()
        app.processEvents()


def test_uid_less_legacy_plan_store_still_deletes_after_confirmation(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    directory = tmp_path / "legacy"
    service = GrowthService(
        plan_store=TodayPlanStore(directory / "today_plan.json", now_provider=_clock),
        action_store=ActionLogStore(directory / "action_log.json", now_provider=_clock),
        growth_store=GrowthLogStore(directory / "growth_log.json", now_provider=_clock),
        now_provider=_clock,
    )
    service.plan_store.add_task("无 UID 计划")
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", _answer_question(prompts))
    dialog = _open_dialog(app, service)
    try:
        _click_delete(dialog)
        assert "无 UID 计划" in prompts[0]
        assert service.plan_store.tasks() == []
    finally:
        dialog.close()
        app.processEvents()
