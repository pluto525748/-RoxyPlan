"""Optional location stays typed through refresh, migration and unrelated writes."""
import json
from datetime import datetime

import pytest

from modules.memory_manager import MemoryManager
from v18_test_support import NoCallLLM, build_service


def manager_at(root):
    return MemoryManager(
        root / "memory.json",
        backup_dir=root / "private/backups",
        conflict_file=root / "private/conflicts.json",
        audit_file=root / "private/audit.json",
        now_provider=lambda: datetime(2026, 10, 23, 12),
    )


def document(manager):
    return json.loads(manager.memory_file.read_text(encoding="utf-8"))


def test_second_formal_save_does_not_change_existing_record(tmp_path):
    service, _, memory, _ = build_service(tmp_path, NoCallLLM())
    first = service.handle("记住：我的长期目标是掌握天文学", "null-location")
    assert first.tool_results[0].success
    before = document(memory)
    assert before["memories"][0]["location"] is None

    second = service.handle("记住：我习惯先自己思考，再看例子", "null-location")
    after = document(memory)

    assert second.tool_results[0].success
    assert len(after["memories"]) == len(before["memories"]) + 1
    assert after["memories"][0] == before["memories"][0]
    assert after["memories"][1]["location"] is None
    assert after["profile"] == before["profile"]


@pytest.mark.parametrize("location", [None, "None", "苏州"])
def test_refresh_reload_and_add_preserve_existing_location_and_optional_fields(tmp_path, location):
    manager = manager_at(tmp_path)
    manager.add_memory("合成长期目标是学习天文学", category="goal")
    seeded = document(manager)
    seeded["memories"][0]["location"] = location
    assert manager.repository.save_memories(seeded)
    disk_before = manager.memory_file.read_bytes()

    assert manager.memories()[0]["location"] == location
    assert manager.memory_file.read_bytes() == disk_before  # read-only refresh
    reloaded = manager_at(tmp_path)
    assert reloaded.memories()[0]["location"] == location
    assert manager.memory_file.read_bytes() == disk_before
    added = reloaded.add_memory("合成项目是整理星图", category="project")
    assert added["status"] == "added"
    after = document(reloaded)
    assert after["memories"][0] == seeded["memories"][0]
    assert len(after["memories"]) == 2


@pytest.mark.parametrize("version", [1, 2], ids=["legacy-migration", "current-refresh"])
@pytest.mark.parametrize("location,expected", [(None, None), ("None", "None"), ("苏州", "苏州")])
def test_explicit_location_is_not_replaced_by_content_inference(tmp_path, version, location, expected):
    manager = manager_at(tmp_path)
    manager.add_memory("我来自苏州", category="other")
    seeded = document(manager)
    seeded["version"] = version
    seeded["memories"][0]["location"] = location
    assert manager.repository.save_memories(seeded)
    original = manager.memory_file.read_bytes()

    reloaded = manager_at(tmp_path)
    assert reloaded.memories()[0]["location"] == expected
    if version == 1:
        assert reloaded.last_backup_path.read_bytes() == original
        assert document(reloaded)["memories"][0]["location"] == expected
    else:
        assert manager.memory_file.read_bytes() == original


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("field", ["missing", "blank"])
def test_legacy_missing_or_blank_location_still_uses_existing_inference(tmp_path, version, field):
    manager = manager_at(tmp_path)
    manager.add_memory("我来自苏州", category="other")
    seeded = document(manager)
    seeded["version"] = version
    if field == "missing":
        del seeded["memories"][0]["location"]
    else:
        seeded["memories"][0]["location"] = ""
    assert manager.repository.save_memories(seeded)

    assert manager_at(tmp_path).memories()[0]["location"] == "苏州"
