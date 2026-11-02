"""设置里的“操作说明”和“退出 Roxy”入口：只发信号，不自己打开窗口或退出。

检查在 offscreen 下运行，不显示窗口、不占用桌面；配置与密钥路径都是临时合成
内容，不读取正式用户数据。
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

import frontend.settings_dialog as settings_module
from frontend.usage_dialog import UsageDialog
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings_module, "SecretStore", lambda _root: SecretStore(tmp_path))
    monkeypatch.setattr(
        settings_module, "ModelUsageStore", lambda _root: ModelUsageStore(tmp_path)
    )
    config = tmp_path / "pet_config.json"
    config.write_text(
        json.dumps(
            {
                "pet_always_on_top": False,
                "pet_asset_path": "assets/pet/default.png",
                "model_name": "deepseek-chat",
            }
        ),
        encoding="utf-8",
    )
    dialog = settings_module.SettingsDialog(
        config_file=config, knowledge_dir=tmp_path / "knowledge"
    )
    return dialog, config


def _find_button(dialog, text: str):
    for button in dialog.findChildren(QPushButton):
        if button.text() == text:
            return button
    return None


def test_settings_expose_help_and_quit_entries(tmp_path, monkeypatch) -> None:
    app = _app()
    settings, _config = _settings(tmp_path, monkeypatch)
    try:
        help_button = _find_button(settings, "打开操作说明")
        quit_button = _find_button(settings, "退出 Roxy")
        assert help_button is not None and help_button.isEnabled()
        assert quit_button is not None and quit_button.isEnabled()

        requested = []
        settings.usage_help_requested.connect(lambda: requested.append("help"))
        existing = {id(widget) for widget in app.topLevelWidgets()}
        help_button.click()
        help_button.click()
        app.processEvents()
        assert requested == ["help", "help"]
        # 设置窗口只发信号，不自己打开帮助窗口。
        opened = [
            widget
            for widget in app.topLevelWidgets()
            if isinstance(widget, UsageDialog) and id(widget) not in existing
        ]
        assert opened == []
    finally:
        settings.close()
        app.processEvents()


def test_settings_module_only_emits_the_requests() -> None:
    """帮助与退出由调用方处理：设置模块不导入帮助窗口，也不自己退出程序。"""
    tree = ast.parse(Path(settings_module.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert "frontend.usage_dialog" not in modules
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "quit" not in called


def test_quit_entry_needs_confirmation_and_cancel_keeps_the_app_open(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    settings, config = _settings(tmp_path, monkeypatch)
    try:
        settings.show()
        app.processEvents()
        before = config.read_bytes()
        emitted = []
        saved = []
        settings.quit_requested.connect(lambda: emitted.append("quit"))
        settings.settings_saved.connect(saved.append)
        monkeypatch.setattr(settings, "_confirm_quit", lambda: False)
        _find_button(settings, "退出 Roxy").click()
        app.processEvents()
        assert emitted == []
        assert saved == []
        assert settings.isVisible()
        assert config.read_bytes() == before
    finally:
        settings.close()
        app.processEvents()


def test_confirmed_quit_only_emits_and_never_saves_or_resets_settings(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    settings, config = _settings(tmp_path, monkeypatch)
    try:
        before = config.read_bytes()
        emitted = []
        saved = []
        settings.quit_requested.connect(lambda: emitted.append("quit"))
        settings.settings_saved.connect(saved.append)
        monkeypatch.setattr(settings, "_confirm_quit", lambda: True)
        _find_button(settings, "退出 Roxy").click()
        app.processEvents()
        assert emitted == ["quit"]
        assert saved == []
        assert config.read_bytes() == before
    finally:
        settings.close()
        app.processEvents()


def test_quit_confirmation_asks_with_cancel_as_the_default(tmp_path, monkeypatch) -> None:
    app = _app()
    settings, _config = _settings(tmp_path, monkeypatch)
    prompts = []

    def fake_question(*args, **_kwargs):
        prompts.append(args)
        # 直接返回默认按钮：默认是“取消”就意味着按回车不会退出。
        return args[4]

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    try:
        assert settings._confirm_quit() is False
        assert len(prompts) == 1
        _parent, title, _text, buttons, default = prompts[0]
        assert title == "退出 Roxy"
        assert buttons == (
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        assert default == QMessageBox.StandardButton.No

        emitted = []
        settings.quit_requested.connect(lambda: emitted.append("quit"))
        settings.request_quit()
        app.processEvents()
        assert emitted == []
    finally:
        settings.close()
        app.processEvents()


def test_saving_settings_is_unchanged_by_the_new_entries(tmp_path, monkeypatch) -> None:
    app = _app()
    settings, config = _settings(tmp_path, monkeypatch)
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: notices.append(True))
    try:
        before = config.read_bytes()
        saved = []
        quit_events = []
        help_events = []
        settings.settings_saved.connect(saved.append)
        settings.quit_requested.connect(lambda: quit_events.append("quit"))
        settings.usage_help_requested.connect(lambda: help_events.append("help"))

        settings.always_on_top.setChecked(True)
        _find_button(settings, "保存").click()
        app.processEvents()

        assert len(saved) == 1
        assert saved[0]["pet_always_on_top"] is True
        assert notices == [True]
        assert config.read_bytes() != before
        assert json.loads(config.read_text(encoding="utf-8"))["pet_always_on_top"] is True
        assert quit_events == [] and help_events == []
    finally:
        settings.close()
        app.processEvents()
