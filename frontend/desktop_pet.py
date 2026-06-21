from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Property, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from frontend.pet_actions import PetActionController
from frontend.pet_bubble import PetBubble


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_CONFIG_FILE = PROJECT_ROOT / "data" / "pet_config.json"
PET_TIPS_FILE = PROJECT_ROOT / "data" / "pet_tips.json"

DEFAULT_CONFIG: Dict[str, object] = {
    "pet_enabled": True,
    "pet_scale": 1.0,
    "pet_always_on_top": True,
    "pet_position": "bottom_right",
    "auto_tips_enabled": True,
    "auto_tips_min_minutes": 5,
    "auto_tips_max_minutes": 15,
    "test_mode": True,
    "study_reminder_seconds_test": 10,
    "sleep_seconds_test": 30,
    "study_reminder_minutes": 25,
    "sleep_minutes": 20,
    "use_pet_asset": True,
    "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
}

DEFAULT_TIPS: List[str] = [
    "今天也要稍微前进一点。",
    "先完成一个小任务，好吗？",
    "记得喝水。",
    "不要一直弯腰，活动一下肩膀。",
    "你不是没有进步，只是现在有点累。",
    "今天的 RoxyPlan 又往前走了一步。",
    "学习不用等状态，先做五分钟就好。",
    "要不要检查一下今天的计划？",
    "休息可以，但不要忘记回来。",
    "我会陪你慢慢完成这个项目。",
]

ENCOURAGEMENTS = [
    "很好，就这样一点点来。",
    "你已经启动了，这很重要。",
    "先别急，我们把下一步看清楚。",
    "今天也请相信自己的积累。",
]

STUDY_TIPS = [
    "先学习五分钟，我在旁边陪你。",
    "把任务拆小一点，现在只做第一步。",
    "检查一下今天最重要的一件事吧。",
]

PET_STATES = {"idle", "happy", "thinking", "study", "sleep"}


class DesktopPet(QWidget):
    """Chibi mage-teacher desktop pet with simple local interactions."""

    def __init__(self, chat_factory: Optional[Callable[[], QWidget]] = None) -> None:
        super().__init__()
        print("[PET] DesktopPet init", flush=True)
        self.chat_factory = chat_factory
        self.chat_window: Optional[QWidget] = None
        self.config = self._load_config()
        self.tips = self._load_tips()
        self._state = "idle"
        self._is_blinking = False
        self._drag_offset: Optional[QPoint] = None
        self._dragging = False
        self._visual_offset = QPoint(0, 0)
        self._body_tilt = 0.0
        self._wand_angle = 0.0
        self._body_scale = 1.0
        self._talk_nod = QPoint(0, 0)
        self._last_interaction_at = time.monotonic()
        self._is_sleeping = False
        self._base_size = QSize(168, 190)
        self._asset_pixmap: Optional[QPixmap] = None
        self._asset_path: Optional[Path] = None

        self.bubble = PetBubble()
        self.actions = PetActionController(self)
        self.auto_tip_timer = QTimer(self)
        self.auto_tip_timer.setSingleShot(True)
        self.auto_tip_timer.timeout.connect(self._show_auto_tip)
        self.study_reminder_timer = QTimer(self)
        self.study_reminder_timer.timeout.connect(self._trigger_study_reminder)
        self.sleep_check_timer = QTimer(self)
        self.sleep_check_timer.timeout.connect(self._check_sleep_timeout)

        self.setWindowTitle("RoxyPlan Desktop Pet")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.OpenHandCursor)
        self._resize_for_scale()
        self._apply_window_flags()

        if self.config.get("pet_position") == "bottom_right":
            QTimer.singleShot(0, self.move_to_bottom_right)

        self.actions.start_idle_breathing()
        self._schedule_next_auto_tip()
        self._start_study_reminder_timer()
        self.sleep_check_timer.start(1000)

    @property
    def state(self) -> str:
        return self._state

    def get_visual_offset(self) -> QPoint:
        return self._visual_offset

    def set_visual_offset(self, value: QPoint) -> None:
        self._visual_offset = value
        self.update()

    visualOffset = Property(QPoint, get_visual_offset, set_visual_offset)

    def get_body_tilt(self) -> float:
        return self._body_tilt

    def set_body_tilt(self, value: float) -> None:
        self._body_tilt = value
        self.update()

    bodyTilt = Property(float, get_body_tilt, set_body_tilt)

    def get_wand_angle(self) -> float:
        return self._wand_angle

    def set_wand_angle(self, value: float) -> None:
        self._wand_angle = value
        self.update()

    wandAngle = Property(float, get_wand_angle, set_wand_angle)

    def get_body_scale(self) -> float:
        return self._body_scale

    def set_body_scale(self, value: float) -> None:
        self._body_scale = value
        self.update()

    bodyScale = Property(float, get_body_scale, set_body_scale)

    def get_talk_nod(self) -> QPoint:
        return self._talk_nod

    def set_talk_nod(self, value: QPoint) -> None:
        self._talk_nod = value
        self.update()

    talkNod = Property(QPoint, get_talk_nod, set_talk_nod)

    def set_state(self, state: str) -> None:
        if state not in PET_STATES:
            raise ValueError(f"Unknown pet state: {state}")

        if state == "idle":
            self.setWindowOpacity(1.0)
            self.bodyScale = 1.0
            self.bodyTilt = 0.0
            self.wandAngle = 0.0
            self.talkNod = QPoint(0, 0)
            self.mark_state("idle")
            self.actions.start_idle_breathing()
        elif state == "happy":
            self.actions.click_happy()
        elif state == "thinking":
            self.actions.thinking()
        elif state == "study":
            self.actions.study_reminder()
        elif state == "sleep":
            self.actions.sleep()

    def start_thinking(self) -> None:
        self.record_interaction()
        print("[STATE] thinking start", flush=True)
        self.mark_state("thinking")
        self.actions.start_thinking()

    def stop_thinking(self) -> None:
        print("[STATE] thinking stop", flush=True)
        self.actions.stop_thinking()
        self.set_state("idle")

    def record_interaction(self) -> None:
        self._last_interaction_at = time.monotonic()
        if self._is_sleeping:
            self.wake()

    def wake(self) -> None:
        if not self._is_sleeping and self.state != "sleep":
            return

        print("[STATE] wake", flush=True)
        self._is_sleeping = False
        self.setWindowOpacity(1.0)
        self.visualOffset = QPoint(0, 0)
        self.bodyScale = 1.0
        self.set_state("idle")

    def mark_state(self, state: str) -> None:
        if state in PET_STATES:
            self._state = state
            self.update()

    def set_blinking(self, is_blinking: bool) -> None:
        self._is_blinking = is_blinking
        self.update()

    def show_bubble(self, text: str, duration_ms: Optional[int] = None, record_interaction: bool = True) -> None:
        if record_interaction:
            self.record_interaction()
        duration = duration_ms if duration_ms is not None else random.randint(5000, 8000)
        print(f"[TIP] show: {text}", flush=True)
        self.bubble.show_message(text, self.frameGeometry(), duration)
        self.actions.speaking_nod()

    def show_random_encouragement(self) -> None:
        self.show_bubble(random.choice(self.tips or ENCOURAGEMENTS))

    def show_study_tip(self) -> None:
        study_pool = [tip for tip in self.tips if "学习" in tip or "任务" in tip or "计划" in tip]
        self.show_bubble(random.choice(study_pool or STUDY_TIPS), record_interaction=False)

    def move_to_bottom_right(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 24, available.bottom() - self.height() - 24)

    def open_chat_window(self) -> None:
        self.record_interaction()
        if self.chat_factory is None:
            self.show_bubble("聊天窗口入口还没有准备好。")
            return

        if self.chat_window is None:
            try:
                self.chat_window = self.chat_factory(self)
            except TypeError:
                self.chat_window = self.chat_factory()

        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        open_chat_action = menu.addAction("打开聊天")
        study_action = menu.addAction("触发学习提醒")
        top_action = menu.addAction("取消置顶" if self.config.get("pet_always_on_top") else "保持置顶")
        menu.addSeparator()
        zoom_in_action = menu.addAction("放大")
        zoom_out_action = menu.addAction("缩小")
        hide_bubble_action = menu.addAction("隐藏气泡")
        menu.addSeparator()
        quit_action = menu.addAction("退出桌宠")

        chosen = menu.exec(event.globalPos())
        if chosen == open_chat_action:
            self.open_chat_window()
        elif chosen == study_action:
            self.set_state("study")
        elif chosen == top_action:
            self.toggle_always_on_top()
        elif chosen == zoom_in_action:
            self.change_scale(0.1)
        elif chosen == zoom_out_action:
            self.change_scale(-0.1)
        elif chosen == hide_bubble_action:
            self.bubble.hide()
        elif chosen == quit_action:
            QApplication.quit()

    def toggle_always_on_top(self) -> None:
        self.config["pet_always_on_top"] = not bool(self.config.get("pet_always_on_top"))
        self._save_config()
        self._apply_window_flags()
        self.show()

    def change_scale(self, delta: float) -> None:
        old_center = self.geometry().center()
        current = float(self.config.get("pet_scale", 1.0))
        self.config["pet_scale"] = round(min(1.8, max(0.65, current + delta)), 2)
        self._save_config()
        self._resize_for_scale()
        self.move(old_center - QPoint(self.width() // 2, self.height() // 2))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.record_interaction()
            self._drag_offset = self._event_global_pos(event) - self.frameGeometry().topLeft()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            next_pos = self._event_global_pos(event) - self._drag_offset
            if (next_pos - self.pos()).manhattanLength() > 3:
                self._dragging = True
            self.move(next_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            was_dragging = self._dragging
            self._drag_offset = None
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            if not was_dragging:
                self.record_interaction()
                self.set_state("happy")
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.open_chat_window()
            event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / self._base_size.width(), self.height() / self._base_size.height())
        painter.translate(self._visual_offset)
        painter.translate(self._talk_nod)

        center = QPointF(84, 100)
        painter.translate(center)
        painter.scale(self._body_scale, self._body_scale)
        painter.rotate(self._body_tilt)
        painter.translate(-center)

        if self._draw_asset_if_enabled(painter):
            return

        self._draw_shadow(painter)
        self._draw_staff(painter)
        self._draw_body(painter)
        self._draw_head_and_hair(painter)
        self._draw_hat(painter)
        self._draw_face(painter)

    def _draw_shadow(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(39, 50, 64, 46))
        painter.drawEllipse(QRectF(44, 166, 82, 12))

    def _draw_staff(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(130, 92)
        painter.rotate(self._wand_angle)
        painter.setPen(QPen(QColor("#6F4A2E"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(0, 58), QPointF(0, 0))
        painter.setPen(QPen(QColor("#E8F8FF"), 2))
        painter.setBrush(QColor("#8AD8FF"))
        painter.drawEllipse(QRectF(-8, -15, 16, 16))
        painter.setPen(QPen(QColor("#F4D36A"), 2))
        painter.drawLine(QPointF(-14, -7), QPointF(14, -7))
        painter.drawLine(QPointF(0, -21), QPointF(0, 7))
        painter.restore()

    def _draw_body(self, painter: QPainter) -> None:
        robe = QPainterPath()
        robe.moveTo(60, 92)
        robe.cubicTo(47, 113, 43, 145, 38, 168)
        robe.lineTo(130, 168)
        robe.cubicTo(125, 143, 121, 113, 108, 92)
        robe.closeSubpath()

        gradient = QLinearGradient(52, 92, 121, 168)
        gradient.setColorAt(0.0, QColor("#5D73C9"))
        gradient.setColorAt(1.0, QColor("#263A86"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#1E2B68"), 3))
        painter.drawPath(robe)

        painter.setPen(QPen(QColor("#BFD9FF"), 3))
        painter.drawLine(QPointF(84, 101), QPointF(84, 165))
        painter.drawLine(QPointF(58, 130), QPointF(111, 130))

        painter.setBrush(QColor("#E9F4FF"))
        painter.setPen(QPen(QColor("#1E2B68"), 2))
        painter.drawEllipse(QRectF(51, 116, 18, 14))
        painter.drawEllipse(QRectF(103, 116, 18, 14))

    def _draw_head_and_hair(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#78C7ED"))
        painter.drawEllipse(QRectF(45, 39, 78, 72))
        painter.drawRoundedRect(41, 67, 18, 56, 9, 9)
        painter.drawRoundedRect(109, 67, 18, 55, 9, 9)

        painter.setBrush(QColor("#FFE6D4"))
        painter.setPen(QPen(QColor("#7E5A55"), 2))
        painter.drawEllipse(QRectF(52, 45, 64, 66))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#7ACBF0"))
        for x, y, w, h in [(50, 44, 18, 48), (64, 37, 20, 52), (82, 37, 22, 54), (100, 45, 18, 45)]:
            painter.drawEllipse(QRectF(x, y, w, h))

    def _draw_hat(self, painter: QPainter) -> None:
        brim = QPainterPath()
        brim.moveTo(31, 48)
        brim.cubicTo(54, 33, 113, 33, 137, 48)
        brim.cubicTo(115, 59, 52, 59, 31, 48)

        painter.setBrush(QColor("#2C3477"))
        painter.setPen(QPen(QColor("#172052"), 3))
        painter.drawPath(brim)

        cone = QPainterPath()
        cone.moveTo(55, 43)
        cone.cubicTo(68, 12, 80, 4, 98, 9)
        cone.cubicTo(91, 25, 103, 31, 116, 43)
        cone.closeSubpath()

        painter.setBrush(QColor("#33409A"))
        painter.drawPath(cone)
        painter.setPen(QPen(QColor("#E7D36B"), 3))
        painter.drawLine(QPointF(63, 34), QPointF(108, 35))
        painter.setBrush(QColor("#F3DA67"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(84, 24, 8, 8))

    def _draw_face(self, painter: QPainter) -> None:
        eye_pen = QPen(QColor("#243055"), 3, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(eye_pen)
        if self._is_blinking or self._state == "sleep":
            painter.drawLine(QPointF(66, 75), QPointF(75, 75))
            painter.drawLine(QPointF(94, 75), QPointF(103, 75))
        else:
            painter.setBrush(QColor("#263D75"))
            painter.drawEllipse(QRectF(67, 68, 8, 12))
            painter.drawEllipse(QRectF(95, 68, 8, 12))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QRectF(70, 70, 2.5, 3))
            painter.drawEllipse(QRectF(98, 70, 2.5, 3))

        painter.setPen(QPen(QColor("#C77E83"), 2, Qt.SolidLine, Qt.RoundCap))
        if self._state == "happy":
            painter.drawArc(QRectF(75, 82, 20, 15), 200 * 16, 140 * 16)
        else:
            painter.drawLine(QPointF(79, 88), QPointF(91, 88))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(246, 157, 167, 90))
        painter.drawEllipse(QRectF(57, 82, 12, 7))
        painter.drawEllipse(QRectF(101, 82, 12, 7))

        painter.setPen(QPen(QColor("#263142"), 1))
        painter.setFont(QFont("Arial", 8, QFont.Bold))
        painter.drawText(QRectF(54, 146, 60, 16), Qt.AlignCenter, "Roxy")

    def _draw_asset_if_enabled(self, painter: QPainter) -> bool:
        if not self.config.get("use_pet_asset", False):
            return False

        pixmap = self._load_asset_pixmap()
        if pixmap is None or pixmap.isNull():
            return False

        self._draw_shadow(painter)
        target = self._asset_target_rect(pixmap)
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        if self._is_blinking or self._state == "sleep":
            painter.setPen(QPen(QColor("#243055"), 3, Qt.SolidLine, Qt.RoundCap))
            eye_y = target.top() + target.height() * 0.38
            painter.drawLine(QPointF(target.left() + target.width() * 0.43, eye_y), QPointF(target.left() + target.width() * 0.49, eye_y))
            painter.drawLine(QPointF(target.left() + target.width() * 0.58, eye_y), QPointF(target.left() + target.width() * 0.64, eye_y))
        return True

    def _asset_target_rect(self, pixmap: QPixmap) -> QRectF:
        source_width = max(1, pixmap.width())
        source_height = max(1, pixmap.height())
        source_ratio = source_width / source_height
        target_width = float(self._base_size.width())
        target_height = float(self._base_size.height())
        target_ratio = target_width / target_height

        if source_ratio > target_ratio:
            height = target_width / source_ratio
            return QRectF(0, target_height - height, target_width, height)

        width = target_height * source_ratio
        return QRectF((target_width - width) / 2, 0, width, target_height)

    def _load_asset_pixmap(self) -> Optional[QPixmap]:
        raw_path = str(self.config.get("pet_asset_path", "")).strip()
        if not raw_path:
            return None

        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path

        if self._asset_path == path and self._asset_pixmap is not None:
            return self._asset_pixmap

        pixmap = QPixmap(str(path))
        self._asset_path = path
        self._asset_pixmap = pixmap
        return pixmap

    def _event_global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _resize_for_scale(self) -> None:
        scale = float(self.config.get("pet_scale", 1.0))
        self.setFixedSize(
            max(100, int(self._base_size.width() * scale)),
            max(114, int(self._base_size.height() * scale)),
        )

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config.get("pet_always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _schedule_next_auto_tip(self) -> None:
        if not self.config.get("auto_tips_enabled", True):
            return

        min_minutes = int(self.config.get("auto_tips_min_minutes", 5))
        max_minutes = int(self.config.get("auto_tips_max_minutes", 15))
        if max_minutes < min_minutes:
            max_minutes = min_minutes

        self.auto_tip_timer.start(random.randint(min_minutes, max_minutes) * 60 * 1000)

    def _show_auto_tip(self) -> None:
        self.show_bubble(random.choice(self.tips or DEFAULT_TIPS), record_interaction=False)
        self._schedule_next_auto_tip()

    def _start_study_reminder_timer(self) -> None:
        interval_seconds = self._study_reminder_interval_seconds()
        self.study_reminder_timer.start(max(1, interval_seconds) * 1000)

    def _trigger_study_reminder(self) -> None:
        if self.state == "sleep":
            return

        print("[REMINDER] study reminder triggered", flush=True)
        self.actions.study_reminder()

    def _check_sleep_timeout(self) -> None:
        if self._is_sleeping or self.state == "thinking":
            return

        if time.monotonic() - self._last_interaction_at >= self._sleep_interval_seconds():
            self._is_sleeping = True
            print("[STATE] sleep", flush=True)
            self.actions.sleep()

    def _study_reminder_interval_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return int(self.config.get("study_reminder_seconds_test", 10))
        return int(self.config.get("study_reminder_minutes", 25)) * 60

    def _sleep_interval_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return int(self.config.get("sleep_seconds_test", 30))
        return int(self.config.get("sleep_minutes", 20)) * 60

    def _load_config(self) -> Dict[str, object]:
        if not PET_CONFIG_FILE.exists():
            self._write_json(PET_CONFIG_FILE, DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)

        try:
            with PET_CONFIG_FILE.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, json.JSONDecodeError):
            loaded = {}

        config = dict(DEFAULT_CONFIG)
        if isinstance(loaded, dict):
            config.update(loaded)
        return config

    def _save_config(self) -> None:
        self._write_json(PET_CONFIG_FILE, self.config)

    def _load_tips(self) -> List[str]:
        if not PET_TIPS_FILE.exists():
            print(f"[TIP] load failed: {PET_TIPS_FILE} does not exist; writing defaults", flush=True)
            self._write_json(PET_TIPS_FILE, DEFAULT_TIPS)
            print(f"[TIP] loaded {len(DEFAULT_TIPS)} tips", flush=True)
            return list(DEFAULT_TIPS)

        try:
            with PET_TIPS_FILE.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except OSError as exc:
            print(f"[TIP] load failed: {PET_TIPS_FILE}: {exc}", flush=True)
            print(f"[TIP] loaded {len(DEFAULT_TIPS)} default tips", flush=True)
            return list(DEFAULT_TIPS)
        except json.JSONDecodeError as exc:
            print(f"[TIP] load failed: {PET_TIPS_FILE}: invalid JSON: {exc}", flush=True)
            print(f"[TIP] loaded {len(DEFAULT_TIPS)} default tips", flush=True)
            return list(DEFAULT_TIPS)

        if not isinstance(loaded, list):
            print(f"[TIP] load failed: {PET_TIPS_FILE} is not a JSON list", flush=True)
            print(f"[TIP] loaded {len(DEFAULT_TIPS)} default tips", flush=True)
            return list(DEFAULT_TIPS)

        tips = [str(item) for item in loaded if str(item).strip()]
        if not tips:
            print(f"[TIP] load failed: {PET_TIPS_FILE} has no non-empty tips", flush=True)
            print(f"[TIP] loaded {len(DEFAULT_TIPS)} default tips", flush=True)
            return list(DEFAULT_TIPS)

        print(f"[TIP] loaded {len(tips)} tips", flush=True)
        return tips

    def _write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
