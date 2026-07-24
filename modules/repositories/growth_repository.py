from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ContextManager, Dict


class GrowthRepository(ABC):
    """Persistence boundary for daily plans, actions and growth logs."""

    @abstractmethod
    def plan_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_plan(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_plan(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def action_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_actions(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_actions(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def growth_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_growth(self, fallback: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_growth(self, data: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def transaction(self, store: str) -> ContextManager[None]:
        raise NotImplementedError
