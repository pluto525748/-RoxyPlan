"""Build the settings dialog decorations under ``assets/ui/settings/``.

Two kinds of assets are produced:

**Sprite slices** from the RoxyPlan UI asset pack V1 sheets (user-provided
sprite sheets, not committed to the repository):

- ``14_分区标题_魔法徽章图集.png`` for the section badges and the header emblem
- ``11_花星书页_小型散件图集.png`` for the small header star ornament

**Drawn control arrows** (``spin_up`` / ``spin_down``) for the number inputs.
Qt draws no arrow once a style sheet customises ``QSpinBox::up-button``, so the
dialog supplies its own arrow images instead of the platform default.

The sheets are 图集 (sprite sheets): every crop window below selects one icon,
alpha-trims it, and centres it on a transparent square canvas.  Re-run only when
the style constants or the source sheets change::

    .venv/Scripts/python.exe tools/build_settings_ui_assets.py \
        --badges  <path to 14_分区标题_魔法徽章图集.png> \
        --sparkles <path to 11_花星书页_小型散件图集.png>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "ui" / "settings"

ALPHA_THRESHOLD = 12
CANVAS_PADDING = 6

# 与设置界面 $scroll_hover 同色系的箭头颜色。
ARROW_COLOR = (110, 119, 160)
ARROW_SIZE = (8, 5)
ARROW_SUPERSAMPLE = 8

# output file: (sheet key, crop box (left, top, right, bottom), canvas size px)
CROPS = {
    "header_emblem.png": ("badges", (508, 366, 748, 616), 128),
    "title_star.png": ("sparkles", (560, 885, 660, 985), 96),
    "section_pet.png": ("badges", (251, 941, 502, 1254), 96),
    "section_companion.png": ("badges", (6, 630, 251, 927), 96),
    "section_model.png": ("badges", (262, 640, 500, 930), 96),
    "section_privacy.png": ("badges", (1010, 648, 1236, 928), 96),
}


def trim_to_content(cell: Image.Image) -> Image.Image:
    alpha = cell.getchannel("A").point(lambda value: 255 if value > ALPHA_THRESHOLD else 0)
    box = alpha.getbbox()
    if box is None:
        raise ValueError("crop window contains no visible pixels")
    return cell.crop(box)


def fit_on_canvas(cell: Image.Image, canvas_size: int) -> Image.Image:
    inner = canvas_size - 2 * CANVAS_PADDING
    width, height = cell.size
    scale = min(inner / width, inner / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    cell = cell.resize(size, Image.LANCZOS)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas.paste(cell, ((canvas_size - size[0]) // 2, (canvas_size - size[1]) // 2), cell)
    return canvas


def make_arrow(direction: str, size: tuple[int, int]) -> Image.Image:
    """Draw a crisp triangular arrow using supersampled polygon rendering."""
    width, height = size
    scale = ARROW_SUPERSAMPLE
    canvas = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if direction == "up":
        points = [(width * scale / 2, 0), (width * scale - 1, height * scale - 1), (0, height * scale - 1)]
    else:
        points = [(0, 0), (width * scale - 1, 0), (width * scale / 2, height * scale - 1)]
    draw.polygon(points, fill=ARROW_COLOR + (255,))
    return canvas.resize(size, Image.LANCZOS)


def emit_control_arrows(output_dir: Path) -> list[str]:
    written = []
    for direction in ("up", "down"):
        doubled = make_arrow(direction, (ARROW_SIZE[0] * 2, ARROW_SIZE[1] * 2))
        for suffix, icon in ((f"spin_{direction}.png", doubled.resize(ARROW_SIZE, Image.LANCZOS)),
                             (f"spin_{direction}@2x.png", doubled)):
            target = output_dir / suffix
            icon.save(target)
            written.append(f"{target} {icon.width}x{icon.height}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--badges", type=Path,
                        help="path to 14_分区标题_魔法徽章图集.png")
    parser.add_argument("--sparkles", type=Path,
                        help="path to 11_花星书页_小型散件图集.png")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--arrows-only", action="store_true",
                        help="只重新生成控件箭头，不切片素材包")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.arrows_only or not (args.badges and args.sparkles):
        if not (args.badges and args.sparkles) and not args.arrows_only:
            print("未提供素材包路径，跳过切片，仅生成控件箭头。")
        for line in emit_control_arrows(args.output_dir):
            print(line)
        return 0

    sheets = {
        "badges": Image.open(args.badges).convert("RGBA"),
        "sparkles": Image.open(args.sparkles).convert("RGBA"),
    }
    for name, (sheet_key, box, canvas_size) in CROPS.items():
        cell = trim_to_content(sheets[sheet_key].crop(box))
        icon = fit_on_canvas(cell, canvas_size)
        target = args.output_dir / name
        icon.save(target)
        print(f"{target} {icon.width}x{icon.height}")
    for line in emit_control_arrows(args.output_dir):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
