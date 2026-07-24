import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.action_batch import ActionBatch
from modules.contracts import AgentStep, ToolResult
from modules.tool_execution_plan import ToolExecutionPlan


class FakeExecutor:
    def __init__(self, failed_tools=()):
        self.failed_tools = set(failed_tools)
        self.calls = []

    def execute(self, tool, arguments, **kwargs):
        self.calls.append((tool, dict(arguments), kwargs.get("tool_call_id")))
        success = tool not in self.failed_tools
        return ToolResult(
            success,
            tool,
            "ok" if success else "failed",
            {"changed_resource_ids": [tool]} if success else {},
            None if success else "failed",
        )


def test_batch_is_bounded_ordered_and_dependency_failure_skips():
    steps = [
        AgentStep("first", depends_on_previous=False),
        AgentStep("dependent", depends_on_previous=True),
        AgentStep("independent", depends_on_previous=False),
        AgentStep("overflow", depends_on_previous=False),
    ]
    batch = ActionBatch.from_steps("c", steps, max_actions=3)
    assert len(batch.ordered_actions) == 3

    executor = FakeExecutor({"first"})
    result = ToolExecutionPlan(executor).execute(batch, confidence=1.0)
    assert [item[0] for item in executor.calls] == ["first", "independent"]
    assert result.status == "partial_success"
    assert len(result.skipped_action_ids) == 1


def test_same_batch_is_idempotent_but_new_batch_can_run_again():
    executor = FakeExecutor()
    plan = ToolExecutionPlan(executor)
    first_batch = ActionBatch.from_steps("c", [AgentStep("write")])
    first = plan.execute(first_batch, confidence=1.0)
    replay = plan.execute(first_batch, confidence=1.0)
    second_batch = ActionBatch.from_steps("c", [AgentStep("write")])
    second = plan.execute(second_batch, confidence=1.0)

    assert first.status == "completed"
    assert replay.skipped_action_ids
    assert second.status == "completed"
    assert [item[0] for item in executor.calls] == ["write", "write"]


if __name__ == "__main__":
    test_batch_is_bounded_ordered_and_dependency_failure_skips()
    test_same_batch_is_idempotent_but_new_batch_can_run_again()
    print("action batch tests passed")
