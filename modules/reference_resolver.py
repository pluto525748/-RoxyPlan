from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence


@dataclass
class ReferenceResolution:
    resolved_id: str = ""
    candidate_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_clarification: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "resolved_id": self.resolved_id,
            "candidate_ids": list(self.candidate_ids),
            "confidence": self.confidence,
            "needs_clarification": self.needs_clarification,
            "reason": self.reason,
        }


class ReferenceResolver:
    """Resolve task references from structured state in one conversation only."""

    REFERENCE_TERMS = (
        "这个",
        "那个",
        "这件事",
        "它",
        "刚才那个",
        "刚添加的",
        "上一个计划",
        "前一个计划",
        "前面的计划",
    )

    def resolve(
        self,
        text: str,
        context: Optional[Mapping[str, object]] = None,
        *,
        conversation_id: str = "",
        candidates: Optional[Sequence[Mapping[str, object]]] = None,
    ) -> ReferenceResolution:
        value = str(text).strip()
        state = dict(context or {})
        state_conversation = str(state.get("conversation_id", "")).strip()
        if conversation_id and state_conversation and conversation_id != state_conversation:
            return ReferenceResolution(
                needs_clarification=True, reason="cross_conversation_reference"
            )

        direct_uid = re.search(r"\b(task_[A-Za-z0-9]+)\b", value)
        direct_number = re.search(r"(?:计划|任务)\s*(\d+)\b", value)
        direct_value = (
            direct_uid.group(1)
            if direct_uid
            else direct_number.group(1)
            if direct_number
            else value
            if value.isdigit()
            else ""
        )
        if direct_value and not any(term in value for term in ("前一个", "上一个")):
            return ReferenceResolution(direct_value, confidence=1.0, reason="explicit_id")

        recent = self._tasks(state.get("recent_tasks"))
        last_task = state.get("last_task")
        if isinstance(last_task, Mapping):
            normalized = self._task(last_task)
            if normalized and not any(
                item.get("id") == normalized.get("id") for item in recent
            ):
                recent.append(normalized)

        has_structured_recent = bool(recent)

        supplied = self._tasks(candidates)
        if supplied:
            known = {str(item.get("id", "")) for item in recent}
            recent.extend(item for item in supplied if str(item.get("id", "")) not in known)

        if any(term in value for term in ("前一个计划", "上一个计划", "前面的计划")):
            if len(recent) >= 2:
                target = recent[-2]
                return ReferenceResolution(
                    str(target.get("uid") or target.get("id") or ""),
                    confidence=0.9,
                    reason="previous_structured_task",
                )
            return ReferenceResolution(
                candidate_ids=self._candidate_ids(recent),
                confidence=0.0,
                needs_clarification=True,
                reason="previous_task_unavailable",
            )

        if any(term in value for term in self.REFERENCE_TERMS):
            if has_structured_recent and recent:
                target = recent[-1]
                return ReferenceResolution(
                    str(target.get("uid") or target.get("id") or ""),
                    confidence=0.95,
                    reason="last_structured_task",
                )
            if len(supplied) == 1:
                target = supplied[0]
                return ReferenceResolution(
                    str(target.get("uid") or target.get("id") or ""),
                    confidence=0.85,
                    reason="single_candidate",
                )
            return ReferenceResolution(
                candidate_ids=self._candidate_ids(supplied),
                confidence=0.0,
                needs_clarification=True,
                reason=(
                    "multiple_candidates"
                    if len(supplied) > 1
                    else "last_task_unavailable"
                ),
            )
        return ReferenceResolution(reason="not_a_reference")

    def resolve_memory_candidates(
        self,
        text: str,
        memory_state: Optional[Mapping[str, object]] = None,
        *,
        conversation_id: str = "",
    ) -> ReferenceResolution:
        value = str(text).strip().strip("。.!！?？")
        state = dict(memory_state or {})
        state_conversation = str(state.get("conversation_id", "")).strip()
        if conversation_id and state_conversation and conversation_id != state_conversation:
            return ReferenceResolution(
                needs_clarification=True,
                reason="cross_conversation_reference",
            )

        explicit_ids = self._positive_unique(
            re.findall(r"(?:候选|记忆)\s*(\d+)", value)
        )
        if explicit_ids:
            return ReferenceResolution(
                resolved_id=str(explicit_ids[0]) if len(explicit_ids) == 1 else "",
                candidate_ids=[str(item) for item in explicit_ids],
                confidence=1.0,
                reason="explicit_candidate_id",
            )

        interaction_status = str(state.get("state", "idle"))
        if interaction_status == "expired":
            return ReferenceResolution(
                needs_clarification=True,
                reason="memory_reference_expired",
            )
        listed = self._positive_unique(state.get("last_listed_candidate_ids", []))
        selected = self._positive_unique(state.get("last_selected_candidate_ids", []))
        if interaction_status == "awaiting_candidate_confirmation" and selected:
            if value in {"确认", "是的", "继续", "继续确认"}:
                return ReferenceResolution(
                    resolved_id=str(selected[0]) if len(selected) == 1 else "",
                    candidate_ids=[str(item) for item in selected],
                    confidence=1.0,
                    reason="awaiting_candidate_confirmation",
                )

        if any(term in value for term in ("全部", "所有", "刚才列出的候选")):
            if listed:
                return ReferenceResolution(
                    candidate_ids=[str(item) for item in listed],
                    confidence=0.98,
                    reason="all_listed_candidates",
                )
            return ReferenceResolution(
                needs_clarification=True,
                reason="candidate_list_unavailable",
            )

        ordinal = None
        if any(term in value for term in ("第一个", "第一条")):
            ordinal = 0
        elif any(term in value for term in ("第二个", "第二条")):
            ordinal = 1
        elif any(term in value for term in ("最后一个", "最后一条")):
            ordinal = -1
        if ordinal is not None:
            if listed and (-len(listed) <= ordinal < len(listed)):
                candidate_id = listed[ordinal]
                return ReferenceResolution(
                    resolved_id=str(candidate_id),
                    candidate_ids=[str(candidate_id)],
                    confidence=0.98,
                    reason="candidate_ordinal",
                )
            return ReferenceResolution(
                needs_clarification=True,
                reason="candidate_ordinal_unavailable",
            )

        if any(term in value for term in ("这两个", "这两条", "上面两个", "上面两条")):
            if len(listed) >= 2:
                return ReferenceResolution(
                    candidate_ids=[str(item) for item in listed[:2]],
                    confidence=0.98,
                    reason="first_two_listed_candidates",
                )
            return ReferenceResolution(
                candidate_ids=[str(item) for item in listed],
                needs_clarification=True,
                reason="two_candidates_unavailable",
            )

        if any(term in value for term in ("这个候选", "这条", "这个", "刚才那条")):
            targets = selected or listed
            if len(targets) == 1:
                return ReferenceResolution(
                    resolved_id=str(targets[0]),
                    candidate_ids=[str(targets[0])],
                    confidence=0.95,
                    reason="single_candidate_reference",
                )
            return ReferenceResolution(
                candidate_ids=[str(item) for item in targets],
                needs_clarification=True,
                reason="multiple_candidate_reference",
            )

        if value in {"确认", "忽略", "接受", "拒绝"}:
            if interaction_status == "awaiting_candidate_confirmation" and selected:
                return ReferenceResolution(
                    resolved_id=str(selected[0]) if len(selected) == 1 else "",
                    candidate_ids=[str(item) for item in selected],
                    confidence=1.0,
                    reason="awaiting_candidate_confirmation",
                )
            return ReferenceResolution(
                candidate_ids=[str(item) for item in listed],
                needs_clarification=True,
                reason=(
                    "candidate_selection_required"
                    if listed
                    else "candidate_list_unavailable"
                ),
            )
        return ReferenceResolution(reason="not_a_memory_candidate_reference")

    @classmethod
    def _tasks(cls, value: object) -> List[Dict[str, object]]:
        if not isinstance(value, (list, tuple)):
            return []
        return [task for item in value if (task := cls._task(item))]

    @staticmethod
    def _task(value: object) -> Dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        return {
            key: value.get(key)
            for key in ("id", "uid", "title", "done", "status")
            if key in value
        }

    @staticmethod
    def _candidate_ids(tasks: Sequence[Mapping[str, object]]) -> List[str]:
        return [
            str(item.get("uid") or item.get("id") or "")
            for item in tasks[:3]
            if str(item.get("uid") or item.get("id") or "")
        ]

    @staticmethod
    def _positive_unique(values: object) -> List[int]:
        if not isinstance(values, (list, tuple)):
            return []
        result: List[int] = []
        for value in values:
            if isinstance(value, bool):
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in result:
                result.append(parsed)
        return result
