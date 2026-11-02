"""Prepared component checks; run via isolated runner in an assigned Qt slot."""
import json
import os
from zipfile import ZipFile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from frontend.storm_magic_preview import (
    CANVAS_SIZE, FOOT_X, FOOT_Y, FRAME_DIRECTORY, MANIFEST_ENTRY,
    StormMagicPreview, canvas_rect, pet_window_size, read_sequence,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_archive(tmp_path, app, *, bad_frame=None):
    archive = tmp_path / "sample.zip"
    image = QImage(CANVAS_SIZE, CANVAS_SIZE, QImage.Format_ARGB32)
    image.fill(QColor(120, 140, 220, 200))
    png = tmp_path / "synthetic.png"
    assert image.save(str(png))
    durations = [370, 350, 370, 410, 380, 460, 520, 650, 580, 480, 520, 850]
    frames = [dict(index=i, file=f"{FRAME_DIRECTORY}/frame_{i:02d}.png",
                   duration_ms=duration) for i, duration in enumerate(durations, 1)]
    manifest = dict(frame_count_primary=12, frame_size_px=[1400, 1400],
                    frame_duration_unit="milliseconds", frames=frames)
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(MANIFEST_ENTRY, json.dumps(manifest))
        for frame in frames:
            bundle.writestr(frame["file"], b"not a png" if frame["index"] == bad_frame
                            else png.read_bytes())
    return archive, png, manifest


def sample_window(tmp_path, app, **options):
    archive, static, _ = make_archive(tmp_path, app)
    return StormMagicPreview(archive, static, **options)


def test_manifest_twelve_frames_and_5940ms(tmp_path, app):
    archive, _, _ = make_archive(tmp_path, app)
    frames = read_sequence(archive)
    assert len(frames) == 12
    assert sum(frame.duration_ms for frame in frames) == 5940


@pytest.mark.parametrize("invalid", ["missing", "path", "duration", "bad_manifest"])
def test_manifest_rejects_missing_path_duration_and_bad_json(tmp_path, app, invalid):
    archive, _, manifest = make_archive(tmp_path, app)
    with ZipFile(archive) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    if invalid == "missing":
        entries.pop(manifest["frames"][2]["file"])
    elif invalid == "path":
        manifest["frames"][0]["file"] = "../../outside.png"
    elif invalid == "duration":
        manifest["frames"][0]["duration_ms"] = 1
    entries[MANIFEST_ENTRY] = (b"broken" if invalid == "bad_manifest"
                               else json.dumps(manifest).encode())
    with ZipFile(archive, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    with pytest.raises((ValueError, KeyError)):
        read_sequence(archive)


@pytest.mark.parametrize("scale", [0.65, 1.0, 1.5, 1.8])
def test_pet_size_and_uncropped_square_canvas(tmp_path, app, scale):
    window = sample_window(tmp_path, app, pet_scale=scale)
    try:
        expected = QSize(max(100, int(168 * scale)), max(114, int(190 * scale)))
        assert window.size() == pet_window_size(scale) == expected
        assert window.rect().contains(window.frame_rect)
        assert window.frame_rect.width() == window.frame_rect.height()
        assert window.frame_rect.bottom() == window.rect().bottom()
        normal = QRect(window.geometry())
        feet = window.mapToGlobal(window.foot_anchor)
        window.play_once()
        assert window.geometry() == normal
        assert window.mapToGlobal(window.foot_anchor) == feet
        window.stop()
        assert window.geometry() == normal
    finally:
        window.close()


def test_default_size_and_150_percent_cache(tmp_path, app):
    class Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1280, 900)

        def devicePixelRatio(self):
            return 1.5

    window = sample_window(tmp_path, app, screen=Screen())
    try:
        assert window.size() == QSize(168, 190)
        assert window.frame_rect == QRect(0, 22, 168, 168)
        assert canvas_rect(window.size()) == window.frame_rect
        assert all(frame.width() == frame.height() == 252 for frame in window.frames)
        assert all(frame.devicePixelRatio() == 1.5 for frame in window.frames)
        assert window.foot_anchor.x() == round(168 * FOOT_X)
        assert window.foot_anchor.y() == 22 + round(168 * FOOT_Y)
    finally:
        window.close()


def test_complete_once_and_restore(tmp_path, app):
    window = sample_window(tmp_path, app)
    try:
        normal = QRect(window.geometry())
        static = window.static_pixmap.cacheKey()
        reasons = []
        window.playback_finished.connect(reasons.append)
        window.play_once()
        for index, spec in enumerate(window.sequence):
            assert window.geometry() == normal
            assert window.frame_index == index
            assert window.timer.interval() == spec.duration_ms
            window.timer.stop()  # Drive each timer boundary without waiting.
            window._advance()
        assert reasons == ["completed"]
        assert not window.playing and not window.timer.isActive()
        assert window.geometry() == normal
        assert window.current_pixmap.cacheKey() == static
        window._advance()
        assert reasons == ["completed"]
        window.play_once()
        assert window.frame_index == 0 and window.geometry() == normal
        window.stop()
    finally:
        window.close()


@pytest.mark.parametrize("ending", ["stop", "close", "error"])
def test_all_endings_restore_and_cancel_timers(tmp_path, app, monkeypatch, ending):
    window = sample_window(tmp_path, app)
    normal = QRect(window.geometry())
    try:
        window.play_once()
        if ending == "error":
            def fail():
                raise RuntimeError("synthetic playback failure")
            monkeypatch.setattr(window, "_display_frame", fail)
            window._advance()
            assert "synthetic playback failure" in window.last_error
        elif ending == "close":
            window.close()
        else:
            window.stop()
        assert not window.playing
        assert not window.timer.isActive() and not window.start_timer.isActive()
        assert window.geometry() == normal
        assert window.current_pixmap.cacheKey() == window.static_pixmap.cacheKey()
    finally:
        window.close()


def test_bad_png_rejected_before_playback(tmp_path, app):
    archive, static, _ = make_archive(tmp_path, app, bad_frame=4)
    with pytest.raises(ValueError, match="Invalid RGBA"):
        StormMagicPreview(archive, static)


def test_no_focus_and_cancel_delayed_start(tmp_path, app):
    window = sample_window(tmp_path, app)
    try:
        assert window.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert window.testAttribute(Qt.WA_ShowWithoutActivating)
        window.schedule_once()
        window.stop()
        assert not window.start_timer.isActive()
        assert not window.timer.isActive()
    finally:
        window.close()
