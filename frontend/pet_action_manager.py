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
        self.set_state("idle")
        self.is_animating = False
        self.pet.set_state("idle", from_manager=True)

    def play_action(self, name: str) -> bool:
        if name not in TRANSIENT_ACTIONS:
            raise ValueError(f"Unknown pet action: {name}")
        print(f"[ACTION_MANAGER] play: {name}", flush=True)

        if name in {"study_reminder", "scale"} and self.current_state == "dancing":
            print("[ACTION_MANAGER] blocked: study reminder during dancing", flush=True)
            return False

        if name in {"jump", "shake", "scale", "study_reminder", "dance"} and self.current_state == "sleeping":
            self.play_action("wake")
            return False

        if name == "jump":
            self._mark_transient(450)
            self.pet.set_state("happy", from_manager=True)
            return True
        if name == "nod":
            self._mark_transient(260)
            self.pet.actions.speaking_nod()
            return True
        if name == "shake":
            self._mark_transient(520)
            self.pet.actions.thinking()
            return True
        if name in {"scale", "study_reminder"}:
            self.set_state("reminding")
            self._mark_transient(520, restore_idle=True)
            self.pet.set_state("study", from_manager=True)
            return True
        if name == "sleep":
            self.set_state("sleeping")
            self.pet.set_state("sleep", from_manager=True)
            return True
        if name == "wake":
            self.pet.wake(from_manager=True)
            self.restore_idle()
            return True
        if name == "dance":
            started = self.pet._start_dance_frames()
            if started:
                self.set_state("dancing")
            return started

        return False

    def _mark_transient(self, duration_ms: int, restore_idle: bool = False) -> None:
        self.is_animating = True
        if restore_idle:
            QTimer.singleShot(duration_ms, self.restore_idle)
        else:
            QTimer.singleShot(duration_ms, self._clear_transient)

    def _clear_transient(self) -> None:
        if self.current_state == "idle":
            self.is_animating = False
