"""Independent, noninteractive thinking indicator; owns only its dot timer."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class PetThinkingBubble(QWidget):
    placement_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("正在思考")
        self.timer = QTimer(self)
        self.timer.setInterval(360)
        self.timer.timeout.connect(self._tick)
        self.phase = 0
        self._can_show = True
        self.resize(68, 32)

    def start(self) -> None:
        if not self.timer.isActive():
            self.phase = 0
            self.timer.start()
        self.placement_requested.emit()
        if self._can_show:
            self.show()
        self.update()

    def stop(self) -> None:
        self.timer.stop()
        self.phase = 0
        self.hide()

    def _tick(self) -> None:
        self.phase = (self.phase + 1) % 3
        self.placement_requested.emit()
        self.update()

    def place(self, pet_rect: QRect, *, avoid_rect: QRect | None = None) -> None:
        screen = QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        scale = max(0.65, min(1.8, pet_rect.width() / 168))
        self.resize(round(68 * scale), round(32 * scale))
        w, h, gap = self.width(), self.height(), round(6 * scale)
        candidates = [
            QRect(pet_rect.center().x() - w // 2, pet_rect.top() - h - gap, w, h),
        ]
        if avoid_rect is not None and not avoid_rect.isEmpty():
            candidates.append(QRect(pet_rect.center().x() - w // 2, avoid_rect.top() - h - gap, w, h))
        candidates.extend((QRect(pet_rect.right() + gap, pet_rect.top(), w, h),
                           QRect(pet_rect.left() - w - gap, pet_rect.top(), w, h)))
        for rect in candidates:
            rect.moveLeft(max(area.left(), min(rect.left(), area.right() - w + 1)))
            rect.moveTop(max(area.top(), min(rect.top(), area.bottom() - h + 1)))
            if not rect.intersects(pet_rect) and (avoid_rect is None or not rect.intersects(avoid_rect)):
                self._can_show = True
                self.move(rect.topLeft())
                if self.timer.isActive():
                    self.show()
                return
        # No legal space: do not cover the ordinary message bubble.
        self._can_show = False
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 68, self.height() / 32)
        painter.setPen(QPen(QColor("#D8E1F0"), 1))
        painter.setBrush(QColor("#FFFDFA"))
        painter.drawRoundedRect(QRectF(1, 1, 66, 29), 13, 13)
        painter.setPen(Qt.NoPen)
        for index in range(3):
            painter.setBrush(QColor("#879AC6" if index <= self.phase else "#DFE3F1"))
            painter.drawEllipse(QRectF(18 + index * 12, 12, 6, 6))

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)
