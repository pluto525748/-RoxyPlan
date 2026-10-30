"""Request-local business date for a single chat turn.

The context is bound only while preparing or completing that turn. It does not
replace the clocks used for message timestamps, timeouts, or shared services.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterator, Optional


@dataclass(frozen=True)
class RequestBoundary:
    conversation_id: str
    business_date: str
    source_text: str
    now_provider: Callable[[], datetime]
    session_exists: Optional[Callable[[str], bool]] = None

    def plan_add_date_allowed(self, proposed_date: object = "") -> bool:
        """Check the target of a chat plan addition against the submit day.

        The model's arguments alone are insufficient: it can omit a date that
        the user explicitly supplied. Quoted task names and causal background
        clauses are not schedule targets.
        """
        if proposed_date and str(proposed_date).strip() != self.business_date:
            return False
        source = str(self.source_text or "")
        if self._adds_whole_multiday_plan(source):
            return False
        source = re.sub(r"《[^》]*》|“[^”]*”|\"[^\"]*\"|‘[^’]*’", "", source)
        source = re.sub(r"(?:因为|为了)[^，,；;。]*[，,；;。]", "", source)
        source = re.sub(r"^[^，,；;。]*[，,；;。](?:所以|因此)", "", source)
        # A recurring schedule is not an undated single task. The model can
        # hide its horizon in note and omit date; do not silently save today.
        recurring = re.search(r"每天|每日|每晚|每周|每星期|每个月|每月", source)
        explicit_today_item = re.search(
            r"(?:加入|添加|放进|加到)(?:我)?(?:今天|今日)(?:的)?(?:计划|任务)"
            r"|(?:今天|今日)(?:先|只)?(?:加入|添加|加一|加条)", source,
        )
        if recurring and not explicit_today_item:
            return False
        anchor = date.fromisoformat(self.business_date)
        absolute = re.findall(
            r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)",
            source,
        )
        for year, month, day in absolute:
            try:
                if date(int(year), int(month), int(day)) != anchor:
                    return False
            except ValueError:
                return False
        if re.search(r"下周|下个月|下月|后天|明天|明日|明早|昨天|昨日|前天", source):
            return False
        for weekday in re.findall(r"(?:周|星期)([一二三四五六日天])", source):
            number = "一二三四五六日".find("日" if weekday == "天" else weekday)
            if number != anchor.weekday():
                return False
        return True

    @staticmethod
    def _adds_whole_multiday_plan(source: str) -> bool:
        """Check the requested item, not every horizon mentioned in a turn.

        Creating a long-term outline today and adopting one concrete day from
        it are single tasks; adopting the outline itself is not one day's work.
        Inspect quoted item names here before ordinary date-title stripping.
        """
        item_patterns = (
            r"(?:^|[，,。；;\n])(?:请|帮我)?(?:把|将)?"
            r"(?P<item>[^，,。；;！？!?\n]{1,100}?)"
            r"(?:加入|添加(?:到)?|放进|加到|纳入)(?:我(?:的)?)?"
            r"(?:今天|今日)(?:的)?(?:计划|任务|待办)",
            r"(?:今天|今日)(?:先|只)?(?:加入|添加)(?:一[条项个])?"
            r"(?:计划|任务|待办)?[：:\s]*(?P<item>[^，,。；;！？!?\n]{1,100})",
        )
        for pattern in item_patterns:
            for match in re.finditer(pattern, source):
                item = match.group("item").strip(' “”‘’"《》')
                if not re.search(
                    r"(?:(?:\d+|[一二两三四五六七八九十百]+)(?:个)?(?:天|日|周|星期|月)"
                    r"|长期|多日|多周|多月).{0,24}(?:规划|方案|计划)", item,
                ):
                    continue
                if re.match(r"(?:制定|编写|设计|草拟|写|完善|修改|修订|准备|阅读|读)", item):
                    continue
                if re.search(r"第[\d一二两三四五六七八九十]+(?:天|步)", item):
                    continue
                return True
        return False

    def target_date(self) -> str:
        text = self.source_text
        absolute = re.search(
            r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)",
            text,
        )
        if absolute and any(
            word in text for word in ("今天", "今日", "今晚", "昨天", "昨日", "前天", "明天", "明日", "明早", "后天")
        ):
            raise ValueError("mixed absolute and relative dates need clarification")
        if absolute:
            return date(*(int(part) for part in absolute.groups())).isoformat()
        anchor = date.fromisoformat(self.business_date)
        if "前天" in text:
            return (anchor - timedelta(days=2)).isoformat()
        if "后天" in text:
            return (anchor + timedelta(days=2)).isoformat()
        if any(word in text for word in ("明天", "明日", "明早")):
            return (anchor + timedelta(days=1)).isoformat()
        if any(word in text for word in ("昨天", "昨日")):
            return (anchor - timedelta(days=1)).isoformat()
        return self.business_date


_CURRENT: ContextVar[Optional[RequestBoundary]] = ContextVar(
    "roxyplan_request_boundary", default=None
)


def current_request_boundary() -> Optional[RequestBoundary]:
    return _CURRENT.get()


@contextmanager
def bind_request_boundary(boundary: Optional[RequestBoundary]) -> Iterator[None]:
    token = _CURRENT.set(boundary)
    try:
        yield
    finally:
        _CURRENT.reset(token)
