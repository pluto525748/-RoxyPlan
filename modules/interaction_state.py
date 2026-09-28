from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping
from uuid import uuid4


SCHEMA_VERSION = "1.0"

VALID_STATES = {
    "idle",
    "candidates_listed",
    "awaiting_clarification",
    "awaiting_choice",
    "awaiting_confirmation",
    "awaiting_candidate_selection",
    "awaiting_tool_result",
    "partial_success",
    "completed",
    "cancelled",
    "expired",
}

VALID_KINDS = {
    "",
    "missing_slots",
    "advice_choice",
    "advice_or_action_choice",
    "object_selection",
    "action_log_offer",
    "memory_candidate_review",
    "formal_memory_save",
    "dangerous_tool",
    "parameter_retry",
    "plan_target_selection",
    "assistant_plan_offer",
    "assistant_plan_refinement",
    "assistant_plan_selection",
    "memory_target_selection",
    "memory_or_plan_destination",
    "missing_slot",
    "multi_action_review",
    "tool_parameter_retry",
    "plan_update",
    "duplicate_plan_resolution",
    "suggestion_batch_duplicate_resolution",
    "plan_batch_completion",
    "action_log_creation",
}


@dataclass
class InteractionState:
    conversation_id: str
    user_id: str = "local_user"
    state: str = "idle"
    interaction_kind: str = ""
    domain: str = ""
    request_mode: str = ""
    original_user_text: str = ""
    originating_turn_id: str = ""
    known_fields: Dict[str, object] = field(default_factory=dict)
    action_candidates: List[Dict[str, object]] = field(default_factory=list)
    candidate_objects: List[Dict[str, object]] = field(default_factory=list)
    suggested_options: List[Dict[str, object]] = field(default_factory=list)
    tool_call_ids: List[str] = field(default_factory=list)
    resolved_object_ids: List[str] = field(default_factory=list)
    listed_object_ids: List[str] = field(default_factory=list)
    selected_object_ids: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    immutable_arguments: Dict[str, object] = field(default_factory=dict)
    action_preview: Dict[str, object] = field(default_factory=dict)
    last_user_fact: str = ""
    safe_summary: str = ""
    last_created_memory_id: str = ""
    last_created_memory_source_turn_id: str = ""
    last_created_memory_created_at: str = ""
    last_created_memory_expires_at: str = ""
    last_read_mode: str = ""
    last_read_intent: str = ""
    last_read_tool: str = ""
    last_read_arguments: Dict[str, object] = field(default_factory=dict)
    last_read_summary: str = ""
    last_read_snapshot: Dict[str, object] = field(default_factory=dict)
    last_suggestion_snapshot: Dict[str, object] = field(default_factory=dict)
    retry_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    version: int = 1
    consumed: bool = False
    schema_version: str = SCHEMA_VERSION
    interaction_id: str = field(
        default_factory=lambda: "interaction_" + uuid4().hex
    )

    def __post_init__(self) -> None:
        self.conversation_id = str(self.conversation_id).strip()
        self.user_id = str(self.user_id or "local_user").strip()
        self.state = str(self.state or "idle")
        if self.state not in VALID_STATES:
            self.state = "idle"
        self.interaction_kind = str(self.interaction_kind or "")
        if self.interaction_kind not in VALID_KINDS:
            self.interaction_kind = ""
        self.domain = str(self.domain or "").strip()
        self.request_mode = str(self.request_mode or "").strip()
        self.original_user_text = str(self.original_user_text or "").strip()
        self.known_fields = _json_mapping(self.known_fields)
        self.action_candidates = [
            dict(item) for item in self.action_candidates if isinstance(item, Mapping)
        ]
        self.candidate_objects = [
            _json_mapping(item)
            for item in self.candidate_objects
            if isinstance(item, Mapping)
        ]
        self.suggested_options = [
            _json_mapping(item)
            for item in self.suggested_options
            if isinstance(item, Mapping)
        ]
        self.tool_call_ids = _strings(self.tool_call_ids)
        self.resolved_object_ids = _strings(self.resolved_object_ids)
        self.listed_object_ids = _strings(self.listed_object_ids)
        self.selected_object_ids = _strings(self.selected_object_ids)
        self.missing_fields = _strings(self.missing_fields)
        self.immutable_arguments = _json_mapping(self.immutable_arguments)
        self.action_preview = _json_mapping(self.action_preview)
        self.last_user_fact = str(self.last_user_fact or "").strip()
        self.safe_summary = str(self.safe_summary or "").strip()
        self.last_created_memory_id = str(self.last_created_memory_id or "").strip()
        self.last_created_memory_source_turn_id = str(self.last_created_memory_source_turn_id or "").strip()
        self.last_created_memory_created_at = str(self.last_created_memory_created_at or "").strip()
        self.last_created_memory_expires_at = str(self.last_created_memory_expires_at or "").strip()
        self.last_read_mode = str(self.last_read_mode or "").strip()
        self.last_read_intent = str(self.last_read_intent or "").strip()
        self.last_read_tool = str(self.last_read_tool or "").strip()
        self.last_read_arguments = _json_mapping(self.last_read_arguments)
        self.last_read_summary = str(self.last_read_summary or "").strip()[:800]
        self.last_read_snapshot = _json_mapping(self.last_read_snapshot)
        self.last_suggestion_snapshot = _json_mapping(self.last_suggestion_snapshot)
        self.retry_count = max(0, int(self.retry_count or 0))
        self.version = max(1, int(self.version or 1))
        self.consumed = bool(self.consumed)
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)
        self.interaction_id = str(self.interaction_id or "interaction_" + uuid4().hex)

    @property
    def pending(self) -> bool:
        return not self.consumed and self.state in {
            "candidates_listed",
            "awaiting_clarification",
            "awaiting_choice",
            "awaiting_confirmation",
            "awaiting_candidate_selection",
            "awaiting_tool_result",
        }

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "interaction_id": self.interaction_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "originating_turn_id": self.originating_turn_id,
            "state": self.state,
            "interaction_kind": self.interaction_kind,
            "domain": self.domain,
            "request_mode": self.request_mode,
            "original_user_text": self.original_user_text,
            "known_fields": dict(self.known_fields),
            "action_candidates": [dict(item) for item in self.action_candidates],
            "candidate_objects": [dict(item) for item in self.candidate_objects],
            "suggested_options": [dict(item) for item in self.suggested_options],
            "tool_call_ids": list(self.tool_call_ids),
            "resolved_object_ids": list(self.resolved_object_ids),
            "listed_object_ids": list(self.listed_object_ids),
            "selected_object_ids": list(self.selected_object_ids),
            "missing_fields": list(self.missing_fields),
            "immutable_arguments": dict(self.immutable_arguments),
            "action_preview": dict(self.action_preview),
            "last_user_fact": self.last_user_fact,
            "safe_summary": self.safe_summary,
            "last_created_memory_id": self.last_created_memory_id,
            "last_created_memory_source_turn_id": self.last_created_memory_source_turn_id,
            "last_created_memory_created_at": self.last_created_memory_created_at,
            "last_created_memory_expires_at": self.last_created_memory_expires_at,
            "last_read_mode": self.last_read_mode,
            "last_read_intent": self.last_read_intent,
            "last_read_tool": self.last_read_tool,
            "last_read_arguments": dict(self.last_read_arguments),
            "last_read_summary": self.last_read_summary,
            "last_read_snapshot": dict(self.last_read_snapshot),
            "last_suggestion_snapshot": dict(self.last_suggestion_snapshot),
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "version": self.version,
            "consumed": self.consumed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "InteractionState":
        data = dict(value or {})
        return cls(
            conversation_id=str(data.get("conversation_id", "")),
            user_id=str(data.get("user_id", "local_user")),
            state=str(data.get("state", "idle")),
            interaction_kind=str(data.get("interaction_kind", "")),
            domain=str(data.get("domain", "")),
            request_mode=str(data.get("request_mode", "")),
            original_user_text=str(data.get("original_user_text", "")),
            originating_turn_id=str(data.get("originating_turn_id", "")),
            known_fields=data.get("known_fields", {})
            if isinstance(data.get("known_fields"), Mapping)
            else {},
            action_candidates=list(data.get("action_candidates", []))
            if isinstance(data.get("action_candidates"), list)
            else [],
            candidate_objects=list(data.get("candidate_objects", []))
            if isinstance(data.get("candidate_objects"), list)
            else [],
            suggested_options=list(data.get("suggested_options", []))
            if isinstance(data.get("suggested_options"), list)
            else [],
            tool_call_ids=list(data.get("tool_call_ids", []))
            if isinstance(data.get("tool_call_ids"), list)
            else [],
            resolved_object_ids=list(data.get("resolved_object_ids", []))
            if isinstance(data.get("resolved_object_ids"), list)
            else [],
            listed_object_ids=list(data.get("listed_object_ids", []))
            if isinstance(data.get("listed_object_ids"), list)
            else [],
            selected_object_ids=list(data.get("selected_object_ids", []))
            if isinstance(data.get("selected_object_ids"), list)
            else [],
            missing_fields=list(data.get("missing_fields", []))
            if isinstance(data.get("missing_fields"), list)
            else [],
            immutable_arguments=data.get("immutable_arguments", {})
            if isinstance(data.get("immutable_arguments"), Mapping)
            else {},
            action_preview=data.get("action_preview", {})
            if isinstance(data.get("action_preview"), Mapping)
            else {},
            last_user_fact=str(data.get("last_user_fact", "")),
            safe_summary=str(data.get("safe_summary", "")),
            last_created_memory_id=str(data.get("last_created_memory_id", "")),
            last_created_memory_source_turn_id=str(data.get("last_created_memory_source_turn_id", "")),
            last_created_memory_created_at=str(data.get("last_created_memory_created_at", "")),
            last_created_memory_expires_at=str(data.get("last_created_memory_expires_at", "")),
            last_read_mode=str(data.get("last_read_mode", "")),
            last_read_intent=str(data.get("last_read_intent", "")),
            last_read_tool=str(data.get("last_read_tool", "")),
            last_read_arguments=data.get("last_read_arguments", {})
            if isinstance(data.get("last_read_arguments"), Mapping)
            else {},
            last_read_summary=str(data.get("last_read_summary", "")),
            last_read_snapshot=data.get("last_read_snapshot", {})
            if isinstance(data.get("last_read_snapshot"), Mapping)
            else {},
            last_suggestion_snapshot=data.get("last_suggestion_snapshot", {})
            if isinstance(data.get("last_suggestion_snapshot"), Mapping)
            else {},
            retry_count=int(data.get("retry_count", 0) or 0),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            expires_at=str(data.get("expires_at", "")),
            version=int(data.get("version", 1) or 1),
            consumed=bool(data.get("consumed", False)),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
            interaction_id=str(data.get("interaction_id", "")),
        )


def _strings(values: object) -> List[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: List[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in result:
            result.append(item)
    return result


def _json_mapping(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: Dict[str, object] = {}
    for key, item in value.items():
        name = str(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            result[name] = item
        elif isinstance(item, list):
            result[name] = _json_list(item)
        elif isinstance(item, Mapping):
            result[name] = _json_mapping(item)
    return result


def _json_list(value: object) -> List[object]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[object] = []
    for item in value:
        if item is None or isinstance(item, (str, int, float, bool)):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(_json_mapping(item))
        elif isinstance(item, (list, tuple)):
            result.append(_json_list(item))
    return result
