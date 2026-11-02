"""Replay the captured public-expression suggestion adoption miswrite.

Source (read-only): cc-test test_workspace/runs/
p0-online-20261026-correction-run1/p0-correction-ahead/turns/
p0-correction-ahead-t01/result.json and .../t02/.

The first turn displayed two prose suggestions.  The first one mentions a
two-minute recording as material but ends with an explicit 15-minute task
total.  The old snapshot parser returned only the title, then the business
resolver promoted the material length (2) to the task duration and persisted
it.  These tests replay the captured semantic decision and reply through the
real service and transport-free tool handler.  They never call a model, the
desktop, private data or another worktree.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pytest

from modules.intent_router import LLMIntentParser
from modules.suggestion_snapshot import SuggestionSnapshot, SuggestionSnapshotObject
from v18_test_support import build_service


CAPTURED_REQUEST = (
    "今天想练习公开表达。请给我两条今天可做的具体建议，"
    "用1、2编号，只展示，先别加入计划。"
)
BODY1 = (
    "选一段两分钟左右的熟悉内容，用手机录音讲一遍，"
    "回听时只记下“哪里卡住、哪里说得太快”，15分钟。"
)
BODY2 = (
    "对着镜子或镜头练一次开场白，只练前三句，"
    "要求抬头、停顿、把最后一句说完整，10分钟。"
)
CAPTURED_REPLY = (
    "今天计划记录里还没有安排，正好可以从轻量练习开始，只展示，不加入计划。\n\n"
    "1. " + BODY1 + "\n"
    "2. " + BODY2
)
CAPTURED_REPLY_SHA256 = (
    "9c74ba2df15e49e08297f7d8756516d8ff64cb37fd725be05efb3371b80847d5"
)
# Verbatim semantic response from t01's raw_visible_responses[0].
CAPTURED_SEMANTIC = {
    "mode": "chat",
    "intent": "chat",
    "entities": {},
    "proposed_tool": None,
    "confidence": 0.95,
    "follow_up_target": None,
    "needs_confirmation": False,
    "warnings": [],
    "clarification_question": None,
    "candidate_actions": [],
    "subject": "self",
    "polarity": "positive",
    "modality": "desire",
    "request_mode": "advice",
    "explicit_command": False,
}
AMBIGUOUS_REPLY = (
    "先给两条今天可做的建议，只展示。\n\n"
    "1. 先用两分钟录音，再用十分钟回听，中间不要看稿。\n"
    "2. 选一段两分钟左右的熟悉内容，用手机录音讲一遍，"
    "回听时只记下“哪里卡住、哪里说得太快”。"
)


class ReplayLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(CAPTURED_SEMANTIC, ensure_ascii=False)
        return self.reply


def runtime(tmp_path, reply=CAPTURED_REPLY):
    llm = ReplayLLM(reply)
    agent, growth, memory, history = build_service(tmp_path, llm)
    agent.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    conversation = agent.conversation_service
    conversation.semantic_decision_compatibility_enabled = False
    # Captured t01/t02 used business date 2026-11-05 while message and
    # snapshot timestamps stayed on the real date 2026-10-26.
    business_now = lambda: datetime(2026, 11, 5, 9, 0)  # noqa: E731
    real_now = lambda: datetime(2026, 10, 26, 0, 12, 55)  # noqa: E731
    growth.now_provider = business_now
    growth.timestamp_provider = real_now
    conversation.now_provider = real_now
    conversation.business_now_provider = business_now
    conversation.interaction_coordinator.now_provider = real_now
    session = history.ensure_session(restore_latest=False)["session_id"]
    return agent, conversation, growth, memory, history, llm, session


def snapshot_objects(conversation, session):
    raw = conversation.interaction_coordinator.current(
        session
    ).last_suggestion_snapshot
    assert raw, "the advice turn must keep a suggestion snapshot"
    return raw["objects"]


def install_handler_recorder(agent):
    definition = agent.tool_executor.registry.get("add_plan")
    assert definition is not None
    original = definition.handler
    calls = []

    def recording_handler(**arguments):
        calls.append(dict(arguments))
        return original(**arguments)

    definition.handler = recording_handler
    return definition, original, calls


def test_captured_reply_is_the_embedded_evidence():
    assert hashlib.sha256(CAPTURED_REPLY.encode()).hexdigest() == CAPTURED_REPLY_SHA256
    assert SuggestionSnapshot.numbered_options(CAPTURED_REPLY) == [
        {"id": "1", "title": BODY1},
        {"id": "2", "title": BODY2},
    ]


def test_captured_bodies_keep_the_explicit_trailing_total():
    first = SuggestionSnapshotObject("1", 1, BODY1)
    second = SuggestionSnapshotObject("2", 2, BODY2)

    assert first.plan_arguments() == {"title": BODY1, "duration_minutes": 15}
    assert second.plan_arguments() == {"title": BODY2, "duration_minutes": 10}
    # The complete displayed body remains the title; only the ambiguous
    # sub-activity/material lengths are excluded from the total.
    assert first.plan_arguments()["title"] == BODY1
    assert second.plan_arguments()["title"] == BODY2


def test_existing_short_duration_form_still_splits_title_and_note():
    body = "复习 SQL，20分钟。挑一个主题，比如 JOIN，先看语法再写两条查询。"
    assert SuggestionSnapshotObject("1", 1, body).plan_arguments() == {
        "title": "复习 SQL",
        "duration_minutes": 20,
        "note": "挑一个主题，比如 JOIN，先看语法再写两条查询。",
    }


@pytest.mark.parametrize("body", [
    "先用两分钟录音，再用十分钟回听，中间不要看稿。",
    "选一段两分钟左右的熟悉内容，用手机录音讲一遍，"
    "回听时只记下“哪里卡住、哪里说得太快”。",
    "先花2分钟热身，再用10分钟练习，最后用5分钟复盘。",
])
def test_displayed_body_without_explicit_total_does_not_invent_duration(body):
    parsed = SuggestionSnapshotObject("1", 1, body).plan_arguments()
    assert parsed == {"title": body}
    assert "duration_minutes" not in parsed


def test_captured_offer_then_adopt_all_saves_total_not_material_length(tmp_path):
    agent, conversation, growth, memory, history, llm, session = runtime(tmp_path)
    offered = agent.handle(CAPTURED_REQUEST, session)
    assert offered.tool_results == []
    assert growth.tasks() == []
    assert offered.message == CAPTURED_REPLY
    assert [item["title"] for item in snapshot_objects(conversation, session)] == [
        BODY1,
        BODY2,
    ]

    definition, original, handler_calls = install_handler_recorder(agent)
    calls_before_adoption = len(llm.calls)
    try:
        adopted = agent.handle("全部加入今天计划", session)
    finally:
        definition.handler = original

    assert [(item.tool, item.success) for item in adopted.tool_results] == [
        ("add_plan", True),
        ("add_plan", True),
    ]
    assert all(
        item.data["postcondition_verified"] for item in adopted.tool_results
    )
    # The handler is the last boundary before persistence: these are the
    # exact arguments the captured run recorded duration=2 on.
    assert [call["duration_minutes"] for call in handler_calls] == [15, 10]
    assert [call["title"] for call in handler_calls] == [BODY1, BODY2]
    assert [call["date"] for call in handler_calls] == [
        "2026-11-05",
        "2026-11-05",
    ]
    assert len(llm.calls) == calls_before_adoption, (
        "suggestion adoption must not add a provider call"
    )

    tasks = growth.tasks("2026-11-05")
    assert [(task["title"], task["duration_minutes"], task["date"]) for task in tasks] == [
        (BODY1, 15, "2026-11-05"),
        (BODY2, 10, "2026-11-05"),
    ]
    uids = [task["uid"] for task in tasks]
    assert all(uids) and len(set(uids)) == 2
    assert growth.tasks("2026-10-26") == []
    persisted = json.loads(growth.plan_file.read_text(encoding="utf-8"))
    persisted_tasks = persisted["days"]["2026-11-05"]["tasks"]
    assert [(item["uid"], item["duration_minutes"]) for item in persisted_tasks] == [
        (uids[0], 15),
        (uids[1], 10),
    ]
    assert [item["consumed"] for item in snapshot_objects(conversation, session)] == [
        True,
        True,
    ]
    assert memory.memories() == []
    assert growth.records_for_date() == []

    messages = history.messages(session)
    assert messages[-2]["role"] == "user"
    assert messages[-2]["content"] == "全部加入今天计划"
    assistant = messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == adopted.message
    adoption = assistant["metadata"]["suggestion_adoption"]
    assert adoption["business_date"] == "2026-11-05"
    assert [item["plan_uid"] for item in adoption["items"]] == uids


def test_captured_single_adoption_uses_the_explicit_total(tmp_path):
    agent, conversation, growth, _memory, history, llm, session = runtime(tmp_path)
    agent.handle(CAPTURED_REQUEST, session)

    definition, original, handler_calls = install_handler_recorder(agent)
    calls_before_adoption = len(llm.calls)
    try:
        adopted = agent.handle("把第一条加入今天计划。", session)
    finally:
        definition.handler = original

    assert [(item.tool, item.success) for item in adopted.tool_results] == [
        ("add_plan", True)
    ]
    assert len(handler_calls) == 1
    assert handler_calls[0]["duration_minutes"] == 15
    assert handler_calls[0]["title"] == BODY1
    assert handler_calls[0]["date"] == "2026-11-05"
    assert len(llm.calls) == calls_before_adoption

    tasks = growth.tasks("2026-11-05")
    assert [(task["title"], task["duration_minutes"]) for task in tasks] == [
        (BODY1, 15)
    ]
    assert tasks[0]["uid"]
    assert growth.tasks("2026-10-26") == []
    assert [item["consumed"] for item in snapshot_objects(conversation, session)] == [
        True,
        False,
    ]
    assistant = history.messages(session)[-1]
    assert assistant["content"] == adopted.message
    adoption = assistant["metadata"]["suggestion_adoption"]
    assert adoption["business_date"] == "2026-11-05"
    assert [item["plan_uid"] for item in adoption["items"]] == [tasks[0]["uid"]]


def test_current_sentence_duration_overrides_the_displayed_total(tmp_path):
    agent, _conversation, growth, _memory, _history, llm, session = runtime(tmp_path)
    agent.handle(CAPTURED_REQUEST, session)

    definition, original, handler_calls = install_handler_recorder(agent)
    calls_before_adoption = len(llm.calls)
    try:
        adopted = agent.handle("全部加入今天计划，每项用20分钟。", session)
    finally:
        definition.handler = original

    assert all(item.success for item in adopted.tool_results)
    assert len(handler_calls) == 2
    assert [call["duration_minutes"] for call in handler_calls] == [20, 20]
    assert len(llm.calls) == calls_before_adoption
    assert [task["duration_minutes"] for task in growth.tasks("2026-11-05")] == [
        20,
        20,
    ]


def test_ambiguous_displayed_durations_are_not_promoted_to_totals(tmp_path):
    agent, _conversation, growth, _memory, _history, _llm, session = runtime(
        tmp_path, AMBIGUOUS_REPLY
    )
    agent.handle("给我两条今天可做的建议，用1、2编号，只展示，先别加入计划。", session)

    adopted = agent.handle("全部加入今天计划", session)

    assert [(item.tool, item.success) for item in adopted.tool_results] == [
        ("add_plan", True),
        ("add_plan", True),
    ]
    tasks = growth.tasks("2026-11-05")
    assert [task["title"] for task in tasks] == [
        "先用两分钟录音，再用十分钟回听，中间不要看稿。",
        "选一段两分钟左右的熟悉内容，用手机录音讲一遍，"
        "回听时只记下“哪里卡住、哪里说得太快”。",
    ]
    assert [task["duration_minutes"] for task in tasks] == [None, None]
