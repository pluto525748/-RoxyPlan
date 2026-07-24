import sys
import tempfile
from datetime import datetime, timedelta
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


class Clock:
    def __init__(self): self.now = datetime(2026, 7, 16, 12, 0, 0)
    def __call__(self): return self.now


def make_executor(root, clock=None):
    growth = GrowthManager(root / "growth")
    memory = MemoryManager(root / "memory.json", backup_dir=root / "backups", conflict_file=root / "conflicts.json")
    confirmation = ConfirmationManager(ttl_seconds=30, now_provider=clock or datetime.now)
    executor = ToolExecutor(create_roxy_tool_registry(growth, memory), SafetyPolicy(), confirmation)
    return growth, memory, executor


def test_unknown_invalid_low_and_medium_tools():
    with tempfile.TemporaryDirectory() as temp:
        growth, _memory, executor = make_executor(Path(temp))
        assert executor.execute("shell", {}).error == "tool_not_found"
        assert executor.execute("add_plan", {"title": "学习", "extra": 1}).error == "unexpected_parameter"
        assert executor.execute("show_plan", {}).success
        assert executor.execute("add_plan", {"title": "学习"}, confidence=0.5).error == "clarification_required"
        assert growth.tasks() == []


def test_high_risk_confirmation_expiry_and_cancel():
    with tempfile.TemporaryDirectory() as temp:
        clock = Clock()
        _growth, memory, executor = make_executor(Path(temp), clock)
        item = memory.add_memory("测试记忆", category="other")["memory"]
        requested = executor.execute("delete_memory", {"memory_id": item["id"]})
        assert requested.error == "confirmation_required"
        assert memory.get(item["id"]) is not None
        executor.confirmation_manager.cancel()
        assert executor.execute_confirmed().error == "confirmation_missing_or_expired"

        executor.execute("delete_memory", {"memory_id": item["id"]})
        clock.now += timedelta(seconds=31)
        assert executor.execute_confirmed().error == "confirmation_missing_or_expired"
        assert memory.get(item["id"]) is not None


if __name__ == "__main__":
    test_unknown_invalid_low_and_medium_tools()
    test_high_risk_confirmation_expiry_and_cancel()
    print("tool executor tests passed")
