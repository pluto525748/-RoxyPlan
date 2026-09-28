from __future__ import annotations

import pytest

from v18_test_support import NoCallLLM, build_service


def test_explicit_generate_today_review_saves_and_refreshes_verified_snapshot(
    tmp_path,
):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    task = growth.add_task("核对发布清单")
    growth.complete_by_id(int(task["id"]))
    growth.add_record("完成依赖检查")

    first = service.handle("给我生成今天的复盘", "v22-review-save-today")
    first_entry = first.tool_results[0].data["entry"]

    assert first.status == "completed"
    assert [item.tool for item in first.tool_results] == ["save_daily_review"]
    assert first_entry["review"]["action_count"] == 1
    assert first_entry["revision"] == 1

    growth.add_record("补完封版说明")
    second = service.handle("生成今日成长复盘", "v22-review-save-today")
    second_entry = second.tool_results[0].data["entry"]
    monthly = service.handle("查看本月成长日志", "v22-review-month")

    assert second.status == "completed"
    assert second_entry["revision"] == 2
    assert second_entry["review"]["action_count"] == 2
    assert second_entry["review"]["actions"] == ["完成依赖检查", "补完封版说明"]
    saved = monthly.tool_results[0].data["entries"]
    assert len(saved) == 1
    assert saved[0]["review"]["action_count"] == 2


def test_reflective_today_review_remains_read_only(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("仍待处理的事项")

    response = service.handle("帮我看看今天完成了什么", "v22-review-preview")

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == [
        "generate_daily_review"
    ]
    assert growth.entries() == []


@pytest.mark.parametrize(
    "utterance",
    [
        "生成并保存今天的复盘",
        "请帮我生成并保存今日成长复盘",
        "生成今天的复盘并保存",
    ],
)
def test_generate_and_save_review_equivalent_commands_use_verified_tool_result(
    tmp_path,
    utterance,
):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    task = growth.add_task("核验组合复盘命令")
    growth.complete_by_id(int(task["id"]))
    growth.add_record("记录组合复盘验收")

    response = service.handle(utterance, f"review-save-{utterance}")

    assert response.status == "completed"
    assert [(item.tool, item.success) for item in response.tool_results] == [
        ("save_daily_review", True)
    ]
    assert len(growth.entries()) == 1
    assert growth.entries()[0]["review"]["action_count"] == 1
