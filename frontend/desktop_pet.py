from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Property, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from frontend.pet_action_manager import PetActionManager
from frontend.pet_actions import PetActionController
from frontend.pet_bubble import PetBubble
from frontend.growth_dialog import GrowthDialog
from frontend.settings_dialog import SettingsDialog
from modules.client_action_policy import ClientActionPolicy
from modules.growth_manager import GrowthManager
from modules.chat_history_manager import ChatHistoryManager
from modules.proactive_manager import ProactiveManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_CONFIG_FILE = PROJECT_ROOT / "data" / "pet_config.json"
PET_TIPS_FILE = PROJECT_ROOT / "data" / "pet_tips.json"
DANCE_FRAMES_DIR = PROJECT_ROOT / "assets" / "pet" / "dance"
DANCE_FRAME_DURATIONS_MS = (120, 110, 100, 110, 90, 140, 90, 140)
DANCE_LOOP_COUNT = 3

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
    "proactive_enabled": True,
    "proactive_interval_minutes": 10,
    "proactive_check_seconds_test": 30,
    "evening_review_enabled": True,
    "idle_nudge_enabled": True,
    "chat_history_enabled": True,
    "restore_last_session": True,
    "enable_memory_candidates": True,
    "recent_context_messages": 16,
    "auto_summary_enabled": True,
    "summary_message_threshold": 30,
    "summary_character_threshold": 12000,
    "model_name": "qwen3:4b",
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
        self.settings_dialog: Optional[SettingsDialog] = None
        self.growth_dialog: Optional[GrowthDialog] = None
        self.growth_service = GrowthManager()
        self.growth_manager = self.growth_service
        # The chat bootstrap owns the shared MemoryService and assigns its
        # candidate manager here. Avoid opening the same private files through
        # a second repository before the chat window exists.
        self.memory_service = None
        self.memory_candidate_manager = None
        self.config = self._load_config()
        self.chat_history_manager = ChatHistoryManager(
            enabled=bool(self.config.get("chat_history_enabled", True))
        )
        self.proactive_manager = ProactiveManager(
            self.growth_service,
            enabled=bool(self.config.get("proactive_enabled", True)),
            cooldown_seconds=self._proactive_interval_seconds(),
            evening_review_enabled=bool(
                self.config.get("evening_review_enabled", True)
            ),
            idle_nudge_enabled=bool(self.config.get("idle_nudge_enabled", True)),
            idle_threshold_seconds=self._proactive_idle_threshold_seconds(),
        )
        self.tips = self._load_tips()
        self._state = "idle"
        self._is_blinking = False
        self._drag_offset: Optional[QPoint] = None
        self._dragging = False
        self._woke_from_sleep_on_press = False
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
        self._dance_frames: List[QPixmap] = []
        self._dance_frame_index = 0
        self._dance_completed_loops = 0
        self._dance_pixmap: Optional[QPixmap] = None

        self.bubble = PetBubble()
        self.actions = PetActionController(self)
        self.action_manager = PetActionManager(self)
        self.client_action_policy = ClientActionPolicy()
        self.auto_tip_timer = QTimer(self)
        self.auto_tip_timer.setSingleShot(True)
        self.auto_tip_timer.timeout.connect(self._show_auto_tip)
        self.study_reminder_timer = QTimer(self)
        self.study_reminder_timer.timeout.connect(self._trigger_study_reminder)
        self.sleep_check_timer = QTimer(self)
        self.sleep_check_timer.timeout.connect(self._check_sleep_timeout)
        self.proactive_timer = QTimer(self)
        self.proactive_timer.timeout.connect(self._check_proactive_reminder)
        self.dance_timer = QTimer(self)
        self.dance_timer.setSingleShot(True)
        self.dance_timer.timeout.connect(self._advance_dance_frame)

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
        self._start_proactive_timer()
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

    def set_state(self, state: str, from_manager: bool = False) -> None:
        if state not in PET_STATES:
            raise ValueError(f"Unknown pet state: {state}")

        if not from_manager:
            if state == "idle":
                self.action_manager.restore_idle()
            elif state == "happy":
                self.action_manager.play_action("jump")
            elif state == "thinking":
                self.action_manager.play_action("shake")
            elif state == "study":
                self.action_manager.play_action("study_reminder")
            elif state == "sleep":
                self.action_manager.play_action("sleep")
            return

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
        self.action_manager.set_state("thinking")
        self.mark_state("thinking")
        self.actions.start_thinking()

    def stop_thinking(self) -> None:
        print("[STATE] thinking stop", flush=True)
        self.actions.stop_thinking()
        self.action_manager.stop_state("thinking")

    def record_interaction(self) -> bool:
        self._last_interaction_at = time.monotonic()
        if self._is_sleeping:
            self.wake()
            return True
        return False

    def wake(self, from_manager: bool = False) -> None:
        if not from_manager:
            self.action_manager.play_action("wake")
            return

        if not self._is_sleeping and self.state != "sleep":
            return

        print("[STATE] wake", flush=True)
        self._is_sleeping = False
        self.setWindowOpacity(1.0)
        self.visualOffset = QPoint(0, 0)
        self.bodyScale = 1.0
        self.set_state("idle", from_manager=True)

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
        self.action_manager.play_action("nod")

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
        chat_window = self._ensure_chat_window()
        if chat_window is None:
            self.show_bubble("聊天窗口入口还没有准备好。")
            return

        chat_window.show()
        chat_window.raise_()
        chat_window.activateWindow()

    def _ensure_chat_window(self) -> Optional[QWidget]:
        if self.chat_factory is None:
            return None

        if self.chat_window is None:
            try:
                self.chat_window = self.chat_factory(self)
            except TypeError:
                self.chat_window = self.chat_factory()
        return self.chat_window

    def open_memory_dialog(self) -> None:
        self.record_interaction()
        chat_window = self._ensure_chat_window()
        if chat_window is None or not hasattr(chat_window, "open_memory_dialog"):
            self.show_bubble("记忆候选入口还没有准备好。")
            return
        chat_window.open_memory_dialog()

    def open_settings_dialog(self) -> None:
        self.record_interaction()
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(
                self, chat_history_manager=self.chat_history_manager
            )
            self.settings_dialog.settings_saved.connect(self.apply_saved_settings)
            self.settings_dialog.history_cleared.connect(
                self._handle_chat_history_cleared
            )
            self.settings_dialog.finished.connect(self._clear_settings_dialog)

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _clear_settings_dialog(self) -> None:
        self.settings_dialog = None

    def _handle_chat_history_cleared(self) -> None:
        if self.chat_window is not None and hasattr(
            self.chat_window, "handle_history_cleared_from_settings"
        ):
            self.chat_window.handle_history_cleared_from_settings()

    def open_growth_dialog(self) -> None:
        self.record_interaction()
        if self.growth_dialog is None:
            self.growth_dialog = GrowthDialog(self.growth_service, self)
            self.growth_dialog.finished.connect(self._clear_growth_dialog)

        self.growth_dialog.refresh_all()
        self.growth_dialog.show()
        self.growth_dialog.raise_()
        self.growth_dialog.activateWindow()

    def _clear_growth_dialog(self) -> None:
        self.growth_dialog = None

    def apply_saved_settings(self, config: Dict[str, object]) -> None:
        old_center = self.geometry().center()
        old_scale = float(self.config.get("pet_scale", 1.0))
        old_always_on_top = bool(self.config.get("pet_always_on_top", True))
        self.config.update(config)
        self.chat_history_manager.set_enabled(
            bool(self.config.get("chat_history_enabled", True))
        )

        if float(self.config.get("pet_scale", 1.0)) != old_scale:
            self._resize_for_scale()
            self.move(old_center - QPoint(self.width() // 2, self.height() // 2))

        if bool(self.config.get("pet_always_on_top", True)) != old_always_on_top:
            self._apply_window_flags()
            self.show()

        self._restart_auto_tip_timer()
        self._start_study_reminder_timer()
        self._start_proactive_timer()
        self._last_interaction_at = time.monotonic()
        print("[SETTINGS] pet settings applied", flush=True)

    def pause_proactive_reminders(self) -> None:
        self.proactive_manager.pause()

    def resume_proactive_reminders(self) -> None:
        self.proactive_manager.resume()

    def notify_plan_completed(self) -> None:
        if self.action_manager.current_state in {"dancing", "sleeping"}:
            return
        reminder = self.proactive_manager.task_completed_reminder()
        if reminder is not None:
            self._present_proactive_reminder(reminder, include_chat=False)

    def start_dance(self) -> bool:
        return self.action_manager.play_action("dance")

    def agent_sleep(self) -> bool:
        return bool(self.action_manager.play_action("sleep"))

    def execute_client_action(self, action: Dict[str, object]) -> bool:
        """Validate and execute one declarative action from an Agent response."""
        allowed, item, reason = self.client_action_policy.validate(action)
        if not allowed or item is None:
            print(f"[CLIENT_ACTION] blocked: {reason}", flush=True)
            return False

        arguments = item.arguments
        handlers = {
            "nod": lambda: self.action_manager.play_action("nod"),
            "jump": lambda: self.action_manager.play_action("jump"),
            "show_bubble": lambda: self.show_bubble(
                str(arguments["text"]),
                int(arguments.get("duration_ms", 6000)),
            ),
            "play_dance": lambda: self.action_manager.play_action("dance"),
            "sleep": lambda: self.action_manager.play_action("sleep"),
            "wake": lambda: self.action_manager.play_action("wake"),
            "scale": lambda: self.action_manager.play_action("scale"),
        }
        result = handlers[item.name]()
        return result is not False

    def _start_dance_frames(self) -> bool:
        self.record_interaction()
        print("[ACTION] dance", flush=True)
        frames = self._load_dance_frames()
        if len(frames) < 2:
            print("[ACTION] dance frames missing", flush=True)
            self.show_bubble("还没有舞蹈动作素材哦。")
            return False

        if self.dance_timer.isActive():
            self._stop_dance_frames()

        print("[ACTION] dance frames start", flush=True)
        self._dance_frames = frames
        self._dance_frame_index = 0
        self._dance_completed_loops = 0
        self._dance_pixmap = self._dance_frames[0]
        self.dance_timer.start(self._dance_frame_duration(0))
        self.update()
        return True

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu, actions = self._create_context_menu()
        chosen = menu.exec(event.globalPos())
        if chosen == actions["chat"]:
            self.open_chat_window()
        elif chosen == actions["growth"]:
            self.open_growth_dialog()
        elif chosen == actions["memory"]:
            self.open_memory_dialog()
        elif chosen == actions["settings"]:
            self.open_settings_dialog()
        elif chosen == actions["dance"]:
            self.start_dance()
        elif chosen == actions["encourage"]:
            self.action_manager.play_action("jump")
        elif chosen == actions["sleep"]:
            self.action_manager.play_action("sleep")
        elif chosen == actions["wake"]:
            self.action_manager.play_action("wake")
        elif chosen == actions["quit"]:
            QApplication.quit()

    def _create_context_menu(self):
        menu = QMenu(self)
        actions = {
            "chat": menu.addAction("打开聊天"),
            "growth": menu.addAction("成长面板"),
            "memory": menu.addAction("记忆管理"),
            "settings": menu.addAction("设置"),
            "dance": menu.addAction("跳舞一下"),
        }
        menu.addSeparator()
        actions["encourage"] = menu.addAction("立即鼓励我")
        actions["sleep"] = menu.addAction("进入睡眠")
        actions["wake"] = menu.addAction("唤醒")
        menu.addSeparator()
        actions["quit"] = menu.addAction("退出 Roxy")
        return menu, actions

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
            self._woke_from_sleep_on_press = self.record_interaction()
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
                woke_from_sleep = self._woke_from_sleep_on_press or self.record_interaction()
                self._woke_from_sleep_on_press = False
                if not woke_from_sleep:
                    self.action_manager.play_action("jump")
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
        if self._dance_pixmap is None and not self.config.get("use_pet_asset", False):
            return False

        pixmap = self._dance_pixmap if self._dance_pixmap is not None else self._load_asset_pixmap()
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

    def _load_dance_frames(self) -> List[QPixmap]:
        if not DANCE_FRAMES_DIR.exists():
            return []

        frames: List[QPixmap] = []
        for path in sorted(DANCE_FRAMES_DIR.glob("dance_*.png"), key=self._dance_frame_sort_key):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                frames.append(pixmap)
        return frames

    def _dance_frame_sort_key(self, path: Path) -> tuple[int, str]:
        match = re.search(r"dance_(\d+)", path.stem)
        frame_number = int(match.group(1)) if match else 999999
        return frame_number, path.name.lower()

    def _advance_dance_frame(self) -> None:
        if not self._dance_frames:
            self._stop_dance_frames()
            return

        self._dance_frame_index += 1
        if self._dance_frame_index >= len(self._dance_frames):
            self._dance_frame_index = 0
            self._dance_completed_loops += 1
            if self._dance_completed_loops >= DANCE_LOOP_COUNT:
                self._stop_dance_frames()
                return

        self._dance_pixmap = self._dance_frames[self._dance_frame_index]
        self.update()
        self.dance_timer.start(self._dance_frame_duration(self._dance_frame_index))

    def _dance_frame_duration(self, frame_index: int) -> int:
        if frame_index < len(DANCE_FRAME_DURATIONS_MS):
            return DANCE_FRAME_DURATIONS_MS[frame_index]
        return 100

    def _stop_dance_frames(self) -> None:
        if self.dance_timer.isActive():
            self.dance_timer.stop()
        had_frames = bool(self._dance_frames) or self._dance_pixmap is not None
        self._dance_frames = []
        self._dance_frame_index = 0
        self._dance_completed_loops = 0
        self._dance_pixmap = None
        self.update()
        if had_frames:
            print("[ACTION] dance frames stop", flush=True)
            self.action_manager.restore_idle()

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

    def _restart_auto_tip_timer(self) -> None:
        self.auto_tip_timer.stop()
        if self.config.get("auto_tips_enabled", True):
            self._schedule_next_auto_tip()

    def _show_auto_tip(self) -> None:
        self.show_bubble(random.choice(self.tips or DEFAULT_TIPS), record_interaction=False)
        self._schedule_next_auto_tip()

    def _start_study_reminder_timer(self) -> None:
        interval_seconds = self._study_reminder_interval_seconds()
        self.study_reminder_timer.start(max(1, interval_seconds) * 1000)

    def _trigger_study_reminder(self) -> None:
        if self.action_manager.current_state == "sleeping":
            return

        print("[REMINDER] study reminder triggered", flush=True)
        self.action_manager.play_action("study_reminder")

    def _check_sleep_timeout(self) -> None:
        if self._is_sleeping or self.action_manager.current_state in {"thinking", "dancing"}:
            return

        if time.monotonic() - self._last_interaction_at >= self._sleep_interval_seconds():
            self._is_sleeping = True
            print("[STATE] sleep", flush=True)
            self.action_manager.play_action("sleep")

    def _start_proactive_timer(self) -> None:
        self.proactive_timer.stop()
        interval_seconds = self._proactive_interval_seconds()
        self.proactive_manager.configure(
            enabled=bool(self.config.get("proactive_enabled", True)),
            cooldown_seconds=interval_seconds,
            evening_review_enabled=bool(
                self.config.get("evening_review_enabled", True)
            ),
            idle_nudge_enabled=bool(self.config.get("idle_nudge_enabled", True)),
            idle_threshold_seconds=self._proactive_idle_threshold_seconds(),
        )
        if self.config.get("proactive_enabled", True):
            self.proactive_timer.start(max(1, interval_seconds) * 1000)

    def _check_proactive_reminder(self) -> None:
        current_state = self.action_manager.current_state
        if current_state == "dancing":
            print("[Proactive] skipped: dancing", flush=True)
            return
        if current_state in {"thinking", "reminding"}:
            print("[Proactive] skipped: busy", flush=True)
            return

        idle_seconds = time.monotonic() - self._last_interaction_at
        allowed_types = {"idle_nudge"} if current_state == "sleeping" else None
        reminder = self.proactive_manager.check(
            idle_seconds=idle_seconds,
            seconds_since_interaction=idle_seconds,
            allowed_types=allowed_types,
        )
        if reminder is None:
            return

        if current_state == "sleeping":
            self.action_manager.play_action("wake")
        self._present_proactive_reminder(reminder)

    def _present_proactive_reminder(
        self,
        reminder: Dict[str, str],
        *,
        include_chat: bool = True,
    ) -> None:
        text = str(reminder.get("text", "")).strip()
        if not text:
            return
        self.show_bubble(text, record_interaction=False)
        if (
            include_chat
            and self.chat_window is not None
            and self.chat_window.isVisible()
            and hasattr(self.chat_window, "add_message")
        ):
            self.chat_window.add_message("Roxy", text)

    def _study_reminder_interval_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return int(self.config.get("study_reminder_seconds_test", 10))
        return int(self.config.get("study_reminder_minutes", 25)) * 60

    def _sleep_interval_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return int(self.config.get("sleep_seconds_test", 30))
        return int(self.config.get("sleep_minutes", 20)) * 60

    def _proactive_interval_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return int(self.config.get("proactive_check_seconds_test", 30))
        return int(self.config.get("proactive_interval_minutes", 10)) * 60

    def _proactive_idle_threshold_seconds(self) -> int:
        if self.config.get("test_mode", False):
            return 30
        return 30 * 60

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
