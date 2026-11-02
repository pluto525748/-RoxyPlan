"""Exercise the combined worker UI against one isolated desktop composition."""

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication

import frontend.pet_app as pet_app
from frontend.growth_dialog import GrowthDialog
from frontend.today_growth_dialog import TodayGrowthDialog
from modules.development_log import DevelopmentLog
from scripts.preview_chat import OfflinePreviewClient


class OfflineRoutedClient(OfflinePreviewClient):
    def __init__(self, data_root, **_kwargs):
        super().__init__(data_root)


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "desktop"
    config = root / "data" / "pet_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "test_mode": False,
        "auto_tips_enabled": False,
        "proactive_enabled": False,
        "evening_review_enabled": False,
        "idle_nudge_enabled": False,
        "auto_complete_yesterday_review": False,
        "pet_always_on_top": False,
        "chat_history_enabled": True,
        "enable_memory_candidates": False,
        "agent_max_steps": 2,
    }), encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(pet_app, "RoutedLLMClient", OfflineRoutedClient)
    pet = pet_app._acceptance_desktop_pet(
        root, DevelopmentLog(root / "logs" / "development", enabled=False)
    )
    try:
        yield app, pet, root
    finally:
        for window in (pet.today_growth_dialog, pet.growth_dialog,
                       pet.settings_dialog, pet.chat_window, pet.bubble):
            if window is not None:
                window.close()
        for timer in pet.findChildren(QTimer):
            timer.stop()
        pet.actions.stop_idle_breathing()
        pet.close()
        app.processEvents()


def _choose_menu(pet, monkeypatch, label):
    menu, actions = pet._create_context_menu()
    chosen = next(action for action in menu.actions() if action.text() == label)
    # PySide's native exec cannot reliably be replaced on the Qt class. Keep
    # the actual action identities, replacing only the blocking selection.
    with monkeypatch.context() as patch:
        patch.setattr(pet, "_create_context_menu", lambda: (
            SimpleNamespace(exec=lambda _position: chosen), actions
        ))
        pet.contextMenuEvent(SimpleNamespace(globalPos=lambda: QPoint(0, 0)))
    menu.close()


def test_pet_growth_entries_open_correct_shared_panels(desktop, monkeypatch):
    app, pet, _root = desktop
    first = pet.growth_service.add_task("通过正式菜单查看的计划")
    _choose_menu(pet, monkeypatch, "打开聊天")
    chat = pet.chat_window
    chat.growth_button.click()
    app.processEvents()
    panel = pet.today_growth_dialog
    assert isinstance(panel, TodayGrowthDialog)
    assert panel.growth_manager is pet.growth_service
    assert panel.tasks[0]["uid"] == first["uid"]
    timer = panel.refresh_timer
    chat.growth_button.click()
    assert pet.today_growth_dialog is panel
    assert panel.refresh_timer is timer
    panel.details_button.click()
    app.processEvents()
    assert isinstance(pet.growth_dialog, GrowthDialog)
    assert pet.growth_dialog.growth_manager is panel.growth_manager
    pet.growth_dialog.plan_input.setText("由详细面板加入的计划")
    pet.growth_dialog.add_plan()
    panel.refresh_from_service()
    assert len(panel.tasks) == 2
    panel.close()
    app.processEvents()
    assert not timer.isActive()
    assert pet.today_growth_dialog is None
    chat.growth_button.click()
    assert pet.today_growth_dialog is not panel
    assert len(pet.today_growth_dialog.tasks) == 2


def test_chat_and_settings_keep_shared_data_and_hidden_configuration(desktop):
    app, pet, root = desktop
    pet.open_chat_window()
    app.processEvents()
    chat = pet.chat_window
    assert chat.growth_service is pet.growth_service
    assert chat.chat_history_manager is pet.chat_history_manager
    chat.growth_button.click()
    assert pet.today_growth_dialog.growth_manager is chat.growth_service
    pet.today_growth_dialog.details_button.click()
    assert pet.growth_dialog.growth_manager is chat.growth_service

    config = root / "data" / "pet_config.json"
    before = config.read_bytes()
    pet.open_settings_dialog()
    settings = pet.settings_dialog
    assert settings.agent_max_steps.value() == 2
    assert settings.agent_max_steps.isHidden()
    assert settings.enable_memory_candidates.isHidden()
    assert settings.open_today_growth_button.isHidden()
    settings.reject()
    app.processEvents()
    assert config.read_bytes() == before
    assert pet.today_growth_dialog.isVisible()

    chat.memory_service.memory_manager.add_memory(
        "集成核验关键词：只在隔离库保存", category="preference", source="integration"
    )
    chat.open_memory_dialog()
    memory = chat.memory_dialog
    assert memory.memory_service is chat.memory_service
    assert [memory.tabs.tabText(i) for i in range(memory.tabs.count())] == [
        "长期记忆", "冲突", "已归档"
    ]
    memory.search_input.setText("集成核验关键词")
    memory.refresh_memories()
    assert memory.memory_table.rowCount() == 1
    # A no-result query must not share the stored keyword/tags: the existing
    # search intentionally includes tag overlap and simple string similarity.
    memory.search_input.setText("火星甜椒种植")
    memory.refresh_memories()
    assert memory.memory_table.rowCount() == 0
    memory.close()
