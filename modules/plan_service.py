from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from modules.growth_manager import GrowthManager


@dataclass
class PlanResolution:
    status: str
    task: Optional[Dict[str, object]] = None
    candidates: List[Dict[str, object]] = field(default_factory=list)
    reason_code: str = ""


class PlanService:
    """Business facade for plan lookup; GrowthManager remains the data owner."""

    def __init__(self, growth_manager: GrowthManager) -> None:
        self.growth_manager = growth_manager

    def list_plans(self, date: Optional[str] = None) -> List[Dict[str, object]]:
        owner = self._plan_owner()
        if date is None:
            return owner.tasks()
        try:
            return owner.tasks(date)
        except TypeError:
            return owner.tasks()

    def action_records(self, date: Optional[str] = None) -> List[Dict[str, object]]:
        owner = (
            self.growth_manager
            if hasattr(self.growth_manager, "records_for_date")
            else getattr(self.growth_manager, "action_store")
        )
        if date is None:
            return owner.records_for_date()
        try:
            return owner.records_for_date(date)
        except TypeError:
            return owner.records_for_date()

    def find_similar_plans(
        self,
        title: str,
        *,
        date: Optional[str] = None,
        threshold: float = 0.72,
        pending_only: bool = False,
    ) -> List[Dict[str, object]]:
        query = self._normalize(title)
        if not query:
            return []
        scored = []
        for task in self.list_plans(date):
            if pending_only and not self._is_pending(task):
                continue
            candidate = self._normalize(task.get("title", ""))
            if not candidate:
                continue
            score = SequenceMatcher(None, query, candidate).ratio()
            query_tokens = self._tokens(query)
            candidate_tokens = self._tokens(candidate)
            overlap = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
            score = max(score, overlap)
            if query in candidate or candidate in query:
                score = max(score, 0.96)
            if score >= threshold:
                scored.append((score, task))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{**dict(task), "similarity": round(score, 4)} for score, task in scored]

    def resolve(
        self,
        reference: object,
        *,
        date: Optional[str] = None,
        pending_only: bool = False,
    ) -> PlanResolution:
        value = str(reference or "").strip()
        tasks = self.list_plans(date)
        if value.startswith("task_"):
            matched = [item for item in tasks if str(item.get("uid")) == value]
        elif value.isdigit():
            matched = [item for item in tasks if int(item.get("id", 0)) == int(value)]
        else:
            matched = self.find_similar_plans(
                value,
                date=date,
                pending_only=pending_only,
            )
        if pending_only:
            matched = [
                item
                for item in matched
                if self._is_pending(item)
            ]
        if not matched:
            return PlanResolution("not_found", reason_code="plan_not_found")
        top_score = float(matched[0].get("similarity", 1.0) or 0.0)
        close = [
            item
            for item in matched
            if top_score - float(item.get("similarity", top_score) or 0.0) <= 0.08
        ]
        if len(close) > 1:
            return PlanResolution(
                "ambiguous",
                candidates=close[:5],
                reason_code="multiple_plan_matches",
            )
        return PlanResolution("resolved", task=dict(matched[0]))

    @staticmethod
    def _normalize(value: object) -> str:
        text = str(value or "").lower()
        for term in (
            "我", "今天", "今晚", "下午", "上午", "早上", "计划", "任务",
            "已经", "刚才", "刚刚", "完成", "做完", "学完", "整理完", "的", "了",
        ):
            text = text.replace(term, "")
        return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")

    def _plan_owner(self):
        return (
            self.growth_manager
            if hasattr(self.growth_manager, "tasks")
            else getattr(self.growth_manager, "plan_store")
        )

    @staticmethod
    def _tokens(text: str):
        if not text:
            return set()
        if len(text) <= 2:
            return {text}
        return {text[index : index + 2] for index in range(len(text) - 1)}

    @staticmethod
    def _is_pending(task: Dict[str, object]) -> bool:
        return not bool(task.get("done")) and task.get("status") in {
            None,
            "",
            "pending",
        }
