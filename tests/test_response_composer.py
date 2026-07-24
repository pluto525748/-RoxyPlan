import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import AgentResponse, ToolResult
from modules.response_composer import ResponseComposer


def test_empty_and_none_like_messages_have_public_fallback():
    response = ResponseComposer().compose(AgentResponse("completed", ""))
    assert response.message
    assert response.message not in {"None", "null"}


def test_verified_candidate_id_and_partial_results_are_reported():
    accepted = ToolResult(
        True,
        "accept_memory_candidate",
        "completed",
        {"memory_operation": {"candidate_id": 7}},
    )
    response = ResponseComposer().compose(
        AgentResponse("completed", "", tool_results=[accepted])
    )
    assert "7" in response.message

    failed = ToolResult(False, "add_action_log", "failed", {}, "write_failed")
    partial = ResponseComposer().compose(
        AgentResponse(
            "partial_success",
            "",
            tool_results=[accepted, failed],
        )
    )
    assert "7" in partial.message
    assert "没有完成" in partial.message


def test_unverified_success_claim_is_blocked():
    response = ResponseComposer().compose(
        AgentResponse("completed", "已经帮你保存好了。"),
        user_text="帮我保存这个",
    )
    assert "保存好了" not in response.message


def test_empty_plan_claim_requires_a_real_successful_query():
    composer = ResponseComposer()
    blocked = composer.compose(
        AgentResponse("chat", "今天还没有计划。"),
        user_text="我今天有计划吗",
    )
    verified = composer.compose(
        AgentResponse(
            "completed",
            "今天还没有计划。",
            tool_results=[ToolResult(True, "show_plan", "listed", {"tasks": []})],
        )
    )
    wrong_tool = composer.compose(
        AgentResponse(
            "completed",
            "计划已经添加。",
            tool_results=[ToolResult(True, "show_plan", "listed", {"tasks": []})],
        )
    )
    assert "读取真实数据" in blocked.message
    assert verified.message == "今天还没有计划。"
    assert "已经添加" not in wrong_tool.message


if __name__ == "__main__":
    test_empty_and_none_like_messages_have_public_fallback()
    test_verified_candidate_id_and_partial_results_are_reported()
    test_unverified_success_claim_is_blocked()
    test_empty_plan_claim_requires_a_real_successful_query()
    print("response composer tests passed")
