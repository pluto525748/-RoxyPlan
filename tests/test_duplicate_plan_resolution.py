import json

from modules.intent_router import LLMIntentParser
from v18_test_support import NoCallLLM, build_service


class DuplicateInspectionLLM:
    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            return json.dumps(
                {
                    "mode": "read",
                    "intent": "inspect_plan_duplicates",
                    "entities": {},
                    "proposed_tool": "inspect_plan_duplicates",
                    "confidence": 0.99,
                    "follow_up_target": None,
                    "needs_confirmation": False,
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "question",
                    "request_mode": "query",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return ""


def test_duplicate_update_keeps_stable_target_until_changes_arrive(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    original = growth.add_task("早睡", time_slot="晚上")
    scope = "duplicate-update"

    duplicate = service.handle("添加计划：早睡", scope)
    choose_update = service.handle("更新原计划", scope)

    assert duplicate.status == "clarification"
    assert service.conversation_service.interaction_coordinator.current(
        scope
    ).interaction_kind == "plan_update"
    assert "早睡" in choose_update.message
    assert len(growth.tasks()) == 1

    updated = service.handle("把时长改成30分钟", scope)

    assert updated.status == "completed"
    assert len(growth.tasks()) == 1
    assert growth.tasks()[0]["uid"] == original["uid"]
    assert growth.tasks()[0]["duration_minutes"] == 30


def test_duplicate_keep_adds_original_proposal_without_stale_update_fields(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("早睡", time_slot="晚上")
    scope = "duplicate-keep"

    service.handle("添加计划：早睡", scope)
    kept = service.handle("仍然添加：早睡", scope)

    assert kept.status == "completed"
    assert len(growth.tasks()) == 2
    assert all(item["title"] == "早睡" for item in growth.tasks())


def test_duplicate_add_can_merge_into_existing_after_confirmation(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    original = growth.add_task("阅读", time_slot="晚上")
    scope = "duplicate-proposed-merge"

    service.handle("添加计划：阅读", scope)
    preview = service.handle("合并计划", scope)

    assert preview.status == "confirmation_required"
    assert len(growth.tasks()) == 1

    merged = service.handle("确认", scope)

    assert merged.status == "completed"
    assert [item["uid"] for item in growth.tasks()] == [original["uid"]]


def test_inspection_lists_groups_and_confirmed_merge_is_conservative(tmp_path):
    llm = DuplicateInspectionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    broad = growth.add_task("写小说")
    specific = growth.add_task(
        "写小说至少五百字", time_slot="晚上", duration_minutes=30
    )
    growth.complete_by_id(int(broad["id"]))
    scope = "duplicate-inspection-merge"

    inspected = service.handle("看看今天的安排里有没有相近的内容", scope)

    assert inspected.status == "completed"
    assert "写小说" in inspected.message
    assert "合并计划" in inspected.message
    assert len(growth.tasks()) == 2

    preview = service.handle("把这组计划合并", scope)
    assert preview.status == "confirmation_required"
    assert "合并预览" in preview.message
    assert "写小说至少五百字" in preview.message
    assert "待完成" in preview.message
    assert len(growth.tasks()) == 2

    merged = service.handle("确认", scope)
    tasks = growth.tasks()
    assert merged.status == "completed"
    assert len(tasks) == 1
    assert tasks[0]["uid"] == specific["uid"]
    assert tasks[0]["title"] == "写小说至少五百字"
    assert tasks[0]["done"] is False
    assert tasks[0]["status"] == "pending"
    assert "写小说至少五百字" in merged.message
    assert "待完成" in merged.message


def test_merge_keeps_completed_status_only_when_every_source_is_completed(tmp_path):
    llm = DuplicateInspectionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    first = growth.add_task("整理实验记录")
    second = growth.add_task("整理实验记录三十分钟", duration_minutes=30)
    growth.complete_by_id(int(first["id"]))
    growth.complete_by_id(int(second["id"]))
    scope = "completed-duplicate-merge"

    service.handle("检查今天的计划有没有相近项", scope)
    preview = service.handle("合并这些计划", scope)
    merged = service.handle("确认", scope)

    tasks = growth.tasks()
    assert preview.status == "confirmation_required"
    assert merged.status == "completed"
    assert len(tasks) == 1
    assert tasks[0]["done"] is True
    assert tasks[0]["status"] == "completed"
    assert "已完成" in merged.message


def test_duplicate_inspection_reports_no_group_without_creating_pending(tmp_path):
    llm = DuplicateInspectionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    growth.add_task("整理实验记录")

    inspected = service.handle("今日计划有没有重复内容", "no-duplicate-group")

    assert inspected.status == "completed"
    assert "没有发现" in inspected.message
    assert not service.conversation_service.interaction_coordinator.current(
        "no-duplicate-group"
    ).pending


def test_merge_conflict_asks_for_value_before_confirmation(tmp_path):
    llm = DuplicateInspectionLLM()
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    growth.add_task("英语练习", time_slot="上午")
    growth.add_task("英语练习30分钟", time_slot="晚上", duration_minutes=30)
    scope = "duplicate-merge-conflict"

    service.handle("检查今天的计划有没有相近项", scope)
    conflict = service.handle("合并这些计划", scope)

    assert conflict.status == "clarification"
    assert "时段冲突" in conflict.message
    assert len(growth.tasks()) == 2

    preview = service.handle("保留晚上", scope)
    assert preview.status == "confirmation_required"
    assert "晚上" in preview.message
    assert len(growth.tasks()) == 2

    service.handle("确认", scope)
    assert len(growth.tasks()) == 1
    assert growth.tasks()[0]["time_slot"] == "晚上"


def test_today_scoped_remember_command_asks_for_destination(tmp_path):
    service, growth, memory, _history = build_service(tmp_path, NoCallLLM())
    scope = "today-memory-destination"

    choice = service.handle("记住我今天晚上要早睡", scope)

    assert choice.status == "clarification"
    assert "加入今日计划" in choice.message
    assert "长期记忆" in choice.message
    assert growth.tasks() == []
    assert memory.memories() == []

    added = service.handle("加入今日计划", scope)

    assert added.status == "completed"
    assert [item["title"] for item in growth.tasks()] == ["早睡"]
    assert memory.memories() == []


def test_today_scoped_remember_can_be_explicitly_saved_long_term(tmp_path):
    service, growth, memory, _history = build_service(tmp_path, NoCallLLM())
    scope = "today-memory-long-term"

    service.handle("记住我今天晚上要早睡", scope)
    saved = service.handle("保存为长期记忆", scope)

    assert saved.status == "completed"
    assert growth.tasks() == []
    assert [item["content"] for item in memory.memories()] == ["我今天晚上要早睡"]


def test_stable_habit_remember_command_still_saves_directly(tmp_path):
    service, growth, memory, _history = build_service(tmp_path, NoCallLLM())

    saved = service.handle("记住：我通常晚上十点睡", "stable-memory")

    assert saved.status == "completed"
    assert growth.tasks() == []
    assert [item["content"] for item in memory.memories()] == ["我通常晚上十点睡"]


def test_explicit_today_plan_with_memory_domain_word_is_not_rerouted(tmp_path):
    service, growth, memory, _history = build_service(tmp_path, NoCallLLM())

    added = service.handle("添加计划：验收资料归档，晚上40分钟", "archive-plan")

    assert added.status == "completed"
    assert len(growth.tasks()) == 1
    assert "验收资料归档" in growth.tasks()[0]["title"]
    assert growth.tasks()[0]["time_slot"] == "晚上"
    assert growth.tasks()[0]["duration_minutes"] == 40
    assert memory.memories() == []
