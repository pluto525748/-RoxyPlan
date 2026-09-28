import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from frontend.pet_action_manager import PetActionManager


class FakePet(QObject):
    def __init__(self, *, fail=False):
        super().__init__()
        self.fail = fail
        self.states = []

    def _start_dance_frames(self):
        if self.fail:
            raise RuntimeError("frame failure")
        return True

    def _stop_dance_frames(self):
        self.manager.restore_idle()

    def set_state(self, name, from_manager=False):
        self.states.append(name)


def make_manager(*, fail=False):
    QApplication.instance() or QApplication([])
    pet = FakePet(fail=fail)
    pet.manager = PetActionManager(pet)
    return pet, pet.manager


def test_dance_failure_always_releases_idle():
    _pet, manager = make_manager(fail=True)
    accepted, reason = manager.try_play_action("dance")
    assert not accepted and reason == "exception"
    assert manager.current_state == "idle"


def test_cancelled_dance_releases_idle():
    _pet, manager = make_manager()
    assert manager.try_play_action("dance")[0]
    assert manager.current_state == "dancing"
    assert manager.cancel_current_action()
    assert manager.current_state == "idle"
