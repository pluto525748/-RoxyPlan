from __future__ import annotations

import math
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set

from modules.memory_manager import MemoryManager, SENSITIVE_CATEGORIES


CATEGORY_QUERY_HINTS = {
    "preference": {"喜欢", "偏好", "适合", "环境"},
    "goal": {"目标", "以后", "长期", "想成为"},
    "habit": {"习惯", "一般", "通常", "每天", "周末"},
    "project": {
        "项目", "roxyplan", "代码", "产品", "桌宠", "codex", "模块",
        "功能", "系统", "实验", "肌电", "emg",
    },
    "learning": {
        "学习", "课程", "复习", "机器学习", "编程", "算法", "模型",
        "梯度", "训练", "特征", "数据", "数学", "python",
    },
    "health": {
        "健康", "身体", "肠胃", "胃", "肚子", "腹", "睡眠", "失眠",
        "吃", "饮食", "药", "疼", "不舒服", "油腻",
    },
    "relationship": {"家人", "朋友", "同事", "伴侣", "父母", "关系"},
    "rule": {"规则", "不要", "必须", "提醒", "要求"},
}

CATEGORY_AFFINITY = {
    "project": {"project": 0.30, "learning": 0.14, "goal": 0.10},
    "learning": {"learning": 0.30, "project": 0.14, "goal": 0.08},
    "health": {"health": 0.34, "habit": 0.07, "preference": 0.05},
    "goal": {"goal": 0.28, "project": 0.10, "learning": 0.08},
    "habit": {"habit": 0.26, "preference": 0.08},
    "preference": {"preference": 0.26, "habit": 0.06},
    "relationship": {"relationship": 0.30},
    "rule": {"rule": 0.30, "project": 0.06},
}

GENERIC_TAGS = {
    "我的", "我们", "这个", "那个", "什么", "怎么", "怎样", "如何",
    "为什么", "一下", "最近", "现在", "今天", "应该", "可以", "能够",
    "下一步", "怎么办", "问题", "事情", "信息", "关于", "这样", "那个",
    "学习", "任务", "计划", "安排", "时间", "开始", "完成", "模型",
}


class MemoryRetriever:
    """Lightweight relevance ranking without embeddings or external services."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        now_provider=datetime.now,
    ) -> None:
        self.memory_manager = memory_manager
        self.now_provider = now_provider
        self.last_skipped_sensitive_categories: Set[str] = set()

    def retrieve(
        self,
        user_text: str,
        session_summary: str = "",
        limit: int = 5,
        *,
        broad: bool = False,
        update_usage: bool = True,
        suppressed_categories: Optional[Set[str]] = None,
    ) -> List[Dict[str, object]]:
        # Retrieval is anchored to the current message. A session summary may mention
        # an old health topic, but must not make that topic look relevant to a new
        # learning or programming question.
        query = str(user_text).strip()
        query_normalized = self.memory_manager.normalize_text(query)
        query_tags = self._meaningful_tags(query)
        query_categories = self._query_categories(query)
        query_scope = self._query_scope(query)
        suppressed = set(suppressed_categories or set())
        scored: List[Dict[str, object]] = []
        self.last_skipped_sensitive_categories = set()
        conflicted_memory_ids = {
            int(item.get("old_memory_id", 0) or 0)
            for item in self.memory_manager.conflicts("pending")
            if int(item.get("old_memory_id", 0) or 0) > 0
        }

        active_memories = self.memory_manager.working_memories(
            now=self.now_provider()
        )
        has_current_location = any(
            item.get("scope") == "current_state" and item.get("location")
            for item in active_memories
        )
        for memory in active_memories:
            if int(memory.get("id", 0) or 0) in conflicted_memory_ids:
                continue
            if str(memory.get("scope", "")) == "temporary_state":
                continue
            category = str(memory.get("category", "other"))
            if category in suppressed and category not in query_categories:
                self.last_skipped_sensitive_categories.add(category)
                continue
            if not self._currently_valid(memory):
                continue
            scope = str(memory.get("scope", "stable_identity"))
            if (
                query_scope == "current_state"
                and has_current_location
                and memory.get("location")
                and scope in {"stable_identity", "historical_state", "future_intent"}
            ):
                continue
            if query_scope == "home" and memory.get("location") and scope != "stable_identity":
                continue
            if query_scope == "future_intent" and scope == "historical_state":
                continue
            if category in SENSITIVE_CATEGORIES and not broad:
                if category not in query_categories or not self._sensitive_relevant(
                    query, memory, query_tags
                ):
                    self.last_skipped_sensitive_categories.add(category)
                    continue
            score, reasons = self._score(
                query_normalized,
                query_tags,
                query_categories,
                memory,
                broad=broad,
                query_scope=query_scope,
            )
            category_bonus = self._category_bonus(category, query_categories)
            if (
                not broad
                and category_bonus > 0
                and category not in SENSITIVE_CATEGORIES
                and not self._has_topic_evidence(
                    query_normalized,
                    query_tags,
                    memory,
                )
            ):
                # Category words such as "学习" or "模型" are too broad to select
                # a concrete long-term memory on their own. Require a subject-level
                # overlap so an old machine-learning memory cannot steer a new
                # singing or general planning conversation.
                continue
            if broad:
                threshold = 0.12
            elif category_bonus > 0:
                threshold = 0.30
            else:
                threshold = 0.38
            if score < threshold:
                continue
            scored.append(
                {
                    "memory": memory,
                    "score": round(score, 4),
                    "reason": "、".join(reasons),
                }
            )

        scored.sort(
            key=lambda item: (
                float(item.get("score", 0)),
                int(item.get("memory", {}).get("importance", 3)),
            ),
            reverse=True,
        )
        result = scored[: max(1, min(int(limit), 8))]
        if update_usage and result:
            self.memory_manager.mark_used(
                [int(item["memory"]["id"]) for item in result]
            )
            for item in result:
                refreshed = self.memory_manager.get(int(item["memory"]["id"]))
                if refreshed is not None:
                    item["memory"] = refreshed
        categories = sorted(
            {
                str(item["memory"].get("category", "other"))
                for item in result
            }
        )
        print(
            f"[Memory] retrieved: {len(result)} items "
            f"category={','.join(categories) or 'none'}",
            flush=True,
        )
        return result

    def _score(
        self,
        query_normalized: str,
        query_tags: Set[str],
        query_categories: Set[str],
        memory: Dict[str, object],
        *,
        broad: bool,
        query_scope: str,
    ) -> tuple:
        content = str(memory.get("content", ""))
        normalized = self.memory_manager.normalize_text(content)
        tags = {
            str(tag)
            for tag in memory.get("tags", [])
            if str(tag) and str(tag) not in GENERIC_TAGS
        }
        content_tags = self._meaningful_tags(content)
        overlap = query_tags & (tags | content_tags)
        similarity = SequenceMatcher(None, query_normalized, normalized).ratio()
        contains = bool(query_normalized and (query_normalized in normalized or normalized in query_normalized))
        category = str(memory.get("category", "other"))
        category_bonus = self._category_bonus(category, query_categories)
        importance = max(1, min(int(memory.get("importance", 3)), 5))
        confidence = self._safe_confidence(memory.get("confidence", 1.0))
        use_count = max(0, int(memory.get("use_count", 0)))

        score = 0.0
        reasons = []
        if contains:
            score += 0.62
            reasons.append("文本包含")
        if overlap:
            score += min(0.45, len(overlap) * 0.09)
            reasons.append("关键词")
            if len(overlap) >= 2:
                score += 0.12
                reasons.append("多词组合")
        score += similarity * 0.24
        if similarity >= 0.5:
            reasons.append("表达相近")
        if category_bonus:
            score += category_bonus
            reasons.append("类别相关")
        score += (importance - 1) * 0.04
        score += confidence * 0.06
        score += min(0.08, math.log1p(use_count) * 0.02)
        score += self._recency_bonus(
            memory.get("last_used") or memory.get("last_used_at")
        )
        scope_bonus = self._scope_bonus(
            query_scope,
            str(memory.get("scope", "stable_identity")),
            bool(memory.get("location")),
        )
        if scope_bonus:
            score += scope_bonus
            reasons.append("时空状态")
        if broad:
            score += 0.15
            reasons.append("明确回忆")
        return score, reasons or ["轻量匹配"]

    def _query_categories(self, query: str) -> Set[str]:
        lowered = query.lower()
        return {
            category
            for category, hints in CATEGORY_QUERY_HINTS.items()
            if any(hint in lowered for hint in hints)
        }

    @staticmethod
    def _category_bonus(category: str, query_categories: Set[str]) -> float:
        return max(
            (
                CATEGORY_AFFINITY.get(query_category, {}).get(category, 0.0)
                for query_category in query_categories
            ),
            default=0.0,
        )

    def _meaningful_tags(self, text: str) -> Set[str]:
        return {
            str(tag)
            for tag in self.memory_manager.extract_tags(text)
            if len(str(tag)) >= 2 and str(tag) not in GENERIC_TAGS
        }

    def _has_topic_evidence(
        self,
        query_normalized: str,
        query_tags: Set[str],
        memory: Dict[str, object],
    ) -> bool:
        content = str(memory.get("content", ""))
        content_normalized = self.memory_manager.normalize_text(content)
        content_tags = self._meaningful_tags(content)
        explicit_tags = {
            str(tag)
            for tag in memory.get("tags", [])
            if str(tag) and str(tag) not in GENERIC_TAGS
        }
        if query_tags & (content_tags | explicit_tags):
            return True
        if (
            min(len(query_normalized), len(content_normalized)) >= 4
            and (
                query_normalized in content_normalized
                or content_normalized in query_normalized
            )
        ):
            return True
        return SequenceMatcher(
            None,
            query_normalized,
            content_normalized,
        ).ratio() >= 0.46

    def _sensitive_relevant(
        self,
        query: str,
        memory: Dict[str, object],
        query_tags: Set[str],
    ) -> bool:
        content = str(memory.get("content", ""))
        content_tags = self._meaningful_tags(content)
        category = str(memory.get("category", ""))
        hints = CATEGORY_QUERY_HINTS.get(category, set())
        has_category_signal = any(hint in query.lower() for hint in hints)
        if category == "health":
            return has_category_signal
        return has_category_signal and bool(query_tags & content_tags)

    def _recency_bonus(self, value) -> float:
        if not value:
            return 0.0
        try:
            used_at = datetime.fromisoformat(str(value))
            days = max(0, (self.now_provider() - used_at).days)
        except (TypeError, ValueError):
            return 0.0
        if days <= 7:
            return 0.04
        if days <= 30:
            return 0.02
        return 0.0

    def _currently_valid(self, memory: Dict[str, object]) -> bool:
        valid_until = memory.get("valid_until")
        if not valid_until:
            return True
        try:
            return datetime.fromisoformat(str(valid_until)) >= self.now_provider()
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _query_scope(query: str) -> str:
        text = str(query)
        if re.search(r"老家|家乡|回家", text):
            return "home"
        if re.search(r"以后|未来|将来|想去", text):
            return "future_intent"
        if re.search(r"现在|目前|当前|附近|周末|通勤|天气|吃饭|游玩", text):
            return "current_state"
        return ""

    @staticmethod
    def _scope_bonus(query_scope: str, memory_scope: str, has_location: bool) -> float:
        if not query_scope:
            return 0.0
        if query_scope == "current_state":
            return 0.56 if memory_scope == "current_state" and has_location else 0.10 if memory_scope == "current_state" else 0.0
        if query_scope == "home":
            return 0.52 if memory_scope == "stable_identity" and has_location else 0.0
        if query_scope == "future_intent":
            return 0.46 if memory_scope == "future_intent" else 0.0
        return 0.0

    @staticmethod
    def _safe_confidence(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 1.0
        return max(0.0, min(number, 1.0))
