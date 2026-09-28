from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple


SENSITIVE_TERMS = {
    "health": ("肠胃", "胃", "咳", "饮食", "营养", "身体", "不舒服", "睡眠", "失眠", "药", "疼"),
    "relationship": ("家人", "朋友", "同事", "伴侣", "父母", "关系"),
}

DEFAULT_CONTEXT_BUDGET = 18000


@dataclass(frozen=True)
class ContextSectionDiagnostic:
    name: str
    source: str
    characters: int
    item_count: int
    trimmed: bool


class ContextBuilder:
    """Build one bounded LLM context, with diagnostics kept out of user replies."""

    def __init__(
        self,
        recent_message_limit: int = 16,
        *,
        character_budget: int = DEFAULT_CONTEXT_BUDGET,
    ) -> None:
        self.recent_message_limit = self._normalize_limit(recent_message_limit)
        self.character_budget = max(1000, int(character_budget))
        self.last_diagnostics: List[ContextSectionDiagnostic] = []

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
        verified_turn_context: str = "",
        client_runtime_context: str = "",
        persona_context: str = "",
        user_goals_context: str = "",
        today_pending_context: str = "",
        today_completed_context: str = "",
        action_context: str = "",
        relevant_conversation_summaries_context: str = "",
        persona_lore_context: str = "",
        persona_relationship_context: str = "",
        persona_examples_context: str = "",
        section_item_counts: Optional[Dict[str, int]] = None,
        memory_count: Optional[int] = None,
        memory_categories: Optional[Iterable[str]] = None,
        skipped_sensitive_categories: Optional[Iterable[str]] = None,
        suppressed_categories: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, str]]:
        print("[Context] build", flush=True)
        suppressed = {str(item) for item in suppressed_categories or []}
        filtered_summary = self._filtered_summary(
            session_summary, current_user_input, suppressed
        )
        recent = self._bounded_recent(recent_messages, current_user_input, suppressed)
        # Summaries are reference material, not an additional user turn.  When a
        # history implementation has retained a summary sentence in recent turns,
        # keep the reference section and remove the duplicate conversational copy.
        summary_lines = {
            line.strip() for line in filtered_summary.splitlines() if line.strip()
        }
        if summary_lines:
            recent = [item for item in recent if item["content"] not in summary_lines]
        counts = dict(section_item_counts or {})
        sections: List[Tuple[str, str, str, bool]] = [
            ("safety_and_tool_facts", "instruction", instruction, True),
            (
                "verified_turn_facts",
                "runtime_verification",
                verified_turn_context,
                True,
            ),
            (
                "client_runtime_facts",
                "client_runtime_snapshot",
                client_runtime_context,
                True,
            ),
            (
                "persona_fixed_core",
                "persona_pack" if persona_context.strip() else "legacy_personality",
                persona_context or personality_context,
                True,
            ),
            ("user_important_goals", "memory_profile", user_goals_context, True),
            ("today_unfinished_plan", "plan_service", today_pending_context, True),
            ("today_completed_summary", "plan_service", today_completed_context, False),
            ("today_action_records", "plan_service", action_context, False),
            (
                "current_session_summary",
                "chat_history",
                "当前会话摘要：\n" + filtered_summary if filtered_summary else "",
                False,
            ),
            ("relevant_memories", "memory_retriever", memory_context, False),
            (
                "relevant_conversation_summaries",
                "chat_history",
                self._history_reference_context(
                    relevant_conversation_summaries_context
                ),
                False,
            ),
            ("persona_lore", "persona_pack", persona_lore_context, False),
            (
                "persona_relationships",
                "persona_pack",
                persona_relationship_context,
                False,
            ),
            (
                "persona_dialogue_examples",
                "persona_pack",
                persona_examples_context,
                False,
            ),
            ("relevant_knowledge", "knowledge_manager", knowledge_context, False),
        ]
        system_sections = self._bounded_sections(
            sections, counts, current_user_input, recent
        )
        messages = [
            {"role": "system", "content": content}
            for _name, content in system_sections
            if content
        ]
        if any(name == "persona_fixed_core" and content for name, content in system_sections):
            print("[Context] personality included", flush=True)
        if memory_context.strip():
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
                print(f"[Context] memory included: category=none count={count}", flush=True)
        for category in sorted(
            {str(value).strip() for value in skipped_sensitive_categories or [] if str(value).strip()}
        ):
            print(f"[Context] sensitive memory skipped: {category}", flush=True)
        if filtered_summary:
            print("[Context] summary included", flush=True)
        relevant_summary_diagnostic = next(
            (
                item
                for item in self.last_diagnostics
                if item.name == "relevant_conversation_summaries"
                and item.characters > 0
            ),
            None,
        )
        if relevant_summary_diagnostic is not None:
            print(
                "[Context] relevant conversation summaries included: "
                f"selected={relevant_summary_diagnostic.item_count} "
                f"characters={relevant_summary_diagnostic.characters} "
                f"trimmed={str(relevant_summary_diagnostic.trimmed).lower()}",
                flush=True,
            )
        messages.extend(self._bounded_recent_for_budget(recent))
        print(f"[Context] recent messages: {len(recent)}", flush=True)
        if knowledge_context.strip():
            print("[Context] knowledge included", flush=True)
        else:
            print("[Context] knowledge included: none", flush=True)
        messages.append({"role": "user", "content": current_user_input.strip()})
        return messages

    def _bounded_sections(
        self,
        sections: List[Tuple[str, str, str, bool]],
        counts: Dict[str, int],
        current_user_input: str,
        recent: List[Dict[str, str]],
    ) -> List[Tuple[str, str]]:
        reserved = len(str(current_user_input).strip())
        required_size = sum(
            len(str(content or "").strip())
            for _name, _source, content, required in sections
            if required
        )
        # Preserve required system sections and the latest conversation turns.
        recent_reserve = sum(len(item["content"]) for item in recent[-2:])
        remaining = max(0, self.character_budget - reserved - required_size - recent_reserve)
        raw_optional = {
            name: str(content or "").strip()
            for name, _source, content, required in sections
            if not required
        }
        optional_total = sum(len(value) for value in raw_optional.values())
        # Cut the least valuable material first, while preserving the prescribed
        # presentation order below. This avoids a long knowledge match crowding out
        # the relevant memory or a recent session summary.
        trim_order = (
            "relevant_knowledge",
            "persona_dialogue_examples",
            "persona_relationships",
            "persona_lore",
            "relevant_conversation_summaries",
            "relevant_memories",
            "today_completed_summary",
            "today_action_records",
            "current_session_summary",
        )
        allowed = {name: len(value) for name, value in raw_optional.items()}
        excess = max(0, optional_total - remaining)
        for name in trim_order:
            if excess <= 0:
                break
            reduction = min(excess, allowed.get(name, 0))
            allowed[name] = max(0, allowed.get(name, 0) - reduction)
            excess -= reduction
        result: List[Tuple[str, str]] = []
        diagnostics: List[ContextSectionDiagnostic] = []
        for name, source, raw_content, required in sections:
            content = str(raw_content or "").strip()
            original_length = len(content)
            trimmed = False
            if content and not required:
                content = content[: allowed.get(name, len(content))].rstrip()
                trimmed = len(content) < original_length
            diagnostics.append(
                ContextSectionDiagnostic(
                    name=name,
                    source=source,
                    characters=len(content),
                    item_count=int(counts.get(name, 1 if content else 0)),
                    trimmed=trimmed,
                )
            )
            if content:
                result.append((name, content))
        self.last_diagnostics = diagnostics + [
            ContextSectionDiagnostic(
                name="recent_messages",
                source="chat_history",
                characters=sum(len(item["content"]) for item in recent),
                item_count=len(recent),
                trimmed=self.character_budget <= 1000 and len(recent) > 2,
            ),
            ContextSectionDiagnostic(
                name="current_user_message",
                source="request",
                characters=len(str(current_user_input).strip()),
                item_count=1,
                trimmed=False,
            ),
        ]
        return result

    def _bounded_recent_for_budget(
        self, recent: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        return recent[-2:] if self.character_budget <= 1000 else recent

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
        if normalized and normalized[-1]["role"] == "user" and normalized[-1]["content"] == current_user_input.strip():
            normalized.pop()
        if skipped_categories:
            print("[Context] sensitive history skipped: " + ",".join(sorted(skipped_categories)), flush=True)
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
            print("[Context] sensitive summary skipped: " + ",".join(sorted(skipped)), flush=True)
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

    @staticmethod
    def _history_reference_context(content: str) -> str:
        evidence = str(content or "").strip()
        if not evidence:
            return ""
        return (
            "旧会话仅是有来源的参考片段。只在它与当前用户表达直接相关时自然衔接，"
            "不要机械复述摘要。当前用户表达优先；如有冲突，按当前表达回答。"
            "除非用户明确询问旧会话或来源，否则不要主动展示日期、会话编号或来源标签。"
            "不得把旧会话当作长期记忆，也不得据此创建或保存长期记忆。\n"
            + evidence
        )
