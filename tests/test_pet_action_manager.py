import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

import frontend.desktop_pet as desktop_pet
from frontend.desktop_pet import DesktopPet


def make_pet():
    app = QApplication.instance() or QApplication([])
    pet = DesktopPet()
    pet.show_bubble = lambda *args, **kwargs: None
    return app, pet


def test_manager_blocks_study_during_dance():
    app, pet = make_pet()
    pet.action_manager.set_state("dancing")

    assert pet.action_manager.play_action("study_reminder") is False
    assert pet.action_manager.current_state == "dancing"

    app.processEvents()


def test_sleeping_jump_wakes_without_jump():
    app, pet = make_pet()
    pet._is_sleeping = True
    pet.mark_state("sleep")
    pet.action_manager.set_state("sleeping")

    assert pet.action_manager.play_action("jump") is False
    assert pet.action_manager.current_state == "idle"
    assert pet.state == "idle"
    assert pet._is_sleeping is False

    app.processEvents()


def test_dance_frames_restore_idle():
    app, pet = make_pet()
    original_dir = desktop_pet.DANCE_FRAMES_DIR

    with tempfile.TemporaryDirectory() as temp_dir:
        frame_dir = Path(temp_dir)
        for index, color in enumerate(("#ff0000", "#00ff00", "#0000ff"), start=1):
            pixmap = QPixmap(32, 32)
            pixmap.fill(QColor(color))
            assert pixmap.save(str(frame_dir / f"dance_{index}.png"))

        desktop_pet.DANCE_FRAMES_DIR = frame_dir
        try:
            assert pet.action_manager.play_action("dance") is True
            assert pet.action_manager.current_state == "dancing"

            QTimer.singleShot(1300, app.quit)
            app.exec()

            assert pet.action_manager.current_state == "idle"
            assert pet._dance_pixmap is None
            assert not pet.dance_timer.isActive()
        finally:
            desktop_pet.DANCE_FRAMES_DIR = original_dir


def test_context_menu_has_only_user_facing_entries():
    app, pet = make_pet()
    menu, _actions = pet._create_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]

    assert labels == [
        "打开聊天",
        "成长面板",
        "设置",
        "跳舞一下",
        "立即鼓励我",
        "进入睡眠",
        "唤醒",
        "退出 Roxy",
    ]
    app.processEvents()


if __name__ == "__main__":
    test_manager_blocks_study_during_dance()
    test_sleeping_jump_wakes_without_jump()
    test_dance_frames_restore_idle()
    test_context_menu_has_only_user_facing_entries()
    print("pet action manager tests passed")
