import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.action_claim_guard import ActionClaimGuard
from modules.chat_history_manager import ChatHistoryManager
from modules.contracts import ToolResult
from modules.conversation_state import ConversationStateManager
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter
from modules.memory_manager import MemoryManager
from server.agent_service import AgentService


class NoCallLLM:
    def chat(self, _messages):
        raise AssertionError("deterministic memory requests must not call the LLM")


def build_service(root: Path) -> AgentService:
    private = root / "data" / "private"
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
        audit_file=private / "memory_audit.json",
    )
    return AgentService(
        project_root=root,
        growth_manager=GrowthManager(private),
        memory_manager=memory,
        chat_history_manager=ChatHistoryManager(private),
        llm_client=NoCallLLM(),
    )


def candidate_id(result) -> int:
    return int(result.data["candidate"]["id"])


def test_memory_query_guard_daily_phrasings():
    router = IntentRouter()
    cases = {
        "你记得我什么": "show_memory",
        "你记住了哪些关于我的信息": "show_memory",
        "你还知道关于我的什么内容": "show_memory",
        "你对我有什么了解": "show_memory",
        "你保存了我的哪些信息": "show_memory",
        "我以前告诉过你哪些事情": "show_memory",
        "我的长期记忆有什么": "show_memory",
        "有哪些记忆冲突": "show_memory_conflicts",
        "查看已归档记忆": "show_archived_memories",
    }
    for text, intent in cases.items():
        result = router.route(text)
        assert result["intent"] == intent, (text, result)
        assert result["needs_confirmation"] is False
        assert result["source"] in {"fixed_command", "memory_query_guard", "rule"}


def test_candidate_words_outside_memory_management_remain_normal_chat():
    router = IntentRouter(enable_llm=False)
    for text in (
        "我有一个候选方案",
        "这个方案还没确认吗",
        "有哪些候选计划",
        "候选人今天表现不错",
    ):
        assert router.route(text)["intent"] == "chat", text

    for text in (
        "待审核候选",
        "有哪些待确认记忆",
        "我还有什么记忆没审核",
        "列一下记忆候选",
        "确认候选4",
        "忽略候选5",
    ):
        result = router.route(text)
        assert result["intent"] == "chat", text
        assert result["source"] == "retired_memory_candidate", text


def test_retired_candidate_memory_requests_have_zero_tool_and_clear_guidance():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_service.set_candidates_enabled(True)
        created = service.memory_service.create_candidate(
            "我习惯每周复盘一次",
            source="compatibility_test",
            explicit=False,
        )
        assert created.success
        before = service.memory_service.list_candidates().data["candidates"]

        for index, text in enumerate(
            (
                "查看待审核记忆",
                "确认候选1",
                "拒绝候选1",
                "删除候选1",
                "清空待确认记忆",
            ),
            start=1,
        ):
            response = service.handle(text, f"retired-candidate-{index}")
            assert response.status == "failed"
            assert response.tool_results == []
            assert "候选记忆功能已经停用" in response.message
            assert "记住：完整内容" in response.message
            assert "查看长期记忆" in response.message

        after = service.memory_service.list_candidates().data["candidates"]
        assert after == before


def test_read_only_queries_use_real_data_without_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_manager.add_memory("我正在学习机器学习", category="learning")
        service.memory_service.set_candidates_enabled(True)
        pending = service.memory_service.create_candidate("RoxyPlan 是我的长期项目")

        remembered = service.handle("你还知道关于我的什么内容", "query-a")

        assert remembered.status == "completed"
        assert "你正在学习机器学习" in remembered.message
        assert "RoxyPlan 是我的长期项目" not in remembered.message
        assert "待审核" not in remembered.message
        assert "候选" not in remembered.message
        assert service.confirmation_manager.pending(scope="query-a") is None
        assert remembered.message not in {"", "None", "null"}


def test_explicit_memory_saves_formally_while_ordinary_statement_stays_chat():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))

        assert IntentRouter(enable_llm=False).route("我喜欢早上安静学习")["intent"] == "chat"
        assert service.memory_service.list_candidates().data["candidates"] == []
        assert service.memory_service.list_memories().data["memories"] == []

        explicit = service.handle("请记住：RoxyPlan 是我的长期项目", "candidate-explicit")
        assert explicit.status == "completed"
        assert len(service.memory_service.list_candidates().data["candidates"]) == 0
        memories = service.memory_service.list_memories().data["memories"]
        assert [item["content"] for item in memories] == ["RoxyPlan 是我的长期项目"]
        assert service.confirmation_manager.pending(scope="candidate-explicit") is None


def test_candidate_batch_compatibility_api_uses_true_ids_and_reports_partial_results():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_service.set_candidates_enabled(True)
        created = [
            service.memory_service.create_candidate("我喜欢早上安静学习"),
            service.memory_service.create_candidate("RoxyPlan 是我的长期项目"),
            service.memory_service.create_candidate("我周末会整理项目文档"),
        ]
        ids = [candidate_id(item) for item in created]

        accepted = service.memory_service.accept_candidates(ids[:2])
        assert accepted.success is True
        assert accepted.data["accepted_ids"] == ids[:2]
        assert [item["id"] for item in service.memory_service.list_candidates().data["candidates"]] == [ids[2]]

        partial = service.memory_service.accept_candidates([ids[2], 999, ids[2]])
        assert partial.success is True
        assert partial.status == "partial_success"
        assert partial.data["accepted_ids"] == [ids[2]]
        assert partial.data["failed_ids"] == [999]
        assert partial.data["already_processed_ids"] == []

        repeated = service.memory_service.accept_candidates([ids[2]])
        assert repeated.status == "already_processed"
        assert repeated.data["already_processed_ids"] == [ids[2]]


def test_candidate_compatibility_api_accepts_and_rejects_true_ids():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_service.set_candidates_enabled(True)
        ids = [
            candidate_id(service.memory_service.create_candidate(text))
            for text in (
                "我喜欢清晨阅读",
                "我想持续学习产品设计",
                "我习惯每周复盘一次",
            )
        ]

        accepted = service.memory_service.accept_candidate(ids[1])
        ignored = service.memory_service.reject_candidate(ids[0])
        assert accepted.success
        assert ignored.success
        assert [item["id"] for item in service.memory_service.list_candidates().data["candidates"]] == [ids[2]]


def test_candidate_references_are_not_created_by_normal_chat():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.memory_service.set_candidates_enabled(True)
        item = service.memory_service.create_candidate("我想长期完善桌面交互")
        item_id = candidate_id(item)
        clock = [datetime(2026, 7, 23, 9, 0, 0)]
        state = ConversationStateManager(
            now_provider=lambda: clock[0],
            memory_reference_ttl_seconds=30,
        )
        service.conversation_service.state_manager = state
        service.interaction_coordinator.now_provider = lambda: clock[0]
        service.interaction_coordinator.ttl_seconds = 30

        assert state.memory_interaction_context("session-a")["last_listed_candidate_ids"] == []
        assert state.memory_interaction_context("session-b")["last_listed_candidate_ids"] == []
        assert service.memory_service.get_candidate(item_id).status == "success"


def test_new_conversation_keeps_memory_data_but_not_references():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        accepted = service.memory_service.save_formal_memory("我想持续学习机器学习")
        assert accepted.success

        first = service.handle("你记得我什么", "old-conversation")
        second = service.handle("你对我有什么了解", "new-conversation")
        assert "你想持续学习机器学习" in first.message
        assert "你想持续学习机器学习" in second.message
        assert service.conversation_service.state_manager.memory_interaction_context(
            "new-conversation"
        )["last_listed_candidate_ids"] == []


def test_action_claim_guard_requires_real_query_or_write_result():
    guard = ActionClaimGuard()
    blocked_empty = guard.validate("我目前没有长期记忆。", [])
    blocked_write = guard.validate("已经全部确认并加入长期记忆。", [])
    assert "不能确认" in blocked_empty
    assert "读取真实记忆数据" not in blocked_empty
    assert "还没有执行" in blocked_write

    verified_empty = guard.validate(
        "我目前没有长期记忆。",
        [
            ToolResult(
                tool="list_memories",
                success=True,
                status="completed",
                message="listed",
                data={"memories": []},
                operation_kind="read",
            )
        ],
    )
    assert verified_empty == "我目前没有长期记忆。"


def test_private_project_files_are_not_touched():
    real_paths = [ROOT / "memory.json", ROOT / "data" / "private" / "memory_candidates.json"]
    snapshots = {
        path: path.read_bytes() if path.exists() else None
        for path in real_paths
    }
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        service.handle("请记住：我喜欢在安静的环境学习", "private-safety")
    for path, before in snapshots.items():
        after = path.read_bytes() if path.exists() else None
        assert after == before


if __name__ == "__main__":
    tests = [
        test_memory_query_guard_daily_phrasings,
        test_read_only_queries_use_real_data_without_confirmation,
        test_explicit_memory_saves_formally_while_ordinary_statement_stays_chat,
        test_candidate_batch_compatibility_api_uses_true_ids_and_reports_partial_results,
        test_candidate_compatibility_api_accepts_and_rejects_true_ids,
        test_candidate_references_are_not_created_by_normal_chat,
        test_new_conversation_keeps_memory_data_but_not_references,
        test_action_claim_guard_requires_real_query_or_write_result,
        test_private_project_files_are_not_touched,
    ]
    for test in tests:
        test()
    print("memory conversation closure tests passed (41 acceptance points)")
