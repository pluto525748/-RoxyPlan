"""Independent event-chain acceptance. Offscreen, isolated, no system input/API."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from frontend.desktop_pet import DesktopPet
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from modules.runtime_paths import RuntimePaths


SOURCE = Path(__file__).resolve().parents[1]


class MouseEvent:
    """Qt6-compatible event values, never injected into OS input queues."""

    def __init__(self, global_pos, *, button=Qt.LeftButton, buttons=Qt.LeftButton):
        self.point = QPoint(*global_pos)
        self._button, self._buttons = button, buttons
        self.accepted = False

    def globalPosition(self):
        return QPointF(self.point)

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def accept(self):
        self.accepted = True


class Gesture:
    def __init__(self, pet):
        self.pet = pet
        self.origin = QPoint(pet.pos())
        self.press = pet.frameGeometry().topLeft() + QPoint(20, 22)
        self.offset = self.press - pet.frameGeometry().topLeft()
        event = MouseEvent(self.press.toTuple())
        pet.mousePressEvent(event)
        assert event.accepted
        self.last = QPoint(self.press)

    def move(self, dx, dy):
        self.last = self.press + QPoint(dx, dy)
        event = MouseEvent(self.last.toTuple())
        self.pet.mouseMoveEvent(event)
        assert event.accepted
        # Every intermediate position must follow the old pointer-offset chain.
        assert self.pet.pos() == self.last - self.offset

    def release(self):
        event = MouseEvent(self.last.toTuple(), buttons=Qt.NoButton)
        self.pet.mouseReleaseEvent(event)
        assert event.accepted
        assert self.pet._drag_offset is None
        assert not self.pet._dragging


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    assert instance.platformName() == "offscreen", "This task has no visible-desktop authorization"
    return instance


@pytest.fixture
def pet_factory(app, tmp_path):
    pets = []

    def make(*, resource_root=SOURCE, custom_image=False):
        root = tmp_path / f"instance-{len(pets)}"
        private = root / "data/private"
        private.mkdir(parents=True)
        config = root / "data/pet_config.json"
        settings = {
            "test_mode": False, "pet_position": "custom", "use_pet_asset": True,
            "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
            "auto_tips_enabled": False, "auto_complete_yesterday_review": False,
            "proactive_enabled": False, "chat_history_enabled": True,
        }
        if custom_image:
            path = root / "custom.png"
            image = QImage(17, 19, QImage.Format_RGBA8888)
            image.fill(QColor("#c36142"))
            assert image.save(str(path))
            settings["pet_asset_path"] = str(path)
        config.write_text(json.dumps(settings), encoding="utf-8")
        tips = root / "data/pet_tips.json"
        tips.write_text(json.dumps(["合成动作验收提示"]), encoding="utf-8")
        runtime = RuntimePaths(Path(resource_root), SOURCE, root, mode="developer", data_source="engineering")
        pet = DesktopPet(
            config_file=config, tips_file=tips, runtime=runtime,
            growth_service=GrowthManager(private), chat_history_manager=ChatHistoryManager(private),
            secret_store=SecretStore(root), usage_store=ModelUsageStore(root),
            knowledge_dir=root / "data/knowledge",
        )
        pets.append(pet)
        # Background reminder/sleep behavior is outside this focused gesture test.
        for timer in (pet.auto_tip_timer, pet.study_reminder_timer, pet.sleep_check_timer, pet.proactive_timer):
            timer.stop()
        pet.move(200, 250)
        assert not pet.isVisible()
        return pet

    yield make
    for pet in reversed(pets):
        pet.shutdown_visuals()
        pet.close()
        assert all(not timer.isActive() for timer in pet.findChildren(QTimer))


@pytest.mark.parametrize("direction,sign", [("left", -1), ("right", 1)])
def test_real_mouse_chain_reverses_and_preserves_direction_on_vertical(pet_factory, direction, sign):
    pet = pet_factory()
    gesture = Gesture(pet)
    gesture.move(sign * 25, 0)
    assert pet.sprite_player.animation == f"running-{direction}"
    index = pet.sprite_player.frame_index
    pet.sprite_player.advance()
    assert pet.sprite_player.frame_index != index
    opposite = "right" if sign < 0 else "left"
    # Still on the same side of the press point, but latest horizontal delta reversed.
    gesture.move(sign * 15, 0)
    assert pet.sprite_player.animation == f"running-{opposite}"
    gesture.move(sign * 15, 30)
    assert pet.sprite_player.animation == f"running-{opposite}"
    gesture.release()
    assert pet.sprite_player.animation == "idle"
    again = Gesture(pet)
    again.move(0, -20)
    assert pet.sprite_player.animation == f"running-{opposite}"
    again.release()


@pytest.mark.parametrize("dx,should_run", [(0, False), (1, False), (3, False), (4, True)])
def test_preserved_click_threshold_and_release(pet_factory, dx, should_run):
    pet = pet_factory()
    gesture = Gesture(pet)
    gesture.move(dx, 0)
    assert pet._dragging is should_run
    assert pet.sprite_player.animation.startswith("running-") is should_run
    gesture.release()
    if should_run:
        assert pet.action_manager.current_state == "idle"
        assert pet.sprite_player.animation == "idle"
    else:
        # A click retains its original jump, rather than being consumed as a run.
        assert pet.state == "happy"
        assert pet.sprite_player.animation == "jumping"


def test_accumulated_small_mouse_moves_are_a_drag_not_a_click(pet_factory):
    pet = pet_factory()
    gesture = Gesture(pet)
    for dx in range(1, 13):
        gesture.move(dx, 0)
    assert pet._dragging, "12px cumulative held movement must not remain a click because each event is only 1px"
    assert pet.sprite_player.animation == "running-right"
    gesture.release()
    assert pet.state == "idle", "Releasing a real drag must not trigger the ordinary click jump"


def test_frame_ticks_and_unheld_moves_never_translate_the_window(pet_factory):
    pet = pet_factory()
    origin = QPoint(pet.pos())
    pet.mouseMoveEvent(MouseEvent((900, 800), button=Qt.NoButton, buttons=Qt.NoButton))
    assert pet.pos() == origin
    gesture = Gesture(pet)
    gesture.move(40, 25)
    held = QPoint(pet.pos())
    frames = set()
    for _ in range(18):
        frames.add(pet.sprite_player.frame_index)
        pet.sprite_player.advance()
        assert pet.pos() == held
    assert len(frames) == 8
    gesture.release()
    for _ in range(8):
        pet.sprite_player.advance()
        assert pet.pos() == held
    pet.mouseMoveEvent(MouseEvent((1000, 800), button=Qt.NoButton, buttons=Qt.NoButton))
    assert pet.pos() == held


def character_pose(pet):
    return (QPoint(pet.visualOffset), pet.bodyTilt, pet.bodyScale,
            pet.wandAngle, QPoint(pet.talkNod), pet.sprite_player.frame_rect)


def test_thinking_timer_changes_only_bubble_and_drag_keeps_thinking(pet_factory):
    pet = pet_factory()
    pet.start_thinking()
    pose = character_pose(pet)
    initial_position = QPoint(pet.pos())
    timers = tuple(pet.thinking_bubble.findChildren(QTimer))
    pet.start_thinking()
    assert tuple(pet.thinking_bubble.findChildren(QTimer)) == timers
    initial_phase = pet.thinking_bubble.phase
    QTest.qWait(410)
    assert pet.thinking_bubble.phase != initial_phase
    assert character_pose(pet) == pose
    assert pet.pos() == initial_position
    gesture = Gesture(pet)
    gesture.move(-30, 15)
    assert not pet.sprite_player.animation.startswith("running-")
    assert character_pose(pet) == pose
    gesture.release()
    assert pet.action_manager.current_state == "thinking"
    assert pet._thinking_active and pet.thinking_bubble.timer.isActive()
    pet.stop_thinking()
    pet.stop_thinking()
    assert pet.action_manager.current_state == "idle"
    assert not pet.thinking_bubble.timer.isActive()
    assert not pet.thinking_bubble.isVisible()


@pytest.mark.parametrize("starts_before_press", [True, False])
def test_thinking_end_during_drag_does_not_restore_stale_thinking(pet_factory, starts_before_press):
    pet = pet_factory()
    if starts_before_press:
        pet.start_thinking()
    gesture = Gesture(pet)
    gesture.move(20, 0)
    if not starts_before_press:
        pet.start_thinking()
    pet.stop_thinking()
    gesture.release()
    assert pet.action_manager.current_state == "idle"
    assert not pet._thinking_active
    assert not pet.thinking_bubble.timer.isActive()
    assert pet.sprite_player.animation == "idle"


def test_thinking_started_mid_drag_stays_frozen_through_release(pet_factory):
    pet = pet_factory()
    gesture = Gesture(pet)
    gesture.move(-20, 0)
    pet.sprite_player.advance()
    pet.start_thinking()
    frozen = character_pose(pet)
    gesture.move(15, -10)
    gesture.release()
    assert pet.action_manager.current_state == "thinking"
    assert pet.thinking_bubble.timer.isActive()
    pet.sprite_player.advance()
    assert character_pose(pet) == frozen
    pet.stop_thinking()
    assert pet.action_manager.current_state == "idle"
    assert pet.sprite_player.animation == "idle"
    assert not pet.thinking_bubble.timer.isActive()


def start_dance(pet):
    assert pet.action_manager.try_play_action("dance") == (True, "running")
    assert len(pet._dance_frames) == 8
    pet._advance_dance_frame()
    return pet._dance_frame_index, pet._dance_completed_loops, pet._dance_pixmap.cacheKey()


def test_dance_progress_and_real_busy_state_survive_drag(pet_factory):
    pet = pet_factory()
    before = start_dance(pet)
    gesture = Gesture(pet)
    assert not pet.dance_timer.isActive()
    gesture.move(-40, 5)
    assert pet.sprite_player.animation == "running-left"
    for _ in range(10):
        pet.sprite_player.advance()
    assert (pet._dance_frame_index, pet._dance_completed_loops, pet._dance_pixmap.cacheKey()) == before
    gesture.release()
    assert pet.action_manager.current_state == "dancing"
    assert pet.dance_timer.isActive()
    assert (pet._dance_frame_index, pet._dance_completed_loops, pet._dance_pixmap.cacheKey()) == before
    assert pet.action_manager.try_play_action("dance") == (False, "busy")


@pytest.mark.parametrize("terminate", ["cancel", "complete"])
def test_dance_terminated_during_drag_does_not_restart_old_timers(pet_factory, terminate):
    pet = pet_factory()
    start_dance(pet)
    gesture = Gesture(pet)
    gesture.move(20, 0)
    if terminate == "cancel":
        assert pet.action_manager.cancel_current_action()
    else:
        # Drive the real completion handler, without waiting for elapsed dance time.
        for _ in range(24):
            if not pet._dance_frames:
                break
            pet._advance_dance_frame()
    assert pet.action_manager.current_state == "idle"
    assert not pet._dance_frames
    gesture.release()
    assert pet.action_manager.current_state == "idle"
    assert not pet.dance_timer.isActive()
    assert not pet.action_manager._failsafe_timer.isActive(), "A terminated dance must not revive its paused failsafe on mouse release"


def test_shutdown_mid_drag_and_thinking_does_not_restart_timers(pet_factory):
    pet = pet_factory()
    gesture = Gesture(pet)
    gesture.move(20, 0)
    pet.start_thinking()
    pet.shutdown_visuals()
    gesture.release()
    pet.start_thinking()
    assert not pet.start_locomotion("left")
    assert not pet.sprite_player.active
    assert not pet.thinking_bubble.timer.isActive()
    assert all(not timer.isActive() for timer in pet.findChildren(QTimer))


@pytest.mark.parametrize("resource_mode", ["custom", "missing_atlas", "missing_all"])
def test_custom_and_missing_assets_keep_drag_and_real_fallback(pet_factory, tmp_path, resource_mode):
    root = SOURCE
    if resource_mode != "custom":
        root = tmp_path / "resources"
        folder = root / "assets/pet"
        folder.mkdir(parents=True)
        if resource_mode == "missing_atlas":
            shutil.copyfile(SOURCE / "assets/pet/roxy_pet_transparent.png", folder / "roxy_pet_transparent.png")
    pet = pet_factory(resource_root=root, custom_image=resource_mode == "custom")
    assert pet.uses_author_sprites() is (resource_mode != "custom")
    if resource_mode != "custom":
        assert not pet.sprite_player.available
    gesture = Gesture(pet)
    gesture.move(-20, 10)
    assert not pet.sprite_player.animation.startswith("running-")
    gesture.release()
    image = QImage(168, 190, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        drawn = pet._draw_asset_if_enabled(painter)
    finally:
        painter.end()
    assert drawn is (resource_mode != "missing_all")
    if resource_mode == "custom":
        assert image.pixelColor(84, 95) == QColor("#c36142")


def test_all_57_source_cells_keep_original_pixel_coordinates(pet_factory, tmp_path):
    pet = pet_factory()
    path = SOURCE / "assets/pet/roxy_sprite_atlas.webp"
    # Source-byte identity preserves authored RGBA, including hidden RGB at
    # alpha=0. QPixmap's render representation is premultiplied; converting it
    # back to straight RGBA introduces rounding at fractional alpha.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "946835f048cd9c885130b3eba1bde2b7f45b25d2cb1c067bc1fc20094e0cae18"
    original = QImage(str(path))
    names = ["idle", "running-right", "running-left", "waving", "jumping", "failed", "waiting", "running", "review"]
    counts = [6, 8, 8, 4, 5, 8, 6, 6, 6]
    visited = 0
    for row, (name, count) in enumerate(zip(names, counts)):
        assert pet.present_character_state(name)
        for column in range(count):
            rect = pet.sprite_player.frame_rect
            assert rect.getRect() == (column * 192, row * 208, 192, 208)
            actual = pet.sprite_player.atlas.copy(rect).toImage()
            source = original.copy(rect)
            expected = source.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            assert actual.convertToFormat(QImage.Format_ARGB32_Premultiplied) == expected
            if visited == 0:
                raw = source.convertToFormat(QImage.Format_RGBA8888).bits().tobytes()
                recovered = actual.convertToFormat(QImage.Format_RGBA8888).bits().tobytes()
                differences = {"opaque": 0, "partial_alpha": 0, "transparent": 0}
                for offset in range(0, len(raw), 4):
                    if raw[offset:offset + 4] != recovered[offset:offset + 4]:
                        alpha = raw[offset + 3]
                        differences["opaque" if alpha == 255 else "transparent" if alpha == 0 else "partial_alpha"] += 1
                assert differences["opaque"] == 0
                (tmp_path / "pixel-domain.json").write_text(json.dumps({
                    "straight_rgba_roundtrip_differences_first_cell": differences,
                    "premultiplied_render_pixels_equal": True,
                    "all_source_bytes_pinned": True,
                }), encoding="utf-8")
            visited += 1
            pet.sprite_player.advance()
        assert pet.sprite_player.animation == "idle"
    assert visited == 57
