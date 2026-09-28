import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.confirmation_manager import ConfirmationManager
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry
from tests.isolation_support import build_isolated_memory_manager


def test_registry_owns_one_confirmation_matrix():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = build_isolated_memory_manager(root)
        registry = create_roxy_tool_registry(growth, memory)

        for name in (
            "show_plan",
            "play_dance",
            "accept_memory_candidate",
            "archive_memory",
            "restore_memory",
        ):
            assert registry.get(name).confirmation_policy == "never"
        for name in (
            "add_plan",
            "add_action_log",
            "update_plan",
            "complete_plan",
            "reschedule_plan",
        ):
            assert registry.get(name).confirmation_policy == "when_ambiguous"
        for name in ("delete_plan", "delete_memory", "delete_all_memories"):
            assert registry.get(name).confirmation_policy == "always"
        assert registry.get("archive_memory").reversible is True


def test_clear_add_executes_but_ambiguous_add_requires_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = build_isolated_memory_manager(root)
        registry = create_roxy_tool_registry(growth, memory)
        executor = ToolExecutor(registry, SafetyPolicy(), ConfirmationManager())
        clear = executor.execute(
            "add_plan",
            {"title": "测试确认策略"},
            require_confirmation=False,
            confidence=1.0,
        )
        assert clear.success is True
        assert clear.error_code is None

        ambiguous = executor.execute(
            "add_plan",
            {"title": "另一个测试计划"},
            require_confirmation=True,
            confidence=0.2,
        )
        assert ambiguous.success is False
        assert ambiguous.error_code == "confirmation_required"


if __name__ == "__main__":
    test_registry_owns_one_confirmation_matrix()
    test_clear_add_executes_but_ambiguous_add_requires_confirmation()
    print("confirmation policy tests passed")
