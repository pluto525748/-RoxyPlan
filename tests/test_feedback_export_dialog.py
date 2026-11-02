"""反馈导出界面：先看清单再同意，取消与失败都不算成功。

界面测试都在 offscreen 下运行，不显示窗口、不占用桌面；数据根与配置全是临时合成
内容，不读取正式用户数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import frontend.feedback_dialog as dialog_module
import frontend.settings_dialog as settings_module
from modules.feedback_export import (
    FeedbackExportService,
    RunMeta,
    SOURCE_ENGINEERING,
)
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore

RUN_DIR_IN_RANGE = "run-20261010T090000-" + "a" * 32


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def build_synthetic_instance(root: Path) -> None:
    """界面测试用的最小合成实例根，布局与真实 data_root 一致。"""
    private = root / "data" / "private"
    _write_json(private / "chat_history.json", {
        "version": 1,
        "sessions": [{
            "session_id": "sess-in-range",
            "started_at": "2026-10-10T09:00:00",
            "updated_at": "2026-10-10T09:20:00",
            "messages": [{"message_id": "m1", "role": "user", "content": "合成内容",
                          "created_at": "2026-10-10T09:00:00", "trace_id": "trace-aaa"}],
        }],
    })
    _write_json(private / "chat_summaries.json", {
        "version": 1,
        "summaries": {"sess-in-range": {"summary": "合成摘要", "updated_at": "2026-10-10T09:20:00"}},
    })
    _write_json(private / "today_plan.json", {
        "version": 1, "days": {"2026-10-10": {"tasks": [{"text": "合成任务", "done": False}]}},
    })
    _write_json(private / "action_log.json", {
        "version": 1, "days": {"2026-10-10": {"actions": []}},
    })
    _write_json(private / "growth_log.json", {
        "version": 1,
        "entries": {"2026-10-10": {"uid": "review_2026-10-10", "date": "2026-10-10",
                                   "review": {"completed": 1, "minutes": 25}}},
    })
    _write_json(private / "model_usage.json", {
        "schema_version": "1.0", "updated_at": "2026-10-10T09:20:00",
        "totals": {"calls": 1}, "recent": [],
    })
    _write_json(root / "memory.json", {
        "version": 1, "entries": [{"uid": "mem-1", "content": "合成记忆"}],
    })
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "interaction_diagnostics.jsonl").write_text(
        json.dumps({"created_at": "2026-10-10T09:00:00", "trace_id": "trace-aaa"}) + "\n",
        encoding="utf-8",
    )
    run_dir = logs / "development" / RUN_DIR_IN_RANGE
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"timestamp": "2026-10-10T09:00:00", "event": "chat_turn"}) + "\n",
        encoding="utf-8",
    )


def make_service(root: Path, source: str = SOURCE_ENGINEERING) -> FeedbackExportService:
    return FeedbackExportService(
        root, RunMeta(version="0.9.0-synthetic", instance_id="inst-0123456789abcdef", source=source)
    )


@pytest.fixture
def instance_root(tmp_path: Path) -> Path:
    root = tmp_path / "instance"
    build_synthetic_instance(root)
    return root


def _settings_dialog(tmp_path: Path, monkeypatch, feedback_export):
    monkeypatch.setattr(settings_module, "SecretStore", lambda _root: SecretStore(tmp_path))
    monkeypatch.setattr(
        settings_module, "ModelUsageStore", lambda _root: ModelUsageStore(tmp_path)
    )
    config = tmp_path / "pet_config.json"
    config.write_text(json.dumps({"pet_always_on_top": False}), encoding="utf-8")
    return settings_module.SettingsDialog(
        config_file=config,
        knowledge_dir=tmp_path / "knowledge",
        feedback_export=feedback_export,
    )


def _dialog(service):
    return dialog_module.FeedbackExportDialog(service, display_info={"screen_logical": "1920x1080"})


def _snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in root.rglob("*")
    }


# ------------------------------------------------------------------ 设置入口


def test_settings_entry_is_disabled_without_an_injected_service(tmp_path: Path, monkeypatch) -> None:
    """未注入数据根时入口明确不可用，不猜路径。"""
    app = _app()
    settings = _settings_dialog(tmp_path, monkeypatch, None)
    try:
        assert settings.feedback_export is None
        button = _find_button(settings, "导出内测反馈")
        assert button is not None and not button.isEnabled()
        assert settings.open_feedback_export_dialog() is None
        assert settings.feedback_dialog is None
    finally:
        settings.close()
        app.processEvents()


def test_settings_entry_reports_why_it_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    service = FeedbackExportService(None, RunMeta("0.9.0", "inst-0123456789abcdef"))
    settings = _settings_dialog(tmp_path, monkeypatch, service)
    try:
        button = _find_button(settings, "导出内测反馈")
        assert button is not None and not button.isEnabled()
        assert "数据根" in service.unavailable_reason
        assert settings.open_feedback_export_dialog() is None
    finally:
        settings.close()
        app.processEvents()


def test_settings_entry_opens_the_dialog_when_a_service_is_injected(
    tmp_path: Path, monkeypatch, instance_root: Path,
) -> None:
    app = _app()
    service = make_service(instance_root)
    settings = _settings_dialog(tmp_path, monkeypatch, service)
    try:
        button = _find_button(settings, "导出内测反馈")
        assert button is not None and button.isEnabled()

        dialog = settings.open_feedback_export_dialog()
        app.processEvents()
        assert dialog is not None and dialog.isVisible()
        assert dialog.service is service
        # 显示环境随导出包一起提供，不读取用户名或主机名。
        assert dialog.display_info.get("screen_logical")
        assert "host" not in dialog.display_info and "user" not in dialog.display_info

        received = []
        dialog.feedback_exported.connect(received.append)
        settings.feedback_exported.connect(received.append)
        assert dialog.export_button.isEnabled() is False
    finally:
        settings.close()
        app.processEvents()


def _find_button(parent, text: str):
    for button in parent.findChildren(QPushButton):
        if button.text() == text:
            return button
    return None


# ---------------------------------------------------------------- 清单与同意


def test_consent_and_export_stay_locked_until_the_list_is_reviewed(instance_root: Path) -> None:
    app = _app()
    dialog = _dialog(make_service(instance_root))
    try:
        assert not dialog.consent_check.isEnabled()
        assert not dialog.export_button.isEnabled()
        assert not dialog.entries_list.isVisible()

        dialog.preview_button.click()
        app.processEvents()
        assert dialog.entries_list.count() > 0
        assert dialog.consent_check.isEnabled()
        # 看过清单还不等于同意。
        assert not dialog.export_button.isEnabled()

        dialog.consent_check.setChecked(True)
        assert dialog.export_button.isEnabled()

        dialog.consent_check.setChecked(False)
        assert not dialog.export_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()


def test_body_categories_are_off_by_default_and_flagged(instance_root: Path) -> None:
    app = _app()
    dialog = _dialog(make_service(instance_root))
    try:
        assert not dialog.chat_check.isChecked()
        assert not dialog.memory_check.isChecked()
        assert dialog.growth_check.isChecked()
        assert dialog.diagnostics_check.isChecked()
        chat_label = dialog.chat_check.text()
        memory_label = dialog.memory_check.text()
        assert "交给开发者" in chat_label and "默认不选" in chat_label
        assert "交给开发者" in memory_label and "默认不选" in memory_label

        dialog.preview_button.click()
        app.processEvents()
        listed = "\n".join(
            dialog.entries_list.item(index).text()
            for index in range(dialog.entries_list.count())
        )
        assert "已保存的聊天" not in listed
        assert "长期记忆" not in listed

        dialog.chat_check.setChecked(True)
        dialog.preview_button.click()
        app.processEvents()
        listed = "\n".join(
            dialog.entries_list.item(index).text()
            for index in range(dialog.entries_list.count())
        )
        assert "已保存的聊天" in listed
    finally:
        dialog.close()
        app.processEvents()


def test_changing_the_scope_invalidates_the_reviewed_list(instance_root: Path) -> None:
    app = _app()
    dialog = _dialog(make_service(instance_root))
    try:
        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        assert dialog.export_button.isEnabled()

        dialog.chat_check.setChecked(True)
        app.processEvents()
        assert dialog.entries_list.count() == 0
        assert not dialog.entries_list.isVisible()
        assert not dialog.consent_check.isEnabled()
        assert not dialog.export_button.isEnabled()

        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        assert dialog.export_button.isEnabled()

        # 改反馈文字同样要重新确认：那也是“以上内容”的一部分。
        dialog.problem_edit.setPlainText("问题描述")
        app.processEvents()
        assert not dialog.consent_check.isChecked()
        assert not dialog.export_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()


def test_out_of_range_scope_reports_empty_instead_of_widening(instance_root: Path) -> None:
    """范围内没有数据时就列出“范围内为空”，不悄悄放宽日期；用量文件的例外要写明。"""
    app = _app()
    dialog = _dialog(make_service(instance_root))
    try:
        dialog.start_date.setDate(dialog.start_date.date().addYears(-3))
        dialog.end_date.setDate(dialog.start_date.date().addDays(3))
        dialog.preview_button.click()
        app.processEvents()

        listed = "\n".join(
            dialog.entries_list.item(index).text()
            for index in range(dialog.entries_list.count())
        )
        assert "范围内为空" in listed
        assert "未按日期裁剪" in listed
        assert "已保存的聊天" not in listed
        # 唯一还在包内的是被明确标注的累计用量文件，用户看着它可以决定同不同意。
        assert dialog.consent_check.isEnabled()

        # 结束早于开始：整份清单为空，直接拒绝。
        dialog.start_date.setDate(dialog.start_date.date().addDays(10))
        dialog.preview_button.click()
        app.processEvents()
        assert dialog.entries_list.count() == 0
        assert "结束日期早于开始日期" in dialog.result_label.text()
        assert not dialog.consent_check.isEnabled()
        assert not dialog.export_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()


# ------------------------------------------------------------------ 取消与失败


def test_cancel_writes_nothing_and_never_claims_success(
    instance_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = _app()
    monkeypatch.setattr(
        dialog_module.QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: ("", "")),
    )
    dialog = _dialog(make_service(instance_root))
    received = []
    dialog.feedback_exported.connect(received.append)
    before = _snapshot(instance_root)
    try:
        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        dialog.export_button.click()
        app.processEvents()

        assert received == []
        assert "已取消" in dialog.result_label.text()
        assert "已导出" not in dialog.result_label.text()
        assert _snapshot(instance_root) == before
        assert list(tmp_path.glob("*.zip")) == []
    finally:
        dialog.close()
        app.processEvents()


def test_write_failure_is_reported_and_never_claims_success(
    instance_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = _app()
    blocker = tmp_path / "blocker"
    blocker.write_text("这是文件不是目录", encoding="utf-8")
    monkeypatch.setattr(
        dialog_module.QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(blocker / "out.zip"), "")),
    )
    dialog = _dialog(make_service(instance_root))
    received = []
    dialog.feedback_exported.connect(received.append)
    try:
        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        dialog.export_button.click()
        app.processEvents()

        assert received == []
        assert "导出失败" in dialog.result_label.text()
        assert "已导出" not in dialog.result_label.text()
        assert not (blocker / "out.zip").exists()
    finally:
        dialog.close()
        app.processEvents()


def test_successful_export_reports_the_real_path_and_emits_it(
    instance_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = _app()
    target = tmp_path / "chosen" / "反馈包.zip"
    monkeypatch.setattr(
        dialog_module.QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )
    dialog = _dialog(make_service(instance_root))
    received = []
    dialog.feedback_exported.connect(received.append)
    before = _snapshot(instance_root)
    try:
        dialog.problem_edit.setPlainText("合成问题")
        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        dialog.export_button.click()
        app.processEvents()

        assert target.exists()
        assert received == [str(target)]
        assert "已导出" in dialog.result_label.text()
        assert str(target) in dialog.result_label.text()
        # 导出没有改动实例根里的任何业务数据，也没有再次导出。
        assert _snapshot(instance_root) == before
        assert not dialog.export_button.isEnabled()

        dialog.consent_check.setChecked(True)
        assert dialog.export_button.isEnabled() is False
    finally:
        dialog.close()
        app.processEvents()


def test_export_rechecks_the_list_and_re_asks_when_it_changed(
    instance_root: Path, tmp_path: Path, monkeypatch,
) -> None:
    """看过清单之后目录又变了：刷新清单并要求重新确认，不按旧同意导出。"""
    app = _app()
    called = []
    target = tmp_path / "stale.zip"
    monkeypatch.setattr(
        dialog_module.QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (called.append(1), str(target))[1]),
    )
    service = make_service(instance_root)
    dialog = _dialog(service)
    try:
        dialog.preview_button.click()
        dialog.consent_check.setChecked(True)
        assert dialog.export_button.isEnabled()

        # 预览之后新增一个范围内的日志分片，清单随之变化。
        run_dir = instance_root / "logs" / "development" / RUN_DIR_IN_RANGE
        (run_dir / "events.000002.jsonl").write_text(
            json.dumps({"timestamp": "2026-10-10T10:00:00", "event": "late"}) + "\n",
            encoding="utf-8",
        )

        dialog.export_button.click()
        app.processEvents()

        assert called == []
        assert not target.exists()
        assert "不一致" in dialog.result_label.text()
        assert not dialog.consent_check.isChecked()
        assert dialog.consent_check.isEnabled()
        listed = "\n".join(
            dialog.entries_list.item(index).text()
            for index in range(dialog.entries_list.count())
        )
        assert "events.000002.jsonl" in listed
    finally:
        dialog.close()
        app.processEvents()


def test_unavailable_service_locks_the_whole_dialog(tmp_path: Path) -> None:
    app = _app()
    service = FeedbackExportService(tmp_path / "missing", RunMeta("0.9.0", "inst-0123456789abcdef"))
    dialog = _dialog(service)
    try:
        assert not dialog.service_available
        for widget in (
            dialog.problem_edit, dialog.expected_edit, dialog.actual_edit,
            dialog.start_date, dialog.end_date, dialog.preview_button,
            dialog.chat_check, dialog.growth_check,
            dialog.diagnostics_check, dialog.memory_check,
            dialog.consent_check, dialog.export_button,
        ):
            assert not widget.isEnabled()
        assert "导出不可用" in dialog.result_label.text()
    finally:
        dialog.close()
        app.processEvents()


def test_engineered_flow_keeps_the_instance_root_untouched(instance_root: Path, tmp_path: Path, monkeypatch) -> None:
    """完整走一遍界面流程，业务数据零写入。"""
    app = _app()
    target = tmp_path / "full-flow.zip"
    monkeypatch.setattr(
        dialog_module.QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )
    before = _snapshot(instance_root)
    dialog = _dialog(
        FeedbackExportService(
            instance_root,
            RunMeta("0.9.0-synthetic", "inst-0123456789abcdef", SOURCE_ENGINEERING),
        )
    )
    try:
        dialog.problem_edit.setPlainText("合成问题")
        dialog.expected_edit.setPlainText("合成预期")
        dialog.actual_edit.setPlainText("合成实际")
        dialog.chat_check.setChecked(True)
        dialog.preview_button.click()
        app.processEvents()
        assert dialog.consent_check.isEnabled()
        dialog.consent_check.setChecked(True)
        dialog.export_button.click()
        app.processEvents()

        assert target.exists()
        assert _snapshot(instance_root) == before

        import zipfile

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert "chat/chat_history.json" in names
        assert manifest["source_label"] == "工程或模拟"
        assert str(instance_root) not in json.dumps(manifest, ensure_ascii=False)
    finally:
        dialog.close()
        app.processEvents()
