"""Reusable compact "互动" dropdown for the existing pet actions.

只提供一个“互动”按钮和当前已有的两项动作（跳舞、鼓励），并且只发信号：
不创建新动画、计时器或动作管理器，也不自己调用桌宠窗口。
调用方把 ``action_selected`` 的取值接到原有的 ``DesktopPet.start_dance``
或鼓励行为上即可；标签与桌宠右键菜单保持一致。
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QToolButton

from frontend.settings_dialog import SETTINGS_COLORS


ACTION_DANCE = "dance"
ACTION_ENCOURAGE = "encourage"

INTERACTION_ACTIONS = (
    (ACTION_DANCE, "跳舞一下"),
    (ACTION_ENCOURAGE, "立即鼓励我"),
)


# 与桌宠右键菜单同一套配色；菜单样式在桌面窗口模块里另有一份，
# 这里不导入那个模块，避免为一个下拉菜单引入整个桌宠运行路径。
INTERACTION_QSS = Template("""
    QToolButton#interactionButton { background: $accent_soft; color: $accent_text;
        border: 1px solid $accent_border; border-radius: 8px; padding: 4px 10px;
        font-family: 'Microsoft YaHei'; font-size: 13px; }
    QToolButton#interactionButton:hover { background: $accent_soft_pressed; }
    QMenu { background: $card; color: $text; border: 1px solid $card_border;
        border-radius: 10px; padding: 3px; font-family: 'Microsoft YaHei';
        font-size: 13px; }
    QMenu::item { padding: 4px 16px; margin: 0px 3px; border-radius: 6px; }
    QMenu::item:selected { background: $accent_soft; color: $accent_text; }
""").substitute(SETTINGS_COLORS)


class InteractionMenu(QToolButton):
    """紧凑互动菜单；只发出动作 id，具体动作由调用方执行。"""

    # 参数为 INTERACTION_ACTIONS 中的动作 id，目前是 "dance" 或 "encourage"。
    action_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setText("互动")
        self.setObjectName("interactionButton")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(INTERACTION_QSS)

        menu = QMenu(self)
        menu.setStyleSheet(INTERACTION_QSS)
        self.action_items = {}
        for action_id, label in INTERACTION_ACTIONS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=action_id: self.action_selected.emit(value)
            )
            self.action_items[action_id] = action
        self.setMenu(menu)

    def action_labels(self):
        """当前菜单项文字（按展示顺序）。"""
        return [action.text() for action in self.action_items.values()]
