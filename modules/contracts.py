from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Mapping, Optional, Union
from uuid import uuid4


SCHEMA_VERSION = "1.0"


class ResponseOutcome(Enum):
    """Single source of truth for what happened in a conversation turn.

    Only ToolResult.success can produce SUCCESS — no other path may set it.
    """

    CHAT = "chat"
    SUCCESS = "success"
    CLARIFY = "clarify"
    FAILURE = "failure"
JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]


def _stable_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_json_value(value: object, path: str = "value") -> JsonValue:
    """Return a JSON-safe copy or reject process-local objects."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path} must contain a finite JSON number")
        return value
    if isinstance(value, (list, tuple)):
        return [ensure_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result: Dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            result[key] = ensure_json_value(item, f"{path}.{key}")
        return result
    raise TypeError(f"{path} contains non-JSON value: {type(value).__name__}")


def _mapping(value: object, contract: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{contract} must be created from an object")
    return value


def _required(data: Mapping[str, object], field_name: str, contract: str) -> object:
    if field_name not in data:
        raise ValueError(f"{contract} missing required field: {field_name}")
    return data[field_name]


def _required_alias(
    data: Mapping[str, object], field_names: List[str], contract: str
) -> object:
    for field_name in field_names:
        if field_name in data:
            return data[field_name]
    raise ValueError(f"{contract} missing required field: {field_names[0]}")


def _safe_string(value: object, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _sanitize_public_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "Traceback (most recent call last)" in text:
        text = "Internal operation failed"
    else:
        text = text.splitlines()[0]
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<redacted-path>", text)
    text = re.sub(r"/(?:Users|home|var|tmp|etc)/[^\s\"']+", "<redacted-path>", text)
    return text[:300]


@dataclass
class IntentResult:
    intent: str
    confidence: float = 0.0
    entities: Dict[str, object] = field(default_factory=dict)
    needs_confirmation: bool = False
    source: str = "fallback"
    schema_version: str = SCHEMA_VERSION
    request_id: str = field(default_factory=lambda: _stable_id("req"))
    warnings: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    candidate_actions: List[Dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.intent = _safe_string(self.intent, "chat")
        try:
            self.confidence = max(0.0, min(float(self.confidence), 1.0))
        except (TypeError, ValueError):
            self.confidence = 0.0
        self.entities = dict(ensure_json_value(dict(self.entities), "IntentResult.entities"))
        self.needs_confirmation = bool(self.needs_confirmation)
        self.source = _safe_string(self.source, "fallback")
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.request_id = _safe_string(self.request_id, _stable_id("req"))
        self.warnings = [str(item) for item in ensure_json_value(list(self.warnings), "IntentResult.warnings")]
        self.clarification_question = _safe_string(self.clarification_question) or None
        actions = ensure_json_value(
            list(self.candidate_actions), "IntentResult.candidate_actions"
        )
        self.candidate_actions = [
            dict(item) for item in actions if isinstance(item, dict)
        ]

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "intent": self.intent,
            "confidence": self.confidence,
            "entities": ensure_json_value(self.entities, "IntentResult.entities"),
            "needs_confirmation": self.needs_confirmation,
            "source": self.source,
            "warnings": list(self.warnings),
            "clarification_question": self.clarification_question,
            "candidate_actions": ensure_json_value(
                self.candidate_actions, "IntentResult.candidate_actions"
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "IntentResult":
        data = _mapping(value, "IntentResult")
        return cls(
            intent=_safe_string(_required(data, "intent", "IntentResult"), "chat"),
            confidence=data.get("confidence", 0.0),
            entities=dict(data.get("entities", {})) if isinstance(data.get("entities", {}), Mapping) else {},
            needs_confirmation=bool(data.get("needs_confirmation", False)),
            source=_safe_string(data.get("source"), "fallback"),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            request_id=_safe_string(data.get("request_id"), _stable_id("req")),
            warnings=list(data.get("warnings", [])) if isinstance(data.get("warnings", []), list) else [],
            clarification_question=_safe_string(data.get("clarification_question")) or None,
            candidate_actions=(
                list(data.get("candidate_actions", []))
                if isinstance(data.get("candidate_actions", []), list)
                else []
            ),
        )


@dataclass(eq=False)
class ToolError:
    code: str
    message: str = ""
    retryable: bool = False
    details: Dict[str, object] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", _safe_string(self.code, "internal_error"))
        self.code = normalized[:80] or "internal_error"
        self.message = _sanitize_public_text(self.message)
        safe_details = ensure_json_value(dict(self.details), "ToolError.details")
        self.details = self._sanitize_details(dict(safe_details))
        self.retryable = bool(self.retryable)
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.code == other
        if isinstance(other, ToolError):
            return self.to_dict() == other.to_dict()
        return False

    def __hash__(self) -> int:
        return hash(self.code)

    def __str__(self) -> str:
        return self.code

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": ensure_json_value(self.details, "ToolError.details"),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ToolError":
        if isinstance(value, str):
            return cls(value)
        data = _mapping(value, "ToolError")
        return cls(
            code=_safe_string(_required(data, "code", "ToolError"), "internal_error"),
            message=_safe_string(data.get("message")),
            retryable=bool(data.get("retryable", False)),
            details=dict(data.get("details", {})) if isinstance(data.get("details", {}), Mapping) else {},
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
        )

    @classmethod
    def from_exception(cls, _error: BaseException, code: str = "tool_execution_failed") -> "ToolError":
        return cls(code=code, message="The internal operation failed.", retryable=False)

    @classmethod
    def _sanitize_details(cls, value: Dict[str, JsonValue]) -> Dict[str, object]:
        def clean(item: JsonValue) -> JsonValue:
            if isinstance(item, str):
                return _sanitize_public_text(item)
            if isinstance(item, list):
                return [clean(child) for child in item]
            if isinstance(item, dict):
                return {key: clean(child) for key, child in item.items()}
            return item

        return dict(clean(value))


@dataclass
class ToolResult:
    success: bool
    tool: str
    message: str
    data: Dict[str, object] = field(default_factory=dict)
    error: Optional[Union[str, ToolError, Mapping[str, object]]] = None
    schema_version: str = SCHEMA_VERSION
    tool_call_id: str = field(default_factory=lambda: _stable_id("call"))
    status: str = ""
    message_code: str = ""
    display_message: str = ""
    operation_kind: str = "unknown"

    def __post_init__(self) -> None:
        self.success = bool(self.success)
        self.tool = _safe_string(self.tool, "unknown")
        self.message = _safe_string(self.message)
        self.data = dict(ensure_json_value(dict(self.data), "ToolResult.data"))
        if self.error is not None and not isinstance(self.error, ToolError):
            self.error = ToolError.from_dict(self.error)
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.tool_call_id = _safe_string(self.tool_call_id, _stable_id("call"))
        self.status = _safe_string(self.status, "completed" if self.success else "failed")
        self.message_code = _safe_string(self.message_code, self.message)
        self.display_message = _safe_string(self.display_message, self.message)
        self.operation_kind = _safe_string(self.operation_kind, "unknown").lower()
        if self.operation_kind not in {"read", "write", "unknown"}:
            self.operation_kind = "unknown"

    @property
    def error_code(self) -> Optional[str]:
        return self.error.code if isinstance(self.error, ToolError) else None

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tool_call_id": self.tool_call_id,
            "success": self.success,
            "status": self.status,
            "tool": self.tool,
            "message": self.message,
            "message_code": self.message_code,
            "display_message": self.display_message,
            "operation_kind": self.operation_kind,
            "data": ensure_json_value(self.data, "ToolResult.data"),
            "error": self.error.to_dict() if isinstance(self.error, ToolError) else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ToolResult":
        data = _mapping(value, "ToolResult")
        return cls(
            success=bool(_required(data, "success", "ToolResult")),
            tool=_safe_string(_required(data, "tool", "ToolResult"), "unknown"),
            message=_safe_string(_required(data, "message", "ToolResult")),
            data=dict(data.get("data", {})) if isinstance(data.get("data", {}), Mapping) else {},
            error=data.get("error"),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            tool_call_id=_safe_string(data.get("tool_call_id"), _stable_id("call")),
            status=_safe_string(data.get("status")),
            message_code=_safe_string(data.get("message_code")),
            display_message=_safe_string(data.get("display_message")),
            operation_kind=_safe_string(data.get("operation_kind"), "unknown"),
        )


@dataclass
class ProposedAction:
    """Provider-neutral, untrusted action proposed by rules or a model."""

    tool_name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "chat_fallback"
    raw_provider_type: str = ""
    needs_clarification: bool = False
    warnings: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    kind: str = "action"
    evidence: List[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    proposal_id: str = field(default_factory=lambda: _stable_id("proposal"))
    call_id: str = field(default_factory=lambda: _stable_id("call"))

    def __post_init__(self) -> None:
        self.tool_name = _safe_string(self.tool_name)
        self.arguments = dict(
            ensure_json_value(dict(self.arguments), "ProposedAction.arguments")
        )
        try:
            self.confidence = max(0.0, min(float(self.confidence), 1.0))
        except (TypeError, ValueError):
            self.confidence = 0.0
        self.source = _safe_string(self.source, "chat_fallback")
        self.raw_provider_type = _safe_string(self.raw_provider_type)
        self.needs_clarification = bool(self.needs_clarification)
        self.warnings = [
            _sanitize_public_text(item)
            for item in ensure_json_value(list(self.warnings), "ProposedAction.warnings")
        ]
        self.missing_fields = [
            _safe_string(item)
            for item in ensure_json_value(
                list(self.missing_fields), "ProposedAction.missing_fields"
            )
            if _safe_string(item)
        ]
        self.kind = _safe_string(self.kind, "action")
        if self.kind not in {"action", "chat", "clarification"}:
            self.kind = "clarification"
            self.needs_clarification = True
            self.warnings.append("invalid_proposal_kind")
        self.evidence = [
            _sanitize_public_text(item)
            for item in ensure_json_value(list(self.evidence), "ProposedAction.evidence")
        ]
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.proposal_id = _safe_string(
            self.proposal_id, _stable_id("proposal")
        )
        self.call_id = _safe_string(self.call_id, _stable_id("call"))

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "call_id": self.call_id,
            "kind": self.kind,
            "tool_name": self.tool_name,
            "arguments": ensure_json_value(
                self.arguments, "ProposedAction.arguments"
            ),
            "confidence": self.confidence,
            "source": self.source,
            "raw_provider_type": self.raw_provider_type,
            "needs_clarification": self.needs_clarification,
            "warnings": list(self.warnings),
            "missing_fields": list(self.missing_fields),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProposedAction":
        data = _mapping(value, "ProposedAction")
        return cls(
            tool_name=_safe_string(data.get("tool_name")),
            arguments=(
                dict(data.get("arguments", {}))
                if isinstance(data.get("arguments", {}), Mapping)
                else {}
            ),
            confidence=data.get("confidence", 0.0),
            source=_safe_string(data.get("source"), "chat_fallback"),
            raw_provider_type=_safe_string(data.get("raw_provider_type")),
            needs_clarification=bool(data.get("needs_clarification", False)),
            warnings=(
                list(data.get("warnings", []))
                if isinstance(data.get("warnings", []), list)
                else []
            ),
            missing_fields=(
                list(data.get("missing_fields", []))
                if isinstance(data.get("missing_fields", []), list)
                else []
            ),
            kind=_safe_string(data.get("kind"), "action"),
            evidence=(
                list(data.get("evidence", []))
                if isinstance(data.get("evidence", []), list)
                else []
            ),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            proposal_id=_safe_string(data.get("proposal_id"), _stable_id("proposal")),
            call_id=_safe_string(data.get("call_id"), _stable_id("call")),
        )


@dataclass
class AssistantToolCall:
    call_id: str
    name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.call_id = _safe_string(self.call_id, _stable_id("call"))
        self.name = _safe_string(self.name)
        self.arguments = dict(
            ensure_json_value(dict(self.arguments), "AssistantToolCall.arguments")
        )
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": ensure_json_value(
                self.arguments, "AssistantToolCall.arguments"
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AssistantToolCall":
        data = _mapping(value, "AssistantToolCall")
        return cls(
            call_id=_safe_string(data.get("call_id"), _stable_id("call")),
            name=_safe_string(_required(data, "name", "AssistantToolCall")),
            arguments=(
                dict(data.get("arguments", {}))
                if isinstance(data.get("arguments", {}), Mapping)
                else {}
            ),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
        )


@dataclass
class ToolMessage:
    call_id: str
    tool_name: str
    status: str
    success: bool
    content: str
    safe_error: Optional[str] = None
    data_summary: Dict[str, object] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.call_id = _safe_string(self.call_id, _stable_id("call"))
        self.tool_name = _safe_string(self.tool_name, "unknown")
        self.status = _safe_string(self.status, "failed")
        self.success = bool(self.success)
        self.content = _sanitize_public_text(self.content)
        self.safe_error = _safe_string(self.safe_error) or None
        self.data_summary = dict(
            ensure_json_value(dict(self.data_summary), "ToolMessage.data_summary")
        )
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> "ToolMessage":
        data = result.data if isinstance(result.data, dict) else {}
        summary: Dict[str, object] = {}
        for key in ("task_id", "task_uid", "memory_id", "candidate_id", "conflict_id", "changed"):
            if key in data and isinstance(data[key], (str, int, float, bool)):
                summary[key] = data[key]
        task = data.get("task")
        if isinstance(task, dict):
            summary["task"] = {
                key: task.get(key)
                for key in ("id", "uid", "title", "done", "status")
                if key in task
            }
        for key in ("tasks", "actions", "entries", "memories", "candidates"):
            if isinstance(data.get(key), list):
                summary[f"{key}_count"] = len(data[key])
        return cls(
            call_id=result.tool_call_id,
            tool_name=result.tool,
            status=result.status,
            success=result.success,
            content=result.display_message or result.message,
            safe_error=result.error_code,
            data_summary=summary,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "success": self.success,
            "content": self.content,
            "safe_error": self.safe_error,
            "data_summary": ensure_json_value(
                self.data_summary, "ToolMessage.data_summary"
            ),
        }

    def to_provider_message(self) -> Dict[str, object]:
        import json

        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": json.dumps(
                self.to_dict(), ensure_ascii=False, separators=(",", ":")
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ToolMessage":
        data = _mapping(value, "ToolMessage")
        return cls(
            call_id=_safe_string(data.get("call_id"), _stable_id("call")),
            tool_name=_safe_string(data.get("tool_name"), "unknown"),
            status=_safe_string(data.get("status"), "failed"),
            success=bool(data.get("success", False)),
            content=_safe_string(data.get("content")),
            safe_error=_safe_string(data.get("safe_error")) or None,
            data_summary=(
                dict(data.get("data_summary", {}))
                if isinstance(data.get("data_summary", {}), Mapping)
                else {}
            ),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
        )


@dataclass
class AgentStep:
    tool: str
    arguments: Dict[str, object] = field(default_factory=dict)
    depends_on_previous: bool = True
    schema_version: str = SCHEMA_VERSION
    step_id: str = field(default_factory=lambda: _stable_id("step"))
    status: str = "pending"

    def __post_init__(self) -> None:
        self.tool = _safe_string(self.tool, "unknown")
        self.arguments = dict(ensure_json_value(dict(self.arguments), "AgentStep.arguments"))
        self.depends_on_previous = bool(self.depends_on_previous)
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.step_id = _safe_string(self.step_id, _stable_id("step"))
        self.status = _safe_string(self.status, "pending")

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "step_id": self.step_id,
            "tool": self.tool,
            "arguments": ensure_json_value(self.arguments, "AgentStep.arguments"),
            "depends_on_previous": self.depends_on_previous,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AgentStep":
        data = _mapping(value, "AgentStep")
        return cls(
            tool=_safe_string(_required(data, "tool", "AgentStep"), "unknown"),
            arguments=dict(data.get("arguments", {})) if isinstance(data.get("arguments", {}), Mapping) else {},
            depends_on_previous=bool(data.get("depends_on_previous", True)),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            step_id=_safe_string(data.get("step_id"), _stable_id("step")),
            status=_safe_string(data.get("status"), "pending"),
        )


@dataclass
class ClientAction:
    name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    expires_at: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    action_id: str = field(default_factory=lambda: _stable_id("action"))
    request_id: str = ""
    conversation_id: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    idempotency_key: str = field(default_factory=lambda: _stable_id("idem"))
    source: str = "agent"
    status: str = "requested"

    def __post_init__(self) -> None:
        self.name = _safe_string(self.name)
        self.arguments = dict(ensure_json_value(dict(self.arguments), "ClientAction.arguments"))
        self.expires_at = _safe_string(self.expires_at) or None
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.action_id = _safe_string(self.action_id, _stable_id("action"))
        self.request_id = _safe_string(self.request_id)
        self.conversation_id = _safe_string(self.conversation_id)
        self.created_at = _safe_string(self.created_at, _utc_now_iso())
        self.idempotency_key = _safe_string(
            self.idempotency_key, _stable_id("idem")
        )
        self.source = _safe_string(self.source, "agent")
        self.status = _safe_string(self.status, "requested")

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "name": self.name,
            "arguments": ensure_json_value(self.arguments, "ClientAction.arguments"),
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "idempotency_key": self.idempotency_key,
            "source": self.source,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ClientAction":
        data = _mapping(value, "ClientAction")
        return cls(
            name=_safe_string(_required(data, "name", "ClientAction")),
            arguments=dict(data.get("arguments", {})) if isinstance(data.get("arguments", {}), Mapping) else {},
            expires_at=_safe_string(data.get("expires_at")) or None,
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            action_id=_safe_string(data.get("action_id"), _stable_id("action")),
            request_id=_safe_string(data.get("request_id")),
            conversation_id=_safe_string(data.get("conversation_id")),
            created_at=_safe_string(data.get("created_at"), _utc_now_iso()),
            idempotency_key=_safe_string(
                data.get("idempotency_key"), _stable_id("idem")
            ),
            source=_safe_string(data.get("source"), "agent"),
            status=_safe_string(data.get("status"), "requested"),
        )


@dataclass
class PendingConfirmation:
    confirmation_id: str
    tool: str
    arguments: Dict[str, object]
    created_at: str
    expires_at: str
    summary: str
    schema_version: str = SCHEMA_VERSION
    conversation_id: str = ""
    call_id: str = ""
    risk_level: str = ""
    state_fingerprint: str = ""

    def __post_init__(self) -> None:
        self.confirmation_id = _safe_string(self.confirmation_id)
        self.tool = _safe_string(self.tool)
        self.arguments = dict(ensure_json_value(dict(self.arguments), "PendingConfirmation.arguments"))
        self.created_at = _safe_string(self.created_at)
        self.expires_at = _safe_string(self.expires_at)
        self.summary = _safe_string(self.summary)
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.conversation_id = _safe_string(self.conversation_id)
        self.call_id = _safe_string(self.call_id)
        self.risk_level = _safe_string(self.risk_level)
        self.state_fingerprint = _safe_string(self.state_fingerprint)

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "confirmation_id": self.confirmation_id,
            "tool": self.tool,
            "arguments": ensure_json_value(self.arguments, "PendingConfirmation.arguments"),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "summary": self.summary,
            "conversation_id": self.conversation_id,
            "call_id": self.call_id,
            "risk_level": self.risk_level,
            "state_fingerprint": self.state_fingerprint,
            # Canonical V1.6 names. Legacy keys above remain for desktop/Web
            # clients created before the tool-call protocol was frozen.
            "tool_name": self.tool,
            "immutable_arguments": ensure_json_value(
                self.arguments, "PendingConfirmation.immutable_arguments"
            ),
            "safe_summary": self.summary,
        }

    @property
    def tool_name(self) -> str:
        return self.tool

    @property
    def immutable_arguments(self) -> Dict[str, object]:
        return dict(self.arguments)

    @property
    def safe_summary(self) -> str:
        return self.summary

    @classmethod
    def from_dict(cls, value: object) -> "PendingConfirmation":
        data = _mapping(value, "PendingConfirmation")
        return cls(
            confirmation_id=_safe_string(_required(data, "confirmation_id", "PendingConfirmation")),
            tool=_safe_string(
                _required_alias(data, ["tool", "tool_name"], "PendingConfirmation")
            ),
            arguments=(
                dict(data.get("arguments", data.get("immutable_arguments", {})))
                if isinstance(
                    data.get("arguments", data.get("immutable_arguments", {})),
                    Mapping,
                )
                else {}
            ),
            created_at=_safe_string(_required(data, "created_at", "PendingConfirmation")),
            expires_at=_safe_string(_required(data, "expires_at", "PendingConfirmation")),
            summary=_safe_string(data.get("summary", data.get("safe_summary"))),
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            conversation_id=_safe_string(data.get("conversation_id")),
            call_id=_safe_string(data.get("call_id")),
            risk_level=_safe_string(data.get("risk_level")),
            state_fingerprint=_safe_string(data.get("state_fingerprint")),
        )


@dataclass
class AgentResponse:
    status: str
    message: str
    steps: List[Union[AgentStep, Mapping[str, object]]] = field(default_factory=list)
    tool_results: List[Union[ToolResult, Mapping[str, object]]] = field(default_factory=list)
    pending_confirmation: Optional[Union[PendingConfirmation, Mapping[str, object]]] = None
    client_actions: List[Union[ClientAction, Mapping[str, object]]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    request_id: str = field(default_factory=lambda: _stable_id("req"))
    conversation_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.status = _safe_string(self.status, "failed")
        self.message = _safe_string(self.message)
        self.steps = [item if isinstance(item, AgentStep) else AgentStep.from_dict(item) for item in self.steps]
        self.tool_results = [
            item if isinstance(item, ToolResult) else ToolResult.from_dict(item)
            for item in self.tool_results
        ]
        if self.pending_confirmation is not None and not isinstance(self.pending_confirmation, PendingConfirmation):
            self.pending_confirmation = PendingConfirmation.from_dict(self.pending_confirmation)
        self.schema_version = _safe_string(self.schema_version, SCHEMA_VERSION)
        self.request_id = _safe_string(self.request_id, _stable_id("req"))
        self.conversation_id = _safe_string(self.conversation_id) or None

        from modules.client_action_policy import ClientActionPolicy

        policy = ClientActionPolicy()
        self.client_actions = [policy.require_valid(item, check_expiry=False) for item in self.client_actions]

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "message": self.message,
            "steps": [item.to_dict() for item in self.steps],
            "tool_results": [item.to_dict() for item in self.tool_results],
            "pending_confirmation": (
                self.pending_confirmation.to_dict()
                if isinstance(self.pending_confirmation, PendingConfirmation)
                else None
            ),
            "client_actions": [item.to_dict() for item in self.client_actions],
        }

    @classmethod
    def from_dict(cls, value: object) -> "AgentResponse":
        data = _mapping(value, "AgentResponse")
        steps = data.get("steps", [])
        results = data.get("tool_results", [])
        actions = data.get("client_actions", [])
        return cls(
            status=_safe_string(_required(data, "status", "AgentResponse"), "failed"),
            message=_safe_string(_required(data, "message", "AgentResponse")),
            steps=list(steps) if isinstance(steps, list) else [],
            tool_results=list(results) if isinstance(results, list) else [],
            pending_confirmation=data.get("pending_confirmation"),
            client_actions=list(actions) if isinstance(actions, list) else [],
            schema_version=_safe_string(data.get("schema_version"), SCHEMA_VERSION),
            request_id=_safe_string(data.get("request_id"), _stable_id("req")),
            conversation_id=_safe_string(data.get("conversation_id")) or None,
        )
