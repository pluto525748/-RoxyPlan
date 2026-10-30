"""Entry contracts. Run only in the coordinator's offscreen Qt test slot."""

from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import frontend.desktop_pet as desktop_pet
import frontend.pet_app as pet_app
from modules.development_log import DevelopmentLog
from scripts.preview_chat import OfflinePreviewClient, build_preview_window
from tests.semantic_contract_fixtures import build_current_semantic_payload


class OfflineClient(OfflinePreviewClient):
    def __init__(self, root, **_kwargs):
        super().__init__(root)
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def chat(self, messages, **_kwargs):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(build_current_semantic_payload({
                "mode": "chat", "intent": "chat", "entities": {},
                "proposed_tool": None, "confidence": 0.99,
                "follow_up_target": None, "needs_confirmation": False,
                "warnings": [], "clarification_question": None, "candidate_actions": [],
                "subject": "self", "polarity": "positive", "modality": "question",
                "request_mode": "discuss", "explicit_command": False,
            }), ensure_ascii=False)
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("Offline reply fixture was not released")
        return "隔离回复：云像一条小船。\n<字符> & 保留换行。"


def pump(app, predicate, timeout=5):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), "Qt entry did not reach the expected state"


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    policy = app.quitOnLastWindowClosed()
    root = tmp_path / "desktop"
    config = root / "data/pet_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "test_mode": False, "auto_tips_enabled": False, "proactive_enabled": False,
        "evening_review_enabled": False, "idle_nudge_enabled": False,
        "auto_complete_yesterday_review": False, "chat_history_enabled": True,
        "enable_memory_candidates": False, "pet_always_on_top": False,
    }), encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(pet_app, "RoutedLLMClient", OfflineClient)
    pet = pet_app._acceptance_desktop_pet(root, DevelopmentLog(root / "logs", enabled=False))
    pet.show()
    pet.open_chat_window()
    yield app, pet, root
    chat = pet.chat_window
    chat.llm_client.release.set()
    pump(app, lambda: chat.reply_thread is None)
    # Our windows only; no desktop or other employee's process is involved.
    for window in (chat.usage_dialog, chat.settings_dialog, chat.today_growth_dialog,
                   chat.growth_dialog, chat.memory_dialog, chat.chat_history_dialog,
                   pet.usage_dialog, pet.settings_dialog, pet.today_growth_dialog,
                   pet.growth_dialog, chat, pet.bubble):
        if window is not None:
            window.close()
    for timer in pet.findChildren(QTimer):
        timer.stop()
    pet.actions.stop_idle_breathing()
    pet.close()
    app.processEvents()
    app.setQuitOnLastWindowClosed(policy)


def test_close_reopen_preserves_pet_draft_day_and_saved_history(desktop):
    app, pet, root = desktop
    chat = pet.chat_window
    session_id = chat.current_session_id
    chat.add_message("You", "入口隔离记录")
    chat.input_box.setText("尚未发送的草稿")
    history_file = root / "data/private/chat_history.json"
    before = history_file.read_bytes()
    chat.close()
    app.processEvents()
    assert pet.isVisible() and not chat.isVisible()
    assert not app.quitOnLastWindowClosed()
    assert not chat.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    assert not chat.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
    pet.open_chat_window()
    assert pet.chat_window is chat and chat.current_session_id == session_id
    assert chat.input_box.text() == "尚未发送的草稿"
    assert "入口隔离记录" in chat.transcript.toPlainText()
    assert history_file.read_bytes() == before


@pytest.mark.parametrize("reopen_before_reply", [False, True])
def test_close_during_real_worker_saves_one_reply_in_original_session(desktop, reopen_before_reply):
    app, pet, _root = desktop
    chat = pet.chat_window
    origin = chat.current_session_id
    client = chat.llm_client
    client.release.clear()
    # Real prepare/worker/complete/presentation/persistence with an offline client.
    text = "窗外的云是什么形状？"
    turn = chat.conversation_service.prepare(text, origin, record_history=False,
                                            allow_llm_intent=False)
    assert turn.requires_llm
    chat.add_message("You", text)
    chat._pending_conversation_turn = turn
    chat.start_ai_reply(text)
    try:
        pump(app, client.started.is_set)
        thread = chat.reply_thread
        chat.close()
        assert thread.isRunning() and pet.isVisible()
        if reopen_before_reply:
            pet.open_chat_window()
            assert chat.current_session_id == origin
        client.release.set()
        pump(app, lambda: chat.reply_thread is None)
        saved = chat.chat_history_manager.get_session(origin)["messages"]
        replies = [m for m in saved if m["role"] == "assistant" and "隔离回复" in m["content"]]
        assert len(replies) == 1
        assert "\n<字符> &" in replies[0]["content"]
        pet.open_chat_window()
        assert chat.current_session_id == origin and "隔离回复" in chat.transcript.toPlainText()
    finally:
        client.release.set()
        pump(app, lambda: chat.reply_thread is None)


def test_original_session_receives_hidden_reply_after_day_changes(desktop):
    app, pet, _root = desktop
    chat = pet.chat_window
    origin = chat.current_session_id
    client = chat.llm_client
    client.release.clear()
    text = "窗外的云是什么形状？"
    turn = chat.conversation_service.prepare(text, origin, record_history=False, allow_llm_intent=False)
    chat.add_message("You", text)
    chat._pending_conversation_turn = turn
    chat.start_ai_reply(text)
    try:
        pump(app, client.started.is_set)
        chat.close()
        next_day = (chat.growth_service.now_provider().date() + timedelta(days=1)).isoformat()
        assert chat._select_today_session(next_day)
        new_id = chat.current_session_id
        client.release.set()
        pump(app, lambda: chat.reply_thread is None)
        assert new_id != origin
        assert sum("隔离回复" in m["content"] for m in chat.chat_history_manager.get_session(origin)["messages"]) == 1
        assert not any("隔离回复" in m["content"] for m in chat.chat_history_manager.get_session(new_id)["messages"])
    finally:
        client.release.set()
        pump(app, lambda: chat.reply_thread is None)


def test_explicit_quit_waits_for_reply_save_and_blocks_new_send(desktop, monkeypatch):
    app, pet, _root = desktop
    chat = pet.chat_window
    exits = []
    monkeypatch.setattr(pet_app.QApplication, "quit", lambda: exits.append("quit"))
    client = chat.llm_client
    client.release.clear()
    text = "窗外的云是什么形状？"
    turn = chat.conversation_service.prepare(text, chat.current_session_id,
                                            record_history=False, allow_llm_intent=False)
    chat._pending_conversation_turn = turn
    chat.start_ai_reply(text)
    try:
        pump(app, client.started.is_set)
        pet.request_quit()
        assert not exits and not chat.send_button.isEnabled()
        before = chat.chat_history_manager.get_session(chat.current_session_id)
        chat.input_box.setText("不能在退出途中新增")
        chat.send_message()
        assert chat.chat_history_manager.get_session(chat.current_session_id) == before
        client.release.set()
        pump(app, lambda: bool(exits))
        assert chat.reply_thread is None
        assert sum("隔离回复" in m["content"] for m in chat.chat_history_manager.get_session(chat.current_session_id)["messages"]) == 1
        assert exits == ["quit"]
    finally:
        client.release.set()
        pump(app, lambda: chat.reply_thread is None)


def test_three_pet_actions_and_chat_interactions_use_existing_dispatcher(desktop, monkeypatch):
    app, pet, _root = desktop
    menu, actions = pet._create_context_menu()
    assert [a.text() for a in menu.actions()] == ["打开聊天", "唤醒", "睡眠"]
    dispatched = []
    monkeypatch.setattr(pet.client_action_dispatcher, "dispatch",
                        lambda action: (dispatched.append(action) or SimpleNamespace(accepted=True)))
    for key in ("wake", "sleep"):
        monkeypatch.setattr(pet, "_create_context_menu", lambda key=key: (
            SimpleNamespace(exec=lambda _pos: actions[key]), actions))
        pet.contextMenuEvent(SimpleNamespace(globalPos=lambda: QPoint(0, 0)))
    chat = pet.chat_window
    chat.dance_action.trigger()
    chat.encourage_action.trigger()
    assert [a.name for a in dispatched] == ["wake", "sleep", "play_dance", "jump"]
    assert all(a.source == "desktop_menu" for a in dispatched)
    menu.close()


def test_chat_growth_details_and_remaining_windows_are_reachable(desktop):
    app, pet, _root = desktop
    chat = pet.chat_window
    assert chat.growth_button.menu() is None
    chat.growth_button.click()
    panel = pet.today_growth_dialog
    assert panel.growth_manager is chat.growth_service
    size, image = panel.size(), panel.pet_image.pixmap().cacheKey()
    panel.details_button.click()
    assert pet.growth_dialog.growth_manager is panel.growth_manager
    assert pet.growth_dialog.tabs.count() == 3
    assert panel.size() == size and panel.pet_image.pixmap().cacheKey() == image
    chat.memory_button.click()
    assert chat.memory_dialog.memory_service is chat.memory_service
    chat.settings_button.click()
    assert chat.settings_dialog.config_file == pet.config_file
    chat.usage_action.trigger()
    assert pet.usage_dialog.isVisible()


class SettingsSignals(QDialog):
    """Only the pending CC interface, without duplicating its implementation."""
    settings_saved = Signal(dict)
    history_cleared = Signal()
    open_today_growth_requested = Signal()
    usage_help_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None, **_kwargs):
        super().__init__(parent)
        self.open_today_growth_button = SimpleNamespace(setEnabled=lambda _: None, setToolTip=lambda _: None)


class UsageSignals(QDialog):
    example_selected = Signal(str)


def test_real_cc_components_wire_examples_interactions_and_settings(desktop, monkeypatch):
    from frontend.interaction_menu import InteractionMenu
    from frontend.usage_dialog import UsageDialog

    app, pet, _root = desktop
    chat = pet.chat_window
    before = chat.chat_history_manager.get_session(chat.current_session_id)
    pet.open_usage_dialog()
    usage = pet.usage_dialog
    try:
        assert isinstance(usage, UsageDialog)
        assert isinstance(chat.interaction_button, InteractionMenu)
        assert "填入聊天输入框" in usage.subtitle.text()
        example = usage.example_texts()[0]
        usage.example_buttons[0].click()
        assert chat.input_box.text() == example
        assert chat.reply_thread is None
        assert chat.chat_history_manager.get_session(chat.current_session_id) == before

        chat.input_box.setText("还没发送的草稿")
        monkeypatch.setattr(pet_app.QMessageBox, "question", lambda *_args: QMessageBox.No)
        usage.example_buttons[0].click()
        assert chat.input_box.text() == "还没发送的草稿"
        monkeypatch.setattr(pet_app.QMessageBox, "question", lambda *_args: QMessageBox.Yes)
        usage.example_buttons[0].click()
        assert chat.input_box.text() == example

        actions = []
        monkeypatch.setattr(pet, "start_dance", lambda: actions.append("dance"))
        monkeypatch.setattr(pet, "encourage", lambda: actions.append("encourage"))
        chat.dance_action.trigger()
        chat.encourage_action.trigger()
        assert actions == ["dance", "encourage"]
        for open_settings in (pet.open_settings_dialog, chat.open_settings_dialog):
            open_settings()
            settings = pet.settings_dialog if open_settings == pet.open_settings_dialog else chat.settings_dialog
            settings.usage_help_requested.emit()
            assert pet.usage_dialog is usage and usage.isVisible()
            settings._confirm_quit = lambda: False
            settings.request_quit()
            assert not chat._quit_requested

        exits = []
        monkeypatch.setattr(pet_app.QApplication, "quit", lambda: exits.append(True))
        settings._confirm_quit = lambda: True
        settings.request_quit()
        assert exits == [True] and chat._quit_requested
        assert chat.chat_history_manager.get_session(chat.current_session_id) == before
    finally:
        usage.close()
        app.processEvents()


def test_cc_signal_contract_both_settings_paths_and_draft_confirmation(desktop, monkeypatch):
    app, pet, _root = desktop
    chat = pet.chat_window
    monkeypatch.setattr(desktop_pet, "SettingsDialog", SettingsSignals)
    monkeypatch.setattr(pet_app, "SettingsDialog", SettingsSignals)
    monkeypatch.setattr(desktop_pet, "UsageDialog", UsageSignals)
    exits = []
    monkeypatch.setattr(pet_app.QApplication, "quit", lambda: exits.append(True))
    pet.open_settings_dialog()
    chat.open_settings_dialog()
    before = chat.chat_history_manager.get_session(chat.current_session_id)
    for settings in (pet.settings_dialog, chat.settings_dialog):
        settings.usage_help_requested.emit()
        assert pet.usage_dialog.isVisible()
    pet.usage_dialog.example_selected.emit("第一条示例")
    assert chat.input_box.text() == "第一条示例"
    monkeypatch.setattr(pet_app.QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.No)
    pet.usage_dialog.example_selected.emit("第二条示例")
    assert chat.input_box.text() == "第一条示例"
    monkeypatch.setattr(pet_app.QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    pet.usage_dialog.example_selected.emit("第二条示例")
    assert chat.input_box.text() == "第二条示例"
    assert chat.chat_history_manager.get_session(chat.current_session_id) == before
    for settings in (pet.settings_dialog, chat.settings_dialog):
        settings.quit_requested.emit()
    assert exits == [True, True]


def test_independent_chat_details_and_cc_signals(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    chat, _old_id = build_preview_window(tmp_path / "independent")
    monkeypatch.setattr(pet_app, "SettingsDialog", SettingsSignals)
    monkeypatch.setattr(pet_app, "UsageDialog", UsageSignals)
    exits = []
    monkeypatch.setattr(pet_app.QApplication, "quit", lambda: exits.append(True))
    try:
        chat.growth_button.click()
        chat.today_growth_dialog.details_button.click()
        assert chat.growth_dialog.growth_manager is chat.today_growth_dialog.growth_manager
        chat.open_settings_dialog()
        chat.settings_dialog.usage_help_requested.emit()
        chat.usage_dialog.example_selected.emit("独立窗口示例")
        assert chat.input_box.text() == "独立窗口示例"
        chat.settings_dialog.quit_requested.emit()
        assert exits == [True]
    finally:
        for window in (chat.today_growth_dialog, chat.growth_dialog, chat.settings_dialog, chat.usage_dialog, chat):
            if window is not None:
                window.close()
        app.processEvents()


@pytest.mark.parametrize("legacy", [True, False])
def test_real_application_loop_legacy_close_and_explicit_exit(tmp_path, legacy):
    # A fresh process proves real app.exec exit behavior, not just widget flags.
    # The 'legacy' case recreates the two old Qt policy settings on this code.
    root = tmp_path / "loop-data"
    script = r'''
import json, sys
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from modules.development_log import DevelopmentLog
import frontend.pet_app as p
from scripts.preview_chat import OfflinePreviewClient
class Offline(OfflinePreviewClient):
    def __init__(self, root, **kwargs): super().__init__(root)
root = Path(sys.argv[1])
config = root / 'data/pet_config.json'
config.parent.mkdir(parents=True)
config.write_text(json.dumps({'auto_tips_enabled':False,'proactive_enabled':False,'auto_complete_yesterday_review':False}))
p.RoutedLLMClient = Offline
app = QApplication([])
pet = p._acceptance_desktop_pet(root, DevelopmentLog(root/'logs',enabled=False))
pet.show()
pet.open_chat_window()
chat = pet.chat_window
origin = chat.current_session_id
legacy = sys.argv[2] == 'True'
if legacy:
    app.setQuitOnLastWindowClosed(True)
    chat.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose,True)
record = {'legacy':legacy,'continued':False,'quit':False}
app.aboutToQuit.connect(lambda: record.update(quit=True))
def continued():
    record['continued'] = True
    record['pet_visible'] = pet.isVisible()
    pet.open_chat_window()
    record['same_session'] = chat.current_session_id == origin
    pet.request_quit()
QTimer.singleShot(0,chat.close)
QTimer.singleShot(100,continued)
QTimer.singleShot(2000,app.quit)
app.exec()
(root/'loop-evidence.json').write_text(json.dumps(record))
'''
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('DEEPSEEK_API_KEY', None)
    result = subprocess.run([sys.executable, '-B', '-c', script, str(root), str(legacy)],
                            cwd=Path(pet_app.__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=20)
    (tmp_path / 'loop-output.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    record = json.loads((root / 'loop-evidence.json').read_text())
    assert record['quit'] and record['continued'] is not legacy
    if not legacy:
        assert record['pet_visible'] and record['same_session']
