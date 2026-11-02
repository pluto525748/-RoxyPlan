"""Isolated desktop-pet settings entry and Today Growth window lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialogButtonBox

import frontend.settings_dialog as settings_module
from frontend.desktop_pet import DesktopPet
from frontend.today_growth_dialog import TodayGrowthDialog, VISIBLE_REFRESH_MS
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_synthetic_preview_keeps_its_preview_title() -> None:
    app = QApplication.instance() or QApplication([])
    preview = TodayGrowthDialog(snapshot={"tasks": [], "actions": []})
    try:
        assert "视觉预览" in preview.windowTitle()
        assert not preview.service_mode
    finally:
        preview.close()
        app.processEvents()


def test_pet_settings_entry_reuses_service_panel_and_preserves_unsaved_settings(
    tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_module, "SecretStore", lambda _root: SecretStore(tmp_path))
    monkeypatch.setattr(settings_module, "ModelUsageStore", lambda _root: ModelUsageStore(tmp_path))
    config = tmp_path / "pet_config.json"
    config.write_text(json.dumps({
        "auto_complete_yesterday_review": False,
        "proactive_enabled": False,
        "auto_tips_enabled": False,
        "pet_always_on_top": False,
    }), encoding="utf-8")
    tips = tmp_path / "pet_tips.json"
    tips.write_text(json.dumps(["隔离测试提示"]), encoding="utf-8")
    manager = GrowthManager(tmp_path / "private")
    first = manager.add_task("设置入口的隔离计划", duration_minutes=25)
    history = ChatHistoryManager(tmp_path / "history")
    pet = DesktopPet(
        growth_service=manager,
        chat_history_manager=history,
        config_file=config,
        tips_file=tips,
    )
    saved = []
    panel = None
    reopened = None
    try:
        pet.show()
        pet.open_settings_dialog()
        app.processEvents()
        settings = pet.settings_dialog
        assert settings is not None and settings.isVisible()
        settings.settings_saved.connect(saved.append)
        settings.always_on_top.setChecked(True)
        config_before = config.read_bytes()
        business_before = _files(tmp_path / "private")

        run_root = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
        if run_root:
            artifact_dir = Path(run_root) / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            assert settings.grab().save(str(artifact_dir / "today-growth-settings-entry.png"))

        settings.open_today_growth_button.click()
        app.processEvents()
        panel = pet.today_growth_dialog
        assert isinstance(panel, TodayGrowthDialog)
        assert panel.isVisible() and panel.service_mode
        assert panel.growth_manager is manager
        assert panel.learning_loop.growth_manager is manager
        assert panel.windowTitle() == "RoxyPlan · 今日成长面板"
        assert [task["uid"] for task in panel.tasks] == [first["uid"]]
        assert panel.refresh_timer.isActive()
        assert panel.refresh_timer.interval() == VISIBLE_REFRESH_MS
        assert settings.always_on_top.isChecked()
        assert config.read_bytes() == config_before
        assert _files(tmp_path / "private") == business_before
        assert not saved
        if run_root:
            assert panel.grab().save(str(artifact_dir / "today-growth-settings-opened.png"))

        timer = panel.refresh_timer
        settings.open_today_growth_button.click()
        app.processEvents()
        assert pet.today_growth_dialog is panel
        assert panel.refresh_timer is timer
        assert len([widget for widget in app.topLevelWidgets()
                    if isinstance(widget, TodayGrowthDialog) and widget.isVisible()]) == 1
        assert config.read_bytes() == config_before
        assert _files(tmp_path / "private") == business_before

        cancel = settings.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Cancel
        )
        cancel.click()
        app.processEvents()
        assert pet.settings_dialog is None
        assert panel.isVisible() and timer.isActive()
        assert not saved and config.read_bytes() == config_before

        panel.tasks[0]["_card"].check_box.click()
        app.processEvents()
        assert manager.tasks()[0]["done"] is True
        assert panel.count_label.text().endswith("1/1")
        assert not saved and config.read_bytes() == config_before

        panel.close()
        app.processEvents()
        assert not timer.isActive()
        assert pet.today_growth_dialog is None
        second = manager.add_task("重开后读到的新计划")
        pet.open_settings_dialog()
        settings = pet.settings_dialog
        settings.open_today_growth_button.click()
        app.processEvents()
        reopened = pet.today_growth_dialog
        assert reopened is not None and reopened is not panel
        assert reopened.growth_manager is manager
        assert reopened.refresh_timer.isActive()
        assert {task["uid"] for task in reopened.tasks} == {first["uid"], second["uid"]}

        pet.open_growth_dialog()
        app.processEvents()
        assert pet.growth_dialog is not None and pet.growth_dialog.isVisible()
        assert pet.growth_dialog.growth_manager is manager
    finally:
        if pet.growth_dialog is not None:
            pet.growth_dialog.close()
        if pet.settings_dialog is not None:
            pet.settings_dialog.close()
        if reopened is not None:
            reopened.close()
        elif panel is not None:
            panel.close()
        pet.close()
        app.processEvents()
