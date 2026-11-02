from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from frontend.desktop_pet import DesktopPet


VALID_STATES = {"idle", "thinking", "sleeping", "dancing", "reminding"}
TRANSIENT_ACTIONS = {"jump", "nod", "shake", "scale", "study_reminder", "sleep", "wake", "dance"}


class PetActionManager(QObject):
    """Lightweight coordinator for desktop pet states and action conflicts."""

    def __init__(self, pet: "DesktopPet") -> None:
        super().__init__(pet)
        self.pet = pet
        self.current_state = "idle"
        self.is_animating = False
        self.last_reason = "idle"
        self._failsafe_timer = QTimer(self)
        self._failsafe_timer.setSingleShot(True)
        self._failsafe_timer.timeout.connect(self._failsafe_release)
        self._transient_timer = QTimer(self)
        self._transient_timer.setSingleShot(True)
        self._transient_timer.timeout.connect(self._finish_transient)
        self._transient_restore_idle = False
        self._timer_pauses: set[str] = set()
        self._paused_timers = []

    def set_state(self, name: str) -> bool:
        if name not in VALID_STATES:
            raise ValueError(f"Unknown action manager state: {name}")
        if self.current_state != name:
            print(f"[ACTION_MANAGER] state: {self.current_state} -> {name}", flush=True)
        self.current_state = name
        self.is_animating = name != "idle"
        return True

    def stop_state(self, name: str) -> bool:
        if name == self.current_state:
            self.restore_idle()
            return True
        return False

    def restore_idle(self) -> None:
        self._transient_timer.stop()
        if self._failsafe_timer.isActive():
            self._failsafe_timer.stop()
        # End the action's timer ownership, including timers suspended by a
        # gesture/reply. Keep those pause reasons until their real lifecycle ends.
        self._paused_timers = []
        self.set_state("idle")
        self.is_animating = False
        self.last_reason = "completed"
        self.pet.set_state("idle", from_manager=True)

    def play_action(self, name: str) -> bool:
        accepted, _reason = self.try_play_action(name)
        return accepted

    def try_play_action(self, name: str) -> tuple[bool, str]:
        if name not in TRANSIENT_ACTIONS:
            raise ValueError(f"Unknown pet action: {name}")
        print(f"[ACTION_MANAGER] play: {name}", flush=True)

        if self._timer_pauses:
            self.last_reason = "busy"
            return False, "busy"

        if name == "sleep" and self.current_state == "sleeping":
            return self._accepted()

        if name == "study_reminder" and self.current_state == "sleeping":
            self.last_reason = "sleeping"
            return False, "sleeping"

        if name not in {"sleep", "wake"} and self.current_state == "dancing":
            self.last_reason = "busy"
            print("[ACTION_MANAGER] blocked: action during dancing", flush=True)
            return False, "busy"

        if name == "nod" and (self.is_animating or self.current_state == "sleeping"):
            # Message bubbles must not replace the owning action's timer.
            self.last_reason = "busy"
            return False, "busy"

        if name in {"jump", "shake", "scale", "dance"} and self.current_state == "sleeping":
            self.play_action("wake")
            self.last_reason = "sleeping"
            return False, "sleeping"

        if name == "jump":
            self._mark_transient(self._visual_duration("jumping", 450))
            self.pet.set_state("happy", from_manager=True)
            return self._accepted()
        if name == "nod":
            self._mark_transient(260)
            self.pet.actions.speaking_nod()
            return self._accepted()
        if name == "shake":
            self._mark_transient(self._visual_duration("waiting", 520))
            self.pet.actions.thinking()
            return self._accepted()
        if name in {"scale", "study_reminder"}:
            self.set_state("reminding")
            duration = self._visual_duration("review", 520) if name == "study_reminder" else 520
            self._mark_transient(duration, restore_idle=True)
            if name == "scale":
                self.pet.actions.study_reminder(sprite=False)
            else:
                self.pet.set_state("study", from_manager=True)
            return self._accepted()
        if name == "sleep":
            if self.current_state == "dancing":
                self.pet._stop_dance_frames()
            self._transient_timer.stop()
            self.set_state("sleeping")
            self.pet.set_state("sleep", from_manager=True)
            return self._accepted()
        if name == "wake":
            if self.current_state == "dancing":
                self.pet._stop_dance_frames()
            self.pet.wake(from_manager=True)
            self.restore_idle()
            present = getattr(self.pet, "present_wake_animation", None)
            if present is not None:
                present()
            return self._accepted()
        if name == "dance":
            try:
                self._transient_timer.stop()
                started = self.pet._start_dance_frames()
                if not started:
                    self.restore_idle()
                    self.last_reason = "action_failed"
                    return False, "action_failed"
                self.set_state("dancing")
                self.last_reason = "running"
                self._failsafe_timer.start(30000)
                return True, "running"
            except Exception as error:
                print(
                    f"[ACTION_MANAGER] dance failed: {type(error).__name__}",
                    flush=True,
                )
                self.restore_idle()
                self.last_reason = "exception"
                return False, "exception"

        self.last_reason = "action_failed"
        return False, "action_failed"

    def cancel_current_action(self) -> bool:
        if self.current_state == "dancing":
            self.pet._stop_dance_frames()
            self.last_reason = "cancelled"
            return True
        if self.current_state != "idle":
            self.restore_idle()
            self.last_reason = "cancelled"
            return True
        return False

    def _accepted(self) -> tuple[bool, str]:
        self.last_reason = "running"
        return True, "running"

    def _failsafe_release(self) -> None:
        if self.current_state == "dancing":
            print("[ACTION_MANAGER] dance failsafe release", flush=True)
            try:
                self.pet._stop_dance_frames()
            finally:
                if self.current_state != "idle":
                    self.restore_idle()

    def _mark_transient(self, duration_ms: int, restore_idle: bool = False) -> None:
        self.is_animating = True
        self._transient_restore_idle = restore_idle
        self._transient_timer.start(duration_ms)

    def _visual_duration(self, name: str, fallback: int) -> int:
        duration = getattr(self.pet, "sprite_action_duration", None)
        return max(fallback, duration(name)) if duration is not None else fallback

    def _finish_transient(self) -> None:
        if self._transient_restore_idle:
            self.restore_idle()
        else:
            self._clear_transient()

    def pause_timers(self, reason: str) -> None:
        if not self._timer_pauses:
            self._paused_timers = []
            for timer in (self._transient_timer, self._failsafe_timer):
                if timer.isActive():
                    self._paused_timers.append((timer, max(1, timer.remainingTime())))
                    timer.stop()
        self._timer_pauses.add(reason)

    def resume_timers(self, reason: str) -> None:
        self._timer_pauses.discard(reason)
        if not self._timer_pauses:
            for timer, remaining in self._paused_timers:
                if ((timer is self._failsafe_timer and self.current_state == "dancing")
                        or (timer is self._transient_timer and self.is_animating)):
                    timer.start(remaining)
            self._paused_timers = []

    def shutdown(self) -> None:
        self._timer_pauses.add("closed")
        self._transient_timer.stop()
        self._failsafe_timer.stop()
        self._paused_timers = []

    def _clear_transient(self) -> None:
        if self.current_state == "idle":
            self._transient_timer.stop()
            self._paused_timers = [
                (timer, remaining) for timer, remaining in self._paused_timers
                if timer is not self._transient_timer
            ]
            self.is_animating = False
