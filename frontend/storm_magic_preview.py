"""Standalone storm sample: ZIP input only, no production pet or business data."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


MANIFEST_ENTRY = "06_交付说明/manifest.json"
FRAME_DIRECTORY = "01_播放序列_已对齐"
CANVAS_SIZE = 1400
# The supplied alignment CSV translates approximate feet to (700, 1236).
FOOT_X = 0.5
FOOT_Y = 1236 / CANVAS_SIZE


@dataclass(frozen=True)
class FrameSpec:
    file: str
    duration_ms: int


def read_sequence(archive: Path) -> tuple[FrameSpec, ...]:
    """Validate exactly the primary manifest; do not extract or execute entries."""
    with ZipFile(archive) as bundle:
        manifest_info = bundle.getinfo(MANIFEST_ENTRY)
        if manifest_info.file_size > 65536:
            raise ValueError("Manifest is too large")
        document = json.loads(bundle.read(manifest_info).decode("utf-8-sig"))
        if not isinstance(document, dict):
            raise ValueError("Manifest must be an object")
        if (document.get("frame_count_primary") != 12
                or document.get("frame_size_px") != [1400, 1400]
                or document.get("frame_duration_unit") != "milliseconds"):
            raise ValueError("Unsupported primary sequence")
        frames = document.get("frames")
        if not isinstance(frames, list) or len(frames) != 12:
            raise ValueError("Expected twelve primary frames")
        result = []
        names = bundle.namelist()
        for index, frame in enumerate(frames, 1):
            expected = f"{FRAME_DIRECTORY}/frame_{index:02d}.png"
            if (not isinstance(frame, dict) or frame.get("index") != index
                    or frame.get("file") != expected):
                raise ValueError("Frame order/path is not the approved primary sequence")
            duration = frame.get("duration_ms")
            if type(duration) is not int or not 1 <= duration <= 5000:
                raise ValueError("Invalid frame duration")
            if names.count(expected) != 1:
                raise ValueError("Missing or duplicate primary frame")
            if bundle.getinfo(expected).file_size > 16 * 1024 * 1024:
                raise ValueError("Frame is too large")
            result.append(FrameSpec(expected, duration))
        if names.count(MANIFEST_ENTRY) != 1:
            raise ValueError("Duplicate manifest")
        if sum(frame.duration_ms for frame in result) != 5940:
            raise ValueError("Primary sequence must total 5940ms")
        return tuple(result)


def pet_window_size(scale: float = 1.0) -> QSize:
    """Mirror DesktopPet sizing without importing it or reading user settings."""
    if not math.isfinite(scale) or not 0.65 <= scale <= 1.8:
        raise ValueError("Pet scale must be between 0.65 and 1.8")
    return QSize(max(100, int(168 * scale)), max(114, int(190 * scale)))


def canvas_rect(size: QSize) -> QRect:
    """Contain the entire square canvas, bottom aligned like existing dance."""
    side = min(size.width(), size.height())
    if side <= 0:
        raise ValueError("Canvas needs a positive window size")
    return QRect((size.width() - side) // 2, size.height() - side, side, side)


def _read_frame(bundle: ZipFile, frame: FrameSpec, physical_side: int,
                dpr: float) -> QPixmap:
    data = bundle.read(frame.file)
    # Check PNG IHDR before Qt allocates a decoded image for an untrusted input.
    if (len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[12:16] != b"IHDR"
            or int.from_bytes(data[16:20], "big") != CANVAS_SIZE
            or int.from_bytes(data[20:24], "big") != CANVAS_SIZE
            or data[25] != 6):
        raise ValueError(f"Invalid RGBA primary frame: {frame.file}")
    image = QImage.fromData(data, "PNG")
    if (image.isNull() or image.width() != CANVAS_SIZE
            or image.height() != CANVAS_SIZE or not image.hasAlphaChannel()):
        raise ValueError(f"Invalid RGBA primary frame: {frame.file}")
    image = image.scaled(physical_side, physical_side, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        raise ValueError("Frame conversion failed")
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class StormMagicPreview(QWidget):
    """One isolated window; left plays, right stops/closes, middle closes."""

    playback_finished = Signal(str)
    playback_error = Signal(str)

    def __init__(self, archive: Path, static_image: Path, *, pet_scale=1.0,
                 screen=None):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("风暴魔术独立样板")
        self.setToolTip("左键播放一次；右键中止（待机时关闭）；中键关闭")
        size = pet_window_size(float(pet_scale))
        self.sequence = read_sequence(Path(archive))
        self._screen = screen or self.screen()
        if self._screen is None:
            raise ValueError("No screen available")
        self._available = self._screen.availableGeometry()
        if (self._available.width() < size.width()
                or self._available.height() < size.height()):
            raise ValueError("Screen is smaller than the normal preview")
        self._normal_geometry = QRect(QPoint(0, 0), size)
        self._normal_geometry.moveCenter(self._available.center())
        self.setFixedSize(size)
        self.setGeometry(self._normal_geometry)
        self.frame_rect = canvas_rect(size)
        self.foot_anchor = QPoint(
            self.frame_rect.x() + round(self.frame_rect.width() * FOOT_X),
            self.frame_rect.y() + round(self.frame_rect.height() * FOOT_Y),
        )
        dpr = max(1.0, float(self._screen.devicePixelRatio()))
        physical_side = max(1, round(self.frame_rect.width() * dpr))
        with ZipFile(archive) as bundle:
            self.frames = tuple(_read_frame(bundle, frame, physical_side, dpr)
                                for frame in self.sequence)
        normal_image = QImage(str(static_image))
        if normal_image.isNull():
            raise ValueError("Normal static image is unavailable")
        normal_image = normal_image.scaled(
            round(size.width() * dpr), round(self.foot_anchor.y() * dpr),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.static_pixmap = QPixmap.fromImage(normal_image)
        self.static_pixmap.setDevicePixelRatio(dpr)
        self.current_pixmap = self.static_pixmap
        self.playing = False
        self.frame_index = -1
        self.last_error = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._advance)
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.timeout.connect(self.play_once)

    def schedule_once(self, delay_ms=1200):
        if type(delay_ms) is not int or not 0 <= delay_ms <= 10000:
            raise ValueError("Delay must be 0..10000ms")
        self.start_timer.start(delay_ms)

    def play_once(self):
        if self.playing:
            return
        self.start_timer.stop()
        # Snapshot only this sample's geometry, never a running DesktopPet.
        self._normal_geometry = QRect(self.geometry())
        try:
            if not self._screen.availableGeometry().contains(self._normal_geometry):
                raise ValueError("Preview is outside the selected screen")
            self.playing = True
            self.frame_index = 0
            self._display_frame()
        except Exception as error:
            self._fail(error)

    def _display_frame(self):
        self.current_pixmap = self.frames[self.frame_index]
        self.update()
        self.timer.start(self.sequence[self.frame_index].duration_ms)

    def _advance(self):
        if not self.playing:
            return
        try:
            self.frame_index += 1
            if self.frame_index == len(self.sequence):
                self.stop("completed")
            else:
                self._display_frame()
        except Exception as error:
            self._fail(error)

    def _fail(self, error):
        self.last_error = f"{type(error).__name__}: {error}"
        self.stop("error")
        self.playback_error.emit(self.last_error)

    def stop(self, reason="interrupted"):
        self.timer.stop()
        self.start_timer.stop()
        self.playing = False
        self.frame_index = -1
        self.current_pixmap = self.static_pixmap
        self.setGeometry(self._normal_geometry)
        self.update()
        self.playback_finished.emit(reason)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.playing:
            painter.drawPixmap(self.frame_rect, self.current_pixmap)
        else:
            width = round(self.static_pixmap.width() / self.static_pixmap.devicePixelRatio())
            height = round(self.static_pixmap.height() / self.static_pixmap.devicePixelRatio())
            painter.drawPixmap(QPoint(round((self.width() - width) / 2),
                                      self.foot_anchor.y() - height),
                               self.static_pixmap)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MiddleButton:
            self.close()
        elif event.button() == Qt.RightButton:
            if self.playing or self.start_timer.isActive():
                self.stop()
            else:
                self.close()
        elif event.button() == Qt.LeftButton:
            self.play_once()

    def closeEvent(self, event):
        self.stop("closed")
        super().closeEvent(event)
