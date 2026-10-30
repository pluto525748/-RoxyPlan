"""首次使用提示与原操作说明入口：只做发现引导，不改变业务行为。

所有配置与会话都位于 pytest 临时目录；不读取正式用户配置，不联网，也不显示
可见桌面（由隔离 runner 使用 offscreen）。
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

import frontend.pet_app as pet_app
import frontend.settings_dialog as settings_module
import frontend.first_use_hint as hint_module
from frontend.first_use_hint import HINT_TEXT
from frontend.usage_dialog import UsageDialog
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from scripts.preview_chat import build_preview_window


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _config_file(root: Path) -> Path:
    return root / "data" / "pet_config.json"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_first_open_shows_hint_once_and_restart_does_not_repeat(tmp_path) -> None:
    app = _app()
    root = tmp_path / "instance"
    window, _ = build_preview_window(root)
    try:
        assert not window.first_use_hint.isVisible()
        window.show()
        app.processEvents()
        assert window.first_use_hint.isVisible()
        assert window.first_use_hint.hint_text() == HINT_TEXT
        assert "安排今天" in HINT_TEXT and "明天" not in HINT_TEXT
        # 非模态：输入和发送没有被提示条挡住。
        assert not window.isModal()
        assert window.input_box.isEnabled() and window.send_button.isEnabled()
        assert settings_module.first_use_hint_seen(_config_file(root)) is True

        window.first_use_hint.close_button.click()
        app.processEvents()
        assert not window.first_use_hint.isVisible()
        window.hide()
        window.show()
        app.processEvents()
        assert not window.first_use_hint.isVisible()
    finally:
        window.close()
        app.processEvents()

    restarted, _ = build_preview_window(root)
    try:
        restarted.show()
        app.processEvents()
        assert not restarted.first_use_hint.isVisible()
        # 独立文字入口移除，原互动菜单中的说明仍可重看。
        assert not hasattr(restarted, "usage_link_button")
        assert restarted.usage_action in restarted.interaction_menu.actions()
    finally:
        restarted.close()
        app.processEvents()


def test_hint_flag_belongs_to_each_instance_config(tmp_path) -> None:
    app = _app()
    first, _ = build_preview_window(tmp_path / "one")
    second, _ = build_preview_window(tmp_path / "two")
    try:
        first.show()
        app.processEvents()
        assert first.first_use_hint.isVisible()
        assert settings_module.first_use_hint_seen(_config_file(tmp_path / "one"))
        assert not settings_module.first_use_hint_seen(_config_file(tmp_path / "two"))
        second.show()
        app.processEvents()
        assert second.first_use_hint.isVisible()
    finally:
        first.close()
        second.close()
        app.processEvents()


def test_mark_seen_only_appends_the_flag_and_preserves_hidden_settings(tmp_path) -> None:
    config = tmp_path / "pet_config.json"
    existing = {
        "pet_scale": 1.25,
        "model_name": "qwen3:4b",
        "study_reminder_seconds_test": 7,
        "proactive_check_seconds_test": 11,
        "hidden_probe": {"nested": [1, "二"]},
    }
    _write_json(config, existing)
    assert settings_module.first_use_hint_seen(config) is False
    assert settings_module.mark_first_use_hint_seen(config) is True
    assert json.loads(config.read_text(encoding="utf-8")) == {
        **existing,
        "first_use_hint_seen": True,
    }
    assert settings_module.first_use_hint_seen(config) is True


def test_missing_config_gets_a_single_flag_and_corrupt_config_is_untouched(tmp_path) -> None:
    missing = tmp_path / "nested" / "pet_config.json"
    assert settings_module.mark_first_use_hint_seen(missing) is True
    assert json.loads(missing.read_text(encoding="utf-8")) == {
        "first_use_hint_seen": True
    }

    corrupt = tmp_path / "broken" / "pet_config.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{ not json", encoding="utf-8")
    before = corrupt.read_bytes()
    assert settings_module.first_use_hint_seen(corrupt) is False
    assert settings_module.mark_first_use_hint_seen(corrupt) is False
    assert corrupt.read_bytes() == before


def test_example_entry_opens_existing_help_and_never_sends(tmp_path) -> None:
    app = _app()
    window, _ = build_preview_window(tmp_path / "app")
    history = window.chat_history_manager.history_file
    try:
        window.show()
        app.processEvents()
        before = history.read_bytes()
        transcript_before = window.transcript.toPlainText()

        window.first_use_hint.example_button.click()
        app.processEvents()
        usage = window.usage_dialog
        assert isinstance(usage, UsageDialog) and usage.isVisible()
        assert not window.first_use_hint.isVisible()
        assert "可以自由聊天" in usage.subtitle.text()
        assert "填入聊天输入框" in usage.subtitle.text()

        example = usage.example_texts()[0]
        usage.example_buttons[0].click()
        app.processEvents()
        assert window.input_box.text() == example
        assert window.reply_thread is None
        assert history.read_bytes() == before
        assert window.transcript.toPlainText() == transcript_before

        # 原互动菜单复用同一个说明窗口，没有第二份帮助页。
        window.usage_action.trigger()
        app.processEvents()
        assert window.usage_dialog is usage and usage.isVisible()
    finally:
        if window.usage_dialog is not None:
            window.usage_dialog.close()
        window.close()
        app.processEvents()


def test_example_keeps_the_draft_until_replacement_is_confirmed(tmp_path, monkeypatch) -> None:
    app = _app()
    window, _ = build_preview_window(tmp_path / "app")
    try:
        window.show()
        app.processEvents()
        window.first_use_hint.example_button.click()
        app.processEvents()
        usage = window.usage_dialog
        example = usage.example_texts()[0]
        before = window.chat_history_manager.history_file.read_bytes()

        window.input_box.setText("还没发送的草稿")
        monkeypatch.setattr(
            pet_app.QMessageBox, "question",
            lambda *_args: QMessageBox.StandardButton.No,
        )
        usage.example_buttons[0].click()
        app.processEvents()
        assert window.input_box.text() == "还没发送的草稿"

        monkeypatch.setattr(
            pet_app.QMessageBox, "question",
            lambda *_args: QMessageBox.StandardButton.Yes,
        )
        usage.example_buttons[0].click()
        app.processEvents()
        assert window.input_box.text() == example
        assert window.reply_thread is None
        assert window.chat_history_manager.history_file.read_bytes() == before
    finally:
        if window.usage_dialog is not None:
            window.usage_dialog.close()
        window.close()
        app.processEvents()


def test_history_view_hides_hint_and_never_becomes_a_send_entry(tmp_path) -> None:
    app = _app()
    window, old_id = build_preview_window(tmp_path / "app")
    try:
        window.show()
        app.processEvents()
        assert window.first_use_hint.isVisible()
        history = window.chat_history_manager.history_file
        before = history.read_bytes()

        assert window.switch_chat_session(old_id)
        app.processEvents()
        assert not window.first_use_hint.isVisible()
        assert not window.input_box.isEnabled()
        assert not window.send_button.isEnabled()
        window.input_box.setText("不能写入昨天")
        window.send_message()
        app.processEvents()
        assert history.read_bytes() == before

        window.return_today_session()
        app.processEvents()
        assert window.input_box.isEnabled()
        # 本次运行已经展示过，回到今天也不重复弹出。
        assert not window.first_use_hint.isVisible()
    finally:
        window.close()
        app.processEvents()


def test_hint_preserves_chat_layout_without_standalone_help_link(tmp_path) -> None:
    app = _app()
    window, _ = build_preview_window(tmp_path / "app")
    try:
        window.resize(460, 560)
        window.show()
        app.processEvents()
        assert (window.width(), window.height()) == (460, 560)
        assert (window.minimumWidth(), window.minimumHeight()) == (360, 420)
        assert window.first_use_hint.isVisible()
        assert not hasattr(window, "usage_link_button")
        assert window.usage_action in window.interaction_menu.actions()
        assert window.first_use_hint.height() <= 96
        assert window.transcript.viewport().height() >= 150

        # 与既有 UI 契约相同的三条相对位置：输入在发送上方，正文在页眉下方，
        # 页脚装饰在输入下方。
        assert window.input_box.geometry().bottom() < window.send_button.geometry().top()
        assert (
            window.header_decoration.geometry().bottom()
            < window.transcript.parentWidget().geometry().top()
        )
        assert (
            window.footer_decoration.geometry().top()
            > window.input_box.geometry().bottom()
        )

        window.resize(360, 420)
        app.processEvents()
        assert (window.width(), window.height()) == (360, 420)
        assert window.input_box.isEnabled() and window.send_button.isEnabled()
        assert window.input_box.geometry().bottom() < window.send_button.geometry().top()
        assert window.usage_action.isEnabled()
        assert window.transcript.viewport().height() >= 60
    finally:
        window.close()
        app.processEvents()


def test_settings_tooltips_explain_confusing_items_without_new_cards(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    monkeypatch.setattr(
        settings_module, "SecretStore", lambda _root: SecretStore(tmp_path)
    )
    monkeypatch.setattr(
        settings_module, "ModelUsageStore", lambda _root: ModelUsageStore(tmp_path)
    )
    config = tmp_path / "pet_config.json"
    _write_json(
        config,
        {
            "pet_asset_path": "assets/pet/default.png",
            "model_name": "deepseek-chat",
        },
    )
    dialog = settings_module.SettingsDialog(
        config_file=config, knowledge_dir=tmp_path / "knowledge"
    )
    try:
        dialog.show()
        app.processEvents()
        titles = [label.text() for label in dialog.findChildren(QLabel, "cardTitle")]
        assert titles == ["桌宠外观", "陪伴与提醒", "模型连接", "隐私与数据", "帮助与退出"]
        labels = {label.text(): label for label in dialog.findChildren(QLabel, "rowLabel")}
        for name in ("连接测试", "主动提醒间隔", "保存聊天历史", "导出内测反馈"):
            assert labels[name].toolTip().strip()
        assert "不会开始对话" in labels["连接测试"].toolTip()
        assert "不会自动删除" in labels["保存聊天历史"].toolTip()
        assert "没有后台上传" in labels["导出内测反馈"].toolTip()
        # 隐藏的技术配置没有变成新的可见项。
        assert labels["连接测试"].toolTip() != labels["主动提醒间隔"].toolTip()
    finally:
        dialog.close()
        app.processEvents()


def test_saving_settings_does_not_reset_a_flag_written_while_open(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    monkeypatch.setattr(
        settings_module, "SecretStore", lambda _root: SecretStore(tmp_path)
    )
    monkeypatch.setattr(
        settings_module, "ModelUsageStore", lambda _root: ModelUsageStore(tmp_path)
    )
    config = tmp_path / "pet_config.json"
    _write_json(
        config,
        {
            "pet_asset_path": "assets/pet/default.png",
            "model_name": "deepseek-chat",
            "default_model": "deepseek-chat",
            "complex_model": "deepseek-reasoner",
        },
    )
    dialog = settings_module.SettingsDialog(
        config_file=config, knowledge_dir=tmp_path / "knowledge"
    )
    try:
        # 设置读入后、保存前，聊天窗口记录了“已看过”；保存不能把它覆盖回 False。
        assert settings_module.mark_first_use_hint_seen(config) is True
        monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
        dialog.always_on_top.setChecked(True)
        dialog._save()
        loaded = json.loads(config.read_text(encoding="utf-8"))
        assert loaded["first_use_hint_seen"] is True
        assert loaded["pet_always_on_top"] is True
    finally:
        dialog.close()
        app.processEvents()


def test_usage_help_keeps_its_single_signal_and_shows_the_discovery_sentence() -> None:
    app = _app()
    dialog = UsageDialog()
    try:
        assert "可以自由聊天" in dialog.subtitle.text()
        dialog.set_chat_destination_ready()
        assert "可以自由聊天" in dialog.subtitle.text()
        assert "填入聊天输入框" in dialog.subtitle.text()
        assert [
            name for name, value in vars(UsageDialog).items() if isinstance(value, Signal)
        ] == ["example_selected"]
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_hint_module_has_no_business_or_file_dependencies() -> None:
    assert _imported_modules(Path(hint_module.__file__)) == {
        "__future__",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
    }
    tree = ast.parse(Path(hint_module.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "exec", "eval", "compile", "__import__"}


def _imported_modules(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules
