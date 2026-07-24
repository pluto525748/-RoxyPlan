import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chinese_entity_parser import ChineseEntityParser


NOW = datetime(2026, 7, 22, 10, 0, 0)


def parser():
    return ChineseEntityParser(lambda: NOW)


def test_today_afternoon_and_half_hour():
    result = parser().parse(
        "\u4eca\u5929\u4e0b\u5348\u5b66\u4e60\u673a\u5668\u5b66\u4e60\u534a\u5c0f\u65f6"
    )
    assert result["date"] == "2026-07-22"
    assert result["time_period"] == "\u4e0b\u5348"
    assert result["duration_minutes"] == 30


def test_tonight_and_tomorrow_morning():
    tonight = parser().parse("\u4eca\u665a\u590d\u76d8")
    tomorrow = parser().parse("\u660e\u65e9\u8bfb\u6587\u6863")
    assert tonight["date"] == "2026-07-22"
    assert tonight["time_period"] == "\u665a\u4e0a"
    assert tomorrow["date"] == "2026-07-23"
    assert tomorrow["time_period"] == "\u4e0a\u5348"


def test_explicit_duration_change():
    result = parser().parse(
        "\u521a\u624d\u90a3\u4e2a\u6539\u621050\u5206\u949f"
    )
    assert result["duration_minutes"] == 50
    assert result["operation"] == "update"
    assert result["reference_text"] == "\u521a\u624d\u90a3\u4e2a"


def test_relative_shift_requires_original_start_time():
    result = parser().parse("\u63a8\u8fdf\u4e24\u4e2a\u5c0f\u65f6")
    assert result["shift_minutes"] == 120
    assert result["ambiguous"] is True
    assert result["clarification_question"]


def test_event_anchor_requires_concrete_time():
    result = parser().parse("\u4e0b\u73ed\u4ee5\u540e\u5b66\u4e60")
    assert result["event_anchor"] == "\u4e0b\u73ed\u4ee5\u540e"
    assert result["ambiguous"] is True


def test_ambiguous_duration_is_not_guessed():
    result = parser().parse("\u5b66\u4e6030\u4e00\u665a\u4e0a")
    assert result["ambiguous"] is True
    assert result["duration_minutes"] is None


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"Chinese entity parsing tests passed ({len(TESTS)} cases)")
