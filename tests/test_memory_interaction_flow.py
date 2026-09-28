import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service


def test_sensitive_explicit_memory_saves_formally_without_second_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        created = service.handle("这点请记住：我的肠胃比较敏感", "memory-a")
        candidates = service.memory_service.list_candidates().data["candidates"]
        memories = service.memory_service.list_memories().data["memories"]
        assert created.status == "completed"
        assert candidates == []
        assert len(memories) == 1
        assert service.interaction_coordinator.current("memory-a").state != "awaiting_confirmation"


def test_candidate_compatibility_api_remains_without_normal_chat_entry():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.memory_service.set_candidates_enabled(True)
        ids = []
        for content in ("我喜欢早上学习", "RoxyPlan 是我的长期项目"):
            result = service.memory_service.create_candidate(content)
            ids.append(int(result.data["candidate"]["id"]))

        rejected = service.memory_service.reject_candidate(ids[0])
        accepted = service.memory_service.accept_candidate(ids[1])
        query = service.handle("你还知道关于我的什么内容", "brand-new-session")

        assert rejected.success
        assert accepted.success
        assert "RoxyPlan 是你的长期项目" in query.message
        assert "待审核" not in query.message
        assert "候选" not in query.message
        assert service.interaction_coordinator.current("brand-new-session").listed_object_ids == []


def test_bare_confirmation_without_pending_never_writes():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("确认", "empty")
        assert response.status in {"clarification", "failed"}
        assert service.memory_service.list_memories().data["memories"] == []
        assert service.memory_service.list_candidates().data["candidates"] == []


if __name__ == "__main__":
    test_sensitive_explicit_memory_saves_formally_without_second_confirmation()
    test_candidate_compatibility_api_remains_without_normal_chat_entry()
    test_bare_confirmation_without_pending_never_writes()
    print("memory interaction flow tests passed")
