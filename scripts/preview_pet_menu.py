"""Launch an isolated offline preview of the desktop pet context menu and bubble.

Isolation guarantees — the preview never touches formal user data or the model:

* **Run directory** — every launch creates its own directory under
  ``test_workspace/preview_runs/``.  The pet config, the tips list, the growth
  store, the chat history, the secret store, the usage store and the knowledge
  folder all point inside that directory, so ``data/pet_config.json``,
  ``data/pet_tips.json`` and ``data/private/`` are neither read nor written.
  Pass ``--workspace`` to reuse a fixed directory on purpose.
* **Quiet synthetic config** — the preview config turns off the proactive
  check, the auto tips, the evening review, the idle nudge, the startup review
  backfill and test mode, and pushes the study reminder and the sleep timer far
  out, so no reminder fires and no growth write happens while the window is
  open.
* **No chat window** — the pet is created without a chat factory.  The entries
  that would open the chat or memory windows only show their
  "入口还没有准备好" bubble instead of building a window that reads real user
  data; those windows have their own previews.
* **No model call** — ``DEEPSEEK_API_KEY`` is removed from the preview process
  and the pet holds no chat window, so nothing here contacts a provider.
* **No developer log** — ``configure_development_log`` is not called, so the
  default disabled logger absorbs the pet's events and nothing is written under
  the project ``logs/``.

``--capture-dir`` renders the pet, its bubble and the menu into PNG files
without ever showing a window: the three widgets are grabbed while hidden, so a
capture run occupies no desktop and needs no interaction.  Without
``--capture-dir`` the preview shows the real pet on the desktop, which does
occupy it — right-click the pet to inspect the menu, and close the window when
you are done.  Judging the real menu and bubble on screen is the user's
acceptance step.

Examples::

    .venv/Scripts/python.exe scripts/preview_pet_menu.py
    .venv/Scripts/python.exe scripts/preview_pet_menu.py --bubble "先做一小步就好。"
    .venv/Scripts/python.exe scripts/preview_pet_menu.py --menu
    .venv/Scripts/python.exe scripts/preview_pet_menu.py \
        --capture-dir test_workspace/preview_shots --quit-after-capture
"""

from __future__ import annotations

import argparse
import json
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

from frontend.desktop_pet import DesktopPet  # noqa: E402
from modules.chat_history_manager import ChatHistoryManager  # noqa: E402
from modules.growth_manager import GrowthManager  # noqa: E402
from modules.llm.secret_store import SecretStore  # noqa: E402
from modules.llm.usage_store import ModelUsageStore  # noqa: E402


PREVIEW_ROOT = PROJECT_ROOT / "test_workspace" / "preview_runs"
ENVIRONMENT_SECRET = "DEEPSEEK_API_KEY"

# 合成的桌宠配置：外观保持默认，所有会自己跑起来的提醒、回顾与测试计时全部关闭，
# 学习提醒与睡眠计时也推到很久以后，保证预览窗口打开期间桌宠安安静静。
PREVIEW_CONFIG = {
    "pet_enabled": True,
    "pet_scale": 1.0,
    "pet_always_on_top": True,
    "pet_position": "bottom_right",
    "auto_tips_enabled": False,
    "proactive_enabled": False,
    "evening_review_enabled": False,
    "idle_nudge_enabled": False,
    "auto_complete_yesterday_review": False,
    "chat_history_enabled": False,
    "test_mode": False,
    "study_reminder_minutes": 240,
    "sleep_minutes": 240,
    "use_pet_asset": True,
    "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
}

# 合成提示语：一条短、一条中等、一条很长，用来看气泡的换行与留白。
PREVIEW_TIPS = [
    "先完成一个小任务，好吗？",
    "不要一直弯腰，活动一下肩膀。",
    "我会陪你慢慢完成这个项目，今天先做最小的一步，剩下的等状态好一点再说。",
]

# 截图用的气泡文本：取合成提示语里最长的一条，最能看出边框与留白。
CAPTURE_BUBBLE_TEXT = PREVIEW_TIPS[-1]


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


def suppress_environment_secret() -> bool:
    """Drop DEEPSEEK_API_KEY from this process. True when a value was removed."""
    return os.environ.pop(ENVIRONMENT_SECRET, None) is not None


def prepare_workspace(workspace: Path) -> tuple[Path, Path]:
    """Write the synthetic config and tips into the isolated directory."""
    config_path = workspace / "pet_config.json"
    config_path.write_text(
        json.dumps(PREVIEW_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tips_path = workspace / "pet_tips.json"
    tips_path.write_text(
        json.dumps(PREVIEW_TIPS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (workspace / "private").mkdir(parents=True, exist_ok=True)
    (workspace / "knowledge").mkdir(parents=True, exist_ok=True)
    return config_path, tips_path


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


def build_pet(workspace: Path, config_path: Path, tips_path: Path) -> DesktopPet:
    """The pet with every store and path pointed inside the preview directory."""
    private_dir = workspace / "private"
    return DesktopPet(
        config_file=config_path,
        tips_file=tips_path,
        # No chat factory on purpose: the entries that open the chat or memory
        # windows stay on their "入口还没有准备好" bubble.
        chat_factory=None,
        growth_service=GrowthManager(private_dir=private_dir),
        chat_history_manager=ChatHistoryManager(private_dir=private_dir, enabled=False),
        secret_store=SecretStore(private_dir, path=private_dir / "llm_secrets.json"),
        usage_store=ModelUsageStore(private_dir, path=private_dir / "model_usage.json"),
        knowledge_dir=workspace / "knowledge",
    )


def save_pixmap(widget, path: Path) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if not pixmap.save(str(target)):
        raise RuntimeError(f"无法保存截图：{target}")
    # 物理像素随系统缩放变化，逻辑尺寸才是布局参数的直接结果；两者都记下，
    # 比较菜单紧凑度时才不会把缩放差异误读成样式变化。
    print(
        f"{target} {pixmap.width()}x{pixmap.height()}"
        f"（逻辑 {widget.width()}x{widget.height()}，dpr={pixmap.devicePixelRatio()}）",
        flush=True,
    )


def capture_widgets(pet: DesktopPet, directory: Path, menu_index: int = 1) -> None:
    """Grab the pet, the bubble and the menu while no window is shown."""
    pet.ensurePolished()
    save_pixmap(pet, directory / "pet_window.png")

    bubble = pet.bubble
    bubble.label.setText(CAPTURE_BUBBLE_TEXT)
    # 与 show_message 走同一条排版路径，否则截的不是真实气泡尺寸。
    bubble.ensurePolished()
    bubble._fit_to_text()
    save_pixmap(bubble, directory / "pet_bubble.png")

    menu, _actions = pet._create_context_menu()
    menu.ensurePolished()
    menu.resize(menu.sizeHint())
    # 让第 menu_index 个非分隔项处于选中态，截图才能看到高亮样式。
    candidates = [action for action in menu.actions() if not action.isSeparator()]
    if 0 <= menu_index < len(candidates):
        menu.setActiveAction(candidates[menu_index])
    menu.update()
    save_pixmap(menu, directory / "pet_menu.png")
    menu.close()


def show_menu(pet: DesktopPet) -> None:
    menu, _actions = pet._create_context_menu()
    menu.popup(pet.frameGeometry().topLeft())
    pet._preview_menu = menu  # 让菜单在预览期间保持存活


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RoxyPlan 桌宠右键菜单与气泡隔离预览（不读写正式数据、不调用模型）"
    )
    parser.add_argument("--workspace", type=Path,
                        help="预览专用的隔离目录；默认每次运行自动新建")
    parser.add_argument("--bubble", metavar="TEXT",
                        help="用这条文本显示一次气泡（默认不显示）")
    parser.add_argument("--menu", action="store_true",
                        help="启动后弹出右键菜单，方便直接查看分组与选中样式")
    parser.add_argument("--capture-dir", type=Path,
                        help="把桌宠、气泡与菜单渲染成 PNG 保存到该目录；该模式不显示任何窗口")
    parser.add_argument("--menu-highlight", type=int, default=1,
                        help="截图时高亮第几项菜单（默认第 2 项，即“今日成长面板”）")
    parser.add_argument("--quit-after-capture", action="store_true")
    args = parser.parse_args()

    workspace = (args.workspace or default_workspace()).resolve()
    config_path, tips_path = prepare_workspace(workspace)
    removed = suppress_environment_secret()

    print("[preview] 配置与提示语：合成数据（未读取 data/pet_config.json 与 data/pet_tips.json）", flush=True)
    print(f"[preview] 隔离目录：{workspace}（配置、成长与聊天记录只写在这里，可在预览结束后删除）", flush=True)
    print("[preview] 未读取正式 memory.json、data/private 或成长数据；未调用任何模型；未写入项目 logs/", flush=True)
    print("[preview] 环境变量 DEEPSEEK_API_KEY："
          + ("已从预览进程移除" if removed else "未设置"), flush=True)

    app = QApplication(sys.argv[:1])
    install_titlebar_policy(app)
    ensure_readable_fonts()
    pet = build_pet(workspace, config_path, tips_path)

    if args.capture_dir:
        def finish() -> None:
            capture_widgets(pet, args.capture_dir, args.menu_highlight)
            if args.quit_after_capture:
                app.quit()

        QTimer.singleShot(400, finish)
    else:
        pet.show()
        print("[preview] 桌宠窗口已显示：右键可以查看菜单；关闭窗口或按 Ctrl+C 结束预览", flush=True)
        if args.bubble:
            pet.show_bubble(args.bubble, duration_ms=12000)
        if args.menu:
            QTimer.singleShot(500, lambda: show_menu(pet))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
