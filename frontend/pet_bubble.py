from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class PetBubble(QWidget):
    """Small auto-hiding desktop bubble that follows the pet window."""

    def __init__(self) -> None:
        super().__init__()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.label.setStyleSheet(
            """
            QLabel {
                background: rgba(252, 254, 255, 238);
                color: #263142;
                border: 2px solid #7bb7d8;
                border-radius: 12px;
                padding: 9px 12px;
                font-size: 13px;
                line-height: 1.35;
            }
            """
        )
        self.label.setMinimumWidth(150)
        self.label.setMaximumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)

    def show_message(self, text: str, pet_rect: QRect, duration_ms: int = 6000) -> None:
        self.label.setText(text)
        self.adjustSize()
        self._move_near_pet(pet_rect)
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def _move_near_pet(self, pet_rect: QRect) -> None:
        screen = QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

        margin = 10
        bubble_width = self.width()
        bubble_height = self.height()

        x = pet_rect.center().x() - bubble_width // 2
        y = pet_rect.top() - bubble_height - margin

        if y < available.top() + margin:
            x = pet_rect.left() - bubble_width - margin
            y = pet_rect.top() + 12

        if x < available.left() + margin:
            x = pet_rect.right() + margin
        if x + bubble_width > available.right() - margin:
            x = available.right() - bubble_width - margin

        y = max(available.top() + margin, min(y, available.bottom() - bubble_height - margin))
        self.move(x, y)
