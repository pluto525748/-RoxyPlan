"""Business-date clock for an explicitly isolated desktop simulation."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from modules.repositories.local_json_utils import atomic_write_json, json_transaction


class SimulatedBusinessClock:
    MARKER = "roxyplan_simulated_days_v1"

    def __init__(self, root: Path, start_date: Optional[str] = None) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / "simulation_state.json"
        self.lock_dir = self.root / "data" / "private" / ".locks"
        self._validate_date(start_date) if start_date is not None else None
        if not self.path.exists():
            if start_date is None:
                raise ValueError("First simulation launch requires --start-date")
            if self.root.exists() and any(self.root.iterdir()):
                raise ValueError("Simulation root must be empty or already marked")
            self.root.mkdir(parents=True, exist_ok=True)
            self._save(start_date)
        elif start_date is not None and self.today() != start_date:
            raise ValueError("Existing simulation date differs; restart without --start-date")
        self.today()

    @staticmethod
    def _validate_date(value: str) -> date:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Simulation date must use YYYY-MM-DD")
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("Simulation date must use YYYY-MM-DD")
        return parsed

    def _load(self) -> dict:
        with json_transaction(self.path, self.lock_dir):
            value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("kind") != self.MARKER:
            raise ValueError("Invalid simulation marker")
        self._validate_date(value.get("business_date"))
        return value

    def _save(self, business_date: str) -> None:
        if not atomic_write_json(
            self.path,
            {"kind": self.MARKER, "business_date": business_date},
            label="SimulationDate", lock_dir=self.lock_dir,
        ):
            raise OSError("Unable to save simulation date")

    def today(self) -> str:
        return str(self._load()["business_date"])

    def now(self) -> datetime:
        """Use the simulated day with the real local clock time for date parsing."""
        return datetime.combine(self._validate_date(self.today()), datetime.now().time())

    def advance_one_day(self) -> str:
        with json_transaction(self.path, self.lock_dir):
            current = self._validate_date(self.today())
            next_day = (current + timedelta(days=1)).isoformat()
            self._save(next_day)
        if self.today() != next_day:
            raise OSError("Unable to verify advanced simulation date")
        return next_day
