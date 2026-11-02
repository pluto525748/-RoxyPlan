"""Launch an isolated preview of the reorganized RoxyPlan settings dialog.

Isolation guarantees — the preview never touches formal user data or the model:

* **Run directory** — every launch creates its own directory under
  ``test_workspace/preview_runs/``, so saving inside one preview cannot
  overwrite the config saved by an earlier preview run.  Pass ``--workspace``
  to reuse a fixed directory on purpose.
* **Config and usage** — ``data/pet_config.json`` and
  ``data/private/model_usage.json`` are read once and copied into that run
  directory; the dialog, the usage store and the knowledge folder all point
  there.  ``--sample`` skips both reads and uses synthetic data instead.
* **Credentials** — ``data/private/llm_secrets.json`` is never copied and
  ``DEEPSEEK_API_KEY`` is removed from the preview process, so the key status
  row shows 未配置 regardless of the shell environment.
* **No model call** — the connection test button is a preview-only stub that
  prints a note instead of contacting DeepSeek, so no request is sent and no
  usage is recorded.

This script only renders the dialog in-process.  Showing it in a real desktop
session and clicking through it is the user's acceptance step and has NOT been
done by the development side.

Examples::

    .venv/Scripts/python.exe scripts/preview_settings_dialog.py
    .venv/Scripts/python.exe scripts/preview_settings_dialog.py --sample
    .venv/Scripts/python.exe scripts/preview_settings_dialog.py \
        --workspace test_workspace/my_preview

Headless screenshots (top of the dialog and scrolled to the bottom)::

    QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 \
        .venv/Scripts/python.exe scripts/preview_settings_dialog.py --sample \
        --capture test_workspace/shots/settings_top.png \
        --capture-scrolled test_workspace/shots/settings_bottom.png \
        --quit-after-capture
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
from PySide6.QtWidgets import QApplication, QScrollArea


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from frontend.window_chrome import install_titlebar_policy

from frontend.settings_dialog import SettingsDialog  # noqa: E402
from modules.llm.secret_store import SecretStore  # noqa: E402
from modules.llm.usage_store import ModelUsageStore  # noqa: E402

SOURCE_CONFIG = PROJECT_ROOT / "data" / "pet_config.json"
SOURCE_USAGE = PROJECT_ROOT / "data" / "private" / "model_usage.json"
PREVIEW_ROOT = PROJECT_ROOT / "test_workspace" / "preview_runs"
ENVIRONMENT_SECRET = "DEEPSEEK_API_KEY"
PREVIEW_CONNECTION_NOTE = "预览模式：不会发起真实模型请求"

# Synthetic values used by --sample; they exercise every visible row without
# depending on the real user configuration.
SAMPLE_SETTINGS = {
    "pet_scale": 1.0,
    "pet_always_on_top": True,
    "auto_tips_enabled": True,
    "auto_tips_min_minutes": 5,
    "auto_tips_max_minutes": 15,
    "proactive_enabled": True,
    "proactive_interval_minutes": 10,
    "evening_review_enabled": True,
    "auto_complete_yesterday_review": True,
    "idle_nudge_enabled": True,
    "chat_history_enabled": True,
    "restore_last_session": True,
    "enable_memory_candidates": False,
    "recent_context_messages": 16,
    "smart_intent_enabled": True,
    "llm_intent_assist_enabled": False,
    "intent_auto_execute_confidence": 0.78,
    "agent_core_enabled": True,
    "agent_multi_step_enabled": True,
    "llm_planner_assist_enabled": False,
    "agent_max_steps": 3,
    "agent_medium_confidence": 0.82,
    "auto_summary_enabled": True,
    "active_persona_id": "roxy",
    "study_reminder_minutes": 25,
    "sleep_minutes": 20,
    "pet_asset_path": "assets/pet/source/pet_default.png",
    "online_model_enabled": True,
    "model_mode": "auto",
    "enable_auto_escalation": True,
    "enable_thinking_for_complex_tasks": True,
    "offline_fallback_enabled": True,
    "offline_model": "qwen3:4b",
    "model_name": "qwen3:4b",
    "request_timeout_seconds": 120,
}

# Synthetic usage totals shown by --sample; shape matches ModelUsageStore.
SAMPLE_USAGE = {
    "updated_at": "2026-10-12T00:00:00",
    "totals": {
        "deepseek:deepseek-chat": {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "requests": 3,
            "successes": 3,
            "failures": 0,
            "input_tokens": 1200,
            "output_tokens": 800,
            "total_tokens": 2000,
            "tool_calls": 1,
            "latency_ms": 900,
        },
    },
    "recent": [],
}


class PreviewSettingsDialog(SettingsDialog):
    """Preview variant: the connection test never contacts the model."""

    def _test_connection(self):
        self.connection_status.setText(PREVIEW_CONNECTION_NOTE)


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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_workspace(workspace: Path, *, sample: bool) -> tuple[Path, list[str]]:
    """Build the disposable preview workspace and describe where data came from."""
    notes: list[str] = []
    if sample or not SOURCE_CONFIG.exists():
        settings = dict(SAMPLE_SETTINGS)
        notes.append("配置来源：合成示例数据（未读取正式配置）")
    else:
        settings = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
        notes.append(f"配置来源：{SOURCE_CONFIG}（只读复制）")

    config_path = workspace / "pet_config.json"
    _write_json(config_path, settings)

    usage_path = workspace / "private" / "model_usage.json"
    if sample or not SOURCE_USAGE.exists():
        _write_json(usage_path, dict(SAMPLE_USAGE))
        notes.append("使用统计来源：合成示例数据（未读取正式用量）")
    else:
        _write_json(usage_path, json.loads(SOURCE_USAGE.read_text(encoding="utf-8")))
        notes.append(f"使用统计来源：{SOURCE_USAGE}（只读复制）")

    (workspace / "knowledge").mkdir(parents=True, exist_ok=True)
    return config_path, notes


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
    print(f"[preview] 当前平台无字体数据库，已加载系统字体（{len(QFontDatabase.families())} 个字体族）",
          flush=True)


def save_pixmap(window, path: Path) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target)):
        raise RuntimeError(f"无法保存截图：{target}")
    print(target, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RoxyPlan 设置界面隔离预览（不读写正式用户数据、不调用模型）"
    )
    parser.add_argument("--workspace", type=Path,
                        help="预览专用的隔离目录；默认每次运行自动新建")
    parser.add_argument("--sample", action="store_true",
                        help="使用纯合成数据，完全不读取 data/pet_config.json 与正式模型用量")
    parser.add_argument("--width", type=int, help="覆盖预览窗口宽度")
    parser.add_argument("--height", type=int, help="覆盖预览窗口高度")
    parser.add_argument("--capture", type=Path, help="保存未滚动时的窗口截图")
    parser.add_argument("--capture-scrolled", type=Path,
                        help="纵向滚动到底部后保存窗口截图")
    parser.add_argument("--quit-after-capture", action="store_true")
    args = parser.parse_args()

    workspace = (args.workspace or default_workspace()).resolve()
    config_path, notes = prepare_workspace(workspace, sample=args.sample)
    removed = suppress_environment_secret()
    for note in notes:
        print(f"[preview] {note}", flush=True)
    print(f"[preview] 隔离目录：{workspace}", flush=True)
    print("[preview] 环境变量 DEEPSEEK_API_KEY："
          + ("已从预览进程移除" if removed else "未设置"), flush=True)
    print("[preview] 预览中的保存只写隔离目录；连接测试按钮不会发起真实请求；"
          "隔离目录可在预览结束后删除", flush=True)

    app = QApplication(sys.argv[:1])
    install_titlebar_policy(app)
    ensure_readable_fonts()
    window = PreviewSettingsDialog(
        config_file=config_path,
        secret_store=SecretStore(workspace, path=workspace / "private" / "llm_secrets.json"),
        usage_store=ModelUsageStore(workspace, path=workspace / "private" / "model_usage.json"),
        knowledge_dir=workspace / "knowledge",
    )
    if args.width is not None or args.height is not None:
        window.resize(args.width or window.width(), args.height or window.height())
    window.show()

    def capture_scrolled() -> None:
        area = window.findChild(QScrollArea, "settingsScroll")
        if area is not None:
            bar = area.verticalScrollBar()
            bar.setValue(bar.maximum())
            print(f"[preview] 滚动条范围 0-{bar.maximum()}", flush=True)
        app.processEvents()
        save_pixmap(window, args.capture_scrolled)

    def finish() -> None:
        if args.capture:
            save_pixmap(window, args.capture)
        if args.capture_scrolled:
            capture_scrolled()
        if args.quit_after_capture:
            app.quit()

    if args.capture or args.capture_scrolled:
        QTimer.singleShot(450, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
