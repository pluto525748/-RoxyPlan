from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from modules.chinese_entity_parser import ChineseEntityParser


@dataclass
class LocalFeatures:
    normalized_text: str
    operation_cues: List[str] = field(default_factory=list)
    domain_cues: List[str] = field(default_factory=list)
    query_cues: List[str] = field(default_factory=list)
    advice_cues: List[str] = field(default_factory=list)
    negation_cues: List[str] = field(default_factory=list)
    confirmation_cues: List[str] = field(default_factory=list)
    cancellation_cues: List[str] = field(default_factory=list)
    risk_cues: List[str] = field(default_factory=list)
    conjunctions: List[str] = field(default_factory=list)
    ordinal_expressions: List[str] = field(default_factory=list)
    reference_expressions: List[str] = field(default_factory=list)
    date_expressions: List[str] = field(default_factory=list)
    time_period_expressions: List[str] = field(default_factory=list)
    duration_expressions: List[str] = field(default_factory=list)
    explicit_ids: Dict[str, List[int]] = field(default_factory=dict)
    is_question: bool = False
    likely_actionable: bool = False
    parsed_time: Dict[str, object] = field(default_factory=dict)
    command_text: str = ""
    payload_text: str = ""
    command_span: List[Tuple[int, int]] = field(default_factory=list)
    protected_payload_span: Optional[Tuple[int, int]] = None
    polarity: str = "statement"
    clause_parse_status: str = "not_actionable"

    def to_dict(self) -> Dict[str, object]:
        return {
            "normalized_text": self.normalized_text,
            "operation_cues": list(self.operation_cues),
            "domain_cues": list(self.domain_cues),
            "query_cues": list(self.query_cues),
            "advice_cues": list(self.advice_cues),
            "negation_cues": list(self.negation_cues),
            "confirmation_cues": list(self.confirmation_cues),
            "cancellation_cues": list(self.cancellation_cues),
            "risk_cues": list(self.risk_cues),
            "conjunctions": list(self.conjunctions),
            "ordinal_expressions": list(self.ordinal_expressions),
            "reference_expressions": list(self.reference_expressions),
            "date_expressions": list(self.date_expressions),
            "time_period_expressions": list(self.time_period_expressions),
            "duration_expressions": list(self.duration_expressions),
            "explicit_ids": {
                key: list(value) for key, value in self.explicit_ids.items()
            },
            "is_question": self.is_question,
            "likely_actionable": self.likely_actionable,
            "parsed_time": dict(self.parsed_time),
            "command_text": self.command_text,
            "payload_text": self.payload_text,
            "command_span": [list(item) for item in self.command_span],
            "protected_payload_span": (
                list(self.protected_payload_span)
                if self.protected_payload_span is not None
                else None
            ),
            "polarity": self.polarity,
            "clause_parse_status": self.clause_parse_status,
        }


class LocalFeatureExtractor:
    """Extract stable local cues without deciding tools or resource IDs."""

    OPERATION_TERMS = (
        "查看", "列出", "搜索", "添加", "加入", "加进", "加到", "加一条", "再加", "放进", "放到", "塞进", "记到", "安排", "记录", "保存", "记住",
        "完成", "做完", "学完", "更新", "修改", "改成", "推迟", "延后",
        "删除", "忘记", "清空", "归档", "恢复", "确认", "忽略", "拒绝", "重开",
        "跳舞", "表演", "唤醒", "睡眠",
    )
    DOMAIN_TERMS = {
        "memory": ("记忆", "记住", "记得", "长期信息", "候选", "待审核", "冲突", "归档"),
        "plan": ("计划", "任务", "要做的事", "安排", "学习时间"),
        "action_log": ("行动记录", "记录", "进展", "推进", "完成了", "理解了", "听了"),
        "growth": ("复盘", "成长日志"),
        "pet": ("跳舞", "舞蹈", "睡眠", "唤醒"),
        "conversation": ("对话", "聊天记录", "历史"),
    }
    QUERY_TERMS = ("什么", "哪些", "多少", "有没有", "查看", "看看", "列出", "告诉我", "怎么", "如何", "为什么")
    ADVICE_TERMS = (
        "你觉得", "建议", "给点建议", "怎么学", "如何学", "怎么安排",
        "如何安排", "帮我安排一下", "给我安排一下", "推荐", "从哪开始",
    )
    NEGATION_TERMS = ("不要", "别", "不想", "不用", "不是", "没有要", "别再")
    CONFIRM_TERMS = ("确认", "是的", "就这样", "执行", "继续")
    CANCEL_TERMS = ("取消", "算了", "不用了", "先不", "不要了")
    RISK_TERMS = ("删除", "清空", "覆盖", "永久", "全部删", "冲突")
    CONJUNCTION_TERMS = ("然后", "同时", "顺便", "并且", "再", "并记录", "再把")
    REFERENCE_TERMS = (
        "这些", "那些", "你说的", "上面说的", "刚才的",
        "这个", "那个", "刚才那个", "上一个", "前一个", "这条", "这两个", "它",
        "上述内容", "前面提到的", "刚才提到的", "这几条", "前者", "后者", "那件事情",
    )

    def __init__(self, entity_parser: ChineseEntityParser = None) -> None:
        self.entity_parser = entity_parser or ChineseEntityParser()

    def extract(self, text: str) -> LocalFeatures:
        raw = str(text or "").strip()
        envelope = self._extract_command_envelope(raw)
        command_source = (
            str(envelope["command_text"])
            if envelope["polarity"] == "command"
            else ""
        )
        normalized = re.sub(r"\s+", "", raw).lower()
        command_normalized = re.sub(r"\s+", "", command_source).lower()
        semantic_text = command_normalized if envelope["payload_text"] else normalized
        parsed_time = self.entity_parser.parse(raw)
        duration_update = re.search(
            r"(?:改成|调整为|设为)((?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|半小时|(?:\d+|[一二两三四五六七八九十]+)分钟)",
            semantic_text,
        )
        if duration_update:
            parsed_duration = self.entity_parser.parse(duration_update.group(1))
            if parsed_duration.get("duration_minutes") is not None:
                parsed_time["duration_minutes"] = parsed_duration["duration_minutes"]
        operations = self._present(command_normalized, self.OPERATION_TERMS)
        domains = [
            name
            for name, terms in self.DOMAIN_TERMS.items()
            if any(term in semantic_text for term in terms)
        ]
        negations = self._present(semantic_text, self.NEGATION_TERMS)
        query = self._present(semantic_text, self.QUERY_TERMS)
        advice = self._present(semantic_text, self.ADVICE_TERMS)
        confirmations = self._present(semantic_text, self.CONFIRM_TERMS)
        cancellations = self._present(semantic_text, self.CANCEL_TERMS)
        conjunctions = self._present(semantic_text, self.CONJUNCTION_TERMS)
        ordinals = re.findall(r"第(?:\d+|[一二三四五六七八九十]+)(?:个|条|项)", semantic_text)
        references = self._present(semantic_text, self.REFERENCE_TERMS)
        dates = re.findall(
            r"今天|今日|昨天|昨日|前天|明天|明日|后天|今晚|明早",
            semantic_text,
        )
        periods = re.findall(r"早上|上午|中午|下午|晚上|今晚|明早|下班(?:以后|后)", semantic_text)
        durations = re.findall(
            r"半小时|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|"
            r"(?:\d+|[一二两三四五六七八九十]+)分钟",
            semantic_text,
        )
        explicit_ids = {
            domain: self._positive_ids(values)
            for domain, values in {
                "plan": re.findall(r"(?:计划|任务)\s*(\d+)", semantic_text),
                "memory": re.findall(r"(?:记忆)\s*(\d+)", semantic_text),
                "candidate": re.findall(r"(?:候选)\s*(\d+)", semantic_text),
                "conflict": re.findall(r"(?:冲突)\s*(\d+)", semantic_text),
            }.items()
            if values
        }
        is_question = bool(
            raw.endswith(("?", "？", "吗", "呢"))
            or query
        )
        action_request_question = bool(
            re.search(
                r"(?:能|可以|可不可以|能不能).{0,12}(?:帮我|给我|替我|来一段|表演|跳一个|执行|添加|记录|保存).{0,12}(?:吗|么|不)",
                normalized,
            )
            or re.search(r"(?:帮我|给我|替我).{0,18}(?:吗|么)$", normalized)
        )
        informational = bool(
            re.search(r"(?:怎么|如何|为什么|好处|原理|教程|代码|制作).{0,12}(?:计划|记录|记忆|跳舞|舞蹈)", normalized)
        )
        likely_actionable = bool(
            envelope["polarity"] == "command"
            and operations
            and not negations
            and not informational
            and (not is_question or action_request_question)
            and (
                bool(domains)
                or any(term in normalized for term in ("帮我", "请", "把", "给我", "放进", "记下来"))
            )
        )
        return LocalFeatures(
            normalized_text=normalized,
            operation_cues=operations,
            domain_cues=domains,
            query_cues=query,
            advice_cues=advice,
            negation_cues=negations,
            confirmation_cues=confirmations,
            cancellation_cues=cancellations,
            risk_cues=self._present(normalized, self.RISK_TERMS),
            conjunctions=conjunctions,
            ordinal_expressions=ordinals,
            reference_expressions=references,
            date_expressions=self._unique(dates),
            time_period_expressions=self._unique(periods),
            duration_expressions=self._unique(durations),
            explicit_ids=explicit_ids,
            is_question=is_question,
            likely_actionable=likely_actionable,
            parsed_time=parsed_time,
            command_text=str(envelope["command_text"]),
            payload_text=str(envelope["payload_text"]),
            command_span=list(envelope["command_span"]),
            protected_payload_span=envelope["protected_payload_span"],
            polarity=str(envelope["polarity"]),
            clause_parse_status=str(envelope["clause_parse_status"]),
        )

    @classmethod
    def _extract_command_envelope(cls, raw: str) -> Dict[str, object]:
        """Separate explicit command words from opaque user-provided content.

        The payload is deliberately not normalized or inspected for operations.
        A downstream router can therefore decide *what* the user asked to do
        without treating words inside the requested content as new commands.
        """
        value = str(raw or "").strip()
        compact = re.sub(r"\s+", "", value).lower()
        empty = {
            "command_text": "",
            "payload_text": "",
            "command_span": [],
            "protected_payload_span": None,
            "polarity": "statement",
            "clause_parse_status": "not_actionable",
        }
        if not value:
            return empty
        # This exact maintenance command owns an opaque, literal payload.
        # Negation, punctuation and operation words inside the memory body
        # are data, not a second instruction to the application.
        if value.startswith(("忘记：", "忘记:")):
            payload = value[3:].strip()
            return {
                "command_text": "忘记",
                "payload_text": payload,
                "command_span": [(0, 3)],
                "protected_payload_span": (3, len(value)),
                "polarity": "command",
                "clause_parse_status": "parsed",
            }
        # A negation may precede a long protected payload (for example,
        # ``先不要把验收事项加入今天计划``).  Bound the match by the
        # operation's command shell rather than by an arbitrary payload length;
        # otherwise the negation is hidden inside ``payload_text`` and the
        # write path incorrectly remains actionable.
        if re.match(
            r"(?:^|先|那|可是|但是|我|我现在|我今天)?(?:不要|别|不用|不想|不是)"
            r"\s*(?:(?:把|将|替我|帮我)[^，,。！？!?；;]*?"
            r"(?:跳(?:舞|舞蹈)?|表演|展示|删除|完成|添加|加入|记录|保存)"
            r"|(?:跳(?:舞|舞蹈)?|表演|展示|删除|完成|添加|加入|记录|保存))"
            r"(?:到|进|入)?(?:我)?(?:今天)?(?:的)?(?:计划|任务|行动记录|记忆)?",
            compact,
        ):
            return {**empty, "polarity": "negated", "clause_parse_status": "negated"}
        if re.search(
            r"(?:不要|别|不用|不想|不是)"
            r"(?:(?:把|将|替我|帮我)[^，,。！？!?;；]*?)?"
            r"(?:添加|加入|加进|放进|放入|加到|放到)"
            r"(?:(?:今天|今日)(?:的)?)?(?:计划|任务|待办|清单)",
            compact,
        ):
            # A write negation can follow a longer advice request.  It still
            # vetoes the plan command shell; text before it remains available
            # to the semantic model as the actual advice request.
            return {**empty, "polarity": "negated", "clause_parse_status": "negated"}
        if value.endswith(("?", "？", "吗", "呢")):
            return {**empty, "polarity": "question", "clause_parse_status": "question"}

        # A command envelope is safe only when the utterance contains one
        # communicative act. Compound, contrastive and conditional sentences
        # must stay intact for the single SemanticDecision; otherwise a prefix
        # such as “记住” or a final “加入计划” would swallow the other clauses as
        # opaque payload and silently drop an action, correction or condition.
        operation_count = len(cls._present(compact, cls.OPERATION_TERMS))
        compound_boundary = bool(
            re.search(r"(?:然后|同时|顺便|并且|，并|,并|，再|,再|；|;|，但|,但|不过|可是)", value)
        )
        conditional_boundary = bool(
            re.match(r"(?:如果|假如|要是|万一|除非)", compact)
        )
        if conditional_boundary or (compound_boundary and operation_count > 1):
            return empty

        def protected(
            command_text: str,
            payload: str,
            command_span: List[Tuple[int, int]],
            payload_span: Tuple[int, int],
        ) -> Dict[str, object]:
            return {
                "command_text": command_text,
                "payload_text": payload,
                "command_span": command_span,
                "protected_payload_span": payload_span,
                "polarity": "command",
                "clause_parse_status": "parsed",
            }

        memory_prefix = re.fullmatch(
            r"(?P<command>(?:请|帮我)?(?:记住|记得))(?P<payload>.+)", value
        )
        if memory_prefix:
            payload = memory_prefix.group("payload").lstrip(" ：:,，")
            payload_start = memory_prefix.end("payload") - len(payload)
            return protected(
                memory_prefix.group("command"),
                payload,
                [(memory_prefix.start("command"), memory_prefix.end("command"))],
                (payload_start, memory_prefix.end("payload")),
            )
        formal_memory_prefix = re.fullmatch(
            r"(?P<command>保存(?:长期)?记忆)[：:\s]*(?P<payload>.+)", value
        )
        if formal_memory_prefix:
            return protected(
                formal_memory_prefix.group("command"),
                formal_memory_prefix.group("payload"),
                [(formal_memory_prefix.start("command"), formal_memory_prefix.end("command"))],
                (formal_memory_prefix.start("payload"), formal_memory_prefix.end("payload")),
            )
        memory_suffix = re.fullmatch(
            r"(?P<command>保存)(?P<payload>.+?)(?:的)?(?:长期)?记忆", value
        )
        if memory_suffix:
            return protected(
                "保存记忆",
                memory_suffix.group("payload"),
                [(memory_suffix.start("command"), memory_suffix.end("command")), (memory_suffix.end("payload"), len(value))],
                (memory_suffix.start("payload"), memory_suffix.end("payload")),
            )
        plan_target = re.fullmatch(
            r"(?:把)?(?P<payload>.+?)(?P<command>(?:加到|加入|加进|添加到|放进)(?:我)?(?:今天)?(?:的)?(?:计划|任务|要做的事)(?:里)?)",
            value,
        )
        if plan_target:
            payload = plan_target.group("payload")
            if re.search(r"[、；;]", payload) or re.search(
                r"[二两三四五六七八九十\d]+(?:件事|项|条|个任务|个计划)",
                payload,
            ):
                # A list is not an opaque single-plan payload.  Keep the whole
                # utterance for the semantic model so every item can become an
                # independently validated action.
                return empty
            spans = [(plan_target.start("command"), plan_target.end("command"))]
            if value.startswith("把"):
                spans.insert(0, (0, 1))
            return protected(
                "加入今天计划",
                payload,
                spans,
                (plan_target.start("payload"), plan_target.end("payload")),
            )
        action_prefix = re.fullmatch(
            r"(?P<command>(?:记录(?:一下)?|记(?:一下|一笔)))(?P<payload>.+)", value
        )
        if action_prefix:
            payload = action_prefix.group("payload").lstrip(" ：:,，")
            payload_start = action_prefix.end("payload") - len(payload)
            return protected(
                action_prefix.group("command"),
                payload,
                [(action_prefix.start("command"), action_prefix.end("command"))],
                (payload_start, action_prefix.end("payload")),
            )
        action_suffix = re.fullmatch(
            r"(?:把)?(?P<payload>.+?)(?P<command>记到(?:今天的)?行动(?:记录)?里)", value
        )
        if action_suffix:
            return protected(
                "记到行动记录里",
                action_suffix.group("payload"),
                [(action_suffix.start("command"), action_suffix.end("command"))],
                (action_suffix.start("payload"), action_suffix.end("payload")),
            )
        if re.fullmatch(r"(?:请|帮我|给我|现在)?(?:开始)?跳(?:个|一支|一段|一下)?(?:舞|舞蹈)?", compact) or re.fullmatch(
            r"(?:请|帮我|给我|现在)?(?:表演|展示)(?:个|一支|一段|一下)?(?:舞|舞蹈)?", compact
        ) or re.fullmatch(r"(?:给我|现在)?来(?:个|一支|一段|一)?(?:段)?舞", compact) or re.fullmatch(
            r"再(?:跳(?:一次|一遍|一个舞|一支舞|一段舞)?|来(?:一个|一段|一次))", compact
        ):
            return {
                "command_text": value,
                "payload_text": "",
                "command_span": [(0, len(value))],
                "protected_payload_span": None,
                "polarity": "command",
                "clause_parse_status": "parsed",
            }
        if re.search(r"(?:添加|加入|加进|再加|放进|安排|改成|调整为|删除|完成|记录|保存|记住)", compact):
            return {
                "command_text": value,
                "payload_text": "",
                "command_span": [(0, len(value))],
                "protected_payload_span": None,
                "polarity": "command",
                "clause_parse_status": "clarification_required"
                if cls._present(compact, cls.REFERENCE_TERMS)
                else "parsed",
            }
        return empty

    @classmethod
    def split_actions(cls, text: str) -> List[str]:
        value = str(text or "").strip()
        parts = re.split(
            r"(?:，|,|；|;)?(?:然后|同时|顺便|并且|再(?=记录|添加|加入|加进|把|完成|更新|修改)|并(?=记录|添加|加入|加进|把|完成|更新|修改))",
            value,
        )
        cleaned = [item.strip(" ，,；;") for item in parts if item.strip(" ，,；;")]
        if len(cleaned) > 1:
            return cleaned
        clauses = [
            item.strip(" ，,；;")
            for item in re.split(r"[，,；;]", value)
            if item.strip(" ，,；;")
        ]
        if len(clauses) > 1 and all(
            any(term in clause for term in cls.OPERATION_TERMS)
            for clause in clauses
        ):
            return clauses
        return cleaned

    @staticmethod
    def _present(text: str, terms) -> List[str]:
        return [term for term in terms if term in text]

    @staticmethod
    def _positive_ids(values) -> List[int]:
        result = []
        for value in values:
            parsed = int(value)
            if parsed > 0 and parsed not in result:
                result.append(parsed)
        return result

    @staticmethod
    def _unique(values) -> List[str]:
        result = []
        for value in values:
            if value not in result:
                result.append(value)
        return result
