"""Launch the Today Growth visual sample without loading application data."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from frontend.window_chrome import install_titlebar_policy

from frontend.today_growth_dialog import TodayGrowthDialog  # noqa: E402


def example_snapshot(scenario: str) -> dict:
    base = {
        "date_label": "2026年10月7日 · 示例",
        "tasks": [
            {"title": "机器学习课程复习", "description": "复习上周内容，完成课后习题", "duration_minutes": 60, "done": True},
            {"title": "RoxyPlan 功能开发", "description": "完成今日开发任务，调试核心功能", "duration_minutes": 45, "done": True},
            {"title": "Agent 学习笔记整理", "description": "整理学习笔记，补充关键知识点", "duration_minutes": 30, "done": False},
            {"title": "英语阅读 / 放松任务", "description": "阅读一篇英文文章，或轻松放松", "duration_minutes": 20, "done": False},
        ],
        "actions": [
            {"time": "09:10", "content": "复习了机器学习课程内容", "duration_minutes": 45},
            {"time": "14:20", "content": "整理了 Agent 学习笔记", "duration_minutes": 30},
            {"time": "19:30", "content": "推进了 RoxyPlan 开发", "duration_minutes": 20},
        ],
        "recent_completed": 13,
        "recent_total": 19,
        "summary": "今天的计划已经开始推进。行动记录帮助回看具体做过什么。接下来可以从尚未完成的事项中选择一项继续。",
    }
    if scenario == "long":
        base["tasks"] = [
            {"title": "把今日成长面板在不同窗口宽度下的卡片、文字换行和滚动区域逐项检查清楚，并记录需要继续优化的细节", "description": "这是一条用于验证长标题与多行描述的合成计划。", "duration_minutes": None, "done": False},
            *base["tasks"],
            {"title": "整理本周阅读摘要", "description": "写出三条要点", "duration_minutes": None, "done": False},
            {"title": "回顾项目笔记", "description": "检查遗漏的事项", "duration_minutes": 25, "done": False},
            {"title": "轻量运动", "description": "伸展与短时步行", "duration_minutes": None, "done": False},
        ]
    elif scenario == "empty":
        base["tasks"] = []
        base["actions"] = []
        base["recent_completed"] = None
        base["recent_total"] = None
        base["summary"] = "今天尚无计划或行动记录。正式接入后，这里会显示已有的今日复盘。"
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="RoxyPlan 今日成长面板视觉预览（纯合成数据）")
    parser.add_argument("--scenario", choices=("default", "long", "empty"), default="default")
    parser.add_argument("--scale", type=float, default=660 / 900, help="按比例缩放窗口及其字体和间距")
    parser.add_argument("--width", type=int, help="可选：覆盖缩放后的客户区宽度")
    parser.add_argument("--height", type=int, help="可选：覆盖缩放后的客户区高度")
    parser.add_argument("--capture", type=Path, help="保存实际 Qt 窗口渲染截图")
    parser.add_argument("--desktop-capture", type=Path, help="保存所在屏幕的真实桌面截图")
    parser.add_argument("--corner", action="store_true", help="仅在预览时将窗口放在屏幕右下角")
    parser.add_argument("--check-first", action="store_true", help="预览中勾选第一条未完成计划")
    parser.add_argument("--quit-after-capture", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv[:1])
    install_titlebar_policy(app)
    window = TodayGrowthDialog(example_snapshot(args.scenario), scale=args.scale)
    if args.width is not None or args.height is not None:
        window.resize(args.width or window.width(), args.height or window.height())
    window.show()
    if args.corner:
        screen = window.screen() or app.primaryScreen()
        available = screen.availableGeometry()
        frame = window.frameGeometry()
        window.move(available.right() - frame.width() - 20,
                    available.bottom() - frame.height() - 20)

    def capture_desktop() -> None:
        path = args.desktop_capture.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        screen = window.screen() or app.primaryScreen()
        if not screen.grabWindow(0).save(str(path)):
            raise RuntimeError(f"Unable to save desktop screenshot: {path}")
        print(path, flush=True)
        if args.quit_after_capture:
            app.quit()
        else:
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            window.show()
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def finish_preview() -> None:
        if args.check_first:
            first = next((task for task in window.tasks if not task.get("done")), None)
            if first is not None:
                first["_card"].check_box.setChecked(True)
                app.processEvents()
        if args.capture:
            path = args.capture.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(path)):
                raise RuntimeError(f"Unable to save screenshot: {path}")
            print(path, flush=True)
        if args.desktop_capture:
            # Only the explicit screenshot mode brings the preview to the front.
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            window.show()
            window.raise_()
            window.activateWindow()
            QTimer.singleShot(350, capture_desktop)
        elif args.quit_after_capture:
            app.quit()

    if args.capture or args.desktop_capture or args.check_first:
        QTimer.singleShot(450, finish_preview)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
