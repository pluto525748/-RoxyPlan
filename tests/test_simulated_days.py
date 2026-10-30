"""Isolated three-day desktop simulation; no online model calls."""

from __future__ import annotations

import os
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from frontend.pet_app import (
    ChatWindow,
    _acceptance_desktop_pet,
    _acceptance_root_from_argv,
)
from modules.chat_history_manager import ChatHistoryManager
from modules.development_log import configure_development_log
from modules.growth_manager import GrowthManager
from modules.intent_router import LLMIntentParser
from modules.learning_loop import LearningLoopService
from modules.memory_service import MemoryService
from modules.simulation_clock import SimulatedBusinessClock
from modules.suggestion_snapshot import SuggestionSnapshot


class OfflineClient:
    def chat(self, _messages):
        return "隔离离线回复"


class FixedTwoPlanClient:
    """Deterministic semantic reply for a native Qt two-item acceptance run."""

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" not in prompt:
            raise AssertionError("native two-plan check must not call an online model")
        return json.dumps({
            "mode": "write", "intent": "multi_action", "entities": {},
            "proposed_tool": None, "confidence": 0.98,
            "follow_up_target": None, "needs_confirmation": True,
            "candidate_actions": [
                {"intent": "add_plan", "entities": {"title": "隔离桌面练习 SQL"}},
                {"intent": "add_plan", "entities": {"title": "隔离桌面阅读英文文档"}},
            ],
            "warnings": [], "clarification_question": None,
            "subject": "self", "polarity": "positive", "modality": "desire",
            "request_mode": "execute", "explicit_command": False,
        }, ensure_ascii=False)


def _screenshot(window, name: str) -> None:
    run_root = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"])
    target = run_root / "artifacts" / name
    app = QApplication.instance()
    app.processEvents()
    screen = app.primaryScreen()
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "windows":
        image = screen.grabWindow(int(window.winId()))
    else:
        image = window.grab()
    assert not image.isNull() and image.save(str(target))


def _wait_for_local_reply(chat, app: QApplication) -> None:
    deadline = time.monotonic() + 8
    while chat.reply_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert chat.reply_thread is None, "offline local reply did not finish"


def test_native_qt_two_plan_confirmation_adds_both(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "two-plan-native"
    clock = SimulatedBusinessClock(root, "2026-10-11")
    pet = _acceptance_desktop_pet(
        root, configure_development_log(root / "logs/development"), clock,
    )
    try:
        pet.show()
        pet.open_chat_window()
        chat = pet.chat_window
        assert isinstance(chat, ChatWindow)
        client = FixedTwoPlanClient()
        chat.llm_client = client
        chat.conversation_service.llm_client = client
        chat.intent_router.configure_llm(LLMIntentParser(client.chat), True)
        chat.conversation_service.semantic_decision_compatibility_enabled = False

        chat.input_box.setText("我想安排两项今天学习任务")
        chat.send_button.click()
        _wait_for_local_reply(chat, app)
        pending_text = chat._last_visible_assistant_message
        assert "隔离桌面练习SQL" in pending_text
        assert "隔离桌面阅读英文文档" in pending_text
        assert pet.growth_service.tasks("2026-10-11") == []
        _screenshot(chat, "two-plan-confirmation.png")

        chat.input_box.setText("确认")
        chat.send_button.click()
        _wait_for_local_reply(chat, app)
        tasks = pet.growth_service.tasks("2026-10-11")
        assert [item["title"] for item in tasks] == [
            "隔离桌面练习SQL", "隔离桌面阅读英文文档",
        ]
        assert len({item["uid"] for item in tasks}) == 2
        assert "隔离桌面练习SQL" in chat._last_visible_assistant_message
        assert "隔离桌面阅读英文文档" in chat._last_visible_assistant_message
        _screenshot(chat, "two-plan-completed.png")
        (Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts" /
         "two-plan-native-evidence.json").write_text(json.dumps({
            "mode": "isolated_offline_windows_qt",
            "date": clock.today(),
            "session_id": chat.current_session_id,
            "pending_shows_both": True,
            "titles": [item["title"] for item in tasks],
            "uids": [item["uid"] for item in tasks],
            "completion_reply": chat._last_visible_assistant_message,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        pet.close()
        app.processEvents()


def test_three_days_restart_and_visible_history(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "simulation"
    clock = SimulatedBusinessClock(root, "2026-10-30")
    logger = configure_development_log(root / "logs" / "development")
    pet = _acceptance_desktop_pet(root, logger, clock)
    assert pet.config["test_mode"] is False
    assert pet.config["auto_complete_yesterday_review"] is True
    pet.open_chat_window()
    chat = pet.chat_window
    assert isinstance(chat, ChatWindow)
    assert chat.simulation_date_label.text() == "当前测试日期 2026-10-30"
    assert chat.business_entity_parser.parse("今天学习")["date"] == "2026-10-30"
    assert chat.business_entity_parser.parse("明天学习")["date"] == "2026-10-31"
    assert chat.growth_service is pet.growth_service
    assert chat.chat_history_manager is pet.chat_history_manager
    assert chat.memory_manager.memory_file.resolve() == (root / "memory.json").resolve()
    assert chat.llm_client.secret_store.path.resolve() == (root / "data/private/llm_secrets.json").resolve()
    assert chat.llm_client.usage_store.project_root.resolve() == root.resolve()
    assert chat.personality_file.resolve() == (root / "data/roxy_personality.json").resolve()
    assert chat.knowledge_dir.resolve() == (root / "data/knowledge").resolve()
    assert chat.legacy_config_file.resolve() == (root / "config.json").resolve()
    assert pet.review_backfill_runner.diagnostics_dir.resolve() == (root / "data/private/diagnostics").resolve()
    pet.open_settings_dialog()
    assert pet.settings_dialog.secret_store is chat.llm_client.secret_store
    assert pet.settings_dialog.usage_store is chat.llm_client.usage_store
    assert pet.settings_dialog.knowledge_dir.resolve() == chat.knowledge_dir.resolve()
    pet.settings_dialog.close()
    app.processEvents()
    chat.open_settings_dialog()
    assert chat.settings_dialog.secret_store is chat.llm_client.secret_store
    assert chat.settings_dialog.usage_store is chat.llm_client.usage_store
    assert chat.settings_dialog.knowledge_dir.resolve() == chat.knowledge_dir.resolve()
    chat.settings_dialog.close()
    app.processEvents()
    first_session = chat.current_session_id
    day_one = clock.today()
    _screenshot(chat, "simulation-date-control.png")

    growth = pet.growth_service
    task_one = growth.add_task("复习算法", duration_minutes=30)
    task_two = growth.add_task("整理学习笔记", duration_minutes=20)
    assert task_one["created_at"][:10] == datetime.now().date().isoformat()
    assert chat.chat_history_manager.get_session(first_session)["started_at"][:10] == datetime.now().date().isoformat()
    completed, changed = growth.complete_today_by_uid(task_one["uid"], day_one)
    assert changed and completed["uid"] == task_one["uid"]
    growth.add_record("做了算法练习", duration_minutes=25)
    review_one = growth.save_today_review()
    assert review_one["date"] == day_one
    assert review_one["saved_at"][:10] == datetime.now().date().isoformat()
    assert chat.chat_history_manager.add_message(first_session, "user", "第一天的学习记录")
    assert chat.chat_history_manager.save_summary(first_session, "主要讨论：算法练习与学习笔记")
    assert clock.today() == day_one
    pet.open_today_growth_dialog()
    panel = pet.today_growth_dialog
    assert panel._today() == day_one
    assert panel.learning_loop.now_provider().date().isoformat() == day_one
    assert len(panel.tasks) == 2
    assert panel.actions_metric.value_label.text() == "1 条"
    assert "25 分钟" in panel.duration_label.text()

    chat.input_box.setText("保留但尚未发送的文字")
    assert chat.advance_simulation_day()
    app.processEvents()
    second_session = chat.current_session_id
    assert second_session != first_session
    assert clock.today() == "2026-10-31"
    assert chat.session_title_label.text() == "魔法修行日记 · 2026-10-31"
    assert chat.business_entity_parser.parse("今天学习")["date"] == "2026-10-31"
    assert chat.business_entity_parser.parse("明天学习")["date"] == "2026-11-01"
    assert chat.input_box.text() == "保留但尚未发送的文字"
    assert growth.tasks() == []
    assert growth.tasks(day_one)[1]["uid"] == task_two["uid"]
    assert growth.tasks(day_one)[1]["done"] is False
    assert growth.records_for_date(clock.today()) == []
    assert panel._today() == clock.today()
    assert panel.tasks == []
    assert panel.recent_metric.value_label.text() == "50%"
    assert any(item["date"] == day_one for item in growth.entries())
    evidence = LearningLoopService(growth, now_provider=clock.now).planning_evidence()
    assert evidence["recent_total"] == 2
    assert evidence["recent_completed"] == 1
    assert any(item["uid"] == task_two["uid"] for item in evidence["overdue_tasks"])
    context = chat.conversation_service.build_llm_messages("算法练习", second_session)
    assert "source_business_date=2026-10-30; relation_to_current_date=yesterday" in str(context)
    history_result = chat.tool_registry.get("show_conversation_history").handler(
        current_conversation_id=second_session, exclude_today=True,
    )
    assert history_result.success
    assert any(item["session_id"] == first_session and item["source_date"] == day_one
               for item in history_result.data["sessions"])
    _screenshot(chat, "simulation-next-day.png")

    assert chat.switch_chat_session(first_session)
    app.processEvents()
    assert chat._viewing_history and not chat.input_box.isEnabled()
    assert not chat.send_button.isEnabled()
    assert chat.return_today_button.isVisible()
    assert "历史对话 · 只读" in chat.session_title_label.text()
    before_history = chat.chat_history_manager.get_session(first_session)
    chat.send_message()
    assert chat.chat_history_manager.get_session(first_session) == before_history
    _screenshot(chat, "simulation-history-readonly.png")
    chat.return_today_button.click()
    assert chat.current_session_id == second_session

    assert chat.advance_simulation_day()
    third_session = chat.current_session_id
    assert clock.today() == "2026-11-01"
    assert len({first_session, second_session, third_session}) == 3
    assert chat.chat_history_manager.get_session(first_session)["messages"] == before_history["messages"]
    assert chat.chat_history_manager.get_session(second_session) is not None
    assert growth.entries()[0]["date"] == day_one
    assert LearningLoopService(growth, now_provider=clock.now).recent_plan_completion() == {
        "recent_total": 2, "recent_completed": 1,
    }
    evidence_file = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts" / "three-day-evidence.json"
    evidence_file.write_text(json.dumps({
        "mode": "isolated_offline_synthetic",
        "days": [
            {"date": day_one, "session_id": first_session, "plans": 2,
             "completed": 1, "actions": 1, "review_saved": True},
            {"date": "2026-10-31", "session_id": second_session, "plans": 0,
             "completed": 0, "actions": 0, "review_saved": False},
            {"date": "2026-11-01", "session_id": third_session, "plans": 0,
             "completed": 0, "actions": 0, "review_saved": False},
        ],
        "day_two_recent_plan_completion": {"completed": 1, "total": 2},
        "day_one_pending_task_stayed_on_day_one": task_two["uid"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    chat.close()
    panel.close()
    pet.close()
    app.processEvents()
    reopened = SimulatedBusinessClock(root)
    assert reopened.today() == "2026-11-01"
    new_growth = GrowthManager(root / "data/private", now_provider=reopened.now,
                               timestamp_provider=datetime.now)
    new_history = ChatHistoryManager(root / "data/private")
    new_chat = ChatWindow(
        growth_service=new_growth,
        chat_history_manager=new_history,
        memory_service=MemoryService.from_data_root(
            root, memory_file=root / "memory.json", private_dir=root / "data/private"
        ),
        llm_client=OfflineClient(),
        settings_config_file=root / "data/pet_config.json",
        personality_file=root / "data/roxy_personality.json",
        knowledge_dir=root / "data/knowledge",
        legacy_config_file=root / "config.json",
        simulation_clock=reopened,
    )
    try:
        assert new_chat.current_session_id == third_session
        assert new_growth.tasks(day_one)[0]["uid"] == task_one["uid"]
        assert new_growth.entries()[0]["review"] == review_one["review"]
        assert new_history.get_session(first_session)["messages"] == before_history["messages"]
    finally:
        new_chat.close()


def test_advance_refuses_active_request_and_invalidates_old_state(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "simulation"
    clock = SimulatedBusinessClock(root, "2026-10-10")
    growth = GrowthManager(root / "data/private", now_provider=clock.now,
                           timestamp_provider=datetime.now)
    history = ChatHistoryManager(root / "data/private")
    chat = ChatWindow(
        growth_service=growth,
        chat_history_manager=history,
        memory_service=MemoryService.from_data_root(
            root, memory_file=root / "memory.json", private_dir=root / "data/private"
        ),
        llm_client=OfflineClient(), settings_config_file=root / "data/pet_config.json",
        personality_file=root / "data/roxy_personality.json",
        knowledge_dir=root / "data/knowledge", legacy_config_file=root / "config.json",
        simulation_clock=clock,
    )
    try:
        first_session = chat.current_session_id
        class Running:
            def isRunning(self):
                return True
        chat.reply_thread = Running()
        assert not chat.advance_simulation_day()
        assert clock.today() == "2026-10-10"
        chat.reply_thread = None
        chat._pending_plan_delete_id = 12
        chat._pending_memory_delete_id = 7
        chat.conversation_service.state_manager.observe_user(first_session, "我在图书馆学习")
        chat.confirmation_manager.create("delete_plan", {"task_uid": "old"}, "旧确认", scope=first_session)
        chat.interaction_coordinator.awaiting_choice(
            first_session, "plan_suggestion", options=[{"id": "old", "title": "旧建议"}],
        )
        snapshot = SuggestionSnapshot.for_options(
            conversation_id=first_session,
            options=[{"id": "old", "title": "旧建议"}],
            target_date=clock.today(),
        )
        chat.interaction_coordinator.remember_suggestion_snapshot(first_session, snapshot)
        assert chat.interaction_coordinator.current(first_session).last_suggestion_snapshot
        assert chat.advance_simulation_day()
        assert chat._pending_plan_delete_id is None
        assert chat._pending_memory_delete_id is None
        assert chat.current_session_id != first_session
        assert chat.conversation_service.state_manager.current_facts(first_session) == {}
        assert chat.confirmation_manager.pending(scope=first_session) is None
        assert chat.interaction_coordinator.current(first_session).state == "idle"
        assert chat.interaction_coordinator.current(first_session).last_suggestion_snapshot == {}
    finally:
        chat.reply_thread = None
        chat.close()
        app.processEvents()


def test_simulation_root_and_formal_mode_guards(tmp_path, monkeypatch):
    root = tmp_path / "simulation"
    with pytest.raises(ValueError, match="First simulation launch"):
        SimulatedBusinessClock(root)
    root.mkdir()
    (root / "unrelated.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ValueError, match="empty or already marked"):
        SimulatedBusinessClock(root, "2026-10-10")
    assert (root / "unrelated.txt").read_text(encoding="utf-8") == "do not overwrite"
    with pytest.raises(ValueError, match="outside|separate"):
        _acceptance_root_from_argv(["--acceptance-root", str(Path(__file__).resolve().parents[1])])
    clean = tmp_path / "clean"
    clock = SimulatedBusinessClock(clean, "2026-12-31")
    assert clock.advance_one_day() == "2027-01-01"
    assert SimulatedBusinessClock(clean).today() == "2027-01-01"
    with pytest.raises(ValueError, match="differs"):
        SimulatedBusinessClock(clean, "2026-12-31")
    normal = GrowthManager(tmp_path / "normal-private")
    assert normal._today() == datetime.now().date().isoformat()
    assert normal.now_provider.__self__ is datetime


def test_startup_backfill_uses_simulated_yesterday_only_on_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "simulation"
    clock = SimulatedBusinessClock(root, "2026-10-30")
    growth = GrowthManager(root / "data/private", now_provider=clock.now,
                           timestamp_provider=datetime.now)
    growth.add_task("第一天未复盘的计划")
    assert clock.advance_one_day() == "2026-10-31"
    pet = _acceptance_desktop_pet(
        root, configure_development_log(root / "logs/development"), clock,
    )
    try:
        pet.show()
        app.processEvents()
        assert pet.review_backfill_runner.now_provider().date().isoformat() == "2026-10-31"
        assert [item["date"] for item in pet.growth_service.entries()] == ["2026-10-30"]
        pet.growth_service.add_task("第二天尚未复盘的计划")
        assert clock.advance_one_day() == "2026-11-01"
        app.processEvents()
        assert [item["date"] for item in pet.growth_service.entries()] == ["2026-10-30"]
    finally:
        pet.close()
        app.processEvents()


def test_simulated_day_ordinal_completion_and_open_panel_rollover(
    tmp_path, monkeypatch,
):
    """Exercise the local chat route and an already open native Qt panel."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "simulation"
    clock = SimulatedBusinessClock(root, "2026-09-30")
    pet = _acceptance_desktop_pet(
        root, configure_development_log(root / "logs/development"), clock,
    )
    try:
        pet.show()
        pet.open_chat_window()
        chat = pet.chat_window
        first_session = chat.current_session_id
        growth = pet.growth_service
        first = growth.add_task("练习 SQL 窗口函数", duration_minutes=35)
        second = growth.add_task("读一篇英文技术文档", duration_minutes=25)

        chat.input_box.setText("我今天安排了什么？")
        chat.send_button.click()
        _wait_for_local_reply(chat, app)
        assert "1." in chat._last_visible_assistant_message
        assert "2." in chat._last_visible_assistant_message

        chat.input_box.setText("第二项我做完了，其余先不动。")
        chat.send_button.click()
        _wait_for_local_reply(chat, app)
        actual = {item["uid"]: item for item in growth.tasks("2026-09-30")}
        assert actual[first["uid"]]["done"] is False
        assert actual[second["uid"]]["done"] is True
        assert "读一篇英文技术文档" in chat._last_visible_assistant_message

        growth.add_record("整理学习笔记", duration_minutes=20)
        saved = growth.save_today_review()
        pet.open_settings_dialog()
        pet.settings_dialog.open_today_growth_button.click()
        app.processEvents()
        panel = pet.today_growth_dialog
        assert panel.isVisible() and panel.refresh_timer.isActive()
        assert panel.growth_manager is growth
        assert panel._display_date == "2026-09-30"
        assert panel.count_label.text().endswith("1/2")
        assert panel.actions_metric.value_label.text() == "1 条"
        assert "20 分钟" in panel.duration_label.text()
        assert panel.summary_text.toPlainText() == saved["review"]["panel_summary"]
        assert panel.summary_status_label.text() == "已保存复盘"
        _screenshot(panel, "simulated-completion-panel.png")

        pet.settings_dialog.close()
        chat.raise_()
        app.processEvents()
        assert panel.isVisible()
        chat.advance_day_button.click()
        app.processEvents()
        assert clock.today() == "2026-10-01"
        assert panel is pet.today_growth_dialog and panel.isVisible()
        assert panel.refresh_timer.isActive()
        assert panel._display_date == "2026-10-01"
        assert panel.tasks == [] and panel.actions == []
        assert panel.count_label.text().endswith("0/0")
        assert panel.actions_metric.value_label.text() == "0 条"
        assert panel.summary_status_label.text() == "尚未保存"
        assert "今天还没有保存复盘" in panel.summary_text.toPlainText()
        recent = panel.learning_loop.recent_plan_completion(
            "2026-10-01", strict_read=True,
        )
        assert recent == {"recent_total": 2, "recent_completed": 1}
        assert panel.recent_metric.value_label.text() == "50%"
        assert "不含今天" in panel.recent_metric.toolTip()
        assert growth.tasks("2026-09-30")[1]["uid"] == second["uid"]
        assert growth.entries()[0]["review"]["panel_summary"] == saved["review"]["panel_summary"]
        QTest.qWait(150)
        _screenshot(panel, "simulated-next-day-open-panel.png")

        evidence = {
            "mode": "isolated_offline_windows_qt",
            "listed_date": "2026-09-30",
            "first_uid": first["uid"],
            "second_uid": second["uid"],
            "completed_uid": second["uid"],
            "saved_panel_summary": saved["review"]["panel_summary"],
            "panel_summary_before_advance": saved["review"]["panel_summary"],
            "panel_date_after_advance": panel._display_date,
            "panel_plans_after_advance": len(panel.tasks),
            "panel_actions_after_advance": len(panel.actions),
            "panel_summary_after_advance": panel.summary_text.toPlainText(),
            "recent_prior_seven_days": recent,
            "recent_metric_text": panel.recent_metric.value_label.text(),
        }
        artifact = Path(os.environ["ROXYPLAN_TEST_WORKSPACE"]) / "artifacts" / "simulated-panel-evidence.json"
        artifact.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

        chat.open_chat_history_dialog()
        app.processEvents()
        assert chat.switch_chat_session(first_session)
        assert chat._viewing_history and not chat.send_button.isEnabled()
        _screenshot(chat, "simulated-prior-day-readonly.png")
    finally:
        pet.close()
        app.processEvents()
