import json
import os
import sys
import tempfile
import time
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.contracts import AgentResponse
from PySide6.QtWidgets import QApplication
from frontend import pet_app
from frontend.pet_app import ChatWindow


def make_manager(root: Path, enabled: bool = True) -> ChatHistoryManager:
    return ChatHistoryManager(
        root / "private",
        now_provider=lambda: datetime(2026, 7, 15, 10, 30, 0),
        enabled=enabled,
    )


def test_missing_files_are_created_and_messages_are_saved():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        assert manager.history_file.exists()
        assert manager.summary_file.exists()

        session = manager.new_session()
        manager.add_message(session["session_id"], "user", "你好", intent="chat")
        manager.add_message(session["session_id"], "assistant", "你好。")

        restored = make_manager(Path(temp_dir))
        latest = restored.latest_session()
        assert latest is not None
        assert latest["message_count"] == 2
        assert [item["role"] for item in latest["messages"]] == ["user", "assistant"]


def test_daily_learning_session_reuses_each_date_and_survives_rebuild(tmp_path):
    manager = make_manager(tmp_path)
    days = ["2026-10-09", "2026-10-10", "2026-10-11"]
    created = [manager.ensure_daily_learning_session(day) for day in days]
    assert len({item["session_id"] for item in created}) == 3
    assert all(item["session_kind"] == "daily_learning" for item in created)
    assert [item["business_date"] for item in created] == days
    assert all(item["started_at"] == "2026-07-15T10:30:00" for item in created)
    assert [manager.ensure_daily_learning_session(day)["session_id"] for day in days] == [
        item["session_id"] for item in created
    ]
    rebuilt = make_manager(tmp_path)
    assert [rebuilt.ensure_daily_learning_session(day)["session_id"] for day in days] == [
        item["session_id"] for item in created
    ]


def test_daily_lookup_preserves_manual_legacy_messages_and_timestamps(tmp_path):
    manager = make_manager(tmp_path)
    manual = manager.new_session("手动会话")
    manager.add_message(manual["session_id"], "user", "原有消息")
    document = json.loads(manager.history_file.read_text(encoding="utf-8"))
    document["sessions"][0]["future_field"] = {"keep": True}
    document["sessions"][0]["messages"][0]["future_message_field"] = "保留"
    manager.history_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    legacy_before = document["sessions"][0]

    daily = manager.ensure_daily_learning_session("2026-10-09")
    assert daily["session_id"] != manual["session_id"]
    assert "session_kind" not in manager.get_session(manual["session_id"])
    after_create = json.loads(manager.history_file.read_text(encoding="utf-8"))
    assert after_create["sessions"][0] == legacy_before
    before_lookup = manager.history_file.read_bytes()
    updated_at = daily["updated_at"]
    latest_before = manager.latest_session()["session_id"]
    manager.now_provider = lambda: datetime(2026, 10, 12, 12, 0)
    found = manager.ensure_daily_learning_session("2026-10-09")
    assert found["updated_at"] == updated_at
    assert manager.history_file.read_bytes() == before_lookup
    assert manager.latest_session()["session_id"] == latest_before


@pytest.mark.parametrize("bad_date", ["", "20261009", "2026-02-30", "2026-10-9", " 2026-10-09", None])
def test_daily_learning_session_rejects_invalid_date_without_writes(tmp_path, bad_date):
    manager = make_manager(tmp_path)
    before = manager.history_file.read_bytes()
    with pytest.raises(ValueError):
        manager.ensure_daily_learning_session(bad_date)
    assert manager.history_file.read_bytes() == before


def test_daily_learning_session_rejects_conflict_without_mutating_history(tmp_path):
    manager = make_manager(tmp_path)
    first = manager.ensure_daily_learning_session("2026-10-09")
    document = json.loads(manager.history_file.read_text(encoding="utf-8"))
    duplicate = {**first, "session_id": "session_duplicate"}
    document["sessions"].append(duplicate)
    manager.history_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    before = manager.history_file.read_bytes()
    with pytest.raises(ValueError, match="Multiple daily learning sessions for 2026-10-09: 2"):
        manager.ensure_daily_learning_session("2026-10-09")
    assert manager.history_file.read_bytes() == before


def test_daily_learning_session_reports_failed_or_unverified_save(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    before = manager.history_file.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(manager.repository, "save_sessions", lambda _sessions: False)
        with pytest.raises(OSError, match="Failed to save daily learning session"):
            manager.ensure_daily_learning_session("2026-10-09")
    assert manager.history_file.read_bytes() == before
    with monkeypatch.context() as patch:
        patch.setattr(manager.repository, "save_sessions", lambda _sessions: True)
        with pytest.raises(OSError, match="Failed to verify daily learning session"):
            manager.ensure_daily_learning_session("2026-10-09")
    assert manager.history_file.read_bytes() == before


def test_daily_learning_session_respects_disabled_history_and_damaged_document(tmp_path):
    manager = make_manager(tmp_path)
    manager.set_enabled(False)
    before = manager.history_file.read_bytes()
    temporary = manager.ensure_daily_learning_session("2026-10-09")
    assert manager.ensure_daily_learning_session("2026-10-09")["session_id"] == temporary["session_id"]
    assert temporary["business_date"] == "2026-10-09"
    assert manager.history_file.read_bytes() == before

    manager.set_enabled(True)
    manager.history_file.write_text('{"version":1,"other":"keep"}', encoding="utf-8")
    before = manager.history_file.read_bytes()
    with pytest.raises(ValueError, match="Invalid chat sessions"):
        manager.ensure_daily_learning_session("2026-10-09")
    assert manager.history_file.read_bytes() == before


def test_daily_learning_session_concurrent_creation_uses_one_session(tmp_path):
    first = make_manager(tmp_path)
    second = make_manager(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(
            lambda manager: manager.ensure_daily_learning_session("2026-10-09")["session_id"],
            (first, second),
        ))
    assert ids[0] == ids[1]
    assert len(make_manager(tmp_path).sessions()) == 1


def test_disabled_daily_session_concurrent_reuse_stays_in_memory(tmp_path):
    manager = make_manager(tmp_path, enabled=False)
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(
            manager.ensure_daily_learning_session,
            ["2026-10-09"] * 8,
        ))
    assert len({item["session_id"] for item in ids}) == 1
    assert not manager.history_file.exists()


def test_new_switch_delete_and_title_generation():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        first = manager.new_session()
        manager.add_message(first["session_id"], "user", "讨论 RoxyPlan 聊天历史设计")
        manager.add_message(first["session_id"], "assistant", "好。")
        manager.add_message(first["session_id"], "user", "继续")
        second = manager.new_session()

        titled = manager.get_session(first["session_id"])
        assert titled["title"].startswith("讨论 RoxyPlan")
        assert manager.switch_session(first["session_id"])["session_id"] == first["session_id"]
        assert manager.delete_session(second["session_id"]) is True
        assert manager.get_session(second["session_id"]) is None


def test_clear_requires_confirmation_and_disabled_storage_does_not_write_messages():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = make_manager(root)
        session = manager.new_session()
        manager.add_message(session["session_id"], "user", "保留")
        assert manager.clear_history(confirmed=False) is False
        assert len(manager.sessions()) == 1

        manager.set_enabled(False)
        assert manager.add_message(session["session_id"], "user", "不写入") is None
        disk_data = json.loads(manager.history_file.read_text(encoding="utf-8"))
        assert len(disk_data["sessions"][0]["messages"]) == 1

        assert manager.clear_history(confirmed=True) is True
        assert manager.sessions() == []


def test_summary_is_separate_and_rule_fallback_works():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        session = manager.new_session()
        session_id = session["session_id"]
        manager.add_message(session_id, "user", "我决定继续完善聊天历史")
        manager.add_message(session_id, "assistant", "好，我们分步推进。")
        manager.add_message(session_id, "user", "下次继续测试会话切换")

        summary = manager.generate_rule_summary(session_id)
        assert "主要讨论" in summary
        assert "下次可继续" in summary
        assert manager.save_summary(session_id, summary) is True
        assert manager.get_summary(session_id) == summary

        history_data = json.loads(manager.history_file.read_text(encoding="utf-8"))
        summary_data = json.loads(manager.summary_file.read_text(encoding="utf-8"))
        assert summary_data["summaries"][session_id]["summary"] == summary
        assert "memories" not in history_data


def test_summary_threshold_uses_message_count_or_character_count():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        session = manager.new_session()
        session_id = session["session_id"]
        for index in range(31):
            manager.add_message(session_id, "user", f"消息 {index}")
        assert manager.should_summarize(session_id, message_threshold=30) is True

        summary = manager.generate_rule_summary(session_id)
        manager.save_summary(session_id, summary)
        assert manager.should_summarize(session_id, message_threshold=30) is False

        long_session = manager.new_session()
        manager.add_message(long_session["session_id"], "user", "内容" * 100)
        assert manager.should_summarize(
            long_session["session_id"],
            message_threshold=30,
            character_threshold=100,
        ) is True


def test_chat_window_restores_daily_session_and_disables_history_commands():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = make_manager(root)
        original_memory_file = pet_app.MEMORY_FILE
        original_config_file = pet_app.CONFIG_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        pet_app.CONFIG_FILE = root / "config.json"
        growth = GrowthManager(root / "growth")
        try:
            first = ChatWindow(
                chat_history_manager=manager,
                growth_service=growth,
                llm_client=_OfflineChatClient(),
            )
            first.add_message("You", "讨论本地会话恢复")
            first.add_message("Roxy", "好，我们继续。")
            session_id = first.current_session_id
            first.close()

            second = ChatWindow(
                chat_history_manager=manager,
                growth_service=growth,
                llm_client=_OfflineChatClient(),
            )
            assert not hasattr(second, "new_chat_button")
            assert second.current_session_id == session_id
            assert "讨论本地会话恢复" in second.transcript.toPlainText()

            second.input_box.setText("新建对话")
            second.send_message()
            assert second.current_session_id == session_id
            assert len(manager.sessions()) == 1
            second.new_chat_session()
            assert second.current_session_id == session_id

            second.input_box.setText("清空聊天记录")
            second.send_message()
            assert len(manager.sessions()) == 1
            second.input_box.setText("确认清空聊天记录")
            second.send_message()
            assert len(manager.sessions()) == 1
            assert "已关闭历史删除和清空操作" in second.transcript.toPlainText()
            second.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.CONFIG_FILE = original_config_file


class _OfflineChatClient:
    def chat(self, *_args, **_kwargs):
        return "隔离测试回复"


def _daily_test_window(tmp_path, monkeypatch, day, *, manager=None, settings=None):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(pet_app, "MEMORY_FILE", tmp_path / "memory.json")
    monkeypatch.setattr(pet_app, "CONFIG_FILE", tmp_path / "llm_config.json")
    monkeypatch.setattr(pet_app, "PERSONALITY_FILE", tmp_path / "personality.json")
    monkeypatch.setattr(pet_app, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    manager = manager or make_manager(tmp_path)
    growth = GrowthManager(
        tmp_path / "growth",
        now_provider=lambda: datetime.fromisoformat(f"{day}T09:00:00"),
    )
    window = ChatWindow(
        chat_history_manager=manager,
        growth_service=growth,
        llm_client=_OfflineChatClient(),
        pet_settings=settings or {
            "chat_history_enabled": True,
            "restore_last_session": True,
        },
        settings_config_file=tmp_path / "pet_config.json",
    )
    return window, manager


def test_daily_window_startup_restores_same_day_and_keeps_previous_day(tmp_path, monkeypatch):
    first, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09")
    first_id = first.current_session_id
    assert manager.get_session(first_id)["business_date"] == "2026-10-09"
    first.add_message("You", "第一天的隔离学习记录")
    first.close()

    second, rebuilt = _daily_test_window(tmp_path, monkeypatch, "2026-10-09")
    assert second.current_session_id == first_id
    assert "第一天的隔离学习记录" in second.transcript.toPlainText()
    second.close()

    third, rebuilt = _daily_test_window(tmp_path, monkeypatch, "2026-10-10")
    assert third.current_session_id != first_id
    assert rebuilt.get_session(third.current_session_id)["business_date"] == "2026-10-10"
    assert "第一天的隔离学习记录" not in third.transcript.toPlainText()
    assert any(
        item["content"] == "第一天的隔离学习记录"
        for item in rebuilt.get_session(first_id)["messages"]
    )
    assert third.switch_chat_session(first_id)
    assert "第一天的隔离学习记录" in third.transcript.toPlainText()
    assert not third.send_button.isEnabled()
    third.new_chat_session()
    assert third.current_session_id == first_id
    third.return_today_session()
    assert third.current_session_id not in {first_id, second.current_session_id}
    third.close()


def test_daily_window_prefers_today_even_when_yesterday_is_more_recent(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    today = manager.ensure_daily_learning_session("2026-10-10")
    yesterday = manager.ensure_daily_learning_session("2026-10-09")
    manager.now_provider = lambda: datetime(2026, 7, 15, 11, 30, 0)
    manager.add_message(yesterday["session_id"], "user", "昨天较晚的消息")
    assert manager.latest_session()["session_id"] == yesterday["session_id"]

    window, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-10", manager=manager)
    assert window.current_session_id == today["session_id"]
    assert "昨天较晚的消息" not in window.transcript.toPlainText()
    window.close()


@pytest.mark.parametrize("saving", [True, False])
def test_daily_window_respects_existing_history_settings(
    tmp_path, monkeypatch, saving
):
    manager = make_manager(tmp_path)
    manual = manager.new_session("手动会话")
    before = manager.history_file.read_bytes()
    window, _ = _daily_test_window(
        tmp_path, monkeypatch, "2026-10-10", manager=manager,
        settings={"chat_history_enabled": saving, "restore_last_session": False},
    )
    assert window.current_session_id != manual["session_id"]
    if saving:
        assert manager.get_session(window.current_session_id)["business_date"] == "2026-10-10"
    else:
        assert manager.history_file.read_bytes() == before
        assert all(item.get("session_kind") != "daily_learning" for item in manager.sessions())
        first_id = window.current_session_id
        window.add_message("You", "进程内临时消息")
        window.close()
        another, _ = _daily_test_window(
            tmp_path, monkeypatch, "2026-10-10", manager=manager,
            settings={"chat_history_enabled": False, "restore_last_session": False},
        )
        assert another.current_session_id == first_id
        assert "进程内临时消息" in another.transcript.toPlainText()
        another.close()
    window.close()


def test_daily_window_failure_does_not_bind_latest_or_write_history(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    manual = manager.new_session("旧会话")
    before = manager.history_file.read_bytes()
    original_ensure = manager.ensure_daily_learning_session
    monkeypatch.setattr(
        manager, "ensure_daily_learning_session",
        lambda _day: (_ for _ in ()).throw(OSError("isolated save failure")),
    )
    window, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-10", manager=manager)
    assert window.current_session_id == ""
    assert "会话不可用" in window.session_title_label.text()
    assert "读取失败" in window.transcript.toPlainText()
    assert not window.send_button.isEnabled()
    window.input_box.setText("不能写入的消息")
    window.send_message()
    assert manager.history_file.read_bytes() == before
    assert manager.get_session(manual["session_id"])["messages"] == []
    window.new_chat_session()
    assert window.current_session_id == ""
    assert not window.send_button.isEnabled()
    monkeypatch.setattr(manager, "ensure_daily_learning_session", original_ensure)
    window.return_today_session()
    assert window.current_session_id not in {"", manual["session_id"]}
    assert window.send_button.isEnabled()
    assert len([s for s in manager.sessions() if s.get("session_kind") == "daily_learning"]) == 1
    window.close()


def test_daily_window_history_is_read_only_and_return_today(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    old = manager.new_session("旧手动会话")
    manager.add_message(old["session_id"], "user", "旧会话原文")
    window, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager)
    today_id = window.current_session_id
    before = manager.history_file.read_bytes()
    old_updated_at = manager.get_session(old["session_id"])["updated_at"]
    assert window.switch_chat_session(old["session_id"])
    assert "历史对话 · 只读" in window.session_title_label.text()
    assert window.return_today_button.isVisibleTo(window)
    assert not window.input_box.isEnabled() and not window.send_button.isEnabled()
    window.input_box.setText("不能写入历史")
    window.send_message()
    assert manager.history_file.read_bytes() == before
    assert window.delete_chat_session(old["session_id"]) is False
    assert manager.history_file.read_bytes() == before
    assert manager.get_session(old["session_id"])["updated_at"] == old_updated_at
    assert "旧会话原文" in window.transcript.toPlainText()
    window.return_today_session()
    assert window.current_session_id == today_id
    assert window.input_box.isEnabled() and window.send_button.isEnabled()
    assert manager.history_file.read_bytes() == before
    window.close()


def test_daily_window_reopen_and_send_after_day_change(tmp_path, monkeypatch):
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09")
    first_id = window.current_session_id
    clock = {"day": "2026-10-09"}
    window.growth_service.now_provider = lambda: datetime.fromisoformat(
        f"{clock['day']}T09:00:00"
    )
    window.show()
    window.hide()
    clock["day"] = "2026-10-10"
    window.show()
    second_id = window.current_session_id
    assert second_id != first_id
    assert manager.get_session(second_id)["business_date"] == "2026-10-10"
    clock["day"] = "2026-10-11"
    window.input_box.setText("新建对话")
    window.send_message()
    third_id = window.current_session_id
    assert third_id not in {first_id, second_id}
    assert manager.get_session(third_id)["business_date"] == "2026-10-11"
    assert any(item["content"] == "新建对话" for item in manager.messages(third_id))
    assert all(item["content"] != "新建对话" for item in manager.messages(second_id))
    assert len([s for s in manager.sessions() if s.get("session_kind") == "daily_learning"]) == 3
    window.close()


def test_disabled_history_uses_only_process_local_daily_sessions(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, enabled=False)
    assert not manager.history_file.exists()
    assert not manager.summary_file.exists()
    settings = {"chat_history_enabled": False, "restore_last_session": False}
    first, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager, settings=settings)
    first_id = first.current_session_id
    first.add_message("You", "仅进程内消息")
    first.close()
    second, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager, settings=settings)
    assert second.current_session_id == first_id
    assert "仅进程内消息" in second.transcript.toPlainText()
    assert not manager.history_file.exists()
    assert not manager.summary_file.exists()
    second.close()


def test_daily_window_switches_storage_mode_without_writing_old_session(tmp_path, monkeypatch):
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09")
    persisted_id = window.current_session_id
    before = manager.history_file.read_bytes()
    window.apply_chat_settings({"chat_history_enabled": False})
    temporary_id = window.current_session_id
    assert temporary_id != persisted_id
    window.add_message("You", "关闭保存后的进程内记录")
    assert manager.history_file.read_bytes() == before
    assert "关闭保存后的进程内记录" in manager.get_session(temporary_id)["messages"][-1]["content"]
    window.apply_chat_settings({"chat_history_enabled": True})
    assert window.current_session_id == persisted_id
    assert all(item["content"] != "关闭保存后的进程内记录" for item in manager.messages(persisted_id))
    window.close()


def test_daily_window_windows_qt_three_states(tmp_path, monkeypatch):
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "windows":
        pytest.skip("Windows Qt rendering only")
    manager = make_manager(tmp_path)
    old = manager.new_session("旧手动会话")
    manager.add_message(old["session_id"], "user", "此前学习笔记仍可回看")
    window, _ = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager)
    window.show()
    QApplication.processEvents()
    artifact_dir = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts"
    screen = QApplication.primaryScreen()
    assert screen is not None
    def capture(name):
        QApplication.processEvents()
        artifact = artifact_dir / name
        assert screen.grabWindow(int(window.winId())).save(str(artifact))
        assert artifact.stat().st_size > 0
    capture("daily-chat-today.png")
    assert window.switch_chat_session(old["session_id"])
    capture("daily-chat-history-readonly.png")
    window.return_today_session()
    capture("daily-chat-return-today.png")
    window.close()


def test_daily_window_windows_qt_render(tmp_path, monkeypatch):
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "windows":
        pytest.skip("Windows Qt rendering only")
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-10")
    manager.add_message(window.current_session_id, "user", "今天继续整理学习笔记")
    window.restore_current_session()
    window.show()
    QApplication.processEvents()
    artifact = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts" / "daily-learning-chat-window.png"
    assert window.grab().save(str(artifact))
    assert artifact.exists() and artifact.stat().st_size > 0
    window.close()


def test_delayed_reply_after_session_switch_is_saved_only_in_origin(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    historical = manager.new_session("旧手动会话")
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager)
    original_id = window.current_session_id
    window._active_reply_trace = window.development_log.new_trace(
        session_id=original_id, source="chat"
    )
    window._message_sequence = window._active_reply_sequence = 1
    assert window.switch_chat_session(historical["session_id"])
    current_id = window.current_session_id
    before_current = manager.messages(current_id)

    window.finish_ai_reply(
        AgentResponse(
            "chat", "延迟回复只属于原会话", request_id="req_delayed",
            conversation_id=original_id,
        ),
        1,
    )
    assert manager.messages(original_id)[-1]["content"] == "延迟回复只属于原会话"
    assert manager.messages(current_id) == before_current
    assert "延迟回复只属于原会话" not in window.transcript.toPlainText()
    window.close()


def test_delayed_reply_does_not_recreate_deleted_origin(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    historical = manager.new_session("旧手动会话")
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager)
    original_id = window.current_session_id
    window._active_reply_trace = window.development_log.new_trace(
        session_id=original_id, source="chat"
    )
    window._message_sequence = window._active_reply_sequence = 1
    assert window.switch_chat_session(historical["session_id"])
    current_id = window.current_session_id
    before_current = manager.messages(current_id)
    assert manager.delete_session(original_id)

    window.finish_ai_reply(
        AgentResponse("chat", "不应保存的延迟回复", conversation_id=original_id),
        1,
    )
    assert manager.get_session(original_id) is None
    assert manager.messages(current_id) == before_current
    window.close()


def test_running_reply_survives_manual_session_switch(tmp_path, monkeypatch):
    class BlockingClient:
        def __init__(self):
            self.started = Event()
            self.release = Event()

        def chat(self, messages):
            prompt = str(messages[0].get("content", "")) if messages else ""
            if "You classify one Chinese user message" in prompt:
                self.started.set()
                assert self.release.wait(5)
                return json.dumps({
                    "mode": "chat", "intent": "chat", "entities": {},
                    "proposed_tool": None, "confidence": 0.98,
                    "follow_up_target": None, "needs_confirmation": False,
                    "warnings": [], "clarification_question": None,
                    "candidate_actions": [], "subject": "self", "polarity": "positive",
                    "modality": "", "request_mode": "discuss",
                    "explicit_command": False,
                }, ensure_ascii=False)
            return "这是原会话的延迟回答"

    from modules.intent_router import LLMIntentParser

    manager = make_manager(tmp_path)
    historical = manager.new_session("旧手动会话")
    window, manager = _daily_test_window(tmp_path, monkeypatch, "2026-10-09", manager=manager)
    client = BlockingClient()
    window.conversation_service.llm_client = client
    window.intent_router.configure_llm(LLMIntentParser(client.chat), True)
    window.conversation_service.semantic_decision_compatibility_enabled = False
    original_id = window.current_session_id
    try:
        window.input_box.setText("聊聊今天的学习")
        window.send_message()
        assert client.started.wait(5)
        assert window.switch_chat_session(historical["session_id"])
        current_id = window.current_session_id
        client.release.set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if any(
                item["content"] == "这是原会话的延迟回答"
                for item in manager.messages(original_id)
            ):
                break
            time.sleep(0.02)
        assert any(
            item["content"] == "这是原会话的延迟回答"
            for item in manager.messages(original_id)
        )
        saved_reply = manager.messages(original_id)[-1]
        assert saved_reply["metadata"]["business_date"] == "2026-10-09"
        assert saved_reply["metadata"]["request_id"]
        assert saved_reply["metadata"]["trace_id"]
        assert all(
            item["content"] != "这是原会话的延迟回答"
            for item in manager.messages(current_id)
        )
    finally:
        client.release.set()
        deadline = time.monotonic() + 8
        while window.reply_thread is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
        window.close()


if __name__ == "__main__":
    test_missing_files_are_created_and_messages_are_saved()
    test_new_switch_delete_and_title_generation()
    test_clear_requires_confirmation_and_disabled_storage_does_not_write_messages()
    test_summary_is_separate_and_rule_fallback_works()
    test_summary_threshold_uses_message_count_or_character_count()
    test_chat_window_restores_sessions_and_clear_needs_second_command()
    print("chat history tests passed")
