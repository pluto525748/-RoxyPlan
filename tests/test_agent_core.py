import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry


def make_core(root):
    growth = GrowthManager(root / "growth")
    memory = MemoryManager(root / "memory.json", backup_dir=root / "backups", conflict_file=root / "conflicts.json")
    registry = create_roxy_tool_registry(growth, memory)
    executor = ToolExecutor(registry, SafetyPolicy())
    return growth, memory, AgentCore(AgentPlanner(registry), executor)


def test_single_and_multi_step_execution():
    with tempfile.TemporaryDirectory() as temp:
        growth, _memory, core = make_core(Path(temp))
        growth.add_task("学习机器学习")
        shown = core.process("看看计划", {"intent": "show_plan", "confidence": 0.9, "entities": {}})
        multi = core.process(
            "机器学习学完了，顺便记录今天学习了逻辑回归",
            {"intent": "multi_action", "confidence": 0.9, "entities": {"completed_task": "机器学习", "action_log": "今天学习了逻辑回归"}},
        )
        assert shown.status == "completed"
        assert multi.status == "completed"
        assert growth.tasks()[0]["done"] is True
        assert len(growth.records_for_date()) == 1


def test_failed_first_step_stops_dependent_step():
    with tempfile.TemporaryDirectory() as temp:
        growth, _memory, core = make_core(Path(temp))
        response = core.process(
            "不存在的任务完成了，顺便记录",
            {"intent": "multi_action", "confidence": 0.9, "entities": {"completed_task": "不存在", "action_log": "不应写入"}},
        )
        assert response.status == "clarification"
        assert growth.records_for_date() == []


def test_dangerous_requests_require_confirmation_and_questions_do_not_execute():
    with tempfile.TemporaryDirectory() as temp:
        _growth, memory, core = make_core(Path(temp))
        item = memory.add_memory("测试记忆", category="other")["memory"]
        denied = core.process(
            "不要删除记忆1",
            {"intent": "delete_memory", "confidence": 1.0, "entities": {"memory_id": item["id"]}},
        )
        assert denied.status == "failed"
        assert memory.get(item["id"]) is not None
        asked = core.process(
            "我只是想问怎么删除记忆1",
            {"intent": "delete_memory", "confidence": 1.0, "entities": {"memory_id": item["id"]}},
        )
        assert asked.status == "failed"
        assert memory.get(item["id"]) is not None
        pending = core.process(
            "删除记忆1",
            {"intent": "delete_memory", "confidence": 1.0, "entities": {"memory_id": item["id"]}},
        )
        assert pending.status == "confirmation_required"
        confirmed = core.handle_confirmation("确认")
        assert confirmed.status == "completed"
        assert memory.get(item["id"]) is None


def test_chat_falls_through():
    with tempfile.TemporaryDirectory() as temp:
        _growth, _memory, core = make_core(Path(temp))
        assert core.process("你好", {"intent": "chat", "confidence": 0.0, "entities": {}}).status == "chat"


if __name__ == "__main__":
    test_single_and_multi_step_execution()
    test_failed_first_step_stops_dependent_step()
    test_dangerous_requests_require_confirmation_and_questions_do_not_execute()
    test_chat_falls_through()
    print("agent core tests passed")
