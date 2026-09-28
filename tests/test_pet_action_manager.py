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
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager


def make_pet(root: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    growth = GrowthManager(root / "growth")
    history = ChatHistoryManager(root / "history")
    monkeypatch.setattr(desktop_pet, "GrowthManager", lambda: growth)
    monkeypatch.setattr(
        desktop_pet,
        "ChatHistoryManager",
        lambda **_kwargs: history,
    )
    pet = DesktopPet()
    pet.show_bubble = lambda *args, **kwargs: None
    return app, pet


def test_manager_blocks_study_during_dance(tmp_path, monkeypatch):
    app, pet = make_pet(tmp_path, monkeypatch)
    pet.action_manager.set_state("dancing")

    assert pet.action_manager.play_action("study_reminder") is False
    assert pet.action_manager.current_state == "dancing"

    app.processEvents()


def test_sleeping_jump_wakes_without_jump(tmp_path, monkeypatch):
    app, pet = make_pet(tmp_path, monkeypatch)
    pet._is_sleeping = True
    pet.mark_state("sleep")
    pet.action_manager.set_state("sleeping")

    assert pet.action_manager.play_action("jump") is False
    assert pet.action_manager.current_state == "idle"
    assert pet.state == "idle"
    assert pet._is_sleeping is False

    app.processEvents()


def test_dance_frames_restore_idle(tmp_path, monkeypatch):
    app, pet = make_pet(tmp_path, monkeypatch)
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


def test_context_menu_has_only_user_facing_entries(tmp_path, monkeypatch):
    app, pet = make_pet(tmp_path, monkeypatch)
    menu, _actions = pet._create_context_menu()
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]

    assert labels == [
        "打开聊天",
        "成长面板",
        "记忆管理",
        "设置",
        "跳舞一下",
        "立即鼓励我",
        "进入睡眠",
        "唤醒",
        "退出 Roxy",
    ]
    app.processEvents()
