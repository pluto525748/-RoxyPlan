from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from modules.interaction_state import InteractionState


@dataclass
class InteractionDecision:
    handled: bool = False
    action: str = "none"
    interaction: Optional[InteractionState] = None
    selected_object_ids: List[str] = field(default_factory=list)
    message: str = ""


class InteractionStateCoordinator:
    """Runtime-only coordinator for references, clarification and confirmation."""

    CONTROL_TERMS = {
        "确认",
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
        user_id: str = "local_user",
    ) -> None:
        self.now_provider = now_provider
        self.ttl_seconds = max(10, int(ttl_seconds))
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
        action_candidates: Optional[Iterable[Mapping[str, object]]] = None,
        resolved_object_ids: Optional[Iterable[object]] = None,
        listed_object_ids: Optional[Iterable[object]] = None,
        selected_object_ids: Optional[Iterable[object]] = None,
        missing_fields: Optional[Iterable[object]] = None,
        immutable_arguments: Optional[Mapping[str, object]] = None,
        safe_summary: str = "",
    ) -> InteractionState:
        key = self._key(conversation_id)
        now = self.now_provider()
        interaction = InteractionState(
            conversation_id=key,
            user_id=self.user_id,
            state=state,
            interaction_kind=interaction_kind,
            originating_turn_id=str(originating_turn_id),
            action_candidates=list(action_candidates or []),
            resolved_object_ids=self._ids(resolved_object_ids),
            listed_object_ids=self._ids(listed_object_ids),
            selected_object_ids=self._ids(selected_object_ids),
            missing_fields=self._ids(missing_fields),
            immutable_arguments=dict(immutable_arguments or {}),
            safe_summary=str(safe_summary).strip(),
            created_at=now.isoformat(timespec="seconds"),
            updated_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(
                timespec="seconds"
            ),
        )
        with self._lock:
            previous = self._states.get(key)
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
        listed_object_ids: Iterable[object] = (),
        immutable_arguments: Optional[Mapping[str, object]] = None,
        safe_summary: str = "",
        originating_turn_id: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "awaiting_clarification",
            originating_turn_id=originating_turn_id,
            action_candidates=candidates,
            listed_object_ids=listed_object_ids,
            missing_fields=missing_fields,
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
        safe_summary: str = "",
        originating_turn_id: str = "",
    ) -> InteractionState:
        return self.start(
            conversation_id,
            interaction_kind,
            "awaiting_confirmation",
            originating_turn_id=originating_turn_id,
            selected_object_ids=selected_object_ids,
            immutable_arguments=immutable_arguments,
            safe_summary=safe_summary,
        )

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

        if value in {"确认", "是的", "执行", "继续", "执行这条"}:
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
        state.state = target
        state.updated_at = now.isoformat(timespec="seconds")
        state.version += 1

    def _expired(self, state: InteractionState) -> bool:
        if not state.pending or not state.expires_at:
            return False
        try:
            return self.now_provider() > datetime.fromisoformat(state.expires_at)
        except ValueError:
            return True

    @classmethod
    def _looks_like_control(cls, text: str) -> bool:
        return (
            text in cls.CONTROL_TERMS
            or bool(re.fullmatch(r"(?:确认|取消|执行)?(?:第[一二三四五六七八九十]+个|第\d+个|这个|这条|全部)", text))
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or "").strip())

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
