import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.context_builder import ContextBuilder
from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever


NOW = datetime(2026, 7, 20, 10, 0, 0)


def make_services(root: Path):
    manager = MemoryManager(
        root / "memory.json",
        backup_dir=root / "backups",
        conflict_file=root / "memory_conflicts.json",
        now_provider=lambda: NOW,
    )
    return manager, MemoryRetriever(manager, now_provider=lambda: NOW)


def test_project_question_prefers_project_and_learning_memories():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        manager.add_memory(
            "我的肌电项目正在实现手势识别模块",
            category="project",
            importance=5,
        )
        manager.add_memory(
            "肌电项目需要继续学习特征提取和模型训练",
            category="learning",
            importance=4,
        )
        manager.add_memory("我的肠胃比较敏感", category="health")

        result = retriever.retrieve(
            "我的肌电项目下一步怎么办",
            update_usage=False,
        )
        categories = {item["memory"]["category"] for item in result}

        assert "project" in categories
        assert "learning" in categories
        assert "health" not in categories


def test_learning_question_never_injects_health_memory():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        manager.add_memory(
            "我正在学习梯度下降和优化算法",
            category="learning",
        )
        manager.add_memory("我的肠胃比较敏感", category="health")

        result = retriever.retrieve(
            "梯度下降为什么这样更新",
            update_usage=False,
        )

        assert any(item["memory"]["category"] == "learning" for item in result)
        assert all(item["memory"]["category"] != "health" for item in result)
        assert retriever.last_skipped_sensitive_categories == {"health"}


def test_health_question_can_retrieve_health_memory():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        manager.add_memory(
            "我的肠胃比较敏感，不适合吃太油",
            category="health",
        )

        result = retriever.retrieve(
            "最近肚子不舒服怎么办",
            update_usage=False,
        )

        assert any(item["memory"]["category"] == "health" for item in result)


def test_ordinary_chat_does_not_load_unrelated_memories():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, retriever = make_services(Path(temp_dir))
        for index, category in enumerate(
            ("project", "learning", "health", "goal", "preference")
        ):
            manager.add_memory(
                f"长期信息 {index} 与今天的问候无关",
                category=category,
                allow_similar=True,
            )

        result = retriever.retrieve(
            "你好，最近过得怎么样",
            update_usage=False,
        )

        assert len(result) <= 1


def test_legacy_memory_file_loads_with_runtime_defaults():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        memory_file = root / "memory.json"
        memory_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profile": {"nickname": "示例"},
                    "memories": [
                        "我喜欢安静的学习环境",
                        {"content": "我在做肌电项目", "category": "project"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        manager = MemoryManager(
            memory_file,
            backup_dir=root / "backups",
            conflict_file=root / "memory_conflicts.json",
            now_provider=lambda: NOW,
        )
        memories = manager.memories()

        assert len(memories) == 2
        for memory in memories:
            assert memory["id"] > 0
            assert memory["category"]
            assert memory["importance"] == 3
            assert memory["confidence"] == 1.0
            assert memory["created_at"]
            assert memory["last_used"] is None
            assert memory["status"] == "active"
        assert manager.last_backup_path is not None


def test_optional_defaults_do_not_rewrite_current_file_on_load():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        memory_file = root / "memory.json"
        original = {
            "version": 2,
            "profile": {},
            "memories": [
                {
                    "id": 1,
                    "content": "我正在学习机器学习",
                    "category": "learning",
                    "importance": 3,
                    "status": "active",
                    "use_count": 0,
                }
            ],
        }
        memory_file.write_text(
            json.dumps(original, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = memory_file.read_bytes()

        manager = MemoryManager(
            memory_file,
            backup_dir=root / "backups",
            conflict_file=root / "memory_conflicts.json",
            now_provider=lambda: NOW,
        )
        memory = manager.memories()[0]

        assert memory["confidence"] == 1.0
        assert memory["created_at"] == "2026-07-20T10:00:00"
        assert memory["last_used"] is None
        assert memory_file.read_bytes() == before


def test_context_sources_remain_separate():
    messages = ContextBuilder(recent_message_limit=4).build(
        personality_context="人格设定",
        memory_context="长期记忆：\n- 我在做肌电项目",
        memory_count=1,
        memory_categories=["project"],
        skipped_sensitive_categories=["health"],
        session_summary="之前讨论了数据采集方案",
        recent_messages=[
            {"role": "user", "content": "采集模块已经能运行"},
            {"role": "assistant", "content": "下一步可以检查数据质量"},
        ],
        knowledge_context="",
        current_user_input="肌电项目下一步怎么办",
    )

    assert messages[0]["content"] == "人格设定"
    summary_index = next(
        index
        for index, item in enumerate(messages)
        if item["content"].startswith("当前会话摘要：")
    )
    memory_index = next(
        index for index, item in enumerate(messages) if "我在做肌电项目" in item["content"]
    )
    assert summary_index < memory_index
    assert messages[-1] == {"role": "user", "content": "肌电项目下一步怎么办"}


if __name__ == "__main__":
    test_project_question_prefers_project_and_learning_memories()
    test_learning_question_never_injects_health_memory()
    test_health_question_can_retrieve_health_memory()
    test_ordinary_chat_does_not_load_unrelated_memories()
    test_legacy_memory_file_loads_with_runtime_defaults()
    test_optional_defaults_do_not_rewrite_current_file_on_load()
    test_context_sources_remain_separate()
    print("memory retrieval quality tests passed")
