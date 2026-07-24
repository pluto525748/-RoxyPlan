import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from PySide6.QtWidgets import QApplication
from frontend.pet_app import ChatWindow


def make_services(root: Path):
    manager = MemoryManager(
        root / "memory.json",
        backup_dir=root / "backups",
        conflict_file=root / "memory_conflicts.json",
        now_provider=lambda: datetime(2026, 7, 15, 10, 0, 0),
    )
    retriever = MemoryRetriever(
        manager, now_provider=lambda: datetime(2026, 7, 15, 10, 0, 0)
    )
    return manager, retriever


def test_keyword_category_importance_and_limit_ranking():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        low = manager.add_memory(
            "我正在学习机器学习基础", category="learning", importance=2
        )["memory"]
        high = manager.add_memory(
            "机器学习项目要先完成数据整理",
            category="learning",
            importance=5,
            allow_similar=True,
        )["memory"]
        manager.add_memory("RoxyPlan 使用 PySide6", category="project")

        result = retriever.retrieve("机器学习的数据应该怎么整理？", limit=2)

        assert len(result) == 2
        assert result[0]["memory"]["id"] == high["id"]
        assert {item["memory"]["id"] for item in result} == {low["id"], high["id"]}


def test_sensitive_memory_requires_clear_relevance():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        manager.add_memory("我肠胃比较敏感，不适合吃太油", category="health")
        manager.add_memory("我正在学习 Python", category="learning")

        unrelated = retriever.retrieve("Python 列表怎么写？", update_usage=False)
        related = retriever.retrieve("我肠胃不舒服，吃东西要注意什么？", update_usage=False)

        assert all(item["memory"]["category"] != "health" for item in unrelated)
        assert any(item["memory"]["category"] == "health" for item in related)


def test_usage_metadata_updates_after_retrieval():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        memory = manager.add_memory("RoxyPlan 是长期桌宠项目", category="project")["memory"]

        result = retriever.retrieve("继续推进 RoxyPlan 项目", limit=1)
        updated = manager.get(int(memory["id"]))

        assert len(result) == 1
        assert updated["use_count"] == 1
        assert updated["last_used_at"] == "2026-07-15T10:00:00"


def test_broad_recall_returns_at_most_eight():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        for index in range(12):
            manager.add_memory(
                f"长期信息 {index}", category="other", allow_similar=True
            )
        result = retriever.retrieve("你记得我什么", limit=20, broad=True, update_usage=False)
        assert len(result) == 8


def test_chat_context_injects_related_memory_only():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager, _retriever = make_services(root)
        learning = manager.add_memory(
            "我正在学习 Python 列表", category="learning"
        )["memory"]
        manager.add_memory(
            "我肠胃比较敏感，不适合吃太油", category="health"
        )
        window = ChatWindow(
            memory_manager=manager,
            growth_service=GrowthManager(root / "growth"),
            chat_history_manager=ChatHistoryManager(root / "private"),
        )

        messages = window.build_llm_messages("Python 列表应该怎么学习？")
        combined = "\n".join(item["content"] for item in messages)

        assert "我正在学习 Python 列表" in combined
        assert "肠胃比较敏感" not in combined
        assert manager.get(int(learning["id"]))["use_count"] == 1
        window.close()
        app.processEvents()


if __name__ == "__main__":
    test_keyword_category_importance_and_limit_ranking()
    test_sensitive_memory_requires_clear_relevance()
    test_usage_metadata_updates_after_retrieval()
    test_broad_recall_returns_at_most_eight()
    test_chat_context_injects_related_memory_only()
    print("memory retriever tests passed")
