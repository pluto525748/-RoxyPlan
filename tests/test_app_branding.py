"""Check icon inheritance in the real isolated desktop composition."""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from frontend.app_branding import APP_ICON_PATH, configure_application_icon
from frontend.today_growth_dialog import TodayGrowthDialog
from scripts.preview_today_growth import example_snapshot
from tests.test_ui_integration import desktop  # noqa: F401; isolated fixture


def test_all_windows_inherit_application_icon(desktop):
    app, pet, _root = desktop
    previous = app.windowIcon()
    try:
        configure_application_icon(app)
        assert not app.windowIcon().isNull()
        pet.open_chat_window()
        chat = pet.chat_window
        pet.open_settings_dialog()
        chat.open_memory_dialog()
        chat.open_chat_history_dialog()
        pet.open_today_growth_dialog()
        pet.open_growth_dialog()
        for window in (pet, chat, pet.settings_dialog, chat.memory_dialog,
                       chat.chat_history_dialog, pet.today_growth_dialog,
                       pet.growth_dialog):
            assert window.windowIcon().cacheKey() == app.windowIcon().cacheKey()
        # Native-size resource samples, not desktop/window screenshots.
        run = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
        for side in (16, 24, 32, 48):
            pixmap = app.windowIcon().pixmap(side, side)
            assert not pixmap.isNull()
            assert pixmap.width() / pixmap.devicePixelRatio() <= side
            assert pixmap.height() / pixmap.devicePixelRatio() <= side
            if run:
                assert pixmap.save(str(Path(run) / "artifacts" / f"app-icon-{side}.png"))
    finally:
        if pet.chat_window is not None:
            if pet.chat_window.chat_history_dialog is not None:
                pet.chat_window.chat_history_dialog.close()
            if pet.chat_window.memory_dialog is not None:
                pet.chat_window.memory_dialog.close()
        app.setWindowIcon(previous)


def test_today_decoration_fits_original_space_without_crop():
    QApplication.instance() or QApplication([])
    window = TodayGrowthDialog(example_snapshot("long"))
    try:
        assert window.size().width() == 660
        assert window.size().height() == round(650 * 660 / 900)
        image = window.pet_image
        assert image.width() == window._u(105)
        assert image.height() == window._u(78)
        source = QPixmap(str(APP_ICON_PATH))
        assert not source.isNull()
        displayed = image.pixmap()
        assert not displayed.isNull()
        ratio = image.devicePixelRatioF()
        expected = source.scaled(int(image.width() * ratio),
                                 int(image.height() * ratio),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        # Pixel equality proves full source + smooth fit, not old fixed crop.
        actual_image = displayed.toImage()
        actual_image.setDevicePixelRatio(1)
        assert actual_image == expected.toImage()
        assert displayed.width() / displayed.devicePixelRatio() <= image.width()
        assert displayed.height() / displayed.devicePixelRatio() <= image.height()
    finally:
        window.close()
