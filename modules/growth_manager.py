from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


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
    ) -> None:
        self.private_dir = Path(private_dir)
        self.now_provider = now_provider
        self.plan_file = self.private_dir / "today_plan.json"
        self.action_file = self.private_dir / "action_log.json"
        self.growth_file = self.private_dir / "growth_log.json"

        use_legacy = self.private_dir.resolve() == DEFAULT_PRIVATE_DIR.resolve()
        self.plan_data = self._initialize_store(
            self.plan_file,
            self._default_plan_data,
            self._convert_plan_data,
            LEGACY_PLAN_FILE if use_legacy else None,
        )
        print("[Growth] loaded today plan", flush=True)
        self.action_data = self._initialize_store(
            self.action_file,
            self._default_action_data,
            self._convert_action_data,
            LEGACY_ACTION_FILE if use_legacy else None,
        )
        self.growth_data = self._initialize_store(
            self.growth_file,
            self._default_growth_data,
            self._convert_growth_data,
            LEGACY_GROWTH_FILE if use_legacy else None,
        )

        # Compatibility aliases for the V0.9 prototype interfaces.
        self.plan_store = self
        self.action_store = self
        self.growth_store = self

    def tasks(self, date: Optional[str] = None) -> List[Dict[str, object]]:
        day = self._plan_day(date)
        return [dict(task) for task in day["tasks"] if isinstance(task, dict)]

    def add_task(self, title: str, date: Optional[str] = None) -> Dict[str, object]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be empty")

        day = self._plan_day(date)
        task_list = day["tasks"]
        next_id = max((int(task.get("id", 0)) for task in task_list), default=0) + 1
        task: Dict[str, object] = {
            "id": next_id,
            "title": clean_title,
            "done": False,
            "created_at": self._now().isoformat(timespec="seconds"),
            "done_at": None,
        }
        task_list.append(task)
        self._safe_save(self.plan_file, self.plan_data)
        print("[Growth] add task", flush=True)
        return dict(task)

    def complete_by_id(
        self, task_id: int, date: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        for task in self._plan_day(date)["tasks"]:
            if int(task.get("id", 0)) != task_id:
                continue
            if task.get("done", False):
                return dict(task), False
            task["done"] = True
            task["done_at"] = self._now().isoformat(timespec="seconds")
            self._safe_save(self.plan_file, self.plan_data)
            print("[Growth] complete task", flush=True)
            return dict(task), True
        return None, False

    def complete_by_title(
        self, title: str, date: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        target = self._normalize_title(title)
        if not target:
            return None, False
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
        task_list = self._plan_day(date)["tasks"]
        for index, task in enumerate(task_list):
            if int(task.get("id", 0)) == task_id:
                removed = task_list.pop(index)
                self._safe_save(self.plan_file, self.plan_data)
                print("[Growth] delete task", flush=True)
                return dict(removed)
        return None

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
            "date": target_date,
            "time": self._now().strftime("%H:%M:%S"),
            "content": clean_content,
            "source": source.strip() or "manual",
        }
        self._action_day(target_date)["records"].append(record)
        self._safe_save(self.action_file, self.action_data)
        print("[Growth] add action", flush=True)
        return dict(record)

    def records_for_date(self, date: Optional[str] = None) -> List[Dict[str, str]]:
        day = self._action_day(date)
        return [dict(record) for record in day["records"] if isinstance(record, dict)]

    def generate_review(self, date: Optional[str] = None) -> Dict[str, object]:
        target_date = date or self._today()
        task_list = self.tasks(target_date)
        records = self.records_for_date(target_date)
        completed = [str(task.get("title", "")) for task in task_list if task.get("done", False)]
        pending = [str(task.get("title", "")) for task in task_list if not task.get("done", False)]
        actions = [str(record.get("content", "")) for record in records if record.get("content")]

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
        text = (
            f"今天你计划了 {len(task_list)} 件事，完成了 {len(completed)} 件，"
            f"还有 {len(pending)} 件未完成。\n"
            f"已完成：{completed_text}\n"
            f"未完成：{pending_text}\n"
            f"行动记录 {len(actions)} 条：{action_text}\n"
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
            "encouragement": encouragement,
            "text": text,
        }
        print("[Growth] generate review", flush=True)
        return review

    def save_today_review(self, date: Optional[str] = None) -> Dict[str, object]:
        review = self.generate_review(date)
        target_date = str(review["date"])
        entry = {
            "date": target_date,
            "saved_at": self._now().isoformat(timespec="seconds"),
            "review": review,
        }
        self.growth_data["entries"][target_date] = entry
        self._safe_save(self.growth_file, self.growth_data)
        print("[Growth] save review", flush=True)
        return dict(entry)

    def entries(self) -> List[Dict[str, object]]:
        entries = self.growth_data.get("entries", {})
        if not isinstance(entries, dict):
            return []
        return [dict(entries[key]) for key in sorted(entries) if isinstance(entries[key], dict)]

    def recent_entries(self, limit: int = 7) -> List[Dict[str, object]]:
        if limit <= 0:
            return []
        return list(reversed(self.entries()))[:limit]

    def _plan_day(self, date: Optional[str] = None) -> Dict[str, object]:
        target_date = date or self._today()
        days = self.plan_data.setdefault("days", {})
        day = days.setdefault(target_date, {"tasks": []})
        if not isinstance(day, dict):
            day = {"tasks": []}
            days[target_date] = day
        if not isinstance(day.get("tasks"), list):
            day["tasks"] = []
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

    def _initialize_store(self, path, default_factory, converter, legacy_path=None):
        if path.exists():
            return converter(self._safe_load(path, default_factory()))

        data = default_factory()
        if legacy_path is not None and legacy_path.exists():
            legacy = self._safe_load(legacy_path, {})
            data = converter(legacy)
            print(f"[Growth] migrated legacy {legacy_path.name}", flush=True)
        self._safe_save(path, data)
        return data

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

    @staticmethod
    def _safe_load(path: Path, fallback: Dict[str, object]) -> Dict[str, object]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else fallback
        except (OSError, json.JSONDecodeError) as error:
            print(f"[Growth] read failed {path.name}: {error}", flush=True)
            return fallback

    @staticmethod
    def _safe_save(path: Path, data: Dict[str, object]) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
            return True
        except OSError as error:
            print(f"[Growth] write failed {path.name}: {error}", flush=True)
            return False

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _now(self) -> datetime:
        return self.now_provider()

    @staticmethod
    def _normalize_title(title: str) -> str:
        return title.strip().strip("。.!！?？：:").casefold()
