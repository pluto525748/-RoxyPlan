"""UI/source correlation uses synthetic data; never starts the desktop app."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from frontend import growth_dialog, memory_dialog
from frontend.growth_dialog import GrowthDialog
from frontend.memory_dialog import MemoryDialog
from modules.growth_manager import GrowthManager
from modules.development_log import DevelopmentLog
from modules.memory_service import MemoryOperationResult
from modules import review_backfill
from modules.review_backfill import ReviewBackfillRunner


@dataclass(frozen=True)
class FakeTrace:
    trace_id: str
    source: str
    session_id: str = ""


class FakeDevelopmentLog:
    def __init__(self):
        self.events = []
        self.current = None

    def new_trace(self, session_id="", source="chat"):
        return FakeTrace("trace_" + uuid4().hex, source, session_id)

    def event(self, trace, event_name, **fields):
        self.events.append({
            "trace_id": trace.trace_id, "source": trace.source,
            "session_id": trace.session_id, "event": event_name, **fields,
        })

    @contextmanager
    def bind(self, trace):
        previous = self.current
        self.current = trace
        try:
            yield trace
        finally:
            self.current = previous

    def record_exception(self, trace, error):
        self.event(trace, "exception", error_type=type(error).__name__)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def evidence(monkeypatch):
    logger = FakeDevelopmentLog()
    for module in (growth_dialog, memory_dialog, review_backfill):
        monkeypatch.setattr(module, "get_development_log", lambda: logger)
    return logger


@pytest.fixture
def growth_panel(tmp_path, qt_app):
    manager = GrowthManager(
        tmp_path / "growth", now_provider=lambda: datetime(2026, 9, 18, 10, 0, 0)
    )
    return manager, GrowthDialog(manager)


@pytest.fixture
def memory_panel(isolated_memory_service, qt_app):
    return isolated_memory_service, MemoryDialog(memory_service=isolated_memory_service)


def finished(logger, event="ui_operation_finished"):
    return [row for row in logger.events if row["event"] == event]


def seed_memory(service):
    result = service.memory_manager.add_memory(
        "合成私密内容，不应出现在日志", category="preference"
    )
    return result["memory"]


def test_growth_save_logs_actual_returned_snapshot_and_independent_ui_trace(
    growth_panel, evidence, monkeypatch
):
    manager, panel = growth_panel
    manager.add_task("合成计划")
    manager.add_record("合成行动")
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    chat = evidence.new_trace(session_id="session_" + uuid4().hex)

    with evidence.bind(chat):
        panel.save_review()
        panel.save_review()
        assert evidence.current is chat

    events = finished(evidence)
    entry = manager.entries()[0]
    assert len(events) == 2
    assert events[0]["trace_id"] != events[1]["trace_id"] != chat.trace_id
    assert all(row["session_id"] == "" for row in events)
    assert all(row["source"] == "ui_growth_save_review" for row in events)
    assert [row["revision"] for row in events] == [1, 2]
    assert all(row["success"] is True and row["persisted"] is True for row in events)
    assert events[-1]["changed_resource_ids"] == [entry["uid"]]
    assert {key: events[-1][key] for key in ("date", "total", "done", "pending", "action_count")} == {
        "date": "2026-09-18", "total": 1, "done": 0, "pending": 1, "action_count": 1,
    }
    assert "合成计划" not in json.dumps(evidence.events, ensure_ascii=False)
    assert "合成行动" not in json.dumps(evidence.events, ensure_ascii=False)


def test_growth_save_error_keeps_original_exception_and_does_not_show_success(
    growth_panel, evidence, monkeypatch
):
    manager, panel = growth_panel
    notices = []
    error = OSError("private path/key must not enter evidence")
    def fail():
        raise error
    monkeypatch.setattr(manager, "save_today_review", fail)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: notices.append(True))
    with pytest.raises(OSError) as caught:
        panel.save_review()
    assert caught.value is error
    assert notices == [] and manager.entries() == []
    assert finished(evidence)[-1]["status"] == "failed"
    assert finished(evidence)[-1]["success"] is False
    assert finished(evidence)[-1]["persisted"] is False
    assert finished(evidence)[-1]["error_type"] == "OSError"
    assert "private path" not in json.dumps(evidence.events)


def test_plan_buttons_log_returned_uid_and_do_not_claim_missing_delete_success(
    growth_panel, evidence
):
    manager, panel = growth_panel
    panel.plan_input.setText("合成计划标题")
    panel.add_plan()
    task = manager.tasks()[0]
    panel.delete_plan(int(task["id"]))
    panel.delete_plan(int(task["id"]))
    events = finished(evidence)
    assert [row["source"] for row in events] == ["ui_plan_add", "ui_plan_delete", "ui_plan_delete"]
    assert [row["status"] for row in events] == ["success", "success", "not_found"]
    assert events[0]["changed_resource_ids"] == events[1]["changed_resource_ids"] == [task["uid"]]
    assert "合成计划标题" not in json.dumps(evidence.events, ensure_ascii=False)


def test_completed_plan_noop_and_duplicate_action_are_logged_unchanged(
    growth_panel, evidence
):
    manager, panel = growth_panel
    task = manager.add_task("合成完成计划")
    for _ in range(2):
        panel._run_logged_write("ui_plan_complete", lambda: manager.complete_by_id(int(task["id"])))
    for _ in range(2):
        panel.action_input.setText("重复的合成行动")
        panel.add_action()
    assert [row["status"] for row in finished(evidence)] == ["success", "unchanged", "success", "unchanged"]
    assert finished(evidence)[1]["changed_resource_ids"] == []
    assert finished(evidence)[3]["changed_resource_ids"] == []
    assert finished(evidence)[1]["persisted"] is False
    assert finished(evidence)[3]["persisted"] is False
    assert len(manager.records_for_date()) == 1


def test_delete_memory_success_projects_no_result_data_or_private_summary(
    memory_panel, evidence, monkeypatch
):
    service, panel = memory_panel
    memory = seed_memory(service)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    panel._delete(memory["id"])
    row = finished(evidence)[-1]
    assert row["status"] == "success" and row["source"] == "ui_memory_delete"
    assert row["session_id"] == "" and row["memory_id"] == memory["id"]
    assert not service.get_memory(memory["id"]).success
    assert "合成私密内容" not in json.dumps(evidence.events, ensure_ascii=False)


def test_delete_memory_cancel_records_cancel_without_running_service(
    memory_panel, evidence, monkeypatch
):
    service, panel = memory_panel
    memory = seed_memory(service)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    def forbidden(*_args):
        pytest.fail("cancelled delete called the business service")
    monkeypatch.setattr(service, "delete_memory", forbidden)
    panel._delete(memory["id"])
    assert evidence.events[-1]["event"] == "ui_operation_cancelled"
    assert evidence.events[-1]["status"] == "cancelled"
    assert finished(evidence) == [] and service.get_memory(memory["id"]).success


def test_failed_memory_result_is_not_promoted_to_success_or_dumped(
    memory_panel, evidence, monkeypatch
):
    service, panel = memory_panel
    result = MemoryOperationResult(
        False, "failed", "delete_memory", memory_id=5,
        error_code="memory_delete_failed", content_summary="private summary",
        safe_message="删除没有成功", data={"memory": {"content": "private payload"}},
    )
    monkeypatch.setattr(service, "delete_memory", lambda _value: result)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    panel._delete(5)
    assert finished(evidence)[-1]["status"] == "failed"
    assert finished(evidence)[-1]["reason_code"] == "memory_delete_failed"
    assert panel.status_label.text() == "删除没有成功"
    assert "private" not in json.dumps(evidence.events)


def test_memory_exception_is_logged_and_not_swallowed(memory_panel, evidence, monkeypatch):
    service, panel = memory_panel
    error = RuntimeError("private runtime detail")
    def fail(_value):
        raise error
    monkeypatch.setattr(service, "archive_memory", fail)
    with pytest.raises(RuntimeError) as caught:
        panel._archive(8)
    assert caught.value is error and finished(evidence)[-1]["status"] == "failed"
    assert "private runtime" not in json.dumps(evidence.events)


@pytest.mark.parametrize("cancel_stage", ["content", "importance"])
def test_edit_cancellation_has_no_write(memory_panel, evidence, monkeypatch, cancel_stage):
    service, panel = memory_panel
    memory = seed_memory(service)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *_args: ("合成修改", cancel_stage != "content"))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *_args: (4, False))
    def forbidden(*_args, **_kwargs):
        pytest.fail("cancelled edit called the business service")
    monkeypatch.setattr(service, "update_memory", forbidden)
    panel._edit(memory["id"])
    assert evidence.events[-1]["event"] == "ui_operation_cancelled"
    assert finished(evidence) == []
    assert service.get_memory(memory["id"]).data["memory"]["content"] == memory["content"]


def test_memory_edit_archive_restore_use_separate_operation_traces(
    memory_panel, evidence, monkeypatch
):
    service, panel = memory_panel
    memory = seed_memory(service)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *_args: ("合成修改", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *_args: (4, True))
    panel._edit(memory["id"])
    panel._archive(memory["id"])
    panel._restore(memory["id"])
    rows = finished(evidence)
    assert [row["source"] for row in rows] == ["ui_memory_update", "ui_memory_archive", "ui_memory_restore"]
    assert len({row["trace_id"] for row in rows}) == 3
    assert all(row["status"] == "success" for row in rows)
    assert service.get_memory(memory["id"]).data["memory"]["content"] == "合成修改"
    assert "合成修改" not in json.dumps(evidence.events, ensure_ascii=False)


def test_already_archived_memory_retains_noop_result_in_developer_evidence(
    memory_panel, evidence
):
    service, panel = memory_panel
    memory = seed_memory(service)
    panel._archive(memory["id"])
    panel._archive(memory["id"])
    row = finished(evidence)[-1]
    assert row["status"] == "already_processed"
    assert row["success"] is True and row["changed"] is False


@pytest.mark.parametrize("action,source", [
    ("_accept", "ui_memory_accept_candidate"),
    ("_reject", "ui_memory_reject_candidate"),
    ("_resolve_conflict", "ui_memory_resolve_conflict"),
])
def test_maintenance_write_buttons_are_correlated_without_private_result(
    memory_panel, evidence, monkeypatch, action, source
):
    service, panel = memory_panel
    operation = {"_accept": "accept_candidate", "_reject": "reject_candidate", "_resolve_conflict": "resolve_conflict"}[action]
    calls = []
    def perform(*args, **kwargs):
        calls.append((args, kwargs))
        return MemoryOperationResult(True, "success", operation, data={"private": "synthetic private content"})
    monkeypatch.setattr(service, operation, perform)
    if action == "_resolve_conflict":
        panel._resolve_conflict(9, "keep_old")
    else:
        getattr(panel, action)(9)
    assert calls and finished(evidence)[-1]["source"] == source
    assert finished(evidence)[-1]["status"] == "success"
    assert "synthetic private" not in json.dumps(evidence.events)


def test_startup_backfill_has_its_own_trace_and_real_result_counts(tmp_path, evidence):
    clock = lambda: datetime(2026, 9, 18, 10, 0, 0)
    manager = GrowthManager(tmp_path / "growth", now_provider=clock)
    manager.add_task("昨天合成计划", date="2026-09-17")
    runner = ReviewBackfillRunner(manager, now_provider=clock, diagnostics_dir=tmp_path / "diagnostics")
    chat = evidence.new_trace(session_id="session_" + uuid4().hex)
    with evidence.bind(chat):
        created = runner.run()
        existing = runner.run()
        assert evidence.current is chat
    rows = finished(evidence, "startup_backfill_finished")
    assert [row["status"] for row in rows] == ["created", "existing"]
    assert all(row["source"] == "startup_backfill" and row["session_id"] == "" for row in rows)
    assert rows[0]["trace_id"] != rows[1]["trace_id"] != chat.trace_id
    assert rows[0]["changed_resource_ids"] == [created["entry"]["uid"]]
    assert rows[1]["changed_resource_ids"] == []
    assert rows[0]["success"] is True and rows[0]["persisted"] is True
    assert rows[1]["success"] is True and rows[1]["persisted"] is False
    assert rows[0]["total"] == 1 and rows[0]["revision"] == existing["entry"]["revision"] == 1


def test_backfill_failure_preserves_existing_failure_handling_and_privacy(tmp_path, evidence):
    class FailingGrowth:
        def ensure_review_for_date(self, _date):
            raise OSError("private failing path")
    runner = ReviewBackfillRunner(
        FailingGrowth(), now_provider=lambda: datetime(2026, 9, 18, 10, 0, 0),
        diagnostics_dir=tmp_path / "diagnostics",
    )
    result = runner.run()
    row = finished(evidence, "startup_backfill_finished")[-1]
    assert result["status"] == row["status"] == "failed"
    assert row["success"] is False and row["persisted"] is False
    assert row["error_type"] == "OSError" and result["notified"] is False
    assert "private failing" not in json.dumps(evidence.events)
    assert (tmp_path / "diagnostics" / "review_backfill.jsonl").is_file()


def test_empty_day_backfill_logs_empty_and_creates_no_growth_entry(tmp_path, evidence):
    clock = lambda: datetime(2026, 9, 18, 10, 0, 0)
    manager = GrowthManager(tmp_path / "growth", now_provider=clock)
    result = ReviewBackfillRunner(manager, now_provider=clock, diagnostics_dir=tmp_path / "diagnostics").run()
    assert result["status"] == finished(evidence, "startup_backfill_finished")[-1]["status"] == "empty"
    assert manager.entries() == []


def test_real_logger_preserves_button_and_backfill_evidence_without_bodies(
    tmp_path, growth_panel, memory_panel, monkeypatch
):
    manager, growth = growth_panel
    service, memory = memory_panel
    logger = DevelopmentLog(tmp_path / "developer_logs", enabled=True)
    for module in (growth_dialog, memory_dialog, review_backfill):
        monkeypatch.setattr(module, "get_development_log", lambda: logger)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    stored = seed_memory(service)
    manager.add_task("不应复制的合成标题")
    manager.add_task("昨天合成计划", date="2026-09-17")
    chat = logger.new_trace("session_" + uuid4().hex)
    with logger.bind(chat):
        growth.save_review()
        memory._delete(stored["id"])
        ReviewBackfillRunner(
            manager, now_provider=lambda: datetime(2026, 9, 18, 10, 0, 0),
            diagnostics_dir=tmp_path / "backfill_diagnostics",
        ).run()
        assert logger.current_trace() is chat
    records = [
        json.loads(line)
        for path in (tmp_path / "developer_logs").rglob("events*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    ends = [row for row in records if row["event"] in {"ui_operation_finished", "startup_backfill_finished"}]
    assert [row["source"] for row in ends] == ["ui_growth_save_review", "ui_memory_delete", "startup_backfill"]
    assert all(row["session_id"] == "" and row["trace_id"] != chat.trace_id for row in ends)
    assert ends[0]["fields"]["record_id"] == "review_2026-09-18"
    assert ends[0]["fields"]["revision"] == 1 and ends[0]["fields"]["total"] == 1
    assert ends[1]["fields"]["memory_id"] == stored["id"]
    assert ends[2]["fields"]["status"] == "created"
    assert ends[2]["fields"]["changed_resource_ids"] == ["review_2026-09-17"]
    raw = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "developer_logs").rglob("*.log"))
    assert "合成" not in json.dumps(records, ensure_ascii=False) and "合成" not in raw


def test_disabled_default_style_logger_never_creates_directory_or_blocks_write(
    tmp_path, growth_panel, monkeypatch
):
    manager, panel = growth_panel
    destination = tmp_path / "disabled_evidence"
    logger = DevelopmentLog(destination)
    monkeypatch.setattr(growth_dialog, "get_development_log", lambda: logger)
    panel.action_input.setText("隔离行动")
    panel.add_action()
    assert len(manager.records_for_date()) == 1
    assert not destination.exists()
