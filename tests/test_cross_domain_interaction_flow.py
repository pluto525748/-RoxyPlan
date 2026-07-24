import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, RecordingLLM, build_service


def test_two_independent_actions_execute_once_with_true_results():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        growth.add_task("今晚学习机器学习", duration_minutes=30)
        response = service.handle(
            "把今晚学习计划改成40分钟，再记录今天完成了V1.7接入",
            "multi",
        )
        assert response.status in {"completed", "partial_success"}
        assert int(growth.tasks()[0].get("duration_minutes") or 0) == 40
        assert len(growth.records_for_date()) == 1
        assert all(result.message not in {None, "None", "null"} for result in response.tool_results)


def test_dance_is_a_single_tool_result_and_not_an_llm_claim():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("我无法跳舞。")
        service, _growth, _memory, _history = build_service(Path(temp), llm)
        response = service.handle("跳个舞看看", "dance")
        # The Qt-free Web adapter has no pet controller, so it may return a
        # deterministic unavailable result. The semantic request must still
        # route exactly once and must never fall through to the chat model.
        assert response.status in {"completed", "failed", "clarification"}
        assert [item.tool for item in response.tool_results] == ["play_dance"]
        assert llm.calls == []
        assert "无法跳舞" not in response.message


def test_plain_chat_has_one_nonempty_safe_reply():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("哈哈，今天的测试很扎实。")
        service, _growth, _memory, history = build_service(Path(temp), llm)
        response = service.handle("哈哈", "chat")
        assistant = [
            item for item in history.messages("chat") if item.get("role") == "assistant"
        ]
        assert response.status == "chat"
        assert response.message
        assert response.message not in {"None", "null"}
        assert len(assistant) == 1


if __name__ == "__main__":
    test_two_independent_actions_execute_once_with_true_results()
    test_dance_is_a_single_tool_result_and_not_an_llm_claim()
    test_plain_chat_has_one_nonempty_safe_reply()
    print("cross-domain interaction flow tests passed")
