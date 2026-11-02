"""Original author atlas playback; never moves windows or interprets Agent text."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap


AUTHOR_SHA256 = "946835f048cd9c885130b3eba1bde2b7f45b25d2cb1c067bc1fc20094e0cae18"
FRAME_COUNTS = dict(zip(
    ("idle", "running-right", "running-left", "waving", "jumping", "failed", "waiting", "running", "review"),
    (6, 8, 8, 4, 5, 8, 6, 6, 6),
))
CELL_WIDTH, CELL_HEIGHT = 192, 208


class PetSpritePlayer(QObject):
    frame_changed = Signal()
    finished = Signal(str)

    def __init__(self, resource_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.atlas = QPixmap()
        self.animations = {}
        self.animation = "idle"
        self.frame_index = 0
        self.repeat = False
        self.active = False
        self._pauses: set[str] = set()
        self._remaining_ms = 0
        self.error = ""
        self._closed = False
        try:
            folder = Path(resource_root) / "assets/pet"
            layout = json.loads((folder / "roxy_sprite_layout.json").read_text(encoding="utf-8"))
            if (not isinstance(layout, dict) or layout.get("schema_version") != 1 or layout.get("atlas") != "roxy_sprite_atlas.webp"
                    or layout.get("image_size") != [1536, 1872]
                    or layout.get("cell_size") != [CELL_WIDTH, CELL_HEIGHT]):
                raise ValueError("Unsupported sprite layout")
            definitions = layout["animations"]
            if not isinstance(definitions, dict) or set(definitions) != set(FRAME_COUNTS):
                raise ValueError("Incomplete author animation set")
            for row, (name, count) in enumerate(FRAME_COUNTS.items()):
                spec = definitions[name]
                if (not isinstance(spec, dict) or spec.get("row") != row or spec.get("frames") != list(range(count))
                        or type(spec.get("frame_ms")) is not int or not 16 <= spec["frame_ms"] <= 5000):
                    raise ValueError("Invalid author frame order")
            path = folder / layout["atlas"]
            if layout.get("sha256") != AUTHOR_SHA256 or hashlib.sha256(path.read_bytes()).hexdigest() != AUTHOR_SHA256:
                raise ValueError("Author atlas differs from approved source")
            pixmap = QPixmap(str(path))
            if pixmap.isNull() or pixmap.size().width() != 1536 or pixmap.size().height() != 1872 or not pixmap.hasAlphaChannel():
                raise ValueError("Author atlas could not be decoded with transparency")
            self.atlas = pixmap
            self.animations = definitions
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = type(error).__name__

    @property
    def available(self) -> bool:
        return bool(self.animations) and not self.atlas.isNull()

    @property
    def frame_rect(self) -> QRect:
        spec = self.animations.get(self.animation)
        if spec is None:
            return QRect()
        column = spec["frames"][self.frame_index]
        return QRect(column * CELL_WIDTH, spec["row"] * CELL_HEIGHT, CELL_WIDTH, CELL_HEIGHT)

    def duration(self, name: str) -> int:
        spec = self.animations.get(name)
        return len(spec["frames"]) * spec["frame_ms"] if spec else 0

    def play(self, name: str, *, repeat: bool = False) -> bool:
        if name not in FRAME_COUNTS:
            raise ValueError("Unknown author animation")
        if self._closed or not self.available:
            return False
        if self.active and self.animation == name and self.repeat == repeat:
            return True
        self.timer.stop()
        self.animation, self.frame_index, self.repeat = name, 0, repeat
        self.active = True
        self._remaining_ms = self.animations[name]["frame_ms"]
        self.frame_changed.emit()
        if not self._pauses:
            self.timer.start(self._remaining_ms)
        return True

    def advance(self) -> None:
        if not self.active or self._pauses:
            return
        spec = self.animations[self.animation]
        if self.frame_index + 1 < len(spec["frames"]):
            self.frame_index += 1
        elif self.repeat:
            self.frame_index = 0
        else:
            name = self.animation
            self.stop()
            self.finished.emit(name)
            return
        self.frame_changed.emit()
        self.timer.start(spec["frame_ms"])

    def pause(self, reason: str) -> None:
        if not self._pauses and self.timer.isActive():
            self._remaining_ms = max(1, self.timer.remainingTime())
            self.timer.stop()
        self._pauses.add(reason)

    def resume(self, reason: str) -> None:
        self._pauses.discard(reason)
        if not self._pauses and self.active and not self.timer.isActive():
            self.timer.start(max(1, self._remaining_ms))

    def stop(self) -> None:
        self.timer.stop()
        self.active = False

    def snapshot(self) -> tuple[str, int, bool, bool, int]:
        """Save a suspended presentation while dragging borrows the atlas."""
        remaining = self.timer.remainingTime() if self.timer.isActive() else self._remaining_ms
        return self.animation, self.frame_index, self.repeat, self.active, max(1, remaining)

    def restore(self, snapshot: tuple[str, int, bool, bool, int], *, pauses: set[str]) -> None:
        self.timer.stop()
        self.animation, self.frame_index, self.repeat, self.active, self._remaining_ms = snapshot
        self._pauses = set(pauses)
        if self._closed:
            self.active = False
            return
        self.frame_changed.emit()
        if self.active and not self._pauses:
            self.timer.start(self._remaining_ms)

    def shutdown(self) -> None:
        self._closed = True
        self.stop()
        self._pauses.clear()

    def draw(self, painter: QPainter, target: QRectF) -> bool:
        if not self.available:
            return False
        # Source rectangles preserve every original frame, including reused
        # non-adjacent cells. No frame atlas resampling/cache expansion.
        painter.drawPixmap(target, self.atlas, QRectF(self.frame_rect))
        return True
