from datetime import date, timedelta
from pathlib import Path

from tests.v18_test_support import NoCallLLM, RecordingLLM, build_service


def test_real_service_keeps_wrapped_queries_read_only_and_stable(tmp_path: Path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("整理项目文档")
    for text in (
        "请今日计划可以吗",
        "麻烦你看看今天的计划谢谢",
        "查询：今日计划",
        "把今天的安排给我看看",
    ):
        response = service.handle(text, "wrapped-query")
        assert response.status == "completed"
        assert [result.tool for result in response.tool_results] == ["show_plan"]
    assert len(growth.tasks()) == 1


def test_real_service_resolves_tomorrow_query_date(tmp_path: Path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    growth.add_task("准备明日材料", date=tomorrow)

    response = service.handle("明日计划", "tomorrow-query")

    assert response.status == "completed"
    assert response.tool_results[0].tool == "show_plan"
    assert "准备明日材料" in response.message


def test_real_service_advice_and_negative_dance_never_write_or_act(tmp_path: Path):
    service, growth, _memory, _history = build_service(
        tmp_path,
        RecordingLLM("可以先了解基础概念，再挑一个小作品练习。"),
    )
    for text in ("cosplay该怎么入门", "帮我规划学习思路"):
        response = service.handle(text, "advice-boundary")
        assert response.status == "chat"
        assert response.client_actions == []
    negative = service.handle("我不想跳舞", "advice-boundary")
    assert negative.client_actions == []
    assert growth.tasks() == []
    assert growth.records_for_date() == []
