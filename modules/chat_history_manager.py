from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from modules.repositories.chat_repository import ChatRepository
from modules.repositories.local_json_chat_repository import LocalJsonChatRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIVATE_DIR = PROJECT_ROOT / "data" / "private"


class ChatHistoryManager:
    """Local, failure-tolerant storage for chat sessions and summaries."""

    def __init__(
        self,
        private_dir: Path = DEFAULT_PRIVATE_DIR,
        now_provider: Callable[[], datetime] = datetime.now,
        enabled: bool = True,
        repository: Optional[ChatRepository] = None,
    ) -> None:
        self.private_dir = Path(private_dir)
        self.repository = repository or LocalJsonChatRepository(self.private_dir)
        self.history_file = getattr(
            self.repository, "history_file", self.private_dir / "chat_history.json"
        )
        self.summary_file = getattr(
            self.repository, "summary_file", self.private_dir / "chat_summaries.json"
        )
        self.now_provider = now_provider
        self.enabled = bool(enabled)
        history_existed = self.repository.history_exists()
        summary_existed = self.repository.summaries_exist()
        self.history_data = {
            "version": 1,
            "sessions": self.repository.load_sessions(),
        }
        self.summary_data = {
            "version": 1,
            "summaries": self.repository.load_summaries(),
        }
        if not history_existed:
            self.repository.save_sessions([])
        if not summary_existed:
            self.repository.save_summaries({})
        self._normalize()
        print("[ChatHistory] load", flush=True)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def new_session(
        self,
        title: str = "新对话",
        session_id: Optional[str] = None,
    ) -> Dict[str, object]:
        with self.repository.transaction("history"):
            self._refresh_history()
            stable_session_id = str(session_id or "").strip()
            if stable_session_id:
                existing = self._session_ref(stable_session_id)
                if existing is not None:
                    return deepcopy(existing)
            now = self._timestamp()
            session = {
                "session_id": stable_session_id or f"session_{uuid.uuid4().hex}",
                "started_at": now,
                "updated_at": now,
                "title": title.strip() or "新对话",
                "summary": "",
                "message_count": 0,
                "messages": [],
            }
            self.history_data["sessions"].append(session)
            if not self._save_history_if_enabled():
                self._refresh_history()
                raise OSError("Failed to save chat session")
        print("[ChatHistory] new session", flush=True)
        return deepcopy(session)

    def ensure_session(self, restore_latest: bool = True) -> Dict[str, object]:
        if restore_latest:
            latest = self.latest_session()
            if latest is not None:
                return latest
        return self.new_session()

    def sessions(self) -> List[Dict[str, object]]:
        self._refresh_history()
        sessions = [
            deepcopy(item)
            for item in self.history_data.get("sessions", [])
            if isinstance(item, dict)
        ]
        sessions.sort(
            key=lambda item: str(item.get("updated_at", "")), reverse=True
        )
        return sessions

    def latest_session(self) -> Optional[Dict[str, object]]:
        sessions = self.sessions()
        return sessions[0] if sessions else None

    def session_by_index(self, index: int) -> Optional[Dict[str, object]]:
        sessions = self.sessions()
        if index < 1 or index > len(sessions):
            return None
        return sessions[index - 1]

    def get_session(self, session_id: str) -> Optional[Dict[str, object]]:
        self._refresh_history()
        session = self._session_ref(session_id)
        return deepcopy(session) if session is not None else None

    def switch_session(self, session_id: str) -> Optional[Dict[str, object]]:
        session = self.get_session(session_id)
        if session is not None:
            print("[ChatHistory] switch session", flush=True)
        return session

    def rename_session(self, session_id: str, title: str) -> Optional[Dict[str, object]]:
        clean_title = " ".join(str(title).split()).strip()
        if not clean_title:
            return None
        with self.repository.transaction("history"):
            self._refresh_history()
            session = self._session_ref(session_id)
            if session is None:
                return None
            session["title"] = clean_title[:60]
            session["updated_at"] = self._timestamp()
            if not self._save_history_if_enabled():
                self._refresh_history()
                return None
        print("[ChatHistory] rename session", flush=True)
        return deepcopy(session)

    def delete_session(self, session_id: str) -> bool:
        with self.repository.transaction("history"), self.repository.transaction("summaries"):
            self._refresh_history()
            self._refresh_summaries()
            sessions = self.history_data.get("sessions", [])
            before = len(sessions)
            self.history_data["sessions"] = [
                item
                for item in sessions
                if not isinstance(item, dict) or item.get("session_id") != session_id
            ]
            if len(self.history_data["sessions"]) == before:
                return False

            summaries = self.summary_data.get("summaries", {})
            if isinstance(summaries, dict):
                summaries.pop(session_id, None)
            # Deletion is an explicit privacy action and must persist even if recording is paused.
            if not self._save_history_if_enabled(force=True):
                self._refresh_history()
                return False
            if not self._save_summaries_if_enabled(force=True):
                self._refresh_summaries()
                return False
        print("[ChatHistory] delete session", flush=True)
        return True

    def clear_history(self, confirmed: bool = False) -> bool:
        if not confirmed:
            return False
        with self.repository.transaction("history"), self.repository.transaction("summaries"):
            self.history_data = {"version": 1, "sessions": []}
            self.summary_data = {"version": 1, "summaries": {}}
            if not self._save_history_if_enabled(force=True):
                self._refresh_history()
                return False
            if not self._save_summaries_if_enabled(force=True):
                self._refresh_summaries()
                return False
        print("[ChatHistory] clear history", flush=True)
        return True

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        if not self.enabled:
            return None
        clean_content = content.strip()
        if not clean_content:
            return None
        with self.repository.transaction("history"):
            self._refresh_history()
            session = self._session_ref(session_id)
            if session is None:
                return None

            normalized_role = role if role in {"user", "assistant", "system"} else "system"
            message = {
                "id": f"message_{uuid.uuid4().hex}",
                "session_id": session_id,
                "role": normalized_role,
                "content": clean_content,
                "created_at": self._timestamp(),
                "intent": intent or None,
                "metadata": dict(metadata or {}),
            }
            session["messages"].append(message)
            session["message_count"] = len(session["messages"])
            session["updated_at"] = message["created_at"]
            self._update_title(session)
            if not self._save_history_if_enabled():
                self._refresh_history()
                return None
        print("[ChatHistory] save message", flush=True)
        return deepcopy(message)

    def messages(self, session_id: str) -> List[Dict[str, object]]:
        self._refresh_history()
        session = self._session_ref(session_id)
        if session is None:
            return []
        return [
            deepcopy(item)
            for item in session.get("messages", [])
            if isinstance(item, dict)
        ]

    def recent_messages(self, session_id: str, limit: int = 16) -> List[Dict[str, object]]:
        if limit <= 0:
            return []
        return self.messages(session_id)[-limit:]

    def character_count(self, session_id: str) -> int:
        return sum(len(str(item.get("content", ""))) for item in self.messages(session_id))

    def get_summary(self, session_id: str) -> str:
        self._refresh_summaries()
        summaries = self.summary_data.get("summaries", {})
        if isinstance(summaries, dict):
            item = summaries.get(session_id, {})
            if isinstance(item, dict):
                return str(item.get("summary", "")).strip()
        self._refresh_history()
        session = self._session_ref(session_id)
        return str(session.get("summary", "")).strip() if session else ""

    def summary_source_count(self, session_id: str) -> int:
        self._refresh_summaries()
        summaries = self.summary_data.get("summaries", {})
        if not isinstance(summaries, dict):
            return 0
        item = summaries.get(session_id, {})
        if not isinstance(item, dict):
            return 0
        try:
            return int(item.get("source_message_count", 0))
        except (TypeError, ValueError):
            return 0

    def save_summary(self, session_id: str, summary: str) -> bool:
        clean_summary = summary.strip()
        with self.repository.transaction("history"), self.repository.transaction("summaries"):
            self._refresh_history()
            self._refresh_summaries()
            session = self._session_ref(session_id)
            if session is None or not clean_summary:
                return False
            summaries = self.summary_data.setdefault("summaries", {})
            summaries[session_id] = {
                "session_id": session_id,
                "summary": clean_summary,
                "updated_at": self._timestamp(),
                "source_message_count": len(session.get("messages", [])),
            }
            session["summary"] = clean_summary
            if not self._save_summaries_if_enabled():
                self._refresh_summaries()
                return False
            if not self._save_history_if_enabled():
                self._refresh_history()
                return False
        return True

    def should_summarize(
        self,
        session_id: str,
        message_threshold: int = 30,
        character_threshold: int = 12000,
    ) -> bool:
        messages = self.messages(session_id)
        over_limit = (
            len(messages) > max(0, message_threshold)
            or self.character_count(session_id) > max(0, character_threshold)
        )
        if not over_limit:
            return False
        previous_count = self.summary_source_count(session_id)
        return previous_count == 0 or len(messages) - previous_count >= 10

    def generate_rule_summary(self, session_id: str) -> str:
        messages = self.messages(session_id)
        user_messages = [
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "user" and str(item.get("content", "")).strip()
        ]
        assistant_messages = [
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "assistant" and str(item.get("content", "")).strip()
        ]
        if not user_messages and not assistant_messages:
            return "这段对话目前还没有足够内容可供总结。"

        topics = self._unique_snippets(user_messages, 3)
        decisions = self._matching_snippets(
            user_messages, ("决定", "计划", "选择", "要做", "完成"), 3
        )
        states = self._matching_snippets(
            user_messages, ("状态", "累", "焦虑", "开心", "难过", "困"), 2
        )
        unfinished = self._matching_snippets(
            user_messages, ("还没", "未完成", "之后", "下次", "待办", "继续"), 3
        )

        lines = [f"主要讨论：{'；'.join(topics) if topics else '日常交流'}。"]
        lines.append(f"用户决定：{'；'.join(decisions) if decisions else '暂无明确决定'}。")
        lines.append(f"当前状态：{'；'.join(states) if states else '未明确提及'}。")
        lines.append(f"未完成事项：{'；'.join(unfinished) if unfinished else '暂无明确事项'}。")
        if user_messages:
            lines.append(f"下次可继续：{self._snippet(user_messages[-1])}。")
        return "\n".join(lines)

    def _session_ref(self, session_id: str) -> Optional[Dict[str, object]]:
        for item in self.history_data.get("sessions", []):
            if isinstance(item, dict) and item.get("session_id") == session_id:
                return item
        return None

    def _update_title(self, session: Dict[str, object]) -> None:
        if session.get("title") != "新对话" or len(session.get("messages", [])) < 3:
            return
        first_user = next(
            (
                str(item.get("content", "")).strip()
                for item in session.get("messages", [])
                if isinstance(item, dict) and item.get("role") == "user"
            ),
            "",
        )
        if first_user:
            session["title"] = self._snippet(first_user, 20)

    def _normalize(self) -> None:
        sessions = self.history_data.get("sessions")
        if not isinstance(sessions, list):
            self.history_data = {"version": 1, "sessions": []}
        summaries = self.summary_data.get("summaries")
        if not isinstance(summaries, dict):
            self.summary_data = {"version": 1, "summaries": {}}

    def _save_history_if_enabled(self, force: bool = False) -> bool:
        if self.enabled or force:
            sessions = self.history_data.get("sessions", [])
            return self.repository.save_sessions(sessions if isinstance(sessions, list) else [])
        return True

    def _save_summaries_if_enabled(self, force: bool = False) -> bool:
        if self.enabled or force:
            summaries = self.summary_data.get("summaries", {})
            return self.repository.save_summaries(summaries if isinstance(summaries, dict) else {})
        return True

    def _refresh_history(self) -> None:
        self.history_data = {
            "version": 1,
            "sessions": self.repository.load_sessions(),
        }
        self._normalize()

    def _refresh_summaries(self) -> None:
        self.summary_data = {
            "version": 1,
            "summaries": self.repository.load_summaries(),
        }
        self._normalize()

    def _timestamp(self) -> str:
        return self.now_provider().isoformat(timespec="seconds")

    @staticmethod
    def _snippet(text: str, limit: int = 36) -> str:
        compact = " ".join(text.split())
        return compact if len(compact) <= limit else compact[:limit].rstrip() + "…"

    @classmethod
    def _unique_snippets(cls, values: List[str], limit: int) -> List[str]:
        result: List[str] = []
        for value in values:
            snippet = cls._snippet(value)
            if snippet and snippet not in result:
                result.append(snippet)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _matching_snippets(
        cls, values: List[str], keywords: tuple, limit: int
    ) -> List[str]:
        return cls._unique_snippets(
            [value for value in values if any(word in value for word in keywords)], limit
        )
