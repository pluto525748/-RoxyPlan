import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.repositories.local_json_chat_repository import LocalJsonChatRepository
from modules.repositories.local_json_growth_repository import LocalJsonGrowthRepository
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository


NOW = lambda: datetime(2026, 7, 16, 10, 0, 0)


def test_growth_repository_preserves_files_and_stable_ids():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        private = root / "private"
        repository = LocalJsonGrowthRepository(private)
        manager = GrowthManager(private, now_provider=NOW, repository=repository)
        task = manager.add_task("测试 Repository")
        action = manager.add_record("完成 Repository 测试")

        reloaded = GrowthManager(private, now_provider=NOW, repository=repository)
        assert reloaded.tasks()[0]["id"] == 1
        assert reloaded.tasks()[0]["uid"] == task["uid"]
        assert reloaded.records_for_date()[0]["uid"] == action["uid"]


def test_growth_repository_keeps_legacy_source_file():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        legacy = root / "today_plan.json"
        legacy.write_text(
            json.dumps({"date": "2026-07-16", "tasks": [{"id": 1, "title": "旧计划", "done": False}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        original = legacy.read_bytes()
        repository = LocalJsonGrowthRepository(root / "private", legacy_plan_file=legacy)
        manager = GrowthManager(root / "private", now_provider=NOW, repository=repository)

        assert manager.tasks()[0]["title"] == "旧计划"
        assert legacy.read_bytes() == original
        assert repository.plan_file.exists()


def test_memory_repository_handles_memory_candidates_conflicts_and_backup():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repository = LocalJsonMemoryRepository(
            root / "memory.json",
            candidate_file=root / "private" / "memory_candidates.json",
            conflict_file=root / "private" / "memory_conflicts.json",
            backup_dir=root / "private" / "backups",
        )
        memory = MemoryManager(
            root / "memory.json",
            backup_dir=root / "private" / "backups",
            conflict_file=root / "private" / "memory_conflicts.json",
            now_provider=NOW,
            repository=repository,
        )
        candidate = MemoryCandidateManager(
            root / "private" / "memory_candidates.json",
            now_provider=NOW,
            repository=repository,
        )

        added = memory.add_memory("我正在学习机器学习")
        queued, created = candidate.add_candidate("我喜欢早上复习", "preference", "原始表达")

        assert added["memory"]["uid"].startswith("memory_")
        assert created is True
        assert queued["uid"].startswith("candidate_")
        assert repository.memory_exists()
        assert repository.candidate_exists()
        assert repository.conflict_exists()


def test_chat_repository_round_trip_sessions_messages_and_summaries():
    with tempfile.TemporaryDirectory() as temp:
        private = Path(temp) / "private"
        repository = LocalJsonChatRepository(private)
        manager = ChatHistoryManager(private, now_provider=NOW, repository=repository)
        session = manager.new_session()
        manager.add_message(session["session_id"], "user", "测试聊天仓储")
        manager.save_summary(session["session_id"], "讨论了聊天仓储。")

        assert repository.load_messages(session["session_id"])[0]["content"] == "测试聊天仓储"
        assert repository.load_summaries()[session["session_id"]]["summary"] == "讨论了聊天仓储。"
        restored = ChatHistoryManager(private, now_provider=NOW, repository=repository)
        assert restored.latest_session()["session_id"] == session["session_id"]


if __name__ == "__main__":
    test_growth_repository_preserves_files_and_stable_ids()
    test_growth_repository_keeps_legacy_source_file()
    test_memory_repository_handles_memory_candidates_conflicts_and_backup()
    test_chat_repository_round_trip_sessions_messages_and_summaries()
    print("repository tests passed")
