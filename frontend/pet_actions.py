from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QEasingCurve,
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
        self._thinking_animation = QPropertyAnimation(pet, b"visualOffset", self)
        self._thinking_animation.setDuration(700)
        self._thinking_animation.setStartValue(QPoint(0, 0))
        self._thinking_animation.setKeyValueAt(0.25, QPoint(-20, 0))
        self._thinking_animation.setKeyValueAt(0.75, QPoint(20, 0))
        self._thinking_animation.setEndValue(QPoint(0, 0))
        self._thinking_animation.setLoopCount(-1)
        self._thinking_animation.setEasingCurve(QEasingCurve.InOutSine)

        self._idle_animation = QPropertyAnimation(pet, b"visualOffset", self)
        self._idle_animation.setDuration(2400)
        self._idle_animation.setStartValue(QPoint(0, 0))
        self._idle_animation.setKeyValueAt(0.5, QPoint(0, -5))
        self._idle_animation.setEndValue(QPoint(0, 0))
        self._idle_animation.setLoopCount(-1)
        self._idle_animation.setEasingCurve(QEasingCurve.InOutSine)

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_once)
        self._schedule_next_blink()

    def start_idle_breathing(self) -> None:
        if self._idle_animation.state() != QPropertyAnimation.Running:
            self._idle_animation.start()

    def stop_idle_breathing(self) -> None:
        if self._idle_animation.state() == QPropertyAnimation.Running:
            self._idle_animation.stop()
        self.pet.visualOffset = QPoint(0, 0)

    def click_happy(self) -> None:
        self.pet.mark_state("happy")
        self.pet.show_random_encouragement()
        self._run_jump(lambda: self.pet.mark_state("idle"))

    def thinking(self) -> None:
        self.pet.mark_state("thinking")
        self.pet.show_bubble("让我想一想……")
        self._run_shake(lambda: self.pet.mark_state("idle"))

    def start_thinking(self) -> None:
        self.stop_idle_breathing()
        if self._transient_group is not None:
            self._transient_group.stop()
        print("[ACTION] shake", flush=True)
        if self._thinking_animation.state() != QPropertyAnimation.Running:
            self._thinking_animation.start()

    def stop_thinking(self) -> None:
        if self._thinking_animation.state() == QPropertyAnimation.Running:
            self._thinking_animation.stop()
        self.pet.visualOffset = QPoint(0, 0)

    def study_reminder(self) -> None:
        self.pet.mark_state("study")
        self.pet.show_study_tip()
        self._run_study_pulse(lambda: self.pet.mark_state("idle"))

    def sleep(self) -> None:
        self.stop_idle_breathing()
        self.pet.mark_state("sleep")
        self.pet.show_bubble("我会安静待着，需要我时再叫我。", record_interaction=False)
        self._run_sleep_pose()

    def speaking_nod(self) -> None:
        if self.pet.state == "sleep":
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
        print("[ACTION] jump", flush=True)
        self._start_transient_group(
            [
                self._offset_animation(QPoint(0, 0), QPoint(0, -30), 150),
                self._offset_animation(QPoint(0, -30), QPoint(0, 0), 220),
            ],
            finished_callback,
        )

    def _run_shake(self, finished_callback) -> None:
        print("[ACTION] shake", flush=True)
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
        if self.pet.state == "sleep":
            self._schedule_next_blink()
            return

        self.pet.set_blinking(True)
        QTimer.singleShot(130, lambda: self.pet.set_blinking(False))
        self._schedule_next_blink()

    def _schedule_next_blink(self) -> None:
        self._blink_timer.start(random.randint(3500, 8000))
