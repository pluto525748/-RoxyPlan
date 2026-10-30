"""Isolated service and offscreen UI checks for the Today Growth panel."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from frontend.today_growth_dialog import TodayGrowthDialog, VISIBLE_REFRESH_MS
from modules.growth_manager import GrowthManager
from modules.learning_loop import LearningLoopService
from modules.tool_registry import create_roxy_tool_registry
from tests.isolation_support import build_isolated_memory_manager
from v18_test_support import NoCallLLM, RecordingLLM, build_service


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _window(manager: GrowthManager, clock: Clock) -> TodayGrowthDialog:
    QApplication.instance() or QApplication([])
    window = TodayGrowthDialog(
        growth_manager=manager,
        learning_loop=LearningLoopService(manager, now_provider=clock),
    )
    window.show()
    QApplication.processEvents()
    return window


def test_service_read_completion_restart_tool_and_render(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 7, 9, 30))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    first = manager.add_task("检查成长面板", duration_minutes=35, note="核对今日计划")
    manager.add_task("无时长计划")
    manager.add_record("已写一条独立行动")
    window = _window(manager, clock)
    reopened = None
    try:
        facts = LearningLoopService(manager, now_provider=clock).day_snapshot()
        assert window._display_date == facts["date"]
        assert window.count_label.text().endswith("0/2")
        assert window.today_metric.value_label.text() == "0%"
        assert window.pending_metric.value_label.text() == "2 项"
        assert "35 分钟" in window.duration_label.text()
        assert "时长未记录 · 0/1 条有时长" in window.duration_label.text()
        assert window.actions_metric.value_label.text() == "1 条"
        assert window.tasks[0]["description"] == "核对今日计划"
        durations = [label.text() for label in window.plan_panel.findChildren(QLabel, "taskDuration")]
        assert "时长未记录" in durations

        window.tasks[0]["_card"].check_box.click()
        QApplication.processEvents()
        assert window.count_label.text().endswith("1/2")
        assert window.progress_bar.value() == 50
        assert window.today_metric.value_label.text() == "50%"
        assert window.pending_metric.value_label.text() == "1 项"
        assert len(manager.records_for_date()) == 1

        reopened_manager = GrowthManager(private, now_provider=clock)
        reopened = _window(reopened_manager, clock)
        same = next(item for item in reopened.tasks if item["uid"] == first["uid"])
        assert same["done"] is True
        assert same["_card"].check_box.isChecked()
        assert not same["_card"].check_box.isEnabled()
        memory = build_isolated_memory_manager(tmp_path)
        chat_tools = create_roxy_tool_registry(reopened_manager, memory)
        query = chat_tools.get("show_plan").handler(date="2026-10-07")
        assert query.success
        assert next(item for item in query.data["tasks"] if item["uid"] == first["uid"])["done"]

        run_root = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
        if run_root:
            image = Path(run_root) / "artifacts" / "today-growth-isolated-service.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            assert reopened.grab().save(str(image))
            reopened.resize(352, 381)
            QApplication.processEvents()
            compact = image.with_name("today-growth-isolated-service-compact.png")
            assert reopened.grab().save(str(compact))
    finally:
        window.close()
        if reopened is not None:
            reopened.close()


def test_visible_timer_tracks_external_writes_and_stops_when_hidden(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 7, 10, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    other = GrowthManager(private, now_provider=clock)
    window = _window(manager, clock)
    try:
        assert window.refresh_timer.interval() == VISIBLE_REFRESH_MS == 2000
        assert window.refresh_timer.isActive()
        window.refresh_timer.setInterval(30)
        added = other.add_task("从其他入口添加")
        QTest.qWait(100)
        assert [task["uid"] for task in window.tasks] == [added["uid"]]
        other.complete_by_id(int(added["id"]))
        QTest.qWait(100)
        assert window.tasks[0]["done"] is True
        other.delete_by_id(int(added["id"]))
        QTest.qWait(100)
        assert window.tasks == []

        window.hide()
        assert not window.refresh_timer.isActive()
        later = other.add_task("隐藏时添加")
        QTest.qWait(100)
        assert window.tasks == []
        window.show()
        QApplication.processEvents()
        assert [task["uid"] for task in window.tasks] == [later["uid"]]
    finally:
        window.close()
        assert not window.refresh_timer.isActive()


def test_deleted_uid_never_completes_reused_numeric_id(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 7, 11, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    other = GrowthManager(private, now_provider=clock)
    old = manager.add_task("旧计划")
    window = _window(manager, clock)
    try:
        old_checkbox = window.tasks[0]["_card"].check_box
        other.delete_by_id(int(old["id"]))
        replacement = other.add_task("新计划")
        assert replacement["id"] == old["id"]
        old_checkbox.click()
        QApplication.processEvents()
        assert other.tasks()[0]["uid"] == replacement["uid"]
        assert other.tasks()[0]["done"] is False
        assert [task["uid"] for task in window.tasks] == [replacement["uid"]]
        assert window.notice_label.isVisible()
    finally:
        window.close()


def test_repeat_cross_day_save_failure_and_read_only_refresh(tmp_path: Path, monkeypatch) -> None:
    clock = Clock(datetime(2026, 10, 7, 12, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    first = manager.add_task("只完成一次")
    stale = manager.add_task("跨天待办")
    window = _window(manager, clock)
    try:
        window.tasks[0]["_card"].check_box.click()
        QApplication.processEvents()
        before = (private / "today_plan.json").read_bytes()
        finished_box = window.tasks[0]["_card"].check_box
        assert not finished_box.isEnabled()
        finished_box.click()
        QApplication.processEvents()
        assert (private / "today_plan.json").read_bytes() == before

        def no_save(*_args, **_kwargs):
            raise AssertionError("refresh must not save")

        with monkeypatch.context() as patch:
            patch.setattr(manager.repository, "save_plan", no_save)
            refresh = window.findChild(QPushButton, "refreshButton")
            assert refresh is not None and refresh.isEnabled()
            refresh.click()
            QApplication.processEvents()
        assert (private / "today_plan.json").read_bytes() == before

        stale_box = next(task for task in window.tasks
                         if task["uid"] == stale["uid"])["_card"].check_box
        clock.value = datetime(2026, 10, 8, 0, 1)
        second = manager.add_task("第二天计划")
        assert second["id"] == first["id"]
        stale_box.click()
        QApplication.processEvents()
        assert window._display_date == "2026-10-08"
        assert [task["uid"] for task in window.tasks] == [second["uid"]]
        assert next(task for task in manager.tasks("2026-10-07")
                    if task["uid"] == stale["uid"])["done"] is False
        assert manager.complete_today_by_uid(first["uid"], "2026-10-07") == (None, False)
        assert manager.tasks("2026-10-08")[0]["done"] is False

        with monkeypatch.context() as patch:
            patch.setattr(manager.repository, "save_plan", lambda _data: False)
            window.tasks[0]["_card"].check_box.click()
            QApplication.processEvents()
            assert window.tasks[0]["done"] is False
            assert not window.tasks[0]["_card"].check_box.isChecked()
            assert window.notice_label.isVisible()
        assert manager.tasks("2026-10-08")[0]["done"] is False
    finally:
        window.close()


def test_positive_save_result_without_persistence_is_not_success(tmp_path: Path,
                                                                 monkeypatch) -> None:
    clock = Clock(datetime(2026, 10, 7, 13, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    manager.add_task("必须回读核验")
    window = _window(manager, clock)
    try:
        monkeypatch.setattr(manager.repository, "save_plan", lambda _data: True)
        window.tasks[0]["_card"].check_box.click()
        QApplication.processEvents()
        assert window.tasks[0]["done"] is False
        assert not window.tasks[0]["_card"].check_box.isChecked()
        assert window.progress_bar.value() == 0
        assert window.notice_label.isVisible()
    finally:
        window.close()


def test_changed_task_list_keeps_scroll_position(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 7, 14, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    for index in range(8):
        manager.add_task(f"计划 {index + 1}")
    window = _window(manager, clock)
    try:
        QTest.qWait(30)
        bar = window.task_scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(min(70, bar.maximum()))
        before = bar.value()
        GrowthManager(private, now_provider=clock).add_task("新计划")
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert abs(bar.value() - before) <= 2
    finally:
        window.close()


def test_legacy_task_without_persisted_uid_cannot_be_completed(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 7, 15, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    plan_file = private / "today_plan.json"
    plan_file.write_text(json.dumps({
        "version": 1,
        "days": {"2026-10-07": {"tasks": [
            {"id": 1, "title": "旧计划", "date": "2026-10-07", "done": False}
        ]}},
    }, ensure_ascii=False), encoding="utf-8")
    before = plan_file.read_bytes()
    window = _window(manager, clock)
    try:
        assert len(window.tasks) == 1
        assert window.tasks[0]["persisted_uid"] is False
        checkbox = window.tasks[0]["_card"].check_box
        assert not checkbox.isEnabled()
        assert "稳定标识" in checkbox.toolTip()
        checkbox.click()
        QApplication.processEvents()
        assert plan_file.read_bytes() == before
        assert manager.tasks("2026-10-07")[0]["done"] is False
    finally:
        window.close()


def test_service_actions_share_snapshot_and_completion_does_not_add_action(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 10, 15))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    manager.add_task("核对行动展示", duration_minutes=30)
    first = manager.add_record("阅读了项目文档", duration_minutes=20)
    second = manager.add_record("整理了笔记", duration_minutes=25)
    day = clock.value.date().isoformat()
    action_file = private / "action_log.json"
    document = json.loads(action_file.read_text(encoding="utf-8"))
    document["days"][day]["records"].append({
        "time": "08:05:00", "content": "旧格式行动，没有 UID 和时长",
    })
    action_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in private.glob("*.json")}
    facts = LearningLoopService(manager, now_provider=clock).day_snapshot(day)
    window = _window(manager, clock)
    try:
        assert len(facts["actions"]) == 3
        assert facts["actual_minutes"] == 45
        assert facts["actual_duration_count"] == 2
        assert window.actions_metric.value_label.text() == "3 条"
        assert "已记录实际时长  45 分钟 · 2/3 条有时长" in window.duration_label.text()
        assert [action["content"] for action in window.actions] == [
            action["content"] for action in facts["actions"]
        ]
        content_labels = [label for label in
                          window.action_panel.findChildren(QLabel, "actionContent")
                          if label.isVisible()]
        assert [label.text() for label in content_labels] == [
            first["content"], second["content"], "旧格式行动，没有 UID 和时长"
        ]
        assert all(label.toolTip() == label.text() for label in content_labels)
        assert [label.text() for label in window.action_panel.findChildren(
            QLabel, "actionTime") if label.isVisible()] == [
                "10:15", "20 分钟", "10:15", "25 分钟", "08:05"
            ]
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert content_labels[0] is [label for label in
            window.action_panel.findChildren(QLabel, "actionContent")
            if label.isVisible()][0]
        assert {path.name: path.read_bytes() for path in private.glob("*.json")} == before

        run_root = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
        if run_root:
            image = Path(run_root) / "artifacts" / "today-growth-actions-isolated-service.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(image))

        window.tasks[0]["_card"].check_box.click()
        QApplication.processEvents()
        after = LearningLoopService(manager, now_provider=clock).day_snapshot(day)
        assert after["actions"] == facts["actions"]
        assert after["actual_minutes"] == 45
        assert window.actions_metric.value_label.text() == "3 条"
        assert "45 分钟 · 2/3 条有时长" in window.duration_label.text()
    finally:
        window.close()


def test_action_empty_external_refresh_cross_day_and_read_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 9, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    other = GrowthManager(private, now_provider=clock)
    window = _window(manager, clock)
    try:
        assert window.actions_metric.value_label.text() == "0 条"
        assert "今天还没有行动记录" in [label.text() for label in
            window.action_panel.findChildren(QLabel, "mutedText") if label.isVisible()][0]
        assert "已记录实际时长  时长未记录" in window.duration_label.text()
        window.refresh_timer.setInterval(30)
        other.add_record("从另一入口新增行动")
        QTest.qWait(100)
        assert [action["content"] for action in window.actions] == ["从另一入口新增行动"]
        assert window.actions_metric.value_label.text() == "1 条"
        assert "时长未记录 · 0/1 条有时长" in window.duration_label.text()

        with monkeypatch.context() as patch:
            patch.setattr(manager, "records_for_date", lambda _day: (_ for _ in ()).throw(OSError("read failed")))
            assert not window.refresh_from_service()
            assert [action["content"] for action in window.actions] == ["从另一入口新增行动"]
            assert "暂未刷新成功" in window.action_notice_label.text()
            assert window.actions_metric.value_label.text() == "1 条"
        assert window.refresh_from_service()
        assert not window.action_notice_label.isVisible()

        clock.value = datetime(2026, 10, 10, 7, 30)
        other.add_record("新一天的行动")
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert [action["content"] for action in window.actions] == ["新一天的行动"]
        assert window.actions_metric.value_label.text() == "1 条"
        assert window._display_date == "2026-10-10"

        clock.value = datetime(2026, 10, 11, 7, 30)
        with monkeypatch.context() as patch:
            patch.setattr(manager, "records_for_date", lambda _day: (_ for _ in ()).throw(OSError("read failed")))
            assert not window.refresh_from_service()
            assert window.actions == []
            assert window.actions_metric.value_label.text() == "读取失败"
            assert "旧日行动已隐藏" in window.action_notice_label.text()
            assert "新一天的行动" not in [label.text() for label in
                window.action_panel.findChildren(QLabel, "actionContent") if label.isVisible()]
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert window.actions == []
        assert window.actions_metric.value_label.text() == "0 条"
        assert not window.action_notice_label.isVisible()
    finally:
        window.close()


def test_initial_action_read_failure_is_not_shown_as_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 11, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    original = manager.records_for_date
    monkeypatch.setattr(
        manager, "records_for_date",
        lambda _day: (_ for _ in ()).throw(OSError("read failed")),
    )
    window = _window(manager, clock)
    try:
        assert window.actions_metric.value_label.text() == "读取失败"
        assert "读取失败" in window.action_notice_label.text()
        assert "今天还没有行动记录" not in [label.text() for label in
            window.action_panel.findChildren(QLabel, "mutedText") if label.isVisible()]
        monkeypatch.setattr(manager, "records_for_date", original)
        assert window.refresh_from_service()
        assert window.actions_metric.value_label.text() == "0 条"
        assert not window.action_notice_label.isVisible()
    finally:
        window.close()


def test_corrupt_action_store_keeps_last_success_without_read_side_effect(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 11, 30))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    manager.add_record("读取前已保存的行动", duration_minutes=15)
    window = _window(manager, clock)
    try:
        action_file = private / "action_log.json"
        for broken in (
            "{broken",
            json.dumps({"version": 1, "days": {
                "2026-10-09": {"records": "invalid"}
            }}),
        ):
            action_file.write_text(broken, encoding="utf-8")
            files_before = {path.relative_to(private): path.read_bytes()
                            for path in private.rglob("*") if path.is_file()}
            assert not window.refresh_from_service()
            assert [action["content"] for action in window.actions] == ["读取前已保存的行动"]
            assert window.actions_metric.value_label.text() == "1 条"
            assert "暂未刷新成功" in window.action_notice_label.text()
            assert {path.relative_to(private): path.read_bytes()
                    for path in private.rglob("*") if path.is_file()} == files_before
    finally:
        window.close()


def test_many_long_actions_scroll_without_resizing_window(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 9, 12, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    long_content = "详细记录今天实际推进的事项，供以后完整查看。" * 12
    manager.add_record(long_content)
    for index in range(9):
        manager.add_record(f"行动记录 {index + 1}")
    window = _window(manager, clock)
    try:
        initial_size = window.size()
        QTest.qWait(30)
        bar = window.action_scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(min(55, bar.maximum()))
        before = bar.value()
        other = GrowthManager(private, now_provider=clock)
        other.add_record("新增的第十一条")
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert abs(bar.value() - before) <= 2
        assert window.size() == initial_size
        assert window.page_scroll.horizontalScrollBar().maximum() == 0
        labels = window.action_panel.findChildren(QLabel, "actionContent")
        assert any(label.text() == long_content and label.wordWrap()
                   and label.toolTip() == long_content for label in labels)
    finally:
        window.close()


def _capture_review_state(window: TodayGrowthDialog, name: str) -> None:
    run_root = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
    if run_root:
        path = Path(run_root) / "artifacts" / f"today-growth-review-{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        QApplication.processEvents()
        assert window.grab().save(str(path))


def test_saved_review_empty_external_save_and_revision_are_read_only(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 14, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    other = GrowthManager(private, now_provider=clock)
    before_open = {path.name: path.read_bytes() for path in private.glob("*.json")}
    window = _window(manager, clock)
    try:
        assert window.summary_status_label.text() == "尚未保存"
        assert window.summary_text.toPlainText() == (
            "今天还没有保存复盘。可在聊天或原成长面板保存。"
        )
        assert {path.name: path.read_bytes() for path in private.glob("*.json")} == before_open
        _capture_review_state(window, "empty")

        other.add_task("隔离复盘计划")
        other.add_record("隔离复盘行动")
        saved = other.save_today_review()
        window.refresh_timer.setInterval(30)
        QTest.qWait(100)
        assert window.summary_status_label.text() == "已保存复盘"
        assert window.summary_text.toPlainText() == saved["review"]["panel_summary"]
        assert not window.summary_notice_label.isVisible()
        _capture_review_state(window, "saved")

        before_refresh = {path.name: path.read_bytes() for path in private.glob("*.json")}
        assert window.refresh_from_service()
        assert window.summary_text.toPlainText() == saved["review"]["panel_summary"]
        assert {path.name: path.read_bytes() for path in private.glob("*.json")} == before_refresh

        other.add_record("保存之后补充的行动")
        assert window.refresh_from_service()
        assert "记录已有变化，当前显示的是此前保存的复盘" in (
            window.summary_notice_label.text()
        )
        assert window.summary_text.toPlainText() == saved["review"]["panel_summary"]
        _capture_review_state(window, "changed")

        revised = other.save_today_review()
        QTest.qWait(100)
        assert revised["revision"] == saved["revision"] + 1
        assert window.summary_text.toPlainText() == revised["review"]["panel_summary"]
        assert not window.summary_notice_label.isVisible()
    finally:
        window.close()


def test_review_snapshot_detects_same_count_plan_and_action_changes(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 15, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    task = manager.add_task("原计划标题")
    manager.add_record("原行动正文")
    manager.save_today_review()
    window = _window(manager, clock)
    try:
        assert not window.summary_notice_label.isVisible()
        updated, changed, _reason = manager.update_task(task["id"], {"title": "新计划标题"})
        assert changed and updated["title"] == "新计划标题"
        assert window.refresh_from_service()
        assert "记录已有变化" in window.summary_notice_label.text()
        assert window._day_facts["total"] == 1
        manager.save_today_review()
        assert window.refresh_from_service()
        assert not window.summary_notice_label.isVisible()

        manager.complete_by_id(task["id"])
        assert window.refresh_from_service()
        assert "记录已有变化" in window.summary_notice_label.text()
        assert window._day_facts["total"] == 1
        manager.save_today_review()
        assert window.refresh_from_service()
        assert not window.summary_notice_label.isVisible()

        action_file = private / "action_log.json"
        data = json.loads(action_file.read_text(encoding="utf-8"))
        data["days"]["2026-10-09"]["records"][0]["content"] = "新行动正文"
        action_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert window.refresh_from_service()
        assert "记录已有变化" in window.summary_notice_label.text()
        assert len(window._day_facts["actions"]) == 1
    finally:
        window.close()


def test_review_legacy_snapshot_cross_day_and_failed_read(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 9, 16, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    manager.add_task("旧版复盘计划")
    saved = manager.save_today_review()
    window = _window(manager, clock)
    try:
        growth_file = private / "growth_log.json"
        data = json.loads(growth_file.read_text(encoding="utf-8"))
        review = data["entries"]["2026-10-09"]["review"]
        review.pop("plan_snapshot")
        review.pop("action_records")
        review.pop("panel_summary")
        growth_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert window.refresh_from_service()
        assert window.summary_text.toPlainText() == saved["review"]["text"]
        assert "无法核对" in window.summary_notice_label.text()

        clock.value = datetime(2026, 10, 10, 8, 0)
        assert window.refresh_from_service()
        assert window.summary_status_label.text() == "尚未保存"
        assert "今天还没有保存复盘" in window.summary_text.toPlainText()
        assert "旧版复盘计划" not in window.summary_text.toPlainText()

        growth_file.write_text("{broken", encoding="utf-8")
        before = {path.relative_to(private): path.read_bytes()
                  for path in private.rglob("*") if path.is_file()}
        assert window.refresh_from_service()
        assert window.summary_status_label.text() == "读取失败"
        assert "读取失败" in window.summary_text.toPlainText()
        assert "今天还没有保存复盘" not in window.summary_text.toPlainText()
        assert {path.relative_to(private): path.read_bytes()
                for path in private.rglob("*") if path.is_file()} == before
    finally:
        window.close()


def test_long_review_scrolls_inside_summary_without_business_write(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 17, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    manager.add_task("需要认真梳理的计划内容" * 28)
    manager.add_record("已完成的一段较长行动说明" * 28)
    saved = manager.save_today_review()
    before = {path.name: path.read_bytes() for path in private.glob("*.json")}
    window = _window(manager, clock)
    try:
        initial_size = window.size()
        QTest.qWait(30)
        bar = window.summary_text.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(min(30, bar.maximum()))
        position = bar.value()
        assert window.refresh_from_service()
        QApplication.processEvents()
        assert bar.value() == position
        assert window.summary_text.toPlainText() == saved["review"]["panel_summary"]
        assert window.size() == initial_size
        assert window.page_scroll.horizontalScrollBar().maximum() == 0
        assert {path.name: path.read_bytes() for path in private.glob("*.json")} == before
    finally:
        window.close()


def test_chat_review_saves_matching_panel_summary_and_survives_reopen(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 18, 0))
    llm = RecordingLLM(
        "今天完成了机器学习课程复习，并记录了梯度下降推导。"
        "项目笔记还没有整理；接下来可先完成这项计划。"
    )
    service, manager, _memory, _history = build_service(tmp_path, llm)
    manager.now_provider = clock
    service.conversation_service.business_now_provider = clock
    task = manager.add_task("机器学习课程复习")
    manager.complete_by_id(task["id"])
    manager.add_task("整理项目笔记")
    manager.add_record("完成梯度下降推导")

    response = service.handle("今日复盘", "panel-chat-sync")
    assert [(item.tool, item.success) for item in response.tool_results] == [
        ("save_daily_review", True)
    ]
    assert response.message == llm.reply
    assert len(llm.calls) == 1
    saved = manager.entries(strict_read=True)[0]
    summary = saved["review"]["panel_summary"]
    assert 60 <= len(summary) <= 100
    assert "机器学习课程复习" in summary
    assert "整理项目笔记" in summary
    assert "未完成" in summary
    assert saved["review"]["text"] != summary

    window = _window(manager, clock)
    reopened = None
    try:
        assert window.summary_text.toPlainText() == summary
        assert not window.summary_notice_label.isVisible()
        _capture_review_state(window, "chat-sync")

        manager.add_record("补充了一条项目资料索引")
        llm.reply = "今天完成了机器学习课程复习，留下两条行动记录。项目笔记仍待整理。"
        second = service.handle("今日复盘", "panel-chat-sync")
        assert second.message == llm.reply
        assert second.tool_results[0].data["entry"]["revision"] == 2
        assert window.refresh_from_service()
        newer = manager.entries(strict_read=True)[0]["review"]["panel_summary"]
        assert newer != summary
        assert window.summary_text.toPlainText() == newer
        assert not window.summary_notice_label.isVisible()
        window.close()

        fresh_manager = GrowthManager(manager.private_dir, now_provider=clock)
        reopened = _window(fresh_manager, clock)
        assert reopened.summary_text.toPlainText() == newer
        assert reopened.summary_status_label.text() == "已保存复盘"
    finally:
        window.close()
        if reopened is not None:
            reopened.close()


def test_chat_review_save_failure_does_not_claim_panel_sync(
    tmp_path: Path, monkeypatch,
) -> None:
    clock = Clock(datetime(2026, 10, 9, 19, 0))
    service, manager, _memory, _history = build_service(tmp_path, NoCallLLM())
    manager.now_provider = clock
    manager.add_task("尚未完成的任务")
    window = _window(manager, clock)
    try:
        growth_files = (manager.plan_file, manager.action_file, manager.growth_file)
        before = {path.name: path.read_bytes() for path in growth_files}
        monkeypatch.setattr(manager.repository, "save_growth", lambda _data: False)
        response = service.handle("今日复盘", "panel-chat-failure")
        assert response.status == "failed"
        assert response.tool_results
        assert not response.tool_results[0].success
        assert "已保存" not in response.message
        assert window.refresh_from_service()
        assert window.summary_status_label.text() == "尚未保存"
        assert {path.name: path.read_bytes() for path in growth_files} == before
    finally:
        window.close()


def _capture_recent_state(window: TodayGrowthDialog, name: str) -> None:
    run_root = os.environ.get("ROXYPLAN_TEST_WORKSPACE")
    if run_root:
        path = Path(run_root) / "artifacts" / f"today-growth-recent-{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        QApplication.processEvents()
        assert window.grab().save(str(path))


def test_recent_metric_seven_day_boundary_and_rollover(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 9, 9, 0))
    manager = GrowthManager(tmp_path / "private", now_provider=clock)
    outside = manager.add_task("八天前", date="2026-10-01")
    manager.complete_by_id(outside["id"], "2026-10-01")
    boundary = manager.add_task("七天前完成", date="2026-10-02")
    manager.complete_by_id(boundary["id"], "2026-10-02")
    manager.add_task("昨天未完成", date="2026-10-08")
    for title in ("今天完成一", "今天完成二"):
        task = manager.add_task(title)
        manager.complete_by_id(task["id"])
    window = _window(manager, clock)
    try:
        assert window.recent_metric.value_label.text() == "50%"
        assert "不含今天" in window.recent_metric.toolTip()
        _capture_recent_state(window, "mixed")

        clock.value = datetime(2026, 10, 10, 9, 0)
        assert window.refresh_from_service()
        assert window.recent_metric.value_label.text() == "67%"
        assert window._display_date == "2026-10-10"
    finally:
        window.close()


def test_recent_metric_other_entry_updates_without_refresh_writes(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 9, 9, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    other = GrowthManager(private, now_provider=clock)
    task = manager.add_task("昨天的事项", date="2026-10-08")
    window = _window(manager, clock)
    try:
        assert window.recent_metric.value_label.text() == "0%"
        other.complete_by_id(task["id"], "2026-10-08")
        window.refresh_timer.setInterval(30)
        QTest.qWait(100)
        assert window.recent_metric.value_label.text() == "100%"
        before = {path.name: path.read_bytes() for path in private.glob("*.json")}
        assert window.refresh_from_service()
        assert {path.name: path.read_bytes() for path in private.glob("*.json")} == before
    finally:
        window.close()


def test_recent_metric_empty_and_corrupt_plan_are_distinct(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 9, 9, 0))
    private = tmp_path / "private"
    manager = GrowthManager(private, now_provider=clock)
    manager.add_record("只有行动", date="2026-10-08")
    window = _window(manager, clock)
    try:
        assert window.recent_metric.value_label.text() == "暂无计划数据"
        _capture_recent_state(window, "empty")
        plan_file = manager.plan_file
        plan_file.write_text("{broken", encoding="utf-8")
        before = {path.relative_to(private): path.read_bytes()
                  for path in private.rglob("*") if path.is_file()}
        assert not window.refresh_from_service()
        assert window.recent_metric.value_label.text() == "读取失败"
        assert {path.relative_to(private): path.read_bytes()
                for path in private.rglob("*") if path.is_file()} == before
        _capture_recent_state(window, "failed")
    finally:
        window.close()
