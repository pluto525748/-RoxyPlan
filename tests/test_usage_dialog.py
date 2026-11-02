"""Small offline checks for help content and the menu's reusable entry."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QWidget

import frontend.desktop_pet as pet_module
import frontend.usage_dialog as usage_module
from frontend.usage_dialog import OPERATION_EXAMPLES, UsageDialog


def _imported_modules(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_help_is_read_only_and_has_precise_operation_examples():
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    text = dialog.content.toPlainText()
    for phrase in (
        "展示长期记忆", "忘记：我喜欢清淡饮食", "完整原文", "确认", "取消",
        "开始学习", "今日成长面板", "勾选", "返回今天", "导出内测反馈", "没有自动上传",
    ):
        assert phrase in text
    assert "编号用于阅读，不是删除命令" in text
    assert dialog.content.isReadOnly()
    assert not dialog.content.openExternalLinks()
    assert not dialog.content.openLinks()
    assert not dialog.isModal()
    buttons = dialog.findChild(QDialogButtonBox)
    assert len(buttons.buttons()) == 1
    assert buttons.button(QDialogButtonBox.Close).text() == "关闭"
    dialog.deleteLater()
    app.processEvents()


def test_pet_menu_has_only_three_entries_without_constructing_business_services():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    menu, actions = pet_module.DesktopPet._create_context_menu(owner)
    assert [action.text() for action in menu.actions()] == ["打开聊天", "唤醒", "睡眠"]
    assert set(actions) == {"chat", "wake", "sleep"}
    owner.deleteLater()
    app.processEvents()


def test_help_open_reuses_instance_without_reading_or_writing_data(monkeypatch):
    calls = []

    class HelpStub:
        def __init__(self, parent):
            calls.append(("created", parent))

        def show(self):
            calls.append("show")

        def raise_(self):
            calls.append("raise")

        def activateWindow(self):
            calls.append("activate")

    monkeypatch.setattr(pet_module, "UsageDialog", HelpStub)
    owner = SimpleNamespace(usage_dialog=None, record_interaction=lambda: None)
    pet_module.DesktopPet.open_usage_dialog(owner)
    first = owner.usage_dialog
    pet_module.DesktopPet.open_usage_dialog(owner)
    assert owner.usage_dialog is first
    assert sum(isinstance(item, tuple) and item[0] == "created" for item in calls) == 1
    assert calls.count("show") == 2


# ------------------------------------------------------- 按钮、示例与信号接口


def test_help_offers_short_examples_for_each_supported_operation():
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    try:
        assert [button.text() for button in dialog.operation_buttons] == [
            "查看今日计划",
            "请求学习建议",
            "记录实际行动",
            "今日复盘",
            "展示长期记忆",
        ]
        assert dialog.operation_index == 0
        assert dialog.example_texts() == ["查看今天的计划。", "查看昨天的计划。"]
        assert dialog.example_title.text() == "查看今日计划 · 可以这样说"
        # 边界提示与当前能力一致：聊天新增只支持今天的计划。
        assert "只支持今天" in dialog.example_note.text()
        assert dialog.status.text() == ""
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_every_operation_example_is_a_sentence_with_a_capability_note():
    for title, note, examples in OPERATION_EXAMPLES:
        assert title and note
        assert examples
        for sentence in examples:
            assert sentence.endswith(("。", "？"))
            assert "\n" not in sentence


def test_clicking_an_example_emits_the_exact_sentence_once_per_click():
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    received = []
    dialog.example_selected.connect(received.append)
    try:
        assert dialog.example_buttons[1].text() == "查看昨天的计划。"
        dialog.example_buttons[1].click()
        assert received == ["查看昨天的计划。"]
        dialog.example_buttons[1].click()
        assert received == ["查看昨天的计划。", "查看昨天的计划。"]
        assert dialog.status.text() == "已选中：查看昨天的计划。"
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_switching_operation_replaces_examples_and_clears_the_selection():
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    try:
        dialog.example_buttons[0].click()
        assert dialog.status.text() == "已选中：查看今天的计划。"
        dialog.operation_buttons[3].click()
        assert dialog.operation_index == 3
        assert dialog.operation_buttons[3].isChecked()
        assert not dialog.operation_buttons[0].isChecked()
        assert dialog.example_texts() == ["今日复盘。", "查看今日复盘。"]
        assert dialog.status.text() == ""
        assert [
            button.text() for button in dialog.findChildren(QPushButton, "exampleButton")
        ] == ["今日复盘。", "查看今日复盘。"]
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_help_keeps_the_full_text_collapsed_until_it_is_asked_for():
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    try:
        # 原文保留，只是默认收起，不强迫每次读全文。
        assert "内测与使用边界" in dialog.content.toPlainText()
        assert dialog.is_details_visible() is False
        assert dialog.details_toggle.isChecked() is False
        dialog.details_toggle.click()
        assert dialog.details_toggle.isChecked() is True
        assert dialog.is_details_visible() is True
        dialog.details_toggle.click()
        assert dialog.is_details_visible() is False
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_help_click_only_emits_and_never_reaches_business_state(tmp_path):
    """点击帮助的唯一出口是 example_selected：没有数据根、后台线程或计时器。"""
    app = QApplication.instance() or QApplication([])
    dialog = UsageDialog()
    received = []
    dialog.example_selected.connect(received.append)
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
        for path in tmp_path.rglob("*")
    }
    try:
        for index in range(len(OPERATION_EXAMPLES)):
            dialog.operation_buttons[index].click()
            for button in list(dialog.example_buttons):
                button.click()
        expected = [
            sentence
            for _title, _note, examples in OPERATION_EXAMPLES
            for sentence in examples
        ]
        assert received == expected
        assert dialog.status.text() == f"已选中：{expected[-1]}"
        assert [
            name for name, value in vars(UsageDialog).items() if isinstance(value, Signal)
        ] == ["example_selected"]
        assert not dialog.findChildren(QTimer)
        assert {
            str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
            for path in tmp_path.rglob("*")
        } == before
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_help_module_stays_a_read_only_ui_component():
    """说明窗口不得引入聊天、桌宠或业务模块，也不做文件访问。"""
    assert _imported_modules(Path(usage_module.__file__)) == {
        "__future__",
        "html",
        "string",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "frontend.settings_dialog",
    }
    tree = ast.parse(Path(usage_module.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "exec", "eval", "compile", "__import__"}
