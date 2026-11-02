"""互动下拉菜单：只含原有两项动作，只发信号，不新增动画或计时器。

检查在 offscreen 下运行，不显示窗口、不占用桌面。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QApplication

import frontend.interaction_menu as menu_module
from frontend.interaction_menu import (
    ACTION_DANCE,
    ACTION_ENCOURAGE,
    INTERACTION_ACTIONS,
    InteractionMenu,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_menu_only_offers_the_existing_two_actions() -> None:
    app = _app()
    menu = InteractionMenu()
    try:
        assert menu.text() == "互动"
        assert menu.action_labels() == ["跳舞一下", "立即鼓励我"]
        assert list(menu.action_items) == [ACTION_DANCE, ACTION_ENCOURAGE]
        assert [action.text() for action in menu.menu().actions()] == [
            "跳舞一下",
            "立即鼓励我",
        ]
        assert [action_id for action_id, _label in INTERACTION_ACTIONS] == [
            ACTION_DANCE,
            ACTION_ENCOURAGE,
        ]
    finally:
        menu.deleteLater()
        app.processEvents()


def test_triggering_an_action_emits_its_id_once() -> None:
    app = _app()
    menu = InteractionMenu()
    received = []
    menu.action_selected.connect(received.append)
    try:
        menu.action_items[ACTION_DANCE].trigger()
        assert received == [ACTION_DANCE]
        menu.action_items[ACTION_ENCOURAGE].trigger()
        assert received == [ACTION_DANCE, ACTION_ENCOURAGE]
    finally:
        menu.deleteLater()
        app.processEvents()


def test_menu_adds_no_timer_animation_or_extra_channel() -> None:
    app = _app()
    menu = InteractionMenu()
    try:
        assert [
            name for name, value in vars(InteractionMenu).items() if isinstance(value, Signal)
        ] == ["action_selected"]
        assert not menu.findChildren(QTimer)
        assert menu.menu().actions()
    finally:
        menu.deleteLater()
        app.processEvents()


def test_menu_module_stays_a_signalling_widget() -> None:
    """菜单组件不引入桌宠、聊天或业务模块，也不创建动画与计时器。"""
    path = Path(menu_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert modules == {
        "__future__",
        "string",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "frontend.settings_dialog",
    }
    created = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not created & {"start_dance", "setInterval", "start", "show_random_encouragement"}
