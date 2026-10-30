"""首次打开聊天时的一次性提示条：轻量、可关闭、非模态。

只发出 ``example_requested`` 与 ``dismissed`` 两个信号：不读取私人数据、不保存
设置、不执行工具，也不发送消息。是否已经看过、什么时候写入“已看”标志，由聊天
窗口负责；提示条本身不接触配置文件。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton


# 只提示当前确实支持的三类入口，并遵守“今天”的新增边界。
HINT_TEXT = "可以自由聊天，也可以安排今天、记录行动、做复盘。"

FIRST_USE_HINT_QSS = """
    QFrame#firstUseHint {
        background: #F3EDFF;
        border: 1px solid #D8CCFF;
        border-radius: 10px;
    }
    QLabel#firstUseHintText {
        color: #475777;
        font-family: 'Microsoft YaHei';
        font-size: 12px;
    }
    QPushButton#firstUseHintExample {
        background: #FFFFFF;
        color: #6677B8;
        border: 1px solid #D8CCFF;
        border-radius: 8px;
        padding: 4px 10px;
        font-family: 'Microsoft YaHei';
        font-size: 12px;
    }
    QPushButton#firstUseHintExample:hover {
        background: #EDE2FF;
    }
    QToolButton#firstUseHintClose {
        color: #8A85A0;
        background: transparent;
        border: none;
        padding: 2px 6px;
        font-size: 14px;
    }
    QToolButton#firstUseHintClose:hover {
        color: #475777;
    }
"""


class FirstUseHint(QFrame):
    """一次性的新手提示；由聊天窗口决定显示时机与是否记住已看过。"""

    example_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("firstUseHint")
        self.setStyleSheet(FIRST_USE_HINT_QSS)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        self.text_label = QLabel(HINT_TEXT, self)
        self.text_label.setObjectName("firstUseHintText")
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label, 1)

        self.example_button = QPushButton("看看示例", self)
        self.example_button.setObjectName("firstUseHintExample")
        self.example_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.example_button.setToolTip(
            "打开操作说明；点击示例只会填入输入框，不会自动发送。"
        )
        self.example_button.clicked.connect(self.example_requested.emit)
        layout.addWidget(self.example_button)

        self.close_button = QToolButton(self)
        self.close_button.setObjectName("firstUseHintClose")
        self.close_button.setText("×")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("关闭提示；之后可从互动或设置中的“操作说明”重看。")
        self.close_button.setAccessibleName("关闭首次使用提示")
        self.close_button.clicked.connect(self.dismissed.emit)
        layout.addWidget(self.close_button)

    def hint_text(self) -> str:
        return self.text_label.text()
