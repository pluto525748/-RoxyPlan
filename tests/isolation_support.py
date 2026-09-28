from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryService
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from modules.repositories.memory_repository import MemoryRepository


@dataclass(frozen=True)
class IsolatedMemoryPaths:
    root: Path
    memory_file: Path
    private_dir: Path
    candidate_file: Path
    conflict_file: Path
    audit_file: Path
    backup_dir: Path
    lock_dir: Path


@dataclass(frozen=True)
class IsolatedAppDataRoot:
    """One complete, disposable application-data layout for a test."""

    root: Path
    memory_file: Path
    private_dir: Path
    chat_history_file: Path
    chat_summaries_file: Path
    model_usage_file: Path
    today_plan_file: Path
    action_log_file: Path
    growth_log_file: Path
    candidate_file: Path
    conflict_file: Path
    audit_file: Path
    archive_file: Path
    backup_dir: Path
    lock_dir: Path


def make_isolated_app_data_root(root: Path) -> IsolatedAppDataRoot:
    """Create the complete private-data tree used by desktop and web tests.

    This intentionally mirrors the production ``<project>/data/private``
    layout so composition roots can receive one temporary project root without
    falling back to the real project directory.
    """

    stable_root = Path(root)
    private_dir = stable_root / "data" / "private"
    backup_dir = private_dir / "backups"
    lock_dir = private_dir / "locks"
    for directory in (private_dir, backup_dir, lock_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return IsolatedAppDataRoot(
        root=stable_root,
        memory_file=stable_root / "memory.json",
        private_dir=private_dir,
        chat_history_file=private_dir / "chat_history.json",
        chat_summaries_file=private_dir / "chat_summaries.json",
        model_usage_file=private_dir / "model_usage.json",
        today_plan_file=private_dir / "today_plan.json",
        action_log_file=private_dir / "action_log.json",
        growth_log_file=private_dir / "growth_log.json",
        candidate_file=private_dir / "memory_candidates.json",
        conflict_file=private_dir / "memory_conflicts.json",
        audit_file=private_dir / "memory_audit.json",
        archive_file=private_dir / "memory_archive.json",
        backup_dir=backup_dir,
        lock_dir=lock_dir,
    )


def make_isolated_memory_paths(root: Path) -> IsolatedMemoryPaths:
    stable_root = Path(root)
    private_dir = stable_root / "private"
    backup_dir = private_dir / "backups"
    lock_dir = private_dir / "locks"
    backup_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)
    return IsolatedMemoryPaths(
        root=stable_root,
        memory_file=stable_root / "memory.json",
        private_dir=private_dir,
        candidate_file=private_dir / "memory_candidates.json",
        conflict_file=private_dir / "memory_conflicts.json",
        audit_file=private_dir / "memory_audit.json",
        backup_dir=backup_dir,
        lock_dir=lock_dir,
    )


def build_isolated_memory_repository(
    paths: IsolatedMemoryPaths,
) -> LocalJsonMemoryRepository:
    return LocalJsonMemoryRepository(
        paths.memory_file,
        candidate_file=paths.candidate_file,
        conflict_file=paths.conflict_file,
        backup_dir=paths.backup_dir,
        audit_file=paths.audit_file,
    )


def build_isolated_memory_manager(
    root: Path,
    *,
    now_provider: Optional[Callable[[], datetime]] = None,
    default_data: Optional[Dict[str, object]] = None,
    repository: Optional[MemoryRepository] = None,
) -> MemoryManager:
    paths = make_isolated_memory_paths(root)
    shared_repository = repository or build_isolated_memory_repository(paths)
    kwargs = {}
    if now_provider is not None:
        kwargs["now_provider"] = now_provider
    return MemoryManager(
        paths.memory_file,
        backup_dir=paths.backup_dir,
        conflict_file=paths.conflict_file,
        audit_file=paths.audit_file,
        default_data=default_data,
        repository=shared_repository,
        **kwargs,
    )


def build_isolated_candidate_manager(
    root: Path,
    *,
    now_provider: Optional[Callable[[], datetime]] = None,
    repository: Optional[MemoryRepository] = None,
) -> MemoryCandidateManager:
    paths = make_isolated_memory_paths(root)
    shared_repository = repository or build_isolated_memory_repository(paths)
    kwargs = {}
    if now_provider is not None:
        kwargs["now_provider"] = now_provider
    return MemoryCandidateManager(repository=shared_repository, **kwargs)


def build_isolated_memory_service(
    root: Path,
    *,
    now_provider: Optional[Callable[[], datetime]] = None,
    default_data: Optional[Dict[str, object]] = None,
    candidates_enabled: bool = True,
) -> MemoryService:
    paths = make_isolated_memory_paths(root)
    repository = build_isolated_memory_repository(paths)
    manager = build_isolated_memory_manager(
        root,
        now_provider=now_provider,
        default_data=default_data,
        repository=repository,
    )
    candidates = build_isolated_candidate_manager(
        root,
        now_provider=now_provider,
        repository=repository,
    )
    return MemoryService(
        manager,
        candidates,
        candidates_enabled=candidates_enabled,
    )
