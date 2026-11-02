"""Run one real detailed growth dialog with disposable, offline data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from frontend.window_chrome import install_titlebar_policy

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from frontend.growth_dialog import GrowthDialog
from modules.growth_manager import GrowthManager


def build_preview(root: Path, scenario: str) -> GrowthDialog:
    now = datetime.now().replace(hour=18, minute=0, second=0, microsecond=0)
    manager = GrowthManager(root / "data" / "private", now_provider=lambda: now)
    if scenario != "empty":
        titles = ["梳理机器学习算法框架", "精读一个核心算法", "动手完成算法练习"]
        if scenario == "long":
            titles = [
                f"长标题检查 {index + 1}：" + "阅读与整理机器学习原理、推导和练习笔记，" * 8
                + '字符原样显示 <标签> & “引号” / 🌙'
                for index in range(16)
            ]
        first = None
        for title in titles:
            task = manager.add_task(title, duration_minutes=30)
            first = first or task
        manager.complete_by_id(first["id"])
        manager.add_record("记录了线性回归核心原理复习", duration_minutes=30)
        manager.add_record("整理了学习笔记" + ("\n补充推导、例题与尚未理解的问题。" * 12
                                              if scenario == "long" else ""))
        manager.save_today_review()
        for day in (now.date() - timedelta(days=1), now.date().replace(day=1) - timedelta(days=1)):
            manager.add_task("隔离预览：回顾上一阶段学习", date=day.isoformat())
            manager.save_today_review(day.isoformat())
    dialog = GrowthDialog(manager)
    dialog.setWindowTitle(dialog.windowTitle() + f" · 隔离预览 ({scenario})")
    return dialog


def business_files(root: Path) -> dict:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.json"))}


def capture_preview(dialog: GrowthDialog, root: Path, scenario: str) -> None:
    """Capture only this window; tabs/resize/read refresh must not persist data."""
    app = QApplication.instance()
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    before = business_files(root / "data")
    screen = dialog.screen()
    report = {
        "scenario": scenario, "offline": True, "platform": app.platformName(),
        "device_pixel_ratio": screen.devicePixelRatio(),
        "screen_logical_size": [screen.size().width(), screen.size().height()],
        "screen_physical_size": [round(screen.size().width() * screen.devicePixelRatio()),
                                 round(screen.size().height() * screen.devicePixelRatio())],
        "logical_dpi": screen.logicalDotsPerInch(),
        "qt_scale_factor_override": os.environ.get("QT_SCALE_FACTOR"),
        "default_client_size": [dialog.width(), dialog.height()], "screenshots": [],
    }
    for index, name in enumerate(("plans-actions", "today-review", "growth-logs")):
        dialog.tabs.setCurrentIndex(index)
        dialog.refresh_all()
        app.processEvents()
        file = f"{scenario}-{name}.png"
        if not dialog.grab().save(str(output / file)):
            raise OSError(f"Could not save {file}")
        report["screenshots"].append(file)
    dialog.resize(500, 520)
    dialog.tabs.setCurrentIndex(0)
    app.processEvents()
    file = f"{scenario}-minimum-size.png"
    if not dialog.grab().save(str(output / file)):
        raise OSError(f"Could not save {file}")
    report["screenshots"].append(file)
    report["read_refresh_preserved_business_files"] = before == business_files(root / "data")
    assert report["read_refresh_preserved_business_files"]
    (output / "environment-and-checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("populated", "empty", "long"), default="populated")
    parser.add_argument("--tab", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--capture", action="store_true", help="Capture all tabs and close this window")
    args = parser.parse_args()
    root = PROJECT_ROOT / "test_workspace" / "runs" / (
        "growth-preview-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    temp = root / "temp"
    temp.mkdir(parents=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)
    app = QApplication.instance() or QApplication([])
    install_titlebar_policy(app)
    app.setQuitOnLastWindowClosed(True)
    dialog = build_preview(root, args.scenario)
    dialog.tabs.setCurrentIndex(args.tab - 1)
    print(f"Isolated offline preview: {root}", flush=True)
    dialog.show()
    errors = []
    if args.capture:
        def capture_and_close():
            try:
                capture_preview(dialog, root, args.scenario)
            except Exception as error:
                errors.append(error)
                print(f"Capture failed: {error}", file=sys.stderr, flush=True)
            finally:
                dialog.close()
        QTimer.singleShot(600, capture_and_close)
    app.exec()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
