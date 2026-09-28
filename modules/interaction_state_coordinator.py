from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from modules.interaction_state import InteractionState
from modules.suggestion_snapshot import SuggestionSnapshot


@dataclass
class InteractionDecision:
    handled: bool = False
    action: str = "none"
    interaction: Optional[InteractionState] = None
    selected_object_ids: List[str] = field(default_factory=list)
    message: str = ""


class InteractionStateCoordinator:
    """Runtime-only coordinator for references, clarification and confirmation."""

    EXTENDED_CONTINUATION_STATES = {
        "awaiting_clarification",
        "awaiting_choice",
    }
    TIMED_PENDING_STATES = {
        "candidates_listed",
        "awaiting_clarification",
        "awaiting_choice",
        "awaiting_confirmation",
        "awaiting_candidate_selection",
        "awaiting_tool_result",
    }

    CONTROL_TERMS = {
        "确认",
        "确认吧",
        "是的",
        "执行",
        "执行这条",
        "继续",
        "取消",
        "算了",
        "不用了",
        "第二个",
        "第一个",
        "最后一个",
        "就这个",
        "这个",
        "这条",
        "全部",
    }

    def __init__(
        self,
        *,
        now_provider: Callable[[], datetime] = datetime.now,
        ttl_seconds: int = 300,
        continuation_ttl_seconds: Optional[int] = None,
        confirmation_ttl_seconds: Optional[int] = None,
        user_id: str = "local_user",
    ) -> None:
        self.now_provider = now_provider
        self.ttl_seconds = max(10, int(ttl_seconds))
        self.continuation_ttl_seconds = max(
            10,
            int(
                self.ttl_seconds
                if continuation_ttl_seconds is None
                else continuation_ttl_seconds
            ),
        )
        self.confirmation_ttl_seconds = max(
            10,
            int(
                self.ttl_seconds
                if confirmation_ttl_seconds is None
                else confirmation_ttl_seconds
            ),
        )
        self.user_id = str(user_id or "local_user")
        self._states: Dict[str, InteractionState] = {}
        self._lock = RLock()

    def current(self, conversation_id: str) -> InteractionState:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return InteractionState(conversation_id=key, user_id=self.user_id)
            if self._expired(state):
                self._transition(state, "expired")
                state.consumed = True
            return InteractionState.from_dict(state.to_dict())

    def start(
        self,
        conversation_id: str,
        interaction_kind: str,
        state: str,
        *,
        originating_turn_id: str = "",
        domain: str = "",
        request_mode: str = "",
        original_user_text: str = "",
        known_fields: Optional[Mapping[str, object]] = None,
        action_candidates: Optional[Iterable[Mapping[str, object]]] = None,
        candidate_objects: Optional[Iterable[Mapping[str, object]]] = None,
        suggested_options: Optional[Iterable[Mapping[str, object]]] = None,
        tool_call_ids: Optional[Iterable[object]] = None,
        resolved_object_ids: Optional[Iterable[object]] = None,
        listed_object_ids: Optional[Iterable[object]] = None,
        selected_object_ids: Optional[Iterable[object]] = None,
        missing_fields: Optional[Iterable[object]] = None,
        immutable_arguments: Optional[Mapping[str, object]] = None,
        action_preview: Optional[Mapping[str, object]] = None,
        last_user_fact: str = "",
        safe_summary: str = "",
    ) -> InteractionState:
        key = self._key(conversation_id)
        now = self.now_provider()
        ttl_seconds = self._ttl_seconds_for_state(state)
        with self._lock:
            previous = self._states.get(key)
        interaction = InteractionState(
            conversation_id=key,
            user_id=self.user_id,
            state=state,
            interaction_kind=interaction_kind,
            domain=str(domain),
            request_mode=str(request_mode),
            original_user_text=str(original_user_text),
            originating_turn_id=str(originating_turn_id),
            known_fields=dict(known_fields or {}),
            action_candidates=list(action_candidates or []),
            candidate_objects=list(candidate_objects or []),
            suggested_options=list(suggested_options or []),
            tool_call_ids=self._ids(tool_call_ids),
            resolved_object_ids=self._ids(resolved_object_ids),
            listed_object_ids=self._ids(listed_object_ids),
            selected_object_ids=self._ids(selected_object_ids),
            missing_fields=self._ids(missing_fields),
            immutable_arguments=dict(immutable_arguments or {}),
            action_preview=dict(action_preview or {}),
            last_user_fact=str(last_user_fact),
            safe_summary=str(safe_summary).strip(),
            created_at=now.isoformat(timespec="seconds"),
            updated_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(
                timespec="seconds"
            ),
            last_created_memory_id=(
                previous.last_created_memory_id if previous else ""
            ),
            last_created_memory_source_turn_id=(
                previous.last_created_memory_source_turn_id if previous else ""
            ),
            last_created_memory_created_at=(
                previous.last_created_memory_created_at if previous else ""
            ),
            last_created_memory_expires_at=(
                previous.last_created_memory_expires_at if previous else ""
            ),
            last_read_mode=previous.last_read_mode if previous else "",
            last_read_intent=previous.last_read_intent if previous else "",
            last_read_tool=previous.last_read_tool if previous else "",
            last_read_arguments=(previous.last_read_arguments if previous else {}),
            last_read_summary=previous.last_read_summary if previous else "",
            last_read_snapshot=(previous.last_read_snapshot if previous else {}),
            last_suggestion_snapshot=(
                previous.last_suggestion_snapshot if previous else {}
            ),
        )
        with self._lock:
            self._states[key] = interaction
        self._log_transition(previous.state if previous else "idle", state, key)
        return InteractionState.from_dict(interaction.to_dict())

    def record_candidates(
        self,
        conversation_id: str,
        interaction_kind: str,
        object_ids: Iterable[object],
        *,
        originating_turn_id: str = "",
        action_candidates: Optional[Iterable[Mapping[str, object]]] = None,
        safe_summary: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "candidates_listed",
            originating_turn_id=originating_turn_id,
            action_candidates=action_candidates,
            listed_object_ids=object_ids,
            safe_summary=safe_summary,
        )

    def awaiting_clarification(
        self,
        conversation_id: str,
        interaction_kind: str,
        *,
        missing_fields: Iterable[object] = (),
        candidates: Iterable[Mapping[str, object]] = (),
        candidate_objects: Iterable[Mapping[str, object]] = (),
        listed_object_ids: Iterable[object] = (),
        immutable_arguments: Optional[Mapping[str, object]] = None,
        domain: str = "",
        request_mode: str = "",
        original_user_text: str = "",
        known_fields: Optional[Mapping[str, object]] = None,
        suggested_options: Iterable[Mapping[str, object]] = (),
        last_user_fact: str = "",
        safe_summary: str = "",
        originating_turn_id: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "awaiting_clarification",
            originating_turn_id=originating_turn_id,
            domain=domain,
            request_mode=request_mode,
            original_user_text=original_user_text,
            known_fields=known_fields,
            action_candidates=candidates,
            candidate_objects=candidate_objects,
            suggested_options=suggested_options,
            listed_object_ids=listed_object_ids,
            missing_fields=missing_fields,
            immutable_arguments=immutable_arguments,
            last_user_fact=last_user_fact,
            safe_summary=safe_summary,
        )

    def awaiting_choice(
        self,
        conversation_id: str,
        interaction_kind: str,
        *,
        options: Iterable[Mapping[str, object]] = (),
        listed_object_ids: Iterable[object] = (),
        immutable_arguments: Optional[Mapping[str, object]] = None,
        domain: str = "",
        request_mode: str = "",
        original_user_text: str = "",
        known_fields: Optional[Mapping[str, object]] = None,
        safe_summary: str = "",
        originating_turn_id: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "awaiting_choice",
            originating_turn_id=originating_turn_id,
            domain=domain,
            request_mode=request_mode,
            original_user_text=original_user_text,
            known_fields=known_fields,
            suggested_options=options,
            listed_object_ids=listed_object_ids,
            immutable_arguments=immutable_arguments,
            safe_summary=safe_summary,
        )

    def awaiting_confirmation(
        self,
        conversation_id: str,
        interaction_kind: str,
        *,
        selected_object_ids: Iterable[object] = (),
        immutable_arguments: Optional[Mapping[str, object]] = None,
        action_preview: Optional[Mapping[str, object]] = None,
        domain: str = "",
        request_mode: str = "execute",
        original_user_text: str = "",
        known_fields: Optional[Mapping[str, object]] = None,
        last_user_fact: str = "",
        safe_summary: str = "",
        originating_turn_id: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "awaiting_confirmation",
            originating_turn_id=originating_turn_id,
            domain=domain,
            request_mode=request_mode,
            original_user_text=original_user_text,
            known_fields=known_fields,
            selected_object_ids=selected_object_ids,
            immutable_arguments=immutable_arguments,
            action_preview=action_preview,
            last_user_fact=last_user_fact,
            safe_summary=safe_summary,
        )

    def update(
        self,
        conversation_id: str,
        **changes: object,
    ) -> InteractionState:
        """Update a pending interaction without replacing its stable identity."""
        key = self._key(conversation_id)
        allowed = {
            "state",
            "interaction_kind",
            "domain",
            "request_mode",
            "original_user_text",
            "known_fields",
            "action_candidates",
            "candidate_objects",
            "suggested_options",
            "tool_call_ids",
            "resolved_object_ids",
            "listed_object_ids",
            "selected_object_ids",
            "missing_fields",
            "immutable_arguments",
            "action_preview",
            "last_user_fact",
            "safe_summary",
            "last_created_memory_id",
            "last_created_memory_source_turn_id",
            "last_created_memory_created_at",
            "last_created_memory_expires_at",
            "last_read_mode",
            "last_read_intent",
            "last_read_tool",
            "last_read_arguments",
            "last_read_summary",
            "last_read_snapshot",
            "last_suggestion_snapshot",
            "retry_count",
            "consumed",
        }
        with self._lock:
            current = self._states.get(key)
            if current is None:
                current = InteractionState(conversation_id=key, user_id=self.user_id)
            previous = current.state
            data = current.to_dict()
            for name, value in changes.items():
                if name in allowed:
                    data[name] = value
            now = self.now_provider()
            target_state = str(data.get("state", current.state))
            if target_state != previous and target_state in self.TIMED_PENDING_STATES:
                data["expires_at"] = self._expires_at_for_state(target_state, now)
            data["updated_at"] = now.isoformat(timespec="seconds")
            data["version"] = int(current.version) + 1
            updated = InteractionState.from_dict(data)
            self._states[key] = updated
        self._log_transition(previous, updated.state, key)
        return InteractionState.from_dict(updated.to_dict())

    def mark_tool_pending(
        self,
        conversation_id: str,
        tool_call_ids: Iterable[object],
    ) -> InteractionState:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = self.start(key, "multi_action_review", "awaiting_tool_result")
                state = self._states[key]
            previous = state.state
            state.tool_call_ids = self._ids(tool_call_ids)
            self._transition(state, "awaiting_tool_result")
            result = InteractionState.from_dict(state.to_dict())
        self._log_transition(previous, "awaiting_tool_result", key)
        return result

    def finish(
        self,
        conversation_id: str,
        *,
        partial: bool = False,
        consumed: bool = True,
        resolved_object_ids: Iterable[object] = (),
    ) -> InteractionState:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key) or InteractionState(
                conversation_id=key, user_id=self.user_id
            )
            previous = state.state
            state.resolved_object_ids = self._ids(resolved_object_ids)
            state.consumed = bool(consumed)
            self._transition(state, "partial_success" if partial else "completed")
            self._states[key] = state
            result = InteractionState.from_dict(state.to_dict())
        self._log_transition(previous, result.state, key)
        return result

    def cancel(self, conversation_id: str) -> InteractionState:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key) or InteractionState(
                conversation_id=key, user_id=self.user_id
            )
            previous = state.state
            state.consumed = True
            self._transition(state, "cancelled")
            self._states[key] = state
            result = InteractionState.from_dict(state.to_dict())
        self._log_transition(previous, "cancelled", key)
        return result

    def clear_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._states.pop(self._key(conversation_id), None)

    def remember_created_memory(
        self,
        conversation_id: str,
        memory_id: object,
        *,
        source_turn_id: str = "",
    ) -> InteractionState:
        """Remember only the newest formally created memory for this conversation."""
        key = self._key(conversation_id)
        parsed = self._ids([memory_id])
        if not parsed:
            return self.current(key)
        now = self.now_provider()
        with self._lock:
            state = self._states.get(key) or InteractionState(
                conversation_id=key,
                user_id=self.user_id,
            )
            state.last_created_memory_id = parsed[0]
            state.last_created_memory_source_turn_id = str(source_turn_id or "").strip()
            state.last_created_memory_created_at = now.isoformat(timespec="seconds")
            state.last_created_memory_expires_at = (
                now + timedelta(seconds=self.ttl_seconds)
            ).isoformat(timespec="seconds")
            state.updated_at = state.last_created_memory_created_at
            state.version += 1
            self._states[key] = state
            return InteractionState.from_dict(state.to_dict())

    def last_created_memory(self, conversation_id: str) -> Optional[Dict[str, str]]:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key)
            if state is None or not state.last_created_memory_id:
                return None
            try:
                expired = self.now_provider() > datetime.fromisoformat(
                    state.last_created_memory_expires_at
                )
            except ValueError:
                expired = True
            if expired:
                state.last_created_memory_id = ""
                state.last_created_memory_source_turn_id = ""
                state.last_created_memory_created_at = ""
                state.last_created_memory_expires_at = ""
                state.updated_at = self.now_provider().isoformat(timespec="seconds")
                state.version += 1
                return None
            return {
                "memory_id": state.last_created_memory_id,
                "source_turn_id": state.last_created_memory_source_turn_id,
                "created_at": state.last_created_memory_created_at,
                "expires_at": state.last_created_memory_expires_at,
            }

    def clear_created_memory(self, conversation_id: str) -> None:
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            state.last_created_memory_id = ""
            state.last_created_memory_source_turn_id = ""
            state.last_created_memory_created_at = ""
            state.last_created_memory_expires_at = ""
            state.updated_at = self.now_provider().isoformat(timespec="seconds")
            state.version += 1

    def remember_read_result(
        self,
        conversation_id: str,
        *,
        intent: str,
        tool_name: str,
        arguments: Optional[Mapping[str, object]] = None,
        summary: str = "",
        snapshot: Optional[Mapping[str, object]] = None,
    ) -> InteractionState:
        """Keep one verified read fact source inside its originating conversation."""
        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key) or InteractionState(
                conversation_id=key, user_id=self.user_id
            )
            state.last_read_mode = "read"
            state.last_read_intent = str(intent or "").strip()
            state.last_read_tool = str(tool_name or "").strip()
            state.last_read_arguments = dict(arguments or {})
            state.last_read_summary = str(summary or "").strip()[:800]
            state.last_read_snapshot = dict(snapshot or {})
            state.updated_at = self.now_provider().isoformat(timespec="seconds")
            state.version += 1
            self._states[key] = state
            return InteractionState.from_dict(state.to_dict())

    def last_read_result(self, conversation_id: str) -> Optional[Dict[str, object]]:
        state = self.current(conversation_id)
        if state.last_read_mode != "read" or not state.last_read_tool:
            return None
        return {
            "mode": state.last_read_mode,
            "intent": state.last_read_intent,
            "tool_name": state.last_read_tool,
            "arguments": dict(state.last_read_arguments),
            "summary": state.last_read_summary,
            "snapshot": dict(state.last_read_snapshot),
        }

    def remember_suggestion_snapshot(
        self,
        conversation_id: str,
        snapshot: SuggestionSnapshot,
    ) -> InteractionState:
        """Keep the newest ordered suggestion list independently of pending state."""

        key = self._key(conversation_id)
        if snapshot.conversation_id != key or not snapshot.objects:
            return self.current(key)
        with self._lock:
            state = self._states.get(key) or InteractionState(
                conversation_id=key, user_id=self.user_id
            )
            state.last_suggestion_snapshot = snapshot.to_dict()
            state.updated_at = self.now_provider().isoformat(timespec="seconds")
            state.version += 1
            self._states[key] = state
            return InteractionState.from_dict(state.to_dict())

    def mark_suggestion_consumed(
        self,
        conversation_id: str,
        *,
        snapshot_id: object,
        object_id: object,
    ) -> InteractionState:
        """Mark one item unavailable after a write or an explicit skip choice."""

        key = self._key(conversation_id)
        with self._lock:
            state = self._states.get(key)
            if state is None or not state.last_suggestion_snapshot:
                return self.current(key)
            try:
                snapshot = SuggestionSnapshot.from_dict(
                    state.last_suggestion_snapshot
                )
            except (TypeError, ValueError):
                return InteractionState.from_dict(state.to_dict())
            if snapshot.snapshot_id != str(snapshot_id or "").strip():
                return InteractionState.from_dict(state.to_dict())
            updated = snapshot.mark_consumed(object_id)
            state.last_suggestion_snapshot = updated.to_dict()
            state.updated_at = self.now_provider().isoformat(timespec="seconds")
            state.version += 1
            self._states[key] = state
            return InteractionState.from_dict(state.to_dict())

    def handle_control(
        self,
        text: str,
        conversation_id: str,
    ) -> InteractionDecision:
        value = self._normalize(text)
        if not self._looks_like_control(value):
            return InteractionDecision()
        current = self.current(conversation_id)
        if current.state == "expired":
            return InteractionDecision(
                True,
                "expired",
                current,
                message="刚才的操作已经过期，请重新发起。",
            )
        if value in {"取消", "算了", "不用了", "不要了", "先不"}:
            if not current.pending:
                return InteractionDecision(
                    True,
                    "none",
                    current,
                    message="现在没有等待处理的操作。",
                )
            cancelled = self.cancel(conversation_id)
            return InteractionDecision(
                True,
                "cancel",
                cancelled,
                message="好，刚才的操作已经取消。",
            )
        if not current.pending:
            message = (
                "现在没有等待确认的操作。它可能已经过期，或服务重启后已失效，请重新发起。"
                if value in {"确认", "是的", "执行", "继续"}
                else "现在没有等待确认或选择的操作。"
            )
            return InteractionDecision(
                True,
                "none",
                current,
                message=message,
            )

        selected = self._select(value, current)
        if selected:
            current = self._remember_selection(conversation_id, selected)
            if value in {"确认", "是的", "执行", "继续", "执行这条"} or "确认" in value:
                if current.state == "awaiting_confirmation" or len(selected) == 1:
                    return InteractionDecision(True, "confirm", current, selected)
            return InteractionDecision(
                True,
                "select",
                current,
                selected,
                "已选中对应内容。请说“确认”继续，或说“取消”。",
            )

        if value in {"确认", "确认吧", "是的", "执行", "继续", "执行这条"}:
            if current.state == "awaiting_confirmation":
                return InteractionDecision(
                    True,
                    "confirm",
                    current,
                    list(current.selected_object_ids or current.resolved_object_ids),
                )
            if current.selected_object_ids:
                return InteractionDecision(
                    True,
                    "confirm",
                    current,
                    list(current.selected_object_ids),
                )
            if len(current.listed_object_ids) > 1:
                return InteractionDecision(
                    True,
                    "clarification",
                    current,
                    message="你想处理全部，还是其中一条？可以说“全部”或“第二个”。",
                )
            if len(current.listed_object_ids) == 1:
                return InteractionDecision(
                    True,
                    "confirm",
                    current,
                    list(current.listed_object_ids),
                )
        return InteractionDecision(
            True,
            "clarification",
            current,
            message=current.safe_summary or "请再明确一下要处理哪一项。",
        )

    def _remember_selection(
        self,
        conversation_id: str,
        selected_object_ids: Iterable[object],
    ) -> InteractionState:
        key = self._key(conversation_id)
        selected = self._ids(selected_object_ids)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return InteractionState(conversation_id=key, user_id=self.user_id)
            state.selected_object_ids = selected
            state.updated_at = self.now_provider().isoformat(timespec="seconds")
            state.version += 1
            return InteractionState.from_dict(state.to_dict())

    def _select(self, text: str, state: InteractionState) -> List[str]:
        listed = list(state.listed_object_ids)
        selected = list(state.selected_object_ids)
        if any(term in text for term in ("全部", "所有")):
            return listed
        if any(term in text for term in ("这两个", "上面两个", "前两个")):
            return listed[:2]
        if any(term in text for term in ("第一个", "第一条")):
            return listed[:1]
        if any(term in text for term in ("第二个", "第二条")):
            return listed[1:2]
        if any(term in text for term in ("最后一个", "最后一条")):
            return listed[-1:]
        if any(term in text for term in ("这个", "这条", "就这个", "刚才那个")):
            targets = selected or listed
            return targets if len(targets) == 1 else []
        explicit = re.findall(r"(?:候选|记忆|计划|任务)\s*(\d+)", text)
        return self._ids(explicit)

    def _transition(self, state: InteractionState, target: str) -> None:
        now = self.now_provider()
        previous = state.state
        state.state = target
        if target != previous and target in self.TIMED_PENDING_STATES:
            state.expires_at = self._expires_at_for_state(target, now)
        state.updated_at = now.isoformat(timespec="seconds")
        state.version += 1

    def _expired(self, state: InteractionState) -> bool:
        if not state.pending or not state.expires_at:
            return False
        try:
            return self.now_provider() > datetime.fromisoformat(state.expires_at)
        except ValueError:
            return True

    def _ttl_seconds_for_state(self, state: str) -> int:
        if state in self.EXTENDED_CONTINUATION_STATES:
            return self.continuation_ttl_seconds
        if state == "awaiting_confirmation":
            return self.confirmation_ttl_seconds
        return self.ttl_seconds

    def _expires_at_for_state(self, state: str, now: datetime) -> str:
        return (now + timedelta(seconds=self._ttl_seconds_for_state(state))).isoformat(
            timespec="seconds"
        )

    @classmethod
    def _looks_like_control(cls, text: str) -> bool:
        return (
            text in cls.CONTROL_TERMS
            or bool(re.fullmatch(r"(?:确认|取消|执行)?(?:第[一二三四五六七八九十]+个|第\d+个|这个|这条|全部)", text))
        )

    @staticmethod
    def _normalize(text: str) -> str:
        value = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or "").strip())
        # Accept harmless surrounding quote punctuation on a short control
        # reply (for example ``确认‘``) without stripping quotes from the
        # middle of ordinary user content.
        return value.strip("\"'“”‘’")

    @staticmethod
    def _ids(values: Optional[Iterable[object]]) -> List[str]:
        if values is None:
            return []
        result: List[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in result:
                result.append(item)
        return result

    @staticmethod
    def _key(conversation_id: str) -> str:
        return str(conversation_id or "default").strip() or "default"

    @staticmethod
    def _log_transition(previous: str, current: str, conversation_id: str) -> None:
        if previous != current:
            print(
                f"[Interaction] conversation={conversation_id} state={previous}->{current}",
                flush=True,
            )
