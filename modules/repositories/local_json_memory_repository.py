from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Dict, Optional

from modules.repositories.local_json_utils import (
    atomic_write_json,
    json_transaction,
    load_json_document,
)
from modules.repositories.memory_repository import MemoryRepository


class LocalJsonMemoryRepository(MemoryRepository):
    """Current local JSON persistence for memories, candidates and conflicts."""

    def __init__(
        self,
        memory_file: Path,
        *,
        candidate_file: Path,
        conflict_file: Path,
        backup_dir: Path,
        audit_file: Optional[Path] = None,
    ) -> None:
        self.memory_file = Path(memory_file)
        self.candidate_file = Path(candidate_file)
        self.conflict_file = Path(conflict_file)
        self.backup_dir = Path(backup_dir)
        self.audit_file = Path(audit_file) if audit_file else self.conflict_file.parent / "memory_audit.json"
        self.lock_dir = self.conflict_file.parent / "locks"

    def memory_exists(self) -> bool:
        return self.memory_file.exists()

    def load_memories(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._safe_load(self.memory_file, fallback, "Memory")

    def save_memories(self, data: Dict[str, object]) -> bool:
        return self._atomic_save(self.memory_file, data, "Memory")

    def candidate_exists(self) -> bool:
        return self.candidate_file.exists()

    def load_candidates(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._safe_load(self.candidate_file, fallback, "MemoryCandidate")

    def save_candidates(self, data: Dict[str, object]) -> bool:
        return self._atomic_save(self.candidate_file, data, "MemoryCandidate")

    def conflict_exists(self) -> bool:
        return self.conflict_file.exists()

    def load_conflicts(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._safe_load(self.conflict_file, fallback, "Memory")

    def save_conflicts(self, data: Dict[str, object]) -> bool:
        return self._atomic_save(self.conflict_file, data, "Memory")

    def audit_exists(self) -> bool:
        return self.audit_file.exists()

    def load_audit(self, fallback: Dict[str, object]) -> Dict[str, object]:
        return self._safe_load(self.audit_file, fallback, "MemoryAudit")

    def save_audit(self, data: Dict[str, object]) -> bool:
        return self._atomic_save(self.audit_file, data, "MemoryAudit")

    def create_backup(self, timestamp: str) -> Optional[Path]:
        try:
            with self.transaction("memory"):
                original = self.memory_file.read_bytes()
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                path = self.backup_dir / f"memory_{timestamp}.json"
                suffix = 1
                while path.exists():
                    path = self.backup_dir / f"memory_{timestamp}_{suffix}.json"
                    suffix += 1
                path.write_bytes(original)
            print("[Memory] backup created", flush=True)
            return path
        except (OSError, TimeoutError) as error:
            print(f"[Memory] backup failed: {type(error).__name__}", flush=True)
            return None

    def _safe_load(self, path: Path, fallback: Dict[str, object], label: str) -> Dict[str, object]:
        return load_json_document(
            path,
            fallback,
            label=label,
            lock_dir=self.lock_dir,
            backup_dir=self.backup_dir,
        )

    def _atomic_save(self, path: Path, data: Dict[str, object], label: str) -> bool:
        return atomic_write_json(
            path,
            data,
            label=label,
            lock_dir=self.lock_dir,
        )

    def transaction(self, store: str) -> AbstractContextManager[None]:
        paths = {
            "memory": self.memory_file,
            "candidates": self.candidate_file,
            "conflicts": self.conflict_file,
            "audit": self.audit_file,
        }
        if store not in paths:
            raise ValueError(f"Unknown memory store: {store}")
        return json_transaction(paths[store], self.lock_dir)
