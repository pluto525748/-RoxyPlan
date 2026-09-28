import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.tool_registry import create_roxy_tool_registry
from tests.isolation_support import build_isolated_memory_manager


def test_plan_writes_return_verified_resource_ids():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = build_isolated_memory_manager(root)
        registry = create_roxy_tool_registry(growth, memory)

        added = registry.get("add_plan").handler(
            title="学习线性规划",
            time_slot="下午",
            duration_minutes=50,
        )
        uid = added.data["task"]["uid"]
        assert added.success and added.data["postcondition_verified"] is True
        assert uid in added.data["changed_resource_ids"]

        updated = registry.get("update_plan").handler(
            task_ref=uid,
            changes={"duration_minutes": 60},
        )
        assert updated.success
        assert updated.data["task"]["title"] == "学习线性规划"
        assert updated.data["task"]["duration_minutes"] == 60
        assert growth.tasks()[0]["duration_minutes"] == 60

        completed = registry.get("complete_plan").handler(match_text="学习线性规划")
        assert completed.success
        assert completed.data["task"]["done"] is True

        deleted = registry.get("delete_plan").handler(task_ref=uid)
        assert deleted.success
        assert deleted.data["postcondition_verified"] is True
        assert growth.tasks() == []


def test_false_handler_success_fails_authoritative_reread():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = build_isolated_memory_manager(root)
        task = growth.add_task("保持50分钟", duration_minutes=50)
        original_update = growth.plan_store.update_task

        def fake_update(*_args, **_kwargs):
            fake = dict(task)
            fake["duration_minutes"] = 60
            return fake, True, "updated"

        growth.plan_store.update_task = fake_update
        try:
            registry = create_roxy_tool_registry(growth, memory)
            result = registry.get("update_plan").handler(
                task_ref=task["uid"],
                changes={"duration_minutes": 60},
            )
        finally:
            growth.plan_store.update_task = original_update
        assert result.success is False
        assert result.error_code == "postcondition_failed"
        assert growth.tasks()[0]["duration_minutes"] == 50


if __name__ == "__main__":
    test_plan_writes_return_verified_resource_ids()
    test_false_handler_success_fails_authoritative_reread()
    print("plan postcondition tests passed")
