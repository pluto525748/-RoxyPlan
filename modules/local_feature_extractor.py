from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from modules.chinese_entity_parser import ChineseEntityParser


@dataclass
class LocalFeatures:
    normalized_text: str
    operation_cues: List[str] = field(default_factory=list)
    domain_cues: List[str] = field(default_factory=list)
    query_cues: List[str] = field(default_factory=list)
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

    def to_dict(self) -> Dict[str, object]:
        return {
            "normalized_text": self.normalized_text,
            "operation_cues": list(self.operation_cues),
            "domain_cues": list(self.domain_cues),
            "query_cues": list(self.query_cues),
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
        }


class LocalFeatureExtractor:
    """Extract stable local cues without deciding tools or resource IDs."""

    OPERATION_TERMS = (
        "查看", "列出", "搜索", "添加", "加入", "加一条", "再加", "放进", "记到", "安排", "记录", "保存",
        "完成", "做完", "学完", "更新", "修改", "改成", "推迟", "延后",
        "删除", "清空", "归档", "恢复", "确认", "忽略", "拒绝", "重开",
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
    NEGATION_TERMS = ("不要", "别", "不想", "不用", "不是", "没有要", "别再")
    CONFIRM_TERMS = ("确认", "是的", "就这样", "执行", "继续")
    CANCEL_TERMS = ("取消", "算了", "不用了", "先不", "不要了")
    RISK_TERMS = ("删除", "清空", "覆盖", "永久", "全部删", "冲突")
    CONJUNCTION_TERMS = ("然后", "同时", "顺便", "并且", "再", "并记录", "再把")
    REFERENCE_TERMS = ("这个", "那个", "刚才那个", "上一个", "前一个", "这条", "这两个", "它")

    def __init__(self, entity_parser: ChineseEntityParser = None) -> None:
        self.entity_parser = entity_parser or ChineseEntityParser()

    def extract(self, text: str) -> LocalFeatures:
        raw = str(text or "").strip()
        normalized = re.sub(r"\s+", "", raw).lower()
        parsed_time = self.entity_parser.parse(raw)
        duration_update = re.search(
            r"(?:改成|调整为|设为)((?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|半小时|(?:\d+|[一二两三四五六七八九十]+)分钟)",
            normalized,
        )
        if duration_update:
            parsed_duration = self.entity_parser.parse(duration_update.group(1))
            if parsed_duration.get("duration_minutes") is not None:
                parsed_time["duration_minutes"] = parsed_duration["duration_minutes"]
        operations = self._present(normalized, self.OPERATION_TERMS)
        domains = [
            name
            for name, terms in self.DOMAIN_TERMS.items()
            if any(term in normalized for term in terms)
        ]
        negations = self._present(normalized, self.NEGATION_TERMS)
        query = self._present(normalized, self.QUERY_TERMS)
        confirmations = self._present(normalized, self.CONFIRM_TERMS)
        cancellations = self._present(normalized, self.CANCEL_TERMS)
        conjunctions = self._present(normalized, self.CONJUNCTION_TERMS)
        ordinals = re.findall(r"第(?:\d+|[一二三四五六七八九十]+)(?:个|条|项)", normalized)
        references = self._present(normalized, self.REFERENCE_TERMS)
        dates = re.findall(r"今天|明天|后天|今晚|明早", normalized)
        periods = re.findall(r"早上|上午|中午|下午|晚上|今晚|明早|下班(?:以后|后)", normalized)
        durations = re.findall(
            r"半小时|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|"
            r"(?:\d+|[一二两三四五六七八九十]+)分钟",
            normalized,
        )
        explicit_ids = {
            domain: self._positive_ids(values)
            for domain, values in {
                "plan": re.findall(r"(?:计划|任务)\s*(\d+)", normalized),
                "memory": re.findall(r"(?:记忆)\s*(\d+)", normalized),
                "candidate": re.findall(r"(?:候选)\s*(\d+)", normalized),
                "conflict": re.findall(r"(?:冲突)\s*(\d+)", normalized),
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
            operations
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
        )

    @classmethod
    def split_actions(cls, text: str) -> List[str]:
        value = str(text or "").strip()
        parts = re.split(
            r"(?:，|,|；|;)?(?:然后|同时|顺便|并且|再(?=记录|添加|加入|把|完成|更新|修改)|并(?=记录|添加|加入|把|完成|更新|修改))",
            value,
        )
        return [item.strip(" ，,；;") for item in parts if item.strip(" ，,；;")]

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
