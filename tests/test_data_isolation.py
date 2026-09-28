from pathlib import Path

import pytest

from modules.chat_history_manager import ChatHistoryManager
from modules.growth import ActionLogStore, GrowthLogStore, GrowthService
from modules.growth_manager import GrowthManager
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryService
from modules.today_plan import TodayPlanStore
from tests.isolation_support import IsolatedAppDataRoot, IsolatedMemoryPaths
from tests.private_data_guard import (
    PROJECT_ROOT,
    RepositoryPathViolation,
    assert_real_private_data_unchanged,
    assert_write_target_is_test_isolated,
    snapshot_paths,
)


def test_isolated_memory_fixture_uses_one_temporary_repository(
    isolated_memory_paths: IsolatedMemoryPaths,
    isolated_memory_service: MemoryService,
):
    repository = isolated_memory_service.repository
    assert isolated_memory_service.memory_manager.repository is repository
    assert isolated_memory_service.candidate_manager.repository is repository
    assert repository.memory_file == isolated_memory_paths.memory_file
    assert repository.candidate_file == isolated_memory_paths.candidate_file
    assert repository.conflict_file == isolated_memory_paths.conflict_file
    assert repository.audit_file == isolated_memory_paths.audit_file
    assert repository.backup_dir == isolated_memory_paths.backup_dir
    assert repository.lock_dir == isolated_memory_paths.lock_dir
    for path in (
        repository.memory_file,
        repository.candidate_file,
        repository.conflict_file,
        repository.audit_file,
        repository.backup_dir,
        repository.lock_dir,
    ):
        path.resolve().relative_to(isolated_memory_paths.root.resolve())


def test_incomplete_memory_manager_is_rejected_before_real_default_paths_are_opened(
    tmp_path: Path,
):
    with pytest.raises(RepositoryPathViolation, match="complete temporary Repository"):
        MemoryManager(tmp_path / "memory.json")


def test_incomplete_candidate_manager_is_rejected_before_real_paths_are_opened(
    tmp_path: Path,
):
    with pytest.raises(RepositoryPathViolation, match="complete temporary Repository"):
        MemoryCandidateManager(tmp_path / "memory_candidates.json")


def test_other_local_stores_reject_real_project_private_paths():
    with pytest.raises(RepositoryPathViolation):
        GrowthManager(PROJECT_ROOT / "data" / "private")
    with pytest.raises(RepositoryPathViolation):
        ChatHistoryManager(PROJECT_ROOT / "data" / "private")
    with pytest.raises(RepositoryPathViolation):
        SecretStore(PROJECT_ROOT)
    with pytest.raises(RepositoryPathViolation):
        ModelUsageStore(PROJECT_ROOT)


def test_legacy_direct_stores_reject_real_default_files_before_opening_them():
    with pytest.raises(RepositoryPathViolation):
        TodayPlanStore(PROJECT_ROOT / "data" / "today_plan.json")
    with pytest.raises(RepositoryPathViolation):
        ActionLogStore(PROJECT_ROOT / "data" / "action_log.json")
    with pytest.raises(RepositoryPathViolation):
        GrowthLogStore(PROJECT_ROOT / "data" / "growth_log.json")
    with pytest.raises(RepositoryPathViolation):
        GrowthService()


def test_isolated_app_data_root_contains_every_runtime_private_store(
    isolated_app_data_root: IsolatedAppDataRoot,
):
    root = isolated_app_data_root
    for path in (
        root.memory_file,
        root.chat_history_file,
        root.chat_summaries_file,
        root.model_usage_file,
        root.today_plan_file,
        root.action_log_file,
        root.growth_log_file,
        root.candidate_file,
        root.conflict_file,
        root.audit_file,
        root.archive_file,
        root.backup_dir,
        root.lock_dir,
    ):
        path.resolve().relative_to(root.root.resolve())


def test_write_firewall_blocks_direct_real_data_mutation_before_it_happens():
    with pytest.raises(RepositoryPathViolation, match="write call site"):
        assert_write_target_is_test_isolated(
            PROJECT_ROOT / "data" / "private" / "today_plan.json",
            operation="test write",
        )


def test_snapshot_detects_content_changes_without_cleaning_them_up(tmp_path: Path):
    memory_file = tmp_path / "memory.json"
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    memory_file.write_text("before", encoding="utf-8")
    before = snapshot_paths(tmp_path, ("memory.json", "private"))
    memory_file.write_text("after", encoding="utf-8")
    after = snapshot_paths(tmp_path, ("memory.json", "private"))

    with pytest.raises(AssertionError, match="changed: memory.json"):
        assert_real_private_data_unchanged(before, after)
    assert memory_file.read_text(encoding="utf-8") == "after"
