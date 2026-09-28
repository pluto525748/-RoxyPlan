import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.action_claim_guard import ActionClaimGuard
from modules.contracts import ToolResult


def test_unverified_assistant_write_claim_is_blocked():
    guard = ActionClaimGuard()
    reply = guard.validate(
        "\u6211\u5df2\u7ecf\u5e2e\u4f60\u6dfb\u52a0\u8ba1\u5212\u4e86\u3002",
        [],
        user_text="\u4e0b\u5348\u5b66\u4e60\u600e\u4e48\u6837",
    )
    assert "\u8fd8\u6ca1\u6709\u6267\u884c" in reply


def test_verified_write_claim_is_allowed():
    guard = ActionClaimGuard()
    text = "\u6211\u5df2\u7ecf\u5e2e\u4f60\u6dfb\u52a0\u8ba1\u5212\u4e86\u3002"
    reply = guard.validate(
        text,
        [ToolResult(True, "add_plan", "added", operation_kind="write")],
    )
    assert reply == text


def test_user_progress_echo_is_not_blocked():
    guard = ActionClaimGuard()
    text = "\u4f60\u5df2\u7ecf\u5b8c\u6210\u4f5c\u4e1a\u4e86\uff0c\u8fd9\u4e00\u6b65\u5f88\u624e\u5b9e\u3002"
    reply = guard.validate(
        text,
        [],
        user_text="\u6211\u521a\u521a\u5b8c\u6210\u4f5c\u4e1a\u4e86",
    )
    assert reply == text


def test_verified_plan_list_status_is_not_mistaken_for_write_claim():
    guard = ActionClaimGuard()
    text = "今天的计划：\n1. [已完成] 验证桌面查询"
    reply = guard.validate(
        text,
        [
            ToolResult(
                True,
                "show_plan",
                "listed",
                {"tasks": [{"id": 1, "title": "验证桌面查询", "done": True}]},
                operation_kind="read",
            )
        ],
    )
    assert reply == text


def test_unverified_plain_completion_claim_is_still_blocked():
    guard = ActionClaimGuard()
    reply = guard.validate("计划已经完成。", [])
    assert reply != "计划已经完成。"


def test_read_result_with_completed_and_pending_statuses_is_a_verified_fact():
    guard = ActionClaimGuard()
    text = "今天的计划：\n1. [已完成] 已复习\n2. [待完成] 复习线性代数"
    reply = guard.validate(
        text,
        [ToolResult(True, "show_plan", "listed", operation_kind="read")],
    )
    assert reply == text


def test_read_result_does_not_verify_an_explicit_add_claim():
    guard = ActionClaimGuard()
    reply = guard.validate(
        "我已经帮你添加计划了。",
        [ToolResult(True, "show_plan", "listed", operation_kind="read")],
    )
    assert "还没有执行" in reply


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"action claim guard tests passed ({len(TESTS)} cases)")
