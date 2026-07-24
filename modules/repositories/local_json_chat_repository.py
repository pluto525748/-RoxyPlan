from __future__ import annotations

from copy import deepcopy
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Dict, List

from modules.repositories.chat_repository import ChatRepository
from modules.repositories.local_json_utils import (
    atomic_write_json,
    json_transaction,
    load_json_document,
)


class LocalJsonChatRepository(ChatRepository):
    """Current local JSON persistence for sessions, messages and summaries."""

    def __init__(self, private_dir: Path) -> None:
        self.private_dir = Path(private_dir)
        self.history_file = self.private_dir / "chat_history.json"
        self.summary_file = self.private_dir / "chat_summaries.json"
        self.backup_dir = self.private_dir / "backups"
        self.lock_dir = self.private_dir / "locks"

    def history_exists(self) -> bool:
        return self.history_file.exists()

    def load_sessions(self) -> List[Dict[str, object]]:
        document = load_json_document(
            self.history_file,
            {"version": 1, "sessions": []},
            label="ChatHistory",
            lock_dir=self.lock_dir,
            backup_dir=self.backup_dir,
        )
        sessions = document.get("sessions", [])
        return deepcopy(sessions) if isinstance(sessions, list) else []

    def save_sessions(self, sessions: List[Dict[str, object]]) -> bool:
        return atomic_write_json(
            self.history_file,
            {"version": 1, "sessions": deepcopy(sessions)},
            label="ChatHistory",
            lock_dir=self.lock_dir,
            trailing_newline=False,
        )

    def load_messages(self, session_id: str) -> List[Dict[str, object]]:
        for session in self.load_sessions():
            if isinstance(session, dict) and session.get("session_id") == session_id:
                messages = session.get("messages", [])
                return deepcopy(messages) if isinstance(messages, list) else []
        return []

    def save_messages(self, session_id: str, messages: List[Dict[str, object]]) -> bool:
        sessions = self.load_sessions()
        for session in sessions:
            if isinstance(session, dict) and session.get("session_id") == session_id:
                session["messages"] = deepcopy(messages)
                session["message_count"] = len(messages)
                return self.save_sessions(sessions)
        return False

    def summaries_exist(self) -> bool:
        return self.summary_file.exists()

    def load_summaries(self) -> Dict[str, object]:
        document = load_json_document(
            self.summary_file,
            {"version": 1, "summaries": {}},
            label="ChatHistory",
            lock_dir=self.lock_dir,
            backup_dir=self.backup_dir,
        )
        summaries = document.get("summaries", {})
        return deepcopy(summaries) if isinstance(summaries, dict) else {}

    def save_summaries(self, summaries: Dict[str, object]) -> bool:
        return atomic_write_json(
            self.summary_file,
            {"version": 1, "summaries": deepcopy(summaries)},
            label="ChatHistory",
            lock_dir=self.lock_dir,
            trailing_newline=False,
        )

    def transaction(self, store: str) -> AbstractContextManager[None]:
        paths = {"history": self.history_file, "summaries": self.summary_file}
        if store not in paths:
            raise ValueError(f"Unknown chat store: {store}")
        return json_transaction(paths[store], self.lock_dir)
