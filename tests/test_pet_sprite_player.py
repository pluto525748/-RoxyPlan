"""Author source/sequence contracts. Run only through the isolated Qt runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from frontend.pet_sprite_player import AUTHOR_SHA256, FRAME_COUNTS, PetSpritePlayer


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def player(app):
    result = PetSpritePlayer(ROOT)
    yield result
    result.shutdown()


def test_exact_approved_transparent_atlas(player):
    assert hashlib.sha256((ROOT / "assets/pet/roxy_sprite_atlas.webp").read_bytes()).hexdigest() == AUTHOR_SHA256
    assert player.available, player.error
    image = player.atlas.toImage()
    assert (image.width(), image.height()) == (1536, 1872)
    assert image.hasAlphaChannel()
    assert image.pixelColor(0, 0).alpha() == 0


@pytest.mark.parametrize("name,count", FRAME_COUNTS.items())
def test_all_author_frames_once_in_original_order_without_blank_cells(player, name, count):
    finished = []
    player.finished.connect(finished.append)
    assert player.play(name)
    row = list(FRAME_COUNTS).index(name)
    seen = []
    for column in range(count):
        rect = player.frame_rect
        seen.append((rect.x(), rect.y(), rect.width(), rect.height()))
        assert rect.x() == column * 192
        assert rect.y() == row * 208
        assert rect.width() == 192 and rect.height() == 208
        assert not player.atlas.copy(rect).isNull()
        player.advance()
    assert len(set(seen)) == count
    assert finished == [name]
    assert not player.active and not player.timer.isActive()
    # The final valid cell stays visible; never visit unused cells in that row.
    assert player.frame_rect.x() == (count - 1) * 192


def test_repeat_pause_and_idempotent_play_preserve_frame(player):
    assert player.play("running-left", repeat=True)
    player.advance()
    original = player.frame_rect
    player.pause("thinking")
    player.pause("thinking")
    player.pause("drag")
    assert player.play("running-left", repeat=True)
    player.advance()
    assert player.frame_rect == original
    assert not player.timer.isActive()
    player.resume("thinking")
    assert not player.timer.isActive()
    player.resume("drag")
    assert player.timer.isActive()
    for _ in range(8):
        player.advance()
    assert player.frame_rect == original and player.active


def test_borrowed_drag_clip_restores_suspended_action_without_restarting(player):
    player.play("review")
    player.advance()
    player.pause("drag")
    snapshot = player.snapshot()
    original = player.frame_rect
    player.resume("drag")
    player.play("running-right", repeat=True)
    player.advance()
    player.restore(snapshot, pauses={"thinking"})
    assert player.animation == "review" and player.frame_rect == original
    assert not player.timer.isActive()
    player.resume("thinking")
    assert player.timer.isActive()


@pytest.mark.parametrize("bad", [[], {"animations": []}, {"animations": {"idle": []}}])
def test_invalid_layout_is_unavailable_instead_of_crashing(app, tmp_path, bad):
    folder = tmp_path / "assets/pet"
    folder.mkdir(parents=True)
    original = json.loads((ROOT / "assets/pet/roxy_sprite_layout.json").read_text(encoding="utf-8"))
    layout = bad if isinstance(bad, list) else {**original, **bad}
    (folder / "roxy_sprite_layout.json").write_text(json.dumps(layout), encoding="utf-8")
    player = PetSpritePlayer(tmp_path)
    assert not player.available
    assert player.error == "ValueError"
    assert not player.play("idle")
    player.shutdown()


def test_missing_atlas_and_shutdown_cannot_start_timers(app, tmp_path, player):
    missing = PetSpritePlayer(tmp_path)
    assert not missing.play("idle") and not missing.timer.isActive()
    player.play("idle", repeat=True)
    player.pause("thinking")
    player.shutdown()
    player.resume("thinking")
    assert not player.play("idle") and not player.timer.isActive()
    missing.shutdown()
