from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class MemoryQueryFeatures:
    is_query: bool = False
    has_memory_domain: bool = False
    scope: str = ""
    ambiguous_scope: bool = False


class MemoryDataQueryGuard:
    """Route local memory-data questions before generative chat."""

    QUERY_TERMS = (
        "什么",
        "哪些",
        "多少",
        "有没有",
        "还有",
        "查看",
        "看看",
        "列",
        "列出",
        "告诉我",
    )
    MEMORY_TERMS = ("记忆", "记得", "记住", "保存", "了解", "知道", "认识")
    CANDIDATE_TERMS = (
        "候选",
        "待确认",
        "待审核",
        "没确认",
        "没有确认",
        "未确认",
        "没审核",
        "没有审核",
        "未审核",
    )
    CONFLICT_TERMS = ("记忆冲突", "冲突记忆", "矛盾记忆")
    ARCHIVED_TERMS = ("归档记忆", "已归档", "归档的记忆")
    REVIEW_ACTION_TERMS = ("确认", "接受", "归入", "加入长期", "忽略", "拒绝")

    def route(self, text: str) -> Optional[Dict[str, object]]:
        features = self.extract(text)
        if not features.is_query or not features.has_memory_domain:
            return None
        if features.ambiguous_scope:
            return {
                "intent": "show_memory",
                "confidence": 0.95,
                "entities": {},
                "needs_confirmation": False,
                "source": "memory_query_guard",
                "clarification_question": (
                    "你是想查看正式长期记忆，还是待审核候选？"
                ),
            }
        intent = {
            "formal": "show_memory",
            "candidate": "show_memory_candidates",
            "conflict": "show_memory_conflicts",
            "archived": "show_archived_memories",
        }.get(features.scope)
        if not intent:
            return None
        return {
            "intent": intent,
            "confidence": 0.96,
            "entities": {},
            "needs_confirmation": False,
            "source": "memory_query_guard",
        }

    def extract(self, text: str) -> MemoryQueryFeatures:
        value = self._clean(text)
        if not value:
            return MemoryQueryFeatures()

        candidate_scope = any(term in value for term in self.CANDIDATE_TERMS)
        conflict_scope = any(term in value for term in self.CONFLICT_TERMS)
        archived_scope = any(term in value for term in self.ARCHIVED_TERMS)
        query_voice = any(term in value for term in self.QUERY_TERMS)
        query_voice = query_voice or value.endswith(("吗", "呢"))
        query_voice = query_voice or value.startswith(("我的长期记忆", "我的记忆"))

        review_action = any(term in value for term in self.REVIEW_ACTION_TERMS)
        if review_action and not query_voice:
            return MemoryQueryFeatures()

        about_user = bool(
            re.search(r"(?:关于|有关|对)?我(?:的|有什么|知道|了解|认识)", value)
        )
        cognitive_query = bool(
            re.search(r"你.{0,6}(?:记得|记住|知道|了解|认识|保存).{0,8}我", value)
            or re.search(r"你对我.{0,6}(?:了解|认识)", value)
        )
        disclosure_query = bool(
            re.search(
                r"我.{0,10}(?:告诉|说过|提过|聊过).{0,5}你.{0,8}(?:哪些|什么)",
                value,
            )
        )
        explicit_memory = any(
            term in value for term in ("记忆", "长期信息", "保存的信息")
        )
        has_domain = bool(
            candidate_scope
            or conflict_scope
            or archived_scope
            or (explicit_memory and query_voice)
            or (cognitive_query and query_voice)
            or (disclosure_query and query_voice)
            or (about_user and query_voice and any(term in value for term in self.MEMORY_TERMS))
        )
        if not has_domain:
            return MemoryQueryFeatures()

        if conflict_scope:
            scope = "conflict"
        elif archived_scope:
            scope = "archived"
        elif candidate_scope:
            scope = "candidate"
        elif cognitive_query or disclosure_query or "长期" in value or about_user or value.startswith("我的"):
            scope = "formal"
        else:
            scope = ""
        return MemoryQueryFeatures(
            is_query=query_voice,
            has_memory_domain=True,
            scope=scope,
            ambiguous_scope=not bool(scope),
        )

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or "").strip())
