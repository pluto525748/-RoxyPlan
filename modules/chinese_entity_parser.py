from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional


CHINESE_DIGITS = {
    "零": 0,
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
    "十": 10,
}


class ChineseEntityParser:
    """Small deterministic parser for RoxyPlan's plan-time vocabulary."""

    def __init__(self, now_provider: Callable[[], datetime] = datetime.now) -> None:
        self.now_provider = now_provider

    def parse(self, text: str) -> Dict[str, object]:
        value = str(text).strip()
        result: Dict[str, object] = {
            "date": "",
            "time_period": "",
            "start_time": "",
            "duration_minutes": None,
            "shift_minutes": None,
            "target_id": "",
            "reference_text": self._reference(value),
            "operation": self._operation(value),
            "scope": "today" if "今天" in value or "今日" in value else "",
            "raw_time_text": "",
            "event_anchor": "",
            "ambiguous": False,
            "clarification_question": "",
        }

        if re.search(r"\d+\s*(?:个)?\s*一晚上", value):
            result["ambiguous"] = True
            result["clarification_question"] = (
                "“一晚上”不是明确时长。你想安排多少分钟或多少小时？"
            )
            return result

        now = self.now_provider()
        if "后天" in value:
            result["date"] = (now.date() + timedelta(days=2)).isoformat()
        elif "明天" in value or "明日" in value or "明早" in value:
            result["date"] = (now.date() + timedelta(days=1)).isoformat()
        elif "昨天" in value or "昨日" in value:
            result["date"] = (now.date() - timedelta(days=1)).isoformat()
        elif "今天" in value or "今晚" in value:
            result["date"] = now.date().isoformat()

        if any(term in value for term in ("明早", "早上", "上午")):
            result["time_period"] = "上午"
        elif "中午" in value:
            result["time_period"] = "中午"
        elif "下午" in value:
            result["time_period"] = "下午"
        elif any(term in value for term in ("今晚", "晚上", "一晚上")):
            result["time_period"] = "晚上"

        clock = re.search(
            r"(?:(上午|早上|下午|晚上|今晚)\s*)?(\d{1,2})(?:点|时)(半|\d{1,2}分)?",
            value,
        )
        if clock:
            hour = int(clock.group(2))
            minute_text = clock.group(3) or ""
            minute = 30 if minute_text == "半" else int(minute_text[:-1] or 0)
            slot = clock.group(1) or result["time_period"]
            if slot in {"下午", "晚上", "今晚"} and hour < 12:
                hour += 12
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                result["start_time"] = f"{hour:02d}:{minute:02d}"
            else:
                result["ambiguous"] = True
                result["clarification_question"] = "这个具体时间无法识别，请换成例如“下午 3 点”。"

        duration = self._duration_minutes(value)
        if duration is not None:
            if re.search(r"(?:推迟|延后|往后挪|往后推)", value):
                result["shift_minutes"] = duration
            else:
                result["duration_minutes"] = duration

        anchor = re.search(r"(下班以后|下班后|放学以后|放学后)", value)
        if anchor:
            result["event_anchor"] = anchor.group(1)
            result["raw_time_text"] = anchor.group(1)
            if not result["start_time"]:
                result["ambiguous"] = True
                result["clarification_question"] = (
                    f"{anchor.group(1)}大概是几点？告诉我具体时间后再调整计划。"
                )

        raw_parts = re.findall(
            r"今天下午|今天晚上|今晚|明天早上|明早|上午|中午|下午|晚上|"
            r"半小时|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|"
            r"(?:\d+|[一二两三四五六七八九十]+)分钟|下班以后|下班后",
            value,
        )
        if raw_parts and not result["raw_time_text"]:
            result["raw_time_text"] = " ".join(raw_parts)

        if result["shift_minutes"] is not None and not result["start_time"]:
            result["ambiguous"] = True
            result["clarification_question"] = (
                "要推迟这条计划，我还需要知道它原来的具体开始时间。"
            )
        return result

    def clean_plan_title(self, text: str) -> str:
        value = str(text).strip().strip("。.!！?？")
        # Keep conversational acknowledgement and first-person planning
        # framing out of the persisted task title.  The task itself remains
        # untouched; this only normalizes the boundary around it.
        value = re.sub(
            r"^(?:(?:好(?:的|啦|了)?|可以(?:了)?|行(?:了)?|明白了|知道了|收到|"
            r"(?:谢谢|多谢)(?:你|您)?(?:的)?(?:指导|建议|帮助|提醒|说明)?|"
            r"感谢(?:你|您)?(?:的)?(?:指导|建议|帮助|提醒|说明)?)[，,、：:；;\s]*)+",
            "",
            value,
        )
        value = re.sub(
            r"^(?:我|本人)(?:现在|今天|今晚|明天|明早|早上|上午|中午|下午|晚上)?"
            r"(?:想|要|打算|准备|计划|希望|决定|开始|继续|会|将要)?",
            "",
            value,
        )
        value = re.sub(
            # Keep longer verbs first.  Matching ``加`` before ``加入`` left
            # the stray title ``入今日计划`` for an otherwise empty command.
            r"^(?:再|又|还)?(?:加入|添加|加)(?:一条|一项|一个)?(?:计划|任务)?\s*",
            "",
            value,
        )
        value = re.sub(
            r"^(?:请|帮我|请帮我|给我)?(?:再|又|也|然后|顺便|另外)?(?:把)?(?:今天|今日)?(?:上午|中午|下午|晚上|今晚|明早)?"
            r"(?:想|要|打算|计划|准备|安排|加入|添加)?(?:先|一下)?\s*",
            "",
            value,
        )
        value = re.sub(
            r"(?:加入|添加|放进|安排进|安排到)(?:今天|今日)?(?:的)?(?:计划|任务|清单)(?:里)?$",
            "",
            value,
        )
        value = re.sub(r"^(?:今天|上午|中午|下午|晚上|今晚|明早)\s*", "", value)
        return re.sub(r"\s+", "", value).strip("，,：:的时间")

    def extract_plan_schedule(self, text: str) -> Dict[str, List[Dict[str, object]]]:
        """Extract closed time ranges without guessing ambiguous alternatives.

        A pasted schedule is still ordinary user text.  This helper only
        recognizes independently executable lines such as ``18:00 到 18:30
        吃饭``.  Open-ended ranges and alternative choices remain unresolved
        so callers can ask instead of silently persisting a partial meaning.
        """
        source = str(text or "")
        items: List[Dict[str, object]] = []
        unresolved: List[Dict[str, object]] = []
        seen = set()
        pattern = re.compile(
            r"(?m)^\s*(\d{1,2})[:：](\d{2})\s*(?:到|至|[-—–])\s*"
            r"(\d{1,2})[:：](\d{2})\s*([^\r\n]+)"
        )
        for match in pattern.finditer(source):
            start_hour, start_minute, end_hour, end_minute = (
                int(match.group(index)) for index in range(1, 5)
            )
            if not (
                0 <= start_hour <= 23
                and 0 <= end_hour <= 23
                and 0 <= start_minute <= 59
                and 0 <= end_minute <= 59
            ):
                continue
            raw_title = re.sub(
                r"^[\s：:，,、—–-]+|[\s。.!！?？；;]+$",
                "",
                match.group(5),
            )
            title = self.clean_plan_title(raw_title)
            if not title:
                continue
            signature = (start_hour, start_minute, end_hour, end_minute, title)
            if signature in seen:
                continue
            seen.add(signature)
            item: Dict[str, object] = {
                "title": title,
                "start_time": f"{start_hour:02d}:{start_minute:02d}",
                "end_time": f"{end_hour:02d}:{end_minute:02d}",
            }
            start_total = start_hour * 60 + start_minute
            end_total = end_hour * 60 + end_minute
            duration = end_total - start_total
            if duration <= 0:
                duration += 24 * 60
            item["duration_minutes"] = duration
            if re.search(r"(?:或者|还是|二选一|任选|看哪个)", raw_title):
                unresolved.append(item)
            else:
                items.append(item)
        return {"items": items, "unresolved": unresolved}

    @classmethod
    def _duration_minutes(cls, text: str) -> Optional[int]:
        if "半小时" in text:
            return 30
        hour = re.search(
            r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(?:个)?\s*小时(半)?",
            text,
        )
        if hour:
            number = cls._number(hour.group(1))
            if number is None:
                return None
            return int(number * 60 + (30 if hour.group(2) else 0))
        minute = re.search(
            r"(\d+|[一二两三四五六七八九十]+)\s*(?:个)?\s*分钟", text
        )
        if minute:
            number = cls._number(minute.group(1))
            return int(number) if number is not None else None
        return None

    @staticmethod
    def _number(value: str) -> Optional[float]:
        text = str(value).strip()
        try:
            return float(text)
        except ValueError:
            pass
        if text in CHINESE_DIGITS:
            return float(CHINESE_DIGITS[text])
        if "十" in text:
            left, _, right = text.partition("十")
            tens = CHINESE_DIGITS.get(left, 1) if left else 1
            ones = CHINESE_DIGITS.get(right, 0) if right else 0
            return float(tens * 10 + ones)
        return None

    @staticmethod
    def _reference(text: str) -> str:
        match = re.search(
            r"(刚才那个|刚添加的|上一个计划|前一个计划|前面的计划|这个|那个|这件事|它)",
            text,
        )
        return match.group(1) if match else ""

    @staticmethod
    def _operation(text: str) -> str:
        for name, terms in (
            ("delete", ("删除", "删掉", "移除")),
            ("complete", ("完成", "做完", "学完", "标记完成")),
            ("reschedule", ("推迟", "延后", "改到", "挪到")),
            ("update", ("改成", "调整为", "修改")),
            ("add", ("加入", "添加", "安排")),
        ):
            if any(term in text for term in terms):
                return name
        return ""
