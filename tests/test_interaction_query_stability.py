import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service


def test_today_plan_is_deterministic_for_twenty_rounds():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("学习特征工程", duration_minutes=50)
        for index in range(20):
            response = service.handle("今日计划", f"stable-query-{index}")
            assert response.status == "completed"
            assert response.pending_confirmation is None
            assert response.tool_results
            assert response.tool_results[0].tool == "show_plan"
            assert "特征工程" in response.message


def test_plan_query_variants_use_the_read_tool():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("整理项目文档")
        for text in (
            "我的计划",
            "今天的计划",
            "今天要做什么",
            "列出今日计划",
            "看看今天安排了什么",
            "我今天还有哪些任务",
        ):
            response = service.handle(text, "query-variants")
            assert response.status == "completed"
            assert response.tool_results[0].tool == "show_plan"


if __name__ == "__main__":
    test_today_plan_is_deterministic_for_twenty_rounds()
    test_plan_query_variants_use_the_read_tool()
    print("interaction query stability tests passed")
