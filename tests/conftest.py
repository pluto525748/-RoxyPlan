from __future__ import annotations

import sys
from pathlib import Path

import pytest

from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryService
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from tests.isolation_support import (
    IsolatedAppDataRoot,
    IsolatedMemoryPaths,
    build_isolated_memory_repository,
    make_isolated_app_data_root,
    make_isolated_memory_paths,
)
from tests.private_data_guard import (
    assert_real_private_data_unchanged,
    install_real_data_write_firewall,
    install_repository_path_guards,
    snapshot_real_private_data,
)
from tests.contract_taxonomy import (
    COMPATIBILITY_CONTRACT,
    HISTORICAL_BASELINE,
    PRODUCTION_CONTRACT,
    contract_lane_for,
)


_restore_repository_guards = None
_restore_write_firewall = None


def pytest_configure(config) -> None:
    global _restore_repository_guards, _restore_write_firewall
    config.addinivalue_line(
        "markers",
        f"{PRODUCTION_CONTRACT}: current unified production behavior and safety contract",
    )
    config.addinivalue_line(
        "markers",
        f"{COMPATIBILITY_CONTRACT}: supported legacy API, fixture, or rollback contract",
    )
    config.addinivalue_line(
        "markers",
        f"{HISTORICAL_BASELINE}: immutable historical evaluation evidence, not current product behavior",
    )
    if _restore_repository_guards is None:
        _restore_repository_guards = install_repository_path_guards()
    if _restore_write_firewall is None:
        _restore_write_firewall = install_real_data_write_firewall()


def pytest_collection_modifyitems(items) -> None:
    """Attach one explicit contract lane to every collected test."""

    for item in items:
        item.add_marker(contract_lane_for(item.nodeid))


def pytest_unconfigure(config) -> None:
    global _restore_repository_guards, _restore_write_firewall
    if _restore_write_firewall is not None:
        _restore_write_firewall()
        _restore_write_firewall = None
    if _restore_repository_guards is not None:
        _restore_repository_guards()
        _restore_repository_guards = None


@pytest.fixture(scope="session", autouse=True)
def protect_real_private_data():
    before = snapshot_real_private_data()
    yield before
    after = snapshot_real_private_data()
    assert_real_private_data_unchanged(before, after)


@pytest.fixture(autouse=True)
def isolated_app_data_root(tmp_path: Path) -> IsolatedAppDataRoot:
    return make_isolated_app_data_root(tmp_path / "app_data")


@pytest.fixture(autouse=True)
def isolate_loaded_desktop_config(monkeypatch, isolated_app_data_root: IsolatedAppDataRoot):
    def isolated_growth_factory(factory):
        def build(*args, **kwargs):
            if not args and "private_dir" not in kwargs:
                kwargs["private_dir"] = isolated_app_data_root.private_dir
            return factory(*args, **kwargs)

        return build

    def isolated_history_factory(factory):
        def build(*args, **kwargs):
            if not args and "private_dir" not in kwargs:
                kwargs["private_dir"] = isolated_app_data_root.private_dir
            return factory(*args, **kwargs)

        return build

    pet_app = sys.modules.get("frontend.pet_app")
    if pet_app is not None:
        monkeypatch.setattr(pet_app, "MEMORY_FILE", isolated_app_data_root.memory_file)
        monkeypatch.setattr(pet_app, "DEFAULT_MEMORY_FILE", isolated_app_data_root.memory_file)
        monkeypatch.setattr(pet_app, "CONFIG_FILE", isolated_app_data_root.root / "config.json")
        monkeypatch.setattr(pet_app, "KNOWLEDGE_DIR", isolated_app_data_root.root / "data" / "knowledge")
        monkeypatch.setattr(
            pet_app,
            "GrowthManager",
            isolated_growth_factory(pet_app.GrowthManager),
        )
        monkeypatch.setattr(
            pet_app,
            "ChatHistoryManager",
            isolated_history_factory(pet_app.ChatHistoryManager),
        )

    desktop_pet = sys.modules.get("frontend.desktop_pet")
    if desktop_pet is not None:
        monkeypatch.setattr(
            desktop_pet,
            "PET_CONFIG_FILE",
            isolated_app_data_root.root / "data" / "pet_config.json",
        )
        monkeypatch.setattr(
            desktop_pet,
            "GrowthManager",
            isolated_growth_factory(desktop_pet.GrowthManager),
        )
        monkeypatch.setattr(
            desktop_pet,
            "ChatHistoryManager",
            isolated_history_factory(desktop_pet.ChatHistoryManager),
        )


@pytest.fixture(autouse=True)
def cleanup_test_qt_resources(isolate_loaded_desktop_config):
    # Explicit dependency keeps private-path patches active during Qt teardown.
    # The helper imports Qt types but never creates QApplication or shows UI.
    from tests.test_qt_resource_lifecycle import (
        cleanup_new_top_level_widgets,
        snapshot_top_level_widgets,
    )

    baseline = snapshot_top_level_widgets()
    yield
    cleanup_new_top_level_widgets(baseline)


@pytest.fixture
def isolated_memory_paths(tmp_path: Path) -> IsolatedMemoryPaths:
    return make_isolated_memory_paths(tmp_path)


@pytest.fixture
def isolated_memory_repository(
    isolated_memory_paths: IsolatedMemoryPaths,
) -> LocalJsonMemoryRepository:
    return build_isolated_memory_repository(isolated_memory_paths)


@pytest.fixture
def isolated_memory_service(
    isolated_memory_paths: IsolatedMemoryPaths,
    isolated_memory_repository: LocalJsonMemoryRepository,
) -> MemoryService:
    manager = MemoryManager(
        isolated_memory_paths.memory_file,
        backup_dir=isolated_memory_paths.backup_dir,
        conflict_file=isolated_memory_paths.conflict_file,
        audit_file=isolated_memory_paths.audit_file,
        repository=isolated_memory_repository,
    )
    candidates = MemoryCandidateManager(repository=isolated_memory_repository)
    return MemoryService(manager, candidates)
