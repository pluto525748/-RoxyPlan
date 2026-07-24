from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_governance import MemoryGovernanceService
from modules.memory_manager import MemoryManager
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository


MEMORY_OPERATION_STATUSES = {
    "success",
    "not_found",
    "ambiguous",
    "validation_error",
    "already_processed",
    "partial_success",
    "conflict",
    "confirmation_required",
    "failed",
}


@dataclass
class MemoryOperationResult:
    """Stable result contract shared by Qt, Agent tools, and Local Web."""

    success: bool
    status: str
    operation: str
    memory_id: Optional[int] = None
    candidate_id: Optional[int] = None
    content_summary: str = ""
    error_code: Optional[str] = None
    safe_message: str = ""
    data: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.success = bool(self.success)
        self.status = (
            str(self.status)
            if str(self.status) in MEMORY_OPERATION_STATUSES
            else "failed"
        )
        self.operation = str(self.operation).strip() or "memory_operation"
        self.memory_id = self._optional_positive_id(self.memory_id)
        self.candidate_id = self._optional_positive_id(self.candidate_id)
        self.content_summary = str(self.content_summary or "").strip()[:120]
        self.error_code = str(self.error_code).strip() if self.error_code else None
        self.safe_message = str(self.safe_message or "").strip()
        self.data = deepcopy(dict(self.data or {}))

    def to_dict(self) -> Dict[str, object]:
        return {
            "success": self.success,
            "status": self.status,
            "operation": self.operation,
            "memory_id": self.memory_id,
            "candidate_id": self.candidate_id,
            "content_summary": self.content_summary,
            "error_code": self.error_code,
            "safe_message": self.safe_message,
            "data": deepcopy(self.data),
        }

    @staticmethod
    def _optional_positive_id(value: object) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None


class MemoryService:
    """Single business entry for long-term memory and candidate governance."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        candidate_manager: Optional[MemoryCandidateManager] = None,
        governance: Optional[MemoryGovernanceService] = None,
        *,
        candidates_enabled: bool = True,
    ) -> None:
        self.memory_manager = memory_manager
        repository = memory_manager.repository
        if (
            candidate_manager is None
            or candidate_manager.repository is not repository
        ):
            candidate_manager = MemoryCandidateManager(repository=repository)
        self.candidate_manager = candidate_manager
        if (
            governance is None
            or governance.memory_manager is not memory_manager
            or governance.candidate_manager is not candidate_manager
        ):
            governance = MemoryGovernanceService(
                memory_manager,
                candidate_manager,
                enabled=candidates_enabled,
            )
        self.governance = governance
        self.repository = repository

    @classmethod
    def from_data_root(
        cls,
        project_root: Path,
        *,
        memory_file: Optional[Path] = None,
        private_dir: Optional[Path] = None,
        default_data: Optional[Dict[str, object]] = None,
        candidates_enabled: bool = True,
    ) -> "MemoryService":
        root = Path(project_root)
        private_dir = Path(private_dir or root / "data" / "private")
        resolved_memory_file = Path(memory_file or root / "memory.json")
        repository = LocalJsonMemoryRepository(
            resolved_memory_file,
            candidate_file=private_dir / "memory_candidates.json",
            conflict_file=private_dir / "memory_conflicts.json",
            backup_dir=private_dir / "backups",
            audit_file=private_dir / "memory_audit.json",
        )
        manager = MemoryManager(
            resolved_memory_file,
            backup_dir=private_dir / "backups",
            conflict_file=private_dir / "memory_conflicts.json",
            audit_file=private_dir / "memory_audit.json",
            default_data=default_data,
            repository=repository,
        )
        candidates = MemoryCandidateManager(repository=repository)
        governance = MemoryGovernanceService(
            manager,
            candidates,
            enabled=candidates_enabled,
        )
        return cls(manager, candidates, governance)

    def set_candidates_enabled(self, enabled: bool) -> None:
        self.governance.set_enabled(bool(enabled))

    def list_memories(
        self,
        *,
        status: Optional[str] = "active",
        category: Optional[str] = None,
    ) -> MemoryOperationResult:
        operation = "list_memories"
        if status not in {None, "active", "archived"}:
            return self._validation(operation, "invalid_status")
        try:
            memories = self.memory_manager.memories(status=status, category=category)
            return self._ok(operation, "已读取长期记忆。", {"memories": memories})
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_read_failed", "长期记忆暂时无法读取。")

    def get_memory(self, memory_id: object) -> MemoryOperationResult:
        operation = "get_memory"
        parsed = self._positive_id(memory_id)
        if parsed is None:
            return self._validation(operation, "invalid_memory_id")
        try:
            memory = self.memory_manager.get(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_read_failed", "这条记忆暂时无法读取。")
        if memory is None:
            return self._not_found(operation, memory_id=parsed)
        return self._ok(
            operation,
            "已找到这条长期记忆。",
            {"memory": memory},
            memory_id=parsed,
            content=memory.get("content"),
        )

    def search_memories(
        self,
        query: str,
        *,
        status: Optional[str] = "active",
        category: Optional[str] = None,
    ) -> MemoryOperationResult:
        operation = "search_memories"
        clean_query = str(query or "").strip()
        if not clean_query:
            return self._validation(operation, "empty_query")
        if status not in {None, "active", "archived"}:
            return self._validation(operation, "invalid_status")
        try:
            memories = self.memory_manager.search(
                clean_query,
                status=status,
                category=category,
            )
            return self._ok(operation, "记忆搜索完成。", {"memories": memories})
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_search_failed", "记忆搜索暂时不可用。")

    def create_candidate(
        self,
        content: str,
        *,
        source_text: str = "",
        source: str = "conversation",
        explicit: bool = True,
    ) -> MemoryOperationResult:
        operation = "create_memory_candidate"
        clean_content = str(content or "").strip()
        if not clean_content:
            return self._validation(operation, "empty_content")
        try:
            proposal = self.governance.propose_from_text(
                source_text or clean_content,
                explicit=explicit,
                source=source,
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_create_failed", "候选记忆暂时没有保存成功。")
        status = str(proposal.get("status", "error"))
        candidate = proposal.get("candidate")
        candidate = candidate if isinstance(candidate, dict) else {}
        candidate_id = candidate.get("id")
        if status == "added":
            return self._ok(
                operation,
                "已加入待确认记忆。",
                proposal,
                candidate_id=candidate_id,
                content=candidate.get("content", clean_content),
            )
        if status in {"duplicate_candidate", "duplicate"}:
            return MemoryOperationResult(
                True,
                "already_processed",
                operation,
                memory_id=proposal.get("duplicate_of"),
                candidate_id=candidate_id,
                content_summary=self._summary(candidate.get("content", clean_content)),
                safe_message="这条信息已经在长期记忆或待确认列表里了。",
                data=proposal,
            )
        if status in {"skipped", "skipped_sensitive"}:
            return self._validation(operation, status)
        return self._failed(
            operation,
            status or "candidate_create_failed",
            "候选记忆暂时没有保存成功。",
            data=proposal,
        )

    def list_candidates(
        self, *, status: Optional[str] = "pending"
    ) -> MemoryOperationResult:
        operation = "list_memory_candidates"
        if status not in {None, "pending", "accepted", "rejected"}:
            return self._validation(operation, "invalid_status")
        try:
            candidates = self.candidate_manager.candidates(status)
            return self._ok(
                operation,
                "已读取记忆候选。",
                {"candidates": candidates},
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_read_failed", "记忆候选暂时无法读取。")

    def get_candidate(self, candidate_id: object) -> MemoryOperationResult:
        operation = "get_memory_candidate"
        parsed = self._positive_id(candidate_id)
        if parsed is None:
            return self._validation(operation, "invalid_candidate_id")
        try:
            candidate = self.candidate_manager.get(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_read_failed", "这条候选记忆暂时无法读取。")
        if candidate is None:
            return self._not_found(operation, candidate_id=parsed)
        return self._ok(
            operation,
            "已找到这条候选记忆。",
            {"candidate": candidate},
            candidate_id=parsed,
            content=candidate.get("content"),
        )

    def accept_candidate(
        self,
        candidate_id: object,
        *,
        edited_content: Optional[str] = None,
        source: str = "user_confirmation",
    ) -> MemoryOperationResult:
        operation = "accept_memory_candidate"
        parsed = self._positive_id(candidate_id)
        if parsed is None:
            return self._validation(operation, "invalid_candidate_id")
        try:
            current = self.candidate_manager.get(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(
                operation,
                "candidate_read_failed",
                "这条候选记忆暂时无法读取。",
                candidate_id=parsed,
            )
        if current is None:
            return self._not_found(operation, candidate_id=parsed)
        if current.get("status") != "pending":
            return MemoryOperationResult(
                False,
                "already_processed",
                operation,
                candidate_id=parsed,
                content_summary=self._summary(current.get("content")),
                error_code="candidate_already_processed",
                safe_message="这条候选记忆已经处理过了。",
                data={"candidate": current},
            )
        if edited_content is not None and not str(edited_content).strip():
            return self._validation(operation, "empty_content", candidate_id=parsed)
        try:
            outcome = self.governance.accept_candidate(
                parsed,
                edited_content=edited_content,
                source=source,
            )
        except (OSError, TypeError, ValueError):
            return self._failed(
                operation,
                "candidate_accept_failed",
                "候选记忆暂时没有确认成功。",
                candidate_id=parsed,
            )
        status = str(outcome.get("status", "error"))
        memory = outcome.get("memory")
        memory = memory if isinstance(memory, dict) else {}
        outcome_candidate = outcome.get("candidate")
        outcome_candidate = outcome_candidate if isinstance(outcome_candidate, dict) else {}
        content = (
            memory.get("content")
            or outcome_candidate.get("content")
            or current.get("content")
        )
        if status == "added":
            return self._ok(
                operation,
                "候选已保存为长期记忆。",
                outcome,
                memory_id=memory.get("id"),
                candidate_id=parsed,
                content=content,
            )
        if status == "duplicate":
            return MemoryOperationResult(
                True,
                "already_processed",
                operation,
                memory_id=memory.get("id"),
                candidate_id=parsed,
                content_summary=self._summary(content),
                safe_message="这条内容已经在长期记忆里，没有重复保存。",
                data=outcome,
            )
        if status == "conflict":
            return MemoryOperationResult(
                False,
                "conflict",
                operation,
                memory_id=memory.get("id"),
                candidate_id=parsed,
                content_summary=self._summary(content),
                error_code="memory_conflict",
                safe_message="这条候选与已有记忆存在差异，已转入冲突列表等待处理。",
                data=outcome,
            )
        if status in {"missing", "invalid"}:
            return self._not_found(operation, candidate_id=parsed)
        return self._failed(
            operation,
            str(outcome.get("error") or status or "candidate_accept_failed"),
            "候选记忆暂时没有确认成功。",
            candidate_id=parsed,
            data=outcome,
        )

    def reject_candidate(
        self,
        candidate_id: object,
        *,
        source: str = "user_confirmation",
    ) -> MemoryOperationResult:
        operation = "reject_memory_candidate"
        parsed = self._positive_id(candidate_id)
        if parsed is None:
            return self._validation(operation, "invalid_candidate_id")
        try:
            current = self.candidate_manager.get(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(
                operation,
                "candidate_read_failed",
                "这条候选记忆暂时无法读取。",
                candidate_id=parsed,
            )
        if current is None:
            return self._not_found(operation, candidate_id=parsed)
        if current.get("status") != "pending":
            return MemoryOperationResult(
                False,
                "already_processed",
                operation,
                candidate_id=parsed,
                content_summary=self._summary(current.get("content")),
                error_code="candidate_already_processed",
                safe_message="这条候选记忆已经处理过了。",
                data={"candidate": current},
            )
        try:
            outcome = self.governance.reject_candidate(parsed, source=source)
        except (OSError, TypeError, ValueError):
            return self._failed(
                operation,
                "candidate_reject_failed",
                "候选记忆暂时没有忽略成功。",
                candidate_id=parsed,
            )
        if outcome.get("status") == "rejected":
            return self._ok(
                operation,
                "候选已忽略，不会写入长期记忆。",
                outcome,
                candidate_id=parsed,
                content=current.get("content"),
            )
        return self._failed(
            operation,
            str(outcome.get("status") or "candidate_reject_failed"),
            "候选记忆暂时没有忽略成功。",
            candidate_id=parsed,
            data=outcome,
        )

    def accept_candidates(
        self,
        candidate_ids: object,
        *,
        source: str = "user_confirmation",
    ) -> MemoryOperationResult:
        return self._review_candidates(
            "accept_memory_candidates",
            candidate_ids,
            lambda candidate_id: self.accept_candidate(
                candidate_id,
                source=source,
            ),
            completed_key="accepted_ids",
            completed_message="候选记忆已加入长期记忆。",
        )

    def reject_candidates(
        self,
        candidate_ids: object,
        *,
        source: str = "user_confirmation",
    ) -> MemoryOperationResult:
        return self._review_candidates(
            "reject_memory_candidates",
            candidate_ids,
            lambda candidate_id: self.reject_candidate(
                candidate_id,
                source=source,
            ),
            completed_key="rejected_ids",
            completed_message="候选记忆已忽略。",
        )

    def update_memory(self, memory_id: object, **changes: object) -> MemoryOperationResult:
        operation = "update_memory"
        parsed = self._positive_id(memory_id)
        if parsed is None:
            return self._validation(operation, "invalid_memory_id")
        allowed = {
            "content",
            "category",
            "importance",
            "confidence",
            "tags",
            "scope",
            "location",
            "valid_from",
            "valid_until",
        }
        filtered = {key: value for key, value in changes.items() if key in allowed}
        if not filtered:
            return self._validation(operation, "empty_changes", memory_id=parsed)
        if "content" in filtered and not str(filtered["content"] or "").strip():
            return self._validation(operation, "empty_content", memory_id=parsed)
        try:
            updated = self.memory_manager.update_memory(parsed, **filtered)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_update_failed", "长期记忆暂时没有更新成功。", memory_id=parsed)
        if updated is None:
            return self._not_found(operation, memory_id=parsed)
        return self._ok(
            operation,
            "长期记忆已更新。",
            {"memory": updated},
            memory_id=parsed,
            content=updated.get("content"),
        )

    def archive_memory(self, memory_id: object) -> MemoryOperationResult:
        return self._change_status(memory_id, "archive_memory", "archived")

    def restore_memory(self, memory_id: object) -> MemoryOperationResult:
        return self._change_status(memory_id, "restore_memory", "active")

    def delete_memory(self, memory_id: object) -> MemoryOperationResult:
        operation = "delete_memory"
        parsed = self._positive_id(memory_id)
        if parsed is None:
            return self._validation(operation, "invalid_memory_id")
        try:
            removed = self.memory_manager.delete_memory(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_delete_failed", "长期记忆暂时没有删除成功。", memory_id=parsed)
        if removed is None:
            return self._not_found(operation, memory_id=parsed)
        return self._ok(
            operation,
            "长期记忆已删除。",
            {"memory": removed},
            memory_id=parsed,
            content=removed.get("content"),
        )

    def delete_all_memories(self) -> MemoryOperationResult:
        operation = "delete_all_memories"
        try:
            memories = self.memory_manager.memories(status=None)
            memory_ids = [
                int(item.get("id", 0))
                for item in memories
                if isinstance(item, dict) and int(item.get("id", 0)) > 0
            ]
            removed = self.memory_manager.delete_memories(
                memory_ids,
                source="confirmed_batch_delete",
            )
            remaining = self.memory_manager.memories(status=None)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_delete_failed", "长期记忆暂时没有删除成功。")
        if remaining or len(removed) != len(memory_ids):
            return self._failed(
                operation,
                "postcondition_failed",
                "长期记忆没有完整删除，请刷新后重试。",
                data={"deleted_count": len(removed)},
            )
        return self._ok(
            operation,
            "全部长期记忆已删除。",
            {"deleted_count": len(removed)},
        )

    def list_conflicts(self, *, status: str = "pending") -> MemoryOperationResult:
        operation = "list_memory_conflicts"
        if status not in {"pending", "resolved"}:
            return self._validation(operation, "invalid_status")
        try:
            conflicts = self.governance.conflicts(status)
            return self._ok(
                operation,
                "已读取记忆冲突。",
                {"conflicts": conflicts},
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "conflict_read_failed", "记忆冲突暂时无法读取。")

    def resolve_conflict(
        self,
        conflict_id: object,
        resolution: str,
        *,
        merged_content: Optional[str] = None,
        source: str = "user_confirmation",
    ) -> MemoryOperationResult:
        operation = "resolve_memory_conflict"
        parsed = self._positive_id(conflict_id)
        if parsed is None:
            return self._validation(operation, "invalid_conflict_id")
        choice = str(resolution or "").strip()
        if choice not in {"keep_old", "use_new", "merge", "keep_both", "defer"}:
            return self._validation(operation, "invalid_resolution")
        if choice == "merge" and not str(merged_content or "").strip():
            return self._validation(operation, "merged_content_required")
        try:
            outcome = self.governance.resolve_conflict(
                parsed,
                choice,
                merged_content=merged_content,
                source=source,
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "conflict_resolve_failed", "记忆冲突暂时没有处理成功。")
        status = str(outcome.get("status", "error"))
        if status in {"missing"}:
            return self._not_found(operation)
        if status in {"invalid", "error"}:
            return self._failed(
                operation,
                str(outcome.get("error") or status),
                "记忆冲突暂时没有处理成功。",
                data=outcome,
            )
        return self._ok(operation, "记忆冲突已处理。", outcome)

    def list_audit(self, *, limit: int = 100) -> MemoryOperationResult:
        operation = "list_memory_audit"
        try:
            entries = self.memory_manager.audit_entries(max(1, min(int(limit), 500)))
            return self._ok(operation, "已读取记忆操作记录。", {"entries": entries})
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "audit_read_failed", "记忆操作记录暂时无法读取。")

    def organize_suggestions(self) -> MemoryOperationResult:
        operation = "organize_memories"
        try:
            suggestions = self.memory_manager.organize_suggestions()
            return self._ok(
                operation,
                "记忆整理建议已生成，不会自动修改数据。",
                {"suggestions": suggestions},
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_organize_failed", "记忆整理建议暂时无法生成。")

    def delete_candidate(self, candidate_id: object) -> MemoryOperationResult:
        operation = "delete_memory_candidate"
        parsed = self._positive_id(candidate_id)
        if parsed is None:
            return self._validation(operation, "invalid_candidate_id")
        try:
            candidate = self.candidate_manager.delete(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_delete_failed", "候选记忆暂时没有删除成功。")
        if candidate is None:
            return self._not_found(operation, candidate_id=parsed)
        return self._ok(
            operation,
            "候选记忆已删除。",
            {"candidate": candidate},
            candidate_id=parsed,
            content=candidate.get("content"),
        )

    def clear_pending_candidates(self) -> MemoryOperationResult:
        operation = "clear_memory_candidates"
        try:
            pending_count = len(self.candidate_manager.pending())
            changed = self.candidate_manager.clear_pending()
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_clear_failed", "待确认记忆暂时没有清空成功。")
        if pending_count and changed != pending_count:
            return self._failed(operation, "candidate_clear_incomplete", "待确认记忆没有完整处理，请刷新后重试。")
        return self._ok(operation, "待确认记忆已清空。", {"changed_count": changed})

    def reject_low_value_candidates(self) -> MemoryOperationResult:
        operation = "reject_low_value_memory_candidates"
        try:
            outcome = self.governance.reject_low_value(source="user_confirmation")
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "candidate_batch_reject_failed", "低价值候选暂时没有处理成功。")
        return self._ok(operation, "低价值候选已处理。", outcome)

    def accept_all_pending(self, *, source: str = "user_confirmation") -> MemoryOperationResult:
        operation = "accept_all_memory_candidates"
        pending = self.list_candidates()
        if not pending.success:
            return self._failed(operation, pending.error_code or "candidate_read_failed", pending.safe_message)
        candidates = pending.data.get("candidates", [])
        candidate_ids = [
            int(item.get("id", 0))
            for item in candidates
            if isinstance(item, dict) and int(item.get("id", 0)) > 0
        ]
        if not candidate_ids:
            return self._ok(
                operation,
                "当前没有待审核候选。",
                {
                    "candidate_ids": [],
                    "accepted_ids": [],
                    "failed_ids": [],
                    "already_processed_ids": [],
                    "results": [],
                },
            )
        reviewed = self.accept_candidates(candidate_ids, source=source)
        reviewed.operation = operation
        reviewed.data.setdefault("candidate_ids", candidate_ids)
        return reviewed

    def _review_candidates(
        self,
        operation: str,
        candidate_ids: object,
        reviewer,
        *,
        completed_key: str,
        completed_message: str,
    ) -> MemoryOperationResult:
        ids = self._positive_ids(candidate_ids)
        if not ids:
            return self._validation(operation, "candidate_ids_required")
        results = [reviewer(candidate_id) for candidate_id in ids]
        completed_ids = [
            candidate_id
            for candidate_id, result in zip(ids, results)
            if result.success and result.status == "success"
        ]
        already_processed_ids = [
            candidate_id
            for candidate_id, result in zip(ids, results)
            if result.status == "already_processed"
        ]
        failed_ids = [
            candidate_id
            for candidate_id, result in zip(ids, results)
            if candidate_id not in completed_ids
            and candidate_id not in already_processed_ids
        ]
        data = {
            "candidate_ids": ids,
            completed_key: completed_ids,
            "failed_ids": failed_ids,
            "already_processed_ids": already_processed_ids,
            "results": [result.to_dict() for result in results],
        }
        if len(completed_ids) == len(ids):
            return self._ok(operation, completed_message, data)
        if completed_ids:
            return MemoryOperationResult(
                True,
                "partial_success",
                operation,
                safe_message="部分候选已经处理，其余候选保持原状态。",
                data=data,
            )
        if already_processed_ids and not failed_ids:
            return MemoryOperationResult(
                False,
                "already_processed",
                operation,
                error_code="candidates_already_processed",
                safe_message="这些候选已经处理过，没有重复写入。",
                data=data,
            )
        status = "not_found" if all(
            result.status == "not_found" for result in results
        ) else "failed"
        return MemoryOperationResult(
            False,
            status,
            operation,
            error_code=("not_found" if status == "not_found" else "candidate_batch_incomplete"),
            safe_message="没有找到可处理的候选，现有记忆没有改变。",
            data=data,
        )

    def _change_status(
        self, memory_id: object, operation: str, target_status: str
    ) -> MemoryOperationResult:
        parsed = self._positive_id(memory_id)
        if parsed is None:
            return self._validation(operation, "invalid_memory_id")
        try:
            current = self.memory_manager.get(parsed)
        except (OSError, TypeError, ValueError):
            return self._failed(
                operation,
                "memory_read_failed",
                "这条长期记忆暂时无法读取。",
                memory_id=parsed,
            )
        if current is None:
            return self._not_found(operation, memory_id=parsed)
        if current.get("status") == target_status:
            return MemoryOperationResult(
                True,
                "already_processed",
                operation,
                memory_id=parsed,
                content_summary=self._summary(current.get("content")),
                safe_message="这条长期记忆已经是目标状态。",
                data={"memory": current},
            )
        try:
            changed = (
                self.memory_manager.archive(parsed)
                if target_status == "archived"
                else self.memory_manager.restore(parsed)
            )
        except (OSError, TypeError, ValueError):
            return self._failed(operation, "memory_status_change_failed", "长期记忆状态暂时没有更新成功。")
        if changed is None:
            return self._failed(operation, "memory_status_change_failed", "长期记忆状态暂时没有更新成功。")
        message = "长期记忆已归档。" if target_status == "archived" else "长期记忆已恢复。"
        return self._ok(
            operation,
            message,
            {"memory": changed},
            memory_id=parsed,
            content=changed.get("content"),
        )

    @staticmethod
    def _positive_id(value: object) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _positive_ids(cls, values: object) -> List[int]:
        if not isinstance(values, (list, tuple)):
            return []
        result: List[int] = []
        for value in values:
            parsed = cls._positive_id(value)
            if parsed is not None and parsed not in result:
                result.append(parsed)
        return result

    @staticmethod
    def _summary(value: object) -> str:
        return str(value or "").strip()[:120]

    @classmethod
    def _ok(
        cls,
        operation: str,
        message: str,
        data: Optional[Dict[str, object]] = None,
        *,
        memory_id: object = None,
        candidate_id: object = None,
        content: object = "",
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            True,
            "success",
            operation,
            memory_id=memory_id,
            candidate_id=candidate_id,
            content_summary=cls._summary(content),
            safe_message=message,
            data=data or {},
        )

    @staticmethod
    def _validation(
        operation: str,
        error_code: str,
        *,
        memory_id: object = None,
        candidate_id: object = None,
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            False,
            "validation_error",
            operation,
            memory_id=memory_id,
            candidate_id=candidate_id,
            error_code=error_code,
            safe_message="输入内容不完整或格式不正确。",
        )

    @staticmethod
    def _not_found(
        operation: str,
        *,
        memory_id: object = None,
        candidate_id: object = None,
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            False,
            "not_found",
            operation,
            memory_id=memory_id,
            candidate_id=candidate_id,
            error_code="not_found",
            safe_message="没有找到对应的记忆记录。",
        )

    @staticmethod
    def _failed(
        operation: str,
        error_code: str,
        message: str,
        *,
        memory_id: object = None,
        candidate_id: object = None,
        data: Optional[Dict[str, object]] = None,
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            False,
            "failed",
            operation,
            memory_id=memory_id,
            candidate_id=candidate_id,
            error_code=error_code,
            safe_message=message,
            data=data or {},
        )
