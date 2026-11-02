"""业务时钟与真实时钟分离时的建议采用回归。

桌面在模拟日期下让 GrowthManager.now_provider 指向业务 clock，而
timestamp_provider 与 InteractionStateCoordinator 仍走真实时间。旧测试把两个
clock 注入成同一天，掩盖了“业务日 24、真实日 25”时刚展示的建议立即被清空的问题。

这里显式构造双时钟：
- business：任务所属日期与“今天”判断；
- real：消息/快照捕获时间与续选 TTL。

不联网，不显示可见窗口；Qt 接线检查仅构造 offscreen 窗口。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from modules.chinese_entity_parser import ChineseEntityParser
from modules.learning_loop import LearningLoopService
from modules.suggestion_snapshot import SuggestionSnapshot
from scripts.preview_chat import build_preview_window
from v18_test_support import build_service
from test_v22_typed_memory_and_suggestions import SemanticChatLLM


ADVICE = "1. 阅读算法资料\n2. 整理错题本"


def _dual_clock_service(tmp_path, reply=ADVICE, business=None, real=None):
    service, growth, _memory, _history = build_service(tmp_path, SemanticChatLLM(reply))
    business_clock = business or {"now": datetime(2026, 10, 24, 9, 0)}
    real_clock = real or {"now": datetime(2026, 10, 25, 9, 0)}
    business_now = lambda: business_clock["now"]
    real_now = lambda: real_clock["now"]

    growth.now_provider = business_now
    growth.timestamp_provider = real_now
    conversation = service.conversation_service
    conversation.now_provider = real_now
    conversation.business_now_provider = business_now
    conversation.interaction_coordinator.now_provider = real_now
    conversation.learning_loop_service = LearningLoopService(
        growth, now_provider=business_now,
    )
    # 与桌面 ChatWindow 的依赖组合一致：业务实体解析与回复日期读业务 clock。
    business_parser = ChineseEntityParser(now_provider=business_now)
    conversation.semantic_action_parser.feature_extractor.entity_parser = business_parser
    conversation.business_resolver.entity_parser = business_parser
    conversation.response_composer.now_provider = business_now
    return service, growth, conversation, business_clock, real_clock


def _snapshot(conversation, session):
    raw = conversation.interaction_coordinator.current(session).last_suggestion_snapshot
    assert raw, "advice turn must keep a suggestion snapshot"
    return SuggestionSnapshot.from_dict(raw)


def _titles(growth, date):
    return [task["title"] for task in growth.tasks(date)]


def test_advice_shown_under_earlier_business_date_is_adoptable(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(tmp_path)
    session = "business-behind"

    advice = service.handle("请建议今天的学习计划", session)

    assert "1. 阅读算法资料" in advice.message
    assert "日期已变化" not in advice.message
    assert _snapshot(conversation, session).target_date == "2026-10-24"

    first = service.handle("第一个加入今天计划", session)
    assert [result.tool for result in first.tool_results] == ["add_plan"]
    assert first.tool_results[0].success
    assert _titles(growth, "2026-10-24") == ["阅读算法资料"]

    second = service.handle("第二个加入今天计划", session)
    assert [result.tool for result in second.tool_results] == ["add_plan"]
    assert second.tool_results[0].data["task"]["date"] == "2026-10-24"
    assert _titles(growth, "2026-10-24") == ["阅读算法资料", "整理错题本"]
    # 真实日期从未被写成计划日期。
    assert growth.tasks("2026-10-25") == []


def test_whole_advice_batch_uses_business_date_not_real_date(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(tmp_path)
    session = "business-behind-batch"

    advice = service.handle("请建议今天的学习计划", session)
    added = service.handle("全部加入今天计划", session)

    assert "日期已变化" not in advice.message
    assert [result.tool for result in added.tool_results] == ["add_plan", "add_plan"]
    assert all(result.success for result in added.tool_results)
    assert {result.data["task"]["date"] for result in added.tool_results} == {
        "2026-10-24"
    }
    assert _titles(growth, "2026-10-24") == ["阅读算法资料", "整理错题本"]
    assert growth.tasks("2026-10-25") == []


def test_advice_shown_under_later_business_date_is_adoptable(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(
        tmp_path,
        business={"now": datetime(2026, 10, 26, 9, 0)},
        real={"now": datetime(2026, 10, 25, 9, 0)},
    )
    session = "business-ahead"

    advice = service.handle("请建议今天的学习计划", session)
    added = service.handle("全部加入今天计划", session)

    assert "日期已变化" not in advice.message
    assert _snapshot(conversation, session).target_date == "2026-10-26"
    assert [result.tool for result in added.tool_results] == ["add_plan", "add_plan"]
    assert _titles(growth, "2026-10-26") == ["阅读算法资料", "整理错题本"]
    assert growth.tasks("2026-10-25") == []


def test_advice_updates_existing_plan_dated_on_business_today(tmp_path):
    service, growth, _conversation, _business, _real = _dual_clock_service(
        tmp_path, reply="1. 验收项目文档续做：只推进一个最小段落",
    )
    original = growth.add_task("验收项目文档续做", date="2026-10-24")
    session = "business-today-update"

    service.handle("请建议今天的学习计划", session)
    selected = service.handle("第一个加入今天计划", session)

    assert [result.tool for result in selected.tool_results] == ["update_plan"]
    assert selected.tool_results[0].success
    tasks = growth.tasks("2026-10-24")
    assert len(tasks) == 1
    assert tasks[0]["uid"] == original["uid"]
    assert tasks[0]["title"] == "验收项目文档续做：只推进一个最小段落"
    assert growth.tasks("2026-10-25") == []


def test_same_day_business_and_real_clock_keeps_original_flow(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(
        tmp_path,
        business={"now": datetime(2026, 10, 25, 9, 0)},
        real={"now": datetime(2026, 10, 25, 9, 0)},
    )
    session = "same-day"

    advice = service.handle("请建议今天的学习计划", session)
    assert "日期已变化" not in advice.message
    assert _snapshot(conversation, session).target_date == "2026-10-25"
    selected = service.handle("第一个加入今天计划", session)
    added = service.handle("第二个加入今天计划", session)

    assert selected.tool_results[0].success
    assert added.tool_results[0].success
    assert _titles(growth, "2026-10-25") == ["阅读算法资料", "整理错题本"]


def test_business_day_advancing_between_prepare_and_complete_is_still_rejected(tmp_path):
    service, growth, conversation, business, _real = _dual_clock_service(tmp_path)
    session = "business-advanced"

    turn = conversation.prepare(
        "请建议今天的学习计划", session, record_history=True,
        business_date="2026-10-24",
    )
    business["now"] = datetime(2026, 10, 25, 9, 0)
    reply = conversation.complete(turn)

    assert "日期已变化" in reply.message
    assert conversation._recent_suggestion_snapshot(session) is None
    selection = service.handle("第一个加入今天计划", session)
    assert selection.tool_results == []
    assert growth.tasks("2026-10-24") == []
    assert growth.tasks("2026-10-25") == []


def test_continuation_ttl_uses_real_seconds_not_business_date(tmp_path):
    service, growth, conversation, business, real = _dual_clock_service(tmp_path)
    conversation.interaction_coordinator.continuation_ttl_seconds = 60
    session = "ttl-window"

    advice = service.handle("请建议今天的学习计划", session)
    assert "日期已变化" not in advice.message
    real["now"] += timedelta(seconds=30)
    # Business date is unchanged; the real-time TTL window is still open.
    selected = service.handle("第一个加入今天计划", session)
    assert selected.tool_results[0].success
    assert _titles(growth, "2026-10-24") == ["阅读算法资料"]

    expired_session = "ttl-expired"
    service.handle("请建议今天的学习计划", expired_session)
    before = [dict(item) for item in growth.tasks("2026-10-24")]
    real["now"] += timedelta(seconds=120)
    expired = service.handle("全部加入今天计划", expired_session)
    assert expired.tool_results == []
    assert growth.tasks("2026-10-24") == before
    assert "失效" in expired.message or "不存在" in expired.message


def test_changed_snapshot_after_batch_confirmation_is_rejected_without_writes(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(tmp_path)
    session = "snapshot-changed"
    existing = growth.add_task("整理三条概念笔记", date="2026-10-24")
    conversation.interaction_coordinator.remember_suggestion_snapshot(
        session,
        SuggestionSnapshot.for_options(
            conversation_id=session,
            options=[
                {"id": "1", "title": "阅读一个课程章节"},
                {"id": "2", "title": "整理三条概念笔记"},
            ],
            captured_at=datetime(2026, 10, 25, 9, 0),
            target_date="2026-10-24",
        ),
    )

    preview = service.handle("全部加入今天计划", session)
    assert "添加未重复项" in preview.message
    assert [task["uid"] for task in growth.tasks("2026-10-24")] == [existing["uid"]]

    # A new list replaces the pending candidate set before the user confirms.
    conversation.interaction_coordinator.remember_suggestion_snapshot(
        session,
        SuggestionSnapshot.for_options(
            conversation_id=session,
            options=[{"id": "1", "title": "阅读一个课程章节"}],
            captured_at=datetime(2026, 10, 25, 9, 0),
            target_date="2026-10-24",
        ),
    )
    confirmed = service.handle("添加未重复项", session)

    assert confirmed.tool_results == []
    assert "变化" in confirmed.message or "失效" in confirmed.message
    assert [task["uid"] for task in growth.tasks("2026-10-24")] == [existing["uid"]]


def test_snapshot_does_not_cross_conversations_or_survive_consumption(tmp_path):
    service, growth, conversation, _business, _real = _dual_clock_service(tmp_path)

    service.handle("请建议今天的学习计划", "source-session")
    other = service.handle("全部加入今天计划", "other-session")
    assert other.tool_results == []
    assert growth.tasks("2026-10-24") == []

    added = service.handle("全部加入今天计划", "source-session")
    assert [result.tool for result in added.tool_results] == ["add_plan", "add_plan"]
    before = [dict(item) for item in growth.tasks("2026-10-24")]
    repeated = service.handle("全部加入今天计划", "source-session")
    assert repeated.tool_results == []
    assert growth.tasks("2026-10-24") == before
    assert conversation._recent_suggestion_snapshot("source-session") is None


def test_desktop_window_injects_business_clock_into_feature_extractor(tmp_path):
    app = QApplication.instance() or QApplication([])
    window, _old_id = build_preview_window(tmp_path / "desktop")
    try:
        assert window.semantic_action_parser.feature_extractor.entity_parser is (
            window.business_entity_parser
        )
        assert window.business_resolver.entity_parser is window.business_entity_parser
        assert window.business_entity_parser.now_provider is (
            window.growth_service.now_provider
        )
        assert window.conversation_service.business_now_provider is (
            window.growth_service.now_provider
        )
        assert window.response_composer.now_provider is (
            window.growth_service.now_provider
        )
    finally:
        window.close()
        app.processEvents()
