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
        if self._failsafe_timer.isActive():
            self._failsafe_timer.stop()
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

        if name == "dance" and self.current_state == "dancing":
            self.last_reason = "busy"
            print("[ACTION_MANAGER] blocked: dance already running", flush=True)
            return False, "busy"

        if name in {"study_reminder", "scale"} and self.current_state == "dancing":
            print("[ACTION_MANAGER] blocked: study reminder during dancing", flush=True)
            self.last_reason = "busy"
            return False, "busy"

        if name in {"jump", "shake", "scale", "study_reminder", "dance"} and self.current_state == "sleeping":
            self.play_action("wake")
            self.last_reason = "sleeping"
            return False, "sleeping"

        if name == "jump":
            self._mark_transient(450)
            self.pet.set_state("happy", from_manager=True)
            return self._accepted()
        if name == "nod":
            self._mark_transient(260)
            self.pet.actions.speaking_nod()
            return self._accepted()
        if name == "shake":
            self._mark_transient(520)
            self.pet.actions.thinking()
            return self._accepted()
        if name in {"scale", "study_reminder"}:
            self.set_state("reminding")
            self._mark_transient(520, restore_idle=True)
            self.pet.set_state("study", from_manager=True)
            return self._accepted()
        if name == "sleep":
            self.set_state("sleeping")
            self.pet.set_state("sleep", from_manager=True)
            return self._accepted()
        if name == "wake":
            self.pet.wake(from_manager=True)
            self.restore_idle()
            return self._accepted()
        if name == "dance":
            try:
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
        if restore_idle:
            QTimer.singleShot(duration_ms, self.restore_idle)
        else:
            QTimer.singleShot(duration_ms, self._clear_transient)

    def _clear_transient(self) -> None:
        if self.current_state == "idle":
            self.is_animating = False
