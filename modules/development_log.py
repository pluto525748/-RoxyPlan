"""Small, opt-in development evidence log; never a copy of stdout or user data.

The desktop enables this once at startup.  Importing this module and obtaining
the default logger do not create files.  Logging failures must not affect an
operation, and fields not explicitly safe are represented only by fingerprints.
"""
from __future__ import annotations

import builtins
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Iterator, Optional
from uuid import uuid4

from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY, VALID_REQUEST_MODES
from modules.interaction_state import VALID_STATES


_EVENTS = frozenset({
    "run_started", "run_finished", "chat_submitted", "history_written",
    "turn_prepare_started", "turn_prepare_finished", "turn_complete_started",
    "turn_complete_finished", "turn_service_finished", "semantic_decision",
    "tool_selected", "tool_started", "tool_result", "tool_reused",
    "tool_response_evidence",
    "reply_composed", "reply_guard_applied", "reply_displayed", "reply_not_displayed",
    "model_request_started", "model_request_finished", "snapshot_state",
    "parameter_validation",
    "ui_operation_started", "ui_operation_result", "ui_operation_finished",
    "ui_operation_cancelled", "startup_backfill_started", "startup_backfill_finished",
    "exception",
})
_STATUSES = frozenset({
    "success", "failed", "chat", "completed", "partial_success", "clarification",
    "confirmation_required", "clarification_required", "cancelled", "expired",
    "deferred", "unavailable", "running", "started", "ignored", "unknown",
    "not_found", "unchanged", "created", "existing", "empty", "validation_error",
    "already_processed", "conflict", "ambiguous",
})
_SOURCES = frozenset({
    "chat", "ui_growth_save_review", "ui_growth_add_action", "ui_memory_delete",
    "ui_memory_update", "ui_memory_archive", "ui_memory_restore",
    "startup_backfill", "desktop", "web", "unknown",
    "ui_plan_add", "ui_plan_delete", "ui_plan_complete", "ui_memory_accept_candidate",
    "ui_memory_reject_candidate", "ui_memory_resolve_conflict",
})
_CODES = frozenset({
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
})
_BOOL_FIELDS = frozenset({
    "changed", "verified", "guard_changed", "read_snapshot_present",
    "suggestion_snapshot_present", "confirmation_pending", "reused", "success",
    "terminal", "persisted", "response_changed",
})
_NUMBER_FIELDS = frozenset({
    "revision", "total", "done", "pending", "action_count", "count", "read_count",
    "suggestion_count", "retry_count", "tool_count", "argument_count", "latency_ms",
    "lineno", "response_count", "message_count",
    "memory_id",
})
_HASH_FIELDS = frozenset({
    "response_hash", "raw_model_response_hash", "user_text_hash", "request_hash",
})
_ID_FIELDS = frozenset({"request_id", "tool_call_id", "message_id", "snapshot_id", "record_id"})
_IDS = re.compile(r"(?:req|request|call|tool|tc|msg|message|read|suggestion|session|trace|run|task|action)_[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_RUN_DIR = re.compile(r"run-[0-9]{8}T[0-9]{6}-[0-9a-f]{32}\Z")
_LOG_FILE = re.compile(r"(?:events(?:\.[0-9]{6})?\.jsonl|runtime(?:\.[0-9]{6})?\.log)\Z")
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ROOT_MARKER = "RoxyPlan development log v1\n"
_active_logger: ContextVar[Optional["DevelopmentLog"]] = ContextVar("roxy_active_development_log", default=None)


@dataclass(frozen=True)
class _VerifiedBasename:
    value: str


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    trace_id: str
    session_id: str = ""
    source: str = "chat"

    def to_metadata(self) -> dict[str, str]:
        """Add correlation only; request_id has different history semantics."""
        return {"run_id": self.run_id, "trace_id": self.trace_id}


def _fingerprint(value: Any) -> dict[str, Any]:
    """Do not call an unknown object's repr/str, or recursively dump a payload."""
    value_type = type(value)
    name = value_type.__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", name):
        name = "object"
    length = None
    if value_type is str:
        length = len(value)
        payload = value[:65536].encode("utf-8", errors="replace")
    elif value_type is bytes:
        length = len(value)
        payload = value[:65536]
    elif value_type in (int, float, bool, type(None)):
        payload = str(value).encode("ascii", errors="replace")
    elif value_type in (list, tuple, dict, set, frozenset):
        length = len(value)
        # A structural fingerprint is deliberate: never serialize nested data.
        payload = f"{name}:{length}".encode("ascii")
    else:
        payload = name.encode("ascii")
    result = {"redacted": True, "type": name, "sha256": hashlib.sha256(payload).hexdigest()}
    if length is not None:
        result["length"] = length
    if length is not None and length > 65536 and value_type in (str, bytes):
        result["fingerprint_truncated"] = True
    return result


def _identifier(value: Any) -> Any:
    if type(value) is str and (not value or _IDS.fullmatch(value) or _UUID.fullmatch(value)):
        return value
    return _fingerprint(value)


def _date(value: Any) -> bool:
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    redacted = []
    for key, value in list(fields.items())[:64]:
        if key in _BOOL_FIELDS and type(value) is bool:
            safe[key] = value
        elif key in _NUMBER_FIELDS and type(value) in (int, float) and abs(value) <= 10**15 and math.isfinite(value):
            safe[key] = value
        elif key in _HASH_FIELDS and type(value) is str and _HEX.fullmatch(value):
            safe[key] = value.lower()
        elif key in _ID_FIELDS:
            if key == "record_id" and type(value) is str and value.startswith("review_") and _date(value[7:]):
                safe[key] = value
            else:
                safe[key] = _identifier(value)
        elif key == "changed_resource_ids" and type(value) in (list, tuple):
            safe[key] = [item if type(item) is str and (_IDS.fullmatch(item) or (item.startswith("review_") and _date(item[7:])) or re.fullmatch(r"(?:candidate|conflict)_[0-9]{1,12}", item)) else _fingerprint(item) for item in value[:100]]
            if len(value) > 100:
                safe["omitted_resource_count"] = len(value) - 100
        elif key in {"status", "service_status"} and type(value) is str and value in _STATUSES:
            safe[key] = value
        elif key in {"reason_code", "rule_code"} and type(value) is str and value in _CODES:
            safe[key] = value
        elif key in {"intent", "capability"} and type(value) is str and DEFAULT_CAPABILITY_REGISTRY.get(value):
            safe[key] = value
        elif key == "tool" and type(value) is str and DEFAULT_CAPABILITY_REGISTRY.for_tool(value):
            safe[key] = value
        elif key == "date" and _date(value):
            safe[key] = value
        elif key == "stage" and type(value) is str and value in _EVENTS:
            safe[key] = value
        elif key == "mode" and type(value) is str and value in (VALID_REQUEST_MODES | {"read", "write", "clarify", "confirm"}):
            safe[key] = value
        elif key == "snapshot_kind" and type(value) is str and value in {"read", "suggestion"}:
            safe[key] = value
        elif key in {"snapshot_state", "state"} and type(value) is str and value in (VALID_STATES | {"missing", "valid", "unavailable", "unknown"}):
            safe[key] = value
        elif key == "operation_kind" and type(value) is str and value in {"read", "write", "unknown"}:
            safe[key] = value
        elif key == "error_type" and type(value) is str and isinstance(getattr(builtins, value, None), type) and issubclass(getattr(builtins, value), BaseException):
            safe[key] = value
        elif key == "filename" and isinstance(value, _VerifiedBasename):
            safe[key] = value.value
        elif key in (_BOOL_FIELDS | _NUMBER_FIELDS | _HASH_FIELDS | _ID_FIELDS | {"status", "service_status", "reason_code", "rule_code", "intent", "capability", "tool", "date", "stage", "mode", "snapshot_kind", "snapshot_state", "state", "operation_kind", "error_type", "filename", "provider", "model", "changed_resource_ids"}):
            safe[key] = _fingerprint(value)
        else:
            redacted.append({"field_hash": hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest(), "value": _fingerprint(value)})
    if redacted:
        safe["redacted_fields"] = redacted
    if len(fields) > 64:
        safe["omitted_field_count"] = len(fields) - 64
    return safe


class DevelopmentLog:
    def __init__(self, directory=None, *, enabled: bool = False,
                 max_file_bytes: int = 8 * 1024 * 1024,
                 max_total_bytes: int = 50 * 1024 * 1024) -> None:
        self.enabled = enabled is True
        self.directory = Path(directory).resolve() if directory is not None else None
        self.run_id = "run_" + uuid4().hex
        self.run_directory: Optional[Path] = None
        self.max_file_bytes = max(1024, max_file_bytes) if type(max_file_bytes) is int else 8 * 1024 * 1024
        self.max_total_bytes = max(2048, max_total_bytes) if type(max_total_bytes) is int else 50 * 1024 * 1024
        self._lock = threading.RLock()
        self._context: ContextVar[Optional[TraceContext]] = ContextVar("roxy_development_trace_" + self.run_id, default=None)
        self._sequence = 0
        self._segment = 0
        self._warned = False
        self._known_total_bytes: Optional[int] = None

    def new_trace(self, session_id: str = "", source: str = "chat") -> TraceContext:
        return TraceContext(self.run_id, "trace_" + uuid4().hex, session_id, source)

    def current_trace(self) -> Optional[TraceContext]:
        return self._context.get()

    @contextmanager
    def bind(self, trace: Optional[TraceContext]) -> Iterator[Optional[TraceContext]]:
        token = self._context.set(trace)
        logger_token = _active_logger.set(self)
        try:
            yield trace
        finally:
            _active_logger.reset(logger_token)
            self._context.reset(token)

    def _warn_once(self) -> None:
        with self._lock:
            if not self._warned:
                self._warned = True
                # Fixed wording only: error messages and paths may contain secrets.
                try:
                    print("[DevelopmentLog] logger_write_failed; business operation is unchanged.")
                except Exception:
                    pass

    def _prepare_directory(self) -> None:
        if self.run_directory is not None:
            return
        if self.directory is None:
            raise OSError("No development log directory configured")
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = self.directory / ".roxy-development-log"
        if marker.exists():
            if marker.is_symlink() or marker.read_text(encoding="utf-8") != _ROOT_MARKER:
                raise OSError("Development log ownership marker mismatch")
        else:
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(_ROOT_MARKER)
                stream.flush()
        run_name = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + self.run_id[4:]
        run_directory = self.directory / run_name
        run_directory.mkdir()
        with (run_directory / ".roxy-run").open("x", encoding="utf-8") as stream:
            stream.write(self.run_id + "\n")
            stream.flush()
        self.run_directory = run_directory

    def _paths(self) -> tuple[Path, Path]:
        suffix = f".{self._segment:06d}" if self._segment else ""
        assert self.run_directory is not None
        return (self.run_directory / f"events{suffix}.jsonl", self.run_directory / f"runtime{suffix}.log")

    def _owned_log_files(self) -> list[Path]:
        assert self.directory is not None
        files = []
        for run in self.directory.iterdir():
            if not _RUN_DIR.fullmatch(run.name) or run.is_symlink() or not run.is_dir() or run.resolve().parent != self.directory:
                continue
            marker = run / ".roxy-run"
            if not marker.is_file() or marker.is_symlink():
                continue
            try:
                if marker.read_text(encoding="utf-8") != "run_" + run.name.rsplit("-", 1)[1] + "\n":
                    continue
            except (OSError, UnicodeError):
                continue
            for path in run.iterdir():
                if _LOG_FILE.fullmatch(path.name) and not path.is_symlink() and path.is_file() and path.resolve().parent == run.resolve():
                    files.append(path)
        return files

    def _prune(self, current: tuple[Path, Path]) -> None:
        files = self._owned_log_files()
        total = sum(path.stat().st_size for path in files)
        removable = sorted((path for path in files if path not in current), key=lambda path: (path.stat().st_mtime_ns, str(path)))
        for path in removable:
            if total <= self.max_total_bytes:
                break
            # Resolve once more immediately before unlink; never recursive-delete.
            resolved = path.resolve()
            if path.is_symlink() or resolved.parent.parent != self.directory or not _RUN_DIR.fullmatch(resolved.parent.name) or not _LOG_FILE.fullmatch(resolved.name):
                continue
            size = path.stat().st_size
            path.unlink()
            total -= size
        self._known_total_bytes = total

    @staticmethod
    def _partial_tail(path: Path) -> bool:
        if path.is_symlink():
            raise OSError("Development log target must not be a symlink")
        if not path.exists() or not path.stat().st_size:
            return False
        # Preserve an interrupted record rather than joining the next event to it.
        with path.open("rb") as stream:
            stream.seek(-1, 2)
            return stream.read(1) != b"\n"

    def event(self, trace: Optional[TraceContext] = None, event_name: str = "", **fields: Any) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return None
        with self._lock:
            try:
                trace = trace or self.current_trace() or self.new_trace(source="desktop")
                self._sequence += 1
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "run_id": self.run_id,
                    "trace_id": _identifier(trace.trace_id),
                    "session_id": _identifier(trace.session_id),
                    "source": trace.source if type(trace.source) is str and trace.source in _SOURCES else "unknown",
                    "sequence": self._sequence,
                    "event": event_name if type(event_name) is str and event_name in _EVENTS else "unknown_event",
                    "fields": _safe_fields(fields),
                }
                if record["event"] == "unknown_event":
                    record["event_fingerprint"] = _fingerprint(event_name)
                line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
                readable = (f"{record['timestamp']} #{record['sequence']} {record['event']} "
                            + json.dumps({key: value for key, value in record.items() if key not in {"timestamp", "sequence", "event"}}, ensure_ascii=True, separators=(",", ":")) + "\n")
                self._prepare_directory()
                paths = self._paths()
                rotated = False
                if any(self._partial_tail(path) or (path.exists() and path.stat().st_size + len(content.encode("utf-8")) > self.max_file_bytes) for path, content in zip(paths, (line, readable))):
                    self._segment += 1
                    paths = self._paths()
                    rotated = True
                for path, content in zip(paths, (line, readable)):
                    if path.is_symlink() or path.resolve().parent != self.run_directory:
                        raise OSError("Development log path escaped its run directory")
                    with path.open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(content)
                        stream.flush()
                if self._known_total_bytes is None or rotated:
                    self._prune(paths)
                else:
                    self._known_total_bytes += len(line.encode("utf-8")) + len(readable.encode("utf-8"))
                    if self._known_total_bytes > self.max_total_bytes:
                        self._prune(paths)
                return record
            except Exception:
                self._warn_once()
                return None

    def record_exception(self, trace: Optional[TraceContext], error: BaseException) -> None:
        if not self.enabled:
            return
        try:
            fields: dict[str, Any] = {"error_type": type(error).__name__}
            frame = error.__traceback__
            while frame is not None:
                path = Path(frame.tb_frame.f_code.co_filename).resolve()
                if path.is_relative_to(_PROJECT_ROOT) and path.is_file() and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\.py", path.name):
                    fields["filename"] = _VerifiedBasename(path.name)
                    fields["lineno"] = frame.tb_lineno
                frame = frame.tb_next
            self.event(trace, "exception", **fields)
        except Exception:
            self._warn_once()


_default_log = DevelopmentLog()
_default_lock = threading.Lock()


def get_development_log() -> DevelopmentLog:
    active = _active_logger.get()
    return active if active is not None else _default_log


def configure_development_log(directory, *, enabled: bool = True, **limits) -> DevelopmentLog:
    global _default_log
    logger = DevelopmentLog(directory, enabled=enabled, **limits)
    with _default_lock:
        _default_log = logger
    return logger
