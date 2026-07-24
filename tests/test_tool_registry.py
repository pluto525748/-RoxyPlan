import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.tool_registry import ToolResult, create_roxy_tool_registry


class FakePet:
    def start_dance(self): return True
    def agent_sleep(self): return True
    def wake(self): return True
    def pause_proactive_reminders(self): return None
    def resume_proactive_reminders(self): return None


def make_registry(root):
    return create_roxy_tool_registry(
        GrowthManager(root / "growth"),
        MemoryManager(root / "memory.json", backup_dir=root / "backups", conflict_file=root / "conflicts.json"),
        FakePet(),
    )


def test_tools_are_registered_with_required_metadata():
    with tempfile.TemporaryDirectory() as temp:
        registry = make_registry(Path(temp))
        expected = {
            "add_plan", "show_plan", "complete_plan", "delete_plan",
            "update_plan", "reschedule_plan", "reopen_plan", "cancel_plan",
            "add_action_log", "show_action_log", "generate_daily_review", "save_daily_review",
            "show_growth_log", "list_memories", "search_memories",
            "show_memory", "search_memory", "create_memory_candidate",
            "request_add_memory", "archive_memory", "restore_memory",
            "queue_memory_candidate", "list_memory_candidates", "show_memory_candidates",
            "accept_memory_candidate", "reject_memory_candidate",
            "accept_memory_candidates", "reject_memory_candidates",
            "accept_all_memory_candidates", "update_memory",
            "list_memory_conflicts", "show_memory_conflicts", "resolve_memory_conflict",
            "list_archived_memories",
            "show_memory_audit",
            "delete_memory", "delete_all_memories",
            "show_recent_conversation", "show_conversation_history",
            "pause_reminders", "resume_reminders",
            "play_dance", "sleep_pet", "wake_pet",
        }
        assert set(registry.names()) == expected
        for name in expected:
            tool = registry.get(name)
            assert tool.name == name
            assert tool.risk_level in {"low", "medium", "high"}
            assert callable(tool.handler)


def test_handlers_return_tool_result():
    with tempfile.TemporaryDirectory() as temp:
        registry = make_registry(Path(temp))
        result = registry.get("show_plan").handler()
        assert isinstance(result, ToolResult)
        serialized = result.to_dict()
        assert serialized["schema_version"] == "1.0"
        assert serialized["tool_call_id"].startswith("call_")
        assert serialized["success"] is True
        assert serialized["status"] == "completed"
        assert serialized["tool"] == "show_plan"
        assert serialized["message"] == "listed"
        assert serialized["data"] == {"tasks": []}
        assert serialized["error"] is None


if __name__ == "__main__":
    test_tools_are_registered_with_required_metadata()
    test_handlers_return_tool_result()
    print("tool registry tests passed")
