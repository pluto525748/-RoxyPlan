from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QAbstractAnimation,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
)

if TYPE_CHECKING:
    from frontend.desktop_pet import DesktopPet


class PetActionController(QObject):
    """Timer and animation controller for the desktop pet."""

    def __init__(self, pet: "DesktopPet") -> None:
        super().__init__(pet)
        self.pet = pet
        self._transient_group: Optional[QSequentialAnimationGroup] = None
        self._nod_group: Optional[QSequentialAnimationGroup] = None
        self._motion_pauses: set[str] = set()
        self._paused_groups = []
        self._paused_blink_timers = []
        self._sprite_completion = None
        self._closed = False
        pet.sprite_player.finished.connect(self._sprite_finished)

        self._idle_animation = QPropertyAnimation(pet, b"visualOffset", self)
        self._idle_animation.setDuration(2400)
        self._idle_animation.setStartValue(QPoint(0, 0))
        self._idle_animation.setKeyValueAt(0.5, QPoint(0, -5))
        self._idle_animation.setEndValue(QPoint(0, 0))
        self._idle_animation.setLoopCount(-1)
        self._idle_animation.setEasingCurve(QEasingCurve.InOutSine)

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_once)
        self._blink_reset_timer = QTimer(self)
        self._blink_reset_timer.setSingleShot(True)
        self._blink_reset_timer.timeout.connect(lambda: self.pet.set_blinking(False))
        self._schedule_next_blink()

    def start_idle_breathing(self) -> None:
        if self._closed or self._motion_pauses or self.pet.state == "sleep":
            return
        if self.pet.uses_author_sprites():
            if self._idle_animation.state() != QAbstractAnimation.Stopped:
                self._idle_animation.stop()
                self.pet.visualOffset = QPoint(0, 0)
            player = self.pet.sprite_player
            if not player.active or player.animation == "idle":
                self._sprite_completion = None
                self.pet.play_reference_animation("idle", repeat=True)
            return
        self.pet.sprite_player.stop()
        if self._idle_animation.state() != QPropertyAnimation.Running:
            self._idle_animation.start()

    def stop_idle_breathing(self) -> None:
        if self._idle_animation.state() == QPropertyAnimation.Running:
            self._idle_animation.stop()
        self.pet.visualOffset = QPoint(0, 0)
        if self.pet.sprite_player.animation == "idle":
            self.pet.sprite_player.stop()

    def click_happy(self) -> None:
        if self._closed or self._motion_pauses:
            return
        self.pet.mark_state("happy")
        self.pet.show_random_encouragement()
        self._run_jump(lambda: self.pet.mark_state("idle"))

    def thinking(self) -> None:
        if self._closed or self._motion_pauses:
            return
        self.pet.mark_state("thinking")
        self.pet.show_bubble("让我想一想……")
        self._run_shake(lambda: self.pet.mark_state("idle"))

    def start_thinking(self) -> None:
        self.pause_motion("thinking")

    def stop_thinking(self) -> None:
        self.resume_motion("thinking")

    def pause_motion(self, reason: str) -> None:
        if not self._motion_pauses:
            self._paused_groups = []
            for group in (self._idle_animation, self._transient_group, self._nod_group):
                if group is not None and group.state() == QAbstractAnimation.Running:
                    group.pause()
                    self._paused_groups.append(group)
            self._paused_blink_timers = []
            for timer in (self._blink_timer, self._blink_reset_timer):
                if timer.isActive():
                    self._paused_blink_timers.append((timer, max(1, timer.remainingTime())))
                    timer.stop()
        self._motion_pauses.add(reason)
        self.pet.sprite_player.pause(reason)

    def resume_motion(self, reason: str) -> None:
        self._motion_pauses.discard(reason)
        self.pet.sprite_player.resume(reason)
        if self._closed or self._motion_pauses:
            return
        for group in self._paused_groups:
            if group.state() == QAbstractAnimation.Paused:
                group.resume()
        self._paused_groups = []
        for timer, remaining in self._paused_blink_timers:
            timer.start(remaining)
        self._paused_blink_timers = []

    def _play_author_once(self, name: str, callback) -> bool:
        if self._closed or self._motion_pauses:
            return False
        if self.pet.play_reference_animation(name):
            self._sprite_completion = (name, callback)
            return True
        return False

    def _sprite_finished(self, name: str) -> None:
        completion, self._sprite_completion = self._sprite_completion, None
        if self._closed:
            return
        if completion and completion[0] == name:
            completion[1]()
        self.start_idle_breathing()

    def wave(self) -> None:
        if not self.pet.action_manager.is_animating:
            self._play_author_once("waving", lambda: self.pet.mark_state("idle"))

    def cancel_body_motion(self) -> None:
        """Release an interrupted local action before sleep/dance takes over."""
        self._sprite_completion = None
        self.stop_idle_breathing()
        for group in (self._transient_group, self._nod_group):
            if group is not None:
                group.stop()
        self.pet.sprite_player.stop()
        self.pet.visualOffset = QPoint(0, 0)
        self.pet.talkNod = QPoint(0, 0)
        self.pet.bodyScale = 1.0
        self.pet.wandAngle = 0.0

    def shutdown(self) -> None:
        self._closed = True
        self._sprite_completion = None
        for group in (self._idle_animation, self._transient_group, self._nod_group):
            if group is not None:
                group.stop()
        self._blink_timer.stop()
        self._blink_reset_timer.stop()
        self._paused_groups = []
        self._paused_blink_timers = []

    def study_reminder(self, *, sprite: bool = True) -> None:
        if self._closed or self._motion_pauses:
            return
        self.pet.mark_state("study")
        self.pet.show_study_tip()
        if sprite and self._play_author_once("review", lambda: self.pet.mark_state("idle")):
            return
        self._run_study_pulse(lambda: self.pet.mark_state("idle"))

    def sleep(self) -> None:
        if self._closed or self._motion_pauses:
            return
        self.cancel_body_motion()
        self.pet.mark_state("sleep")
        self._sprite_completion = None
        if self.pet.uses_author_sprites():
            self.pet.sprite_player.play("idle")
        self.pet.sprite_player.stop()
        self.pet.show_bubble("我会安静待着，需要我时再叫我。", record_interaction=False)
        self._run_sleep_pose()

    def speaking_nod(self) -> None:
        if self._closed or self._motion_pauses or self.pet.state == "sleep":
            return

        print("[ACTION] nod", flush=True)
        if self._nod_group is not None:
            self._nod_group.stop()

        group = QSequentialAnimationGroup(self)
        group.addAnimation(self._point_animation(b"talkNod", QPoint(0, 0), QPoint(0, 12), 90))
        group.addAnimation(self._point_animation(b"talkNod", QPoint(0, 12), QPoint(0, 0), 140))
        self._nod_group = group
        group.start()

    def _run_jump(self, finished_callback) -> None:
        if self._closed or self._motion_pauses:
            return
        print("[ACTION] jump", flush=True)
        if self._play_author_once("jumping", finished_callback):
            return
        self._start_transient_group(
            [
                self._offset_animation(QPoint(0, 0), QPoint(0, -30), 150),
                self._offset_animation(QPoint(0, -30), QPoint(0, 0), 220),
            ],
            finished_callback,
        )

    def _run_shake(self, finished_callback) -> None:
        if self._closed or self._motion_pauses:
            return
        print("[ACTION] shake", flush=True)
        if self._play_author_once("waiting", finished_callback):
            return
        self._start_transient_group(
            [
                self._offset_animation(QPoint(0, 0), QPoint(-20, 0), 130),
                self._offset_animation(QPoint(-20, 0), QPoint(20, 0), 220),
                self._offset_animation(QPoint(20, 0), QPoint(0, 0), 130),
            ],
            finished_callback,
        )

    def _run_study_pulse(self, finished_callback) -> None:
        print("[ACTION] scale", flush=True)
        grow = QParallelAnimationGroup()
        grow.addAnimation(self._float_animation(b"bodyScale", 1.0, 1.12, 180))
        grow.addAnimation(self._float_animation(b"wandAngle", 0.0, -9.0, 180))

        settle = QParallelAnimationGroup()
        settle.addAnimation(self._float_animation(b"bodyScale", 1.12, 1.0, 260))
        settle.addAnimation(self._float_animation(b"wandAngle", -9.0, 0.0, 260))

        self._start_transient_group(
            [
                grow,
                settle,
            ],
            finished_callback,
        )

    def _run_sleep_pose(self) -> None:
        print("[ACTION] sleep", flush=True)
        if self._transient_group is not None:
            self._transient_group.stop()

        group = QParallelAnimationGroup(self)
        group.addAnimation(self._point_animation(b"visualOffset", QPoint(0, 0), QPoint(0, 20), 260))
        group.addAnimation(self._float_animation(b"bodyScale", 1.0, 0.96, 260))
        group.addAnimation(self._float_animation(b"windowOpacity", self.pet.windowOpacity(), 0.55, 260))
        self._transient_group = group
        group.start()

    def _start_transient_group(self, animations, finished_callback) -> None:
        self.stop_idle_breathing()
        if self._transient_group is not None:
            self._transient_group.stop()

        group = QSequentialAnimationGroup(self)
        for animation in animations:
            group.addAnimation(animation)
        group.finished.connect(finished_callback)
        group.finished.connect(self.start_idle_breathing)
        self._transient_group = group
        group.start()

    def _offset_animation(self, start: QPoint, end: QPoint, duration: int) -> QPropertyAnimation:
        return self._point_animation(b"visualOffset", start, end, duration)

    def _point_animation(self, prop: bytes, start: QPoint, end: QPoint, duration: int) -> QPropertyAnimation:
        animation = QPropertyAnimation(self.pet, b"visualOffset")
        animation.setPropertyName(prop)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        return animation

    def _float_animation(self, prop: bytes, start: float, end: float, duration: int) -> QPropertyAnimation:
        animation = QPropertyAnimation(self.pet, prop)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.InOutSine)
        return animation

    def _blink_once(self) -> None:
        if self._closed or self._motion_pauses:
            return
        if self.pet.state == "sleep":
            self._schedule_next_blink()
            return

        self.pet.set_blinking(True)
        self._blink_reset_timer.start(130)
        self._schedule_next_blink()

    def _schedule_next_blink(self) -> None:
        self._blink_timer.start(random.randint(3500, 8000))
