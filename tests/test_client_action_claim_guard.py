from modules.client_action_claim_guard import ClientActionClaimGuard
from modules.client_action_result import ClientActionResult


def action_result(status, accepted=False):
    return ClientActionResult(
        action_id="act_1",
        name="play_dance",
        status=status,
        accepted=accepted,
        started=accepted,
        reason_code=status,
        display_message="failed message",
    )


def test_success_claim_requires_accepted_action():
    guard = ClientActionClaimGuard()
    assert guard.validate("好，我跳一小段。", [], action_expected=True) != "好，我跳一小段。"
    assert (
        guard.validate("好，我跳一小段。", [action_result("running", True)])
        == "好，我跳一小段。"
    )


def test_incidental_dance_words_are_not_treated_as_missing_actions():
    guard = ClientActionClaimGuard()
    message = "我记得，你喜欢看我跳舞。"
    assert guard.validate(message, [], action_expected=False) == message


def test_busy_and_failed_override_success_claim():
    guard = ClientActionClaimGuard()
    assert "还没结束" in guard.validate(
        "好，我跳一小段。", [action_result("skipped_busy")], action_expected=True
    )
    assert guard.validate(
        "好，我跳一小段。", [action_result("failed")], action_expected=True
    ) == "failed message"
