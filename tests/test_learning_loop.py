from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from frontend.growth_dialog import GrowthDialog
from modules.growth_manager import GrowthManager
from modules.learning_loop import LearningLoopService
from modules.suggestion_snapshot import SuggestionSnapshot
from v18_test_support import build_service
from test_v22_typed_memory_and_suggestions import SemanticChatLLM


NOW = datetime(2026, 9, 29, 9, 0, 0)


def test_snapshot_uses_existing_stores_and_keeps_missing_duration_unknown(tmp_path):
    growth = GrowthManager(tmp_path / "private", now_provider=lambda: NOW)
    first = growth.add_task("复习算法", duration_minutes=60)
    growth.add_task("整理笔记")
    growth.complete_by_id(first["id"])
    growth.add_record("做了练习", duration_minutes=25)
    growth.add_record("整理错题")

    snapshot = LearningLoopService(growth, now_provider=lambda: NOW).day_snapshot()

    assert (snapshot["total"], snapshot["completed"], snapshot["completion_rate"]) == (2, 1, 0.5)
    assert (snapshot["planned_minutes"], snapshot["planned_duration_count"]) == (60, 1)
    assert (snapshot["actual_minutes"], snapshot["actual_duration_count"]) == (25, 1)
    assert "duration_minutes" not in snapshot["actions"][1]
    with pytest.raises(ValueError):
        growth.add_record("无效时长", duration_minutes=0)


def test_planning_evidence_reads_saved_review_and_overdue_without_writing(tmp_path):
    growth = GrowthManager(tmp_path / "private", now_provider=lambda: NOW)
    growth.add_task("算法练习", date="2026-09-28", duration_minutes=60)
    growth.add_task("项目文档", date="2026-09-28", duration_minutes=60)
    growth.add_record("实际练习", date="2026-09-28", duration_minutes=20)
    saved = growth.save_today_review("2026-09-28")
    before = growth.growth_file.read_bytes()

    loop = LearningLoopService(growth, now_provider=lambda: NOW)
    evidence = loop.planning_evidence()
    context = loop.planning_context()

    assert evidence["saved_yesterday_review"]["text"] == saved["review"]["text"]
    assert evidence["recent_total"] == 2
    assert len(evidence["overdue_tasks"]) == 2
    assert "昨日 2026-09-28：计划 0/2 完成" in context
    assert "昨日已保存复盘" in context
    assert "suggestion_limit" not in evidence
    assert growth.growth_file.read_bytes() == before


def test_recent_plan_completion_uses_previous_seven_days_and_skips_empty_days(tmp_path):
    clock = lambda: datetime(2026, 10, 9, 9, 0)
    growth = GrowthManager(tmp_path / "private", now_provider=clock)
    outside = growth.add_task("八天前不计入", date="2026-10-01")
    growth.complete_by_id(outside["id"], "2026-10-01")
    first = growth.add_task("七天边界已完成", date="2026-10-02")
    growth.complete_by_id(first["id"], "2026-10-02")
    growth.add_record("仅有行动的日期", date="2026-10-05")
    growth.add_task("昨天未完成", date="2026-10-08")
    today = growth.add_task("今天不计入")
    growth.complete_by_id(today["id"])
    loop = LearningLoopService(growth, now_provider=clock)
    before = {path.name: path.read_bytes() for path in growth.private_dir.glob("*.json")}

    assert loop.recent_plan_completion(strict_read=True) == {
        "recent_total": 2, "recent_completed": 1,
    }
    evidence = loop.planning_evidence()
    assert (evidence["recent_total"], evidence["recent_completed"]) == (2, 1)
    assert {path.name: path.read_bytes()
            for path in growth.private_dir.glob("*.json")} == before


def test_growth_history_does_not_enter_plan_advice_or_ordinary_chat(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path)
    growth.add_task("昨日任务", date="2026-09-28")
    growth.save_today_review("2026-09-28")
    conversation = service.conversation_service
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=lambda: NOW)

    advice = conversation.build_llm_messages("请建议今天的学习计划", "test_session")
    ordinary = conversation.build_llm_messages("你好", "test_session")
    direct_write = conversation.build_llm_messages("把复习算法加入今天计划", "test_session")

    assert "昨日已保存复盘" not in str(advice)
    assert "昨日已保存复盘" not in str(ordinary)
    assert "昨日已保存复盘" not in str(direct_write)
    assert conversation._requests_new_plan_advice("给我一个今天的学习计划")


def test_growth_dialog_shows_saved_review_and_shared_statistics(tmp_path):
    app = QApplication.instance() or QApplication([])
    growth = GrowthManager(tmp_path / "private", now_provider=lambda: NOW)
    growth.add_task("复习算法", duration_minutes=45)
    growth.add_record("复习了算法", duration_minutes=30)
    growth.save_today_review()

    dialog = GrowthDialog(growth)

    assert "今日学习面板" in dialog.windowTitle()
    assert "完成 0/1" in dialog.today_summary.text()
    assert "计划时长 45 分钟" in dialog.today_summary.text()
    assert "实际时长 30 分钟" in dialog.today_summary.text()
    assert "今天你计划了 1 件事" in dialog.review_text.toPlainText()
    growth.add_record("补充整理笔记")
    dialog.refresh_all()
    assert "已保存复盘与当前记录不同" in dialog.review_text.toPlainText()
    dialog.close()
    app.processEvents()


def test_advice_turn_keeps_full_model_list_without_history_intervention(tmp_path):
    llm = SemanticChatLLM("1. 复习算法基础\n2. 做一道练习题")
    service, growth, _memory, _history = build_service(tmp_path, llm)
    growth.add_task("昨日算法", date="2026-09-28")
    growth.add_task("昨日练习", date="2026-09-28")
    growth.save_today_review("2026-09-28")
    service.conversation_service.learning_loop_service = LearningLoopService(
        growth, now_provider=lambda: NOW,
    )

    response = service.handle("请建议今天的学习计划", "learning-loop-advice")

    assert "调整依据：" not in response.message
    assert "1. 复习算法基础" in response.message
    assert "2. 做一道练习题" in response.message
    assert growth.tasks() == []

    selected = service.handle("第一个加入今天计划", "learning-loop-advice")
    assert selected.status != "failed"
    assert [task["title"] for task in growth.tasks()] == ["复习算法基础"]


def test_chat_action_duration_reaches_the_shared_action_store(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM("好的。"),
    )
    routed = service.intent_router.route_exact_command(
        "记录", payload_text="今天的计划外行动：复习算法，实际用了30分钟",
    )
    assert routed["entities"]["duration_minutes"] == 30
    parsed = service.conversation_service.semantic_action_parser.parse_unified(
        "记录今天的计划外行动：复习算法，实际用了30分钟", allow_llm=False,
    )
    assert parsed.candidates[0].arguments["duration_minutes"] == 30

    turn = service.conversation_service.prepare(
        "记录今天的计划外行动：复习算法，实际用了30分钟", "duration-action",
    )
    assert turn.intent_result["entities"]["duration_minutes"] == 30
    assert turn.intent_result["resolved_actions"][0]["arguments"]["duration_minutes"] == 30
    service.conversation_service.complete(turn)

    actions = growth.records_for_date()
    assert len(actions) == 1
    assert actions[0]["duration_minutes"] == 30


def test_user_explicit_candidate_count_is_not_silently_reduced(tmp_path):
    llm = SemanticChatLLM("1. 复习算法\n2. 做练习\n3. 整理错题")
    service, growth, _memory, _history = build_service(tmp_path, llm)
    growth.add_task("昨日算法", date="2026-09-28")
    growth.add_task("昨日练习", date="2026-09-28")
    service.conversation_service.learning_loop_service = LearningLoopService(
        growth, now_provider=lambda: NOW,
    )

    response = service.handle("请给我三项今天的学习计划建议", "explicit-count")

    assert "3. 整理错题" in response.message
    assert growth.tasks("2026-09-29") == []


@pytest.mark.parametrize("selection", ["就按这四条安排吧", "就按这几条安排吧"])
def test_visible_four_item_advice_all_gets_four_add_plan_results(tmp_path, selection):
    llm = SemanticChatLLM(
        "1. 阅读文档\n2. 整理这些标记\n3. 写一段总结\n4. 做一道算法题"
    )
    service, growth, _memory, history = build_service(tmp_path, llm)
    session = "four-visible-advice"

    advice = service.handle("先给我今天的学习建议，不要改计划", session)
    selected = service.handle(selection, session)

    assert "4. 做一道算法题" in advice.message
    assert [item.tool for item in selected.tool_results] == ["add_plan"] * 4
    assert all(item.success for item in selected.tool_results)
    assert [item["title"] for item in growth.tasks()] == [
        "阅读文档", "整理这些标记", "写一段总结", "做一道算法题",
    ]
    adoption = history.messages(session)[-1]["metadata"]["suggestion_adoption"]
    assert len(adoption["items"]) == 4
    assert {item["plan_uid"] for item in adoption["items"]} == {
        task["uid"] for task in growth.tasks()
    }


def test_numbered_snapshot_accepts_long_visible_list_without_truncation():
    source = "\n".join(f"{index}. 完成任务{index}" for index in range(1, 26))
    options = SuggestionSnapshot.numbered_options(source)
    assert len(options) == 25
    assert options[-1] == {"id": "25", "title": "完成任务25"}


def test_advice_selection_refines_existing_today_plan_without_duplicate(tmp_path):
    title = "验收项目文档续做：只推进一个最小段落"
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM(f"1. {title}"),
    )
    growth.add_task("验收项目文档", date=(datetime.now().date() - timedelta(days=1)).isoformat())
    original = growth.add_task("验收项目文档续做", duration_minutes=30)

    advice = service.handle("请建议今天的学习计划", "advice-update")
    selected = service.handle("第一个加入今天计划", "advice-update")

    assert title in advice.message
    assert len(growth.tasks()) == 1
    assert growth.tasks()[0]["uid"] == original["uid"]
    assert growth.tasks()[0]["title"] == title
    assert growth.tasks()[0]["duration_minutes"] == 30
    assert selected.status == "completed"
    assert len(selected.tool_results) == 1
    assert selected.tool_results[0].tool == "update_plan"
    assert selected.tool_results[0].success
    repeated = service.handle("第一个加入今天计划", "advice-update")
    assert not repeated.tool_results
    assert len(growth.tasks()) == 1


def test_rolling_execution_uses_previous_natural_days_and_duration_coverage(tmp_path):
    growth = GrowthManager(tmp_path / "private", now_provider=lambda: NOW)
    outside = growth.add_task("三十一天前", date="2026-08-29")
    growth.complete_by_id(outside["id"], "2026-08-29")
    growth.add_task("三十天边界", date="2026-08-30")
    edge = growth.add_task("七天边界", date="2026-09-22")
    growth.complete_by_id(edge["id"], "2026-09-22")
    growth.add_record("七天边界行动", date="2026-09-22", duration_minutes=25)
    growth.add_record("无时长行动", date="2026-09-28")
    growth.add_task("今天不计入", date="2026-09-29")
    loop = LearningLoopService(growth, now_provider=lambda: NOW)

    seven = loop.rolling_execution("2026-09-29", 7, strict_read=True)
    thirty = loop.rolling_execution("2026-09-29", 30, strict_read=True)

    assert (seven["planned_days"], seven["completed"], seven["total"]) == (1, 1, 1)
    assert (seven["action_count"], seven["actual_duration_count"], seven["actual_minutes"]) == (2, 1, 25)
    assert (thirty["planned_days"], thirty["completed"], thirty["total"]) == (2, 1, 2)
    assert loop.rolling_execution("2026-09-30", 7)["total"] == 1


def test_planning_evidence_uses_bound_date_and_detects_same_count_review_change(tmp_path):
    growth = GrowthManager(tmp_path / "private", now_provider=lambda: NOW)
    yesterday = growth.add_task("昨天的原任务", date="2026-09-28")
    growth.add_record("昨天的行动", date="2026-09-28")
    growth.save_today_review("2026-09-28")
    loop = LearningLoopService(growth, now_provider=lambda: NOW)
    assert loop.planning_evidence("2026-09-29", strict_read=True)["yesterday_review_status"] == "current"
    growth.update_task(yesterday["id"], {"title": "同数量的新标题"}, "2026-09-28")
    facts = loop.planning_evidence("2026-09-29", strict_read=True)
    assert facts["yesterday_review_status"] == "stale"
    assert facts["today"]["date"] == "2026-09-29"
    assert loop.planning_evidence("2026-09-30", strict_read=True)["today"]["date"] == "2026-09-30"


def test_advice_with_no_history_does_not_invent_execution_or_review(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path)
    conversation = service.conversation_service
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=lambda: NOW)
    facts = {}

    messages = conversation.build_llm_messages(
        "请建议今天的学习计划", "empty-history", planning_advice=True,
        planning_evidence_sink=facts,
    )

    assert facts["yesterday"]["total"] == 0
    assert facts["review_status"] == "missing"
    assert facts["last_7_days"]["planned_days"] == 0
    assert facts["last_30_days"]["actual_minutes"] is None
    assert facts["last_7_days"]["total"] == 0
    assert "review_note" not in facts


def test_advice_strict_read_failure_is_not_reported_as_empty(tmp_path, monkeypatch):
    service, growth, _memory, _history = build_service(tmp_path)
    conversation = service.conversation_service
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=lambda: NOW)
    before = {path.name: path.read_bytes() for path in growth.private_dir.glob("*.json")}
    original = growth.tasks

    def fail_strict(date=None, *, strict_read=False, include_cancelled=False):
        if strict_read:
            raise OSError("isolated read failure")
        return original(date, strict_read=strict_read, include_cancelled=include_cancelled)

    monkeypatch.setattr(growth, "tasks", fail_strict)
    facts = {}
    messages = conversation.build_llm_messages(
        "请建议今天的学习计划", "read-failure", planning_advice=True,
        planning_evidence_sink=facts,
    )

    assert facts["read_error"] == "OSError"
    assert "last_7_days" not in facts
    assert "读取失败" in str(messages)
    assert {path.name: path.read_bytes() for path in growth.private_dir.glob("*.json")} == before


def test_advice_evidence_save_failure_is_disclosed(tmp_path, monkeypatch):
    service, growth, _memory, history = build_service(
        tmp_path, SemanticChatLLM("1. 复习算法"),
    )
    original_add = history.add_message

    def fail_assistant(session_id, role, content, *args, **kwargs):
        if role == "assistant":
            return None
        return original_add(session_id, role, content, *args, **kwargs)

    monkeypatch.setattr(history, "add_message", fail_assistant)
    response = service.handle("请建议今天的学习计划", "save-failure")
    assert "调整依据未能保存" in response.message
    assert growth.tasks() == []


def test_old_advice_cannot_be_adopted_after_business_date_changes(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM("1. 阅读算法资料"),
    )
    clock = {"now": datetime(2026, 10, 30, 9, 0)}
    now = lambda: clock["now"]
    growth.now_provider = now
    conversation = service.conversation_service
    conversation.business_now_provider = now
    conversation.interaction_coordinator.now_provider = now
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=now)

    advice = service.handle("请建议今天的学习计划", "cross-date-advice")
    assert "1. 阅读算法资料" in advice.message
    clock["now"] = datetime(2026, 10, 31, 9, 0)
    selected = service.handle("第一个加入今天计划", "cross-date-advice")
    assert not selected.tool_results
    assert growth.tasks("2026-10-30") == []
    assert growth.tasks("2026-10-31") == []


def test_delayed_advice_reply_keeps_submission_date_but_is_not_adoptable(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM("1. 阅读算法资料"),
    )
    clock = {"now": datetime(2026, 10, 30, 23, 59)}
    now = lambda: clock["now"]
    growth.now_provider = now
    conversation = service.conversation_service
    conversation.business_now_provider = now
    conversation.interaction_coordinator.now_provider = now
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=now)

    prepared = conversation.prepare(
        "请建议今天的学习计划", "delayed-advice", record_history=True,
        business_date="2026-10-30",
    )
    assert prepared.planning_evidence["business_date"] == "2026-10-30"
    clock["now"] = datetime(2026, 10, 31, 0, 1)
    reply = conversation.complete(prepared)

    assert "日期已变化" in reply.message
    assert conversation._recent_suggestion_snapshot("delayed-advice") is None
    assert growth.tasks("2026-10-31") == []


def test_advice_prompt_and_persisted_evidence_match_selected_plan(tmp_path):
    class CapturingAdviceLLM(SemanticChatLLM):
        def __init__(self):
            super().__init__("1. 继续昨天的算法练习，30分钟。先完成一道题\n2. 整理错题，20分钟")
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            return super().chat(messages)

    llm = CapturingAdviceLLM()
    service, growth, memory, history = build_service(tmp_path, llm)
    conversation = service.conversation_service
    growth.now_provider = lambda: NOW
    conversation.business_now_provider = lambda: NOW
    conversation.interaction_coordinator.now_provider = lambda: NOW
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=lambda: NOW)
    memory.add_memory("长期目标是掌握算法题", category="goal")
    old = growth.add_task("昨天的算法练习", date="2026-09-28")
    growth.add_record("练习算法", date="2026-09-28", duration_minutes=15)
    growth.add_record("读了算法讲义", date="2026-09-28")
    growth.save_today_review("2026-09-28")
    existing = growth.add_task("整理错题", date="2026-09-29")
    before = [dict(item) for item in growth.tasks("2026-09-29")]

    advice = service.handle("请建议今天两项算法学习计划，总共50分钟", "adjustment-evidence")
    assert growth.tasks("2026-09-29") == before
    assert "1. 继续昨天的算法练习" in advice.message
    assert any("last_30_days" in str(call) and "长期目标是掌握算法题" in str(call)
               for call in llm.calls)
    advice_message = history.messages("adjustment-evidence")[-1]
    record = advice_message["metadata"]["planning_advice"]
    assert record["business_date"] == "2026-09-29"
    assert record["facts"]["yesterday"]["pending_plans"][0]["uid"] == old["uid"]
    assert record["facts"]["last_7_days"]["actual_duration_count"] == 1
    assert record["displayed_candidates"][0]["title"].startswith("继续昨天的算法练习")

    assert conversation._recent_suggestion_snapshot("adjustment-evidence") is not None
    prepared = conversation.prepare(
        "第一个加入今天计划", "adjustment-evidence", record_history=True,
        business_date="2026-09-29",
    )
    assert prepared.intent_result.get("suggestion_snapshot_selection")
    first = conversation.complete(prepared)
    assert first.status == "completed"
    assert getattr(first, "_suggestion_adoptions", None)
    added = next(item for item in growth.tasks("2026-09-29") if item["uid"] != existing["uid"])
    assert added["duration_minutes"] == 30
    assert added["note"] == "先完成一道题"
    adoption = history.messages("adjustment-evidence")[-1]["metadata"]["suggestion_adoption"]
    assert adoption["items"][0]["plan_uid"] == added["uid"]
    assert adoption["items"][0]["source_plan_uid"] == old["uid"]
    assert growth.tasks("2026-09-28")[0]["uid"] == old["uid"]

    second = service.handle("第二个加入今天计划", "adjustment-evidence")
    assert second.status == "completed"
    assert "更新" in second.message and "加入" not in second.message
    assert len(growth.tasks("2026-09-29")) == 2
    assert second.tool_results[0].data["changes"] == {"title": "整理错题", "duration_minutes": 20}
    assert second.tool_results[0].data["task"]["uid"] == existing["uid"]
    assert second.tool_results[0].data["task"]["duration_minutes"] == 20
    assert next(item for item in growth.tasks("2026-09-29") if item["uid"] == existing["uid"])["duration_minutes"] == 20
    repeat = service.handle("第二个加入今天计划", "adjustment-evidence")
    assert not repeat.tool_results
    assert len(growth.tasks("2026-09-29")) == 2


def test_explicit_advice_with_do_not_add_plan_still_receives_history_facts(tmp_path):
    class CapturingAdviceLLM(SemanticChatLLM):
        def __init__(self):
            super().__init__("1. 继续算法练习，25分钟\n2. 整理错题，20分钟")
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            return super().chat(messages)

    llm = CapturingAdviceLLM()
    service, growth, memory, history = build_service(tmp_path, llm)
    conversation = service.conversation_service
    growth.now_provider = lambda: NOW
    conversation.business_now_provider = lambda: NOW
    conversation.interaction_coordinator.now_provider = lambda: NOW
    conversation.learning_loop_service = LearningLoopService(growth, now_provider=lambda: NOW)
    memory.add_memory("长期目标是稳步掌握算法基础和错题复盘", category="goal")
    growth.add_task("算法练习", date="2026-09-28")
    growth.add_record("做了两道算法题", date="2026-09-28", duration_minutes=25)

    response = service.handle(
        "请根据我的长期目标和昨天执行情况，建议今天两项学习计划：继续算法练习并整理错题，"
        "今天总共最多50分钟。先给建议，不要直接加入计划。",
        "advice-without-write",
    )

    assert not response.tool_results
    assert growth.tasks("2026-09-29") == []
    sent = "\n".join(str(message.get("content", "")) for call in llm.calls for message in call)
    assert "last_7_days" in sent and "last_30_days" in sent
    assert "长期目标是稳步掌握算法基础和错题复盘" in sent
    saved = history.messages("advice-without-write")[-1]["metadata"]["planning_advice"]
    assert len(saved["displayed_candidates"]) == 2
    assert saved["facts"]["last_7_days"]["actual_duration_count"] == 1

    adopted = service.handle("就按这两条安排吧", "advice-without-write")
    assert adopted.status == "completed"
    assert len(growth.tasks("2026-09-29")) == 2
    assert {item["duration_minutes"] for item in growth.tasks("2026-09-29")} == {20, 25}
    adoption = history.messages("advice-without-write")[-1]["metadata"]["suggestion_adoption"]
    assert len(adoption["items"]) == 2
    repeated = service.handle("就按这两条安排吧", "advice-without-write")
    assert not repeated.tool_results
    assert len(growth.tasks("2026-09-29")) == 2


def test_advice_selection_requires_explicit_words_to_keep_two_plans(tmp_path):
    title = "验收项目文档续做：只推进一个最小段落"
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM(f"1. {title}"),
    )
    original = growth.add_task("验收项目文档续做")

    service.handle("请建议今天的学习计划", "advice-separate")
    selected = service.handle("第一个另建一项", "advice-separate")

    tasks = growth.tasks()
    assert len(tasks) == 2
    assert tasks[0]["uid"] == original["uid"]
    assert tasks[0]["title"] == original["title"]
    assert tasks[1]["title"] == title
    assert selected.tool_results[0].tool == "add_plan"
    assert selected.tool_results[0].success


def test_unanchored_existing_plan_advice_asks_instead_of_guessing(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path, SemanticChatLLM("1. 继续验收项目文档续做的小段落"),
    )
    original = growth.add_task("验收项目文档续做")

    service.handle("请建议今天的学习计划", "advice-ambiguous")
    selected = service.handle("第一个加入今天计划", "advice-ambiguous")

    assert not selected.tool_results
    assert "未写入" in selected.message
    assert [item["uid"] for item in growth.tasks()] == [original["uid"]]


def test_batch_advice_does_not_add_linked_old_task_as_new(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path,
        SemanticChatLLM("1. 验收项目文档续做：只推进一个最小段落\n2. 复习算法基础"),
    )
    original = growth.add_task("验收项目文档续做")

    service.handle("请建议今天的学习计划", "advice-batch")
    selected = service.handle("全部加入今天计划", "advice-batch")

    assert not selected.tool_results
    assert "不能把它们全部当作新计划添加" in selected.message
    assert "第1项" in selected.message
    assert "第一个另建一项" not in selected.message
    assert [item["uid"] for item in growth.tasks()] == [original["uid"]]


def test_old_overdue_topic_does_not_block_new_today_advice_batch(tmp_path):
    service, growth, _memory, _history = build_service(
        tmp_path,
        SemanticChatLLM(
            "1. 上午学习机器学习并做笔记\n"
            "2. 下午学习 agent 课程内容并做练习"
        ),
    )
    overdue = growth.add_task(
        "机器学习", date=(datetime.now().date() - timedelta(days=1)).isoformat(),
    )

    advice = service.handle("请建议今天的学习计划", "old-topic-new-day")
    added = service.handle("全部加入", "old-topic-new-day")

    assert "上午学习机器学习并做笔记" in advice.message
    assert [result.tool for result in added.tool_results] == ["add_plan", "add_plan"]
    assert all(result.success for result in added.tool_results)
    assert [task["title"] for task in growth.tasks()] == [
        "上午学习机器学习并做笔记",
        "下午学习 agent 课程内容并做练习",
    ]
    assert growth.tasks((datetime.now().date() - timedelta(days=1)).isoformat())[0][
        "uid"
    ] == overdue["uid"]
