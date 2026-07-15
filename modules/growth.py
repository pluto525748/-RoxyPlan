from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from modules.today_plan import TodayPlanStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTION_LOG_FILE = PROJECT_ROOT / "data" / "action_log.json"
DEFAULT_GROWTH_LOG_FILE = PROJECT_ROOT / "data" / "growth_log.json"


class _JsonStore:
    def __init__(self, path: Path, now_provider: Callable[[], datetime]) -> None:
        self.path = Path(path)
        self.now_provider = now_provider
        self.data = self._load_or_create()

    def _load_or_create(self) -> Dict[str, object]:
        if not self.path.exists():
            data = self._default_data()
            self.data = data
            self._save()
            return data

        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"[GROWTH] failed to read {self.path.name}: {error}", flush=True)
            loaded = self._default_data()

        if not isinstance(loaded, dict):
            loaded = self._default_data()
        return self._normalize_data(loaded)

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

    def _default_data(self) -> Dict[str, object]:
        raise NotImplementedError

    def _normalize_data(self, data: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError


class ActionLogStore(_JsonStore):
    def __init__(
        self,
        path: Path = DEFAULT_ACTION_LOG_FILE,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(path, now_provider)
        print(f"[ACTION_LOG] loaded {self.path.name}", flush=True)

    def add_record(self, content: str, source: str = "manual") -> Dict[str, str]:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Action content cannot be empty")

        now = self._now()
        record = {
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "content": clean_content,
            "source": source.strip() or "manual",
        }
        self.data["records"].append(record)
        self._save()
        print("[ACTION_LOG] add record", flush=True)
        return record

    def records_for_date(self, date: Optional[str] = None) -> List[Dict[str, str]]:
        target_date = date or self._now().date().isoformat()
        return [
            dict(record)
            for record in self.data["records"]
            if isinstance(record, dict) and record.get("date") == target_date
        ]

    def _default_data(self) -> Dict[str, object]:
        return {"version": 1, "records": []}

    def _normalize_data(self, data: Dict[str, object]) -> Dict[str, object]:
        records = data.get("records", [])
        return {"version": 1, "records": records if isinstance(records, list) else []}


class GrowthLogStore(_JsonStore):
    def __init__(
        self,
        path: Path = DEFAULT_GROWTH_LOG_FILE,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(path, now_provider)
        print(f"[GROWTH_LOG] loaded {self.path.name}", flush=True)

    def save_review(self, review: Dict[str, object]) -> Dict[str, object]:
        date = str(review.get("date") or self._now().date().isoformat())
        entry = {
            "date": date,
            "saved_at": self._now().isoformat(timespec="seconds"),
            "review": dict(review),
        }
        entries = [
            item
            for item in self.data["entries"]
            if not isinstance(item, dict) or item.get("date") != date
        ]
        entries.append(entry)
        entries.sort(key=lambda item: str(item.get("date", "")))
        self.data["entries"] = entries
        self._save()
        print("[GROWTH_LOG] save review", flush=True)
        return entry

    def entries(self) -> List[Dict[str, object]]:
        return [dict(item) for item in self.data["entries"] if isinstance(item, dict)]

    def _default_data(self) -> Dict[str, object]:
        return {"version": 1, "entries": []}

    def _normalize_data(self, data: Dict[str, object]) -> Dict[str, object]:
        entries = data.get("entries", [])
        return {"version": 1, "entries": entries if isinstance(entries, list) else []}


class GrowthService:
    """Shared lightweight facade for plans, actions, reviews, and saved growth logs."""

    def __init__(
        self,
        plan_store: Optional[TodayPlanStore] = None,
        action_store: Optional[ActionLogStore] = None,
        growth_store: Optional[GrowthLogStore] = None,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.now_provider = now_provider
        self.plan_store = plan_store or TodayPlanStore(now_provider=now_provider)
        self.action_store = action_store or ActionLogStore(now_provider=now_provider)
        self.growth_store = growth_store or GrowthLogStore(now_provider=now_provider)

    def generate_review(self) -> Dict[str, object]:
        stats = self.plan_store.review()
        date = self.now_provider().date().isoformat()
        records = self.action_store.records_for_date(date)
        actions = [str(record.get("content", "")).strip() for record in records]
        actions = [content for content in actions if content]

        total = stats["total"]
        done = stats["done"]
        pending = stats["pending"]
        if total > 0 and done == total:
            encouragement = "今天的计划都落到了行动里，可以安心收尾了。"
        elif done > 0 or actions:
            encouragement = "已经留下了真实的进展，剩下的按自己的节奏继续就好。"
        elif total > 0:
            encouragement = "今天可能有些累，先保留计划，明天再从最小的一步开始。"
        else:
            encouragement = "今天还没有记录也没关系，愿意回来看一眼就是新的起点。"

        action_lines = "\n".join(f"- {content}" for content in actions) or "- 暂无行动记录"
        text = (
            f"今日复盘\n"
            f"计划 {total} 件，完成 {done} 件，未完成 {pending} 件。\n"
            f"行动记录：\n{action_lines}\n"
            f"{encouragement}"
        )
        print("[GROWTH] review", flush=True)
        return {
            "date": date,
            "total": total,
            "done": done,
            "pending": pending,
            "actions": actions,
            "encouragement": encouragement,
            "text": text,
        }

    def save_today_review(self) -> Dict[str, object]:
        return self.growth_store.save_review(self.generate_review())
