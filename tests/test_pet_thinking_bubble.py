"""Thinking lifecycle and integration contracts; no model or formal data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from frontend.desktop_pet import DesktopPet
from frontend.pet_thinking_bubble import PetThinkingBubble
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pet(app, tmp_path):
    config = tmp_path / "pet_config.json"
    config.write_text(json.dumps({
        "test_mode": False, "pet_position": "custom", "auto_tips_enabled": False,
        "auto_complete_yesterday_review": False, "proactive_enabled": False,
    }), encoding="utf-8")
    widget = DesktopPet(
        config_file=config, tips_file=tmp_path / "tips.json",
        growth_service=GrowthManager(tmp_path / "growth"),
        chat_history_manager=ChatHistoryManager(tmp_path / "history"),
    )
    yield widget
    widget.close()


def pose(pet):
    return (pet.pos(), pet.size(), pet.visualOffset, pet.bodyScale, pet.bodyTilt,
            pet.wandAngle, pet.talkNod, pet.sprite_player.frame_rect)


def test_indicator_is_noninteractive_on_screen_and_avoids_message(app):
    bubble = PetThinkingBubble()
    area = app.primaryScreen().availableGeometry()
    pet_rect = QRect(area.center().x(), area.center().y(), 168, 190)
    ordinary = QRect(pet_rect.x() - 70, pet_rect.y() - 100, 300, 100)
    bubble.place(pet_rect, avoid_rect=ordinary)
    bubble.start()
    assert area.contains(bubble.frameGeometry())
    assert not ordinary.intersects(bubble.frameGeometry())
    assert not pet_rect.intersects(bubble.frameGeometry())
    assert bubble.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert bubble.windowFlags() & Qt.WindowTransparentForInput
    assert bubble.testAttribute(Qt.WA_TransparentForMouseEvents)
    bubble._tick()
    phase = bubble.phase
    bubble.start()
    assert bubble.phase == phase
    bubble.place(pet_rect, avoid_rect=area)
    assert not bubble.isVisible()
    bubble.stop()
    bubble.stop()
    assert not bubble.timer.isActive()
    bubble.close()


def test_thinking_freezes_character_and_resumes_exact_frame(pet):
    assert pet.size().width() == 168 and pet.size().height() == 190
    pet.sprite_player.advance()
    pet.visualOffset = QPoint(2, -3)
    original = pose(pet)
    pet.start_thinking()
    pet.start_thinking()
    assert pet.action_manager.current_state == "thinking"
    assert pet.thinking_bubble.timer.isActive()
    assert not pet.sprite_player.timer.isActive()
    QTest.qWait(220)
    assert pose(pet) == original
    assert pet.action_manager.try_play_action("jump") == (False, "busy")
    assert not pet.present_character_state("failed")
    pet.stop_thinking()
    pet.stop_thinking()
    assert pose(pet) == original
    assert pet.sprite_player.timer.isActive()
    assert not pet.thinking_bubble.timer.isActive() and not pet.thinking_bubble.isVisible()
    assert pet.action_manager.current_state == "idle"


def test_ordinary_bubble_timeout_cannot_end_thinking(pet):
    pet.start_thinking()
    pet.show_bubble("离线提醒", duration_ms=30)
    QTest.qWait(80)
    assert not pet.bubble.isVisible()
    assert pet._thinking_active and pet.thinking_bubble.timer.isActive()
    assert pet.action_manager.current_state == "thinking"


def test_author_jump_timer_survives_bubble_nod_and_thinking(pet):
    assert pet.action_manager.play_action("jump")
    assert pet.sprite_player.animation == "jumping"
    timer = pet.action_manager._transient_timer
    assert timer.remainingTime() > 500
    original = pose(pet)
    pet.start_thinking()
    QTest.qWait(220)
    assert not timer.isActive() and pose(pet) == original
    pet.stop_thinking()
    assert timer.isActive() and pose(pet) == original
    for _ in range(5):
        pet.sprite_player.advance()
    assert pet.state == "idle" and pet.sprite_player.animation == "idle"


def test_dance_thinking_drag_and_sleep_release_owned_animation(pet, monkeypatch):
    frames = []
    for color in ("#ABCDEF", "#FEDCBA"):
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        frames.append(pixmap)
    monkeypatch.setattr(pet, "_load_dance_frames", lambda: frames)
    assert pet.action_manager.play_action("dance")
    original = pose(pet)
    frame = pet._dance_frame_index
    pet.start_thinking()
    pet.actions.pause_motion("drag")
    pet._pause_dance("drag")
    QTest.qWait(220)
    assert pet._dance_frame_index == frame and pose(pet) == original
    pet.stop_thinking()
    assert not pet.dance_timer.isActive()
    pet.actions.resume_motion("drag")
    pet._resume_dance("drag")
    assert pet.dance_timer.isActive()
    assert pet.action_manager.try_play_action("jump") == (False, "busy")
    assert pet.action_manager.play_action("sleep")
    assert not pet.dance_timer.isActive() and not pet._dance_frames
    assert not pet.sprite_player.timer.isActive()
    assert pet.state == "sleep"
    assert pet.action_manager.play_action("wake")
    assert pet.state == "idle" and pet.sprite_player.animation == "waving"


def test_public_presentation_rejects_custom_busy_and_undragged_locomotion(pet, tmp_path):
    original = pet.pos()
    assert not pet.start_locomotion("left")
    assert not pet.start_locomotion("right")
    assert pet.pos() == original
    assert pet.present_character_state("failed")
    assert not pet.present_character_state("review")
    for _ in range(8):
        pet.sprite_player.advance()
    assert pet.sprite_player.animation == "idle"
    pet.config["pet_asset_path"] = str(tmp_path / "custom.png")
    assert not pet.uses_author_sprites()
    assert not pet.present_character_state("waving")
    pet.actions.start_idle_breathing()
    assert not pet.sprite_player.active


class DragEvent:
    """Only the position/button data consumed by the existing mouse handlers."""

    def __init__(self, position):
        self.position = position

    def button(self):
        return Qt.LeftButton

    def buttons(self):
        return Qt.LeftButton

    def globalPosition(self):
        return QPointF(self.position)

    def accept(self):
        pass


def test_drag_threshold_direction_vertical_memory_and_release(pet):
    pet.move(200, 200)
    press = pet.pos() + QPoint(30, 30)
    pet.mousePressEvent(DragEvent(press))
    pet.mouseMoveEvent(DragEvent(press + QPoint(2, 0)))
    assert not pet._dragging and pet._locomotion_snapshot is None
    assert pet.pos() == QPoint(202, 200)
    pet.mouseMoveEvent(DragEvent(press + QPoint(20, 0)))
    assert pet._dragging and pet.sprite_player.animation == "running-right"
    pet.sprite_player.advance()
    frame = pet.sprite_player.frame_rect
    pet.mouseMoveEvent(DragEvent(press + QPoint(30, 0)))
    assert pet.sprite_player.frame_rect == frame  # no restart on each move
    pet.mouseMoveEvent(DragEvent(press + QPoint(-20, 0)))
    assert pet.sprite_player.animation == "running-left"
    pet.mouseMoveEvent(DragEvent(press + QPoint(-20, 30)))
    assert pet.sprite_player.animation == "running-left"
    assert pet.pos() == QPoint(180, 230)  # mouse owns all displacement
    pet.mouseReleaseEvent(DragEvent(press + QPoint(-20, 30)))
    assert pet._locomotion_snapshot is None
    assert pet.sprite_player.animation == "idle" and pet.sprite_player.active
    assert pet.pos() == QPoint(180, 230)


def test_drag_restores_dance_and_thinking_release_does_not_change_frozen_pose(pet, monkeypatch):
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("#ABCDEF"))
    monkeypatch.setattr(pet, "_load_dance_frames", lambda: [pixmap, pixmap])
    assert pet.action_manager.play_action("dance")
    saved_frame = pet._dance_frame_index
    press = pet.pos() + QPoint(30, 30)
    pet.mousePressEvent(DragEvent(press))
    pet.mouseMoveEvent(DragEvent(press + QPoint(-20, 0)))
    assert pet.sprite_player.animation == "running-left"
    assert not pet.dance_timer.isActive()
    pet.start_thinking()
    original = pose(pet)
    pet.mouseReleaseEvent(DragEvent(press + QPoint(-20, 0)))
    QTest.qWait(220)
    assert pose(pet) == original
    assert not pet.sprite_player.timer.isActive() and not pet.dance_timer.isActive()
    assert pet.action_manager.current_state == "thinking"
    pet.stop_thinking()
    assert pet.action_manager.current_state == "dancing"
    assert pet._dance_frame_index == saved_frame and pet.dance_timer.isActive()
    assert pet._locomotion_snapshot is None


def test_drag_during_thinking_moves_only_window_and_does_not_start_running(pet):
    pet.start_thinking()
    frame = pet.sprite_player.frame_rect
    press = pet.pos() + QPoint(30, 30)
    start = pet.pos()
    pet.mousePressEvent(DragEvent(press))
    pet.mouseMoveEvent(DragEvent(press + QPoint(40, 10)))
    assert pet.pos() == start + QPoint(40, 10)
    assert pet.sprite_player.frame_rect == frame
    assert pet._locomotion_snapshot is None and not pet.sprite_player.timer.isActive()
    pet.mouseReleaseEvent(DragEvent(press + QPoint(40, 10)))
    assert pet.action_manager.current_state == "thinking"
    pet.stop_thinking()
    assert pet.action_manager.current_state == "idle" and pet.sprite_player.timer.isActive()


def test_missing_atlas_uses_static_author_and_close_stops_all_owned_timers(pet):
    pet.sprite_player.animations = {}
    assert not pet.present_character_state("idle")
    assert not pet._load_asset_pixmap().isNull()
    pet.start_thinking()
    pet.shutdown_visuals()
    pet.shutdown_visuals()
    pet.stop_thinking()
    pet.start_thinking()
    pet.actions.start_idle_breathing()
    assert not pet.thinking_bubble.isVisible()
    assert not any(timer.isActive() for timer in pet.findChildren(QTimer))
    assert not any(timer.isActive() for timer in pet.bubble.findChildren(QTimer))


def test_custom_picture_keeps_original_image_and_body_animation_thinking_pause(pet, tmp_path):
    path = tmp_path / "custom-pet.png"
    pixmap = QPixmap(48, 64)
    pixmap.fill(QColor("#D8CDE9"))
    assert pixmap.save(str(path))
    pet.config["pet_asset_path"] = str(path)
    pet.actions.start_idle_breathing()
    assert not pet.uses_author_sprites() and not pet.sprite_player.active
    assert pet._load_asset_pixmap().toImage() == pixmap.toImage()
    QTest.qWait(60)
    original = pose(pet)
    pet.start_thinking()
    QTest.qWait(220)
    assert pose(pet) == original
    assert not pet.present_character_state("running-left")
    pet.stop_thinking()
    assert pose(pet) == original and not pet.sprite_player.active
