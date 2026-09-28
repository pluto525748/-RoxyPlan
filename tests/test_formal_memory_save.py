import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from v18_test_support import NoCallLLM, RecordingLLM, build_service


def _formal_operation(response):
    result = next(item for item in response.tool_results if item.tool == "save_formal_memory")
    return result.data["memory_operation"]


def test_explicit_memory_is_formal_once_with_payload_and_no_pet_action():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        response = service.handle("记住我喜欢你跳舞", "formal-a")

        operation = _formal_operation(response)
        assert response.status == "completed"
        assert operation["memory_id"]
        assert operation["data"]["operation"] == "created"
        assert operation["data"]["final_content"] == "我喜欢你跳舞"
        assert operation["data"]["undo_available"] is True
        assert all(item.tool != "play_dance" for item in response.tool_results)
        assert service.memory_service.list_candidates().data["candidates"] == []
        assert [item["content"] for item in service.memory_service.list_memories().data["memories"]] == ["我喜欢你跳舞"]


def test_duplicate_and_conflict_save_directly_without_candidate_review():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        first = service.handle("保存长期记忆：我更适合早上学习", "formal-b")
        duplicate = service.handle("记住我更适合早上学习", "formal-b")
        conflict = service.handle("记住我现在更适合晚上学习", "formal-b")

        assert _formal_operation(first)["data"]["operation"] == "created"
        assert _formal_operation(duplicate)["data"]["operation"] == "duplicate"
        assert len(service.memory_service.list_memories().data["memories"]) == 1
        assert conflict.status == "completed"
        assert _formal_operation(conflict)["data"]["operation"] == "updated"
        assert service.memory_service.list_candidates().data["candidates"] == []
        memories = service.memory_service.list_memories().data["memories"]
        assert len(memories) == 1
        assert memories[0]["content"] == "我现在更适合晚上学习"


def test_sensitive_explicit_memory_is_authorized_and_saves_without_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        completed = service.handle("请记住我的肠胃比较敏感", "formal-sensitive")
        assert completed.status == "completed"
        assert _formal_operation(completed)["data"]["operation"] == "created"
        assert len(service.memory_service.list_memories().data["memories"]) == 1
        assert service.memory_service.list_candidates().data["candidates"] == []
        assert service.interaction_coordinator.current("formal-sensitive").state != "awaiting_confirmation"


def test_scoped_undo_archives_only_recent_created_memory_and_is_single_use():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        created = service.handle("记住我喜欢喝冰可乐", "undo-a")
        memory_id = int(_formal_operation(created)["memory_id"])

        other_window = service.handle("撤销刚才保存", "undo-b")
        assert other_window.status == "clarification"
        assert service.memory_service.get_memory(memory_id).data["memory"]["status"] == "active"

        undone = service.handle("撤销刚才保存", "undo-a")
        assert undone.status == "completed"
        assert any(item.tool == "archive_memory" and item.success for item in undone.tool_results)
        assert service.memory_service.get_memory(memory_id).data["memory"]["status"] == "archived"

        repeated = service.handle("刚才那条别记了", "undo-a")
        assert repeated.status == "clarification"


def test_scoped_undo_expires_and_candidate_compatibility_api_remains_available():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        now = [datetime(2026, 8, 1, 12, 0, 0)]
        service.interaction_coordinator.now_provider = lambda: now[0]
        service.interaction_coordinator.ttl_seconds = 30
        created = service.handle("记住我喜欢清晨阅读", "undo-expired")
        memory_id = int(_formal_operation(created)["memory_id"])

        now[0] += timedelta(seconds=31)
        expired = service.handle("取消刚才的记忆", "undo-expired")
        assert expired.status == "clarification"
        assert service.memory_service.get_memory(memory_id).data["memory"]["status"] == "active"

        service.memory_service.set_candidates_enabled(True)
        candidate = service.memory_service.create_candidate(
            "我习惯每周复盘一次",
            source="automatic_discovery",
            explicit=False,
        )
        assert candidate.success
        assert len(service.memory_service.list_candidates().data["candidates"]) == 1
