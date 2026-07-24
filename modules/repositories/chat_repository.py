from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ContextManager, Dict, List


class ChatRepository(ABC):
    """Persistence boundary for chat sessions, messages and summaries."""

    @abstractmethod
    def history_exists(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_sessions(self) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def save_sessions(self, sessions: List[Dict[str, object]]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_messages(self, session_id: str) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def save_messages(self, session_id: str, messages: List[Dict[str, object]]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def summaries_exist(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def load_summaries(self) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def save_summaries(self, summaries: Dict[str, object]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def transaction(self, store: str) -> ContextManager[None]:
        raise NotImplementedError
