import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.agent_planner import AgentPlanner, LLMPlanner
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter
from modules.memory_manager import MemoryManager
from modules.tool_registry import create_roxy_tool_registry


def make_registry(root):
    return create_roxy_tool_registry(
        GrowthManager(root / "growth"),
        MemoryManager(root / "memory.json", backup_dir=root / "backups", conflict_file=root / "conflicts.json"),
    )


def test_single_and_multi_step_rule_plans():
    with tempfile.TemporaryDirectory() as temp:
        planner = AgentPlanner(make_registry(Path(temp)))
        single = planner.plan("查看计划", {"intent": "show_plan", "entities": {}})
        multi = planner.plan(
            "机器学习学完了，顺便记一下今天学了逻辑回归",
            {"intent": "multi_action", "entities": {"completed_task": "机器学习", "action_log": "今天学了逻辑回归"}},
        )
        assert [step.tool for step in single.steps] == ["show_plan"]
        assert [step.tool for step in multi.steps] == ["complete_plan", "add_action_log"]
        natural = IntentRouter().route(
            "我学完机器学习了，顺便记录一下今天学习了逻辑回归"
        )
        natural_plan = planner.plan("我学完机器学习了，顺便记录一下今天学习了逻辑回归", natural)
        assert [step.tool for step in natural_plan.steps] == ["complete_plan", "add_action_log"]


def test_plan_is_bounded_and_invalid_llm_falls_back():
    with tempfile.TemporaryDirectory() as temp:
        registry = make_registry(Path(temp))
        invalid = LLMPlanner(lambda _messages: "not-json")
        planner = AgentPlanner(registry, max_steps=3, llm_planner=invalid, enable_llm=True)
        fallback = planner.plan("随便做点什么", {"intent": "unknown", "entities": {}})
        assert fallback.steps == []

        valid = LLMPlanner(lambda _messages: '{"goal":"x","steps":[' + ','.join(
            '{"tool":"show_plan","arguments":{}}' for _ in range(5)
        ) + "]}")
        planner = AgentPlanner(registry, max_steps=3, llm_planner=valid, enable_llm=True)
        bounded = planner.plan("帮我处理", {"intent": "unknown", "entities": {}})
        assert len(bounded.steps) == 3


if __name__ == "__main__":
    test_single_and_multi_step_rule_plans()
    test_plan_is_bounded_and_invalid_llm_falls_back()
    print("agent planner tests passed")
