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
    reply = guard.validate(text, [ToolResult(True, "add_plan", "added")])
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


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"action claim guard tests passed ({len(TESTS)} cases)")
