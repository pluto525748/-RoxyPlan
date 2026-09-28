from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Mapping, Optional

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
        exact_only: bool = False,
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
            if exact_only:
                if query != candidate:
                    continue
                scored.append((1.0, task))
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

    def find_semantic_duplicates(
        self,
        title: str,
        *,
        duration_minutes: Optional[int] = None,
        time_slot: str = "",
        date: Optional[str] = None,
        pending_only: bool = False,
    ) -> List[Dict[str, object]]:
        """Find plans that represent the same scheduled work item.

        Fuzzy matching is intentionally not used here.  A duplicate requires
        an exact canonical task title and compatible explicit scheduling
        fields.  Two different explicit durations remain separate plans;
        missing duration is treated as compatible because it does not prove a
        distinct task.
        """
        target = self._canonical_task_title(title)
        if not target:
            return []
        target_duration = self._duration_value(title, duration_minutes)
        target_slot = str(time_slot or "").strip().casefold()
        matches: List[Dict[str, object]] = []
        for task in self.list_plans(date):
            if pending_only and not self._is_pending(task):
                continue
            if target != self._canonical_task_title(task.get("title", "")):
                continue
            existing_duration = self._duration_value(
                task.get("title", ""), task.get("duration_minutes")
            )
            if (
                target_duration is not None
                and existing_duration is not None
                and target_duration != existing_duration
            ):
                continue
            existing_slot = str(task.get("time_slot", "") or "").strip().casefold()
            if target_slot and existing_slot and target_slot != existing_slot:
                continue
            matches.append({**dict(task), "similarity": 1.0})
        return matches

    def find_duplicate_groups(
        self,
        *,
        date: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        """Return deterministic groups of user-reviewable similar plans."""
        tasks = [dict(item) for item in self.list_plans(date)]
        remaining = list(range(len(tasks)))
        groups: List[Dict[str, object]] = []
        while remaining:
            seed = remaining.pop(0)
            members = {seed}
            changed = True
            while changed:
                changed = False
                for index in list(remaining):
                    if any(
                        self._plans_are_merge_candidates(tasks[index], tasks[member])
                        for member in members
                    ):
                        remaining.remove(index)
                        members.add(index)
                        changed = True
            if len(members) < 2:
                continue
            candidates = [tasks[index] for index in sorted(members)]
            preview = self.build_merge_preview(candidates)
            groups.append(
                {
                    "group_id": f"duplicate_group_{len(groups) + 1}",
                    "candidates": candidates,
                    "preview": preview,
                }
            )
        return groups

    def build_merge_preview(
        self,
        tasks: List[Mapping[str, object]],
        *,
        proposed: Optional[Mapping[str, object]] = None,
    ) -> Dict[str, object]:
        """Build a non-mutating merge preview from verified task records."""
        stored = [dict(item) for item in tasks if isinstance(item, Mapping)]
        if not stored:
            return {}
        sources = [*stored]
        if proposed:
            sources.append(dict(proposed))
        target = max(stored, key=self._merge_information_score)
        target_ref = str(target.get("uid") or target.get("id") or "")
        duplicate_refs = [
            str(item.get("uid") or item.get("id") or "")
            for item in stored
            if str(item.get("uid") or item.get("id") or "") != target_ref
        ]
        conflicts: Dict[str, List[object]] = {}
        merged: Dict[str, object] = {}
        titles = [str(item.get("title", "")).strip() for item in sources]
        titles = [item for item in titles if item]
        if titles:
            merged["title"] = max(titles, key=lambda item: (len(self._normalize(item)), len(item)))
        for field_name in ("time_slot", "duration_minutes", "priority", "note"):
            values: List[object] = []
            for item in sources:
                value = item.get(field_name)
                if value in (None, "", 0):
                    continue
                if value not in values:
                    values.append(value)
            if len(values) > 1:
                conflicts[field_name] = values
            elif values:
                merged[field_name] = values[0]
        changes = {
            key: value
            for key, value in merged.items()
            if target.get(key) != value
        }
        all_completed = bool(sources) and all(
            bool(item.get("done")) and item.get("status") == "completed"
            for item in sources
        )
        return {
            "target_ref": target_ref,
            "duplicate_refs": duplicate_refs,
            "date": str(target.get("date", "")),
            "changes": changes,
            "result": {
                **dict(target),
                **merged,
                "done": all_completed,
                "status": "completed" if all_completed else "pending",
            },
            "conflicts": conflicts,
            "source_count": len(sources),
        }

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
            # Resolve identity before checking eligibility.  Filtering out
            # completed plans first would turn a real target into not_found,
            # or silently select a similarly named pending plan instead.
            normalized = self._normalize(value)
            exact = [
                item for item in tasks
                if normalized and self._normalize(item.get("title", "")) == normalized
            ]
            matched = exact or self.find_similar_plans(
                value,
                date=date,
                pending_only=False,
            )
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
        if pending_only and (
            bool(matched[0].get("done"))
            or str(matched[0].get("status", "")) == "completed"
        ):
            return PlanResolution(
                "already_completed",
                task=dict(matched[0]),
                reason_code="plan_already_completed",
            )
        if pending_only and not self._is_pending(matched[0]):
            return PlanResolution(
                "unavailable", task=dict(matched[0]), reason_code="plan_unavailable"
            )
        return PlanResolution("resolved", task=dict(matched[0]))

    @staticmethod
    def _normalize(value: object) -> str:
        text = str(value or "").lower()
        # Command shells are only removable at the title boundary.  Removing
        # these words globally would corrupt legitimate titles such as
        # ``先验收后发布`` and create false duplicate matches.
        text = re.sub(r"^(?:请)?把", "", text)
        text = re.sub(r"^先", "", text)
        text = re.sub(
            r"^(?:再|又|还)?(?:加|添加|加入)(?:一条|一项|一个)?(?:计划|任务)?",
            "",
            text,
        )
        for term in (
            "我", "今天", "今晚", "下午", "上午", "早上", "计划", "任务",
            "已经", "刚才", "刚刚", "完成", "做完", "学完", "整理完", "的", "了",
        ):
            text = text.replace(term, "")
        return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")

    @classmethod
    def _canonical_task_title(cls, value: object) -> str:
        text = str(value or "")
        text = re.sub(r"半小时", "", text)
        text = re.sub(
            r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*(?:个)?\s*(?:分钟|小时半?)",
            "",
            text,
        )
        return cls._normalize(text)

    @staticmethod
    def _duration_value(title: object, explicit: object = None) -> Optional[int]:
        if explicit not in (None, ""):
            try:
                return int(explicit)
            except (TypeError, ValueError):
                pass
        text = str(title or "")
        if "半小时" in text:
            return 30
        hour = re.search(
            r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*(?:个)?\s*小时(半)?",
            text,
        )
        if hour:
            number = PlanService._chinese_number(hour.group(1))
            if number is not None:
                return int(number * 60 + (30 if hour.group(2) else 0))
        minute = re.search(
            r"(\d+|[零一二两三四五六七八九十]+)\s*(?:个)?\s*分钟", text
        )
        if minute:
            number = PlanService._chinese_number(minute.group(1))
            if number is not None:
                return int(number)
        return None

    @staticmethod
    def _chinese_number(value: str) -> Optional[float]:
        digits = {
            "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        if value in digits:
            return float(digits[value])
        if "十" in value:
            left, _, right = value.partition("十")
            return float((digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0))
        return None

    def _plan_owner(self):
        return (
            self.growth_manager
            if hasattr(self.growth_manager, "tasks")
            else getattr(self.growth_manager, "plan_store")
        )

    @classmethod
    def _plans_are_merge_candidates(
        cls,
        first: Mapping[str, object],
        second: Mapping[str, object],
    ) -> bool:
        left = cls._canonical_task_title(first.get("title", ""))
        right = cls._canonical_task_title(second.get("title", ""))
        if not left or not right:
            return False
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) >= 2 and shorter in longer:
            return True
        return SequenceMatcher(None, left, right).ratio() >= 0.82

    @classmethod
    def _merge_information_score(cls, task: Mapping[str, object]):
        filled = sum(
            task.get(field_name) not in (None, "", 0)
            for field_name in ("time_slot", "duration_minutes", "priority", "note")
        )
        title = str(task.get("title", ""))
        return filled, len(cls._normalize(title)), len(title)

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
