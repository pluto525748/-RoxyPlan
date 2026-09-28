from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from modules import development_log
from modules.development_log import DevelopmentLog, TraceContext


def read_events(logger):
    return sorted([json.loads(line) for path in sorted(logger.run_directory.glob("events*.jsonl"))
                   for line in path.read_text(encoding="utf-8").splitlines()],
                  key=lambda event: event["sequence"])


def test_default_and_disabled_logger_do_not_touch_files(tmp_path):
    logger = DevelopmentLog(tmp_path / "absent")
    trace = logger.new_trace()
    with logger.bind(trace):
        assert logger.current_trace() == trace
        assert logger.event(event_name="chat_submitted", user_text="private") is None
    logger.record_exception(trace, RuntimeError("private"))
    assert logger.current_trace() is None
    assert not logger.directory.exists()
    assert development_log.DevelopmentLog().directory is None


def test_trace_is_frozen_and_metadata_does_not_reuse_request_id(tmp_path):
    logger = DevelopmentLog(tmp_path)
    trace = logger.new_trace("session_" + "a" * 32)
    assert trace.to_metadata() == {"run_id": logger.run_id, "trace_id": trace.trace_id}
    assert "request_id" not in trace.to_metadata()
    with pytest.raises(FrozenInstanceError):
        trace.trace_id = "different"


def test_event_is_readable_immediately_without_shutdown(tmp_path):
    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    trace = logger.new_trace("session_" + "a" * 32)
    record = logger.event(trace, "chat_submitted", status="started")
    assert read_events(logger) == [record]
    text = (logger.run_directory / "runtime.log").read_text(encoding="utf-8")
    assert "chat_submitted" in text and trace.trace_id in text
    assert record["source"] == "chat"
    assert record["session_id"] == trace.session_id


def test_repeated_phrase_has_distinct_trace_even_in_same_session(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    first = logger.new_trace("session_" + "b" * 32)
    second = logger.new_trace(first.session_id)
    phrase_hash = hashlib.sha256("相同问题".encode()).hexdigest()
    logger.event(first, "chat_submitted", user_text_hash=phrase_hash)
    logger.event(second, "chat_submitted", user_text_hash=phrase_hash)
    events = read_events(logger)
    assert events[0]["trace_id"] != events[1]["trace_id"]
    assert events[0]["fields"]["user_text_hash"] == events[1]["fields"]["user_text_hash"]


def test_chat_and_button_save_are_distinct_sources(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    chat = logger.new_trace("session_" + "c" * 32)
    button = logger.new_trace(chat.session_id, "ui_growth_save_review")
    logger.event(chat, "turn_service_finished", status="chat", persisted=False)
    logger.event(button, "ui_operation_finished", status="success", persisted=True,
                 record_id="review_2026-09-18", date="2026-09-18", revision=1)
    events = read_events(logger)
    assert events[0]["trace_id"] != events[1]["trace_id"]
    assert [event["source"] for event in events] == ["chat", "ui_growth_save_review"]
    assert [event["fields"]["persisted"] for event in events] == [False, True]


def test_two_startups_have_separate_run_directories(tmp_path):
    first = DevelopmentLog(tmp_path, enabled=True)
    second = DevelopmentLog(tmp_path, enabled=True)
    first.event(None, "run_started")
    second.event(None, "run_started")
    assert first.run_directory != second.run_directory
    assert first.run_id != second.run_id
    assert len(read_events(first)) == len(read_events(second)) == 1


def test_bind_restores_previous_trace_on_exception(tmp_path):
    logger = DevelopmentLog(tmp_path)
    first, second = logger.new_trace(), logger.new_trace()
    with logger.bind(first):
        with pytest.raises(ValueError), logger.bind(second):
            assert logger.current_trace() == second
            raise ValueError("private")
        assert logger.current_trace() == first
    assert logger.current_trace() is None


def test_thread_bound_traces_and_sequence_are_independent(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)

    def work(number):
        trace = logger.new_trace("session_" + f"{number:032x}")
        with logger.bind(trace):
            for _ in range(10):
                logger.event(event_name="tool_started", tool="save_daily_review")
        return trace

    with ThreadPoolExecutor(max_workers=4) as executor:
        traces = list(executor.map(work, range(4)))
    events = read_events(logger)
    assert len(events) == 40
    assert [event["sequence"] for event in events] == list(range(1, 41))
    for trace in traces:
        matched = [event for event in events if event["trace_id"] == trace.trace_id]
        assert len(matched) == 10
        assert {event["session_id"] for event in matched} == {trace.session_id}


def test_known_values_are_checked_against_existing_capability_catalog(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "semantic_decision", intent="save_review", tool="save_daily_review",
                 status="success", rule_code="schema_rejected", retry_count=1,
                 confirmation_pending=False)
    fields = read_events(logger)[0]["fields"]
    assert fields == {"intent": "save_review", "tool": "save_daily_review", "status": "success",
                      "rule_code": "schema_rejected", "retry_count": 1, "confirmation_pending": False}


@pytest.mark.parametrize("field", ["intent", "tool", "status", "reason_code", "rule_code", "provider", "model", "request_id", "message_id", "date", "filename", "response_hash", "total", "success"])
def test_secrets_disguised_as_safe_fields_are_redacted(tmp_path, field):
    logger = DevelopmentLog(tmp_path, enabled=True)
    secret = "sk-PRIVATE-SECRET-\\Users\\someone\\memory.json"
    logger.event(None, "tool_result", **{field: secret})
    for file in logger.run_directory.glob("*"):
        assert secret not in file.read_text(encoding="utf-8")
    assert read_events(logger)[0]["fields"][field]["redacted"] is True


def test_unknown_field_names_and_nested_payload_are_never_written(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    secret = "DO_NOT_WRITE_PRIVATE_FACT"
    logger.event(None, "tool_result", **{secret: {"memory": secret}}, prompt=secret,
                 data={"tool_result": secret}, exception=RuntimeError(secret))
    events = read_events(logger)
    assert len(events[0]["fields"]["redacted_fields"]) == 4
    for file in logger.run_directory.glob("*"):
        assert secret not in file.read_text(encoding="utf-8")


def test_unknown_object_repr_is_not_called(tmp_path):
    class PrivatePayload:
        def __str__(self):
            raise AssertionError("Should not serialize")

        __repr__ = __str__

    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "tool_result", data=PrivatePayload())
    assert len(read_events(logger)) == 1


def test_top_level_untrusted_labels_and_session_are_redacted(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    secret = "PRIVATE_PERSONAL_LABEL"
    trace = TraceContext(logger.run_id, secret, secret, secret)
    logger.event(trace, secret)
    event = read_events(logger)[0]
    assert event["event"] == "unknown_event" and event["source"] == "unknown"
    assert event["session_id"]["redacted"] and event["trace_id"]["redacted"]
    assert secret not in (logger.run_directory / "events.jsonl").read_text(encoding="utf-8")


def test_resource_ids_keep_real_identifiers_and_hide_arbitrary_titles(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    ids = ["task_" + "a" * 32, "action_" + "b" * 32, "review_2026-09-18", "PRIVATE_TITLE"]
    logger.event(None, "ui_operation_finished", changed_resource_ids=ids, memory_id=3)
    fields = read_events(logger)[0]["fields"]
    assert fields["changed_resource_ids"][:3] == ids[:3]
    assert fields["changed_resource_ids"][3]["redacted"]
    assert fields["memory_id"] == 3


def test_exception_records_only_type_and_trusted_source_location(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    try:
        raise ValueError("PRIVATE EXCEPTION MESSAGE C:\\Users\\someone\\memory.json")
    except ValueError as error:
        logger.record_exception(None, error)
    event = read_events(logger)[0]
    assert event["event"] == "exception"
    assert event["fields"]["error_type"] == "ValueError"
    assert event["fields"]["filename"] == "test_development_log.py"
    assert event["fields"]["lineno"] > 0
    text = (logger.run_directory / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE EXCEPTION" not in text and "Users" not in text


def test_unwritable_directory_warning_once_and_no_business_exception(tmp_path, capsys):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("existing", encoding="utf-8")
    logger = DevelopmentLog(blocked, enabled=True)
    for _ in range(3):
        assert logger.event(None, "tool_result", status="success") is None
    output = capsys.readouterr().out
    assert output.count("logger_write_failed") == 1
    assert str(blocked) not in output
    assert blocked.read_text(encoding="utf-8") == "existing"


def test_invalid_root_ownership_marker_is_not_overwritten(tmp_path):
    marker = tmp_path / ".roxy-development-log"
    marker.write_text("owned by user", encoding="utf-8")
    logger = DevelopmentLog(tmp_path, enabled=True)
    assert logger.event(None, "run_started") is None
    assert marker.read_text(encoding="utf-8") == "owned by user"
    assert logger.run_directory is None


def test_rotation_and_retention_only_remove_owned_log_files(tmp_path):
    directory = tmp_path / "development"
    directory.mkdir()
    unrelated = directory / "runtime.log"
    unrelated.write_text("USER EXISTING LOG", encoding="utf-8")
    lookalike = directory / ("run-20260101T000000-" + "a" * 32)
    lookalike.mkdir()
    fake_log = lookalike / "events.jsonl"
    fake_log.write_text("USER UNOWNED RUN LOG", encoding="utf-8")
    logger = DevelopmentLog(directory, enabled=True, max_file_bytes=1024, max_total_bytes=4096)
    for _ in range(35):
        logger.event(None, "tool_result", status="success")
    owned = logger._owned_log_files()
    assert logger._segment > 0
    assert sum(file.stat().st_size for file in owned) <= 4096
    assert read_events(logger)[-1]["sequence"] == 35
    assert unrelated.read_text(encoding="utf-8") == "USER EXISTING LOG"
    assert fake_log.read_text(encoding="utf-8") == "USER UNOWNED RUN LOG"
    assert (logger.run_directory / ".roxy-run").exists()


def test_retention_does_not_delete_other_files_in_owned_run(tmp_path):
    first = DevelopmentLog(tmp_path, enabled=True)
    first.event(None, "run_started")
    private = first.run_directory / "private.json"
    private.write_text("USER DATA", encoding="utf-8")
    second = DevelopmentLog(tmp_path, enabled=True, max_file_bytes=1024, max_total_bytes=2048)
    for _ in range(15):
        second.event(None, "run_started")
    assert private.read_text(encoding="utf-8") == "USER DATA"


def test_symlink_log_target_cannot_overwrite_external_data(tmp_path):
    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    logger.event(None, "run_started")
    external = tmp_path / "private.json"
    external.write_text("PRIVATE DATA", encoding="utf-8")
    target = logger.run_directory / "events.jsonl"
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    assert logger.event(None, "tool_result") is None
    assert external.read_text(encoding="utf-8") == "PRIVATE DATA"


def test_configure_replaces_default_without_creating_directory(tmp_path, monkeypatch):
    previous = development_log.get_development_log()
    monkeypatch.setattr(development_log, "_default_log", previous)
    configured = development_log.configure_development_log(tmp_path / "later")
    assert development_log.get_development_log() is configured
    assert not configured.directory.exists()


def test_interrupted_tail_is_preserved_and_next_event_rotates(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "run_started")
    broken = logger.run_directory / "events.jsonl"
    with broken.open("a", encoding="utf-8") as stream:
        stream.write('{"sequence":2,"event":')
    preserved = broken.read_bytes()
    logger.event(None, "tool_result", status="success")
    assert broken.read_bytes() == preserved
    new_event = json.loads((logger.run_directory / "events.000001.jsonl").read_text(encoding="utf-8"))
    assert new_event["event"] == "tool_result" and new_event["sequence"] == 2


def test_restart_does_not_modify_previous_run_partial_tail(tmp_path):
    previous = DevelopmentLog(tmp_path, enabled=True)
    previous.event(None, "run_started")
    broken = previous.run_directory / "events.jsonl"
    with broken.open("a", encoding="utf-8") as stream:
        stream.write('{"incomplete":')
    preserved = broken.read_bytes()
    current = DevelopmentLog(tmp_path, enabled=True)
    current.event(None, "run_started")
    assert broken.read_bytes() == preserved
    assert read_events(current)[0]["sequence"] == 1


def test_non_rotating_events_do_not_rescan_all_runs(tmp_path, monkeypatch):
    logger = DevelopmentLog(tmp_path, enabled=True)
    scans = []
    original = logger._owned_log_files

    def counted():
        scans.append(True)
        return original()

    monkeypatch.setattr(logger, "_owned_log_files", counted)
    for _ in range(50):
        logger.event(None, "parameter_validation", mode="write", status="success")
    assert len(read_events(logger)) == 50
    assert len(scans) == 1


def test_suspected_symlink_is_rejected_before_append(tmp_path, monkeypatch):
    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "run_started")
    target = logger.run_directory / "events.jsonl"
    before = target.read_bytes()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    assert logger.event(None, "tool_result") is None
    assert target.read_bytes() == before


def test_concurrent_log_failures_warn_once(tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("EXISTING", encoding="utf-8")
    logger = DevelopmentLog(blocked, enabled=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: logger.event(None, "tool_result"), range(20)))
    assert results == [None] * 20
    assert capsys.readouterr().out.count("logger_write_failed") == 1
    assert blocked.read_text(encoding="utf-8") == "EXISTING"


def test_huge_and_nonfinite_numeric_fields_are_safe_metadata(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "tool_result", total=10**1000, latency_ms=float("inf"), done=float("nan"))
    fields = read_events(logger)[0]["fields"]
    assert all(fields[key]["redacted"] for key in ("total", "latency_ms", "done"))


def test_arbitrary_valid_python_basename_is_not_trusted_as_source(tmp_path):
    logger = DevelopmentLog(tmp_path, enabled=True)
    logger.event(None, "exception", filename="private_personal_fact.py")
    assert read_events(logger)[0]["fields"]["filename"]["redacted"]
    assert "private_personal_fact.py" not in (logger.run_directory / "events.jsonl").read_text(encoding="utf-8")


def test_nested_loggers_route_global_helpers_and_restore_context(tmp_path):
    default = development_log.get_development_log()
    first = DevelopmentLog(tmp_path / "first", enabled=True)
    second = DevelopmentLog(tmp_path / "second", enabled=True)
    first_trace, second_trace = first.new_trace(), second.new_trace()
    with first.bind(first_trace):
        assert development_log.get_development_log() is first
        development_log.get_development_log().event(None, "tool_started")
        with pytest.raises(ValueError), second.bind(second_trace):
            assert development_log.get_development_log() is second
            development_log.get_development_log().event(None, "reply_composed")
            raise ValueError("test")
        assert development_log.get_development_log() is first
        development_log.get_development_log().event(None, "tool_result")
    assert development_log.get_development_log() is default
    assert [event["event"] for event in read_events(first)] == ["tool_started", "tool_result"]
    assert read_events(second)[0]["trace_id"] == second_trace.trace_id


def test_disabled_local_logger_overrides_enabled_default_and_bind_none(tmp_path, monkeypatch):
    enabled = DevelopmentLog(tmp_path / "enabled", enabled=True)
    disabled = DevelopmentLog(tmp_path / "disabled", enabled=False)
    monkeypatch.setattr(development_log, "_default_log", enabled)
    with enabled.bind(enabled.new_trace()):
        with disabled.bind(None):
            assert development_log.get_development_log() is disabled
            assert disabled.current_trace() is None
            assert development_log.get_development_log().event(None, "tool_started") is None
        assert development_log.get_development_log() is enabled
    assert not enabled.directory.exists() and not disabled.directory.exists()


def test_active_logger_binding_is_thread_local(tmp_path):
    default = development_log.get_development_log()

    def work(number):
        logger = DevelopmentLog(tmp_path / str(number), enabled=True)
        trace = logger.new_trace()
        with logger.bind(trace):
            assert development_log.get_development_log() is logger
            development_log.get_development_log().event(None, "tool_started")
        assert development_log.get_development_log() is default
        return read_events(logger)[0]["trace_id"] == trace.trace_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(work, range(4))) == [True] * 4
    assert development_log.get_development_log() is default
