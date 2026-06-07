from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ChatWindow(QMainWindow):
    """Interactive chat window for the V0.2 desktop pet prototype."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RoxyPlan Chat")
        self.resize(420, 520)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("RoxyPlan")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)

        status = QLabel("V0.2 prototype: local fixed reply only. AI is not connected.")
        status.setAlignment(Qt.AlignCenter)
        status.setWordWrap(True)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("Chat records will appear here.")

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message and press Enter...")
        self.input_box.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send_message)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        input_layout.addWidget(self.input_box)
        input_layout.addWidget(self.send_button)

        layout.addWidget(title)
        layout.addWidget(status)
        layout.addWidget(self.transcript)
        layout.addLayout(input_layout)

        self.setCentralWidget(container)
        self.add_message("Roxy", "你好，我是Roxy。")

    def send_message(self) -> None:
        # V0.2 只做本地固定回复，不接入 AI、数据库或 memory.json。
        user_text = self.input_box.text().strip()
        if not user_text:
            return

        self.add_message("You", user_text)
        self.input_box.clear()

        # 临时回复逻辑：用户发送任意内容后，Roxy 固定回复这一句。
        self.add_message("Roxy", "你好，我是Roxy。")

    def add_message(self, sender: str, message: str) -> None:
        # 每条消息都显示本地时间戳，方便后续升级为真实聊天记录。
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.transcript.append(f"[{timestamp}] {sender}: {message}")
        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        # 新消息出现后自动滚动到底部。
        scroll_bar = self.transcript.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())


class RoxyPet(QWidget):
    """A tiny draggable, always-on-top desktop pet window."""

    def __init__(self) -> None:
        super().__init__()
        self.chat_window: Optional[ChatWindow] = None
        self._drag_start: Optional[QPoint] = None

        self.setWindowTitle("RoxyPlan Pet")
        self.setFixedSize(132, 132)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.OpenHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setBrush(QColor("#F9D7E8"))
        painter.setPen(QPen(QColor("#7B3F61"), 3))
        painter.drawEllipse(16, 12, 100, 100)

        painter.setBrush(QColor("#7B3F61"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(46, 48, 9, 12)
        painter.drawEllipse(78, 48, 9, 12)

        painter.setPen(QPen(QColor("#7B3F61"), 3))
        painter.drawArc(54, 62, 28, 20, 200 * 16, 140 * 16)

        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#7B3F61"), 2))
        painter.drawRoundedRect(34, 98, 64, 22, 10, 10)

        painter.setPen(QColor("#7B3F61"))
        painter.setFont(QFont("Arial", 9, QFont.Bold))
        painter.drawText(34, 98, 64, 22, Qt.AlignCenter, "Roxy")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_start = self._event_global_pos(event) - self.frameGeometry().topLeft()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None and event.buttons() & Qt.LeftButton:
            self.move(self._event_global_pos(event) - self._drag_start)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_start = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.open_chat_window()
            event.accept()

    def _event_global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def open_chat_window(self) -> None:
        if self.chat_window is None:
            self.chat_window = ChatWindow()

        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RoxyPlan")

    pet = RoxyPet()
    pet.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
