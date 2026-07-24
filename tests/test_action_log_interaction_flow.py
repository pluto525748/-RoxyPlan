import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service


def test_explicit_action_log_and_duplicate_protection():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        first = service.handle(
            "记录我早上通勤听了三个AI前沿电台",
            "action-flow",
        )
        repeated = service.handle(
            "记录我早上通勤听了三个AI前沿电台",
            "action-flow",
        )
        assert first.status == "completed"
        assert repeated.status == "completed"
        assert "已经记录" in repeated.message or "没有重复" in repeated.message
        assert len(growth.records_for_date()) == 1


def test_progress_statement_asks_before_writing_then_confirm_executes_once():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        proposal = service.handle(
            "我已经理解随机森林的两个随机性来源",
            "progress",
        )
        assert proposal.status == "clarification"
        assert growth.records_for_date() == []

        confirmed = service.handle("确认", "progress")
        repeated = service.handle("确认", "progress")
        assert confirmed.status == "completed"
        assert len(growth.records_for_date()) == 1
        assert repeated.status in {"clarification", "failed"}
        assert len(growth.records_for_date()) == 1


def test_action_record_is_not_mistaken_for_plan_completion():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("学习机器学习")
        logged = service.handle("记录今天完成了V1.8接入", "separation")
        assert logged.status == "completed"
        assert growth.tasks()[0]["done"] is False
        assert len(growth.records_for_date()) == 1


if __name__ == "__main__":
    test_explicit_action_log_and_duplicate_protection()
    test_progress_statement_asks_before_writing_then_confirm_executes_once()
    test_action_record_is_not_mistaken_for_plan_completion()
    print("action log interaction flow tests passed")
