from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_FILE = PROJECT_ROOT / "data" / "today_plan.json"


class TodayPlanStore:
    """Small JSON store for today's tasks with automatic daily archiving."""

    def __init__(
        self,
        path: Path = DEFAULT_PLAN_FILE,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = Path(path)
        self.now_provider = now_provider
        self.data = self._load_or_create()
        print("[PLAN] loaded today_plan.json", flush=True)

    def tasks(self) -> List[Dict[str, object]]:
        self._ensure_today()
        return self.data["tasks"]

    def add_task(self, title: str) -> Dict[str, object]:
        self._ensure_today()
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be empty")

        tasks = self.data["tasks"]
        next_id = max((int(task.get("id", 0)) for task in tasks), default=0) + 1
        task: Dict[str, object] = {
            "id": next_id,
            "title": clean_title,
            "done": False,
            "created_at": self._now().isoformat(timespec="seconds"),
            "done_at": None,
        }
        tasks.append(task)
        self._save()
        print("[PLAN] add task", flush=True)
        return task

    def complete_by_id(self, task_id: int) -> Tuple[Optional[Dict[str, object]], bool]:
        self._ensure_today()
        for task in self.data["tasks"]:
            if int(task.get("id", 0)) == task_id:
                return task, self._complete_task(task)
        return None, False

    def complete_by_title(self, title: str) -> Tuple[Optional[Dict[str, object]], bool]:
        self._ensure_today()
        target = self._normalize_title(title)
        if not target:
            return None, False

        tasks = self.data["tasks"]
        exact = [task for task in tasks if self._normalize_title(str(task.get("title", ""))) == target]
        candidates = exact or [
            task
            for task in tasks
            if target in self._normalize_title(str(task.get("title", "")))
            or self._normalize_title(str(task.get("title", ""))) in target
        ]
        if not candidates:
            return None, False

        task = next((item for item in candidates if not item.get("done", False)), candidates[0])
        return task, self._complete_task(task)

    def delete_by_id(self, task_id: int) -> Optional[Dict[str, object]]:
        self._ensure_today()
        for index, task in enumerate(self.data["tasks"]):
            if int(task.get("id", 0)) == task_id:
                removed = self.data["tasks"].pop(index)
                self._save()
                print("[PLAN] delete task", flush=True)
                return removed
        return None

    def review(self) -> Dict[str, int]:
        self._ensure_today()
        tasks = self.data["tasks"]
        done = sum(1 for task in tasks if task.get("done", False))
        result = {
            "total": len(tasks),
            "done": done,
            "pending": len(tasks) - done,
        }
        print("[PLAN] review", flush=True)
        return result

    def _complete_task(self, task: Dict[str, object]) -> bool:
        if task.get("done", False):
            return False
        task["done"] = True
        task["done_at"] = self._now().isoformat(timespec="seconds")
        self._save()
        print("[PLAN] complete task", flush=True)
        return True

    def _load_or_create(self) -> Dict[str, object]:
        if not self.path.exists():
            data = self._default_data()
            self.data = data
            self._save()
            return data

        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"[PLAN] failed to read today_plan.json: {error}", flush=True)
            loaded = self._default_data()

        if not isinstance(loaded, dict):
            loaded = self._default_data()

        data = self._normalize_data(loaded)
        self.data = data
        self._ensure_today()
        return self.data

    def _ensure_today(self) -> None:
        today = self._now().date().isoformat()
        if self.data.get("date") == today:
            return

        old_date = str(self.data.get("date", "")).strip()
        old_tasks = self.data.get("tasks", [])
        history = self.data.get("history", [])
        if not isinstance(history, list):
            history = []

        if old_date:
            archive = {
                "date": old_date,
                "tasks": old_tasks if isinstance(old_tasks, list) else [],
            }
            history = [item for item in history if not isinstance(item, dict) or item.get("date") != old_date]
            history.append(archive)

        self.data = {
            "version": 1,
            "date": today,
            "tasks": [],
            "history": history,
        }
        self._save()

    def _normalize_data(self, data: Dict[str, object]) -> Dict[str, object]:
        tasks = data.get("tasks", [])
        history = data.get("history", [])
        return {
            "version": 1,
            "date": str(data.get("date", "")),
            "tasks": tasks if isinstance(tasks, list) else [],
            "history": history if isinstance(history, list) else [],
        }

    def _default_data(self) -> Dict[str, object]:
        return {
            "version": 1,
            "date": self._now().date().isoformat(),
            "tasks": [],
            "history": [],
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _now(self) -> datetime:
        return self.now_provider()

    @staticmethod
    def _normalize_title(title: str) -> str:
        return title.strip().strip("。.!！?？：:").casefold()
