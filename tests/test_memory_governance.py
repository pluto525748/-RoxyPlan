import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.knowledge_manager import KnowledgeManager
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_governance import MemoryGovernanceService
from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from server.agent_service import AgentService
from server.main import create_app


class FakeLLM:
    provider = "ollama"
    model = "fake-model"

    def __init__(self, reply="收到，我会只把明确且稳定的信息交给你审核。"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


def build_governance(root: Path):
    private = root / "private"
    repository = LocalJsonMemoryRepository(
        root / "memory.json",
        candidate_file=private / "memory_candidates.json",
        conflict_file=private / "memory_conflicts.json",
        backup_dir=private / "backups",
        audit_file=private / "memory_audit.json",
    )
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
        audit_file=private / "memory_audit.json",
        repository=repository,
    )
    candidates = MemoryCandidateManager(
        private / "memory_candidates.json",
        repository=repository,
    )
    return MemoryGovernanceService(memory, candidates), memory, candidates


def build_service(root: Path):
    data = root / "data"
    private = data / "private"
    knowledge = data / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    governance, memory, candidates = build_governance(root)
    service = AgentService(
        project_root=root,
        growth_manager=GrowthManager(private),
        memory_manager=memory,
        memory_candidate_manager=candidates,
        chat_history_manager=ChatHistoryManager(private),
        knowledge_manager=KnowledgeManager(knowledge),
        llm_client=FakeLLM(),
        status_probe=lambda _client: False,
    )
    service.memory_governance.set_enabled(governance.enabled)
    return service


def add_and_accept(governance, text, *, category="preference"):
    candidate, _ = governance.candidate_manager.add_candidate(
        text,
        category,
        text,
        confidence=0.9,
        reason="test",
    )
    return governance.accept_candidate(int(candidate["id"]), source="test")


def test_candidate_generation_quality_and_sensitive_boundary():
    with tempfile.TemporaryDirectory() as temp:
        governance, memory, candidates = build_governance(Path(temp))

        stable = governance.propose_from_text("我喜欢晚上安静地学习")
        assert stable["status"] == "added"
        assert stable["candidate"]["category"] == "preference"

        assert governance.propose_from_text("梯度下降为什么这样更新")["status"] == "skipped"
        assert governance.propose_from_text("今晚学习机器学习30分钟")["status"] == "skipped"
        assert governance.propose_from_text("我的肠胃一直不稳定")["status"] == "skipped_sensitive"
        assert memory.memories() == []

        sensitive = governance.propose_from_text(
            "请记住：我的肠胃一直不稳定",
            explicit=True,
            source="explicit_request",
        )
        assert sensitive["status"] == "added"
        assert sensitive["candidate"]["sensitivity"] == "high"
        assert memory.memories() == []
        assert len(candidates.pending()) == 2


def test_accept_reject_edit_deduplicate_and_audit():
    with tempfile.TemporaryDirectory() as temp:
        governance, memory, candidates = build_governance(Path(temp))
        first = governance.propose_from_text("我喜欢晚上学习")["candidate"]
        duplicate = governance.propose_from_text("我喜欢晚上学习")
        assert duplicate["status"] == "duplicate_candidate"
        assert len(candidates.pending()) == 1

        accepted = governance.accept_candidate(int(first["id"]), source="test")
        assert accepted["status"] == "added"
        assert len(memory.memories()) == 1
        memory_id = int(memory.memories()[0]["id"])
        memory.update_memory(memory_id, importance=4)
        memory.archive(memory_id)
        memory.restore(memory_id)

        second = governance.propose_from_text("我以后想长期做 AI 桌宠")["candidate"]
        edited = governance.accept_candidate(
            int(second["id"]),
            edited_content="我的长期目标是持续开发 AI 桌宠",
            source="test",
        )
        assert edited["status"] == "added"
        assert any("持续开发" in item["content"] for item in memory.memories())

        third = governance.propose_from_text("我通常周末整理项目")["candidate"]
        rejected = governance.reject_candidate(int(third["id"]), source="test")
        assert rejected["status"] == "rejected"
        assert all("周末整理" not in item["content"] for item in memory.memories())

        actions = [item["action"] for item in memory.audit_entries()]
        assert "accept_candidate" in actions
        assert "reject_candidate" in actions
        assert {"update", "archive", "restore"}.issubset(actions)


def make_conflict(root: Path):
    governance, memory, _candidates = build_governance(root)
    memory.add_memory("我通常晚上学习机器学习", category="preference", source="test")
    candidate, _ = governance.candidate_manager.add_candidate(
        "我现在更习惯早晨学习机器学习",
        "preference",
        "我现在更习惯早晨学习机器学习",
        confidence=0.95,
        reason="test conflict",
    )
    result = governance.accept_candidate(int(candidate["id"]), source="test")
    assert result["status"] == "conflict"
    assert len(memory.memories("active")) == 1
    return governance, memory, int(result["conflict"]["id"])


def test_conflict_does_not_overwrite_and_all_resolutions():
    for resolution in ("keep_old", "use_new", "keep_both", "merge", "defer"):
        with tempfile.TemporaryDirectory() as temp:
            governance, memory, conflict_id = make_conflict(Path(temp))
            kwargs = {"merged_content": "我会根据状态在早晨或晚上学习机器学习"} if resolution == "merge" else {}
            result = governance.resolve_conflict(conflict_id, resolution, source="test", **kwargs)
            if resolution == "defer":
                assert result["status"] == "deferred"
                assert len(governance.conflicts()) == 1
                continue
            assert result["status"] in {"resolved", "added", "merged"}
            assert governance.conflicts() == []
            active = memory.memories("active")
            if resolution == "keep_old":
                assert len(active) == 1 and "晚上" in active[0]["content"]
            elif resolution == "use_new":
                assert len(active) == 1 and "早晨" in active[0]["content"]
            elif resolution == "keep_both":
                assert len(active) == 2
            elif resolution == "merge":
                assert len(active) == 1 and "根据状态" in active[0]["content"]
            assert any(item["action"] == "resolve_conflict" for item in memory.audit_entries())


def test_candidates_and_archived_memories_do_not_enter_retrieval():
    with tempfile.TemporaryDirectory() as temp:
        governance, memory, _candidates = build_governance(Path(temp))
        governance.propose_from_text("我喜欢晚上学习")
        saved = memory.add_memory("我正在学习机器学习", category="learning")["memory"]
        memory.archive(int(saved["id"]))
        retriever = MemoryRetriever(memory)
        assert retriever.retrieve("机器学习下一步怎么学", limit=5) == []

    with tempfile.TemporaryDirectory() as temp:
        governance, memory, _candidates = build_governance(Path(temp))
        memory.add_memory("我通常晚上学习机器学习", category="preference")
        candidate, _ = governance.candidate_manager.add_candidate(
            "我现在更适合早晨学习机器学习",
            "preference",
            "我现在更适合早晨学习机器学习",
            reason="test conflict",
        )
        governance.accept_candidate(int(candidate["id"]), source="test")
        assert MemoryRetriever(memory).retrieve("我通常什么时候学习机器学习", limit=5) == []


def test_disabled_and_corrupt_files_degrade_safely():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        private = root / "private"
        private.mkdir(parents=True)
        (private / "memory_candidates.json").write_text("{broken", encoding="utf-8")
        (private / "memory_audit.json").write_text("{broken", encoding="utf-8")
        governance, memory, candidates = build_governance(root)
        assert candidates.pending() == []
        assert memory.audit_entries() == []
        assert list((private / "backups").glob("corrupt_memory_*.json"))
        governance.set_enabled(False)
        assert governance.propose_from_text("我喜欢晚上学习")["status"] == "disabled"


async def request(app, method, path, *, host="127.0.0.1", **kwargs):
    transport = httpx.ASGITransport(app=app, client=(host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://roxy.test") as client:
        return await client.request(method, path, **kwargs)


@contextmanager
def local_token(value):
    previous = os.environ.get("ROXY_LOCAL_TOKEN")
    if value is None:
        os.environ.pop("ROXY_LOCAL_TOKEN", None)
    else:
        os.environ["ROXY_LOCAL_TOKEN"] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ROXY_LOCAL_TOKEN", None)
        else:
            os.environ["ROXY_LOCAL_TOKEN"] = previous


def test_conversation_generation_and_web_governance_api():
    async def scenario():
        with tempfile.TemporaryDirectory() as temp:
            service = build_service(Path(temp))
            service.handle("我的长期目标是掌握机器学习", "web_candidate")
            assert len(service.memory_candidate_manager.pending()) == 1
            service.handle("梯度下降为什么这样更新", "web_candidate")
            assert len(service.memory_candidate_manager.pending()) == 1

            app = create_app(agent_service=service)
            listed = await request(app, "GET", "/v1/memory-candidates")
            candidate_id = listed.json()["candidates"][0]["id"]
            preview = await request(
                app,
                "POST",
                f"/v1/memory-candidates/{candidate_id}/accept",
                json={"confirmed": False},
            )
            assert preview.status_code == 409
            assert service.memory_manager.memories() == []

            accepted = await request(
                app,
                "POST",
                f"/v1/memory-candidates/{candidate_id}/accept-edited",
                json={"content": "我的长期目标是系统掌握机器学习", "confirmed": True},
            )
            assert accepted.status_code == 200
            assert len(service.memory_manager.memories()) == 1
            audit = await request(app, "GET", "/v1/memory-audit")
            assert audit.json()["entries"]

            pending = service.memory_governance.propose_from_text("我喜欢安静学习")["candidate"]
            with local_token("secret"):
                denied = await request(
                    app,
                    "POST",
                    f"/v1/memory-candidates/{pending['id']}/reject",
                    host="192.168.1.50",
                    headers={"X-Roxy-Token": "wrong"},
                )
            assert denied.status_code == 401
            assert service.memory_candidate_manager.get(int(pending["id"]))["status"] == "pending"

    asyncio.run(scenario())


if __name__ == "__main__":
    test_candidate_generation_quality_and_sensitive_boundary()
    test_accept_reject_edit_deduplicate_and_audit()
    test_conflict_does_not_overwrite_and_all_resolutions()
    test_candidates_and_archived_memories_do_not_enter_retrieval()
    test_disabled_and_corrupt_files_degrade_safely()
    test_conversation_generation_and_web_governance_api()
    print("memory governance tests passed")
