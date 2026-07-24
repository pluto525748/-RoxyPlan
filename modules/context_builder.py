from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set


SENSITIVE_TERMS = {
    "health": ("肠胃", "胃", "吃", "饮食", "油腻", "身体", "不舒服", "睡眠", "失眠", "药", "疼"),
    "relationship": ("家人", "朋友", "同事", "伴侣", "父母", "关系"),
}


class ContextBuilder:
    """Build a bounded LLM message list from separated local context sources."""

    def __init__(self, recent_message_limit: int = 16) -> None:
        self.recent_message_limit = self._normalize_limit(recent_message_limit)

    def set_recent_message_limit(self, limit: int) -> None:
        self.recent_message_limit = self._normalize_limit(limit)

    def build(
        self,
        *,
        personality_context: str,
        memory_context: str,
        session_summary: str,
        recent_messages: Iterable[Dict[str, object]],
        knowledge_context: str,
        current_user_input: str,
        instruction: str = "",
        memory_count: Optional[int] = None,
        memory_categories: Optional[Iterable[str]] = None,
        skipped_sensitive_categories: Optional[Iterable[str]] = None,
        suppressed_categories: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, str]]:
        print("[Context] build", flush=True)
        messages: List[Dict[str, str]] = []

        primary_system = "\n\n".join(
            part.strip() for part in (personality_context, instruction) if part.strip()
        )
        if primary_system:
            messages.append({"role": "system", "content": primary_system})
            print("[Context] personality included", flush=True)
        if memory_context.strip():
            messages.append({"role": "system", "content": memory_context.strip()})
            count = memory_count if memory_count is not None else 0
            category_counts: Dict[str, int] = {}
            for category in memory_categories or []:
                name = str(category).strip() or "other"
                category_counts[name] = category_counts.get(name, 0) + 1
            if category_counts:
                for category in sorted(category_counts):
                    print(
                        f"[Context] memory included: category={category} "
                        f"count={category_counts[category]}",
                        flush=True,
                    )
            else:
                print(
                    f"[Context] memory included: category=none count={count}",
                    flush=True,
                )
        for category in sorted(
            {
                str(value).strip()
                for value in skipped_sensitive_categories or []
                if str(value).strip()
            }
        ):
            print(
                f"[Context] sensitive memory skipped: {category}",
                flush=True,
            )
        filtered_summary = self._filtered_summary(
            session_summary,
            current_user_input,
            set(str(item) for item in suppressed_categories or []),
        )
        if filtered_summary:
            messages.append(
                {
                    "role": "system",
                    "content": "当前会话摘要：\n" + filtered_summary,
                }
            )
            print("[Context] summary included", flush=True)

        recent = self._bounded_recent(
            recent_messages,
            current_user_input,
            set(str(item) for item in suppressed_categories or []),
        )
        messages.extend(recent)
        print(f"[Context] recent messages: {len(recent)}", flush=True)

        if knowledge_context.strip():
            messages.append({"role": "system", "content": knowledge_context.strip()})
            print("[Context] knowledge included", flush=True)
        else:
            print("[Context] knowledge included: none", flush=True)
        messages.append({"role": "user", "content": current_user_input.strip()})
        return messages

    def _bounded_recent(
        self,
        source: Iterable[Dict[str, object]],
        current_user_input: str,
        suppressed_categories: Optional[Set[str]] = None,
    ) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        current_categories = self._query_categories(current_user_input)
        suppressed = set(suppressed_categories or set())
        skipped_categories: Set[str] = set()
        skip_following_assistant = False
        for item in source:
            role = str(item.get("role", ""))
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            message_categories = self._query_categories(content)
            sensitive = message_categories & set(SENSITIVE_TERMS)
            irrelevant_sensitive = sensitive - current_categories
            blocked_sensitive = sensitive & suppressed - current_categories
            if role == "user" and (irrelevant_sensitive or blocked_sensitive):
                skipped_categories.update(sensitive)
                skip_following_assistant = True
                continue
            if role == "assistant" and (irrelevant_sensitive or blocked_sensitive):
                skipped_categories.update(sensitive)
                skip_following_assistant = False
                continue
            if role == "assistant" and skip_following_assistant:
                skip_following_assistant = False
                continue
            if role != "assistant":
                skip_following_assistant = False
            normalized.append({"role": role, "content": content})

        if (
            normalized
            and normalized[-1]["role"] == "user"
            and normalized[-1]["content"] == current_user_input.strip()
        ):
            normalized.pop()
        if skipped_categories:
            print(
                "[Context] sensitive history skipped: " + ",".join(sorted(skipped_categories)),
                flush=True,
            )
        return normalized[-self.recent_message_limit :]

    def _filtered_summary(
        self,
        summary: str,
        current_user_input: str,
        suppressed_categories: Set[str],
    ) -> str:
        current_categories = self._query_categories(current_user_input)
        lines = []
        skipped: Set[str] = set()
        for line in str(summary).splitlines():
            categories = self._query_categories(line)
            sensitive = categories & set(SENSITIVE_TERMS)
            if sensitive and not (sensitive & current_categories):
                skipped.update(sensitive)
                continue
            if sensitive & suppressed_categories and not (sensitive & current_categories):
                skipped.update(sensitive)
                continue
            if line.strip():
                lines.append(line.strip())
        if skipped:
            print(
                "[Context] sensitive summary skipped: " + ",".join(sorted(skipped)),
                flush=True,
            )
        return "\n".join(lines)

    @staticmethod
    def _query_categories(text: str) -> Set[str]:
        lowered = str(text).lower()
        return {
            category
            for category, terms in SENSITIVE_TERMS.items()
            if any(term in lowered for term in terms)
        }

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            value = 16
        return max(1, min(value, 100))
