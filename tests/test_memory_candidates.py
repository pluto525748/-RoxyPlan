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
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from tests.isolation_support import (
    build_isolated_candidate_manager,
    build_isolated_memory_service,
)


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_missing_file_is_created():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "private" / "memory_candidates.json"
        manager = build_isolated_candidate_manager(Path(temp_dir))

        assert path.exists()
        assert manager.pending() == []
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "candidates": [],
        }


def test_add_candidate_and_skip_duplicate():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        clock = FakeClock(datetime(2026, 7, 15, 10, 0, 0))
        manager = build_isolated_candidate_manager(root, now_provider=clock)

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
        manager = build_isolated_candidate_manager(Path(temp_dir))
        first, _ = manager.add_candidate("我周末适合整理项目", "stable_habit", "source 1")
        manager.add_candidate("不要自动生图", "project_preference", "source 2")

        rejected, changed = manager.reject(int(first["id"]))
        cleared = manager.clear_pending()

        assert changed is True
        assert rejected["status"] == "rejected"
        assert cleared == 1
        assert manager.pending() == []


def test_ordinary_statements_fall_back_to_chat_and_explicit_memory_stays_deterministic():
    router = IntentRouter()
    examples = {
        "我喜欢晚上学习": "user_preference",
        "我以后想做 AI 桌宠": "long_term_goal",
        "我一般晚上学习效率高": "stable_habit",
        "我肠胃比较敏感": "health_lifestyle",
        "RoxyPlan 不要做成恋爱机器人": "project_preference",
        "以后 Codex 提示词不要太长": "project_preference",
    }

    for text in examples:
        result = router.route(text)
        assert result["intent"] == "chat"

    assert router.route("我喜欢你")["intent"] == "chat"
    assert router.route("今天有点累，想聊聊天")["intent"] == "chat"
    assert router.route("记住：我喜欢晚上学习")["intent"] == "add_memory_request"
    assert router.route("帮我记住我喜欢晚上学习")["intent"] == "add_memory_request"


def _build_chat_window(root: Path):
    memory_service = build_isolated_memory_service(
        root,
        default_data=pet_app.default_memory(),
    )
    candidate_manager = memory_service.candidate_manager
    growth_manager = GrowthManager(root / "growth")
    window = ChatWindow(
        growth_service=growth_manager,
        memory_service=memory_service,
        chat_history_manager=ChatHistoryManager(root / "history"),
    )
    window.start_ai_reply = lambda *_args: (_ for _ in ()).throw(
        AssertionError("deterministic memory commands must not call the LLM")
    )
    return window, candidate_manager


def test_explicit_memory_from_desktop_writes_formal_memory_once():
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
            window.input_box.setText("记住：我喜欢晚上学习")
            window.send_message()

            assert manager.pending() == []
            loaded = json.loads(pet_app.MEMORY_FILE.read_text(encoding="utf-8"))
            assert [item["content"] for item in loaded["memories"]] == ["我喜欢晚上学习"]
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.MEMORY_EXAMPLE_FILE = original_example_file


def test_chat_cannot_reject_internal_candidate_or_write_formal_memory():
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
            manager.add_candidate("不要自动生图", "project_preference", "automatic_discovery")
            window.input_box.setText("忽略记忆1")
            window.send_message()

            loaded = json.loads(pet_app.MEMORY_FILE.read_text(encoding="utf-8"))
            assert loaded["memories"] == []
            assert manager.get(1)["status"] == "pending"
            assert "候选记忆功能已经停用" in window.transcript.toPlainText()
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file
            pet_app.MEMORY_EXAMPLE_FILE = original_example_file


def test_memory_dialog_lists_source_and_runs_callbacks():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = build_isolated_candidate_manager(Path(temp_dir))
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

        assert all(
            dialog.tabs.tabText(index) != "待整理（高级）"
            for index in range(dialog.tabs.count())
        )
        dialog.refresh_candidates()
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
        memory_service = build_isolated_memory_service(root)
        candidate_manager = memory_service.candidate_manager
        memory_manager = memory_service.memory_manager
        active = memory_manager.add_memory("RoxyPlan 是长期项目", category="project")["memory"]
        archived = memory_manager.add_memory("旧的学习习惯", category="habit")["memory"]
        memory_manager.archive(int(archived["id"]))
        memory_manager.add_memory("我喜欢晚上学习", category="preference")
        memory_manager.add_memory("我现在更适合早上学习", category="preference")

        dialog = MemoryDialog(
            memory_service=memory_service,
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
