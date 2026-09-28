from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from modules.repositories.growth_repository import GrowthRepository
from modules.repositories.local_json_growth_repository import LocalJsonGrowthRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIVATE_DIR = PROJECT_ROOT / "data" / "private"
LEGACY_PLAN_FILE = PROJECT_ROOT / "data" / "today_plan.json"
LEGACY_ACTION_FILE = PROJECT_ROOT / "data" / "action_log.json"
LEGACY_GROWTH_FILE = PROJECT_ROOT / "data" / "growth_log.json"


class GrowthManager:
    """Safe local JSON manager for the V0.9 daily growth loop."""

    def __init__(
        self,
        private_dir: Path = DEFAULT_PRIVATE_DIR,
        now_provider: Callable[[], datetime] = datetime.now,
        repository: Optional[GrowthRepository] = None,
    ) -> None:
        self.private_dir = Path(private_dir)
        self.now_provider = now_provider
        use_legacy = self.private_dir.resolve() == DEFAULT_PRIVATE_DIR.resolve()
        self.repository = repository or LocalJsonGrowthRepository(
            self.private_dir,
            legacy_plan_file=LEGACY_PLAN_FILE if use_legacy else None,
            legacy_action_file=LEGACY_ACTION_FILE if use_legacy else None,
            legacy_growth_file=LEGACY_GROWTH_FILE if use_legacy else None,
        )
        self.plan_file = getattr(
            self.repository, "plan_file", self.private_dir / "today_plan.json"
        )
        self.action_file = getattr(
            self.repository, "action_file", self.private_dir / "action_log.json"
        )
        self.growth_file = getattr(
            self.repository, "growth_file", self.private_dir / "growth_log.json"
        )

        self.plan_data = self._initialize_store(
            self.repository.plan_exists,
            self.repository.load_plan,
            self.repository.save_plan,
            self._default_plan_data,
            self._convert_plan_data,
        )
        print("[Growth] loaded today plan", flush=True)
        self.action_data = self._initialize_store(
            self.repository.action_exists,
            self.repository.load_actions,
            self.repository.save_actions,
            self._default_action_data,
            self._convert_action_data,
        )
        self.growth_data = self._initialize_store(
            self.repository.growth_exists,
            self.repository.load_growth,
            self.repository.save_growth,
            self._default_growth_data,
            self._convert_growth_data,
        )

        # Compatibility aliases for the V0.9 prototype interfaces.
        self.plan_store = self
        self.action_store = self
        self.growth_store = self

    def tasks(
        self,
        date: Optional[str] = None,
        *,
        include_cancelled: bool = False,
    ) -> List[Dict[str, object]]:
        self._refresh_plan()
        day = self._plan_day(date)
        items = [
            self._normalize_task(task, date or self._today())
            for task in day["tasks"]
            if isinstance(task, dict)
        ]
        if not include_cancelled:
            items = [item for item in items if item.get("status") != "cancelled"]
        return [dict(task) for task in items]

    def add_task(
        self,
        title: str,
        date: Optional[str] = None,
        *,
        time_slot: str = "",
        duration_minutes: Optional[int] = None,
        priority: str = "",
        note: str = "",
    ) -> Dict[str, object]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be empty")

        with self.repository.transaction("plan"):
            self._refresh_plan()
            target_date = date or self._today()
            day = self._plan_day(target_date)
            task_list = day["tasks"]
            next_id = max((int(task.get("id", 0)) for task in task_list), default=0) + 1
            now_text = self._now().isoformat(timespec="seconds")
            task: Dict[str, object] = {
                "id": next_id,
                "uid": f"task_{uuid4().hex}",
                "title": clean_title,
                "done": False,
                "status": "pending",
                "date": target_date,
                "time_slot": str(time_slot).strip(),
                "duration_minutes": self._normalize_duration(duration_minutes),
                "priority": self._normalize_priority(priority),
                "note": str(note).strip(),
                "created_at": now_text,
                "updated_at": now_text,
                "done_at": None,
                "cancelled_at": None,
            }
            task_list.append(task)
            if not self.repository.save_plan(self.plan_data):
                self._refresh_plan()
                raise OSError("Failed to save today plan")
        print("[Growth] add task", flush=True)
        return dict(task)

    def complete_by_id(
        self, task_id: int, date: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        with self.repository.transaction("plan"):
            self._refresh_plan()
            for task in self._plan_day(date)["tasks"]:
                if int(task.get("id", 0)) != task_id:
                    continue
                self._normalize_task(task, date or self._today())
                if task.get("done", False):
                    return dict(task), False
                if task.get("status") == "cancelled":
                    return dict(task), False
                task["done"] = True
                task["status"] = "completed"
                task["done_at"] = self._now().isoformat(timespec="seconds")
                task["updated_at"] = task["done_at"]
                if not self.repository.save_plan(self.plan_data):
                    self._refresh_plan()
                    raise OSError("Failed to save completed plan")
                print("[Growth] complete task", flush=True)
                return dict(task), True
        return None, False

    def complete_by_title(
        self, title: str, date: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        target = self._normalize_title(title)
        if not target:
            return None, False
        self._refresh_plan()
        task_list = self._plan_day(date)["tasks"]
        exact = [task for task in task_list if self._normalize_title(str(task.get("title", ""))) == target]
        candidates = exact or [
            task
            for task in task_list
            if target in self._normalize_title(str(task.get("title", "")))
            or self._normalize_title(str(task.get("title", ""))) in target
        ]
        if not candidates:
            return None, False
        task = next((item for item in candidates if not item.get("done", False)), candidates[0])
        return self.complete_by_id(int(task.get("id", 0)), date)

    def delete_by_id(self, task_id: int, date: Optional[str] = None) -> Optional[Dict[str, object]]:
        with self.repository.transaction("plan"):
            self._refresh_plan()
            task_list = self._plan_day(date)["tasks"]
            for index, task in enumerate(task_list):
                if int(task.get("id", 0)) == task_id:
                    removed = task_list.pop(index)
                    if not self.repository.save_plan(self.plan_data):
                        self._refresh_plan()
                        raise OSError("Failed to save deleted plan")
                    print("[Growth] delete task", flush=True)
                    return dict(removed)
        return None

    def update_task(
        self,
        task_id: int,
        changes: Dict[str, object],
        date: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, object]], bool, str]:
        allowed = {"title", "date", "time_slot", "duration_minutes", "priority", "note"}
        requested = {key: value for key, value in dict(changes).items() if key in allowed}
        if not requested:
            return None, False, "no_changes"
        source_date = date or self._today()
        with self.repository.transaction("plan"):
            self._refresh_plan()
            source_day = self._plan_day(source_date)
            task = next(
                (item for item in source_day["tasks"] if int(item.get("id", 0)) == int(task_id)),
                None,
            )
            if task is None:
                return None, False, "not_found"
            self._normalize_task(task, source_date)
            if task.get("status") == "completed" and set(requested) - {"note", "priority"}:
                return dict(task), False, "completed_requires_reopen"
            if task.get("status") == "cancelled":
                return dict(task), False, "cancelled_requires_reopen"

            old_date = str(task.get("date") or source_date)
            target_date = str(requested.get("date") or old_date).strip() or old_date
            if "title" in requested:
                title = str(requested["title"]).strip()
                if not title:
                    return dict(task), False, "invalid_title"
                task["title"] = title
            if "time_slot" in requested:
                task["time_slot"] = str(requested["time_slot"]).strip()
            if "duration_minutes" in requested:
                duration = self._normalize_duration(requested["duration_minutes"])
                if duration is None:
                    return dict(task), False, "invalid_duration"
                task["duration_minutes"] = duration
            if "priority" in requested:
                task["priority"] = self._normalize_priority(requested["priority"])
            if "note" in requested:
                task["note"] = str(requested["note"]).strip()
            task["date"] = target_date
            task["updated_at"] = self._now().isoformat(timespec="seconds")

            if target_date != source_date:
                source_day["tasks"].remove(task)
                target_day = self._plan_day(target_date)
                task["id"] = max(
                    (int(item.get("id", 0)) for item in target_day["tasks"] if isinstance(item, dict)),
                    default=0,
                ) + 1
                target_day["tasks"].append(task)
            if not self.repository.save_plan(self.plan_data):
                self._refresh_plan()
                raise OSError("Failed to update plan")
        print("[Growth] update task", flush=True)
        return dict(task), True, "updated"

    def merge_tasks(
        self,
        target_id: int,
        duplicate_ids: List[int],
        changes: Dict[str, object],
        date: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, object]], List[Dict[str, object]], bool, str]:
        """Merge verified same-day tasks and persist the result once.

        The caller owns semantic comparison and the confirmation preview.  This
        method only applies the already-confirmed merge atomically at the plan
        repository boundary.  A mixed completion state remains pending; only a
        group whose every source task is complete stays complete.
        """
        allowed = {"title", "time_slot", "duration_minutes", "priority", "note"}
        requested = {key: value for key, value in dict(changes).items() if key in allowed}
        duplicate_id_set = {
            int(value) for value in duplicate_ids if int(value) != int(target_id)
        }
        target_date = date or self._today()
        with self.repository.transaction("plan"):
            self._refresh_plan()
            task_list = self._plan_day(target_date)["tasks"]
            target = next(
                (item for item in task_list if int(item.get("id", 0)) == int(target_id)),
                None,
            )
            duplicates = [
                item
                for item in task_list
                if int(item.get("id", 0)) in duplicate_id_set
            ]
            if target is None or len(duplicates) != len(duplicate_id_set):
                return None, [], False, "not_found"
            sources = [target, *duplicates]
            for item in sources:
                self._normalize_task(item, target_date)
            if any(item.get("status") == "cancelled" for item in sources):
                return dict(target), [], False, "cancelled_requires_reopen"

            if "title" in requested:
                title = str(requested["title"]).strip()
                if not title:
                    return dict(target), [], False, "invalid_title"
                target["title"] = title
            if "time_slot" in requested:
                target["time_slot"] = str(requested["time_slot"]).strip()
            if "duration_minutes" in requested:
                duration = self._normalize_duration(requested["duration_minutes"])
                if duration is None:
                    return dict(target), [], False, "invalid_duration"
                target["duration_minutes"] = duration
            if "priority" in requested:
                target["priority"] = self._normalize_priority(requested["priority"])
            if "note" in requested:
                target["note"] = str(requested["note"]).strip()

            all_completed = bool(sources) and all(
                bool(item.get("done")) and item.get("status") == "completed"
                for item in sources
            )
            target["done"] = all_completed
            target["status"] = "completed" if all_completed else "pending"
            target["done_at"] = (
                max(
                    (str(item.get("done_at") or "") for item in sources),
                    default="",
                )
                or None
                if all_completed
                else None
            )
            target["cancelled_at"] = None
            target["updated_at"] = self._now().isoformat(timespec="seconds")
            removed = [dict(item) for item in duplicates]
            if duplicates:
                task_list[:] = [
                    item
                    for item in task_list
                    if int(item.get("id", 0)) not in duplicate_id_set
                ]
            if not self.repository.save_plan(self.plan_data):
                self._refresh_plan()
                raise OSError("Failed to save merged plans")
        print("[Growth] merge tasks", flush=True)
        return dict(target), removed, True, "merged" if removed else "already_merged"

    def reopen_task(
        self,
        task_id: int,
        date: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        with self.repository.transaction("plan"):
            self._refresh_plan()
            target_date = date or self._today()
            task = next(
                (item for item in self._plan_day(target_date)["tasks"] if int(item.get("id", 0)) == int(task_id)),
                None,
            )
            if task is None:
                return None, False
            self._normalize_task(task, target_date)
            if task.get("status") == "pending" and not task.get("done"):
                return dict(task), False
            task["done"] = False
            task["status"] = "pending"
            task["done_at"] = None
            task["cancelled_at"] = None
            task["updated_at"] = self._now().isoformat(timespec="seconds")
            if not self.repository.save_plan(self.plan_data):
                self._refresh_plan()
                raise OSError("Failed to reopen plan")
        print("[Growth] reopen task", flush=True)
        return dict(task), True

    def cancel_task(
        self,
        task_id: int,
        date: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        with self.repository.transaction("plan"):
            self._refresh_plan()
            target_date = date or self._today()
            task = next(
                (item for item in self._plan_day(target_date)["tasks"] if int(item.get("id", 0)) == int(task_id)),
                None,
            )
            if task is None:
                return None, False
            self._normalize_task(task, target_date)
            if task.get("status") == "cancelled":
                return dict(task), False
            now_text = self._now().isoformat(timespec="seconds")
            task["done"] = False
            task["status"] = "cancelled"
            task["done_at"] = None
            task["cancelled_at"] = now_text
            task["updated_at"] = now_text
            if not self.repository.save_plan(self.plan_data):
                self._refresh_plan()
                raise OSError("Failed to cancel plan")
        print("[Growth] cancel task", flush=True)
        return dict(task), True

    def similar_tasks(
        self,
        title: str,
        date: Optional[str] = None,
        threshold: float = 0.82,
    ) -> List[Dict[str, object]]:
        target = self._semantic_task_title(title)
        if not target:
            return []
        scored = []
        for task in self.tasks(date):
            candidate = self._semantic_task_title(str(task.get("title", "")))
            score = SequenceMatcher(None, target, candidate).ratio()
            if target in candidate or candidate in target:
                score = max(score, 0.96)
            if score >= threshold:
                scored.append((score, task))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{**dict(task), "similarity": round(score, 4)} for score, task in scored]

    def review(self, date: Optional[str] = None) -> Dict[str, int]:
        task_list = self.tasks(date)
        done = sum(1 for task in task_list if task.get("done", False))
        return {"total": len(task_list), "done": done, "pending": len(task_list) - done}

    def add_record(
        self, content: str, source: str = "manual", date: Optional[str] = None
    ) -> Dict[str, str]:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Action content cannot be empty")
        target_date = date or self._today()
        record = {
            "uid": f"action_{uuid4().hex}",
            "date": target_date,
            "time": self._now().strftime("%H:%M:%S"),
            "content": clean_content,
            "source": source.strip() or "manual",
        }
        with self.repository.transaction("actions"):
            self._refresh_actions()
            records = self._action_day(target_date)["records"]
            duplicate = next(
                (
                    item
                    for item in records
                    if isinstance(item, dict)
                    and self._normalize_title(str(item.get("content", "")))
                    == self._normalize_title(clean_content)
                ),
                None,
            )
            if duplicate is not None:
                existing = dict(duplicate)
                existing["duplicate"] = True
                return existing
            records.append(record)
            if not self.repository.save_actions(self.action_data):
                self._refresh_actions()
                raise OSError("Failed to save action log")
        print("[Growth] add action", flush=True)
        return dict(record)

    def records_for_date(self, date: Optional[str] = None) -> List[Dict[str, str]]:
        self._refresh_actions()
        day = self._action_day(date)
        return [dict(record) for record in day["records"] if isinstance(record, dict)]

    def generate_review(self, date: Optional[str] = None) -> Dict[str, object]:
        target_date = self._validated_date(date or self._today())
        task_list = self.tasks(target_date)
        records = self.records_for_date(target_date)
        return self._build_review(target_date, task_list, records)

    def _build_review(
        self,
        target_date: str,
        task_list: List[Dict[str, object]],
        records: List[Dict[str, object]],
    ) -> Dict[str, object]:
        completed = [str(task.get("title", "")) for task in task_list if task.get("done", False)]
        pending = [str(task.get("title", "")) for task in task_list if not task.get("done", False)]
        actions = [str(record.get("content", "")) for record in records if record.get("content")]
        plan_snapshot = [
            {
                "stable_id": str(task.get("uid") or task.get("id") or ""),
                "title": str(task.get("title", "")).strip(),
                "done": bool(task.get("done", False)),
                "status": str(task.get("status", "pending") or "pending"),
                "date": str(task.get("date") or target_date),
                "time_slot": str(task.get("time_slot", "")).strip(),
                "duration_minutes": task.get("duration_minutes"),
            }
            for task in task_list
            if isinstance(task, dict)
        ]
        action_records = [
            dict(record) for record in records if isinstance(record, dict)
        ]

        if completed and pending:
            encouragement = "已经推进的部分很扎实，剩下的可以明天继续拆小一点。"
        elif completed:
            encouragement = "今天的计划都落到了行动里，可以安心收尾了。"
        elif actions:
            encouragement = "计划之外的行动也算进展，今天留下的每一步都是真实的。"
        elif task_list:
            encouragement = "今天可能有些累，先保留计划，明天再从最小的一步开始。"
        else:
            encouragement = "今天还没有记录也没关系，愿意回来看一眼就是新的起点。"

        completed_text = "、".join(completed) or "暂无"
        pending_text = "、".join(pending) or "暂无"
        action_text = "、".join(actions) or "暂无"
        date_label = "今天" if target_date == self._today() else target_date
        text = (
            f"{date_label}你计划了 {len(task_list)} 件事，完成了 {len(completed)} 件，"
            f"还有 {len(pending)} 件未完成。\n"
            f"已完成：{completed_text}\n"
            f"未完成：{pending_text}\n"
            f"计划外行动 {len(actions)} 条：{action_text}\n"
            f"{encouragement}"
        )
        review = {
            "date": target_date,
            "total": len(task_list),
            "done": len(completed),
            "pending": len(pending),
            "action_count": len(actions),
            "completed_tasks": completed,
            "pending_tasks": pending,
            "actions": actions,
            "plan_snapshot": plan_snapshot,
            "action_records": action_records,
            "encouragement": encouragement,
            "text": text,
        }
        print("[Growth] generate review", flush=True)
        return review

    def save_today_review(self, date: Optional[str] = None) -> Dict[str, object]:
        review = self.generate_review(date)
        target_date = str(review["date"])
        with self.repository.transaction("growth"):
            self._refresh_growth()
            existing = self.growth_data.setdefault("entries", {}).get(target_date)
            existing = existing if isinstance(existing, dict) else {}
            now_text = self._now().isoformat(timespec="seconds")
            entry = {
                "uid": str(existing.get("uid") or f"review_{target_date}"),
                "date": target_date,
                "saved_at": str(existing.get("saved_at") or now_text),
                "created_at": str(existing.get("created_at") or existing.get("saved_at") or now_text),
                "updated_at": now_text,
                "revision": int(existing.get("revision", 0) or 0) + 1,
                "review": review,
            }
            self.growth_data["entries"][target_date] = entry
            if not self.repository.save_growth(self.growth_data):
                self._refresh_growth()
                raise OSError("Failed to save growth review")
        print("[Growth] save review", flush=True)
        return deepcopy(entry)

    def ensure_review_for_date(self, date: str) -> Dict[str, object]:
        """Create one verified local review if and only if that day needs one.

        The growth-store transaction makes the final existence check and write
        atomic.  Existing entries are returned byte-for-byte-equivalent and are
        never revised by this automatic path.
        """
        target_date = self._validated_date(date)
        task_list = self.tasks(target_date)
        records = self.records_for_date(target_date)
        with self.repository.transaction("growth"):
            self._refresh_growth()
            entries = self.growth_data.setdefault("entries", {})
            existing = entries.get(target_date)
            if isinstance(existing, dict):
                return {
                    "status": "existing",
                    "created": False,
                    "entry": deepcopy(existing),
                }
            if not task_list and not records:
                return {"status": "empty", "created": False, "entry": None}
            review = self._build_review(target_date, task_list, records)
            now_text = self._now().isoformat(timespec="seconds")
            entry = {
                "uid": f"review_{target_date}",
                "date": target_date,
                "saved_at": now_text,
                "created_at": now_text,
                "updated_at": now_text,
                "revision": 1,
                "review": review,
            }
            entries[target_date] = entry
            if not self.repository.save_growth(self.growth_data):
                self._refresh_growth()
                raise OSError("Failed to save growth review")
        return {"status": "created", "created": True, "entry": deepcopy(entry)}

    def entries(self) -> List[Dict[str, object]]:
        self._refresh_growth()
        entries = self.growth_data.get("entries", {})
        if not isinstance(entries, dict):
            return []
        return [dict(entries[key]) for key in sorted(entries) if isinstance(entries[key], dict)]

    def recent_entries(self, limit: int = 7) -> List[Dict[str, object]]:
        if limit <= 0:
            return []
        return list(reversed(self.entries()))[:limit]

    def current_month(self) -> str:
        return self._now().strftime("%Y-%m")

    def entries_for_month(self, month: Optional[str] = None) -> List[Dict[str, object]]:
        target_month = self._validated_month(month or self.current_month())
        prefix = target_month + "-"
        return [
            deepcopy(entry)
            for entry in self.entries()
            if str(entry.get("date", "")).startswith(prefix)
        ]

    def monthly_statistics(self, month: Optional[str] = None) -> Dict[str, object]:
        target_month = self._validated_month(month or self.current_month())
        entries = self.entries_for_month(target_month)
        total = 0
        done = 0
        pending = 0
        action_count = 0
        for entry in entries:
            review = entry.get("review", {})
            review = review if isinstance(review, dict) else {}
            total += int(review.get("total", 0) or 0)
            done += int(review.get("done", 0) or 0)
            pending += int(review.get("pending", 0) or 0)
            action_count += int(
                review.get("action_count", len(review.get("actions", []))) or 0
            )
        return {
            "month": target_month,
            "logged_days": len(entries),
            "total_plans": total,
            "completed_plans": done,
            "pending_plans": pending,
            "action_count": action_count,
            "completion_rate": round(done / total, 4) if total else 0.0,
        }

    def _plan_day(self, date: Optional[str] = None) -> Dict[str, object]:
        target_date = date or self._today()
        days = self.plan_data.setdefault("days", {})
        day = days.setdefault(target_date, {"tasks": []})
        if not isinstance(day, dict):
            day = {"tasks": []}
            days[target_date] = day
        if not isinstance(day.get("tasks"), list):
            day["tasks"] = []
        for task in day["tasks"]:
            if isinstance(task, dict):
                self._normalize_task(task, target_date)
        return day

    def _action_day(self, date: Optional[str] = None) -> Dict[str, object]:
        target_date = date or self._today()
        days = self.action_data.setdefault("days", {})
        day = days.setdefault(target_date, {"records": []})
        if not isinstance(day, dict):
            day = {"records": []}
            days[target_date] = day
        if not isinstance(day.get("records"), list):
            day["records"] = []
        return day

    def _initialize_store(self, exists, loader, saver, default_factory, converter):
        existed = bool(exists())
        fallback = default_factory()
        data = converter(loader(fallback))
        if not existed:
            saver(data)
        return data

    def _refresh_plan(self) -> None:
        self.plan_data = self._convert_plan_data(
            self.repository.load_plan(self._default_plan_data())
        )

    def _refresh_actions(self) -> None:
        self.action_data = self._convert_action_data(
            self.repository.load_actions(self._default_action_data())
        )

    def _refresh_growth(self) -> None:
        self.growth_data = self._convert_growth_data(
            self.repository.load_growth(self._default_growth_data())
        )

    @staticmethod
    def _default_plan_data() -> Dict[str, object]:
        return {"version": 1, "days": {}}

    @staticmethod
    def _default_action_data() -> Dict[str, object]:
        return {"version": 1, "days": {}}

    @staticmethod
    def _default_growth_data() -> Dict[str, object]:
        return {"version": 1, "entries": {}}

    def _convert_plan_data(self, data: object) -> Dict[str, object]:
        if not isinstance(data, dict):
            return self._default_plan_data()
        days = data.get("days")
        if isinstance(days, dict):
            return {"version": 1, "days": days}

        converted = self._default_plan_data()
        current_date = str(data.get("date", "")).strip()
        tasks = data.get("tasks", [])
        if current_date and isinstance(tasks, list):
            converted["days"][current_date] = {"tasks": tasks}
        history = data.get("history", [])
        if isinstance(history, list):
            for item in history:
                if isinstance(item, dict) and item.get("date") and isinstance(item.get("tasks"), list):
                    converted["days"][str(item["date"])] = {"tasks": item["tasks"]}
        return converted

    def _convert_action_data(self, data: object) -> Dict[str, object]:
        if not isinstance(data, dict):
            return self._default_action_data()
        days = data.get("days")
        if isinstance(days, dict):
            return {"version": 1, "days": days}

        converted = self._default_action_data()
        records = data.get("records", [])
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict) or not record.get("date"):
                    continue
                date = str(record["date"])
                converted["days"].setdefault(date, {"records": []})["records"].append(record)
        return converted

    def _convert_growth_data(self, data: object) -> Dict[str, object]:
        if not isinstance(data, dict):
            return self._default_growth_data()
        entries = data.get("entries", {})
        if isinstance(entries, dict):
            return {"version": 1, "entries": entries}
        converted = self._default_growth_data()
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("date"):
                    converted["entries"][str(entry["date"])] = entry
        return converted

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _now(self) -> datetime:
        return self.now_provider()

    @staticmethod
    def _validated_date(value: object) -> str:
        text = str(value or "").strip()
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("date must use YYYY-MM-DD") from error
        if parsed.strftime("%Y-%m-%d") != text:
            raise ValueError("date must use YYYY-MM-DD")
        return text

    @staticmethod
    def _validated_month(value: object) -> str:
        text = str(value or "").strip()
        try:
            parsed = datetime.strptime(text, "%Y-%m")
        except ValueError as error:
            raise ValueError("month must use YYYY-MM") from error
        if parsed.strftime("%Y-%m") != text:
            raise ValueError("month must use YYYY-MM")
        return text

    def _normalize_task(
        self,
        task: Dict[str, object],
        date: str,
    ) -> Dict[str, object]:
        created_at = str(task.get("created_at") or self._now().isoformat(timespec="seconds"))
        done = bool(task.get("done", False))
        status = str(task.get("status", ""))
        if status not in {"pending", "completed", "cancelled"}:
            status = "completed" if done else "pending"
        if status == "completed":
            done = True
        elif status == "cancelled":
            done = False
        task.setdefault("uid", f"task_{uuid4().hex}")
        task["done"] = done
        task["status"] = status
        task["date"] = str(task.get("date") or date)
        task["time_slot"] = str(task.get("time_slot", ""))
        task["duration_minutes"] = self._normalize_duration(
            task.get("duration_minutes")
        )
        task["priority"] = self._normalize_priority(task.get("priority", ""))
        task["note"] = str(task.get("note", ""))
        task["created_at"] = created_at
        task["updated_at"] = str(task.get("updated_at") or created_at)
        task.setdefault("done_at", None)
        task.setdefault("cancelled_at", None)
        return task

    @staticmethod
    def _normalize_duration(value: object) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            return None
        return minutes if 1 <= minutes <= 1440 else None

    @staticmethod
    def _normalize_priority(value: object) -> str:
        text = str(value or "").strip().lower()
        mapping = {"高": "high", "中": "medium", "低": "low"}
        text = mapping.get(text, text)
        return text if text in {"", "low", "medium", "high"} else ""

    @classmethod
    def _semantic_task_title(cls, title: str) -> str:
        text = cls._normalize_title(title)
        text = re.sub(r"\d+(?:\.\d+)?\s*(?:个)?\s*(?:分钟|小时)", "", text)
        text = re.sub(r"今天|上午|下午|晚上|今晚|明天|计划|任务", "", text)
        return re.sub(r"\s+", "", text)

    @staticmethod
    def _normalize_title(title: str) -> str:
        return title.strip().strip("。.!！?？：:").casefold()
