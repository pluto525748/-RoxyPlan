import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.reference_resolver import ReferenceResolver


LAST = {"id": 2, "uid": "task_second", "title": "study math"}
PREVIOUS = {"id": 1, "uid": "task_first", "title": "read docs"}


def context():
    return {
        "conversation_id": "session_a",
        "last_task": LAST,
        "recent_tasks": [PREVIOUS, LAST],
    }


def test_last_reference_uses_structured_session_state():
    result = ReferenceResolver().resolve(
        "\u521a\u624d\u90a3\u4e2a\u6539\u621050\u5206\u949f",
        context(),
        conversation_id="session_a",
    )
    assert result.resolved_id == "task_second"
    assert result.needs_clarification is False


def test_duration_number_is_not_mistaken_for_task_id():
    result = ReferenceResolver().resolve(
        "\u521a\u624d\u90a3\u4e2a\u6539\u621050\u5206\u949f",
        context(),
        conversation_id="session_a",
    )
    assert result.resolved_id != "50"


def test_previous_reference_uses_previous_structured_task():
    result = ReferenceResolver().resolve(
        "\u524d\u4e00\u4e2a\u8ba1\u5212",
        context(),
        conversation_id="session_a",
    )
    assert result.resolved_id == "task_first"


def test_explicit_task_number_still_works():
    result = ReferenceResolver().resolve("\u5b8c\u6210\u8ba1\u52122", context())
    assert result.resolved_id == "2"


def test_cross_session_reference_requires_clarification():
    result = ReferenceResolver().resolve(
        "\u521a\u624d\u90a3\u4e2a",
        context(),
        conversation_id="session_b",
    )
    assert result.needs_clarification is True
    assert result.reason == "cross_conversation_reference"


def test_missing_reference_state_requires_clarification():
    result = ReferenceResolver().resolve("\u521a\u624d\u90a3\u4e2a", {})
    assert result.needs_clarification is True


def test_multiple_candidates_are_not_guessed_without_session_state():
    result = ReferenceResolver().resolve(
        "\u8fd9\u4e2a",
        {},
        candidates=[PREVIOUS, LAST],
    )
    assert result.needs_clarification is True
    assert result.reason == "multiple_candidates"
    assert result.candidate_ids == ["task_first", "task_second"]


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"reference resolver tests passed ({len(TESTS)} cases)")
