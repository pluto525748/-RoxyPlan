from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ContextManager, Dict, Optional


class MemoryRepository(ABC):
    """Persistence boundary for long-term memory and its private workflow data."""

    @abstractmethod
    def memory_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_memories(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_memories(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def candidate_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_candidates(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_candidates(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def conflict_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_conflicts(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_conflicts(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def audit_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_audit(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_audit(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_backup(self, timestamp: str) -> Optional[Path]:
        raise NotImplementedError

    @abstractmethod
    def transaction(self, store: str) -> ContextManager[None]:
        raise NotImplementedError
