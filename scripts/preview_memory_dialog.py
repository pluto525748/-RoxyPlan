"""Launch an isolated preview of the reorganized RoxyPlan memory dialog.

Isolation guarantees — the preview never touches formal user data or the model:

* **Run directory** — every launch creates its own directory under
  ``test_workspace/preview_runs/``.  The whole memory store lives there, so a
  preview that archives or deletes something can never overwrite a previous
  preview.  Pass ``--workspace`` to reuse a fixed directory on purpose.
* **Synthetic data only** — the store is seeded from the literals in this file
  through the public ``MemoryService`` API.  ``memory.json``, ``data/private/``
  and any formal memory are never opened or copied.
* **No model call** — the memory panel is a local UI over the local store; the
  preview makes no provider request and reads no API key.
* **No developer log** — the preview does not call
  ``configure_development_log``, so the default disabled logger absorbs the
  dialog's UI events and nothing is written under the project ``logs/``.

This script renders the dialog in-process.  Showing it on a real desktop and
clicking through the operations (详情／编辑／归档／恢复／删除／冲突处理) is the
user's acceptance step and has NOT been done by the development side.

Examples::

    .venv/Scripts/python.exe scripts/preview_memory_dialog.py
    .venv/Scripts/python.exe scripts/preview_memory_dialog.py --scenario empty
    .venv/Scripts/python.exe scripts/preview_memory_dialog.py \
        --workspace test_workspace/my_memory_preview

Headless screenshots of the three tabs::

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 \
        .venv/Scripts/python.exe scripts/preview_memory_dialog.py \
        --capture-dir test_workspace/shots --quit-after-capture
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from frontend.window_chrome import install_titlebar_policy

from frontend.memory_dialog import MemoryDialog  # noqa: E402
from modules.memory_service import MemoryService  # noqa: E402


PREVIEW_ROOT = PROJECT_ROOT / "test_workspace" / "preview_runs"
TAB_CAPTURE_NAMES = ("active", "conflict", "archived")
SCENARIOS = ("all", "empty")

# 合成的普通长期记忆：(内容, 类别, 重要度)。类别取自 MemoryManager 支持的真实分类。
SAMPLE_MEMORIES = (
    ("我喜欢在早上先整理当天计划，再开始写代码。", "preference", 4),
    ("长期目标：把 RoxyPlan 做成能长期陪伴用户成长的桌面伙伴。", "goal", 5),
    ("稳定的学习习惯是每天看一小节课程，并整理三条概念笔记。", "habit", 3),
    ("健康提醒：尽量在 23:30 前休息，第二天状态会更好。", "health", 2),
    ("明确规则：写新功能前先确认当前工作树和分支，不要直接动正式数据。", "rule", 4),
)

# 合成的一条超长记忆：包含换行，用来检查长内容在表格里的摘要显示，
# 以及“详情”弹窗和悬停提示是否还能读到完整内容。
SAMPLE_LONG_MEMORY = (
    "关于 RoxyPlan 的项目偏好：在这个项目里推进功能时，我习惯先把“现状是什么、"
    "目标是什么、哪些不能改”写清楚，再动手改代码；\n"
    "如果一个改动需要同时调整界面、业务逻辑和存储结构，我希望先做一小步可以验证的改动，"
    "看到真实结果之后再决定要不要继续扩大范围；\n"
    "遇到解释不清的现象时，我更希望先找到第一处真正偏离预期的地方，"
    "而不是在最终表现上不断叠加补丁；\n"
    "另外，正式记忆文件和本地私有配置属于用户数据，任何测试、预览和调试都不应该读写它们，"
    "需要验证时一律使用一次性隔离目录和合成数据。"
)

# 合成冲突：(旧记忆内容, 新信息, 类别)。旧记忆先写入正式记忆，
# 再通过 create_relation_conflict 生成一条待处理冲突，避免依赖启发式判断。
SAMPLE_CONFLICTS = (
    (
        "我喜欢晚上学习，觉得安静的时候效率更高。",
        "我现在更适合早上学习，晚上容易分心。",
        "preference",
    ),
    (
        "每天学习时间固定安排在 60 分钟。",
        "每天的完整学习时段现在是 90 分钟，中间会休息两次，"
        "其余时间里只做一些零散的复习，不再长时间连续学习，"
        "这样安排之后注意力明显更稳定，也不容易在晚上过度疲劳。",
        "habit",
    ),
)

SAMPLE_ARCHIVED = (
    ("旧的作息习惯：凌晨一点后再睡。", "habit", 2),
    ("旧的项目目标：先做一个能聊天的桌宠原型。", "goal", 3),
)


def default_workspace() -> Path:
    """A fresh run directory per launch, so previews never overwrite each other.

    The directory is created here with ``exist_ok=False``: the timestamp and pid
    only make collisions unlikely, the atomic create makes them impossible.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{os.getpid()}"
    for attempt in range(100):
        candidate = PREVIEW_ROOT / (base if attempt == 0 else f"{base}-{attempt + 1}")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"无法创建独立预览目录：{PREVIEW_ROOT}")


def ensure_readable_fonts() -> None:
    """Load the system UI font when the platform ships no font database.

    Offscreen rendering under ``QT_QPA_PLATFORM=offscreen`` starts with zero
    font families, so every glyph would render as an empty box.  Desktop
    launches already have the full Windows font database and this is a no-op.
    """
    if QFontDatabase.families():
        return
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        candidate = fonts_dir / name
        if candidate.exists():
            QFontDatabase.addApplicationFont(str(candidate))
    print(
        f"[preview] 当前平台无字体数据库，已加载系统字体（{len(QFontDatabase.families())} 个字体族）",
        flush=True,
    )


def save_pixmap(window, path: Path) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target)):
        raise RuntimeError(f"无法保存截图：{target}")
    print(target, flush=True)


def build_service(workspace: Path) -> MemoryService:
    """One isolated memory store: every path resolves inside ``workspace``."""
    return MemoryService.from_data_root(
        workspace,
        memory_file=workspace / "memory.json",
        private_dir=workspace / "private",
        candidates_enabled=False,
    )


def seed_sample_data(service: MemoryService, scenario: str) -> list[str]:
    """Fill the isolated store with synthetic memories, conflicts and archives."""
    if scenario == "empty":
        return ["记忆数据：空列表场景（未写入任何记忆）"]

    manager = service.memory_manager
    notes = [f"记忆数据：合成示例数据（{len(SAMPLE_MEMORIES) + 1} 条普通内容，未读取正式记忆）"]
    for content, category, importance in SAMPLE_MEMORIES:
        manager.add_memory(content, category=category, importance=importance, source="preview")

    result = manager.add_memory(
        SAMPLE_LONG_MEMORY, category="project", importance=5, source="preview"
    )
    long_memory = result.get("memory") or {}
    if not long_memory:
        raise RuntimeError(f"合成超长记忆写入失败：{result.get('status')}")
    notes.append(f"其中 1 条为超长内容（{len(SAMPLE_LONG_MEMORY)} 字，含换行）")

    for old_content, new_content, category in SAMPLE_CONFLICTS:
        old = manager.add_memory(old_content, category=category, importance=3, source="preview")
        old_memory = old.get("memory") or {}
        if not old_memory:
            raise RuntimeError(f"合成冲突旧记忆写入失败：{old.get('status')}")
        manager.create_relation_conflict(
            old_memory, new_content, category, source="preview"
        )
    notes.append(f"冲突：{len(SAMPLE_CONFLICTS)} 条待处理冲突")

    for content, category, importance in SAMPLE_ARCHIVED:
        archived = manager.add_memory(
            content, category=category, importance=importance, source="preview"
        )
        archived_memory = archived.get("memory") or {}
        if not archived_memory:
            raise RuntimeError(f"合成归档记忆写入失败：{archived.get('status')}")
        manager.archive(int(archived_memory["id"]))
    notes.append(f"归档：{len(SAMPLE_ARCHIVED)} 条已归档记忆")
    return notes


def capture_tabs(window, app: QApplication, directory: Path) -> None:
    for index in range(min(window.tabs.count(), len(TAB_CAPTURE_NAMES))):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        name = TAB_CAPTURE_NAMES[index]
        table = (window.memory_table, window.conflict_table, window.archived_table)[index]
        print(f"[preview] {window.tabs.tabText(index)}：{table.rowCount()} 行", flush=True)
        save_pixmap(window, directory / f"memory_{name}.png")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RoxyPlan 记忆管理界面隔离预览（不读取正式记忆、不调用模型）"
    )
    parser.add_argument("--workspace", type=Path,
                        help="预览专用的隔离目录；默认每次运行自动新建")
    parser.add_argument("--scenario", choices=SCENARIOS, default="all",
                        help="all＝普通内容／长内容／冲突／归档（默认）；empty＝空列表")
    parser.add_argument("--width", type=int, help="覆盖预览窗口宽度")
    parser.add_argument("--height", type=int, help="覆盖预览窗口高度")
    parser.add_argument("--capture-dir", type=Path,
                        help="把三个页签分别截图保存到该目录")
    parser.add_argument("--quit-after-capture", action="store_true")
    args = parser.parse_args()

    workspace = (args.workspace or default_workspace()).resolve()
    service = build_service(workspace)
    notes = seed_sample_data(service, args.scenario)

    for note in notes:
        print(f"[preview] {note}", flush=True)
    print(f"[preview] 隔离目录：{workspace}（记忆数据只写在这里，可在预览结束后删除）", flush=True)
    print("[preview] 未读取正式 memory.json 或 data/private；未调用任何模型；未写入项目 logs/", flush=True)

    app = QApplication(sys.argv[:1])
    install_titlebar_policy(app)
    ensure_readable_fonts()
    window = MemoryDialog(memory_service=service)
    if args.width is not None or args.height is not None:
        window.resize(args.width or window.width(), args.height or window.height())
    window.show()

    def finish() -> None:
        if args.capture_dir:
            capture_tabs(window, app, args.capture_dir)
        if args.quit_after_capture:
            app.quit()

    if args.capture_dir:
        QTimer.singleShot(450, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
