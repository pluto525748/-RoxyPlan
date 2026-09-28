"""Read-only correlation of saved chat history and development events.

This script deliberately imports no application managers: constructing those
objects can initialize/migrate persistent stores. Default output contains no
chat body. ``--excerpts`` opts into short, redacted diagnostic excerpts.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # The standalone diagnostic command must not create source-tree caches.
    sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

# Pure schema only; no repository, manager, application, or logger initialization.
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY, VALID_REQUEST_MODES
from modules.interaction_state import VALID_STATES

TOKEN = re.compile(r"[A-Za-z0-9_.:@+-]{1,160}\Z")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
GENERATED_ID = re.compile(r"(?:req|request|call|tool|tc|msg|message|read|suggestion|session|trace|run|task|action)_[0-9a-f]{32}\Z")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
KNOWN_EVENTS = {
    "run_started", "run_finished", "chat_submitted", "history_written",
    "turn_prepare_started", "turn_prepare_finished", "turn_complete_started",
    "turn_complete_finished", "turn_service_finished", "semantic_decision",
    "tool_selected", "tool_started", "tool_result", "tool_reused", "tool_response_evidence",
    "reply_composed", "reply_guard_applied", "reply_displayed", "reply_not_displayed",
    "model_request_started", "model_request_finished", "snapshot_state",
    "parameter_validation",
    "ui_operation_started", "ui_operation_result", "ui_operation_finished",
    "ui_operation_cancelled", "startup_backfill_started", "startup_backfill_finished", "exception",
}
KNOWN_SOURCES = {
    "chat", "ui_growth_save_review", "ui_growth_add_action", "ui_memory_delete",
    "ui_memory_update", "ui_memory_archive", "ui_memory_restore", "startup_backfill",
    "desktop", "web", "unknown", "ui_plan_add", "ui_plan_delete", "ui_plan_complete",
    "ui_memory_accept_candidate", "ui_memory_reject_candidate", "ui_memory_resolve_conflict",
}
KNOWN_CODES = {
    "schema_rejected", "memory_save_without_tool", "confirmation_without_pending",
    "completion_without_verified_write", "false_execution_claim", "missing_snapshot",
    "expired_snapshot", "cross_session_snapshot", "missing_or_expired_snapshot",
    "suggestion_unavailable", "unregistered_tool", "tool_not_found", "tool_disabled",
    "invalid_tool_result", "reply_stale", "session_changed", "history_disabled",
    "history_save_failed", "turn_interrupted", "model_error", "logger_write_failed",
    "pet_unavailable", "legacy_direct", "unknown",
    "memory_delete_failed", "memory_update_failed", "memory_status_change_failed",
    "invalid_memory_id", "empty_changes", "empty_content", "success", "not_found",
    "validation_error", "already_processed", "partial_success", "conflict",
    "confirmation_required", "ambiguous", "failed",
}
KNOWN_STATUSES = {
    "success", "failed", "chat", "completed", "partial_success", "clarification",
    "confirmation_required", "clarification_required", "cancelled", "expired",
    "deferred", "unavailable", "running", "started", "ignored", "unknown", "not_found",
    "unchanged", "created", "existing", "empty", "validation_error", "already_processed",
    "conflict", "ambiguous", "pending", "failure", "error", "rejected", "blocked", "aborted",
}
KNOWN_STATES = VALID_STATES | {"missing", "valid", "unavailable", "unknown"}
KNOWN_MODES = VALID_REQUEST_MODES | {"read", "write", "clarify", "confirm", "unknown"}
FAILURES = {"failed", "failure", "error", "rejected", "blocked", "aborted", "validation_error"}
NONFAILURE_STATUSES = {"confirmation_required", "clarification_required", "clarification", "deferred", "cancelled", "unchanged", "existing", "empty", "already_processed"}
STAGES = {
    "semantic": "semantic_decision", "schema": "parameter_validation",
    "validation": "parameter_validation", "model": "model_request",
    "snapshot": "snapshot", "tool_response_evidence": "result_verification", "tool": "tool_execution",
    "persistence": "persistence", "history": "persistence",
    "reply_guard": "reply_guard", "reply_composed": "reply_composition",
    "display": "display", "ui_operation": "ui_operation",
}
SAFE_FIELDS = (
    "stage", "status", "reason_code", "rule_code", "tool_name", "capability",
    "intent", "mode", "request_id", "tool_call_id", "record_id", "date",
    "success", "terminal", "persisted", "deferred", "revision", "count",
    "message_count", "pending_type", "pending_count", "snapshot_id",
    "snapshot_status", "duration_ms", "exception_type", "tool", "service_status",
    "snapshot_state", "state", "latency_ms", "error_type", "guard_changed",
    "response_changed", "changed", "verified", "reused", "read_snapshot_present",
    "suggestion_snapshot_present", "confirmation_pending", "read_count",
    "suggestion_count", "tool_count", "argument_count", "operation_kind",
    "response_hash", "raw_model_response_hash", "user_text_hash", "request_hash",
    "snapshot_kind", "retry_count", "memory_id", "total", "done", "pending", "action_count",
    "filename", "lineno",
)


def _token(value: Any) -> str:
    if not isinstance(value, str) or value.startswith(("sk-", "Bearer")):
        return ""
    return value if TOKEN.fullmatch(value) else ""


def _hash_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return "unknown_" + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def _identifier(value: Any) -> str:
    return value if isinstance(value, str) and (GENERATED_ID.fullmatch(value) or UUID.fullmatch(value)) else ""


def _record_id(value: Any) -> str:
    if isinstance(value, str) and value.startswith("review_") and DAY.fullmatch(value[7:]):
        return value
    return _identifier(value)


def _session_identifier(value: Any) -> str:
    if isinstance(value, dict) and value.get("redacted") is True:
        digest = value.get("sha256")
        return "unknown_" + digest[:24] if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else ""
    return _identifier(value) or _hash_label(value)


def _label(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else _hash_label(value)


def _safe_field(key: str, value: Any) -> Any:
    bool_keys = {"success", "terminal", "persisted", "deferred", "guard_changed", "response_changed", "changed", "verified", "reused", "read_snapshot_present", "suggestion_snapshot_present", "confirmation_pending"}
    number_keys = {"revision", "count", "message_count", "pending_count", "duration_ms", "latency_ms", "read_count", "suggestion_count", "tool_count", "argument_count", "retry_count", "memory_id", "total", "done", "pending", "action_count", "lineno"}
    if key in bool_keys:
        return value if type(value) is bool else None
    if key in number_keys:
        return value if type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    if key in {"reason_code", "rule_code"}:
        return _label(value, KNOWN_CODES)
    if key in {"status", "service_status"}:
        return _label(value, KNOWN_STATUSES)
    if key in {"intent", "capability"}:
        return value if DEFAULT_CAPABILITY_REGISTRY.get(value) else _hash_label(value)
    if key in {"tool", "tool_name"}:
        return value if DEFAULT_CAPABILITY_REGISTRY.for_tool(value) else _hash_label(value)
    if key == "stage":
        return _label(value, KNOWN_EVENTS | set(STAGES) | set(STAGES.values()))
    if key in {"snapshot_state", "snapshot_status", "state", "pending_type"}:
        return _label(value, KNOWN_STATES | set(DEFAULT_CAPABILITY_REGISTRY.capability_ids()))
    if key in {"mode", "operation_kind"}:
        return _label(value, KNOWN_MODES)
    if key == "snapshot_kind":
        return _label(value, {"read", "suggestion"})
    if key == "date":
        return value if DAY.fullmatch(value) and _timestamp(value) else _hash_label(value)
    if key == "record_id":
        return _record_id(value) or _hash_label(value)
    if key.endswith("_id"):
        return _identifier(value) or _hash_label(value)
    if key.endswith("_hash"):
        return value if re.fullmatch(r"[0-9a-f]{64}", value) else _hash_label(value)
    if key in {"exception_type", "error_type"}:
        return value if value in {"OSError", "IOError", "RuntimeError", "ValueError", "TypeError", "TimeoutError", "ConnectionError", "JSONDecodeError", "UnicodeDecodeError", "PermissionError", "FileNotFoundError", "AttributeError", "KeyError", "IndexError", "Exception"} else _hash_label(value)
    if key == "filename":
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\.py", value) and any((PROJECT_ROOT / directory / value).is_file() for directory in ("modules", "frontend", "tests", "backend")):
            return value
        return _hash_label(value)
    return _hash_label(value)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _time_text(value: Any) -> str:
    parsed = _timestamp(value)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def _excerpt(value: Any) -> str:
    text = str(value or "")
    if re.search(r"身份暗号|身份密语|只有.{0,12}知道|api[_ -]?key|access[_ -]?token|password|密码|密钥|凭据", text, re.I):
        return "[敏感内容已省略]"
    text = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s，。；]*", "[本地路径]", text)
    text = re.sub(r"(?<!\w)/(?:Users|home|tmp|private|data)/[^\s，。；]*", "[本地路径]", text)
    text = re.sub(r"\b(?:sk-|Bearer\s+)[A-Za-z0-9_.-]+", "[凭据]", text, flags=re.I)
    text = re.sub(r"https?://[^\s，。；]+", "[链接]", text)
    return re.sub(r"\s+", " ", text).strip()[:120]


def _read_json(path: Path, label: str, gaps: list[dict]) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        gaps.append({"kind": "file_missing", "file": label})
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        gaps.append({"kind": "file_unreadable", "file": label, "error_type": type(error).__name__})
    return None


def _read_events(log_root: Path, gaps: list[dict]) -> list[dict]:
    events = []
    if not log_root.exists():
        gaps.append({"kind": "log_directory_missing"})
        return events
    paths = [log_root] if log_root.is_file() else sorted(log_root.rglob("events*.jsonl"))
    if not paths:
        gaps.append({"kind": "no_event_files"})
    for file_index, path in enumerate(paths, 1):
        label = f"event_file_{file_index}"
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        gaps.append({"kind": "malformed_event_line", "file": label, "line": line_number})
                        continue
                    if not isinstance(row, dict):
                        gaps.append({"kind": "invalid_event_record", "file": label, "line": line_number})
                        continue
                    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
                    event = {key: _identifier(row.get(key) or fields.get(key)) for key in ("run_id", "trace_id", "session_id")}
                    event["session_id"] = _session_identifier(row.get("session_id") or fields.get("session_id"))
                    event["source"] = _label(row.get("source") or fields.get("source"), KNOWN_SOURCES)
                    event["event"] = _label(row.get("event") or fields.get("event"), KNOWN_EVENTS)
                    event["timestamp"] = _time_text(row.get("timestamp") or fields.get("timestamp"))
                    sequence = row.get("sequence", 0)
                    event["sequence"] = sequence if type(sequence) is int and sequence >= 0 else 0
                    event["fields"] = {}
                    for key in SAFE_FIELDS:
                        value = _safe_field(key, fields.get(key, row.get(key)))
                        if value is not None and value != "":
                            event["fields"][key] = value
                    resource_ids = fields.get("changed_resource_ids")
                    if isinstance(resource_ids, list):
                        event["fields"]["changed_resource_ids"] = [item for item in resource_ids[:100] if _record_id(item)]
                    events.append(event)
        except (OSError, UnicodeError) as error:
            gaps.append({"kind": "event_file_unreadable", "file": label, "error_type": type(error).__name__})
    return sorted(events, key=lambda event: (event["timestamp"], event["run_id"], event["sequence"]))


def _stage(event: dict) -> str:
    explicit = event["fields"].get("stage")
    if isinstance(explicit, str):
        if explicit in STAGES.values():
            return explicit
        for prefix, stage in STAGES.items():
            if prefix in explicit:
                return stage
        return explicit
    for prefix, stage in STAGES.items():
        if prefix in event["event"]:
            return stage
    return "turn_lifecycle"


def _first_failure(events: list[dict]) -> dict | None:
    for event in events:
        fields = event["fields"]
        statuses = {fields.get("status"), fields.get("service_status")} - {None, ""}
        explicit_failed = bool(statuses & FAILURES)
        false_success = fields.get("success") is False and not statuses & NONFAILURE_STATUSES
        if explicit_failed or false_success:
            return {
                "stage": _stage(event), "event": event["event"],
                "reason_code": fields.get("reason_code", ""),
                "timestamp": event["timestamp"],
            }
    return None


def _first_deviation(events: list[dict]) -> dict | None:
    """A recorded protection is evidence, not proof that protection was wrong."""
    for event in events:
        failure = _first_failure([event])
        if failure:
            return dict(failure, kind="explicit_failure")
        fields = event["fields"]
        rule = fields.get("rule_code")
        if event["event"] == "reply_guard_applied" and rule in KNOWN_CODES - {"unknown", "success"}:
            return {"stage": "reply_guard", "event": event["event"], "reason_code": rule, "timestamp": event["timestamp"], "kind": "recorded_protection_not_proof_of_bug"}
    return None


def _outcome(events: list[dict]) -> str:
    names = {event["event"] for event in events}
    if not events:
        return "missing_log"
    if "reply_displayed" in names:
        return "displayed"
    if "reply_not_displayed" in names:
        return "not_displayed"
    if "ui_operation_cancelled" in names:
        return "cancelled"
    if "ui_operation_finished" in names:
        return "completed" if not _first_failure(events) else "failed"
    if "startup_backfill_finished" in names:
        if _first_failure(events):
            return "failed"
        finished = next(event for event in reversed(events) if event["event"] == "startup_backfill_finished")
        status = finished["fields"].get("status")
        return status if status in {"created", "existing", "empty"} else "finished_status_unknown"
    if "turn_service_finished" in names:
        return "service_failed_display_unknown" if _first_failure(events) else "service_completed_display_unknown"
    if any(event["fields"].get("terminal") is True for event in events):
        return "completed" if not _first_failure(events) else "failed"
    return "interrupted_or_unfinished"


def _operation(events: list[dict]) -> dict:
    if all(event["sequence"] > 0 for event in events):
        # The wall clock can move backwards; sequence is authoritative per run.
        events = sorted(events, key=lambda event: event["sequence"])
    else:
        events = sorted(events, key=lambda event: (event["timestamp"], event["sequence"]))
    first = events[0]
    return {
        "run_id": first["run_id"], "trace_id": first["trace_id"],
        "session_id": first["session_id"], "source": first["source"],
        "outcome": _outcome(events), "first_failure": _first_failure(events),
        "first_deviation": _first_deviation(events),
        "events": [dict(event, stage=_stage(event)) for event in events],
    }


def _independent(event: dict) -> bool:
    return event["source"].startswith(("ui_", "startup", "background"))


def _select_sessions(raw: Any, count: int, since: datetime | None, session_id: str) -> list[dict]:
    sessions = raw.get("sessions", []) if isinstance(raw, dict) else []
    if not isinstance(sessions, list):
        return []
    selected = []
    for session in sessions:
        if not isinstance(session, dict) or not isinstance(session.get("session_id"), str) or not session["session_id"]:
            continue
        if session_id and session["session_id"] != session_id:
            continue
        stamp = _timestamp(session.get("updated_at") or session.get("started_at"))
        if since and (stamp is None or stamp < since):
            continue
        selected.append(session)
    selected.sort(key=lambda item: _time_text(item.get("updated_at") or item.get("started_at")), reverse=True)
    return selected[:count]


def _time_candidates(message: dict, events: list[dict]) -> list[dict]:
    stamp = _timestamp(message.get("created_at"))
    if stamp is None:
        return []
    result, seen = [], set()
    for event in events:
        event_time = _timestamp(event["timestamp"])
        if event_time is None or abs(event_time - stamp) > timedelta(seconds=120):
            continue
        key = (event["run_id"], event["trace_id"], event["source"])
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "run_id": key[0], "trace_id": key[1], "source": key[2],
            "timestamp": event["timestamp"], "certainty": "uncertain_time_only",
        })
        if len(result) >= 6:
            break
    return result


def _current_data(data_dir: Path, events: list[dict], gaps: list[dict]) -> list[dict]:
    """Observe only dates/record IDs explicitly reported persisted by a log.

    Current state never proves which earlier turn wrote it. No memory store is
    opened, and no titles, action bodies, or full reviews are emitted.
    """
    refs = set()
    for event in events:
        fields = event["fields"]
        day, record_id = fields.get("date"), fields.get("record_id")
        if fields.get("success") is True and fields.get("persisted") is True and isinstance(day, str) and DAY.fullmatch(day):
            resource_ids = fields.get("changed_resource_ids")
            ids = [_token(record_id)] if _token(record_id) else []
            if isinstance(resource_ids, list):
                ids.extend(item for item in resource_ids if _token(item))
            refs.update((day, item) for item in ids or [""])
    if not refs:
        return []
    observed = []
    for store, filename in (("plan", "today_plan.json"), ("action", "action_log.json"), ("review", "growth_log.json")):
        raw = _read_json(data_dir / filename, filename, gaps)
        if not isinstance(raw, dict):
            continue
        for day, logged_id in sorted(refs):
            if store == "review":
                entries = raw.get("entries", {})
                row = entries.get(day) if isinstance(entries, dict) else next((item for item in entries if isinstance(item, dict) and item.get("date") == day), None) if isinstance(entries, list) else None
                rows = [row] if isinstance(row, dict) else []
            else:
                days = raw.get("days", {})
                day_data = days.get(day, {}) if isinstance(days, dict) else {}
                rows = day_data.get("tasks" if store == "plan" else "records", []) if isinstance(day_data, dict) else []
                if not rows and raw.get("date") == day:
                    rows = raw.get("tasks" if store == "plan" else "records", [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                record_id = _record_id(row.get("uid") or row.get("record_id"))
                if logged_id and record_id != logged_id:
                    continue
                item = {"store": store, "date": day, "record_id": record_id, "proof_scope": "current_snapshot_only"}
                if store == "plan":
                    item["status"] = _label(row.get("status"), KNOWN_STATUSES | {"todo", "done", "in_progress", "cancelled"})
                if store == "review" and type(row.get("revision")) is int:
                    item["revision"] = row["revision"]
                if item not in observed:
                    observed.append(item)
    return observed


def inspect_recent_interactions(
    history: Path, logs: Path, *, sessions: int = 3,
    since: str = "", session_id: str = "", excerpts: bool = False,
    data_dir: Path | None = None,
) -> dict:
    """Return safe evidence without writing files or invoking application code."""
    if sessions < 1:
        raise ValueError("sessions must be positive")
    since_time = _timestamp(since) if since else None
    if since and since_time is None:
        raise ValueError("since must be an ISO date or timestamp")
    gaps: list[dict] = []
    raw = _read_json(Path(history), "chat_history", gaps)
    if raw is not None and (not isinstance(raw, dict) or not isinstance(raw.get("sessions"), list)):
        gaps.append({"kind": "invalid_history_schema"})
    selected = _select_sessions(raw, sessions, since_time, session_id)
    all_events = _read_events(Path(logs), gaps)
    events = [event for event in all_events if not since_time or (_timestamp(event["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc)) >= since_time]
    if session_id:
        requested_sid = _session_identifier(session_id)
        scoped_runs = {event["run_id"] for event in events if event["session_id"] == requested_sid}
        events = [event for event in events if event["session_id"] == requested_sid or (event["event"] in {"run_started", "run_finished"} and event["run_id"] in scoped_runs)]
    keyed: dict[tuple[str, str, str], list[dict]] = {}
    for event in events:
        if event["trace_id"] and event["run_id"] and event["event"] not in {"run_started", "run_finished"}:
            keyed.setdefault((event["run_id"], event["trace_id"], event["session_id"]), []).append(event)
    report = {
        "schema_version": 1, "selected_sessions": [], "chat_turns": [],
        "independent_operations": [], "uncertain_candidates": [],
        "log_only_operations": [], "unassociated_events": [], "run_events": [],
        "current_data": [], "evidence_gaps": gaps,
        "limits": {"excerpts_enabled": excerpts, "excerpt_messages_per_session": 6, "recent_unlinked_operation_limit": max(3, sessions * 12), "time_candidates_are_not_matches": True, "current_files_do_not_prove_turn_execution": True},
    }
    referenced_keys = set()
    for session in selected:
        sid = _session_identifier(session["session_id"])
        messages = session.get("messages", [])
        messages = messages if isinstance(messages, list) else []
        report["selected_sessions"].append({
            "session_id": sid, "started_at": _time_text(session.get("started_at")),
            "updated_at": _time_text(session.get("updated_at")), "message_count": len(messages),
        })
        grouped_messages: dict[tuple[str, str, str], list[dict]] = {}
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            stamp = _timestamp(message.get("created_at"))
            if since_time and (stamp is None or stamp < since_time):
                continue
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            run_id, trace_id = _identifier(metadata.get("run_id")), _identifier(metadata.get("trace_id"))
            summary = {"message_index": index, "role": _label(message.get("role"), {"user", "assistant", "system"}), "created_at": _time_text(message.get("created_at"))}
            if excerpts and message.get("role") in {"user", "assistant"} and index >= max(0, len(messages) - 6):
                summary["excerpt"] = _excerpt(message.get("content"))
            if run_id and trace_id:
                grouped_messages.setdefault((run_id, trace_id, sid), []).append(summary)
            else:
                report["uncertain_candidates"].append({
                    "session_id": sid, "message": summary, "reason": "missing_trace_metadata",
                    "candidates": _time_candidates(message, events),
                })
        for key, summaries in grouped_messages.items():
            # UI events remain independent even if a buggy producer reused a chat ID.
            matching = [event for event in keyed.get(key, []) if event["source"] in {"chat", "web", "desktop"}]
            referenced_keys.add(key)
            turn = _operation(matching) if matching else {"run_id": key[0], "trace_id": key[1], "session_id": key[2], "source": "chat", "outcome": "missing_log", "first_failure": None, "first_deviation": None, "events": []}
            turn["messages"] = summaries
            turn["certainty"] = "exact_metadata_match" if matching else "no_matching_events"
            report["chat_turns"].append(turn)
    unlinked_operations = []
    for key, group in keyed.items():
        independent = [event for event in group if _independent(event)]
        if independent:
            operation = _operation(independent)
            operation["certainty"] = "independent_operation_not_chat_execution"
            unlinked_operations.append(("independent_operations", operation))
        remaining = [event for event in group if not _independent(event)]
        if key not in referenced_keys and remaining:
            operation = _operation(remaining)
            operation["certainty"] = "log_only_not_linked_to_selected_chat"
            unlinked_operations.append(("log_only_operations", operation))
    # Saved history may be old while logging continued with history disabled.
    # Keep a bounded recent evidence tail; proximity never links it to old chat.
    unlinked_operations.sort(key=lambda item: max(event["timestamp"] for event in item[1]["events"]))
    for destination, operation in unlinked_operations[-max(3, sessions * 12):]:
        report[destination].append(operation)
    if not selected:
        gaps.append({"kind": "no_selected_chat_sessions"})
    report["run_events"] = [event for event in events if event["event"] in {"run_started", "run_finished"}][-max(3, sessions * 2):]
    unassociated = [event for event in events if (not event["run_id"] or not event["trace_id"]) and event["event"] not in {"run_started", "run_finished"}]
    if unassociated:
        gaps.append({"kind": "events_without_trace_metadata", "count": len(unassociated)})
        report["unassociated_events"] = unassociated[-max(3, sessions * 12):]
    if any(turn["outcome"] == "missing_log" for turn in report["chat_turns"]):
        gaps.append({"kind": "chat_trace_without_matching_events"})
    if any(turn["outcome"] == "interrupted_or_unfinished" for turn in report["chat_turns"]):
        gaps.append({"kind": "chat_trace_without_terminal_event"})
    if data_dir is not None:
        relevant = [event for name in ("chat_turns", "independent_operations", "log_only_operations") for turn in report[name] for event in turn["events"]]
        report["current_data"] = _current_data(Path(data_dir), relevant, gaps)
    return report


def render_text(report: dict) -> str:
    lines = [f"已选择 {len(report['selected_sessions'])} 个会话；精确关联 {len(report['chat_turns'])} 轮。"]
    for turn in report["chat_turns"]:
        lines.append(f"[{turn['session_id']}] {turn['trace_id']}: {turn['outcome']}")
        for message in turn["messages"]:
            lines.append(f"  {message['created_at']} {message['role']}" + (f": {message['excerpt']}" if "excerpt" in message else ""))
        failure = turn["first_failure"]
        if failure:
            lines.append(f"  首个显式失败：{failure['stage']} / {failure['event']} / {failure['reason_code'] or '未记录原因码'}")
        deviation = turn["first_deviation"]
        if deviation and deviation["kind"] != "explicit_failure":
            lines.append(f"  首个回复保护节点（不等于程序缺陷）：{deviation['stage']} / {deviation['reason_code']}")
        for event in turn["events"]:
            lines.append(f"  {event['timestamp']} {event['stage']} {event['event']} {json.dumps(event['fields'], ensure_ascii=False)}")
    for operation in report["independent_operations"]:
        lines.append(f"独立操作（不计入聊天成功）：{operation['source']} / {operation['trace_id']} / {operation['outcome']}")
    for operation in report["log_only_operations"]:
        lines.append(f"仅日志证据：{operation['trace_id']} / {operation['outcome']}")
    for event in report["unassociated_events"]:
        lines.append(f"无法逐轮关联的日志：{event['timestamp']} / {event['event']}")
    for candidate in report["uncertain_candidates"]:
        message = candidate["message"]
        lines.append(f"旧消息 {candidate['session_id']} #{message['message_index']}：仅有 {len(candidate['candidates'])} 个不确定时间候选，未关联。" + (f" {message['excerpt']}" if "excerpt" in message else ""))
    for gap in report["evidence_gaps"]:
        lines.append("证据缺口：" + json.dumps(gap, ensure_ascii=False))
    for row in report["current_data"]:
        lines.append("当前文件观察（不证明当轮执行）：" + json.dumps(row, ensure_ascii=False))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=PROJECT_ROOT / "data" / "private" / "chat_history.json")
    parser.add_argument("--logs", type=Path, default=PROJECT_ROOT / "logs" / "development")
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--since", default="", help="ISO date or timestamp")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--data-dir", type=Path, help="Optional read-only growth-store verification")
    parser.add_argument("--excerpts", action="store_true", help="Opt into redacted chat excerpts, at most 120 characters each")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        report = inspect_recent_interactions(args.history, args.logs, sessions=args.sessions, since=args.since, session_id=args.session_id, excerpts=args.excerpts, data_dir=args.data_dir)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
