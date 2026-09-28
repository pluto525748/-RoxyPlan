from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from modules.memory_read import MemoryReadRequest


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
        "说说",
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
        value = self._clean(text)
        if (
            self._is_assistant_capability_query(value)
            or self._rejects_memory_disclosure(value)
            or re.search(
                r"(?:不要|别|无需|不用)(?:再)?(?:告诉|展示|查看|读取|说)|"
                r"(?:我不是|我没)(?:让|要)你",
                value,
            )
        ):
            return None
        typed_request = MemoryReadRequest.from_user_text(text)
        if typed_request is not None:
            return {
                "intent": "show_memory",
                "confidence": 0.99,
                "entities": typed_request.to_tool_arguments(),
                "needs_confirmation": False,
                "source": "memory_query_guard",
                "clarification_question": None,
            }
        features = self.extract(text)
        if not features.has_memory_domain:
            return None
        # Candidate memory is retired from product interaction. Keep its
        # storage/service compatibility, but never alias candidate wording to
        # formal memory: those are different user purposes.
        if features.scope == "candidate":
            return None
        if features.is_query and features.ambiguous_scope:
            return None
        if not features.is_query:
            return None
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
            "entities": (
                {"query_mode": "overview", "query": str(text or "").strip()}
                if intent == "show_memory"
                else {}
            ),
            "needs_confirmation": False,
            "source": "memory_query_guard",
        }

    def extract(self, text: str) -> MemoryQueryFeatures:
        value = self._clean(text)
        if not value:
            return MemoryQueryFeatures()

        # Questions about Roxy's own capabilities are not questions about the
        # user's stored memory.  This negative gate intentionally runs before
        # the broad disclosure patterns below so clauses such as
        # “告诉我你能干什么” cannot be reinterpreted as “我告诉过你什么”.
        if self._is_assistant_capability_query(value):
            return MemoryQueryFeatures()

        # A correction that explicitly rejects memory disclosure must return to
        # ordinary conversation.  It must never open a new memory-scope chooser.
        if self._rejects_memory_disclosure(value):
            return MemoryQueryFeatures()

        candidate_scope = self._has_candidate_memory_scope(value)
        conflict_scope = any(term in value for term in self.CONFLICT_TERMS)
        archived_scope = any(term in value for term in self.ARCHIVED_TERMS)
        query_voice = any(term in value for term in self.QUERY_TERMS)
        query_voice = query_voice or value.endswith(("吗", "呢"))
        query_voice = query_voice or value.startswith(("我的长期记忆", "我的记忆"))

        review_action = any(term in value for term in self.REVIEW_ACTION_TERMS)
        if review_action and not query_voice:
            return MemoryQueryFeatures()

        formal_self_query = bool(
            re.fullmatch(
                r"你(?:都|还|现在|到底|究竟|大概|真正)?"
                r"(?:记得|记住|知道|了解|认识|保存)(?:了)?(?:关于)?我(?:的)?"
                r"(?:什么|哪些|多少)(?:信息|事情|事|内容)?",
                value,
            )
            or re.fullmatch(
                r"你(?:都|还|现在|到底|究竟|大概|真正)?"
                r"(?:记得|记住|知道|了解|认识|保存)(?:了)?"
                r"(?:什么|哪些|多少)(?:条)?(?:关于)?我(?:的)?"
                r"(?:信息|事情|事|内容|偏好|习惯|目标)",
                value,
            )
            or re.fullmatch(
                r"(?:说说|讲讲|谈谈|描述一下)?你对我(?:的)?(?:有|算有|是)?"
                r"(?:什么|哪些|多少|怎样|什么样)?(?:的)?(?:了解|认识|印象)",
                value,
            )
            or re.fullmatch(
                r"按你(?:记得|记住|知道|了解|认识)(?:的)?(?:内容|事情|信息)?"
                r"(?:来)?(?:说说|讲讲|描述一下)?我",
                value,
            )
            or bool(
                re.search(
                    r"你(?:都|还)?(?:记得|知道|了解)我(?:什么|哪些).{0,18}"
                    r"(?:表达|说说|讲讲).{0,12}(?:对我|关于我).{0,6}(?:了解|认识|印象)",
                    value,
                )
            )
            or bool(
                re.fullmatch(
                    r"你(?:都|还|现在)?(?:记得|知道|了解|认识)(?:了)?"
                    r"(?:关于)?我(?:的)?(?:全部|所有)(?:信息|事情|事|内容|东西)?",
                    value,
                )
                or re.fullmatch(
                    r"(?:把)?你(?:都|还|现在)?(?:记得|知道|了解|认识)(?:的)?"
                    r"(?:关于)?我(?:的)?(?:全部|所有)(?:信息|事情|事|内容|东西)?"
                    r"(?:都)?(?:告诉我|说出来|列出来|讲出来)",
                    value,
                )
            )
        )
        disclosure_query = bool(
            re.search(
                r"我(?:之前|以前|曾经|过去|先前).{0,8}"
                r"(?:告诉(?:过)?|说过|提过|聊过).{0,5}你.{0,8}(?:哪些|什么)",
                value,
            )
            or re.search(
                r"我(?:都|还)?(?:跟|向)?你(?:说过|提过|聊过|告诉过)"
                r".{0,8}(?:哪些|什么)",
                value,
            )
            or re.search(
                r"我(?:都|还)?(?:告诉过|说过|提过|聊过)你"
                r".{0,8}(?:哪些|什么)",
                value,
            )
        )
        query_voice = query_voice or formal_self_query or disclosure_query
        explicit_memory = any(
            term in value for term in ("记忆", "长期信息", "保存的信息")
        )
        explicit_memory_data_query = bool(
            explicit_memory
            and not re.search(
                r"(?:区别|概念|形成|原理|机制|怎么|如何|为什么|什么意思)",
                value,
            )
            and (
                value.startswith(("我的记忆", "我的长期记忆", "查看记忆", "查看长期记忆"))
                or re.search(
                    r"(?:查看|看看|列出|告诉我|展示).{0,8}(?:记忆|长期信息|保存的信息)",
                    value,
                )
                or re.search(
                    r"(?:记忆|长期信息|保存的信息).{0,8}(?:有哪些|有什么|是什么|多少条)",
                    value,
                )
            )
        )
        has_domain = bool(
            candidate_scope
            or conflict_scope
            or archived_scope
            or explicit_memory_data_query
            or formal_self_query
            or (disclosure_query and query_voice)
        )
        if not has_domain:
            return MemoryQueryFeatures()

        if conflict_scope:
            scope = "conflict"
        elif archived_scope:
            scope = "archived"
        elif candidate_scope:
            scope = "candidate"
        elif formal_self_query or disclosure_query or "长期" in value or value.startswith("我的"):
            scope = "formal"
        else:
            scope = ""
        return MemoryQueryFeatures(
            is_query=query_voice,
            has_memory_domain=True,
            scope=scope,
            ambiguous_scope=not bool(scope),
        )

    @classmethod
    def _has_candidate_memory_scope(cls, value: str) -> bool:
        candidate_state = (
            "待确认",
            "待审核",
            "没确认",
            "没有确认",
            "未确认",
            "没审核",
            "没有审核",
            "未审核",
        )
        if ("记忆" in value or "长期信息" in value) and any(
            term in value for term in cls.CANDIDATE_TERMS
        ):
            return True
        return bool(
            "候选" in value
            and any(term in value for term in candidate_state)
            and re.fullmatch(
                r"(?:查看|看看|列出|列一下|说说|有哪些|哪些|我的|还有什么)*"
                r"(?:待确认|待审核|未确认|未审核|没确认|没审核|没有确认|没有审核)"
                r"(?:的)?候选",
                value,
            )
        )

    @staticmethod
    def _is_candidate_scope_fragment(value: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:我的)?(?:待确认|待审核|未确认|未审核)(?:记忆|候选)?|"
                r"(?:我的)?(?:记忆候选|候选记忆|候选)",
                value,
            )
        )

    def is_retired_candidate_request(self, text: str) -> bool:
        value = self._clean(text)
        if not value:
            return False
        if self._has_candidate_memory_scope(value):
            return True
        return bool(
            re.fullmatch(
                r"(?:把)?待确认的都确认|确认全部待审核记忆|全部确认这些记忆|"
                r"(?:确认|接受|保存|忽略|拒绝|删除)(?:记忆)?候选\d+|"
                r"(?:确认记忆|保存记忆|忽略记忆)\d+|"
                r"(?:加入候选|放入候选|清空待确认记忆)",
                value,
            )
        )

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or "").strip())

    @staticmethod
    def _is_assistant_capability_query(value: str) -> bool:
        return bool(
            re.search(
                r"你(?:能|会|可以|能够|擅长)(?:帮我)?(?:做|干|处理)?(?:些)?什么",
                value,
            )
            or re.search(r"你(?:有|具备)(?:哪些|什么)(?:功能|能力)", value)
        )

    @staticmethod
    def _rejects_memory_disclosure(value: str) -> bool:
        if not any(term in value for term in ("记忆", "关于我", "我的信息")):
            return False
        return bool(
            re.search(r"我不是(?:让|要)你", value)
            or re.search(r"我没(?:让|要)你", value)
            or re.search(r"(?:请)?不要(?:再)?(?:告诉|展示|查看|说)", value)
            or re.search(r"我不想(?:查看|听|知道)", value)
            or re.search(r"并不是(?:要|让)你", value)
        )
