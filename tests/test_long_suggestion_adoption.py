"""Offline replay of the captured G07/G08 engineering model replies.

Source: 20261014-deepseek-expanded/raw_results.jsonl (synthetic data).
Raw file SHA256: a694d73c84b15f6bd560d4964f3a8d99be8b9cddfbd35b9b1754c708ffdb4074.
No API/provider, private stores, GUI or model correctness evaluation here.
"""
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from modules.intent_router import LLMIntentParser
from modules.suggestion_snapshot import SuggestionSnapshot, SuggestionSnapshotObject
from v18_test_support import build_service


G07_REQUEST = "我今晚有一个半小时，想练 Python 列表推导式和读英文技术文档。给我两条具体的今日建议，先别加入计划。"
G07_NOTE1 = "用 20 分钟过一遍基本形式（带条件、嵌套、和普通 for 循环的对照），剩下 30 分钟自己写 5 到 8 道小题，从「把列表里的数字平方」这种开始，逐步加条件过滤和字典转换。写完对照答案，重点看你写的是不是比 for 循环更清楚，而不是更短。"
G07_NOTE2 = "选一篇你正在用的库的官方文档，比如 Python 官方教程或某个包的 README。前 10 分钟通读抓结构，不查词；中间 20 分钟逐段读，把不认识的术语记下来，先靠上下文猜再查；最后 5 分钟用两三句英文写下这篇文档在讲什么。"
G07_BODY1 = "Python 列表推导式练习，50分钟。" + G07_NOTE1
G07_BODY2 = "读英文技术文档，35分钟。" + G07_NOTE2
G07_REPLY = (
    "好，今晚一个半小时，两个方向。今天还没有已记录的计划，也没有近期执行数据可参考，所以我按你给的时间直接拆。\n\n"
    "1. " + G07_BODY1 + "\n\n2. " + G07_BODY2
    + "\n\n中间留 5 分钟休息，别连着坐满。这两条先放在这里，你说加入我再处理。"
)
G08_REQUEST = "给我两项今天能做的建议，一项复习 SQL，一项整理错题，每项二十分钟，暂时不要写入。"
G08_REPLY = (
    "好，只给建议，不写入。\n\n"
    "1. 复习 SQL，20分钟。挑一个主题，比如 JOIN，先看一遍语法，再自己写两条查询验证。\n"
    "2. 整理错题，20分钟。只处理最近一批里最没头绪的两三道，写清错因和正确思路就够了。\n\n"
    "先做哪项都由你。想的话，我可以帮你把其中一项再拆细一点。"
)


class ReplayLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        prompt = messages[0]["content"] if messages else ""
        if "You classify one Chinese user message" in prompt:
            text = prompt.rsplit("User message: ", 1)[-1]
            adopting = "加入" in text and "建议" not in text
            # G07-1 captured advice decision and G07-2 captured correct title
            # proposal. Its guessed entity must not bypass missing snapshots.
            return json.dumps({
                "mode": "write" if adopting else "chat", "intent": "add_plan" if adopting else "chat",
                "entities": {"title": "读英文技术文档", "time_slot": "", "duration_minutes": 35} if adopting else {},
                "proposed_tool": "add_plan" if adopting else None,
                "confidence": 0.9 if adopting else 0.93, "follow_up_target": "",
                "needs_confirmation": False, "warnings": [], "clarification_question": "", "candidate_actions": [],
                "subject": "self", "polarity": "positive", "modality": "commitment" if adopting else "desire",
                "request_mode": "execute" if adopting else "advice", "explicit_command": adopting,
            }, ensure_ascii=False)
        return self.reply


def runtime(tmp_path, reply):
    llm = ReplayLLM(reply)
    agent, growth, memory, history = build_service(tmp_path, llm)
    agent.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service = agent.conversation_service
    service.semantic_decision_compatibility_enabled = False
    clock = [datetime(2026, 10, 14, 21, 34)]
    service.now_provider = service.business_now_provider = lambda: clock[0]
    service.interaction_coordinator.now_provider = lambda: clock[0]
    growth.now_provider = lambda: clock[0]
    session = history.ensure_session(restore_latest=False)["session_id"]
    return service, growth, memory, llm, session, clock


def test_g07_full_raw_reply_keeps_both_real_numbers_and_bodies():
    assert hashlib.sha256(G07_REPLY.encode()).hexdigest() == "25e33cd2b91dd33cf9a686921cf7232608b80a7bcdf2b667bfeb7b419c6dbbe4"
    assert [len(G07_BODY1), len(G07_BODY2)] == [143, 128]
    options = SuggestionSnapshot.numbered_options(G07_REPLY)
    assert options == [{"id": "1", "title": G07_BODY1}, {"id": "2", "title": G07_BODY2}]
    snapshot = SuggestionSnapshot.for_options(conversation_id="G07-offline", options=options)
    restored = SuggestionSnapshot.from_dict(snapshot.to_dict())
    second = restored.select_position(2)
    assert second.stable_id == "2" and second.title == G07_BODY2
    assert second.plan_arguments() == {"title": "读英文技术文档", "duration_minutes": 35, "note": G07_NOTE2}


def test_g07_service_adopts_only_second_item_with_evening_and_full_note(tmp_path):
    service, growth, memory, _llm, session, _clock = runtime(tmp_path, G07_REPLY)
    offered = service.handle(G07_REQUEST, session)
    before = service.interaction_coordinator.current(session).to_dict()
    assert offered.tool_results == [] and growth.tasks() == []
    selected = service.handle("把第二条加入今天计划。", session)
    persisted = json.loads(growth.plan_file.read_text(encoding="utf-8"))
    (tmp_path / "G07-replay-chain.json").write_text(json.dumps({
        "source_reply": G07_REPLY, "before_selection": before,
        "after_selection": service.interaction_coordinator.current(session).to_dict(),
        "results": [item.to_dict() for item in selected.tool_results],
        "persisted": persisted, "reply": selected.message,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert [item.tool for item in selected.tool_results] == ["add_plan"]
    assert selected.tool_results[0].success and selected.tool_results[0].data["postcondition_verified"]
    assert len(growth.tasks()) == 1
    task = growth.tasks()[0]
    assert (task["title"], task["date"], task["duration_minutes"], task["time_slot"], task["note"]) == (
        "读英文技术文档", "2026-10-14", 35, "晚上", G07_NOTE2)
    assert task in persisted["days"]["2026-10-14"]["tasks"]
    assert memory.memories() == [] and growth.records_for_date() == []
    assert [item["consumed"] for item in service.interaction_coordinator.current(session).last_suggestion_snapshot["objects"]] == [False, True]


def test_g08_captured_short_advice_preserves_two_successive_twenty_minute_choices(tmp_path):
    assert hashlib.sha256(G08_REPLY.encode()).hexdigest() == "a0c0dbb37342097314b6f0598d79fa5cba9dc1720e1045f26448f87bcb9239f2"
    service, growth, _memory, _llm, session, _clock = runtime(tmp_path, G08_REPLY)
    service.handle(G08_REQUEST, session)
    first = service.handle("第一项加入今天计划。", session)
    second = service.handle("第二项也加入。", session)
    assert all([result.tool for result in response.tool_results] == ["add_plan"] for response in (first, second))
    assert all(response.tool_results[0].success for response in (first, second))
    assert [task["title"] for task in growth.tasks()] == ["复习 SQL", "整理错题"]
    assert [task["duration_minutes"] for task in growth.tasks()] == [20, 20]
    assert [task["note"] for task in growth.tasks()] == [
        "挑一个主题，比如 JOIN，先看一遍语法，再自己写两条查询验证。",
        "只处理最近一批里最没头绪的两三道，写清错因和正确思路就够了。"]


@pytest.mark.parametrize("source", [
    "1. " + G07_BODY1 + "\n  2. 子列表",
    "1. " + G07_BODY1 + "\n1. 重复编号",
    "1. " + G07_BODY1 + "\n3. 不连续",
    "1. " + G07_BODY1 + "\n中间正文\n2. " + G07_BODY2,
    "1. " + G07_BODY1 + "\n\n另一段\n1. " + G07_BODY2,
    "```python\n1. " + G07_BODY1 + "\n```",
    "1. " + G07_BODY1 + " 2. 同行第二项",
    "1. " + G07_BODY1 + "？",
    "1. [待完成] " + G07_BODY1,
])
def test_long_text_does_not_weaken_structure_validation(source):
    assert SuggestionSnapshot.numbered_options(source) == []


def test_note_can_be_long_without_truncation_but_collection_is_bounded():
    note = "保留完整备注，不裁剪。" * 200
    body = "复习变量，35分钟。" + note
    assert SuggestionSnapshot.numbered_options("1. " + body) == [{"id": "1", "title": body}]
    assert SuggestionSnapshotObject("1", 1, body).plan_arguments()["note"] == note
    assert SuggestionSnapshot.numbered_options("1. 小任务\n2. " + "文" * 16385) == []
    assert SuggestionSnapshot.numbered_options("\n".join(f"{index}. 小任务" for index in range(1, 66))) == []


@pytest.mark.parametrize("kind", ["direction", "stage", "teaching"])
def test_long_non_task_lists_remain_read_only(tmp_path, kind):
    framing = {"direction": "先选择目标方向：", "stage": "长期分阶段方案：", "teaching": "下面解释两个知识点："}[kind]
    body1 = ("第1周：" if kind == "stage" else "") + G07_BODY1
    body2 = ("第2周：" if kind == "stage" else "") + G07_BODY2
    reply = framing + "\n1. " + body1 + "\n2. " + body2
    service, growth, memory, _llm, session, _clock = runtime(tmp_path, reply)
    request = "带我学 Python" if kind == "teaching" else "给我一个30天的Python学习计划"
    offered = service.handle(request, session)
    selected = service.handle("第二项", session)
    assert offered.tool_results == [] and selected.tool_results == []
    assert growth.tasks() == [] and memory.memories() == []
    assert not service.interaction_coordinator.current(session).last_suggestion_snapshot


@pytest.mark.parametrize("control", ["cancel", "expire"])
def test_long_daily_advice_retains_cancel_and_expiry_safety(tmp_path, control):
    service, growth, _memory, _llm, session, clock = runtime(tmp_path, G07_REPLY)
    service.handle(G07_REQUEST, session)
    if control == "expire":
        clock[0] += timedelta(seconds=service.interaction_coordinator.continuation_ttl_seconds + 1)
    response = service.handle("取消" if control == "cancel" else "把第二条加入今天计划。", session)
    assert response.tool_results == [] and growth.tasks() == []
