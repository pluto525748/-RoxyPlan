"""内测反馈导出：授权边界、范围选择、记录关联、损坏与缺失、凭据与越界。

全部使用合成数据与临时实例根，不读取正式用户数据，不继承模型密钥，不发网络请求，
也不启动任何可见窗口（Qt 以 offscreen 运行）。
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from modules.feedback_export import (
    CATEGORY_CHAT,
    CATEGORY_DIAGNOSTICS,
    CATEGORY_GROWTH,
    CATEGORY_MEMORY,
    FeedbackExportService,
    FeedbackText,
    ExportScope,
    RunMeta,
    SOURCE_ENGINEERING,
    SOURCE_REAL,
    STATUS_EMPTY,
    STATUS_INCLUDED,
    STATUS_MISSING,
    STATUS_SKIPPED,
    STATUS_UNREADABLE,
    default_scope,
)

SCOPE_START = date(2026, 10, 7)
SCOPE_END = date(2026, 10, 13)
RUN_DIR_IN_RANGE = "run-20261010T090000-" + "a" * 32
RUN_DIR_OUT_OF_RANGE = "run-20260801T090000-" + "b" * 32


# --------------------------------------------------------------------- 合成数据


def test_daily_session_export_uses_business_date_not_creation_date(tmp_path):
    from modules.feedback_export import _chat_session_in_scope

    session = {"session_kind": "daily_learning", "business_date": "2026-09-30",
               "started_at": "2026-10-13T09:00:00", "updated_at": "2026-10-13T09:00:00"}
    assert _chat_session_in_scope(session, ExportScope(date(2026, 9, 30), date(2026, 9, 30)))
    assert not _chat_session_in_scope(session, ExportScope(date(2026, 10, 13), date(2026, 10, 13)))


def test_resolved_subdirectory_outside_instance_is_refused(tmp_path, monkeypatch):
    from modules.feedback_export import PlannedEntry

    root = tmp_path / "instance"
    allowed = root / "data/private"
    allowed.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "today_plan.json"
    external.write_text('{"days":{}}', encoding="utf-8")
    source = allowed / external.name
    original_resolve = Path.resolve

    def resolve_junction(path, *args, **kwargs):
        if path == allowed:
            return outside
        if path == source:
            return external
        return original_resolve(path, *args, **kwargs)

    # 模拟 Windows junction 的解析结果，不依赖创建链接权限。
    monkeypatch.setattr(Path, "resolve", resolve_junction)
    service = FeedbackExportService(data_root=root,
                                   run_meta=RunMeta("0.9.0-beta.1", "synthetic-instance", SOURCE_ENGINEERING))
    entry = service._inspect(CATEGORY_GROWTH, source, "growth/today_plan.json", allowed)
    assert entry.status == STATUS_SKIPPED
    old_entry = PlannedEntry(CATEGORY_GROWTH, "growth/today_plan.json", STATUS_INCLUDED,
                             "", str(source), "", allowed)
    payload, reason = service._render_entry(old_entry, default_scope(), {})
    assert payload is None and "实例根" in reason


def _write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def build_instance(root: Path) -> None:
    """一个完整但完全合成的实例根，布局与真实 data_root 一致。"""
    private = root / "data" / "private"
    private.mkdir(parents=True, exist_ok=True)

    _write_json(private / "chat_history.json", {
        "version": 1,
        "sessions": [
            {
                "session_id": "sess-in-range",
                "date": "2026-10-10",
                "started_at": "2026-10-10T09:00:00",
                "updated_at": "2026-10-10T09:20:00",
                "messages": [
                    {"message_id": "m1", "role": "user", "content": "合成问题",
                     "created_at": "2026-10-10T09:00:00", "trace_id": "trace-aaa"},
                    {"message_id": "m2", "role": "assistant", "content": "合成回答",
                     "created_at": "2026-10-10T09:00:05", "trace_id": "trace-aaa"},
                ],
            },
            {
                "session_id": "sess-summer",
                "date": "2026-08-01",
                "started_at": "2026-08-01T09:00:00",
                "updated_at": "2026-08-01T09:20:00",
                "messages": [
                    {"message_id": "m3", "role": "user", "content": "范围外的合成内容",
                     "created_at": "2026-08-01T09:00:00"},
                ],
            },
            {
                "session_id": "sess-no-time",
                "messages": [
                    {"message_id": "m4", "role": "user", "content": "没有时间的合成内容"},
                ],
            },
        ],
    })
    _write_json(private / "chat_summaries.json", {
        "version": 1,
        "summaries": {
            "sess-in-range": {"summary": "合成会话摘要", "updated_at": "2026-10-10T09:20:00"},
            "sess-summer": {"summary": "范围外的合成摘要", "updated_at": "2026-08-01T09:20:00"},
        },
    })

    _write_json(private / "today_plan.json", {
        "version": 1,
        "days": {
            "2026-10-10": {"tasks": [{"text": "合成任务", "done": False, "duration_minutes": 25}]},
            "2026-08-01": {"tasks": [{"text": "范围外的合成任务", "done": True, "duration_minutes": 10}]},
        },
    })
    _write_json(private / "action_log.json", {
        "version": 1,
        "days": {
            "2026-10-10": {"actions": [{"name": "jump", "at": "2026-10-10T09:05:00"}]},
            "2026-08-01": {"actions": []},
        },
    })
    _write_json(private / "growth_log.json", {
        "version": 1,
        "entries": {
            "2026-10-10": {
                "uid": "review_2026-10-10",
                "date": "2026-10-10",
                "saved_at": "2026-10-10T21:00:00",
                "review": {"completed": 1, "minutes": 25, "summary": "合成复盘"},
            },
            "2026-08-01": {
                "uid": "review_2026-08-01",
                "date": "2026-08-01",
                "saved_at": "2026-08-01T21:00:00",
                "review": {"completed": 0, "minutes": 0, "summary": "范围外的合成复盘"},
            },
        },
    })
    _write_json(private / "model_usage.json", {
        "schema_version": "1.0",
        "updated_at": "2026-10-10T09:20:00",
        "totals": {"calls": 2, "prompt_tokens": 30, "completion_tokens": 12},
        "recent": [{"at": "2026-10-10T09:00:00", "model": "synthetic-model"}],
    })
    # 私人配置与凭据：都不在白名单里，任何情况下都不应进入导出内容。
    _write_json(private / "llm_secrets.json", {"deepseek_api_key": "sk-SYNTHETICLEAKVALUE0001"})
    (root / ".env").write_text("DEEPSEEK_API_KEY=sk-SYNTHETICLEAKVALUE0002\n", encoding="utf-8")
    _write_json(root / "data" / "pet_config.json", {"pet_scale": 1.0})

    _write_json(root / "memory.json", {
        "version": 1,
        "entries": [{"uid": "mem-1", "content": "合成记忆", "created_at": "2026-10-10T09:30:00"}],
    })

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "interaction_diagnostics.jsonl").write_text(
        "\n".join([
            json.dumps({"created_at": "2026-10-10T09:00:00", "session_id": "sess-in-range",
                        "trace_id": "trace-aaa", "normalized_text": {"length": 4, "sha256": "0" * 64}},
                       ensure_ascii=False),
            json.dumps({"created_at": "2026-08-01T09:00:00", "trace_id": "trace-outside"},
                       ensure_ascii=False),
            "这不是 JSON，模拟损坏的一行",
        ]) + "\n",
        encoding="utf-8",
    )

    development = logs / "development"
    for run_name, stamp in ((RUN_DIR_IN_RANGE, "2026-10-10T09:00:00"),
                            (RUN_DIR_OUT_OF_RANGE, "2026-08-01T09:00:00")):
        run_dir = development / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "events.jsonl").write_text(
            json.dumps({"timestamp": stamp, "event": "chat_turn",
                        "trace_id": "trace-aaa" if run_name == RUN_DIR_IN_RANGE else "trace-outside"})
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "runtime.log").write_text(f"{stamp} chat_turn\n", encoding="utf-8")
        # 轮转后的旧分片与不在命名白名单里的文件，都不应被当作日志收走。
        (run_dir / "events.000001.jsonl").write_text(
            json.dumps({"timestamp": stamp, "event": "rotated"}) + "\n", encoding="utf-8"
        )
        (run_dir / "notes.txt").write_text("不应被导出\n", encoding="utf-8")


def make_service(root: Path, source: str = SOURCE_ENGINEERING) -> FeedbackExportService:
    return FeedbackExportService(
        root, RunMeta(version="0.9.0-synthetic", instance_id="inst-0123456789abcdef", source=source)
    )


def full_scope(**overrides) -> ExportScope:
    settings = dict(
        start=SCOPE_START, end=SCOPE_END,
        include_chat=False, include_growth=True,
        include_diagnostics=True, include_memory=False,
    )
    settings.update(overrides)
    return ExportScope(**settings)


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }


def read_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def archive_names(path: Path) -> list:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


# ------------------------------------------------------------------ 授权与边界


def test_service_is_unavailable_without_explicit_root_or_run_meta(tmp_path: Path) -> None:
    """没有显式注入数据根或运行元信息时，入口不可用，且不猜路径。"""
    root = tmp_path / "instance"
    build_instance(root)

    no_root = FeedbackExportService(None, RunMeta("0.9.0", "inst-0123456789abcdef"))
    assert not no_root.available
    assert "数据根" in no_root.unavailable_reason

    no_meta = FeedbackExportService(root, None)
    assert not no_meta.available
    assert "运行元信息" in no_meta.unavailable_reason

    bad_version = FeedbackExportService(root, RunMeta("", "inst-0123456789abcdef"))
    assert not bad_version.available

    bad_id = FeedbackExportService(root, RunMeta("0.9.0", ""))
    assert not bad_id.available

    bad_source = FeedbackExportService(root, RunMeta("0.9.0", "inst-0123456789abcdef", "unknown"))
    assert not bad_source.available

    missing_root = FeedbackExportService(tmp_path / "not-there", RunMeta("0.9.0", "inst-0123456789abcdef"))
    assert not missing_root.available

    for service in (no_root, no_meta, bad_version):
        target = tmp_path / "never.zip"
        outcome = service.export(service.plan(full_scope()), FeedbackText("a", "b", "c"), target)
        assert outcome.ok is False
        assert outcome.error
        assert not target.exists()


def test_plan_and_export_write_nothing_into_the_instance_root(tmp_path: Path) -> None:
    """零业务写入：清单只读，导出只落在调用方给出的目标路径。"""
    root = tmp_path / "instance"
    build_instance(root)
    before = snapshot(root)

    service = make_service(root)
    plan = service.plan(full_scope(include_chat=True, include_memory=True))
    assert service.available and plan.included
    assert snapshot(root) == before

    target = tmp_path / "out" / "feedback.zip"
    outcome = service.export(plan, FeedbackText("问题", "预期", "实际"), target)
    assert outcome.ok and outcome.target == target and target.exists()
    assert snapshot(root) == before
    # 临时文件也不留在目标目录里。
    assert [path.name for path in target.parent.iterdir()] == ["feedback.zip"]


# -------------------------------------------------------------- 范围与内容选择


def test_selected_categories_decide_what_enters_the_zip(tmp_path: Path) -> None:
    """正文类别默认不选；显式选中后才进入导出内容。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    default_plan = service.plan(full_scope())
    default_categories = {entry.category for entry in default_plan.included}
    assert default_categories == {CATEGORY_GROWTH, CATEGORY_DIAGNOSTICS}

    narrow = tmp_path / "narrow.zip"
    assert service.export(default_plan, FeedbackText("问题", "", ""), narrow).ok
    narrow_names = archive_names(narrow)
    assert not any(name.startswith("chat/") for name in narrow_names)
    assert not any(name.startswith("memory/") for name in narrow_names)
    assert "growth/growth_log.json" in narrow_names
    assert "diagnostics/interaction_diagnostics.jsonl" in narrow_names

    wide_plan = service.plan(full_scope(include_chat=True, include_memory=True))
    wide = tmp_path / "wide.zip"
    assert service.export(wide_plan, FeedbackText("问题", "", ""), wide).ok
    wide_names = archive_names(wide)
    assert "chat/chat_history.json" in wide_names
    assert "memory/memory.json" in wide_names
    assert set(narrow_names) < set(wide_names)


def test_date_range_trims_growth_and_diagnostic_lines(tmp_path: Path) -> None:
    """成长数据按天裁剪，诊断日志按行裁剪；范围外内容不进入包内。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    target = tmp_path / "range.zip"
    plan = service.plan(full_scope())
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    members = read_zip(target)

    growth_log = json.loads(members["growth/growth_log.json"].decode("utf-8"))
    assert set(growth_log["entries"]) == {"2026-10-10"}
    plan_days = json.loads(members["growth/today_plan.json"].decode("utf-8"))
    assert set(plan_days["days"]) == {"2026-10-10"}

    diagnostics = members["diagnostics/interaction_diagnostics.jsonl"].decode("utf-8")
    assert "trace-aaa" in diagnostics
    assert "trace-outside" not in diagnostics
    assert "无法按日期关联的行 0 行" in diagnostics
    assert "无法解析的行 1 行" in diagnostics

    # 诊断与运行日志的选取范围都写在清单里，不静默扩大。
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    assert manifest["scope"]["start"] == "2026-10-07"
    assert manifest["scope"]["end"] == "2026-10-13"
    usage = [entry for entry in manifest["files"] if entry["archive_path"] == "diagnostics/model_usage.json"]
    assert usage and "未按日期裁剪" in usage[0]["detail"]
    assert any("整份包含" in note for note in manifest["notes"])


def test_rotated_log_shards_are_kept_but_unlisted_files_are_not(tmp_path: Path) -> None:
    """复用既有轮转结果；命名白名单之外的文件不导出。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    target = tmp_path / "logs.zip"
    assert service.export(service.plan(full_scope()), FeedbackText("", "", ""), target).ok
    names = archive_names(target)

    in_range_prefix = f"logs/development/{RUN_DIR_IN_RANGE}/"
    assert f"{in_range_prefix}events.jsonl" in names
    assert f"{in_range_prefix}events.000001.jsonl" in names
    assert f"{in_range_prefix}runtime.log" in names
    assert not any(name.endswith("notes.txt") for name in names)
    assert not any(RUN_DIR_OUT_OF_RANGE in name for name in names)

    manifest = json.loads(read_zip(target)["manifest.json"].decode("utf-8"))
    outside = [entry for entry in manifest["not_included"]
               if entry["archive_path"] == "logs/development/"]
    assert outside and "不在所选日期范围内" in outside[0]["detail"]


def test_development_logs_are_declared_as_fingerprints_only(tmp_path: Path) -> None:
    """日志只是摘要与指纹，导出说明必须写明无法还原完整模型输入输出。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    target = tmp_path / "notes.zip"
    plan = service.plan(full_scope())
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    members = read_zip(target)

    assert any("无法还原完整模型输入输出" in note for note in plan.notes)
    readme = members["README.txt"].decode("utf-8")
    assert "无法还原完整模型输入输出或工具原文" in readme
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    assert any("无法还原完整模型输入输出" in item for item in manifest["limitations"])


def test_record_associations_are_preserved_and_never_invented(tmp_path: Path) -> None:
    """session_id、trace_id、uid 原样保留；不伪造不存在的关联。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    target = tmp_path / "assoc.zip"
    plan = service.plan(full_scope(include_chat=True, include_memory=True))
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    members = read_zip(target)

    chat = json.loads(members["chat/chat_history.json"].decode("utf-8"))
    sessions = {session["session_id"]: session for session in chat["sessions"]}
    assert set(sessions) == {"sess-in-range"}
    in_range = sessions["sess-in-range"]
    assert [message["message_id"] for message in in_range["messages"]] == ["m1", "m2"]
    assert {message["trace_id"] for message in in_range["messages"]} == {"trace-aaa"}
    # 范围外的会话是“没进来”，不是被改写成范围内的时间混进来。
    summaries = json.loads(members["chat/chat_summaries.json"].decode("utf-8"))
    assert set(summaries["summaries"]) == {"sess-in-range"}

    growth = json.loads(members["growth/growth_log.json"].decode("utf-8"))
    assert growth["entries"]["2026-10-10"]["uid"] == "review_2026-10-10"

    with zipfile.ZipFile(target) as archive:
        events = archive.read(f"logs/development/{RUN_DIR_IN_RANGE}/events.jsonl").decode("utf-8")
    assert "trace-aaa" in events


# ------------------------------------------------------------ 缺失、损坏与关联


def test_missing_files_are_listed_as_missing_not_silently_dropped(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    (root / "data" / "private" / "growth_log.json").unlink()
    (root / "memory.json").unlink()
    service = make_service(root)

    plan = service.plan(full_scope(include_memory=True))
    missing = {entry.archive_path for entry in plan.entries if entry.status == STATUS_MISSING}
    assert missing == {"growth/growth_log.json", "memory/memory.json"}

    target = tmp_path / "missing.zip"
    outcome = service.export(plan, FeedbackText("", "", ""), target)
    assert outcome.ok and target.exists()
    manifest = json.loads(read_zip(target)["manifest.json"].decode("utf-8"))
    listed = {entry["archive_path"] for entry in manifest["not_included"]}
    assert {"growth/growth_log.json", "memory/memory.json"} <= listed
    assert not any(name.startswith("memory/") for name in archive_names(target))


def test_corrupt_json_is_reported_and_never_written_into_the_zip(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    (root / "data" / "private" / "chat_history.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    service = make_service(root)

    plan = service.plan(full_scope(include_chat=True))
    corrupt = [entry for entry in plan.entries
               if entry.archive_path == "chat/chat_history.json"]
    assert corrupt and corrupt[0].status == STATUS_UNREADABLE
    assert corrupt[0].detail

    target = tmp_path / "corrupt.zip"
    outcome = service.export(plan, FeedbackText("", "", ""), target)
    assert outcome.ok
    names = archive_names(target)
    assert "chat/chat_history.json" not in names
    manifest = json.loads(read_zip(target)["manifest.json"].decode("utf-8"))
    reported = [entry for entry in manifest["not_included"]
                if entry["archive_path"] == "chat/chat_history.json"]
    assert reported and reported[0]["status"] == STATUS_UNREADABLE


def test_undated_records_are_counted_and_explained(tmp_path: Path) -> None:
    """无法按日期关联的记录如实计入说明，不悄悄丢掉也不猜时间。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    plan = service.plan(full_scope(include_chat=True))
    assert any("没有可解析的时间" in note and "1 条" in note for note in plan.notes)

    target = tmp_path / "undated.zip"
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    chat = json.loads(read_zip(target)["chat/chat_history.json"].decode("utf-8"))
    assert "sess-no-time" not in {session["session_id"] for session in chat["sessions"]}


def test_out_of_range_selection_never_widens_the_date_filter(tmp_path: Path) -> None:
    """范围内没有内容时报告为空，不为了“有东西可导”而放宽日期。

    唯一仍然包含的是累计汇总的用量文件；它必须在清单里被明确标注“未按日期裁剪”，
    不能不动声色地整份塞进来。
    """
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    plan = service.plan(full_scope(start=date(2026, 1, 1), end=date(2026, 1, 7)))
    dated = [entry for entry in plan.included if entry.category != CATEGORY_DIAGNOSTICS]
    assert dated == []
    included_usage = [entry for entry in plan.included
                      if entry.archive_path == "diagnostics/model_usage.json"]
    assert included_usage and "未按日期裁剪" in included_usage[0].detail

    target = tmp_path / "narrow-range.zip"
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    names = archive_names(target)
    assert "growth/growth_log.json" not in names
    assert "diagnostics/interaction_diagnostics.jsonl" not in names
    assert "diagnostics/model_usage.json" in names

    # 结束日期早于开始日期：整份清单为空，导出直接拒绝。
    reversed_scope = full_scope(start=date(2026, 10, 13), end=date(2026, 10, 7))
    assert reversed_scope.validate()
    blocked = service.plan(reversed_scope)
    assert not blocked.included and blocked.notes
    outcome = service.export(blocked, FeedbackText("", "", ""), tmp_path / "empty.zip")
    assert outcome.ok is False and outcome.error
    assert not (tmp_path / "empty.zip").exists()


# ------------------------------------------------------------ 凭据与越界路径


def test_credential_files_are_never_collected_even_when_everything_is_selected(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    target = tmp_path / "creds.zip"
    plan = service.plan(full_scope(include_chat=True, include_memory=True))
    assert service.export(plan, FeedbackText("", "", ""), target).ok

    names = " ".join(archive_names(target))
    assert "llm_secrets" not in names
    assert ".env" not in names
    assert "pet_config" not in names

    blob = b"".join(read_zip(target).values())
    assert b"sk-SYNTHETICLEAK" not in blob


def test_credential_fields_and_recognizable_values_are_masked(tmp_path: Path) -> None:
    """结构化字段按键名移除凭据，正文里可识别的凭据也会被遮蔽。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    _write_json(root / "data" / "private" / "action_log.json", {
        "version": 1,
        "days": {"2026-10-10": {"api_key": "sk-SYNTHETICLEAKVALUE0003", "actions": []}},
    })
    _write_json(root / "data" / "private" / "growth_log.json", {
        "version": 1,
        "entries": {"2026-10-10": {
            "uid": "review_2026-10-10",
            "review": {"summary": "把 sk-SYNTHETICLEAKVALUE0004 贴进了复盘"},
        }},
    })
    _write_json(root / "data" / "private" / "chat_history.json", {
        "version": 1,
        "sessions": [{
            "session_id": "sess-in-range", "started_at": "2026-10-10T09:00:00",
            "messages": [{
                "message_id": "m1", "role": "user",
                "content": "Authorization: Bearer sk-SYNTHETICLEAKVALUE0005",
                "created_at": "2026-10-10T09:00:00",
            }],
        }],
    })

    target = tmp_path / "masked.zip"
    plan = service.plan(full_scope(include_chat=True))
    outcome = service.export(plan, FeedbackText("", "", ""), target)
    assert outcome.ok
    assert outcome.redacted_fields >= 2

    members = read_zip(target)
    blob = b"".join(members.values())
    assert b"sk-SYNTHETICLEAK" not in blob

    actions = json.loads(members["growth/action_log.json"].decode("utf-8"))
    assert actions["days"]["2026-10-10"]["api_key"] == "[已移除的凭据字段]"
    chat = json.loads(members["chat/chat_history.json"].decode("utf-8"))
    content = chat["sessions"][0]["messages"][0]["content"]
    assert "[已遮蔽]" in content and "Bearer" not in content
    assert chat["sessions"][0]["session_id"] == "sess-in-range"


def test_ordinary_content_is_not_mistaken_for_a_credential(tmp_path: Path) -> None:
    """遮蔽不能伤到正常内容：用量计数和普通英文词必须原样保留。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)

    normal_text = "这周的 risk-management 复盘写完了，风险清单也更新了。"
    _write_json(root / "data" / "private" / "chat_history.json", {
        "version": 1,
        "sessions": [{
            "session_id": "sess-in-range", "started_at": "2026-10-10T09:00:00",
            "messages": [{"message_id": "m1", "role": "user", "content": normal_text,
                          "created_at": "2026-10-10T09:00:00"}],
        }],
    })

    target = tmp_path / "plain.zip"
    plan = service.plan(full_scope(include_chat=True))
    outcome = service.export(plan, FeedbackText("", "", ""), target)
    assert outcome.ok and outcome.redacted_fields == 0

    members = read_zip(target)
    chat = json.loads(members["chat/chat_history.json"].decode("utf-8"))
    assert chat["sessions"][0]["messages"][0]["content"] == normal_text

    usage = json.loads(members["diagnostics/model_usage.json"].decode("utf-8"))
    assert usage["totals"] == {"calls": 2, "prompt_tokens": 30, "completion_tokens": 12}
    assert "已移除的凭据字段" not in members["diagnostics/model_usage.json"].decode("utf-8")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    """符号链接／junction 指向实例根之外时一律拒绝，不把外部文件装进 ZIP。"""
    root = tmp_path / "instance"
    build_instance(root)
    outside = tmp_path / "outside-secret.json"
    _write_json(outside, {"content": "实例根之外的内容"})
    link = root / "data" / "private" / "chat_history.json"
    link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")

    service = make_service(root)
    plan = service.plan(full_scope(include_chat=True))
    entry = [item for item in plan.entries if item.archive_path == "chat/chat_history.json"][0]
    assert entry.status == STATUS_SKIPPED
    assert "符号链接" in entry.detail

    target = tmp_path / "escape.zip"
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    names = archive_names(target)
    assert "chat/chat_history.json" not in names
    assert "实例根之外的内容".encode("utf-8") not in b"".join(read_zip(target).values())


def test_a_directory_swapped_in_for_a_file_is_not_collected(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    victim = root / "data" / "private" / "growth_log.json"
    victim.unlink()
    victim.mkdir()
    (victim / "inner.json").write_text("{}", encoding="utf-8")
    service = make_service(root)

    plan = service.plan(full_scope())
    entry = [item for item in plan.entries if item.archive_path == "growth/growth_log.json"][0]
    assert entry.status == STATUS_SKIPPED
    target = tmp_path / "dir.zip"
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    assert "growth/growth_log.json" not in archive_names(target)


# ---------------------------------------------------------------- 写入与元信息


def test_write_failure_reports_failure_and_leaves_no_partial_file(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root)
    plan = service.plan(full_scope())

    blocker = tmp_path / "blocker"
    blocker.write_text("这是一个文件，不是目录", encoding="utf-8")
    outcome = service.export(plan, FeedbackText("", "", ""), blocker / "out.zip")

    assert outcome.ok is False
    assert outcome.error
    assert not (blocker / "out.zip").exists()
    assert not (blocker / "out.zip.part").exists()
    assert [path.name for path in tmp_path.iterdir() if "part" in path.name] == []


def test_manifest_carries_run_meta_and_no_local_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "instance"
    build_instance(root)
    service = FeedbackExportService(
        root, RunMeta(version="0.9.0-synthetic", instance_id="inst-0123456789abcdef", source=SOURCE_REAL)
    )

    target = tmp_path / "meta.zip"
    plan = service.plan(full_scope(include_chat=True, include_memory=True))
    assert service.export(plan, FeedbackText("问题", "预期", "实际"), target).ok
    members = read_zip(target)

    feedback = json.loads(members["feedback.json"].decode("utf-8"))
    assert feedback["app_version"] == "0.9.0-synthetic"
    assert feedback["instance_id"] == "inst-0123456789abcdef"
    assert feedback["source"] == SOURCE_REAL
    assert feedback["feedback"] == {"problem": "问题", "expected": "预期", "actual": "实际"}

    environment = json.loads(members["environment.json"].decode("utf-8"))
    assert environment["instance_id"] == "inst-0123456789abcdef"
    assert environment["environment"]["platform"]
    assert environment["environment"]["python"]

    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    assert manifest["source"] == SOURCE_REAL
    assert manifest["instance_id"] == "inst-0123456789abcdef"
    assert {entry["archive_path"] for entry in manifest["files"]} == set(
        name for name in members if name not in {"manifest.json", "feedback.json", "environment.json", "README.txt"}
    )
    # 清单只写实例根内的相对位置，本机绝对路径不进入包内。
    assert all(not Path(entry["source"]).is_absolute() for entry in manifest["files"])

    blob = b"".join(members.values())
    for leak in (str(tmp_path).encode("utf-8"),
                 str(tmp_path).replace("\\", "/").encode("utf-8"),
                 str(Path.home()).encode("utf-8")):
        assert leak not in blob


def test_engineering_source_is_not_described_as_real_usage(tmp_path: Path) -> None:
    """工程模拟数据必须自报来源，不能被读成真实用户的使用证据。"""
    root = tmp_path / "instance"
    build_instance(root)
    service = make_service(root, source=SOURCE_ENGINEERING)

    target = tmp_path / "engineering.zip"
    plan = service.plan(full_scope())
    assert service.export(plan, FeedbackText("", "", ""), target).ok
    members = read_zip(target)

    feedback = json.loads(members["feedback.json"].decode("utf-8"))
    assert feedback["source"] == SOURCE_ENGINEERING
    assert feedback["source_label"] == "工程或模拟"

    readme = members["README.txt"].decode("utf-8")
    assert "来源标记：工程或模拟" in readme
    assert "不能当作真实用户连续使用的证据" in readme

    real = make_service(root, source=SOURCE_REAL)
    real_target = tmp_path / "real.zip"
    assert real.export(real.plan(full_scope()), FeedbackText("", "", ""), real_target).ok
    real_readme = read_zip(real_target)["README.txt"].decode("utf-8")
    assert "来源标记：真实使用" in real_readme
    assert "不能当作真实用户连续使用的证据" not in real_readme


def test_default_scope_covers_a_week_ending_today() -> None:
    scope = default_scope(date(2026, 10, 13))
    assert scope.start == date(2026, 10, 7)
    assert scope.end == date(2026, 10, 13)
    assert scope.validate() is None
    assert CATEGORY_CHAT not in scope.selected_categories()
    assert CATEGORY_MEMORY not in scope.selected_categories()
