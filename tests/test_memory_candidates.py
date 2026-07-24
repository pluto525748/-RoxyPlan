import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from frontend import pet_app
from frontend.memory_dialog import MemoryDialog
from frontend.pet_app import ChatWindow
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager


class TestClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_missing_file_is_created():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "private" / "memory_candidates.json"
        manager = MemoryCandidateManager(path)

        assert path.exists()
        assert manager.pending() == []
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "candidates": [],
        }


def test_add_candidate_and_skip_duplicate():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory_candidates.json"
        clock = TestClock(datetime(2026, 7, 15, 10, 0, 0))
        manager = MemoryCandidateManager(path, now_provider=clock)

        first, added = manager.add_candidate(
            "我喜欢晚上学习",
            "user_preference",
            "我喜欢晚上学习",
        )
        duplicate, duplicate_added = manager.add_candidate(
            "我喜欢晚上学习。",
            "user_preference",
            "我又说了一次",
        )

        assert added is True
        assert duplicate_added is False
        assert duplicate["id"] == first["id"]
        assert len(manager.pending()) == 1
        assert first["created_at"] == "2026-07-15T10:00:00"


def test_pending_reject_and_clear():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = MemoryCandidateManager(Path(temp_dir) / "memory_candidates.json")
        first, _ = manager.add_candidate("我周末适合整理项目", "stable_habit", "source 1")
        manager.add_candidate("不要自动生图", "project_preference", "source 2")

        rejected, changed = manager.reject(int(first["id"]))
        cleared = manager.clear_pending()

        assert changed is True
        assert rejected["status"] == "rejected"
        assert cleared == 1
        assert manager.pending() == []


def test_candidate_intent_rules_and_normal_chat_fallback():
    router = IntentRouter()
    examples = {
        "我喜欢晚上学习": "user_preference",
        "我以后想做 AI 桌宠": "long_term_goal",
        "我一般晚上学习效率高": "stable_habit",
        "我肠胃比较敏感": "health_lifestyle",
        "RoxyPlan 不要做成恋爱机器人": "project_preference",
        "以后 Codex 提示词不要太长": "project_preference",
    }

    for text, category in examples.items():
        result = router.route(text)
        assert result["intent"] == "memory_candidate"
        assert result["slots"]["category"] == category

    assert router.route("我喜欢你")["intent"] == "chat"
    assert router.route("今天有点累，想聊聊天")["intent"] == "chat"
    assert router.route("记住：我喜欢晚上学习")["reason"] == "fixed_command"
    assert router.route("帮我记住我喜欢晚上学习")["intent"] == "add_memory_request"


def _build_chat_window(root: Path):
    candidate_manager = MemoryCandidateManager(root / "private" / "memory_candidates.json")
    growth_manager = GrowthManager(root / "growth")
    window = ChatWindow(
        growth_service=growth_manager,
        memory_candidate_manager=candidate_manager,
    )
    window.start_ai_reply = lambda _text: (_ for _ in ()).throw(
        AssertionError("Memory candidate commands must not call the LLM")
    )
    return window, candidate_manager


def test_confirm_candidate_writes_memory_once():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        original_memory_file = pet_app.MEMORY_FILE
        original_example_file = pet_app.MEMORY_EXAMPLE_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        pet_app.MEMORY_EXAMPLE_FILE = root / "memory.example.json"
        pet_app.MEMORY_EXAMPLE_FILE.write_text(
            json.dumps(
                {"version": 1, "profile": {"nickname": ""}, "memories": []},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            window, manager = _build_chat_window(root)
            window.input_box.setText("我喜欢晚上学习")
            window.send_message()

            assert manager.pending() == []
            assert window.memory["memories"] == []

            window.input_box.setText("确认")
            window.send_message()
            assert len(manager.pending()) == 1

            window.input_box.setText("确认记忆1")
            window.send_message()
            window.input_box.setText("记住：我喜欢晚上学习")
            window.send_message()

            loaded = json.loads(pet_app.MEMORY_FILE.read_text(encoding="utf-8"))
            assert [item["content"] for item in loaded["memories"]] == ["我喜欢晚上学习"]
            assert manager.get(1)["status"] == "accepted"
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.MEMORY_EXAMPLE_FILE = original_example_file


def test_ignore_candidate_does_not_write_memory():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        original_memory_file = pet_app.MEMORY_FILE
        original_example_file = pet_app.MEMORY_EXAMPLE_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        pet_app.MEMORY_EXAMPLE_FILE = root / "memory.example.json"
        pet_app.MEMORY_EXAMPLE_FILE.write_text(
            json.dumps(
                {"version": 1, "profile": {"nickname": ""}, "memories": []},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            window, manager = _build_chat_window(root)
            window.input_box.setText("不要自动生图")
            window.send_message()
            window.input_box.setText("确认")
            window.send_message()
            window.input_box.setText("忽略记忆1")
            window.send_message()

            loaded = json.loads(pet_app.MEMORY_FILE.read_text(encoding="utf-8"))
            assert loaded["memories"] == []
            assert manager.get(1)["status"] == "rejected"
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.MEMORY_EXAMPLE_FILE = original_example_file


def test_memory_dialog_lists_source_and_runs_callbacks():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = MemoryCandidateManager(Path(temp_dir) / "memory_candidates.json")
        candidate, _ = manager.add_candidate(
            "我周末适合整理项目",
            "stable_habit",
            "我周末适合整理项目",
        )
        accepted = []
        rejected = []
        dialog = MemoryDialog(
            manager,
            lambda candidate_id: accepted.append(candidate_id) or True,
            lambda candidate_id: rejected.append(candidate_id) or True,
        )
        dialog.show()
        app.processEvents()

        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == "稳定习惯"
        assert dialog.table.item(0, 2).text() == "我周末适合整理项目"
        dialog._accept(int(candidate["id"]))
        assert accepted == [1]
        dialog.close()
        app.processEvents()


def test_memory_dialog_shows_confirmed_archived_and_conflict_sections():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        candidate_manager = MemoryCandidateManager(root / "memory_candidates.json")
        memory_manager = MemoryManager(
            root / "memory.json",
            backup_dir=root / "backups",
            conflict_file=root / "memory_conflicts.json",
        )
        active = memory_manager.add_memory("RoxyPlan 是长期项目", category="project")["memory"]
        archived = memory_manager.add_memory("旧的学习习惯", category="habit")["memory"]
        memory_manager.archive(int(archived["id"]))
        memory_manager.add_memory("我喜欢晚上学习", category="preference")
        memory_manager.add_memory("我现在更适合早上学习", category="preference")

        dialog = MemoryDialog(
            candidate_manager,
            lambda _candidate_id: True,
            lambda _candidate_id: True,
            memory_manager=memory_manager,
        )
        dialog.show()
        app.processEvents()

        assert dialog.memory_table.rowCount() == 2
        assert dialog.archived_table.rowCount() == 1
        assert dialog.conflict_table.rowCount() == 1
        assert dialog.memory_table.item(0, 0).text() == str(active["id"])
        dialog.close()
        app.processEvents()


if __name__ == "__main__":
    test_missing_file_is_created()
    test_add_candidate_and_skip_duplicate()
    test_pending_reject_and_clear()
    test_candidate_intent_rules_and_normal_chat_fallback()
    test_confirm_candidate_writes_memory_once()
    test_ignore_candidate_does_not_write_memory()
    test_memory_dialog_lists_source_and_runs_callbacks()
    test_memory_dialog_shows_confirmed_archived_and_conflict_sections()
    print("memory candidate tests passed")
