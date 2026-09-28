from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.inspect_recent_interactions import inspect_recent_interactions, main, render_text


def _id(value):
    if not value:
        return ""
    prefix = value.split("_", 1)[0]
    if prefix not in {"run", "trace", "session", "task", "action"}:
        prefix = "trace"
    return prefix + "_" + hashlib.sha256(value.encode()).hexdigest()[:32]


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _history(tmp_path, messages, *, session_id="session_one", stamp="2026-09-18T10:00:00"):
    path = tmp_path / "history.json"
    _write_json(path, {"version": 1, "sessions": [{"session_id": _id(session_id), "started_at": stamp, "updated_at": stamp, "messages": messages}]})
    return path


def _message(trace="trace_one", *, run="run_one", role="user", content="生成并保存今天的复盘", stamp="2026-09-18T10:00:00"):
    return {"role": role, "content": content, "created_at": stamp, "metadata": {"trace_id": _id(trace), "run_id": _id(run)}}


def _event(trace="trace_one", *, event="chat_submitted", source="chat", run="run_one", session="session_one", stamp="2026-09-18T10:00:00", sequence=1, **fields):
    return {"run_id": _id(run), "trace_id": _id(trace), "session_id": _id(session), "source": source, "event": event, "timestamp": stamp, "sequence": sequence, "fields": fields}


def _logs(tmp_path, events, *, malformed=False):
    root = tmp_path / "logs"
    path = root / "run_one" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    if malformed:
        lines.append('{"unfinished":')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _fingerprints(root: Path):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}


def test_repeated_identical_messages_match_only_full_trace_identity(tmp_path):
    history = _history(tmp_path, [_message("trace_one"), _message("trace_two"), _message("trace_one", run="run_two")])
    logs = _logs(tmp_path, [
        _event(event="tool_result", success=False, reason_code="schema_rejected"),
        _event("trace_two", event="reply_displayed", success=True),
        _event(run="run_two", event="reply_displayed", success=True),
    ])
    report = inspect_recent_interactions(history, logs)
    turns = report["chat_turns"]
    assert len(turns) == 3
    assert turns[0]["first_failure"]["reason_code"] == "schema_rejected"
    assert turns[1]["outcome"] == "displayed"
    assert turns[1]["first_failure"] is None
    assert turns[2]["run_id"] == _id("run_two")


def test_session_switch_does_not_reassign_async_reply(tmp_path):
    history = _history(tmp_path, [_message(), _message(role="assistant")])
    logs = _logs(tmp_path, [
        _event(event="reply_displayed", session="session_other"),
        _event(event="reply_not_displayed", status="blocked", reason_code="session_changed"),
    ])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert turn["outcome"] == "not_displayed"
    assert len(turn["events"]) == 1
    assert turn["first_failure"]["reason_code"] == "session_changed"


def test_ui_save_is_independent_not_proof_of_chat_success(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [
        _event(event="semantic_decision", status="rejected", reason_code="schema_rejected"),
        _event(event="reply_displayed"),
        _event("ui_trace", event="ui_operation_started", source="ui_growth_save_review", session=""),
        _event("ui_trace", event="ui_operation_finished", source="ui_growth_save_review", session="", success=True, persisted=True, record_id="review_2026-09-18", date="2026-09-18"),
    ])
    report = inspect_recent_interactions(history, logs)
    assert report["chat_turns"][0]["first_failure"]["reason_code"] == "schema_rejected"
    assert report["independent_operations"][0]["source"] == "ui_growth_save_review"
    assert report["independent_operations"][0]["outcome"] == "completed"
    assert all(event["source"] == "chat" for event in report["chat_turns"][0]["events"])
    assert "不计入聊天成功" in render_text(report)


def test_ui_event_with_erroneously_reused_chat_id_still_not_merged(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [
        _event(event="chat_submitted"),
        _event(event="ui_operation_finished", source="ui_memory_delete", success=True),
    ])
    report = inspect_recent_interactions(history, logs)
    assert report["chat_turns"][0]["outcome"] == "interrupted_or_unfinished"
    assert report["independent_operations"][0]["source"] == "ui_memory_delete"


def test_legacy_history_gets_uncertain_candidates_never_tool_success(tmp_path):
    message = _message()
    message.pop("metadata")
    history = _history(tmp_path, [message, dict(message)])
    logs = _logs(tmp_path, [_event(event="tool_result", success=True, persisted=True)])
    report = inspect_recent_interactions(history, logs)
    assert report["chat_turns"] == []
    assert len(report["uncertain_candidates"]) == 2
    assert report["uncertain_candidates"][0]["candidates"][0]["certainty"] == "uncertain_time_only"
    assert report["limits"]["time_candidates_are_not_matches"] is True


def test_malformed_last_log_line_retains_completed_events_and_gap(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="reply_displayed")], malformed=True)
    report = inspect_recent_interactions(history, logs)
    assert report["chat_turns"][0]["outcome"] == "displayed"
    assert {"kind": "malformed_event_line", "file": "event_file_1", "line": 2} in report["evidence_gaps"]


@pytest.mark.parametrize("events,expected", [
    ([_event(event="turn_prepare_finished", deferred=True)], "interrupted_or_unfinished"),
    ([_event(event="turn_service_finished", terminal=True)], "service_completed_display_unknown"),
    ([_event(event="turn_service_finished"), _event(event="reply_displayed")], "displayed"),
    ([], "missing_log"),
])
def test_incomplete_and_display_lifecycle_are_distinct(tmp_path, events, expected):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, events)
    assert inspect_recent_interactions(history, logs)["chat_turns"][0]["outcome"] == expected


def test_reason_code_without_explicit_failure_does_not_invent_failure(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="snapshot_state", reason_code="new_unfamiliar_code", status="pending"), _event(event="reply_displayed")])
    assert inspect_recent_interactions(history, logs)["chat_turns"][0]["first_failure"] is None


def test_default_report_never_dumps_body_prompt_tooldata_or_private_paths(tmp_path):
    history = _history(tmp_path, [_message(content="私人对话全文以及身份暗号不应外泄")])
    event = _event(event="tool_result", success=False, reason_code="schema_rejected")
    event["fields"].update({"prompt": "完整提示词秘密", "data": {"identity": "身份秘密"}, "error": "C:\\private\\memory.json", "request_id": "sk-testcredential"})
    logs = _logs(tmp_path, [event])
    report = inspect_recent_interactions(history, logs)
    output = json.dumps(report, ensure_ascii=False) + render_text(report)
    for private in ("私人对话", "身份暗号", "完整提示词", "身份秘密", "C:\\private", "sk-testcredential", str(tmp_path)):
        assert private not in output
    assert report["chat_turns"][0]["first_failure"]["reason_code"] == "schema_rejected"


@pytest.mark.parametrize("content,expected", [
    ("我的身份暗号是不能公开的", "[敏感内容已省略]"),
    ("api_key=topsecret", "[敏感内容已省略]"),
    ("路径 C:\\private\\history.json", "路径 [本地路径]"),
    ("x" * 200, "x" * 120),
])
def test_explicit_excerpts_are_redacted_and_bounded(tmp_path, content, expected):
    history = _history(tmp_path, [_message(content=content)])
    logs = _logs(tmp_path, [_event(event="reply_displayed")])
    report = inspect_recent_interactions(history, logs, excerpts=True)
    assert report["chat_turns"][0]["messages"][0]["excerpt"] == expected


def test_missing_history_retains_log_only_evidence_without_writing(tmp_path):
    history = tmp_path / "disabled_history.json"
    logs = _logs(tmp_path, [_event(event="turn_service_finished", terminal=True)])
    before = _fingerprints(tmp_path)
    report = inspect_recent_interactions(history, logs)
    assert report["chat_turns"] == []
    assert report["log_only_operations"][0]["outcome"] == "service_completed_display_unknown"
    assert _fingerprints(tmp_path) == before
    assert not history.exists()


def test_current_review_file_does_not_override_failed_chat(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [
        _event(event="tool_result", success=False, reason_code="invalid_tool_result"),
        _event("ui_trace", event="ui_operation_finished", source="ui_growth_save_review", success=True, persisted=True, record_id="review_2026-09-18", date="2026-09-18"),
    ])
    data = tmp_path / "data"
    _write_json(data / "growth_log.json", {"entries": {"2026-09-18": {"uid": "review_2026-09-18", "revision": 1, "review": {"private": "不能输出的成长正文"}}}})
    _write_json(data / "today_plan.json", {"days": {}})
    _write_json(data / "action_log.json", {"days": {}})
    _write_json(data / "memory.json", {"private": "不要读取正式记忆"})
    before = _fingerprints(tmp_path)
    report = inspect_recent_interactions(history, logs, data_dir=data)
    assert report["chat_turns"][0]["first_failure"]["reason_code"] == "invalid_tool_result"
    assert report["current_data"] == [{"store": "review", "date": "2026-09-18", "record_id": "review_2026-09-18", "proof_scope": "current_snapshot_only", "revision": 1}]
    assert "成长正文" not in json.dumps(report, ensure_ascii=False)
    assert _fingerprints(tmp_path) == before


def test_since_and_session_selection_and_json_cli(tmp_path, capsys):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="reply_displayed")])
    before = _fingerprints(tmp_path)
    assert main(["--history", str(history), "--logs", str(logs), "--session-id", _id("session_one"), "--since", "2026-09-18", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["selected_sessions"][0]["session_id"] == _id("session_one")
    assert _fingerprints(tmp_path) == before
    assert inspect_recent_interactions(history, logs, since="2026-09-19")["selected_sessions"] == []


@pytest.mark.parametrize("kwargs", [{"sessions": 0}, {"since": "not-a-date"}])
def test_invalid_filters_are_rejected_without_writes(tmp_path, kwargs):
    history = _history(tmp_path, [])
    logs = _logs(tmp_path, [])
    before = _fingerprints(tmp_path)
    with pytest.raises(ValueError):
        inspect_recent_interactions(history, logs, **kwargs)
    assert _fingerprints(tmp_path) == before


def test_untraceable_legacy_log_evidence_is_retained_not_silently_discarded(tmp_path):
    history = _history(tmp_path, [])
    logs = _logs(tmp_path, [{"timestamp": "2026-09-18T10:00:00", "event": "tool_result", "fields": {"success": False, "reason_code": "schema_rejected"}}])
    report = inspect_recent_interactions(history, logs)
    assert report["unassociated_events"][0]["fields"]["reason_code"] == "schema_rejected"
    assert {"kind": "events_without_trace_metadata", "count": 1} in report["evidence_gaps"]


def test_untrusted_ascii_labels_and_identifiers_are_not_reexposed(tmp_path):
    history = _history(tmp_path, [_message()])
    event = _event(event="secret_event_identity", source="private_secret_source", reason_code="secret_reason_identity", status="secret_status_identity", intent="secret_intent_identity", tool="secret_tool_identity", request_id="private_credential_identifier", success=True)
    logs = _logs(tmp_path, [event])
    report = inspect_recent_interactions(history, logs)
    output = json.dumps(report)
    for marker in ("secret_event_identity", "private_secret_source", "secret_reason_identity", "secret_status_identity", "secret_intent_identity", "secret_tool_identity", "private_credential_identifier"):
        assert marker not in output
    assert report["chat_turns"][0]["outcome"] == "missing_log"


def test_excerpt_optin_does_not_reconstruct_entire_selected_conversation(tmp_path):
    history = _history(tmp_path, [_message(f"trace_{index}", content=f"fragment_{index}") for index in range(20)])
    logs = _logs(tmp_path, [])
    report = inspect_recent_interactions(history, logs, excerpts=True)
    messages = [message for turn in report["chat_turns"] for message in turn["messages"]]
    assert len(messages) == 20
    assert sum("excerpt" in message for message in messages) == 6
    assert "fragment_0" not in json.dumps(report)


def test_real_logger_schema_and_rotated_segments_correlate_without_writes(tmp_path):
    from modules.development_log import DevelopmentLog

    logger = DevelopmentLog(tmp_path / "development", enabled=True, max_file_bytes=1024)
    trace = logger.new_trace(_id("session_one"), "chat")
    logger.event(event_name="run_started")
    for _ in range(8):
        logger.event(trace, "model_request_started", request_hash="a" * 64)
    logger.event(trace, "tool_result", success=False, status="failed", reason_code="schema_rejected", tool="save_daily_review")
    logger.event(trace, "turn_service_finished", terminal=True)
    logger.event(trace, "reply_displayed")
    message = _message()
    message["metadata"] = trace.to_metadata()
    history = _history(tmp_path, [message])
    before = _fingerprints(tmp_path)
    report = inspect_recent_interactions(history, tmp_path / "development")
    assert len(list((tmp_path / "development").rglob("events*.jsonl"))) > 1
    assert report["chat_turns"][0]["trace_id"] == trace.trace_id
    assert report["chat_turns"][0]["outcome"] == "displayed"
    assert report["chat_turns"][0]["first_failure"]["reason_code"] == "schema_rejected"
    assert report["run_events"][0]["event"] == "run_started"
    assert not any(gap["kind"] == "events_without_trace_metadata" for gap in report["evidence_gaps"])
    assert _fingerprints(tmp_path) == before


def test_explicit_missing_session_does_not_return_other_sessions_logs(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="reply_displayed")])
    report = inspect_recent_interactions(history, logs, session_id=_id("session_missing"))
    assert report["selected_sessions"] == []
    assert report["log_only_operations"] == []
    assert report["independent_operations"] == []


def test_excerpt_optin_never_outputs_system_prompt(tmp_path):
    history = _history(tmp_path, [_message(role="system", content="完整系统提示不应输出")])
    logs = _logs(tmp_path, [_event(event="reply_displayed")])
    report = inspect_recent_interactions(history, logs, excerpts=True)
    assert "完整系统提示" not in json.dumps(report, ensure_ascii=False)


def test_parameter_validation_and_safe_snapshot_statistics_remain_visible(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [
        _event(event="snapshot_state", snapshot_kind="suggestion", state="awaiting_clarification", mode="clarify", retry_count=1, total=3, done=1, pending=2, action_count=1),
        _event(event="parameter_validation", success=False, reason_code="schema_rejected"),
    ])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert turn["first_failure"]["stage"] == "parameter_validation"
    assert turn["events"][0]["fields"]["snapshot_kind"] == "suggestion"
    assert turn["events"][0]["fields"]["state"] == "awaiting_clarification"
    assert turn["events"][0]["fields"]["mode"] == "clarify"
    assert turn["events"][0]["fields"]["total"] == 3


def test_reply_protection_is_first_deviation_not_fake_tool_failure(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="reply_guard_applied", rule_code="confirmation_without_pending", guard_changed=True), _event(event="reply_displayed")])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert turn["outcome"] == "displayed"
    assert turn["first_failure"] is None
    assert turn["first_deviation"]["stage"] == "reply_guard"
    assert turn["first_deviation"]["kind"] == "recorded_protection_not_proof_of_bug"
    assert "不等于程序缺陷" in render_text(inspect_recent_interactions(history, logs))


def test_legacy_custom_session_remains_available_as_safe_time_candidate(tmp_path):
    history = tmp_path / "history.json"
    message = _message()
    message.pop("metadata")
    private_sid = "legacy-private-identity-session"
    _write_json(history, {"sessions": [{"session_id": private_sid, "started_at": "2026-09-18T10:00:00", "updated_at": "2026-09-18T10:00:00", "messages": [message]}]})
    logs = _logs(tmp_path, [_event(event="reply_displayed")])
    report = inspect_recent_interactions(history, logs, session_id=private_sid)
    assert len(report["selected_sessions"]) == 1
    assert len(report["uncertain_candidates"]) == 1
    assert private_sid not in json.dumps(report)


def test_redacted_custom_session_still_matches_full_run_trace_and_session_hash(tmp_path):
    from modules.development_log import DevelopmentLog

    private_sid = "legacy-private-identity-session"
    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    trace = logger.new_trace(private_sid, "chat")
    logger.event(trace, "reply_displayed")
    history = tmp_path / "history.json"
    message = _message()
    message["metadata"] = trace.to_metadata()
    _write_json(history, {"sessions": [{"session_id": private_sid, "updated_at": "2026-09-18T10:00:00", "messages": [message]}]})
    report = inspect_recent_interactions(history, tmp_path / "development")
    assert report["chat_turns"][0]["certainty"] == "exact_metadata_match"
    assert private_sid not in json.dumps(report)


def test_final_tool_evidence_is_not_a_second_execution_event(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="tool_started"), _event(event="tool_result", success=True), _event(event="tool_response_evidence", success=True, verified=True), _event(event="reply_displayed")])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert sum(event["event"] == "tool_started" for event in turn["events"]) == 1
    assert sum(event["event"] == "tool_result" for event in turn["events"]) == 1
    assert turn["events"][2]["stage"] == "result_verification"


def test_trusted_source_location_is_kept_without_private_path(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="exception", success=False, error_type="OSError", filename="conversation_service.py", lineno=120)])
    fields = inspect_recent_interactions(history, logs)["chat_turns"][0]["events"][0]["fields"]
    assert fields["filename"] == "conversation_service.py"
    assert fields["lineno"] == 120


@pytest.mark.parametrize("status", ["confirmation_required", "clarification_required", "clarification", "deferred", "cancelled", "unchanged", "already_processed"])
def test_normal_nonterminal_tool_result_is_not_execution_failure(tmp_path, status):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="tool_result", success=False, status=status), _event(event="reply_displayed")])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert turn["first_failure"] is None
    assert turn["first_deviation"] is None
    assert turn["events"][0]["fields"]["status"] == status


def test_huge_corrupt_integer_is_filtered_not_a_reader_exception(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [_event(event="tool_result", count=10 ** 1000)])
    report = inspect_recent_interactions(history, logs)
    assert "count" not in report["chat_turns"][0]["events"][0]["fields"]


def test_per_run_sequence_preserves_execution_order_when_clock_moves_backwards(tmp_path):
    history = _history(tmp_path, [_message()])
    logs = _logs(tmp_path, [
        _event(event="parameter_validation", sequence=1, stamp="2026-09-18T10:01:00", success=False, reason_code="schema_rejected"),
        _event(event="tool_result", sequence=2, stamp="2026-09-18T10:00:00", success=False, reason_code="invalid_tool_result"),
    ])
    turn = inspect_recent_interactions(history, logs)["chat_turns"][0]
    assert [event["sequence"] for event in turn["events"]] == [1, 2]
    assert turn["first_failure"]["reason_code"] == "schema_rejected"


def test_real_logger_failed_service_is_not_reported_as_completed(tmp_path):
    from modules.development_log import DevelopmentLog

    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    trace = logger.new_trace(_id("session_one"), "chat")
    logger.event(trace, "turn_service_finished", status="failed", terminal=True)
    message = _message()
    message["metadata"] = trace.to_metadata()
    history = _history(tmp_path, [message])
    turn = inspect_recent_interactions(history, tmp_path / "development")["chat_turns"][0]
    assert turn["outcome"] == "service_failed_display_unknown"
    assert turn["first_failure"]["event"] == "turn_service_finished"


@pytest.mark.parametrize("status", ["created", "existing", "empty", "failed"])
def test_real_logger_startup_backfill_finished_is_not_reported_interrupted(tmp_path, status):
    from modules.development_log import DevelopmentLog

    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    trace = logger.new_trace("", "startup_backfill")
    logger.event(trace, "startup_backfill_started")
    logger.event(trace, "startup_backfill_finished", status=status, success=status != "failed")
    report = inspect_recent_interactions(tmp_path / "disabled_history.json", tmp_path / "development")
    operation = report["independent_operations"][0]
    assert operation["outcome"] == status
    assert operation["source"] == "startup_backfill"


def test_old_saved_history_does_not_hide_new_unsaved_session_log_evidence(tmp_path):
    history = _history(tmp_path, [_message()], stamp="2026-09-17T10:00:00")
    logs = _logs(tmp_path, [
        _event("trace_new", session="session_unsaved", stamp="2026-09-18T18:00:00", event="semantic_decision", success=False, status="validation_error", reason_code="schema_rejected"),
        _event("trace_new", session="session_unsaved", stamp="2026-09-18T18:00:00", event="reply_displayed"),
        _event("trace_run", session="", stamp="2026-09-18T18:00:00", event="run_started"),
    ])
    report = inspect_recent_interactions(history, logs)
    assert report["log_only_operations"][0]["trace_id"] == _id("trace_new")
    assert report["log_only_operations"][0]["certainty"] == "log_only_not_linked_to_selected_chat"
    assert report["log_only_operations"][0]["first_failure"]["reason_code"] == "schema_rejected"
    assert all(operation["trace_id"] != _id("trace_run") for operation in report["log_only_operations"])
    assert report["chat_turns"][0]["outcome"] == "missing_log"
    assert inspect_recent_interactions(history, logs, session_id=_id("session_one"))["log_only_operations"] == []


def test_recent_ui_and_startup_evidence_outside_old_chat_window_remains_bounded_and_independent(tmp_path):
    history = _history(tmp_path, [_message()], stamp="2026-09-17T10:00:00")
    events = [_event(f"trace_old_{index}", session="session_unsaved", stamp=f"2026-09-18T18:00:{index:02d}", event="reply_displayed") for index in range(40)]
    events.extend([
        _event("trace_button", source="ui_growth_save_review", session="", stamp="2026-09-18T18:01:00", event="ui_operation_finished", success=True),
        _event("trace_backfill", source="startup_backfill", session="", stamp="2026-09-18T18:02:00", event="startup_backfill_finished", status="created", success=True),
    ])
    logs = _logs(tmp_path, events)
    report = inspect_recent_interactions(history, logs)
    assert len(report["log_only_operations"]) + len(report["independent_operations"]) == 36
    assert {operation["trace_id"] for operation in report["independent_operations"]} == {_id("trace_button"), _id("trace_backfill")}
    assert all(operation["certainty"] == "independent_operation_not_chat_execution" for operation in report["independent_operations"])
    assert report["chat_turns"][0]["outcome"] == "missing_log"
    scoped = inspect_recent_interactions(history, logs, session_id=_id("session_one"))
    assert scoped["independent_operations"] == []
    assert scoped["log_only_operations"] == []
