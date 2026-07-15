from __future__ import annotations

import argparse
import colorsys
from collections import deque
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image
except ImportError as error:
    raise SystemExit(
        "Pillow is required. Install dependencies with: pip install -r requirements.txt"
    ) from error


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "assets" / "pet" / "source" / "roxy_dance_sheet.png"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "pet" / "dance"

GRID_COLUMNS = 4
GRID_ROWS = 2
FRAME_COUNT = GRID_COLUMNS * GRID_ROWS
FRAME_DURATIONS_MS = [120, 110, 100, 110, 90, 140, 90, 140]
JUMP_FRAME_INDEX = 5
JUMP_RAISE_PIXELS = 42
CANVAS_MARGIN_X = 10
CANVAS_MARGIN_TOP = 8
CANVAS_MARGIN_BOTTOM = 8


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def smoothstep(value: float) -> float:
    value = clamp(value)
    return value * value * (3.0 - 2.0 * value)


def remove_green_screen(image: Image.Image) -> Image.Image:
    """Create a soft alpha matte and suppress green spill on antialiased edges."""
    source = image.convert("RGBA")
    output = Image.new("RGBA", source.size)
    output_pixels = []

    for red, green, blue, source_alpha in source.getdata():
        maximum_rb = max(red, blue)
        average_rb = (red + blue) / 2.0
        green_excess = green - average_rb
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)

        is_green_hue = 0.20 <= hue <= 0.47
        is_green_dominant = green > maximum_rb + 5 and green > 45
        if is_green_hue and is_green_dominant and saturation > 0.08:
            hue_weight = 1.0 - clamp(abs(hue - (1.0 / 3.0)) / 0.14)
            excess_weight = smoothstep((green_excess - 5.0) / 62.0)
            saturation_weight = smoothstep((saturation - 0.06) / 0.34)
            brightness_weight = smoothstep((value - 0.10) / 0.45)
            key_strength = clamp(
                max(excess_weight * hue_weight, excess_weight * saturation_weight * brightness_weight)
            )
        else:
            key_strength = 0.0

        alpha = int(round(source_alpha * (1.0 - key_strength)))
        if alpha < 8:
            alpha = 0
        elif alpha > 247:
            alpha = 255

        # Despill partially transparent edge pixels so JPEG green does not leave a neon outline.
        spill_strength = clamp((green_excess - 1.0) / 45.0) if is_green_hue else 0.0
        spill_strength = max(spill_strength, key_strength)
        neutral_green = int(round((red + blue) / 2.0))
        cleaned_green = int(round(green * (1.0 - spill_strength) + neutral_green * spill_strength))
        if alpha == 0:
            red = green = blue = 0

        output_pixels.append((red, cleaned_green, blue, alpha))

    output.putdata(output_pixels)
    return output


def remove_disconnected_artifacts(image: Image.Image) -> Image.Image:
    """Keep the main connected character and discard fragments crossing cell borders."""
    width, height = image.size
    alpha = list(image.getchannel("A").getdata())
    visited = bytearray(width * height)
    components: list[list[int]] = []

    for start in range(width * height):
        if visited[start] or alpha[start] < 16:
            continue
        visited[start] = 1
        queue = deque([start])
        component = []
        while queue:
            pixel = queue.popleft()
            component.append(pixel)
            x = pixel % width
            y = pixel // width
            if x > 0:
                neighbor = pixel - 1
                if not visited[neighbor] and alpha[neighbor] >= 16:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if x + 1 < width:
                neighbor = pixel + 1
                if not visited[neighbor] and alpha[neighbor] >= 16:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y > 0:
                neighbor = pixel - width
                if not visited[neighbor] and alpha[neighbor] >= 16:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y + 1 < height:
                neighbor = pixel + width
                if not visited[neighbor] and alpha[neighbor] >= 16:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        components.append(component)

    if not components:
        return image

    keep = set(max(components, key=len))
    pixels = list(image.getdata())
    for index, (red, green, blue, pixel_alpha) in enumerate(pixels):
        if pixel_alpha >= 16 and index not in keep:
            pixels[index] = (0, 0, 0, 0)
    cleaned = Image.new("RGBA", image.size)
    cleaned.putdata(pixels)
    return cleaned


def split_sheet(sheet: Image.Image) -> list[Image.Image]:
    frames = []
    for row in range(GRID_ROWS):
        top = round(row * sheet.height / GRID_ROWS)
        bottom = round((row + 1) * sheet.height / GRID_ROWS)
        for column in range(GRID_COLUMNS):
            left = round(column * sheet.width / GRID_COLUMNS)
            right = round((column + 1) * sheet.width / GRID_COLUMNS)
            crop = sheet.crop((left, top, right, bottom))
            keyed = remove_green_screen(crop)
            frames.append(remove_disconnected_artifacts(keyed))
    return frames


def visible_bbox(frame: Image.Image) -> tuple[int, int, int, int]:
    alpha = frame.getchannel("A")
    visible = alpha.point(lambda value: 255 if value >= 16 else 0)
    bbox = visible.getbbox()
    if bbox is None:
        raise ValueError("A frame became fully transparent after green-screen removal")
    return bbox


def align_frames(frames: Iterable[Image.Image]) -> list[Image.Image]:
    frame_list = list(frames)
    subjects = [frame.crop(visible_bbox(frame)) for frame in frame_list]

    normal_heights = [subject.height for index, subject in enumerate(subjects) if index != JUMP_FRAME_INDEX]
    jump_height = subjects[JUMP_FRAME_INDEX].height
    canvas_width = max(
        max(frame.width for frame in frame_list),
        max(subject.width for subject in subjects) + CANVAS_MARGIN_X * 2,
    )
    canvas_height = max(
        max(frame.height for frame in frame_list),
        max(normal_heights) + CANVAS_MARGIN_TOP + CANVAS_MARGIN_BOTTOM,
        jump_height + JUMP_RAISE_PIXELS + CANVAS_MARGIN_TOP + CANVAS_MARGIN_BOTTOM,
    )

    aligned = []
    normal_baseline = canvas_height - CANVAS_MARGIN_BOTTOM
    for index, subject in enumerate(subjects):
        baseline = normal_baseline - (JUMP_RAISE_PIXELS if index == JUMP_FRAME_INDEX else 0)
        x = (canvas_width - subject.width) // 2
        y = baseline - subject.height
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        canvas.alpha_composite(subject, (x, y))
        aligned.append(canvas)
    return aligned


def to_gif_frame(frame: Image.Image) -> Image.Image:
    # Reserve palette index 255 for transparency.
    palette_frame = frame.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    transparent_pixels = frame.getchannel("A").point(lambda value: 255 if value < 128 else 0)
    palette_frame.paste(255, mask=transparent_pixels)
    palette_frame.info["transparency"] = 255
    return palette_frame


def save_outputs(frames: list[Image.Image], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        target = output_dir / f"dance_{index:03d}.png"
        frame.save(target, format="PNG", optimize=True)
        print(f"[DANCE_EXTRACT] wrote {target.relative_to(PROJECT_ROOT)}")

    gif_frames = [to_gif_frame(frame) for frame in frames]
    preview_path = output_dir / "preview.gif"
    gif_frames[0].save(
        preview_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=FRAME_DURATIONS_MS,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )
    print(f"[DANCE_EXTRACT] wrote {preview_path.relative_to(PROJECT_ROOT)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Roxy dance frames from a 4x2 green-screen sheet.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    output_dir = args.output.resolve()
    if not source.exists():
        raise SystemExit(f"Dance sheet not found: {source}")

    with Image.open(source) as sheet:
        frames = split_sheet(sheet.convert("RGB"))
    if len(frames) != FRAME_COUNT:
        raise RuntimeError(f"Expected {FRAME_COUNT} frames, got {len(frames)}")

    aligned_frames = align_frames(frames)
    save_outputs(aligned_frames, output_dir)
    width, height = aligned_frames[0].size
    print(f"[DANCE_EXTRACT] complete: {len(aligned_frames)} frames, canvas {width}x{height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
