import sys
from pathlib import Path

import pytest


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


def test_multiple_structured_references_require_selection():
    result = ReferenceResolver().resolve(
        "\u521a\u624d\u90a3\u4e2a\u6539\u621050\u5206\u949f",
        context(),
        conversation_id="session_a",
    )
    assert result.resolved_id == ""
    assert result.needs_clarification is True
    assert result.reason == "multiple_structured_references"


def test_single_session_task_wins_over_unrelated_global_candidates():
    result = ReferenceResolver().resolve(
        "现在把它改到晚上",
        {
            "conversation_id": "session_a",
            "last_task": LAST,
            "recent_tasks": [LAST],
        },
        conversation_id="session_a",
        candidates=[PREVIOUS, LAST],
    )
    assert result.resolved_id == "task_second"
    assert result.needs_clarification is False
    assert result.reason == "unique_structured_task"


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


def test_arabic_plan_id_is_not_reinterpreted_as_display_ordinal():
    result = ReferenceResolver().resolve(
        "确认删除计划1",
        context(),
        candidates=[PREVIOUS, LAST],
    )
    assert result.resolved_id == "1"
    assert result.reason == "explicit_id"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("计划一完成了", "task_first"),
        ("计划二完成了", "task_second"),
        ("第一项完成了", "task_first"),
        ("第二项完成了", "task_second"),
        ("第一个完成了", "task_first"),
        ("第二个完成了", "task_second"),
        ("第1项完成了", "task_first"),
        ("第2个完成了", "task_second"),
    ],
)
def test_plan_display_ordinal_resolves_to_stable_candidate_uid(text, expected):
    result = ReferenceResolver().resolve(text, {}, candidates=[PREVIOUS, LAST])
    assert result.resolved_id == expected
    assert result.reason == "plan_display_ordinal"
    assert result.needs_clarification is False


def test_out_of_range_plan_display_ordinal_requires_clarification():
    result = ReferenceResolver().resolve("第三项完成了", {}, candidates=[PREVIOUS, LAST])
    assert result.resolved_id == ""
    assert result.needs_clarification is True
    assert result.reason == "plan_ordinal_unavailable"
    assert result.candidate_ids == ["task_first", "task_second"]


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
