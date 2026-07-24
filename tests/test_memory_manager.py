import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.memory_manager import MemoryManager


def make_manager(root: Path) -> MemoryManager:
    return MemoryManager(
        root / "memory.json",
        backup_dir=root / "private" / "backups",
        conflict_file=root / "private" / "memory_conflicts.json",
        now_provider=lambda: datetime(2026, 7, 15, 10, 30, 0),
    )


def test_legacy_memory_migrates_after_exact_backup():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        legacy = {
            "version": 1,
            "profile": {"nickname": "测试用户"},
            "memories": ["我喜欢晚上学习", {"content": "RoxyPlan 是长期项目"}],
        }
        original = json.dumps(legacy, ensure_ascii=False, indent=2).encode("utf-8")
        (root / "memory.json").write_bytes(original)

        manager = make_manager(root)

        assert manager.data["version"] == 2
        assert manager.data["profile"]["nickname"] == "测试用户"
        assert len(manager.memories()) == 2
        assert manager.last_backup_path is not None
        assert manager.last_backup_path.read_bytes() == original
        assert all("id" in item and "category" in item for item in manager.memories())


def test_failed_backup_leaves_legacy_file_unchanged():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        legacy = {"version": 1, "profile": {}, "memories": ["私人记忆"]}
        original = json.dumps(legacy, ensure_ascii=False).encode("utf-8")
        memory_file = root / "memory.json"
        memory_file.write_bytes(original)
        blocked_backup_dir = root / "blocked"
        blocked_backup_dir.write_text("not a directory", encoding="utf-8")

        manager = MemoryManager(
            memory_file,
            backup_dir=blocked_backup_dir,
            conflict_file=root / "memory_conflicts.json",
        )

        assert memory_file.read_bytes() == original
        assert manager.data["version"] == 1


def test_add_classify_duplicate_archive_restore_and_delete():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        first = manager.add_memory("我喜欢晚上学习")
        duplicate = manager.add_memory("我晚上学习效率更高")

        assert first["status"] == "added"
        assert first["memory"]["category"] == "preference"
        assert duplicate["status"] == "duplicate"
        assert len(manager.memories()) == 1

        memory_id = int(first["memory"]["id"])
        assert manager.archive(memory_id)["status"] == "archived"
        assert manager.memories("active") == []
        assert manager.restore(memory_id)["status"] == "active"
        assert manager.delete_memory(memory_id)["id"] == memory_id


def test_categories_cover_goal_project_learning_health_and_rule():
    assert MemoryManager.classify("我以后想做 AI 桌宠") == "goal"
    assert MemoryManager.classify("RoxyPlan 项目使用 PySide6") == "project"
    assert MemoryManager.classify("我正在学习机器学习") == "learning"
    assert MemoryManager.classify("我肠胃比较敏感") == "health"
    assert MemoryManager.classify("RoxyPlan 不要做成恋爱机器人") == "rule"


def test_conflict_is_recorded_without_overwriting_old_memory():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        old = manager.add_memory("我喜欢晚上学习", category="preference")
        conflict = manager.add_memory("我现在更适合早上学习", category="preference")

        assert conflict["status"] == "conflict"
        assert len(manager.memories()) == 1
        assert manager.memories()[0]["id"] == old["memory"]["id"]
        assert len(manager.conflicts()) == 1

        conflict_id = int(conflict["conflict"]["id"])
        manager.resolve_conflict(conflict_id, "keep_both")
        assert len(manager.memories()) == 2
        assert manager.conflicts() == []


def test_organize_only_suggests_and_does_not_delete():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = make_manager(Path(temp_dir))
        manager.add_memory("目标", category="goal", allow_similar=True)
        before = len(manager.memories())
        suggestions = manager.organize_suggestions()
        assert "vague" in suggestions
        assert len(manager.memories()) == before


if __name__ == "__main__":
    test_legacy_memory_migrates_after_exact_backup()
    test_failed_backup_leaves_legacy_file_unchanged()
    test_add_classify_duplicate_archive_restore_and_delete()
    test_categories_cover_goal_project_learning_health_and_rule()
    test_conflict_is_recorded_without_overwriting_old_memory()
    test_organize_only_suggests_and_does_not_delete()
    print("memory manager tests passed")
