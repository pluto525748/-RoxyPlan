from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Mapping, Optional
from uuid import uuid4

from modules.memory_manager import MemoryManager


VALID_MEMORY_READ_MODES = {"overview", "attribute", "existence", "provenance"}
VALID_MEMORY_ATTRIBUTES = {
    "preferred_name",
    "preference",
    "habit",
    "goal",
    "project",
    "current_state",
    "fact",
}

_NON_MEMORY_FACT_TOPICS = {
    "今日计划",
    "今天计划",
    "计划",
    "任务",
    "待办",
    "行动记录",
    "复盘",
    "成长日志",
    "日期",
    "时间",
}

_QUERY_FILLERS = {
    "什么",
    "哪些",
    "知道",
    "记得",
    "记住",
    "保存",
    "关于",
    "我的",
    "喜欢",
    "偏好",
    "是不是",
    "有没有",
}


@dataclass(frozen=True)
class MemoryReadRequest:
    query_mode: str = "overview"
    attribute: str = ""
    topic: str = ""
    query: str = ""

    def __post_init__(self) -> None:
        if self.query_mode not in VALID_MEMORY_READ_MODES:
            raise ValueError("invalid_memory_read_mode")
        if self.attribute and self.attribute not in VALID_MEMORY_ATTRIBUTES:
            raise ValueError("invalid_memory_read_attribute")
        object.__setattr__(self, "topic", str(self.topic or "").strip()[:80])
        object.__setattr__(self, "query", str(self.query or "").strip()[:500])

    @classmethod
    def from_arguments(cls, arguments: Mapping[str, object]) -> "MemoryReadRequest":
        return cls(
            query_mode=str(arguments.get("query_mode", "overview") or "overview"),
            attribute=str(arguments.get("attribute", "") or ""),
            topic=str(arguments.get("topic", "") or ""),
            query=str(arguments.get("query", "") or ""),
        )

    @classmethod
    def from_user_text(cls, text: str) -> Optional["MemoryReadRequest"]:
        """Parse stable memory-query slots, not individual sentence templates."""

        raw = str(text or "").strip()
        value = re.sub(r"[\s，,。.!！?？；;：:]", "", raw)
        if not value:
            return None
        # Typed memory reads answer questions about the user. Questions about
        # Roxy herself ("你叫什么/你喜欢什么") must remain ordinary chat.
        if re.match(
            r"^(?:你|你的)(?:叫|名字|姓名|称呼|喜欢|偏好|爱好|口味|目标|梦想|习惯|通常|一般|现在|目前|当前)",
            value,
        ):
            return None

        # Explicit category reads are a typed product capability even when the
        # user naturally omits the pronoun, for example "看看项目记忆".  Keep
        # this as one closed category grammar rather than treating every phrase
        # containing "记忆" as a data request.
        category_query = re.fullmatch(
            r"(?:(?:查看|看看|列出|说说|展示)(?:一下)?(?:我的)?|我的)"
            r"(?P<kind>长期目标|目标|梦想|项目|偏好|喜好|习惯)"
            r"(?:长期)?记忆(?:有哪些|有什么|是什么|多少条)?",
            value,
        )
        if category_query:
            attribute = {
                "长期目标": "goal",
                "目标": "goal",
                "梦想": "goal",
                "项目": "project",
                "偏好": "preference",
                "喜好": "preference",
                "习惯": "habit",
            }[category_query.group("kind")]
            return cls("attribute", attribute, query=raw)

        if "我" not in value:
            return None

        name_subject = bool(re.search(r"(?:名字|姓名|称呼|叫我|我叫)", value))
        name_question = bool(
            re.search(r"(?:什么|叫啥|怎么称呼|如何称呼|还记得|知道)", value)
        )
        if name_subject and name_question:
            mode = (
                "provenance"
                if re.search(r"(?:为什么|怎么知道|从哪|来源)", value)
                else "attribute"
            )
            return cls(mode, "preferred_name", query=raw)

        preference_subject = bool(re.search(r"(?:喜欢|偏好|爱好|口味)", value))
        preference_existence = bool(
            re.search(r"^(?:我)?(?:是不是|是否)(?:喜欢|偏好|爱)(?:吃|喝)?.{1,30}$", value)
        )
        preference_question = bool(
            re.search(r"(?:什么|哪些|有什么|还记得|知道|吗)$", value)
        ) or preference_existence
        if preference_subject and preference_question:
            topic = "food" if re.search(r"(?:吃|喝|口味|饮食|食物|菜)", value) else ""
            if re.search(r"(?:学习|读书|阅读)", value):
                topic = "learning"
            mode = (
                "existence"
                if (preference_existence or value.endswith("吗"))
                and not re.search(r"(?:什么|哪些|有什么)", value)
                else "attribute"
            )
            return cls(mode, "preference", topic=topic, query=raw)

        if re.search(
            r"(?:在哪里|在哪儿|在哪|哪里|哪儿).{0,8}(?:实习|工作)|"
            r"(?:实习|工作).{0,8}(?:在哪里|在哪儿|在哪|哪里|哪儿|什么地方)",
            value,
        ):
            return cls("attribute", "current_state", topic="location", query=raw)

        attribute_patterns = (
            ("goal", r"(?:目标|梦想|长期想)", ""),
            ("habit", r"(?:习惯|通常|一般)", ""),
            ("project", r"(?:项目|在做什么)", "work"),
            ("current_state", r"(?:现在|目前|当前).*(?:哪里|哪儿|在哪)", "location"),
        )
        for attribute, subject_pattern, topic in attribute_patterns:
            if re.search(subject_pattern, value) and re.search(
                r"(?:什么|哪些|哪里|哪儿|在哪|还记得|知道|吗)$", value
            ):
                return cls("attribute", attribute, topic=topic, query=raw)

        # A named stable property can be queried without being one of the
        # built-in profile categories.  Keep the property name as a topic so
        # the reader still selects only records grounded in the question.
        generic = re.fullmatch(
            r"(?:你(?:还|现在)?(?:记得|知道|了解))?"
            r"我的(?P<topic>[\u4e00-\u9fffa-zA-Z0-9_-]{2,30}?)"
            r"(?P<ending>是什么|有哪些|吗)",
            value,
        )
        if generic:
            topic = generic.group("topic").strip()
            # A custom stable property is a noun-like topic (for example
            # “桌面验收代号”), not an embedded conversational question such
            # as “问题出在哪”.  Reject interrogative clauses as a class instead
            # of growing a list of complete user sentences.
            embedded_question = re.search(
                r"(?:什么|哪|怎么|为何|为什么|多少|是否|是不是|有没有|出在|意思|刚才|说的)",
                topic,
            )
            if (
                topic
                and embedded_question is None
                and not any(term in topic for term in _NON_MEMORY_FACT_TOPICS)
            ):
                mode = "existence" if generic.group("ending") == "吗" else "attribute"
                return cls(mode, "fact", topic=topic, query=raw)
        return None

    def to_tool_arguments(self) -> Dict[str, object]:
        result: Dict[str, object] = {"query_mode": self.query_mode}
        if self.attribute:
            result["attribute"] = self.attribute
        if self.topic:
            result["topic"] = self.topic
        if self.query:
            result["query"] = self.query
        return result

    def to_dict(self) -> Dict[str, object]:
        return {
            "query_mode": self.query_mode,
            "attribute": self.attribute,
            "topic": self.topic,
            "query": self.query,
        }


@dataclass(frozen=True)
class MemoryReadFact:
    memory_id: Optional[int]
    content: str
    value: str
    category: str
    scope: str
    source: str
    created_at: str = ""
    updated_at: str = ""
    authority: str = "formal_memory"

    def to_dict(self) -> Dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "value": self.value,
            "category": self.category,
            "scope": self.scope,
            "source": self.source,
            "authority": self.authority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MemoryReadSnapshot:
    request: MemoryReadRequest
    facts: List[MemoryReadFact] = field(default_factory=list)
    captured_at: str = ""
    snapshot_id: str = ""
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "request": self.request.to_dict(),
            "facts": [item.to_dict() for item in self.facts],
        }


class TypedMemoryReader:
    """Resolve typed questions against verified formal memory only."""

    def __init__(self, memory_manager: MemoryManager, now_provider=datetime.now):
        self.memory_manager = memory_manager
        self.now_provider = now_provider

    def read(
        self,
        request: MemoryReadRequest,
        *,
        category: str = "",
    ) -> MemoryReadSnapshot:
        memories = self._working_memories(category=category)
        facts: List[MemoryReadFact]
        if request.query_mode == "overview" and not request.attribute:
            facts = [self._fact(item) for item in memories]
        elif request.attribute == "preferred_name":
            record = self.memory_manager.preferred_name_record(memories=memories)
            facts = [self._fact(record, attribute="preferred_name")] if record else []
        else:
            selected = [
                item
                for item in memories
                if self._matches_attribute(item, request.attribute)
                and self._matches_topic(item, request.topic)
            ]
            if request.query_mode in {"existence", "provenance"} and request.query:
                selected = [
                    item for item in selected if self._matches_query(item, request.query)
                ]
            facts = [self._fact(item, attribute=request.attribute) for item in selected]
        facts = [item for item in facts if item.content and item.value]
        facts.sort(key=lambda item: (item.updated_at or item.created_at), reverse=True)
        return MemoryReadSnapshot(
            request=request,
            facts=facts,
            captured_at=self.now_provider().isoformat(timespec="seconds"),
            snapshot_id="memory_read_" + uuid4().hex,
        )

    def _working_memories(self, *, category: str = "") -> List[Dict[str, object]]:
        return self.memory_manager.working_memories(
            category=category or None,
            now=self.now_provider(),
        )

    @staticmethod
    def _matches_attribute(memory: Mapping[str, object], attribute: str) -> bool:
        if not attribute:
            return True
        category = str(memory.get("category", "other"))
        scope = str(memory.get("scope", "stable_identity"))
        content = str(memory.get("content", ""))
        if attribute == "preference":
            return category == "preference" or scope == "preference"
        if attribute == "habit":
            return category == "habit" or bool(re.search(r"(?:习惯|通常|一般|每天)", content))
        if attribute == "goal":
            return category in {"goal", "long_term_goal"} or bool(
                re.search(r"(?:目标|梦想|长期想|想成为)", content)
            )
        if attribute == "project":
            return category == "project"
        if attribute == "current_state":
            return scope == "current_state"
        if attribute == "fact":
            return True
        return False

    @staticmethod
    def _matches_topic(memory: Mapping[str, object], topic: str) -> bool:
        if not topic:
            return True
        content = str(memory.get("content", ""))
        if topic == "food":
            return bool(re.search(r"(?:吃|喝|口味|饮食|食物|菜)", content))
        if topic == "learning":
            return bool(re.search(r"(?:学习|读书|阅读|课程|复习)", content))
        if topic == "work":
            return bool(re.search(r"(?:工作|实习|项目|开发|代码)", content))
        if topic == "location":
            return bool(memory.get("location")) or bool(
                re.search(
                    r"(?:位于|住在|人在|地点|城市|现在在|目前在|当前在|"
                    r"在[^，,。；;]{2,30}(?:工作|实习|生活|居住))",
                    content,
                )
            )
        return topic.lower() in content.lower()

    def _matches_query(self, memory: Mapping[str, object], query: str) -> bool:
        content = str(memory.get("content", ""))
        query_core = self.memory_manager.normalize_text(query)
        for filler in sorted(_QUERY_FILLERS, key=len, reverse=True):
            query_core = query_core.replace(filler, "")
        query_core = self.memory_manager.semantic_core(query_core).strip("我你的吗呢")
        content_core = self.memory_manager.semantic_core(content)
        query_tags = {
            tag
            for tag in self.memory_manager.extract_tags(query_core)
            if len(tag) >= 2 and tag not in _QUERY_FILLERS
        }
        content_tags = set(self.memory_manager.extract_tags(content_core))
        if query_tags & content_tags:
            return True
        return bool(
            query_core
            and content_core
            and SequenceMatcher(None, query_core, content_core).ratio() >= 0.52
        )

    def _fact(
        self,
        memory: Optional[Mapping[str, object]],
        *,
        attribute: str = "",
    ) -> MemoryReadFact:
        value = dict(memory or {})
        content = str(value.get("content", "") or "").strip()
        fact_value = self._display_value(content, attribute)
        if attribute == "preferred_name":
            fact_value = str(value.get("preferred_name", "") or "").strip()
        raw_id = value.get("id")
        try:
            memory_id = int(raw_id) if raw_id not in (None, "") else None
        except (TypeError, ValueError):
            memory_id = None
        return MemoryReadFact(
            memory_id=memory_id,
            content=content,
            value=fact_value or content,
            category=str(value.get("category", "other") or "other"),
            scope=str(value.get("scope", "stable_identity") or "stable_identity"),
            source=str(value.get("source") or value.get("authority") or "formal_memory"),
            authority=str(value.get("authority") or "formal_memory"),
            created_at=str(value.get("created_at", "") or ""),
            updated_at=str(value.get("updated_at", "") or ""),
        )

    @staticmethod
    def _display_value(content: str, attribute: str) -> str:
        text = str(content or "").strip().strip("。.!！?？；;")
        if attribute == "preference":
            text = re.sub(r"^(?:我)?(?:喜欢|偏好|爱)(?:吃|喝)?", "", text)
            text = re.sub(r"^(?:我)?(?:喜欢)?(?:吃|喝)", "", text)
            text = re.sub(
                r"(?:你应该知道|你知道吧|你应该记得|吧|呀|啊|呢)$", "", text
            )
        elif attribute == "goal":
            text = re.sub(r"^(?:我的)?(?:长期)?(?:目标|梦想)(?:是|为)?", "", text)
        return text.strip(" ，,。.!！?？；;：:")
