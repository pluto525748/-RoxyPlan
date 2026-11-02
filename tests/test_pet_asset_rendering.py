"""Asset pixels must not receive program-drawn closed-eye marks."""
from types import SimpleNamespace
from pathlib import Path
import hashlib

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from frontend.desktop_pet import DesktopPet


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_default_pet_preserves_author_frame_pixels_and_source():
    path = Path(__file__).resolve().parents[1] / "assets/pet/roxy_pet_transparent.png"
    image = QImage(str(path)).convertToFormat(QImage.Format_RGBA8888)
    assert not image.isNull()
    assert (image.width(), image.height()) == (192, 208)
    assert image.hasAlphaChannel()
    assert image.text("Source") == "https://github.com/yznzl/roxy-pet"
    assert hashlib.sha256(image.bits().tobytes()).hexdigest() == (
        "feffd6ae9b9411ead97771518213e8c8929fc9723ac5eefdffae064c8bd1d7f2"
    )


@pytest.mark.parametrize("blinking,state,dancing", [
    (True, "idle", False),
    (False, "sleep", False),
    (True, "sleep", False),
    (True, "idle", True),
])
def test_asset_pixels_unchanged_during_blink_sleep_and_dance(app, blinking, state, dancing):
    source = QImage(192, 208, QImage.Format_ARGB32)
    source.fill(QColor("#b8d5ef"))
    pixmap = QPixmap.fromImage(source)
    pet = SimpleNamespace(
        config={"use_pet_asset": True},
        _dance_pixmap=pixmap if dancing else None,
        _is_blinking=blinking,
        _state=state,
        _load_asset_pixmap=lambda: pixmap,
        _draw_shadow=lambda painter: None,
        _asset_target_rect=lambda image: QRectF(0, 0, 192, 208),
    )
    actual = QImage(source.size(), QImage.Format_ARGB32)
    actual.fill(Qt.transparent)
    painter = QPainter(actual)
    try:
        assert DesktopPet._draw_asset_if_enabled(pet, painter)
    finally:
        painter.end()
    assert actual == source, "Rendered asset contains pixels not present in the source"
