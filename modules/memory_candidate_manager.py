from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from modules.repositories.memory_repository import MemoryRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_FILE = PROJECT_ROOT / "data" / "private" / "memory_candidates.json"
VALID_STATUSES = {"pending", "accepted", "rejected"}
VALID_SENSITIVITY = {"normal", "sensitive", "high"}


class MemoryCandidateManager:
    """Manage private memory candidates without writing long-term memory."""

    def __init__(
        self,
        file_path: Path = DEFAULT_CANDIDATE_FILE,
        now_provider: Callable[[], datetime] = datetime.now,
        repository: Optional[MemoryRepository] = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.now_provider = now_provider
        self.repository = repository or LocalJsonMemoryRepository(
            PROJECT_ROOT / "memory.json",
            candidate_file=self.file_path,
            conflict_file=PROJECT_ROOT / "data" / "private" / "memory_conflicts.json",
            backup_dir=PROJECT_ROOT / "data" / "private" / "backups",
        )
        self.file_path = getattr(self.repository, "candidate_file", self.file_path)
        self.data = self._load_or_create()

    def candidates(self, status: Optional[str] = None) -> List[Dict[str, object]]:
        self._refresh()
        items = self.data.get("candidates", [])
        if not isinstance(items, list):
            return []
        result = [self._candidate_view(item) for item in items if isinstance(item, dict)]
        if status is None:
            return result
        if status not in VALID_STATUSES:
            raise ValueError(f"Unknown memory candidate status: {status}")
        return [item for item in result if item.get("status") == status]

    def pending(self) -> List[Dict[str, object]]:
        return self.candidates("pending")

    def get(self, candidate_id: int) -> Optional[Dict[str, object]]:
        for item in self.candidates():
            if int(item.get("id", 0)) == int(candidate_id):
                return item
        return None

    def add_candidate(
        self,
        content: str,
        category: str,
        source_text: str,
        *,
        source: str = "conversation",
        confidence: float = 0.85,
        sensitivity: str = "normal",
        reason: str = "用户明确表达了可能长期稳定的信息",
        memory_fields: Optional[Dict[str, object]] = None,
    ) -> Tuple[Dict[str, object], bool]:
        clean_content = str(content).strip()
        if not clean_content:
            raise ValueError("Memory candidate content cannot be empty")

        with self.repository.transaction("candidates"):
            self._refresh()
            normalized = self._normalize_content(clean_content)
            for item in self.candidates():
                existing = str(item.get("content", ""))
                similarity = SequenceMatcher(
                    None,
                    self._semantic_core(existing),
                    self._semantic_core(clean_content),
                ).ratio()
                if (
                    self._normalize_content(existing) == normalized
                    or similarity >= 0.86
                ):
                    mutable = self._mutable_item(int(item.get("id", 0)))
                    if mutable is not None and mutable.get("status") == "pending":
                        previous_source = str(mutable.get("source_text", "")).strip()
                        incoming_source = str(source_text).strip()
                        if incoming_source and incoming_source not in previous_source:
                            mutable["source_text"] = "；".join(
                                value for value in (previous_source, incoming_source) if value
                            )[:1200]
                        mutable["confidence"] = max(
                            self._normalize_confidence(mutable.get("confidence", 0.0)),
                            self._normalize_confidence(confidence),
                        )
                        mutable["reason"] = "相似候选已合并来源，等待人工审核"
                        if not self._safe_save():
                            self._refresh()
                            raise OSError("Failed to merge memory candidate")
                        item = self._candidate_view(mutable)
                    print("[MemoryCandidate] duplicate skipped", flush=True)
                    return item, False

            items = self.data.setdefault("candidates", [])
            next_id = max(
                (int(item.get("id", 0)) for item in items if isinstance(item, dict)),
                default=0,
            ) + 1
            candidate: Dict[str, object] = {
                "id": next_id,
                "uid": f"candidate_{uuid4().hex}",
                "content": clean_content,
                "category": str(category).strip() or "other",
                "source": str(source).strip() or "conversation",
                "source_text": str(source_text).strip() or clean_content,
                "created_at": self._now_text(),
                "confidence": self._normalize_confidence(confidence),
                "sensitivity": (
                    sensitivity if sensitivity in VALID_SENSITIVITY else "normal"
                ),
                "status": "pending",
                "duplicate_of": None,
                "conflict_with": None,
                "reason": str(reason).strip(),
                "memory_fields": dict(memory_fields or {}),
                "accepted_at": None,
                "rejected_at": None,
            }
            items.append(candidate)
            if not self._safe_save():
                self._refresh()
                raise OSError("Failed to save memory candidate")
        print("[MemoryCandidate] add", flush=True)
        return dict(candidate), True

    def accept(
        self,
        candidate_id: int,
        *,
        content: Optional[str] = None,
        duplicate_of: Optional[int] = None,
        conflict_with: Optional[int] = None,
    ) -> Tuple[Optional[Dict[str, object]], bool]:
        with self.repository.transaction("candidates"):
            self._refresh()
            item = self._mutable_item(candidate_id)
            if item is None:
                return None, False
            if item.get("status") == "accepted":
                return self._candidate_view(item), False
            if item.get("status") != "pending":
                return self._candidate_view(item), False

            if content is not None and str(content).strip():
                item["content"] = str(content).strip()
            item["status"] = "accepted"
            item["accepted_at"] = self._now_text()
            item["rejected_at"] = None
            item["duplicate_of"] = duplicate_of
            item["conflict_with"] = conflict_with
            if not self._safe_save():
                self._refresh()
                return None, False
        print("[MemoryCandidate] accept", flush=True)
        return self._candidate_view(item), True

    def reject(self, candidate_id: int) -> Tuple[Optional[Dict[str, object]], bool]:
        with self.repository.transaction("candidates"):
            self._refresh()
            item = self._mutable_item(candidate_id)
            if item is None:
                return None, False
            if item.get("status") == "rejected":
                return self._candidate_view(item), False
            if item.get("status") != "pending":
                return self._candidate_view(item), False

            item["status"] = "rejected"
            item["rejected_at"] = self._now_text()
            item["accepted_at"] = None
            if not self._safe_save():
                self._refresh()
                return None, False
        print("[MemoryCandidate] reject", flush=True)
        return self._candidate_view(item), True

    def update_pending(
        self,
        candidate_id: int,
        *,
        content: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        with self.repository.transaction("candidates"):
            self._refresh()
            item = self._mutable_item(candidate_id)
            if item is None or item.get("status") != "pending":
                return None
            if content is not None:
                clean_content = str(content).strip()
                if not clean_content:
                    raise ValueError("Memory candidate content cannot be empty")
                item["content"] = clean_content
            if category is not None and str(category).strip():
                item["category"] = str(category).strip()
            if not self._safe_save():
                self._refresh()
                return None
            return self._candidate_view(item)

    def delete(self, candidate_id: int) -> Optional[Dict[str, object]]:
        with self.repository.transaction("candidates"):
            self._refresh()
            items = self.data.setdefault("candidates", [])
            for index, item in enumerate(items):
                if isinstance(item, dict) and int(item.get("id", 0)) == int(candidate_id):
                    removed = items.pop(index)
                    if not self._safe_save():
                        self._refresh()
                        return None
                    return dict(removed)
        return None

    def clear_pending(self) -> int:
        with self.repository.transaction("candidates"):
            self._refresh()
            now_text = self._now_text()
            changed = 0
            for item in self.data.setdefault("candidates", []):
                if not isinstance(item, dict) or item.get("status") != "pending":
                    continue
                item["status"] = "rejected"
                item["rejected_at"] = now_text
                item["accepted_at"] = None
                changed += 1
            if changed:
                if not self._safe_save():
                    self._refresh()
                    return 0
                print("[MemoryCandidate] reject", flush=True)
        return changed

    def _mutable_item(self, candidate_id: int) -> Optional[Dict[str, object]]:
        for item in self.data.setdefault("candidates", []):
            if isinstance(item, dict) and int(item.get("id", 0)) == int(candidate_id):
                return item
        return None

    def _load_or_create(self) -> Dict[str, object]:
        fallback: Dict[str, object] = {"version": 1, "candidates": []}
        if not self.repository.candidate_exists():
            self.data = fallback
            self._safe_save()
            return fallback

        loaded = self.repository.load_candidates(fallback)

        if not isinstance(loaded, dict):
            loaded = fallback
        loaded.setdefault("version", 1)
        if not isinstance(loaded.get("candidates"), list):
            loaded["candidates"] = []
        return loaded

    def _safe_save(self) -> bool:
        return self.repository.save_candidates(self.data)

    def _refresh(self) -> None:
        fallback: Dict[str, object] = {"version": 1, "candidates": []}
        loaded = self.repository.load_candidates(fallback)
        if not isinstance(loaded, dict):
            loaded = fallback
        loaded.setdefault("version", 1)
        if not isinstance(loaded.get("candidates"), list):
            loaded["candidates"] = []
        self.data = loaded

    def _now_text(self) -> str:
        return self.now_provider().isoformat(timespec="seconds")

    @staticmethod
    def _normalize_confidence(value: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return round(max(0.0, min(number, 1.0)), 3)

    def _candidate_view(self, item: Dict[str, object]) -> Dict[str, object]:
        result = dict(item)
        result.setdefault("source", "conversation")
        result.setdefault("source_text", str(result.get("content", "")))
        result["confidence"] = self._normalize_confidence(
            result.get("confidence", 0.75)
        )
        if result.get("sensitivity") not in VALID_SENSITIVITY:
            result["sensitivity"] = "normal"
        result.setdefault("duplicate_of", None)
        result.setdefault("conflict_with", None)
        result.setdefault("reason", "旧版候选记忆")
        result.setdefault("memory_fields", {})
        return result

    @staticmethod
    def _normalize_content(content: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(content)).lower().strip()
        normalized = re.sub(r"\s+", "", normalized)
        return normalized.strip("，,。.!！?？：:；;")

    @classmethod
    def _semantic_core(cls, content: str) -> str:
        text = cls._normalize_content(content)
        for phrase in (
            "我现在",
            "我一般",
            "我通常",
            "我喜欢",
            "我更适合",
            "效率更高",
            "效率高",
        ):
            text = text.replace(phrase, "")
        return text.lstrip("我")
