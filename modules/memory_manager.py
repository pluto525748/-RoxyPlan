from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from modules.repositories.memory_repository import MemoryRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_FILE = PROJECT_ROOT / "memory.json"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "private" / "backups"
DEFAULT_CONFLICT_FILE = PROJECT_ROOT / "data" / "private" / "memory_conflicts.json"
DEFAULT_AUDIT_FILE = PROJECT_ROOT / "data" / "private" / "memory_audit.json"

MEMORY_CATEGORIES = {
    "preference",
    "goal",
    "habit",
    "project",
    "learning",
    "health",
    "relationship",
    "rule",
    "other",
}
MEMORY_STATUSES = {"active", "archived"}
SENSITIVE_CATEGORIES = {"health", "relationship"}
MEMORY_SCOPES = {
    "current_state",
    "stable_identity",
    "historical_state",
    "future_intent",
    "preference",
    "temporary_state",
    "constraint",
}

_INVALID_LEGACY_NICKNAMES = {"", "我", "你", "用户", "未设置"}
_PREFERRED_NAME_PATTERNS = (
    re.compile(r"^(?:以后(?:请)?|今后(?:请)?)?(?:叫我|称呼我(?:为)?)(.+)$"),
    re.compile(r"^(?:我的)?名字(?:叫|是)(.+)$"),
    re.compile(r"^我叫(.+)$"),
    re.compile(r"^(?:用户)?昵称(?:是|为)(.+)$"),
    re.compile(r"^你的名字(?:叫|是)(.+)$"),
)


class MemoryManager:
    """Versioned local long-term memory storage with safe legacy migration."""

    def __init__(
        self,
        memory_file: Path = DEFAULT_MEMORY_FILE,
        backup_dir: Path = DEFAULT_BACKUP_DIR,
        conflict_file: Path = DEFAULT_CONFLICT_FILE,
        audit_file: Optional[Path] = None,
        now_provider: Callable[[], datetime] = datetime.now,
        default_data: Optional[Dict[str, object]] = None,
        repository: Optional[MemoryRepository] = None,
    ) -> None:
        self.memory_file = Path(memory_file)
        self.backup_dir = Path(backup_dir)
        self.conflict_file = Path(conflict_file)
        self.audit_file = (
            Path(audit_file)
            if audit_file is not None
            else self.conflict_file.parent / "memory_audit.json"
        )
        self.repository = repository or LocalJsonMemoryRepository(
            self.memory_file,
            candidate_file=self.conflict_file.parent / "memory_candidates.json",
            conflict_file=self.conflict_file,
            backup_dir=self.backup_dir,
            audit_file=self.audit_file,
        )
        self.memory_file = getattr(self.repository, "memory_file", self.memory_file)
        self.backup_dir = getattr(self.repository, "backup_dir", self.backup_dir)
        self.conflict_file = getattr(self.repository, "conflict_file", self.conflict_file)
        self.audit_file = getattr(self.repository, "audit_file", self.audit_file)
        self.now_provider = now_provider
        self.default_data = deepcopy(
            default_data or {"version": 2, "profile": {}, "memories": []}
        )
        self.default_data["version"] = 2
        self.default_data.setdefault("profile", {})
        self.default_data.setdefault("memories", [])
        self.last_backup_path: Optional[Path] = None
        self.data = self._load_or_create_memory()
        self.conflict_data = self._load_or_create_conflicts()
        self.audit_data = self._load_or_create_audit()

    def memories(
        self,
        status: Optional[str] = "active",
        category: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        self._refresh_memories()
        items = [
            deepcopy(item)
            for item in self.data.get("memories", [])
            if isinstance(item, dict)
        ]
        if status is not None:
            items = [
                item
                for item in items
                if item.get("status", "active") == status
            ]
        if category:
            items = [
                item
                for item in items
                if self.normalize_category(str(item.get("category", "other")))
                == category
            ]
        return items

    def preferred_name_record(
        self,
        *,
        memories: Optional[List[Dict[str, object]]] = None,
    ) -> Optional[Dict[str, object]]:
        """Resolve one preferred name, with formal memory outranking legacy profile."""

        candidates: List[Tuple[datetime, int, Dict[str, object], str]] = []
        source_memories = self.working_memories() if memories is None else memories
        for item in source_memories:
            if str(item.get("scope", "")) == "temporary_state":
                continue
            name = self.extract_preferred_name(str(item.get("content", "")))
            if not name:
                continue
            timestamp = str(item.get("updated_at") or item.get("created_at") or "")
            try:
                updated_at = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
            except (TypeError, ValueError, OSError):
                updated_at = datetime.min.replace(tzinfo=timezone.utc)
            candidates.append(
                (updated_at, self._safe_int(item.get("id"), 0), item, name)
            )
        if candidates:
            _timestamp, _memory_id, memory, name = max(
                candidates, key=lambda value: (value[0], value[1])
            )
            result = deepcopy(memory)
            result["preferred_name"] = name
            result["authority"] = "formal_memory"
            return result

        self._refresh_memories()
        profile = self.data.get("profile", {})
        nickname = (
            str(profile.get("nickname", "")).strip()
            if isinstance(profile, dict)
            else ""
        )
        if nickname in _INVALID_LEGACY_NICKNAMES:
            return None
        return {
            "id": None,
            "content": f"用户昵称是{nickname}",
            "preferred_name": nickname,
            "category": "other",
            "scope": "stable_identity",
            "authority": "legacy_profile",
        }

    def preferred_name(self) -> str:
        record = self.preferred_name_record()
        return str(record.get("preferred_name", "")).strip() if record else ""

    def working_memories(
        self,
        *,
        category: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, object]]:
        """Verified runtime view; retained temporary/archived data is not deleted."""

        conflicted_ids = {
            int(item.get("old_memory_id", 0) or 0)
            for item in self.conflicts("pending")
            if int(item.get("old_memory_id", 0) or 0) > 0
        }
        current_time = (now or self.now_provider()).astimezone(timezone.utc)

        def valid(item: Dict[str, object]) -> bool:
            try:
                valid_from = item.get("valid_from")
                valid_until = item.get("valid_until")
                if (
                    valid_from
                    and datetime.fromisoformat(str(valid_from)).astimezone(timezone.utc) > current_time
                ):
                    return False
                if (
                    valid_until
                    and datetime.fromisoformat(str(valid_until)).astimezone(timezone.utc) < current_time
                ):
                    return False
            except (TypeError, ValueError, OSError):
                return False
            return True

        return [
            item
            for item in self.memories("active", category=category)
            if str(item.get("scope", "")) != "temporary_state"
            and int(item.get("id", 0) or 0) not in conflicted_ids
            and valid(item)
        ]

    @staticmethod
    def extract_preferred_name(content: str) -> str:
        text = str(content or "").strip().strip("。.!！?？；;")
        for pattern in _PREFERRED_NAME_PATTERNS:
            match = pattern.fullmatch(text)
            if match is None:
                continue
            name = re.sub(
                r"(?:你应该知道|你知道吧|就行|即可|好了|吧|呀|啊|呢)$",
                "",
                match.group(1).strip(),
            ).strip(" ，,。.!！?？；;：:")
            if name and name not in _INVALID_LEGACY_NICKNAMES and len(name) <= 40:
                return name
        return ""

    def get(self, memory_id: int) -> Optional[Dict[str, object]]:
        self._refresh_memories()
        item = self._memory_ref(memory_id)
        return deepcopy(item) if item is not None else None

    def update_profile(self, values: Dict[str, object]) -> Dict[str, object]:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            profile = self.data.setdefault("profile", {})
            if not isinstance(profile, dict):
                profile = {}
                self.data["profile"] = profile
            profile.update(deepcopy(dict(values)))
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                raise OSError("Failed to save memory profile")
            return deepcopy(profile)

    def add_memory(
        self,
        content: str,
        *,
        category: Optional[str] = None,
        importance: int = 3,
        confidence: float = 1.0,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        scope: Optional[str] = None,
        location: Optional[str] = None,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        last_confirmed_at: Optional[str] = None,
        supersedes: Optional[object] = None,
        contradicted_by: Optional[object] = None,
        allow_conflict: bool = False,
        allow_similar: bool = False,
    ) -> Dict[str, object]:
        clean_content = str(content).strip()
        if not clean_content:
            raise ValueError("Memory content cannot be empty")
        resolved_category = self.normalize_category(
            category or self.classify(clean_content)
        )
        with self.repository.transaction("memory"):
            self._refresh_memories()
            duplicate, similarity = self.find_duplicate(clean_content)
            if duplicate is not None and not allow_similar:
                print(
                    f"[Memory] duplicate skipped: id={duplicate.get('id')} category={duplicate.get('category')}",
                    flush=True,
                )
                return {
                    "status": "duplicate",
                    "memory": duplicate,
                    "similarity": similarity,
                }

            conflict = None
            if not allow_conflict:
                conflict = self.detect_conflict(clean_content, resolved_category)
            if conflict is not None:
                created = self._add_conflict(
                    old_memory=conflict,
                    new_content=clean_content,
                    category=resolved_category,
                    source=source,
                )
                print(
                    f"[Memory] conflict detected: id={created.get('id')} category={resolved_category}",
                    flush=True,
                )
                return {"status": "conflict", "conflict": created, "memory": conflict}

            now_text = self._now_text()
            resolved_scope = self.normalize_scope(
                scope or self.infer_scope(clean_content, resolved_category)
            )
            memory: Dict[str, object] = {
                "id": self._next_memory_id(),
                "uid": f"memory_{uuid4().hex}",
                "content": clean_content,
                "category": resolved_category,
                "created_at": now_text,
                "updated_at": now_text,
                "importance": self._normalize_importance(importance),
                "confidence": self._normalize_confidence(confidence),
                "last_used": None,
                "last_used_at": None,
                "use_count": 0,
                "status": "active",
                "source": str(source).strip() if source else None,
                "tags": self._normalize_tags(tags or self.extract_tags(clean_content)),
                "scope": resolved_scope,
                "location": str(location or self.extract_location(clean_content) or "").strip() or None,
                "valid_from": str(valid_from or "").strip() or None,
                "valid_until": str(valid_until or "").strip() or None,
                "last_confirmed_at": str(last_confirmed_at or now_text),
                "supersedes": supersedes,
                "contradicted_by": contradicted_by,
            }
            self.data.setdefault("memories", []).append(memory)
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                return {"status": "error", "memory": None}
        self.record_audit("create", memory.get("id"), None, memory, source or "memory_manager")
        return {"status": "added", "memory": deepcopy(memory)}

    def update_memory(
        self,
        memory_id: int,
        *,
        content: Optional[str] = None,
        category: Optional[str] = None,
        importance: Optional[int] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        scope: Optional[str] = None,
        location: Optional[str] = None,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            item = self._memory_ref(memory_id)
            if item is None:
                return None
            old_value = deepcopy(item)
            if content is not None and content.strip():
                item["content"] = content.strip()
                if tags is None:
                    item["tags"] = self.extract_tags(item["content"])
            if category is not None:
                item["category"] = self.normalize_category(category)
            if importance is not None:
                item["importance"] = self._normalize_importance(importance)
            if confidence is not None:
                item["confidence"] = self._normalize_confidence(confidence)
            if tags is not None:
                item["tags"] = self._normalize_tags(tags)
            if scope is not None:
                item["scope"] = self.normalize_scope(scope)
            if location is not None:
                item["location"] = str(location).strip() or None
            if valid_from is not None:
                item["valid_from"] = str(valid_from).strip() or None
            if valid_until is not None:
                item["valid_until"] = str(valid_until).strip() or None
            item["updated_at"] = self._now_text()
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                return None
            updated = deepcopy(item)
        self.record_audit("update", memory_id, old_value, updated, "memory_manager")
        return updated

    def delete_memory(self, memory_id: int) -> Optional[Dict[str, object]]:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            items = self.data.setdefault("memories", [])
            for index, item in enumerate(items):
                if isinstance(item, dict) and int(item.get("id", 0)) == int(memory_id):
                    removed = items.pop(index)
                    if not self.repository.save_memories(self.data):
                        self._refresh_memories()
                        return None
                    result = deepcopy(removed)
                    break
            else:
                return None
        self.record_audit("delete", memory_id, result, None, "memory_manager")
        return result

    def delete_memories(
        self,
        memory_ids: List[int],
        *,
        source: str = "memory_manager",
    ) -> List[Dict[str, object]]:
        targets = {int(value) for value in memory_ids if int(value) > 0}
        if not targets:
            return []
        with self.repository.transaction("memory"):
            self._refresh_memories()
            items = self.data.setdefault("memories", [])
            removed = [
                deepcopy(item)
                for item in items
                if isinstance(item, dict) and int(item.get("id", 0)) in targets
            ]
            self.data["memories"] = [
                item
                for item in items
                if not isinstance(item, dict) or int(item.get("id", 0)) not in targets
            ]
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                return []
        for item in removed:
            self.record_audit(
                "delete",
                int(item.get("id", 0)),
                item,
                None,
                source,
            )
        return removed

    def archive(self, memory_id: int) -> Optional[Dict[str, object]]:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            item = self._memory_ref(memory_id)
            if item is None:
                return None
            old_value = deepcopy(item)
            item["status"] = "archived"
            item["updated_at"] = self._now_text()
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                return None
            updated = deepcopy(item)
        self.record_audit("archive", memory_id, old_value, updated, "memory_manager")
        print(
            f"[Memory] archived: id={updated.get('id')} category={updated.get('category')}",
            flush=True,
        )
        return updated

    def restore(self, memory_id: int) -> Optional[Dict[str, object]]:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            item = self._memory_ref(memory_id)
            if item is None:
                return None
            old_value = deepcopy(item)
            item["status"] = "active"
            item["updated_at"] = self._now_text()
            if not self.repository.save_memories(self.data):
                self._refresh_memories()
                return None
            updated = deepcopy(item)
        self.record_audit("restore", memory_id, old_value, updated, "memory_manager")
        print(
            f"[Memory] restored: id={updated.get('id')} category={updated.get('category')}",
            flush=True,
        )
        return updated

    def mark_used(self, memory_ids: List[int]) -> None:
        with self.repository.transaction("memory"):
            self._refresh_memories()
            changed = False
            now_text = self._now_text()
            for memory_id in set(int(value) for value in memory_ids):
                item = self._memory_ref(memory_id)
                if item is None or item.get("status") != "active":
                    continue
                item["last_used"] = now_text
                item["last_used_at"] = now_text
                item["use_count"] = int(item.get("use_count", 0)) + 1
                changed = True
            if changed and not self.repository.save_memories(self.data):
                self._refresh_memories()

    def search(
        self,
        query: str,
        *,
        status: Optional[str] = "active",
        category: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        clean_query = self.normalize_text(query)
        print("[Memory] search", flush=True)
        if not clean_query:
            return self.memories(status=status, category=category)
        matches = []
        query_tags = set(self.extract_tags(query))
        for item in self.memories(status=status, category=category):
            content = str(item.get("content", ""))
            normalized = self.normalize_text(content)
            tag_overlap = query_tags & set(item.get("tags", []))
            similarity = SequenceMatcher(None, clean_query, normalized).ratio()
            if clean_query in normalized or normalized in clean_query or tag_overlap or similarity >= 0.45:
                item["match_score"] = round(
                    similarity + min(0.3, len(tag_overlap) * 0.1), 4
                )
                matches.append(item)
        matches.sort(key=lambda item: float(item.get("match_score", 0)), reverse=True)
        return matches

    def find_duplicate(
        self, content: str, category: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, object]], float]:
        best = None
        best_score = 0.0
        normalized = self.normalize_text(content)
        semantic = self.semantic_core(content)
        for item in self.memories(status="active", category=category):
            existing = str(item.get("content", ""))
            existing_normalized = self.normalize_text(existing)
            existing_semantic = self.semantic_core(existing)
            score = SequenceMatcher(None, normalized, existing_normalized).ratio()
            if normalized == existing_normalized:
                score = 1.0
            elif semantic and semantic == existing_semantic:
                score = max(score, 0.96)
            elif (
                len(semantic) >= 4
                and len(existing_semantic) >= 4
                and (semantic in existing_semantic or existing_semantic in semantic)
            ):
                score = max(score, 0.9)
            if score > best_score:
                best = item
                best_score = score
        if best_score >= 0.82:
            return best, best_score
        return None, best_score

    def detect_conflict(
        self, content: str, category: str
    ) -> Optional[Dict[str, object]]:
        for item in self.memories(status="active", category=category):
            existing = str(item.get("content", ""))
            if self._texts_conflict(existing, content):
                return item
        return None

    def conflicts(self, status: Optional[str] = "pending") -> List[Dict[str, object]]:
        self._refresh_conflicts()
        items = [
            deepcopy(item)
            for item in self.conflict_data.get("conflicts", [])
            if isinstance(item, dict)
        ]
        if status is None:
            return items
        return [item for item in items if item.get("status") == status]

    def get_conflict(self, conflict_id: int) -> Optional[Dict[str, object]]:
        self._refresh_conflicts()
        item = self._conflict_ref(conflict_id)
        return deepcopy(item) if item is not None else None

    def resolve_conflict(
        self,
        conflict_id: int,
        resolution: str,
        *,
        merged_content: Optional[str] = None,
        source: str = "user_confirmation",
    ) -> Dict[str, object]:
        self._refresh_conflicts()
        item = self._conflict_ref(conflict_id)
        if item is None or item.get("status") != "pending":
            return {"status": "missing"}
        if resolution == "defer":
            self.record_audit(
                "defer_conflict",
                f"conflict:{conflict_id}",
                item,
                item,
                source,
            )
            return {"status": "deferred", "conflict": deepcopy(item)}
        old_id = int(item.get("old_memory_id", 0))
        new_content = str(item.get("new_content", ""))
        category = str(item.get("category", "other"))
        result: Dict[str, object] = {"status": "resolved"}
        if resolution == "use_new":
            self.archive(old_id)
            result = self.add_memory(
                new_content,
                category=category,
                source=item.get("source"),
                allow_conflict=True,
                allow_similar=True,
            )
            if result.get("status") == "error":
                self.restore(old_id)
                return result
        elif resolution == "keep_both":
            result = self.add_memory(
                new_content,
                category=category,
                source=item.get("source"),
                allow_conflict=True,
                allow_similar=True,
            )
            if result.get("status") == "error":
                return result
        elif resolution == "merge":
            clean_merged = str(merged_content or "").strip()
            if not clean_merged:
                return {"status": "invalid", "error": "merged_content_required"}
            updated = self.update_memory(
                old_id,
                content=clean_merged,
                category=category,
            )
            if updated is None:
                return {"status": "error", "error": "memory_update_failed"}
            result = {"status": "merged", "memory": updated}
        elif resolution != "keep_old":
            return {"status": "invalid"}
        with self.repository.transaction("conflicts"):
            self._refresh_conflicts()
            current = self._conflict_ref(conflict_id)
            if current is None or current.get("status") != "pending":
                return {"status": "missing"}
            old_conflict = deepcopy(current)
            current["status"] = "resolved"
            current["resolution"] = resolution
            current["resolved_at"] = self._now_text()
            if not self._save_conflicts():
                self._refresh_conflicts()
                return {"status": "error", "error": "conflict_save_failed"}
            resolved_conflict = deepcopy(current)
        self.record_audit(
            "resolve_conflict",
            old_id,
            old_conflict,
            resolved_conflict,
            source,
        )
        return result

    def create_relation_conflict(
        self,
        old_memory: Dict[str, object],
        new_content: str,
        category: str,
        *,
        source: Optional[str] = None,
        relation: str = "mergeable",
    ) -> Dict[str, object]:
        return self._add_conflict(
            old_memory=old_memory,
            new_content=new_content,
            category=self.normalize_category(category),
            source=source,
            relation=relation,
        )

    def audit_entries(self, limit: int = 100) -> List[Dict[str, object]]:
        self._refresh_audit()
        entries = self.audit_data.get("entries", [])
        if not isinstance(entries, list):
            return []
        safe_limit = max(1, min(int(limit), 500))
        return [deepcopy(item) for item in entries[-safe_limit:] if isinstance(item, dict)]

    def record_audit(
        self,
        action: str,
        memory_id,
        old_value,
        new_value,
        source: str,
    ) -> bool:
        with self.repository.transaction("audit"):
            self._refresh_audit()
            entries = self.audit_data.setdefault("entries", [])
            entry = {
                "id": max(
                    (self._safe_int(item.get("id"), 0) for item in entries if isinstance(item, dict)),
                    default=0,
                ) + 1,
                "uid": f"audit_{uuid4().hex}",
                "action": str(action).strip() or "unknown",
                "memory_id": memory_id,
                "old_value": deepcopy(old_value),
                "new_value": deepcopy(new_value),
                "timestamp": self._now_text(),
                "source": str(source).strip() or "unknown",
            }
            entries.append(entry)
            if not self.repository.save_audit(self.audit_data):
                self._refresh_audit()
                return False
        print(
            f"[MemoryAudit] action={entry['action']} id={entry['memory_id']}",
            flush=True,
        )
        return True

    def organize_suggestions(self) -> Dict[str, List[object]]:
        active = self.memories("active")
        duplicates = []
        for index, first in enumerate(active):
            for second in active[index + 1 :]:
                if first.get("category") != second.get("category"):
                    continue
                score = self._similarity(
                    str(first.get("content", "")), str(second.get("content", ""))
                )
                if score >= 0.82:
                    duplicates.append(
                        {"ids": [first.get("id"), second.get("id")], "score": round(score, 2)}
                    )
        stale = []
        now = self.now_provider()
        for item in active:
            used_at = self._parse_time(
                item.get("last_used")
                or item.get("last_used_at")
                or item.get("updated_at")
            )
            if used_at is not None and (now - used_at).days >= 365:
                stale.append(item.get("id"))
        vague = [item.get("id") for item in active if len(self.normalize_text(str(item.get("content", "")))) < 6]
        return {
            "duplicates": duplicates,
            "stale": stale,
            "conflicts": [item.get("id") for item in self.conflicts("pending")],
            "vague": vague,
        }

    def save(self) -> bool:
        self.data["version"] = 2
        with self.repository.transaction("memory"):
            return self.repository.save_memories(self.data)

    @classmethod
    def classify(cls, content: str) -> str:
        text = str(content).lower()
        rules = (
            ("health", ("健康", "肠胃", "过敏", "睡不着", "睡眠", "药", "疼", "油腻")),
            ("relationship", ("家人", "朋友", "同事", "伴侣", "父母", "老师", "重要的人")),
            ("rule", ("不要", "必须", "禁止", "规则", "以后要", "提醒我")),
            ("goal", ("目标", "以后想", "我想成为", "长期", "计划做")),
            ("habit", ("一般", "通常", "习惯", "每天", "周末", "效率高")),
            ("preference", ("喜欢", "不喜欢", "偏好", "更适合", "更愿意")),
            ("project", ("项目", "roxyplan", "codex", "代码", "产品", "桌宠", "github")),
            ("learning", ("学习", "复习", "课程", "机器学习", "编程", "阅读", "练习")),
        )
        for category, keywords in rules:
            if any(keyword in text for keyword in keywords):
                return category
        return "other"

    @classmethod
    def extract_tags(cls, content: str) -> List[str]:
        text = unicodedata.normalize("NFKC", str(content)).lower()
        values = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)
        tags: List[str] = []
        for value in values:
            if value not in tags:
                tags.append(value)
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", value):
                for index in range(len(value) - 1):
                    pair = value[index : index + 2]
                    if pair not in tags:
                        tags.append(pair)
        return tags[:20]

    @classmethod
    def normalize_text(cls, content: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(content)).lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)

    @classmethod
    def semantic_core(cls, content: str) -> str:
        text = cls.normalize_text(content)
        for phrase in (
            "我现在",
            "我一般",
            "我通常",
            "我比较",
            "我喜欢",
            "我不喜欢",
            "我更适合",
            "我适合",
            "效率更高",
            "效率高",
            "以后",
            "请记住",
            "帮我记住",
        ):
            text = text.replace(phrase, "")
        return text.lstrip("我")

    @staticmethod
    def normalize_category(category: str) -> str:
        mapping = {
            "user_preference": "preference",
            "long_term_goal": "goal",
            "stable_habit": "habit",
            "routine": "habit",
            "health_lifestyle": "health",
            "project_preference": "project",
            "constraint": "rule",
        }
        value = mapping.get(str(category).strip(), str(category).strip())
        return value if value in MEMORY_CATEGORIES else "other"

    @staticmethod
    def normalize_scope(scope: object) -> str:
        value = str(scope or "").strip()
        return value if value in MEMORY_SCOPES else "stable_identity"

    @classmethod
    def infer_scope(cls, content: str, category: str = "other") -> str:
        text = str(content)
        if re.search(r"(?:现在|目前|当前|正在|已经回到|已经回)", text):
            return "current_state"
        if re.search(r"(?:以前|曾经|过去|之前在)", text):
            return "historical_state"
        if re.search(r"(?:以后|将来|未来|想去|准备以后|希望有一天)", text):
            return "future_intent"
        if category == "preference":
            return "preference"
        if category == "rule":
            return "constraint"
        if re.search(r"(?:今天|这会儿|刚才|暂时)", text):
            return "temporary_state"
        return "stable_identity"

    @staticmethod
    def extract_location(content: str) -> Optional[str]:
        text = str(content).strip().strip("。.!！?？")
        patterns = (
            r"(?:现在|目前|当前)(?:正在)?(?:在|住在|位于|已经回到|已经回)\s*([\u4e00-\u9fff]{2,8}?)(?:上学|实习|工作|生活|了|$)",
            r"(?:我)?来自\s*([\u4e00-\u9fff]{2,8}?)(?:$|。|，|,)",
            r"我是\s*([\u4e00-\u9fff]{2,8}?)(?:人|的)(?:$|。|，|,)",
            r"(?:老家|家乡)(?:在|是)\s*([\u4e00-\u9fff]{2,8})(?:$|。)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip() or None
        return None

    def _load_or_create_memory(self) -> Dict[str, object]:
        if not self.repository.memory_exists():
            data = deepcopy(self.default_data)
            self.repository.save_memories(data)
            return data
        loaded = self.repository.load_memories(self.default_data)
        if not isinstance(loaded, dict):
            return deepcopy(self.default_data)
        if not self._needs_migration(loaded):
            return self._normalize_loaded(loaded)

        migrated = self._migrate_data(loaded)
        backup_path = self._create_backup()
        if backup_path is None:
            return loaded
        if not self.repository.save_memories(migrated):
            return loaded
        self.last_backup_path = backup_path
        print("[Memory] migrated", flush=True)
        return migrated

    def _migrate_data(self, loaded: Dict[str, object]) -> Dict[str, object]:
        migrated = deepcopy(loaded)
        migrated["version"] = 2
        if not isinstance(migrated.get("profile"), dict):
            migrated["profile"] = {}
        old_items = migrated.get("memories", [])
        if not isinstance(old_items, list):
            old_items = []
        normalized = []
        used_ids = set()
        next_id = 1
        now_text = self._now_text()
        for raw in old_items:
            if isinstance(raw, dict):
                content = str(raw.get("content", "")).strip()
                source = raw.get("source")
                created_at = raw.get("created_at") or now_text
                requested_id = self._safe_int(raw.get("id"), 0)
            else:
                content = str(raw).strip()
                source = None
                created_at = now_text
                requested_id = 0
            if not content:
                continue
            memory_id = requested_id if requested_id > 0 and requested_id not in used_ids else next_id
            while memory_id in used_ids:
                memory_id += 1
            used_ids.add(memory_id)
            next_id = max(next_id, memory_id + 1)
            category = self.normalize_category(
                str(raw.get("category", "")) if isinstance(raw, dict) else ""
            )
            if category == "other":
                category = self.classify(content)
            normalized.append(
                {
                    "id": memory_id,
                    "uid": (
                        str(raw.get("uid", "")).strip()
                        if isinstance(raw, dict) and raw.get("uid")
                        else f"memory_{uuid4().hex}"
                    ),
                    "content": content,
                    "category": category,
                    "created_at": created_at,
                    "updated_at": raw.get("updated_at", created_at) if isinstance(raw, dict) else created_at,
                    "importance": self._normalize_importance(raw.get("importance", 3) if isinstance(raw, dict) else 3),
                    "confidence": self._normalize_confidence(raw.get("confidence", 1.0) if isinstance(raw, dict) else 1.0),
                    "last_used": (
                        raw.get("last_used", raw.get("last_used_at"))
                        if isinstance(raw, dict)
                        else None
                    ),
                    "last_used_at": (
                        raw.get("last_used_at", raw.get("last_used"))
                        if isinstance(raw, dict)
                        else None
                    ),
                    "use_count": self._safe_int(raw.get("use_count"), 0) if isinstance(raw, dict) else 0,
                    "status": raw.get("status") if isinstance(raw, dict) and raw.get("status") in MEMORY_STATUSES else "active",
                    "source": source,
                    "tags": self._normalize_tags(raw.get("tags", []) if isinstance(raw, dict) else self.extract_tags(content)),
                    "scope": self.normalize_scope(
                        (
                            raw.get("scope")
                            if isinstance(raw, dict)
                            else None
                        )
                        or self.infer_scope(content, category)
                    ),
                    "location": (
                        (
                            str(raw.get("location", "")).strip()
                            or self.extract_location(content)
                        )
                        if isinstance(raw, dict)
                        else self.extract_location(content)
                    ),
                    "valid_from": raw.get("valid_from") if isinstance(raw, dict) else None,
                    "valid_until": raw.get("valid_until") if isinstance(raw, dict) else None,
                    "last_confirmed_at": (
                        raw.get("last_confirmed_at", created_at)
                        if isinstance(raw, dict)
                        else created_at
                    ),
                    "supersedes": raw.get("supersedes") if isinstance(raw, dict) else None,
                    "contradicted_by": raw.get("contradicted_by") if isinstance(raw, dict) else None,
                }
            )
        migrated["memories"] = normalized
        return migrated

    def _normalize_loaded(self, loaded: Dict[str, object]) -> Dict[str, object]:
        normalized = deepcopy(loaded)
        normalized.setdefault("profile", {})
        normalized.setdefault("memories", [])
        now_text = self._now_text()
        for item in normalized.get("memories", []):
            if not isinstance(item, dict):
                continue
            created_at = item.get("created_at") or item.get("updated_at") or now_text
            last_used = item.get("last_used") or item.get("last_used_at")
            item.setdefault("uid", f"memory_{uuid4().hex}")
            item["category"] = self.normalize_category(
                str(item.get("category", "other"))
            )
            item["importance"] = self._normalize_importance(
                item.get("importance", 3)
            )
            item["confidence"] = self._normalize_confidence(
                item.get("confidence", 1.0)
            )
            item["created_at"] = created_at
            item.setdefault("updated_at", created_at)
            item["last_used"] = last_used
            item["last_used_at"] = item.get("last_used_at") or last_used
            item["status"] = (
                item.get("status")
                if item.get("status") in MEMORY_STATUSES
                else "active"
            )
            item["use_count"] = self._safe_int(item.get("use_count"), 0)
            item["tags"] = self._normalize_tags(
                item.get("tags", self.extract_tags(str(item.get("content", ""))))
            )
            item["scope"] = self.normalize_scope(
                item.get("scope")
                or self.infer_scope(
                    str(item.get("content", "")), str(item.get("category", "other"))
                )
            )
            item["location"] = (
                str(item.get("location", "")).strip()
                or self.extract_location(str(item.get("content", "")))
                or None
            )
            item.setdefault("valid_from", None)
            item.setdefault("valid_until", None)
            item.setdefault("last_confirmed_at", item.get("updated_at") or created_at)
            item.setdefault("supersedes", None)
            item.setdefault("contradicted_by", None)
        return normalized

    def _needs_migration(self, loaded: Dict[str, object]) -> bool:
        if self._safe_int(loaded.get("version"), 1) < 2:
            return True
        items = loaded.get("memories", [])
        if not isinstance(items, list):
            return True
        required = {"id", "content", "category", "importance", "status", "use_count"}
        return any(not isinstance(item, dict) or not required.issubset(item) for item in items)

    def _create_backup(self) -> Optional[Path]:
        timestamp = self.now_provider().strftime("%Y%m%d_%H%M%S")
        return self.repository.create_backup(timestamp)

    def _load_or_create_conflicts(self) -> Dict[str, object]:
        fallback = {"version": 1, "conflicts": []}
        if not self.repository.conflict_exists():
            self.repository.save_conflicts(fallback)
            return fallback
        loaded = self.repository.load_conflicts(fallback)
        if isinstance(loaded, dict) and isinstance(loaded.get("conflicts"), list):
            return loaded
        return fallback

    def _load_or_create_audit(self) -> Dict[str, object]:
        fallback = {"version": 1, "entries": []}
        if not self.repository.audit_exists():
            self.repository.save_audit(fallback)
            return fallback
        loaded = self.repository.load_audit(fallback)
        if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
            return loaded
        return fallback

    def _refresh_memories(self) -> None:
        loaded = self.repository.load_memories(self.default_data)
        if not isinstance(loaded, dict):
            refreshed = deepcopy(self.default_data)
        elif self._needs_migration(loaded):
            refreshed = self._migrate_data(loaded)
        else:
            refreshed = self._normalize_loaded(loaded)
        if isinstance(getattr(self, "data", None), dict):
            self.data.clear()
            self.data.update(refreshed)
        else:
            self.data = refreshed

    def _refresh_conflicts(self) -> None:
        fallback = {"version": 1, "conflicts": []}
        loaded = self.repository.load_conflicts(fallback)
        self.conflict_data = (
            loaded
            if isinstance(loaded, dict) and isinstance(loaded.get("conflicts"), list)
            else fallback
        )

    def _refresh_audit(self) -> None:
        fallback = {"version": 1, "entries": []}
        loaded = self.repository.load_audit(fallback)
        self.audit_data = (
            loaded
            if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list)
            else fallback
        )

    def _add_conflict(
        self,
        *,
        old_memory: Dict[str, object],
        new_content: str,
        category: str,
        source: Optional[str],
        relation: str = "conflict",
    ) -> Dict[str, object]:
        with self.repository.transaction("conflicts"):
            self._refresh_conflicts()
            for item in self.conflicts("pending"):
                if (
                    int(item.get("old_memory_id", 0)) == int(old_memory.get("id", 0))
                    and self.normalize_text(str(item.get("new_content", ""))) == self.normalize_text(new_content)
                ):
                    return item
            items = self.conflict_data.setdefault("conflicts", [])
            conflict = {
                "id": max((self._safe_int(item.get("id"), 0) for item in items if isinstance(item, dict)), default=0) + 1,
                "uid": f"conflict_{uuid4().hex}",
                "old_memory_id": int(old_memory.get("id", 0)),
                "new_content": new_content,
                "category": category,
                "source": str(source).strip() if source else None,
                "relation": relation,
                "created_at": self._now_text(),
                "status": "pending",
                "resolution": None,
                "resolved_at": None,
            }
            items.append(conflict)
            if not self._save_conflicts():
                self._refresh_conflicts()
                return {"status": "error"}
            return deepcopy(conflict)

    def _save_conflicts(self) -> bool:
        return self.repository.save_conflicts(self.conflict_data)

    def _memory_ref(self, memory_id: int) -> Optional[Dict[str, object]]:
        for item in self.data.setdefault("memories", []):
            if isinstance(item, dict) and self._safe_int(item.get("id"), 0) == int(memory_id):
                return item
        return None

    def _conflict_ref(self, conflict_id: int) -> Optional[Dict[str, object]]:
        for item in self.conflict_data.setdefault("conflicts", []):
            if isinstance(item, dict) and self._safe_int(item.get("id"), 0) == int(conflict_id):
                return item
        return None

    def _next_memory_id(self) -> int:
        return max((self._safe_int(item.get("id"), 0) for item in self.data.get("memories", []) if isinstance(item, dict)), default=0) + 1

    def _texts_conflict(self, old: str, new: str) -> bool:
        old_n = self.normalize_text(old)
        new_n = self.normalize_text(new)
        contrast_pairs = (
            ("早上", "晚上"),
            ("早晨", "晚上"),
            ("早晨", "夜晚"),
            ("白天", "夜里"),
            ("喜欢", "不喜欢"),
            ("适合", "不适合"),
            ("想要", "不想要"),
            ("开启", "关闭"),
            ("可以", "不要"),
        )
        has_contrast = any(
            (left in old_n and right in new_n) or (right in old_n and left in new_n)
            for left, right in contrast_pairs
        )
        if not has_contrast:
            return False
        topic_old = old_n
        topic_new = new_n
        for left, right in contrast_pairs:
            topic_old = topic_old.replace(left, "").replace(right, "")
            topic_new = topic_new.replace(left, "").replace(right, "")
        topic_old = self.semantic_core(topic_old)
        topic_new = self.semantic_core(topic_new)
        common = set(self.extract_tags(topic_old)) & set(self.extract_tags(topic_new))
        return bool(common) or SequenceMatcher(None, topic_old, topic_new).ratio() >= 0.45

    def _similarity(self, first: str, second: str) -> float:
        normal = SequenceMatcher(None, self.normalize_text(first), self.normalize_text(second)).ratio()
        semantic = SequenceMatcher(None, self.semantic_core(first), self.semantic_core(second)).ratio()
        return max(normal, semantic)

    def _now_text(self) -> str:
        return self.now_provider().isoformat(timespec="seconds")

    @staticmethod
    def _normalize_tags(tags) -> List[str]:
        if not isinstance(tags, list):
            return []
        result = []
        for tag in tags:
            clean = str(tag).strip().lower()
            if clean and clean not in result:
                result.append(clean)
        return result[:20]

    @staticmethod
    def _normalize_importance(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 3
        return max(1, min(number, 5))

    @staticmethod
    def _normalize_confidence(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 1.0
        return max(0.0, min(number, 1.0))

    @staticmethod
    def _safe_int(value, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _parse_time(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except (TypeError, ValueError):
            return None
