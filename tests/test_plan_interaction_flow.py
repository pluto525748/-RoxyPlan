import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service


def test_natural_add_show_complete_and_duration():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        added = service.handle(
            "下午帮我留50分钟学特征工程，放进今天要做的事里",
            "plan-flow",
        )
        shown = service.handle("看看我今天要做什么", "plan-flow")
        completed = service.handle("特征工程学完了", "plan-flow")

        tasks = growth.tasks()
        assert added.status == "completed"
        assert len(tasks) == 1
        assert "特征工程" in tasks[0]["title"]
        assert int(tasks[0].get("duration_minutes") or 0) == 50
        assert "特征工程" in shown.message
        assert completed.status == "completed"
        assert growth.tasks()[0]["done"] is True


def test_vague_plan_clarifies_and_similar_plan_is_not_duplicated():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        vague = service.handle("我下午想学一会儿", "plan-vague")
        assert vague.status == "clarification"
        assert growth.tasks() == []

        first = service.handle("添加计划：今晚学习机器学习40分钟", "plan-dup")
        duplicate = service.handle("再加一条今晚学习机器学习", "plan-dup")
        assert first.status == "completed"
        assert duplicate.status == "clarification"
        assert len(growth.tasks()) == 1


def test_plan_references_do_not_cross_conversations():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("添加计划：整理RoxyPlan文档", "old-session")
        service.handle("查看计划", "old-session")
        response = service.handle("刚才那个改成一小时", "new-session")
        assert response.status == "clarification"
        assert growth.tasks()[0].get("duration_minutes") in {None, 0}


if __name__ == "__main__":
    test_natural_add_show_complete_and_duration()
    test_vague_plan_clarifies_and_similar_plan_is_not_duplicated()
    test_plan_references_do_not_cross_conversations()
    print("plan interaction flow tests passed")
