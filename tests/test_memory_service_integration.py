import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from frontend.memory_dialog import MemoryDialog
from frontend.pet_app import ChatWindow
from modules.action_claim_guard import ActionClaimGuard
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryOperationResult, MemoryService
from modules.repositories.local_json_memory_repository import LocalJsonMemoryRepository
from modules.tool_registry import create_roxy_tool_registry


def build_service(root: Path) -> MemoryService:
    private = root / "data" / "private"
    repository = LocalJsonMemoryRepository(
        root / "memory.json",
        candidate_file=private / "memory_candidates.json",
        conflict_file=private / "memory_conflicts.json",
        backup_dir=private / "backups",
        audit_file=private / "memory_audit.json",
    )
    manager = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
        audit_file=private / "memory_audit.json",
        repository=repository,
    )
    candidates = MemoryCandidateManager(repository=repository)
    return MemoryService(manager, candidates)


def build_registry(root: Path, service: MemoryService):
    return create_roxy_tool_registry(
        GrowthManager(root / "growth"),
        service.memory_manager,
        memory_governance=service.governance,
        memory_service=service,
    )


def candidate_id_from(result) -> int:
    candidate = result.data.get("candidate", {})
    return int(candidate.get("id", 0))


def test_panel_agent_share_service_repository_and_data_root():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        dialog = MemoryDialog(memory_service=service)

        assert dialog.memory_service is service
        assert dialog.memory_manager.repository is service.repository
        assert dialog.candidate_manager.repository is service.repository
        assert registry.get("list_memories") is not None
        assert service.memory_manager.memory_file == root / "memory.json"
        assert service.candidate_manager.file_path == root / "data" / "private" / "memory_candidates.json"
        dialog.close()
        app.processEvents()


def test_candidate_written_from_each_entry_is_immediately_visible():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        dialog = MemoryDialog(memory_service=service)

        panel_side = service.create_candidate(
            "我喜欢安静地学习",
            source="desktop_panel_test",
        )
        assert panel_side.success
        queried = registry.get("list_memory_candidates").handler()
        assert queried.success
        assert candidate_id_from(panel_side) in {
            int(item["id"]) for item in queried.data["candidates"]
        }

        agent_side = registry.get("create_memory_candidate").handler(
            content="我想长期完善 RoxyPlan",
            source_text="请记住我想长期完善 RoxyPlan",
        )
        assert agent_side.success
        dialog.refresh_candidates()
        assert dialog.table.rowCount() == 2
        dialog.close()
        app.processEvents()


def test_agent_accept_updates_panel_and_formal_memory_once():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        dialog = MemoryDialog(memory_service=service)
        created = service.create_candidate("我希望持续学习机器学习")
        candidate_id = candidate_id_from(created)

        accepted = registry.get("accept_memory_candidate").handler(
            candidate_id=candidate_id
        )
        assert accepted.success
        dialog.refresh_all()
        assert dialog.table.rowCount() == 0
        memories = service.list_memories().data["memories"]
        assert [item["content"] for item in memories] == ["我希望持续学习机器学习"]

        repeated = service.accept_candidate(candidate_id)
        assert repeated is not None
        assert repeated.success is False
        assert repeated.status == "already_processed"
        assert len(service.list_memories().data["memories"]) == 1
        dialog.close()
        app.processEvents()


def test_panel_accept_and_reject_are_visible_to_agent_queries():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        dialog = MemoryDialog(memory_service=service)

        accepted_candidate = service.create_candidate("我习惯在早上整理项目")
        dialog._accept(candidate_id_from(accepted_candidate))
        memories = registry.get("list_memories").handler().data["memories"]
        assert any(item["content"] == "我习惯在早上整理项目" for item in memories)

        rejected_candidate = service.create_candidate("我想长期学习产品设计")
        rejected_id = candidate_id_from(rejected_candidate)
        dialog._reject(rejected_id)
        pending = registry.get("list_memory_candidates").handler().data["candidates"]
        assert rejected_id not in {int(item["id"]) for item in pending}
        assert all(item["content"] != "我想长期学习产品设计" for item in memories)
        dialog.close()
        app.processEvents()


def test_archive_and_restore_are_consistent_between_entries():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        dialog = MemoryDialog(memory_service=service)
        created = service.create_candidate("RoxyPlan 是我的长期项目")
        accepted = service.accept_candidate(candidate_id_from(created))
        memory_id = int(accepted.memory_id)

        archived = registry.get("archive_memory").handler(memory_id=memory_id)
        assert archived.success
        dialog.refresh_archived()
        assert dialog.archived_table.rowCount() == 1
        assert service.list_memories().data["memories"] == []

        dialog._restore(memory_id)
        active = registry.get("list_memories").handler().data["memories"]
        assert [item["id"] for item in active] == [memory_id]
        dialog.close()
        app.processEvents()


def test_new_conversation_keeps_service_memories_and_candidates():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        formal = service.create_candidate("我长期在学习机器学习")
        service.accept_candidate(candidate_id_from(formal))
        pending = service.create_candidate("我想长期维护 RoxyPlan")
        pending_id = candidate_id_from(pending)
        history = ChatHistoryManager(root / "chat")
        window = ChatWindow(
            growth_service=GrowthManager(root / "growth"),
            chat_history_manager=history,
            memory_service=service,
        )
        original_service = window.memory_service
        original_repository = window.memory_service.repository
        old_session = window.current_session_id

        window.new_chat_session()

        assert window.current_session_id != old_session
        assert window.memory_service is original_service
        assert window.memory_service.repository is original_repository
        assert len(window.memory_service.list_memories().data["memories"]) == 1
        assert pending_id in {
            int(item["id"])
            for item in window.memory_service.list_candidates().data["candidates"]
        }
        window.close()
        app.processEvents()


def test_invalid_and_missing_ids_return_stable_results():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        results = [
            service.accept_candidate(999),
            service.get_memory(999),
            service.accept_candidate(0),
            service.archive_memory(-1),
            service.delete_memory("bad"),
        ]
        assert all(isinstance(item, MemoryOperationResult) for item in results)
        assert all(item is not None for item in results)
        assert [item.status for item in results[:2]] == ["not_found", "not_found"]
        assert all(item.status == "validation_error" for item in results[2:])


def test_deterministic_memory_phrases_and_explicit_candidate_ids():
    router = IntentRouter(enable_llm=False)
    for text in (
        "你记得我什么",
        "你知道我什么",
        "你了解我什么",
        "你记住了哪些信息",
        "我的长期记忆有哪些",
    ):
        assert router.route(text)["intent"] == "show_memory"
    for text in (
        "有哪些待确认记忆",
        "待审核记忆有哪些",
        "哪些记忆还没确认",
    ):
        result = router.route(text)
        assert result["intent"] == "chat"
        assert result["source"] == "retired_memory_candidate"
    accepted = router.route("确认候选4")
    rejected = router.route("忽略候选5")
    assert accepted["intent"] == "chat"
    assert accepted["source"] == "retired_memory_candidate"
    assert rejected["intent"] == "chat"
    assert rejected["source"] == "retired_memory_candidate"


def test_accept_all_uses_ids_returned_by_repository():
    with tempfile.TemporaryDirectory() as temp:
        service = build_service(Path(temp))
        first = service.create_candidate("我喜欢早上安静学习")
        second = service.create_candidate("我想长期完善桌宠项目")
        expected_ids = [candidate_id_from(first), candidate_id_from(second)]
        result = service.accept_all_pending()
        assert result.success
        assert result.data["candidate_ids"] == expected_ids
        assert service.list_candidates().data["candidates"] == []
        assert len(service.list_memories().data["memories"]) == 2


def test_tool_results_match_persisted_state_and_positive_id_schema():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        registry = build_registry(root, service)
        created = registry.get("create_memory_candidate").handler(
            content="我更喜欢用对话方式学习",
            source_text="请记住我更喜欢用对话方式学习",
        )
        candidate_id = int(created.data["candidate"]["id"])
        accepted = registry.get("accept_memory_candidate").handler(
            candidate_id=candidate_id
        )
        assert accepted.success
        assert service.candidate_manager.get(candidate_id)["status"] == "accepted"
        assert len(service.list_memories().data["memories"]) == 1

        contracts = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in registry.model_tool_schemas()
        }
        assert "accept_memory_candidate" not in contracts
        assert "archive_memory" not in contracts
        assert registry.get("accept_memory_candidate").parameters_schema[
            "candidate_id"
        ]["minimum"] == 1
        assert registry.get("archive_memory").parameters_schema["memory_id"][
            "minimum"
        ] == 1


def test_action_claim_guard_requires_matching_memory_tool_success():
    from modules.contracts import ToolResult

    guard = ActionClaimGuard()
    claim = "已经把这条加入待确认记忆了。"
    blocked_without_result = guard.validate(claim, [])
    blocked_with_unrelated_result = guard.validate(
        claim,
        [ToolResult(True, "add_plan", "added")],
    )
    allowed = guard.validate(
        claim,
        [
            ToolResult(
                True,
                "create_memory_candidate",
                "added",
                operation_kind="write",
            )
        ],
    )
    blocked_already_processed = guard.validate(
        claim,
        [
            ToolResult(
                True,
                "create_memory_candidate",
                "duplicate",
                status="already_processed",
            )
        ],
    )
    assert "还没有执行" in blocked_without_result
    assert "还没有执行" in blocked_with_unrelated_result
    assert "还没有执行" in blocked_already_processed
    assert allowed == claim


def test_temp_service_does_not_touch_project_private_memory():
    project_memory = ROOT / "memory.json"
    before = project_memory.read_bytes() if project_memory.exists() else None
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        service = build_service(root)
        created = service.create_candidate("我想长期学习 Agent 架构")
        assert service.accept_candidate(candidate_id_from(created)).success
        assert (root / "memory.json").exists()
    after = project_memory.read_bytes() if project_memory.exists() else None
    assert after == before


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"memory service integration tests passed ({len(TESTS)} cases)")
