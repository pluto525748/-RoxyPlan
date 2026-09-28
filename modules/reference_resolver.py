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


@dataclass
class MessageContentResolution:
    content: str = ""
    role: str = ""
    confidence: float = 0.0
    needs_clarification: bool = False
    reason: str = ""


class ReferenceResolver:
    """Resolve task references from structured state in one conversation only."""

    _PLAN_ORDINAL_NUMBER = r"[0-9零〇一二两三四五六七八九十百]+"
    _PLAN_CHINESE_ORDINAL_NUMBER = r"[零〇一二两三四五六七八九十百]+"

    REFERENCE_TERMS = (
        "这些",
        "那些",
        "这个",
        "那个",
        "你说的",
        "上面说的",
        "刚才的",
        "这件事",
        "它",
        "刚才那个",
        "刚添加的",
        "上一个计划",
        "前一个计划",
        "前面的计划",
        "上述内容",
        "前面提到的",
        "刚才提到的",
        "这几条",
        "前者",
        "后者",
        "那件事情",
    )

    @staticmethod
    def is_previous_assistant_plan_reference(value: object) -> bool:
        """Recognize a plan payload that refers to the preceding assistant reply.

        This is only a reference-shape check.  It does not infer a plan title or
        decide whether to write anything.
        """
        text = re.sub(r"[\s，,。.!！?？；;：:]", "", str(value or "").strip())
        text = re.sub(
            r"^(?:(?:你好|嗨|您好|好的|好啦|可以了|行了|谢谢(?:你|您)?(?:的)?(?:指导|建议|帮助|提醒)?|"
            r"我的意思是|意思是|我现在想|我想要?|我把|把)+)",
            "",
            text,
        )
        if not text:
            return False
        return bool(
            re.fullmatch(
                r"(?:你(?:刚才|上面|前面)?(?:说的|说|给我(?:的)?|对我(?:的)?)"
                r"|(?:上面|前面|刚才)(?:的)?(?:说的|给我(?:的)?|对我(?:的)?))"
                r"(?:这?(?:段|个))?(?:规划|安排|建议|回复|内容|步骤)?",
                text,
            )
            or text in {
                "你说的",
                "你说",
                "你刚才说的",
                "你上面说的",
                "你上面的",
                "你上面的规划",
                "你上面的安排",
                "你前面说的",
                "上面的规划",
                "上面的安排",
                "刚才的规划",
                "刚才的安排",
            }
        )

    def resolve_message_content(
        self,
        reference: str,
        context: Optional[Mapping[str, object]] = None,
        *,
        conversation_id: str = "",
    ) -> MessageContentResolution:
        """Resolve an explicit model-declared message reference in one session.

        This method never infers whether the user wants an action. It only
        resolves a canonical reference emitted by the single SemanticDecision.
        """
        marker = str(reference or "").strip()
        state = dict(context or {})
        state_conversation = str(state.get("conversation_id", "")).strip()
        if conversation_id and state_conversation and conversation_id != state_conversation:
            return MessageContentResolution(
                needs_clarification=True,
                reason="cross_conversation_message_reference",
            )
        role_key = {
            "previous_user_message": ("last_user_message", "user"),
            "previous_assistant_message": ("last_assistant_message", "assistant"),
        }.get(marker)
        if role_key is None:
            return MessageContentResolution(
                needs_clarification=True,
                reason="unsupported_message_reference",
            )
        key, role = role_key
        content = str(state.get(key, "") or "").strip()
        if not content:
            return MessageContentResolution(
                role=role,
                needs_clarification=True,
                reason=f"previous_{role}_message_unavailable",
            )
        return MessageContentResolution(
            content=content,
            role=role,
            confidence=1.0,
            reason=f"previous_{role}_message_resolved",
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
        supplied = self._tasks(candidates)
        if direct_uid is None and supplied:
            ordinal_resolution = self.resolve_plan_ordinal(value, supplied)
            if ordinal_resolution.reason != "not_a_plan_ordinal":
                return ordinal_resolution
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

        has_session_reference = bool(recent)
        # A task returned by a successful tool in this conversation is stronger
        # evidence for a deictic follow-up than the global plan list.  The
        # supplied list is only a fallback when no session-scoped task exists;
        # otherwise even a single "它" becomes ambiguous as soon as the user
        # has any unrelated plan.
        if supplied and not recent:
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
            unique = []
            known = set()
            for item in recent:
                identity = str(item.get("uid") or item.get("id") or "")
                if identity and identity not in known:
                    known.add(identity)
                    unique.append(item)
            if len(unique) == 1:
                target = unique[0]
                return ReferenceResolution(
                    str(target.get("uid") or target.get("id") or ""),
                    confidence=0.95,
                    reason="unique_structured_task",
                )
            return ReferenceResolution(
                candidate_ids=self._candidate_ids(unique),
                confidence=0.0,
                needs_clarification=True,
                reason=(
                    "multiple_structured_references"
                    if len(unique) > 1 and has_session_reference
                    else "multiple_candidates"
                    if len(unique) > 1
                    else "structured_reference_unavailable"
                ),
            )
        return ReferenceResolution(reason="not_a_reference")

    def resolve_plan_ordinal(
        self,
        text: str,
        candidates: Optional[Sequence[Mapping[str, object]]] = None,
    ) -> ReferenceResolution:
        """Bind an explicit 1-based display ordinal to a stable task identity."""
        value = str(text or "").strip()
        ordinal_text = ""
        ordinal_patterns = (
            rf"第\s*({self._PLAN_ORDINAL_NUMBER})\s*(?:个|条|项)(?:计划|任务)?",
            rf"(?:计划|任务)\s*({self._PLAN_CHINESE_ORDINAL_NUMBER})(?=(?:已经|已|完成|做完|结束|取消|删除|改|更新|调整|$|[\s，。！？、]))",
        )
        for pattern in ordinal_patterns:
            matched = re.search(pattern, value)
            if matched:
                ordinal_text = matched.group(1)
                break
        if not ordinal_text:
            return ReferenceResolution(reason="not_a_plan_ordinal")

        ordinal = self._parse_positive_ordinal(ordinal_text)
        tasks = self._tasks(candidates)
        if ordinal is None or ordinal > len(tasks):
            return ReferenceResolution(
                candidate_ids=self._candidate_ids(tasks),
                needs_clarification=True,
                reason="plan_ordinal_unavailable",
            )
        target = tasks[ordinal - 1]
        identity = str(target.get("uid") or target.get("id") or "")
        if not identity:
            return ReferenceResolution(
                candidate_ids=self._candidate_ids(tasks),
                needs_clarification=True,
                reason="plan_ordinal_unavailable",
            )
        return ReferenceResolution(
            resolved_id=identity,
            candidate_ids=[identity],
            confidence=1.0,
            reason="plan_display_ordinal",
        )

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

    @staticmethod
    def _parse_positive_ordinal(value: object) -> Optional[int]:
        text = str(value or "").strip()
        if text.isdigit():
            parsed = int(text)
            return parsed if parsed > 0 else None

        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if text in digits:
            parsed = digits[text]
            return parsed if parsed > 0 else None
        if text == "十":
            return 10
        if "十" in text and text.count("十") == 1:
            tens_text, ones_text = text.split("十")
            tens = 1 if not tens_text else digits.get(tens_text)
            ones = 0 if not ones_text else digits.get(ones_text)
            if tens is not None and ones is not None:
                parsed = tens * 10 + ones
                return parsed if parsed > 0 else None
        return None
