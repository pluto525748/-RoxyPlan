import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service


def test_sensitive_explicit_memory_stays_candidate_until_review():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        created = service.handle("这点请记住：我的肠胃比较敏感", "memory-a")
        candidates = service.memory_service.list_candidates().data["candidates"]
        memories = service.memory_service.list_memories().data["memories"]
        assert created.status == "completed"
        assert len(candidates) == 1
        assert memories == []

        candidate_id = int(candidates[0]["id"])
        accepted = service.handle(f"确认记忆{candidate_id}", "memory-a")
        assert accepted.status == "completed"
        assert str(candidate_id) in accepted.message
        assert len(service.memory_service.list_memories().data["memories"]) == 1


def test_list_select_reject_and_new_conversation_memory_query():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        ids = []
        for content in ("我喜欢早上学习", "RoxyPlan 是我的长期项目"):
            result = service.memory_service.create_candidate(content)
            ids.append(int(result.data["candidate"]["id"]))

        listed = service.handle("有哪些待确认记忆", "memory-list")
        rejected = service.handle("忽略第一个", "memory-list")
        accepted = service.handle("第二个确认", "memory-list")
        query = service.handle("你还知道关于我的什么内容", "brand-new-session")

        assert all(str(item) in listed.message for item in ids)
        assert str(ids[0]) in rejected.message
        assert str(ids[1]) in accepted.message
        assert "RoxyPlan 是我的长期项目" in query.message
        assert service.interaction_coordinator.current("brand-new-session").listed_object_ids == []


def test_bare_confirmation_without_pending_never_writes():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle("确认", "empty")
        assert response.status in {"clarification", "failed"}
        assert service.memory_service.list_memories().data["memories"] == []
        assert service.memory_service.list_candidates().data["candidates"] == []


if __name__ == "__main__":
    test_sensitive_explicit_memory_stays_candidate_until_review()
    test_list_select_reject_and_new_conversation_memory_query()
    test_bare_confirmation_without_pending_never_writes()
    print("memory interaction flow tests passed")
