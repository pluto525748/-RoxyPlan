from __future__ import annotations

from copy import deepcopy
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Dict, Optional

from modules.repositories.growth_repository import GrowthRepository
from modules.repositories.local_json_utils import (
    atomic_write_json,
    json_transaction,
    load_json_document,
)


class LocalJsonGrowthRepository(GrowthRepository):
    """Current local JSON persistence for the growth loop."""

    def __init__(
        self,
        private_dir: Path,
        *,
        legacy_plan_file: Optional[Path] = None,
        legacy_action_file: Optional[Path] = None,
        legacy_growth_file: Optional[Path] = None,
    ) -> None:
        self.private_dir = Path(private_dir)
        self.plan_file = self.private_dir / "today_plan.json"
        self.action_file = self.private_dir / "action_log.json"
        self.growth_file = self.private_dir / "growth_log.json"
        self.legacy_plan_file = Path(legacy_plan_file) if legacy_plan_file else None
        self.legacy_action_file = Path(legacy_action_file) if legacy_action_file else None
        self.legacy_growth_file = Path(legacy_growth_file) if legacy_growth_file else None
        self.backup_dir = self.private_dir / "backups"
        self.lock_dir = self.private_dir / "locks"

    def plan_exists(self) -> bool:
        return self.plan_file.exists()

    def load_plan(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._load_with_legacy(self.plan_file, self.legacy_plan_file, fallback)

    def save_plan(self, data: Dict[str, object]) -> bool:
        return atomic_write_json(
            self.plan_file, data, label="Growth", lock_dir=self.lock_dir
        )

    def action_exists(self) -> bool:
        return self.action_file.exists()

    def load_actions(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._load_with_legacy(self.action_file, self.legacy_action_file, fallback)

    def save_actions(self, data: Dict[str, object]) -> bool:
        return atomic_write_json(
            self.action_file, data, label="Growth", lock_dir=self.lock_dir
        )

    def growth_exists(self) -> bool:
        return self.growth_file.exists()

    def load_growth(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._load_with_legacy(self.growth_file, self.legacy_growth_file, fallback)

    def save_growth(self, data: Dict[str, object]) -> bool:
        return atomic_write_json(
            self.growth_file, data, label="Growth", lock_dir=self.lock_dir
        )

    def transaction(self, store: str) -> AbstractContextManager[None]:
        paths = {
            "plan": self.plan_file,
            "actions": self.action_file,
            "growth": self.growth_file,
        }
        if store not in paths:
            raise ValueError(f"Unknown growth store: {store}")
        return json_transaction(paths[store], self.lock_dir)

    def _load_with_legacy(
        self,
        current: Path,
        legacy: Optional[Path],
        fallback: Dict[str, object],
    ) -> Dict[str, object]:
        if current.exists():
            return load_json_document(
                current,
                fallback,
                label="Growth",
                lock_dir=self.lock_dir,
                backup_dir=self.backup_dir,
            )
        if legacy is not None and legacy.exists():
            print(f"[Growth] migrated legacy {legacy.name}", flush=True)
            return load_json_document(
                legacy,
                fallback,
                label="Growth",
                lock_dir=self.lock_dir,
                backup_dir=self.backup_dir,
            )
        return deepcopy(fallback)
