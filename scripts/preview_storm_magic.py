"""Isolated visible sample; run only with desktop permission or by the user.

From the repository root:
  .venv/Scripts/python.exe -B scripts/preview_storm_magic.py --pet-scale 1.0

The fixed window is 168x190 logical pixels at scale 1, just like DesktopPet.
The scale changes the entire sample, never enlarges it during playback.
Windows 150% scaling is automatic; the complete square frame is letterboxed.
The sample shows normal posture, plays twelve frames once, then restores itself.
Left click replays once; right click stops, then closes when idle; middle closes.
No production app, configs, secrets, model, extraction, screenshots or caches.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="风暴魔术独立播放样板")
    parser.add_argument("--pet-scale", "--scale", dest="pet_scale", type=float,
                        default=1.0, help="桌宠整体比例0.65..1.8；默认168×190，全程不放大")
    parser.add_argument("--delay-ms", type=int, default=1200,
                        help="正常姿态展示时间，0..10000ms")
    options = parser.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from frontend.storm_magic_preview import StormMagicPreview

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("RoxyPlan Storm Preview")
    # Tool windows do not define last-window quit semantics on every platform.
    app.setQuitOnLastWindowClosed(False)
    try:
        sample = StormMagicPreview(
            PROJECT_ROOT / "assets/pet/source/RoxyPlan_风暴魔术_完整播放素材包.zip",
            PROJECT_ROOT / "assets/pet/roxy_pet_transparent.png",
            pet_scale=options.pet_scale,
        )
        sample.playback_error.connect(lambda message: print(message, file=sys.stderr))
        sample.playback_finished.connect(
            lambda reason: app.quit() if reason == "closed" else None)
        sample.schedule_once(options.delay_ms)
        print("独立风暴样板：左键播放一次；右键中止，待机时右键关闭；中键关闭。")
        print("窗口全程同尺寸，12帧目标时长5940ms；视觉连贯性待验收。")
        sample.show()
        return app.exec()
    except Exception as error:
        print(f"风暴样板无法启动：{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
