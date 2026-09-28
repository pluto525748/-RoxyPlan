from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set


SENSITIVE_TOPIC_TERMS = {
    "health": ("肠胃", "胃", "肚子", "身体", "睡眠", "失眠", "吃", "饮食", "疼", "不舒服"),
    "relationship": ("家人", "父母", "伴侣", "关系", "家庭"),
}


@dataclass
class MemoryInteractionState:
    conversation_id: str
    state: str = "idle"
    last_listed_candidate_ids: List[int] = field(default_factory=list)
    last_listed_candidate_summaries: List[Dict[str, object]] = field(default_factory=list)
    last_selected_candidate_ids: List[int] = field(default_factory=list)
    originating_turn_id: str = ""
    pending_candidate_content: str = ""
    pending_source_text: str = ""
    created_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "state": self.state,
            "last_listed_candidate_ids": list(self.last_listed_candidate_ids),
            "last_listed_candidate_summaries": deepcopy(
                self.last_listed_candidate_summaries
            ),
            "last_selected_candidate_ids": list(self.last_selected_candidate_ids),
            "originating_turn_id": self.originating_turn_id,
            "pending_candidate_content": self.pending_candidate_content,
            "pending_source_text": self.pending_source_text,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class ConversationStateManager:
    """Runtime-only dialogue state shared by desktop and Web conversation flows."""

    def __init__(
        self,
        *,
        now_provider: Optional[Callable[[], datetime]] = None,
        memory_reference_ttl_seconds: int = 600,
    ) -> None:
        self._states: Dict[str, Dict[str, object]] = {}
        self._lock = RLock()
        self.now_provider = now_provider or datetime.now
        self.memory_reference_ttl_seconds = max(
            30, int(memory_reference_ttl_seconds)
        )

    def observe_user(self, conversation_id: str, text: str) -> Dict[str, object]:
        clean = str(text).strip()
        with self._lock:
            state = self._state(conversation_id)
            state["previous_user_message"] = str(state.get("last_user_message", ""))
            state["last_user_message"] = clean
            result: Dict[str, object] = {}
            if clean.startswith(("忘记：", "忘记:")):
                # The record body is not a new location, preference or topic
                # suppression request. Only the literal command is actionable.
                return result

            suppressed = self._suppression_request(clean)
            if suppressed:
                values = state.setdefault("suppressed_categories", set())
                values.update(suppressed)
                result["suppressed_categories"] = sorted(suppressed)

            location = self._current_location(clean)
            if location:
                state.setdefault("current_facts", {})["location"] = {
                    "value": location,
                    "source_text": clean,
                    "scope": "current_state",
                }
                result["current_location"] = location

            response_preferences = self._response_preference_update(clean)
            if response_preferences:
                state.setdefault("response_preferences", {}).update(
                    response_preferences
                )
                result["response_preferences"] = dict(response_preferences)
            return result

    def observe_assistant(self, conversation_id: str, text: str) -> None:
        with self._lock:
            state = self._state(conversation_id)
            state["last_assistant_message"] = str(text).strip()

    def observe_client_action_result(self, conversation_id: str, result: Mapping[str, object]) -> None:
        """Remember an actual same-client report, never a tool's request."""
        if not conversation_id or not isinstance(result, Mapping):
            return
        if result.get("source") not in {"desktop_dispatcher", "desktop_state_snapshot"}:
            return
        status = str(result.get("status", ""))
        if status not in {
            "accepted", "running", "finished", "completed", "failed", "rejected",
            "skipped_busy", "skipped_duplicate", "expired", "cancelled",
        } or not re.fullmatch(r"[a-z][a-z0-9_]*", str(result.get("name", ""))):
            return
        allowed = ("name", "status", "accepted", "started", "completed", "reason_code", "source", "observed_at", "state")
        with self._lock:
            self._state(conversation_id)["prior_client_action"] = {
                key: deepcopy(result.get(key)) for key in allowed if key in result
            }

    def observe_memory_facts(self, conversation_id: str, facts: Iterable[Mapping[str, object]]) -> None:
        """Keep a small in-process cache of real tool-read record references."""
        with self._lock:
            state = self._state(conversation_id)
            recent = state.setdefault("prior_memory_facts", [])
            for fact in facts:
                if not isinstance(fact, Mapping):
                    continue
                try:
                    memory_id = int(fact.get("memory_id", fact.get("id", 0)) or 0)
                except (TypeError, ValueError):
                    continue
                if memory_id <= 0 or not str(fact.get("content", "")).strip():
                    continue
                value = dict(fact)
                value["memory_id"] = memory_id
                value["observed_at"] = self.now_provider().astimezone().isoformat(timespec="seconds")
                recent[:] = [item for item in recent if item.get("memory_id") != memory_id]
                recent.append(deepcopy(value))
            del recent[:-5]

    def observe_memory_operation(self, conversation_id: str, operation: Mapping[str, object]) -> None:
        if not isinstance(operation, Mapping) or operation.get("status") != "success":
            return
        with self._lock:
            state = self._state(conversation_id)
            state["prior_memory_operation"] = deepcopy(dict(operation))
            if operation.get("tool") in {"delete_memory", "archive_memory"}:
                state["prior_memory_facts"] = [
                    item for item in state.get("prior_memory_facts", [])
                    if item.get("memory_id") != operation.get("memory_id")
                ]

    def observe_tool_result(self, conversation_id: str, result: Dict[str, object]) -> None:
        if not bool(result.get("success", False)):
            return
        data = result.get("data", {})
        data = data if isinstance(data, dict) else {}
        task = data.get("task")
        with self._lock:
            state = self._state(conversation_id)
            state["last_tool"] = str(result.get("tool", ""))
            state["last_tool_result"] = {
                "tool": str(result.get("tool", "")),
                "tool_call_id": str(result.get("tool_call_id", "")),
                "status": str(result.get("status", "")),
            }
            if isinstance(task, dict):
                normalized = {
                    key: deepcopy(task.get(key))
                    for key in ("id", "uid", "title", "done", "status")
                    if key in task
                }
                state["last_task"] = normalized
                recent = state.setdefault("recent_tasks", [])
                identity = str(normalized.get("uid") or normalized.get("id") or "")
                recent[:] = [
                    item
                    for item in recent
                    if str(item.get("uid") or item.get("id") or "") != identity
                ]
                recent.append(deepcopy(normalized))
                del recent[:-5]
            elif str(result.get("tool", "")) in {
                "add_plan",
                "complete_plan",
                "delete_plan",
                "update_plan",
                "reschedule_plan",
                "reopen_plan",
                "cancel_plan",
            }:
                # Never pair a newly verified plan operation with an older
                # task title when the new ToolResult contains no task object.
                state["last_task"] = None
            tool = str(result.get("tool", ""))
            if tool in {"list_memory_candidates", "show_memory_candidates"}:
                candidates = data.get("candidates", [])
                if isinstance(candidates, list):
                    self._remember_candidate_list(
                        state,
                        conversation_id,
                        candidates,
                        originating_turn_id=str(result.get("tool_call_id", "")),
                    )
            elif tool in {
                "create_memory_candidate",
                "request_add_memory",
                "queue_memory_candidate",
                "accept_memory_candidate",
                "accept_memory_candidates",
                "accept_all_memory_candidates",
                "reject_memory_candidate",
                "reject_memory_candidates",
            }:
                selected = self._candidate_ids_from_result(data)
                self._set_memory_interaction(
                    state,
                    MemoryInteractionState(
                        conversation_id=self._key(conversation_id),
                        state="completed",
                        last_listed_candidate_ids=self._existing_candidate_ids(state),
                        last_listed_candidate_summaries=self._existing_candidate_summaries(state),
                        last_selected_candidate_ids=selected,
                        originating_turn_id=str(result.get("tool_call_id", "")),
                    ),
                )

    def remember_memory_candidates(
        self,
        conversation_id: str,
        candidates: Iterable[Mapping[str, object]],
        *,
        originating_turn_id: str = "",
    ) -> Dict[str, object]:
        with self._lock:
            state = self._state(conversation_id)
            return self._remember_candidate_list(
                state,
                conversation_id,
                candidates,
                originating_turn_id=originating_turn_id,
            ).to_dict()

    def prepare_memory_candidate_creation(
        self,
        conversation_id: str,
        content: str,
        *,
        source_text: str = "",
        originating_turn_id: str = "",
    ) -> Dict[str, object]:
        with self._lock:
            state = self._state(conversation_id)
            interaction = MemoryInteractionState(
                conversation_id=self._key(conversation_id),
                state="awaiting_candidate_confirmation",
                originating_turn_id=str(originating_turn_id),
                pending_candidate_content=str(content).strip(),
                pending_source_text=str(source_text or content).strip(),
            )
            self._set_memory_interaction(state, interaction)
            return interaction.to_dict()

    def set_memory_candidate_selection(
        self,
        conversation_id: str,
        candidate_ids: Iterable[object],
        *,
        awaiting_confirmation: bool = False,
        originating_turn_id: str = "",
    ) -> Dict[str, object]:
        selected = self._positive_ids(candidate_ids)
        with self._lock:
            state = self._state(conversation_id)
            interaction = MemoryInteractionState(
                conversation_id=self._key(conversation_id),
                state=(
                    "awaiting_candidate_confirmation"
                    if awaiting_confirmation
                    else "awaiting_candidate_selection"
                ),
                last_listed_candidate_ids=self._existing_candidate_ids(state),
                last_listed_candidate_summaries=self._existing_candidate_summaries(state),
                last_selected_candidate_ids=selected,
                originating_turn_id=str(originating_turn_id),
            )
            self._set_memory_interaction(state, interaction)
            return interaction.to_dict()

    def begin_memory_candidate_review(
        self,
        conversation_id: str,
        candidate_ids: Iterable[object],
        *,
        originating_turn_id: str = "",
    ) -> Dict[str, object]:
        selected = self._positive_ids(candidate_ids)
        with self._lock:
            state = self._state(conversation_id)
            interaction = MemoryInteractionState(
                conversation_id=self._key(conversation_id),
                state="processing_candidate_review",
                last_listed_candidate_ids=self._existing_candidate_ids(state),
                last_listed_candidate_summaries=self._existing_candidate_summaries(state),
                last_selected_candidate_ids=selected,
                originating_turn_id=str(originating_turn_id),
            )
            self._set_memory_interaction(state, interaction)
            return interaction.to_dict()

    def memory_interaction_context(self, conversation_id: str) -> Dict[str, object]:
        with self._lock:
            state = self._state(conversation_id)
            interaction = state.get("memory_interaction")
            if not isinstance(interaction, MemoryInteractionState):
                return MemoryInteractionState(
                    conversation_id=self._key(conversation_id)
                ).to_dict()
            if self._is_expired(interaction):
                interaction.state = "expired"
                interaction.last_listed_candidate_ids = []
                interaction.last_listed_candidate_summaries = []
                interaction.last_selected_candidate_ids = []
                interaction.pending_candidate_content = ""
                interaction.pending_source_text = ""
            return interaction.to_dict()

    def complete_memory_interaction(self, conversation_id: str) -> None:
        with self._lock:
            state = self._state(conversation_id)
            interaction = state.get("memory_interaction")
            if isinstance(interaction, MemoryInteractionState):
                interaction.state = "completed"
                interaction.pending_candidate_content = ""
                interaction.pending_source_text = ""

    def suppressed_categories(self, conversation_id: str) -> Set[str]:
        with self._lock:
            return set(self._state(conversation_id).get("suppressed_categories", set()))

    def current_facts(self, conversation_id: str) -> Dict[str, object]:
        with self._lock:
            return deepcopy(dict(self._state(conversation_id).get("current_facts", {})))

    def reference_context(self, conversation_id: str) -> Dict[str, object]:
        with self._lock:
            state = self._state(conversation_id)
            return {
                "conversation_id": str(conversation_id).strip() or "local_default",
                "last_task": deepcopy(state.get("last_task")),
                "recent_tasks": deepcopy(state.get("recent_tasks", [])),
                "last_tool_result": deepcopy(state.get("last_tool_result")),
                "prior_memory_facts": deepcopy(state.get("prior_memory_facts", [])),
                "prior_memory_operation": deepcopy(state.get("prior_memory_operation", {})),
                "prior_client_action": deepcopy(state.get("prior_client_action", {})),
                "last_user_message": str(state.get("previous_user_message", "")),
                "previous_user_message": str(state.get("previous_user_message", "")),
                "last_assistant_message": str(state.get("last_assistant_message", "")),
                "current_facts": deepcopy(state.get("current_facts", {})),
                "response_preferences": deepcopy(
                    state.get("response_preferences", {})
                ),
                "memory_interaction": self.memory_interaction_context(
                    conversation_id
                ),
            }

    def reset(self, conversation_id: str) -> None:
        with self._lock:
            self._states.pop(str(conversation_id), None)

    @classmethod
    def explicit_sensitive_topics(cls, text: str) -> Set[str]:
        clean = str(text).lower()
        return {
            category
            for category, terms in SENSITIVE_TOPIC_TERMS.items()
            if any(term in clean for term in terms)
        }

    def should_suppress(
        self,
        conversation_id: str,
        category: str,
        current_text: str,
    ) -> bool:
        suppressed = self.suppressed_categories(conversation_id)
        if category not in suppressed:
            return False
        # A direct user question about the topic temporarily overrides suppression.
        return category not in self.explicit_sensitive_topics(current_text)

    def _state(self, conversation_id: str) -> Dict[str, object]:
        key = str(conversation_id).strip() or "local_default"
        return self._states.setdefault(
            key,
            {
                "suppressed_categories": set(),
                "current_facts": {},
                "response_preferences": {},
                "last_task": None,
                "recent_tasks": [],
                "last_tool": "",
                "last_tool_result": None,
                "prior_memory_facts": [],
                "prior_memory_operation": {},
                "prior_client_action": {},
                "last_user_message": "",
                "previous_user_message": "",
                "last_assistant_message": "",
                "memory_interaction": MemoryInteractionState(
                    conversation_id=key
                ),
            },
        )

    def _remember_candidate_list(
        self,
        state: Dict[str, object],
        conversation_id: str,
        candidates: Iterable[Mapping[str, object]],
        *,
        originating_turn_id: str = "",
    ) -> MemoryInteractionState:
        normalized = [item for item in candidates if isinstance(item, Mapping)]
        ids = self._positive_ids(item.get("id") for item in normalized)
        summaries = [
            {
                "candidate_id": int(item.get("id", 0)),
                "summary": str(item.get("content", "")).strip()[:120],
            }
            for item in normalized
            if self._positive_id(item.get("id")) is not None
        ]
        interaction = MemoryInteractionState(
            conversation_id=self._key(conversation_id),
            state="candidates_listed",
            last_listed_candidate_ids=ids,
            last_listed_candidate_summaries=summaries,
            originating_turn_id=str(originating_turn_id),
        )
        self._set_memory_interaction(state, interaction)
        return interaction

    def _set_memory_interaction(
        self,
        state: Dict[str, object],
        interaction: MemoryInteractionState,
    ) -> None:
        now = self.now_provider()
        interaction.created_at = now.isoformat(timespec="seconds")
        interaction.expires_at = (
            now + timedelta(seconds=self.memory_reference_ttl_seconds)
        ).isoformat(timespec="seconds")
        state["memory_interaction"] = interaction

    def _is_expired(self, interaction: MemoryInteractionState) -> bool:
        if interaction.state in {"idle", "completed", "expired"}:
            return interaction.state == "expired"
        try:
            expires_at = datetime.fromisoformat(interaction.expires_at)
        except (TypeError, ValueError):
            return False
        return self.now_provider() >= expires_at

    @staticmethod
    def _positive_id(value: object) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _positive_ids(cls, values: Iterable[object]) -> List[int]:
        result: List[int] = []
        for value in values:
            parsed = cls._positive_id(value)
            if parsed is not None and parsed not in result:
                result.append(parsed)
        return result

    @staticmethod
    def _key(conversation_id: str) -> str:
        return str(conversation_id).strip() or "local_default"

    @staticmethod
    def _existing_candidate_ids(state: Dict[str, object]) -> List[int]:
        interaction = state.get("memory_interaction")
        return (
            list(interaction.last_listed_candidate_ids)
            if isinstance(interaction, MemoryInteractionState)
            else []
        )

    @staticmethod
    def _existing_candidate_summaries(
        state: Dict[str, object]
    ) -> List[Dict[str, object]]:
        interaction = state.get("memory_interaction")
        return (
            deepcopy(interaction.last_listed_candidate_summaries)
            if isinstance(interaction, MemoryInteractionState)
            else []
        )

    @classmethod
    def _candidate_ids_from_result(cls, data: Dict[str, object]) -> List[int]:
        values: List[object] = []
        for key in (
            "accepted_ids",
            "rejected_ids",
            "already_processed_ids",
            "failed_ids",
            "candidate_ids",
        ):
            item = data.get(key, [])
            if isinstance(item, list):
                values.extend(item)
        operation = data.get("memory_operation")
        if isinstance(operation, dict):
            values.append(operation.get("candidate_id"))
        return cls._positive_ids(values)

    @staticmethod
    def _suppression_request(text: str) -> Set[str]:
        if not re.search(r"(?:不要|别|不用).{0,8}(?:再|总是|一直)?(?:提|说|提醒)", text):
            return set()
        categories = ConversationStateManager.explicit_sensitive_topics(text)
        return categories

    @staticmethod
    def _response_preference_update(text: str) -> Dict[str, object]:
        value = re.sub(r"\s+", "", str(text or ""))
        avoids_repetition = any(
            term in value
            for term in ("不要固定", "别固定", "不要总是", "别总是", "不要每次", "别每次")
        )
        names_format = any(term in value for term in ("版式", "格式", "模板", "套路"))
        names_reply = any(term in value for term in ("回答", "回复", "说"))
        if avoids_repetition and names_format and names_reply:
            return {"avoid_fixed_memory_format": True}
        return {}

    @staticmethod
    def _current_location(text: str) -> Optional[str]:
        patterns: Iterable[str] = (
            r"我现在(?:已经)?(?:回到|回|到了|在)\s*([\u4e00-\u9fff]{2,8}?)(?:了|上学|实习|工作|生活)?$",
            r"我目前(?:正在)?(?:在|住在|位于)\s*([\u4e00-\u9fff]{2,8}?)(?:上学|实习|工作|生活)?$",
        )
        clean = str(text).strip().strip("。.!！?？")
        for pattern in patterns:
            match = re.fullmatch(pattern, clean)
            if match:
                value = match.group(1)
                value = re.sub(r"(?:这边|这里|了)$", "", value).strip()
                return value or None
        return None
