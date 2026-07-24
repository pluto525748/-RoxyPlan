from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Iterable, Optional, Set


REMINDER_TYPES = {
    "plan_pending",
    "no_action_log",
    "review_time",
    "progress_encourage",
    "idle_nudge",
}


class ProactiveManager:
    """Build lightweight reminders from growth data without changing that data."""

    def __init__(
        self,
        growth_manager,
        now_provider: Callable[[], datetime] = datetime.now,
        *,
        enabled: bool = True,
        cooldown_seconds: int = 600,
        evening_review_enabled: bool = True,
        idle_nudge_enabled: bool = True,
        idle_threshold_seconds: int = 30 * 60,
        recent_interaction_grace_seconds: int = 2 * 60,
    ) -> None:
        self.growth_manager = growth_manager
        self.now_provider = now_provider
        self.enabled = bool(enabled)
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.evening_review_enabled = bool(evening_review_enabled)
        self.idle_nudge_enabled = bool(idle_nudge_enabled)
        self.idle_threshold_seconds = max(1, int(idle_threshold_seconds))
        self.recent_interaction_grace_seconds = max(
            0, int(recent_interaction_grace_seconds)
        )

        self.paused = False
        self._last_any_reminder_at: Optional[datetime] = None
        self._last_type_reminder_at: Dict[str, datetime] = {}
        self._review_reminded_dates: Set[str] = set()
        self._last_growth_reminder_type = ""

    def configure(
        self,
        *,
        enabled: Optional[bool] = None,
        cooldown_seconds: Optional[int] = None,
        evening_review_enabled: Optional[bool] = None,
        idle_nudge_enabled: Optional[bool] = None,
        idle_threshold_seconds: Optional[int] = None,
    ) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if cooldown_seconds is not None:
            self.cooldown_seconds = max(1, int(cooldown_seconds))
        if evening_review_enabled is not None:
            self.evening_review_enabled = bool(evening_review_enabled)
        if idle_nudge_enabled is not None:
            self.idle_nudge_enabled = bool(idle_nudge_enabled)
        if idle_threshold_seconds is not None:
            self.idle_threshold_seconds = max(1, int(idle_threshold_seconds))

    def pause(self) -> None:
        self.paused = True
        print("[Proactive] paused", flush=True)

    def resume(self) -> None:
        self.paused = False
        print("[Proactive] resumed", flush=True)

    def check(
        self,
        *,
        idle_seconds: float = 0,
        seconds_since_interaction: Optional[float] = None,
        allowed_types: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, str]]:
        print("[Proactive] check", flush=True)
        if not self.enabled or self.paused:
            print("[Proactive] skipped: disabled", flush=True)
            return None

        now = self.now_provider()
        allowed = set(allowed_types) if allowed_types is not None else REMINDER_TYPES
        if seconds_since_interaction is not None and (
            seconds_since_interaction < self.recent_interaction_grace_seconds
        ):
            print("[Proactive] skipped: cooldown", flush=True)
            return None

        if self._is_global_cooldown(now):
            print("[Proactive] skipped: cooldown", flush=True)
            return None

        try:
            tasks = self.growth_manager.tasks()
            records = self.growth_manager.records_for_date()
        except Exception as error:
            print(f"[Proactive] skipped: growth data unavailable: {error}", flush=True)
            return None

        pending = [task for task in tasks if not task.get("done", False)]
        candidates = self._build_candidates(now, tasks, records, pending, idle_seconds)
        candidates = [item for item in candidates if item["type"] in allowed]
        if not candidates:
            return None

        for reminder in candidates:
            reminder_type = reminder["type"]
            if self._is_type_cooldown(reminder_type, now):
                continue
            self._mark_reminded(reminder_type, now)
            print(f"[Proactive] remind: {reminder_type}", flush=True)
            return reminder

        print("[Proactive] skipped: cooldown", flush=True)
        return None

    def task_completed_reminder(self) -> Optional[Dict[str, str]]:
        if not self.enabled or self.paused:
            print("[Proactive] skipped: disabled", flush=True)
            return None

        now = self.now_provider()
        if self._is_global_cooldown(now) or self._is_type_cooldown(
            "progress_encourage", now
        ):
            print("[Proactive] skipped: cooldown", flush=True)
            return None

        reminder = {
            "type": "progress_encourage",
            "text": "这个完成得不错，要不要顺手记录一下你刚才做了什么？",
        }
        self._mark_reminded("progress_encourage", now)
        print("[Proactive] remind: progress_encourage", flush=True)
        return reminder

    def _build_candidates(self, now, tasks, records, pending, idle_seconds):
        candidates = []
        today = now.date().isoformat()
        if (
            self.evening_review_enabled
            and (tasks or records)
            and self._is_review_time(now)
            and today not in self._review_reminded_dates
        ):
            candidates.append(
                {
                    "type": "review_time",
                    "text": "今天已经推进了一些事情，要不要做个简单复盘？",
                }
            )

        if (
            self.idle_nudge_enabled
            and pending
            and idle_seconds >= self.idle_threshold_seconds
        ):
            candidates.append(
                {
                    "type": "idle_nudge",
                    "text": "我还在这儿。今天的计划不用一次做完，先动一下就行。",
                }
            )

        plan_reminder = {
            "type": "plan_pending",
            "text": (
                f"今天还有 {len(pending)} 个计划没完成，"
                "要不要先挑一个最小的做？"
            ),
        }
        action_reminder = {
            "type": "no_action_log",
            "text": "今天还没有行动记录，要不要从一个很小的动作开始？",
        }

        if pending and tasks and not records:
            if self._last_growth_reminder_type == "plan_pending":
                candidates.extend((action_reminder, plan_reminder))
            else:
                candidates.extend((plan_reminder, action_reminder))
        elif pending:
            candidates.append(plan_reminder)
        elif tasks and not records:
            candidates.append(action_reminder)
        return candidates

    def _mark_reminded(self, reminder_type: str, now: datetime) -> None:
        self._last_any_reminder_at = now
        self._last_type_reminder_at[reminder_type] = now
        if reminder_type == "review_time":
            self._review_reminded_dates.add(now.date().isoformat())
        if reminder_type in {"plan_pending", "no_action_log"}:
            self._last_growth_reminder_type = reminder_type

    def _is_global_cooldown(self, now: datetime) -> bool:
        if self._last_any_reminder_at is None:
            return False
        return (now - self._last_any_reminder_at).total_seconds() < self.cooldown_seconds

    def _is_type_cooldown(self, reminder_type: str, now: datetime) -> bool:
        previous = self._last_type_reminder_at.get(reminder_type)
        if previous is None:
            return False
        return (now - previous).total_seconds() < self.cooldown_seconds

    @staticmethod
    def _is_review_time(now: datetime) -> bool:
        minutes = now.hour * 60 + now.minute
        return 20 * 60 + 30 <= minutes <= 23 * 60 + 30
