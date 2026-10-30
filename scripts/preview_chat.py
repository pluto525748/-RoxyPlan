"""Preview the real ChatWindow with isolated stores and an offline reply client."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from frontend.window_chrome import install_titlebar_policy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from frontend.pet_app import ChatWindow
from modules.chat_history_manager import ChatHistoryManager
from modules.development_log import DevelopmentLog
from modules.growth_manager import GrowthManager
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from modules.memory_service import MemoryService


NORMAL_REPLY = "可以呀。我们慢慢聊，把今天的一点进步留在日记里。\n你想先从哪件小事说起？"
LONG_REPLY = (
    "把长回复分成小段，读起来会轻松一些。\n\n"
    "1. 先读一小节，写下最想继续了解的问题。\n"
    "2. 用自己的话整理两三句，再回看原文补上遗漏。\n"
    "3. 遇到难点先做标记，休息后再回来。\n\n"
    "字符示例：<标签> & 引号“你好” / emoji 🌙；这些应原样显示。\n"
    "    缩进的这一行也应保留。\n"
    "连续字符：ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 5
    + "\n\n最后，不必一次全部做完。可以先选最轻的一步，完成后再聊下一步。"
)


class OfflinePreviewClient:
    def __init__(self, root: Path):
        self.secret_store = SecretStore(root)
        self.usage_store = ModelUsageStore(root)
        self.calls = 0

    def chat(self, messages, **_kwargs):
        self.calls += 1
        time.sleep(0.15)
        text = next((str(item.get("content", "")) for item in reversed(messages)
                     if item.get("role") == "user"), "")
        return LONG_REPLY if "长回复" in text else NORMAL_REPLY


def build_preview_window(root: Path) -> tuple[ChatWindow, str]:
    root = root.resolve()
    private = root / "data" / "private"
    day = datetime.now().date()
    growth = GrowthManager(private)
    history = ChatHistoryManager(private)
    old = history.ensure_daily_learning_session((day - timedelta(days=1)).isoformat())
    if not old.get("messages"):
        history.add_message(old["session_id"], "user", "昨天读了一小节，也写了几句笔记。")
        history.add_message(old["session_id"], "assistant", "一点点积累，也值得好好记下。\n今天可以从昨天的笔记接着读。")
    memory = MemoryService.from_data_root(
        root, memory_file=root / "memory.json", private_dir=private,
        default_data={"version": 1, "profile": {}, "memories": []},
    )
    window = ChatWindow(
        growth_service=growth, chat_history_manager=history, memory_service=memory,
        development_log=DevelopmentLog(root / "logs" / "development", enabled=True),
        llm_client=OfflinePreviewClient(root),
        pet_settings={"chat_history_enabled": True, "agent_core_enabled": True},
        settings_config_file=root / "data" / "pet_config.json",
        personality_file=root / "data" / "roxy_personality.json",
        knowledge_dir=root / "data" / "knowledge", legacy_config_file=root / "config.json",
    )
    return window, old["session_id"]


def exercise_preview(window: ChatWindow, old_id: str, output: Path) -> dict:
    """Send actual Qt input events and retain screenshots of one window."""
    from PySide6.QtTest import QTest

    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    screen = window.screen()
    report = {
        "platform": app.platformName(), "screen_name": screen.name(),
        "screen_logical_size": [screen.size().width(), screen.size().height()],
        "device_pixel_ratio": screen.devicePixelRatio(),
        "screen_physical_size": [round(screen.size().width() * screen.devicePixelRatio()),
                                 round(screen.size().height() * screen.devicePixelRatio())],
        "logical_dpi": screen.logicalDotsPerInch(),
        "qt_scale_factor_override": os.environ.get("QT_SCALE_FACTOR"),
        "offline": True, "screenshots": [],
    }

    def capture(name):
        app.processEvents()
        QTest.qWait(100)
        assert window.grab().save(str(output / name))
        report["screenshots"].append(name)

    def send(text, enter=False):
        window.input_box.setFocus()
        # Unicode is set on the real editor; submission uses its actual key/button event.
        window.input_box.setText(text)
        if enter:
            QTest.keyClick(window.input_box, Qt.Key.Key_Return)
        else:
            QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 8
        while window.reply_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.reply_thread is None, "Offline reply did not finish"

    window.resize(460, 560)
    send("你好，今天想和你轻松聊几句。", enter=True)
    assert NORMAL_REPLY in window.transcript.toPlainText()
    capture("chat-default.png")
    send("请给我一段长回复，看看换行和特殊字符。")
    plain = window.transcript.toPlainText()
    assert "<标签> & 引号“你好” / emoji 🌙" in plain
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 5 in plain
    capture("chat-long-bottom.png")
    window.transcript.verticalScrollBar().setValue(0)
    capture("chat-long-top.png")
    clipboard = app.clipboard()
    original_clipboard = clipboard.text()
    try:
        window.transcript.setFocus()
        QTest.keyClick(window.transcript, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window.transcript, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert "<标签> & 引号“你好” / emoji 🌙" in clipboard.text()
        assert NORMAL_REPLY in clipboard.text()
    finally:
        clipboard.setText(original_clipboard)
        cursor = window.transcript.textCursor()
        cursor.clearSelection()
        window.transcript.setTextCursor(cursor)
    window.resize(360, 420)
    app.processEvents()
    assert window.transcript.document().size().width() <= window.transcript.viewport().width() + 1
    capture("chat-small.png")
    window.resize(700, 740)
    capture("chat-wide.png")
    window.resize(460, 560)
    today_id = window.current_session_id
    history_before = window.chat_history_manager.history_file.read_bytes()
    assert window.switch_chat_session(old_id)
    assert not window.input_box.isEnabled() and not window.send_button.isEnabled()
    window.input_box.setText("不能写入昨天")
    window.send_message()
    assert window.chat_history_manager.history_file.read_bytes() == history_before
    capture("chat-history-readonly.png")
    QTest.mouseClick(window.return_today_button, Qt.MouseButton.LeftButton)
    assert window.current_session_id == today_id
    assert window.input_box.isEnabled()
    assert window.chat_history_manager.history_file.read_bytes() == history_before
    window.input_box.clear()
    capture("chat-return-today.png")
    report.update({"window_logical_size": [window.width(), window.height()],
                   "offline_reply_calls": window.llm_client.calls,
                   "selection_copy": True, "history_unchanged": True,
                   "enter_and_button_send": True, "resize_wrapping": True})
    (output / "environment-and-checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="真实聊天窗口离线样板；不读取正式用户数据或调用在线模型")
    parser.add_argument("--capture", action="store_true", help="自动操作并保存各状态截图后退出")
    args = parser.parse_args()
    run = PROJECT_ROOT / "test_workspace" / "runs" / ("chat-preview-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    run.mkdir(parents=True)
    temp = run / "temp"
    temp.mkdir()
    os.environ["TEMP"] = str(temp)
    os.environ["TMP"] = str(temp)
    app = QApplication(sys.argv[:1])
    install_titlebar_policy(app)
    window, old_id = build_preview_window(run / "app")
    window.show()
    print(f"隔离预览目录：{run}", flush=True)
    if args.capture:
        def finish():
            try:
                exercise_preview(window, old_id, run / "artifacts")
            except Exception:
                window.close()
                app.exit(1)
                raise
            else:
                window.close()
                app.quit()
        QTimer.singleShot(350, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
