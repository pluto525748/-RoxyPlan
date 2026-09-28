from __future__ import annotations

import uuid
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from modules.repositories.chat_repository import ChatRepository
from modules.repositories.local_json_chat_repository import LocalJsonChatRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIVATE_DIR = PROJECT_ROOT / "data" / "private"


# These are retrieval scaffolding rather than user topics. Removing them before
# lexical matching prevents a greeting or "do you remember" shell from pulling
# an otherwise unrelated old session into an ordinary chat turn.
_HISTORY_RETRIEVAL_NOISE = (
    "相关旧会话摘要",
    "relation_to_current_date",
    "source_message_time_start",
    "source_message_time_end",
    "主要讨论",
    "用户决定",
    "当前状态",
    "未完成事项",
    "下次可继续",
    "我们之前",
    "我们以前",
    "你还记得",
    "还记得",
    "新的一天",
    "又见面了",
    "接下来",
    "下一步",
    "怎么办",
    "讨论过",
    "提到过",
    "说过",
    "聊过",
    "说的",
    "聊的",
    "关于",
    "相关",
    "以前",
    "之前",
    "上次",
    "那次",
    "曾经",
    "哪些",
    "什么",
    "怎么",
    "如何",
    "历史",
    "会话",
    "对话",
    "内容",
    "事情",
    "这个",
    "那个",
    "现在",
    "目前",
    "如今",
    "最近",
    "已经",
    "今天",
    "昨天",
    "明天",
    "继续",
    "计划",
    "任务",
    "完成",
    "用户",
    "我们",
    "你好",
    "您好",
    "见面",
)

_CURRENT_STATE_MARKERS = ("现在", "目前", "如今", "已经", "刚刚")
_EXPLICIT_HISTORY_MARKERS = ("以前", "之前", "上次", "那次", "曾经", "历史", "旧会话")
_EMPTY_SUMMARY_FRAGMENTS = (
    "暂无明确决定",
    "未明确提及",
    "暂无明确事项",
    "日常交流",
    "没有足够内容可供总结",
)
_WEAK_HISTORY_TERMS = {
    "学习",
    "工作",
    "项目",
    "问题",
    "事情",
    "内容",
}


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

    def save_summary(
        self,
        session_id: str,
        summary: str,
        *,
        persona_id: str = "roxy",
    ) -> bool:
        clean_summary = summary.strip()
        with self.repository.transaction("history"), self.repository.transaction("summaries"):
            self._refresh_history()
            self._refresh_summaries()
            session = self._session_ref(session_id)
            if session is None or not clean_summary:
                return False
            summaries = self.summary_data.setdefault("summaries", {})
            existing = summaries.get(session_id, {})
            existing = existing if isinstance(existing, dict) else {}
            messages = [
                item for item in session.get("messages", []) if isinstance(item, dict)
            ]
            created_at = str(existing.get("created_at") or self._timestamp())
            summaries[session_id] = {
                "session_id": session_id,
                "summary": clean_summary,
                "created_at": created_at,
                "updated_at": self._timestamp(),
                "source_message_count": len(messages),
                "started_at": str(session.get("started_at", "")),
                "time_range": {
                    "start": str(messages[0].get("created_at", "")) if messages else "",
                    "end": str(messages[-1].get("created_at", "")) if messages else "",
                },
                "persona_id": str(persona_id or "roxy"),
            }
            session["summary"] = clean_summary
            if not self._save_summaries_if_enabled():
                self._refresh_summaries()
                return False
            if not self._save_history_if_enabled():
                self._refresh_history()
                return False
        return True

    def update_session_summary(self, session_id: str, *, persona_id: str = "roxy") -> bool:
        """Refresh a bounded episodic summary only at explicit/threshold boundaries."""
        if not self.messages(session_id):
            return False
        summary = self.generate_rule_summary(session_id)
        return self.save_summary(session_id, summary, persona_id=persona_id)

    def finalize_session(self, session_id: str, *, persona_id: str = "roxy") -> bool:
        """Persist a short-session-safe episodic summary before a window changes."""
        return self.update_session_summary(session_id, persona_id=persona_id)

    def relevant_summaries(
        self,
        query: str,
        *,
        exclude_session_id: str = "",
        limit: int = 3,
        char_budget: int = 900,
        persona_id: str = "roxy",
    ) -> List[Dict[str, object]]:
        """Retrieve up to two query-centred fragments from verified old summaries.

        This remains deliberately lexical and local.  It does not turn old chat
        into memory, and it does not expose full transcripts to the model.
        """
        self._refresh_summaries()
        terms = self._retrieval_terms(query)
        if not terms or limit <= 0 or char_budget <= 0:
            return []
        records = self.summary_data.get("summaries", {})
        if not isinstance(records, dict):
            return []
        effective_limit = min(2, max(0, int(limit)))
        current_state_query = self._is_current_state_query(query)
        ranked = []
        for session_id, raw in records.items():
            if session_id == exclude_session_id or not isinstance(raw, dict):
                continue
            if str(raw.get("persona_id", "roxy")) != str(persona_id or "roxy"):
                continue
            summary = str(raw.get("summary", "")).strip()
            if not summary:
                continue
            fragment_match = self._best_summary_fragment(
                summary,
                terms,
                current_state_query=current_state_query,
            )
            if fragment_match is None:
                continue
            score, coverage, fragment = fragment_match
            ranked.append(
                (
                    score,
                    coverage,
                    str(raw.get("updated_at", "")),
                    str(session_id),
                    raw,
                    fragment,
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        remaining = int(char_budget)
        result: List[Dict[str, object]] = []
        for (
            _score,
            _coverage,
            _updated_at,
            session_id,
            raw,
            fragment,
        ) in ranked[:effective_limit]:
            if remaining <= 0:
                break
            excerpt = fragment[:remaining].rstrip()
            if not excerpt:
                continue
            raw_time_range = raw.get("time_range", {})
            time_range = raw_time_range if isinstance(raw_time_range, dict) else {}
            result.append(
                {
                    "session_id": session_id,
                    "summary": excerpt,
                    "updated_at": str(raw.get("updated_at", "")),
                    "time_range": {
                        "start": str(time_range.get("start", "")),
                        "end": str(time_range.get("end", "")),
                    },
                    "persona_id": str(raw.get("persona_id", "roxy")),
                    "source_message_count": int(raw.get("source_message_count", 0) or 0),
                    "trimmed": len(excerpt) < len(fragment),
                }
            )
            remaining -= len(excerpt)
        return result

    def should_summarize(
        self,
        session_id: str,
        message_threshold: int = 30,
        character_threshold: int = 12000,
    ) -> bool:
        messages = self._summary_messages(session_id)
        over_limit = (
            len(messages) > max(0, message_threshold)
            or self.character_count(session_id) > max(0, character_threshold)
        )
        if not over_limit:
            return False
        previous_count = self.summary_source_count(session_id)
        return previous_count == 0 or len(messages) - previous_count >= 10

    def generate_rule_summary(self, session_id: str) -> str:
        messages = self._summary_messages(session_id)
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

    def _summary_messages(self, session_id: str) -> List[Dict[str, object]]:
        return [
            item
            for item in self.messages(session_id)
            if not self._is_transient_summary_message(item)
        ]

    @staticmethod
    def _is_transient_summary_message(message: Dict[str, object]) -> bool:
        content = str(message.get("content", "")).strip().lower()
        metadata = message.get("metadata", {})
        transient_markers = (
            "confirmation_",
            "确认 id",
            "确认id",
            "刚才那个",
            "刚才那条",
            "第一个",
            "第二个",
            "第三个",
        )
        if any(marker in content for marker in transient_markers):
            return True
        if isinstance(metadata, dict) and any(
            key in metadata for key in ("confirmation_id", "tool_arguments", "request_id")
        ):
            return True
        return bool(re.search(r"\bconfirmation_[a-z0-9_\-]+\b", content))

    @staticmethod
    def _summary_terms(text: str) -> set:
        normalized = str(text or "").lower()
        latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        pairs = {
            normalized[index : index + 2]
            for index in range(max(0, len(normalized) - 1))
            if "\u4e00" <= normalized[index] <= "\u9fff"
            and "\u4e00" <= normalized[index + 1] <= "\u9fff"
        }
        return latin | pairs

    @classmethod
    def _retrieval_terms(cls, text: str) -> set:
        normalized = str(text or "").lower()
        for phrase in _HISTORY_RETRIEVAL_NOISE:
            normalized = normalized.replace(phrase, " ")
        latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        pairs = set()
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            pairs.update(
                run[index : index + 2]
                for index in range(max(0, len(run) - 1))
            )
        return latin | pairs

    @classmethod
    def _best_summary_fragment(
        cls,
        summary: str,
        query_terms: set,
        *,
        current_state_query: bool,
    ) -> Optional[tuple]:
        best: Optional[tuple] = None
        for fragment in cls._summary_fragments(summary):
            fragment_terms = cls._retrieval_terms(fragment)
            overlap = query_terms & fragment_terms
            if not overlap:
                continue
            latin_overlap = any(re.fullmatch(r"[a-z0-9_]{2,}", term) for term in overlap)
            # A single generic Chinese bigram (for example only “学习” or
            # “工作”) is too weak for implicit continuity. A distinctive term
            # such as “人格” may still be the useful bridge when related words
            # are separated in the stored summary.
            if (
                not latin_overlap
                and len(overlap) == 1
                and (current_state_query or bool(overlap & _WEAK_HISTORY_TERMS))
            ):
                continue
            # A present-state statement must not be overridden merely because an
            # old state shares one generic noun (for example "工作"). A concrete
            # multi-term topic or exact Latin identifier can still provide useful
            # continuity in "我现在还在做 RoxyPlan".
            coverage = len(overlap) / max(1, min(len(query_terms), 8))
            candidate = (len(overlap), coverage, -len(fragment), fragment)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
        if best is None:
            return None
        return best[0], best[1], best[3]

    @staticmethod
    def _summary_fragments(summary: str) -> List[str]:
        fragments: List[str] = []
        for raw in re.split(r"[\n\r；;。！？!?]+", str(summary or "")):
            fragment = raw.strip(" \t-：:")
            if not fragment:
                continue
            if "：" in fragment:
                label, content = fragment.split("：", 1)
                if label.strip() in {
                    "主要讨论",
                    "用户决定",
                    "当前状态",
                    "未完成事项",
                    "下次可继续",
                }:
                    fragment = content.strip()
            if not fragment or any(
                marker in fragment for marker in _EMPTY_SUMMARY_FRAGMENTS
            ):
                continue
            if fragment not in fragments:
                fragments.append(fragment)
        return fragments

    @staticmethod
    def _is_current_state_query(query: str) -> bool:
        text = str(query or "")
        return any(marker in text for marker in _CURRENT_STATE_MARKERS) and not any(
            marker in text for marker in _EXPLICIT_HISTORY_MARKERS
        )

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
