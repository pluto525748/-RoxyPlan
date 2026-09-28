import json
from datetime import datetime, timedelta

import pytest

from modules.contracts import ToolResult
from modules.intent_router import LLMIntentParser
from modules.semantic_action_parser import SemanticParseResult
from v18_test_support import NoCallLLM, RecordingLLM, build_service


class SemanticThenReplyLLM:
    def __init__(self, decision, reply):
        self.decision = dict(decision)
        self.reply = str(reply)
        self.semantic_calls = 0
        self.reply_calls = []

    def chat(self, messages):
        first = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in first:
            self.semantic_calls += 1
            return json.dumps(self.decision, ensure_ascii=False)
        self.reply_calls.append(messages)
        return self.reply


class AssistantPlanOptionLLM:
    def __init__(self, options, decision=None):
        self.options = list(options)
        self.decision = dict(
            decision
            or {
                "mode": "write",
                "intent": "add_plan",
                "entities": {"title": "你刚才的安排"},
                "proposed_tool": "add_plan",
                "confidence": 0.98,
                "follow_up_target": "previous_assistant_plan",
                "needs_confirmation": False,
                "warnings": [],
                "clarification_question": None,
                "candidate_actions": [],
                "subject": "self",
                "polarity": "positive",
                "modality": "commitment",
                "request_mode": "execute",
                "explicit_command": True,
            }
        )
        self.semantic_calls = 0
        self.calls = []
        self.events = []

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            self.semantic_calls += 1
            self.events.append("semantic_decision")
            return json.dumps(self.decision, ensure_ascii=False)
        self.calls.append(messages)
        self.events.append("option_extraction")
        assert "提取可执行的今日计划候选" in prompt
        return json.dumps({"options": self.options}, ensure_ascii=False)


class AdvicePlanLLM:
    def __init__(self, reply, options):
        self.reply = str(reply)
        self.options = list(options)
        self.calls = []

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        self.calls.append(prompt)
        if "提取可执行的今日计划候选" in prompt:
            return json.dumps({"options": self.options}, ensure_ascii=False)
        return self.reply


def _chat_decision():
    return {
        "mode": "chat",
        "intent": "chat",
        "entities": {},
        "proposed_tool": None,
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "",
        "request_mode": "discuss",
        "explicit_command": False,
    }


def _production_semantic_service(tmp_path, llm):
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def _seed_mixed_plan_state(growth):
    created = [
        growth.add_task("写小说"),
        growth.add_task("写小说至少五百字", time_slot="晚上", duration_minutes=30),
        growth.add_task("机器学习", time_slot="下午"),
        growth.add_task(
            "睡前半小时放下手机，做慢呼吸，比平时早半小时睡",
            time_slot="晚上",
            duration_minutes=30,
        ),
    ]
    growth.complete_by_id(int(created[0]["id"]))
    growth.complete_by_id(int(created[1]["id"]))
    return created


def test_except_one_pending_plan_still_enters_verified_confirmation(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    _seed_mixed_plan_state(growth)
    conversation_id = "v22-single-target-exclusion"

    first = service.handle("我今日计划除了早睡都完成了", conversation_id)

    assert first.status == "clarification"
    assert "机器学习" in first.message
    assert "早半小时睡" not in first.message
    assert [item["title"] for item in growth.tasks() if not item["done"]] == [
        "机器学习",
        "睡前半小时放下手机，做慢呼吸，比平时早半小时睡",
    ]

    confirmed = service.handle("确认", conversation_id)

    assert [item.tool for item in confirmed.tool_results] == ["complete_plan"]
    assert [item["title"] for item in growth.tasks() if not item["done"]] == [
        "睡前半小时放下手机，做慢呼吸，比平时早半小时睡"
    ]


def test_chat_reply_receives_verified_turn_facts_and_cannot_fake_plan_state(tmp_path):
    llm = SemanticThenReplyLLM(
        _chat_decision(),
        "今天很充实啊，把计划一项项做完了，不容易。",
    )
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    _seed_mixed_plan_state(growth)

    response = service.handle(
        "这些今日计划看起来都完成了吧",
        "v22-verified-turn-chat",
    )

    assert response.status == "chat"
    assert response.tool_results == []
    assert len([item for item in growth.tasks() if not item["done"]]) == 2
    prompt = "\n".join(str(item.get("content", "")) for item in llm.reply_calls[-1])
    assert "程序已核验的本轮事实" in prompt
    assert '"performed":false' in prompt
    assert "机器学习" in prompt
    assert "把计划一项项做完了" not in response.message
    assert "还没有更新计划记录" in response.message
    diagnostic = service.diagnostic_snapshot()[-1]["verified_turn_context"]
    assert diagnostic["execution_performed"] is False
    assert diagnostic["user_reported_plan_progress"] is True
    assert diagnostic["pending_plan_count"] == 2
    assert diagnostic["completed_plan_count"] == 2
    assert "pending_titles" not in diagnostic


@pytest.mark.parametrize(
    "text",
    ["今天都完成了什么", "我做到了什么", "今天状态怎么样", "帮我回顾一下今天"],
)
def test_reflective_today_queries_use_comprehensive_review(text):
    from modules.intent_router import IntentRouter

    decision = IntentRouter(enable_llm=False).route_semantic_decision(text)

    assert decision["intent"] == "daily_review"
    assert decision["source"] == "business_read_guard"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("查看计划", "show_plan"),
        ("看看今天的任务", "show_plan"),
        ("查看行动记录", "show_action_log"),
    ],
)
def test_management_reads_keep_deterministic_list_tools(text, intent):
    from modules.intent_router import IntentRouter

    decision = IntentRouter(enable_llm=False).route_semantic_decision(text)

    assert decision["intent"] == intent
    assert decision["source"] == "business_read_guard"


@pytest.mark.parametrize(
    ("query", "offset", "label", "title"),
    [
        ("昨天有什么计划", -1, "昨天的计划", "核对昨日验收记录"),
        ("明天有什么计划", 1, "明天的计划", "准备明日验收材料"),
    ],
)
def test_relative_day_plan_query_reads_and_labels_requested_date(
    tmp_path,
    query,
    offset,
    label,
    title,
):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    target = (datetime.now().date() + timedelta(days=offset)).isoformat()
    growth.add_task(title, date=target)
    growth.add_task("今天的对照任务")

    response = service.handle(query, f"v22-relative-plan-{offset}")

    assert [item.tool for item in response.tool_results] == ["show_plan"]
    assert response.tool_results[0].data["date"] == target
    assert [item["title"] for item in response.tool_results[0].data["tasks"]] == [
        title
    ]
    assert label in response.message
    assert title in response.message
    assert "今天的对照任务" not in response.message


@pytest.mark.parametrize(
    "text",
    [
        "帮我把展览讲解拆成三步，用编号列出来，先只讨论，不要写进今日任务",
        "把发布准备分成三件列清楚，先别纳入今天待办",
        "整理三个做法给我看看，暂时不用放到今日计划",
    ],
)
def test_negated_plan_write_discussion_bypasses_business_read_guard(tmp_path, text):
    reply = "可以，我们先把步骤理清楚，不改动今天的计划。"
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    growth.add_task("既有计划")
    before = growth.tasks()

    response = service.handle(text, f"v22-negated-plan-write-{len(text)}")

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert growth.tasks() == before
    assert llm.semantic_calls == 1
    assert len(llm.reply_calls) == 1


def test_long_negated_plan_command_vetoes_positive_model_proposal(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "不应写入"},
        "proposed_tool": "add_plan",
        "confidence": 1.0,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "模型不应覆盖本地否定。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle("先不要把验收-否定事项C加入今天计划", "v22-negation-veto")

    assert response.tool_results == []
    assert growth.tasks() == []
    assert response.status == "chat"


def test_plan_duplicate_gate_handles_missing_duration_without_blocking_distinct_duration(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())

    first = service.handle("添加计划：今晚学习机器学习40分钟", "v22-plan-duplicate")
    duplicate = service.handle("再加一条今晚学习机器学习", "v22-plan-duplicate")
    distinct = service.handle("再加一条今晚学习机器学习50分钟", "v22-plan-duplicate")

    assert first.status == "completed"
    assert duplicate.status == "clarification"
    assert distinct.status == "completed"
    assert len(growth.tasks()) == 2


def test_comprehensive_review_uses_verified_facts_context_and_llm(tmp_path):
    llm = RecordingLLM("你今天已经把线性回归复习落到了行动上，项目整理也有推进。")
    service, growth, memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-review"
    history.new_session(session_id=session_id)
    history.add_message(session_id, "user", "我最近更偏向先理解原理再写代码")
    history.add_message(session_id, "assistant", "好，我们按这个节奏推进。")
    memory.add_memory("完成机器学习课程是我最近一个月的目标", category="goal")
    completed = growth.add_task("复习线性回归")
    growth.complete_by_id(int(completed["id"]))
    growth.add_task("整理项目笔记")
    growth.add_record("完成梯度下降推导")

    response = service.handle("我做到了什么", session_id)

    assert [item.tool for item in response.tool_results] == ["generate_daily_review"]
    review = response.tool_results[0].data["review"]
    assert review["completed_tasks"] == ["复习线性回归"]
    assert review["pending_tasks"] == ["整理项目笔记"]
    assert review["actions"] == ["完成梯度下降推导"]
    assert response.message == llm.reply
    assert len(llm.calls) == 1

    prompt = json.dumps(llm.calls[0], ensure_ascii=False)
    assert "完成机器学习课程是我最近一个月的目标" in prompt
    assert "我最近更偏向先理解原理再写代码" in prompt
    assert "复习线性回归" in prompt
    assert "整理项目笔记" in prompt
    assert "完成梯度下降推导" in prompt
    assert "今天你计划了 2 件事" not in prompt
    assert "已经推进的部分很扎实" not in prompt


def test_conversation_summary_save_is_degraded_to_user_confirmed_lines(tmp_path):
    reply = (
        "这段对话里可以保留两件由你明确说过的事：\n"
        "请记住：我最近一个月的目标是完成机器学习课程。\n"
        "请记住：我更喜欢先理解原理再写代码。\n"
        "请单独发送其中一条，发送后我才会保存。"
    )
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, _growth, memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-summary-memory"
    history.new_session(session_id=session_id)
    history.add_message(session_id, "user", "我最近一个月的目标是完成机器学习课程")
    history.add_message(session_id, "assistant", "你还可以每天安排三个小时强化训练。")
    before = len(memory.memories())

    response = service.handle("总结一下我们的对话然后保存长期记忆", session_id)

    assert response.status == "chat"
    assert response.tool_results == []
    assert len(memory.memories()) == before
    assert "请记住：" in response.message
    assert llm.semantic_calls == 1
    assert len(llm.reply_calls) == 1

    saved = service.handle(
        "请记住：我最近一个月的目标是完成机器学习课程。",
        session_id,
    )

    assert [item.tool for item in saved.tool_results] == ["save_formal_memory"]
    contents = [str(item.get("content", "")) for item in memory.memories()]
    assert contents == ["我最近一个月的目标是完成机器学习课程。"]
    assert all("每天安排三个小时" not in item for item in contents)


def test_stable_preference_uses_one_llm_decision_then_requires_confirmation(tmp_path, capsys):
    text = "我喜欢羊肉串"
    decision = {
        "mode": "write",
        "intent": "add_memory_request",
        "entities": {"content": "羊肉串", "category": "user_preference"},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "",
        "request_mode": "execute",
        "explicit_command": False,
    }
    llm = SemanticThenReplyLLM(decision, "不会用于工具成功回复。")
    service, _growth, memory, _history = _production_semantic_service(tmp_path, llm)

    offered = service.handle(text, "v22-preference")

    assert offered.status == "clarification"
    assert offered.tool_results == []
    assert memory.memories() == []
    assert llm.semantic_calls == 1
    assert llm.reply_calls == []
    output = capsys.readouterr().out
    assert output.count("source=llm intent=add_memory_request") == 1
    assert "source=preference_guard" not in output

    saved = service.handle("确认", "v22-preference")

    assert [item.tool for item in saved.tool_results] == ["save_formal_memory"]
    assert [item["content"] for item in memory.memories()] == ["羊肉串"]


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("我要努力学习", "这个方向还比较宽。你最想先提升哪门具体内容？"),
        ("我要成为百万富翁", "这个愿望背后，你更看重自由、保障，还是事业成就？"),
    ],
)
def test_broad_aspirations_remain_chat_without_side_effects(tmp_path, text, reply):
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, growth, memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle(text, f"v22-aspiration-{text}")

    assert response.status == "chat"
    assert response.message == reply
    assert response.tool_results == []
    assert growth.tasks() == []
    assert memory.memories() == []
    assert "目前还没有执行" not in response.message


def test_plan_candidate_strips_conversational_prefix_before_pending_and_write(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {
            "title": "好啦，多谢你的提醒，我现在想整理摄影构图",
        },
        "proposed_tool": "add_plan",
        "confidence": 0.96,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "desire",
        "request_mode": "possible_action",
        "explicit_command": False,
    }
    llm = SemanticThenReplyLLM(decision, "不应直接进入模型回复。")
    service, growth, _memory, _history = _production_semantic_service(
        tmp_path,
        llm,
    )

    first = service.handle("好啦，多谢你的提醒，我现在想整理摄影构图", "v22-plan-title")

    assert first.status == "clarification"
    state = service.interaction_coordinator.current("v22-plan-title")
    assert state.known_fields["title"] == "整理摄影构图"
    assert growth.tasks() == []

    second = service.handle("加入今天计划", "v22-plan-title")

    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["整理摄影构图"]


def test_plan_payload_command_strips_conversation_before_persisting(tmp_path):
    """Explicit payload commands must persist only their task payload."""
    llm = RecordingLLM("这次不应调用模型。")
    service, growth, _memory, _history = _production_semantic_service(
        tmp_path,
        llm,
    )

    response = service.handle(
        "好的，多谢你的建议，我现在想把整理露营装备加入今天计划",
        "v22-envelope-plan-title",
    )

    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["整理露营装备"]
    assert llm.calls == []


def test_specific_learning_interest_reuses_three_way_choice(tmp_path):
    llm = SemanticThenReplyLLM(_chat_decision(), "建议内容不应直接执行。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle("我想入门手工皮具制作", "v22-interest-choice")

    assert response.status == "clarification"
    assert "现在开始" in response.message
    assert "先听建议" in response.message
    assert "加入今天计划" in response.message
    assert growth.tasks() == []


def test_different_plan_title_is_allowed_even_when_it_contains_existing_title(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "整理验收旅行资料"},
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    growth.add_task("整理旅行资料")

    response = service.handle("把整理验收旅行资料加入今天计划", "v22-exact-duplicate")

    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert {item["title"] for item in growth.tasks()} == {"整理旅行资料", "整理验收旅行资料"}


def test_duplicate_confirmation_accepts_keep_phrasings_and_preserves_fields(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {
            "title": "验收资料归档",
            "time_slot": "晚上",
            "duration_minutes": 40,
        },
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    growth.add_task("验收资料归档", time_slot="晚上", duration_minutes=40)

    first = service.handle(
        "请把验收资料归档加入今天计划，今晚安排40分钟",
        "v22-duplicate-confirm",
    )
    second = service.handle("继续保留这条计划", "v22-duplicate-confirm")

    assert first.status == "clarification"
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    tasks = [item for item in growth.tasks() if item["title"] == "验收资料归档"]
    assert len(tasks) == 2
    assert all(item.get("time_slot") == "晚上" for item in tasks)
    assert all(item.get("duration_minutes") == 40 for item in tasks)


def test_plan_title_removes_repeated_action_shell(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "再把验收-统计概念理解加入今天计划"},
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle(
        "再把验收-统计概念理解加入今天计划",
        "v22-title-shell",
    )

    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["验收-统计概念理解"]


def test_complete_new_plan_command_preempts_missing_slot_pending(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "v22-fresh-command-preempts-pending"
    service.interaction_coordinator.awaiting_clarification(
        conversation_id,
        "missing_slots",
        missing_fields=["title"],
        immutable_arguments={"tool_name": "add_plan"},
        domain="plan",
        request_mode="execute",
    )

    response = service.handle(
        "再把验收-完整新命令加入今天计划",
        conversation_id,
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["验收-完整新命令"]


def test_stable_preference_with_routine_prefix_is_saved_only_after_confirmation(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_memory_request",
        "entities": {},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "",
        "request_mode": "execute",
        "explicit_command": False,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, _growth, memory, _history = _production_semantic_service(tmp_path, llm)

    offered = service.handle(
        "我平时更喜欢在安静的环境里读验收文档",
        "v22-routine-preference",
    )

    assert offered.status == "clarification"
    assert offered.tool_results == []
    assert memory.memories() == []

    saved = service.handle("确认", "v22-routine-preference")

    assert [item.tool for item in saved.tool_results] == ["save_formal_memory"]
    assert [item["content"] for item in memory.memories()] == ["在安静的环境里读验收文档"]


def test_bare_time_memory_follow_up_keeps_previous_user_fact(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_memory_request",
        "entities": {"content": "10点"},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, _growth, memory, _history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-memory-time-slot"
    service.conversation_service.state_manager.observe_user(
        session_id,
        "我以后要早睡才行",
    )
    service.conversation_service.state_manager.observe_assistant(
        session_id,
        "你今天打算几点睡？",
    )

    response = service.handle("10点你记住", session_id)

    assert [item.tool for item in response.tool_results] == ["save_formal_memory"]
    assert [item["content"] for item in memory.memories()] == [
        "我以后要早睡才行，时间是10点"
    ]


def test_noon_plan_value_survives_validation_without_repair(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {
            "title": "整理午间验收资料",
            "time_slot": "中午",
            "duration_minutes": 25,
        },
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle(
        "今天中午安排25分钟整理午间验收资料",
        "v22-noon-plan",
    )

    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert growth.tasks()[0]["time_slot"] == "中午"
    assert llm.reply_calls == []


def test_summary_memory_prompt_excludes_old_session_and_assistant_advice(tmp_path):
    reply = "请记住：我最近一个月的目标是完成验收课程。请单独发送这条后才会保存。"
    llm = SemanticThenReplyLLM(_chat_decision(), reply)
    service, _growth, memory, history = _production_semantic_service(tmp_path, llm)
    old = history.new_session(session_id="v22-old-summary")
    history.add_message(old["session_id"], "user", "我最近一个月的目标是完成机器学习")
    history.save_summary(old["session_id"], "用户最近一个月的目标是完成机器学习。")
    session_id = "v22-current-summary"
    history.new_session(session_id=session_id)
    history.add_message(session_id, "user", "我最近一个月的目标是完成验收课程")
    history.add_message(session_id, "assistant", "你还可以每天安排三个小时强化训练。")

    response = service.handle("总结一下我们的对话然后保存长期记忆", session_id)

    assert response.status == "chat"
    prompt = json.dumps(llm.reply_calls[0], ensure_ascii=False)
    assert "完成验收课程" in prompt
    assert "完成机器学习" not in prompt
    assert "每天安排三个小时" not in prompt
    assert memory.memories() == []


def test_current_pending_plan_target_wins_for_just_that_reference(tmp_path):
    llm = SemanticThenReplyLLM(_chat_decision(), "不应直接进入模型回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    first = service.handle("我想学习数学", "v22-current-plan-reference")
    second = service.handle(
        "就把刚才这个安排进今天计划",
        "v22-current-plan-reference",
    )

    assert first.status == "clarification"
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["学习数学"]


@pytest.mark.parametrize(
    "follow_up",
    ["把它加入今天计划", "把这个加入今天计划", "把这件事加入今天计划"],
)
def test_pure_pronoun_payload_uses_current_pending_plan_title(tmp_path, follow_up):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "学习机器学习", "duration_minutes": 30},
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "desire",
        "request_mode": "possible_action",
        "explicit_command": False,
    }
    llm = SemanticThenReplyLLM(decision, "不应直接进入模型回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    conversation_id = "v22-pending-pronoun-plan-reference"

    first = service.handle("我今天想学半小时机器学习", conversation_id)
    pending = service.interaction_coordinator.current(conversation_id)
    second = service.handle(follow_up, conversation_id)
    diagnostic = service.diagnostic_snapshot()[-1]

    assert first.status == "clarification"
    assert pending.state == "awaiting_choice"
    assert pending.known_fields == {
        "title": "学习机器学习",
        "duration_minutes": 30,
    }
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["学习机器学习"]
    assert growth.tasks()[0]["duration_minutes"] == 30
    assert diagnostic["interaction_state_before"] == "awaiting_choice"
    assert diagnostic["coordinator_decision"] == "pending_continuation"


def test_assistant_plan_reference_lists_options_then_writes_selected_item(tmp_path):
    assistant_reply = (
        "今天只做三件事。第一，打开终端，输入 python --version，"
        "把输出结果发给我，确认环境没问题。第二，装一个叫 scikit-learn 的库，"
        "命令是 pip install scikit-learn，装完也告诉我。第三，先不用跑代码，"
        "只需要打开一个 Python 文件，写下 Iris 数据集示例。"
    )
    llm = AssistantPlanOptionLLM(
        [
            {"id": "1", "title": "运行 python --version"},
            {"id": "2", "title": "安装 scikit-learn"},
            {"id": "3", "title": "去跑步"},
        ]
    )
    service, growth, _memory, history = _production_semantic_service(
        tmp_path,
        llm,
    )
    session_id = "v22-assistant-plan-reference"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")
    conversation_service.state_manager.observe_assistant(
        session_id,
        "好，我已经把刚才的内容加入今天计划了。",
    )

    first = service.handle(
        "你好把你说的加入今天计划",
        session_id,
    )

    assert first.status == "clarification"
    assert "运行python--version" in first.message
    assert "安装scikit-learn" in first.message
    assert "去跑步" not in first.message
    assert growth.tasks() == []
    first_diagnostic = service.diagnostic_snapshot()[-1]

    second = service.handle("第二个", session_id)

    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["安装scikit-learn"]
    assert "你上面的规划" not in str(growth.tasks())
    assert first_diagnostic["semantic_decision"]["intent"] == "add_plan"
    assert first_diagnostic["semantic_decision"]["proposed_tool"] == "add_plan"
    assert llm.events == ["option_extraction"]
    extraction_prompt = json.dumps(llm.calls[0], ensure_ascii=False)
    assert "输入 python --version" in extraction_prompt
    assert "已经把刚才的内容加入" not in extraction_prompt


def test_assistant_plan_reference_cannot_override_canonical_chat_decision(tmp_path):
    llm = SemanticThenReplyLLM(_chat_decision(), "这次先不修改计划。")
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-assistant-plan-reference-chat"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(
        session_id,
        "assistant",
        "第一步核对镜头，第二步检查音轨。",
        intent="chat",
    )
    semantic = SemanticParseResult(
        source="fake_model",
        candidates=[],
        request_mode="discuss",
        plain_chat_probability=1.0,
        intent_result=_chat_decision(),
    )
    parser = conversation_service.semantic_action_parser
    parser.parse = lambda *args, **kwargs: semantic
    parser.parse_unified = lambda *args, **kwargs: semantic

    response = service.handle("把你刚才的安排加入今天计划", session_id)

    assert response.status == "chat"
    assert response.message == "这次先不修改计划。"
    assert growth.tasks() == []
    assert llm.semantic_calls == 0
    assert len(llm.reply_calls) == 1


def test_natural_assistant_plan_reference_lists_options_before_writing(tmp_path):
    assistant_reply = (
        "今天按三步推进：先核对镜头清单，再确认素材筛选，最后记录成片复盘。"
    )
    llm = AssistantPlanOptionLLM(
        [
            {"id": "1", "title": "核对镜头清单"},
            {"id": "2", "title": "确认素材筛选"},
            {"id": "3", "title": "记录成片复盘"},
        ]
    )
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-natural-assistant-plan-reference"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")

    first = service.handle(
        "嗯，把你刚刚梳理的安排塞进我今天待办吧",
        session_id,
    )

    assert first.status == "clarification"
    assert "核对镜头清单" in first.message
    assert "确认素材筛选" in first.message
    assert growth.tasks() == []

    second = service.handle("第二项", session_id)

    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["确认素材筛选"]


def test_assistant_plan_reference_separates_context_from_write_destination(tmp_path):
    assistant_reply = "今晚按三步推进：核对片头、检查音轨、记录发布清单。"
    llm = AssistantPlanOptionLLM(
        [
            {"id": "1", "title": "核对片头"},
            {"id": "2", "title": "检查音轨"},
            {"id": "3", "title": "记录发布清单"},
        ]
    )
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-assistant-plan-structural-reference"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")

    first = service.handle(
        "行，把你方才整理出来的内容放到我今天清单里",
        session_id,
    )

    assert first.status == "clarification"
    assert "核对片头" in first.message
    assert "检查音轨" in first.message
    assert growth.tasks() == []


def test_assistant_plan_reference_falls_back_to_ordered_headings(tmp_path):
    assistant_reply = (
        "第一步，章节标记。抽查关键节点。第二步，旁白音量。重点听噪声段落。"
        "第三步，交付备注。汇总确认结果。"
    )
    llm = AssistantPlanOptionLLM([])
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-assistant-plan-fallback-options"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")

    first = service.handle("把你刚才整理的步骤放进今天待办", session_id)

    assert first.status == "clarification"
    assert "章节标记" in first.message
    assert "旁白音量" in first.message
    assert growth.tasks() == []


def test_assistant_plan_reference_falls_back_to_natural_chinese_ordinals(tmp_path):
    assistant_reply = (
        "第一，核对功能描述与实际版本是否一致。"
        "第二，单独检查日期、版本号和链接。"
        "第三，从用户视角通读模糊表述。"
    )
    llm = AssistantPlanOptionLLM([])
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-assistant-plan-natural-ordinal-fallback"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")

    first = service.handle(
        "把你刚才列的这些办法放到今天待办，我先挑一条。",
        session_id,
    )
    pending = service.interaction_coordinator.current(session_id)
    diagnostic = service.diagnostic_snapshot()[-1]

    assert first.status == "clarification"
    assert growth.tasks() == []
    assert pending.state == "awaiting_choice"
    assert pending.interaction_kind == "assistant_plan_selection"
    assert len(pending.suggested_options) == 3
    assert "功能描述" in pending.suggested_options[0]["title"]
    assert "日期、版本号和链接" in pending.suggested_options[1]["title"]
    assert "用户视角" in pending.suggested_options[2]["title"]
    assert diagnostic["route_source"] == "reference_resolution"
    assert diagnostic["semantic_parse_source"] == "assistant_plan_fallback"
    assert "assistant_plan_model_options:0" in diagnostic["validation_notes"]
    assert len(llm.calls) == 1

    second = service.handle("第二项，就它。", session_id)

    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["单独检查日期、版本号和链接"]


def test_direct_schedule_confirmation_writes_all_clear_items_once(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "吃饭休息，不碰学习", "duration_minutes": 30},
        "proposed_tool": "add_plan",
        "confidence": 0.98,
        "follow_up_target": None,
        "needs_confirmation": True,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "desire",
        "request_mode": "possible_action",
        "explicit_command": False,
    }
    llm = SemanticThenReplyLLM(decision, "不应进入普通模型回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-direct-schedule-batch"
    schedule = (
        "18:00 到 18:30 吃饭休息，不碰学习\n"
        "18:30 到 19:10 整理今天的资料\n"
        "19:10 到 19:20 站起来活动一下\n"
        "19:20 到 20:00 看书或者复习英语，二选一\n"
        "20:00之后留给自己"
    )

    first = service.handle(schedule, session_id)
    pending = service.interaction_coordinator.current(session_id)
    second = service.handle("加入计划", session_id)

    assert first.status == "clarification"
    assert "3" in first.message
    assert "未确定的选择" in first.message
    assert len(pending.suggested_options) == 3
    assert [item.tool for item in second.tool_results] == [
        "add_plan",
        "add_plan",
        "add_plan",
    ]
    assert [item["title"] for item in growth.tasks()] == [
        "吃饭休息，不碰学习",
        "整理今天的资料",
        "站起来活动一下",
    ]
    assert [item["duration_minutes"] for item in growth.tasks()] == [30, 40, 10]


def test_assistant_time_schedule_fallback_exposes_selectable_options(tmp_path):
    assistant_reply = (
        "18:00 到 18:30 吃饭休息\n"
        "18:30 到 19:10 整理今天的资料\n"
        "19:10 到 19:20 站起来活动一下"
    )
    llm = AssistantPlanOptionLLM([])
    service, growth, _memory, history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-assistant-time-schedule-fallback"
    conversation_service = service.conversation_service
    conversation_service._ensure_history_session(session_id)
    history.add_message(session_id, "assistant", assistant_reply, intent="chat")

    first = service.handle("把你刚才的时间安排放进今天计划", session_id)
    pending = service.interaction_coordinator.current(session_id)
    second = service.handle("第二项", session_id)

    assert first.status == "clarification"
    assert pending.interaction_kind == "assistant_plan_selection"
    assert [item["title"] for item in pending.suggested_options] == [
        "吃饭休息",
        "整理今天的资料",
        "站起来活动一下",
    ]
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["整理今天的资料"]


def test_advice_reply_keeps_multiple_plan_options_for_natural_follow_up(tmp_path):
    llm = AdvicePlanLLM(
        "下午先复习机器学习四十分钟，再写五百字小说。",
        [
            {"id": "1", "title": "复习机器学习四十分钟"},
            {"id": "2", "title": "写五百字小说"},
        ],
    )
    service, growth, _memory, _history = build_service(tmp_path, llm)
    conversation_id = "v22-advice-options-follow-up"
    service.interaction_coordinator.awaiting_choice(
        conversation_id,
        "advice_or_action_choice",
        immutable_arguments={"tool_name": "add_plan"},
        domain="plan",
        request_mode="possible_action",
        known_fields={"title": "下午规划"},
    )

    advice = service.handle("你建议", conversation_id)
    pending = service.interaction_coordinator.current(conversation_id)
    choose = service.handle("这个安排合适，安排进今日计划吧", conversation_id)
    completed = service.handle("第二个", conversation_id)

    assert advice.status == "chat"
    assert advice.message == "下午先复习机器学习四十分钟，再写五百字小说。"
    assert pending.interaction_kind == "assistant_plan_offer"
    assert [item["title"] for item in pending.suggested_options] == [
        "复习机器学习四十分钟",
        "写五百字小说",
    ]
    assert choose.status == "clarification"
    assert "选择一项" in choose.message
    assert completed.status == "completed"
    assert [item["title"] for item in growth.tasks()] == ["写五百字小说"]


def test_single_advice_plan_option_can_be_added_by_contextual_reference(tmp_path):
    llm = AdvicePlanLLM(
        "今晚睡前半小时放下手机。",
        [{"id": "1", "title": "睡前半小时放下手机"}],
    )
    service, growth, _memory, _history = build_service(tmp_path, llm)
    conversation_id = "v22-single-advice-follow-up"
    service.interaction_coordinator.awaiting_choice(
        conversation_id,
        "advice_or_action_choice",
        immutable_arguments={"tool_name": "add_plan"},
        domain="plan",
        request_mode="possible_action",
        known_fields={"title": "早睡"},
    )

    advice = service.handle("你建议", conversation_id)
    completed = service.handle("把你说的安排进今日计划", conversation_id)

    assert advice.status == "chat"
    assert completed.status == "completed"
    assert [item["title"] for item in growth.tasks()] == ["睡前半小时放下手机"]


def test_chat_prompt_prioritizes_current_greeting_over_prior_advice(tmp_path):
    service, _growth, _memory, history = build_service(tmp_path, NoCallLLM())
    conversation_id = "v22-greeting-relevance"
    service.conversation_service._ensure_history_session(conversation_id)
    history.add_message(conversation_id, "assistant", "今晚早点睡，睡前放下手机。", intent="chat")

    messages = service.conversation_service.build_llm_messages("你好", conversation_id)

    system_text = "\n".join(
        str(item.get("content", ""))
        for item in messages
        if item.get("role") == "system"
    )
    assert "最后一条当前用户输入为最高优先级" in system_text
    assert messages[-1] == {"role": "user", "content": "你好"}


def test_plan_choice_confirmation_consumes_pending_without_semantic_reroute(tmp_path):
    llm = SemanticThenReplyLLM(_chat_decision(), "不应进入普通模型回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    session_id = "v22-plan-choice-confirmation"
    clock = [datetime(2026, 8, 20, 9, 0, 0)]
    service.interaction_coordinator.now_provider = lambda: clock[0]

    first = service.handle("我今天想整理验收字幕卡", session_id)
    clock[0] += timedelta(seconds=301)
    second_turn = service.conversation_service.prepare(
        "好，还是按刚才说的来，把这件事放进我今天的清单。",
        session_id,
        record_history=True,
        allow_llm_intent=False,
    )
    second = service.conversation_service.complete(second_turn)
    diagnostics = service.diagnostic_snapshot()

    assert first.status == "clarification"
    assert [item.tool for item in second.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == ["整理验收字幕卡"]
    assert llm.semantic_calls == 1
    assert llm.reply_calls == []
    assert diagnostics[-2]["coordinator_decision"] == "fresh_turn"
    assert diagnostics[-1]["coordinator_decision"] == "pending_continuation"
    assert diagnostics[-1]["interaction_state_before"] == "awaiting_choice"
    assert diagnostics[-1]["route_source"] == "interaction_state"
    assert diagnostics[-1]["semantic_parse_source"] == "interaction_state"
    assert diagnostics[-1]["interaction_state_after"] == "completed"


@pytest.mark.parametrize("confidence", [0.8, 0.9])
def test_resolved_explicit_add_plan_is_not_rejected_by_confidence_twice(
    tmp_path,
    confidence,
):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": f"验收置信度基线 {confidence}"},
        "proposed_tool": "add_plan",
        "confidence": confidence,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "不应进入普通聊天回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)

    response = service.handle("请按刚才约定落实这项安排", f"v22-confidence-{confidence}")

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["add_plan"]
    assert [item["title"] for item in growth.tasks()] == [f"验收置信度基线{confidence}"]


def test_missing_completed_plan_creates_structured_action_log_offer(tmp_path):
    decision = {
        "mode": "write",
        "intent": "complete_plan",
        "entities": {"query": "完成桌面真实验收"},
        "proposed_tool": "complete_plan",
        "confidence": 0.9,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "不应进入普通聊天回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    conversation_id = "v22-action-log-offer"

    first = service.handle("我完成了桌面真实验收", conversation_id)
    pending = service.interaction_coordinator.current(conversation_id)

    assert first.status == "clarification"
    assert "记录一条行动" in first.message
    assert pending.state == "awaiting_confirmation"
    assert pending.interaction_kind == "action_log_offer"
    assert pending.immutable_arguments == {
        "tool_name": "add_action_log",
        "content": "我完成了桌面真实验收",
    }
    assert growth.records_for_date() == []

    second = service.handle("好", conversation_id)

    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == ["add_action_log"]
    assert [item["content"] for item in growth.records_for_date()] == [
        "我完成了桌面真实验收"
    ]
    assert service.interaction_coordinator.current(conversation_id).state == "completed"


def test_plan_display_ordinal_corrects_wrong_model_target_end_to_end(tmp_path):
    decision = {
        "mode": "write",
        "intent": "complete_plan",
        "entities": {"query": "隔离验收乙：给绿植浇水"},
        "proposed_tool": "complete_plan",
        "confidence": 0.95,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "不应进入普通聊天回复。")
    service, growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    first = growth.add_task("隔离验收甲：整理窗边书桌")
    second = growth.add_task("隔离验收乙：给绿植浇水")

    response = service.handle(
        "我刚把计划一做完啦，麻烦替我记成完成。",
        "v22-plan-display-ordinal",
    )
    tasks = growth.tasks()
    diagnostic = service.diagnostic_snapshot()[-1]

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["complete_plan"]
    assert tasks[0]["uid"] == first["uid"]
    assert tasks[0]["done"] is True
    assert tasks[1]["uid"] == second["uid"]
    assert tasks[1]["done"] is False
    assert diagnostic["selected_object_ids"] == [first["uid"]]
    assert diagnostic["tool_results"][0]["changed_resource_ids"] == [first["uid"]]
    assert diagnostic["postcondition_result"] == [
        {"tool": "complete_plan", "verified": True}
    ]


def test_ambiguous_plan_selection_keeps_selected_task_identity(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first_task = growth.add_task("写小说至少三百字")
    second_task = growth.add_task("写小说至少五百字")
    conversation_id = "v22-selected-plan-identity"

    first = service.handle("我已经完成写小说了", conversation_id)
    pending = service.interaction_coordinator.current(conversation_id)
    second = service.handle("1.", conversation_id)
    tasks = growth.tasks()

    assert first.status == "clarification"
    assert pending.interaction_kind == "object_selection"
    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == ["complete_plan"]
    assert next(item for item in tasks if item["uid"] == first_task["uid"])["done"] is True
    assert next(item for item in tasks if item["uid"] == second_task["uid"])["done"] is False
    assert service.interaction_coordinator.current(conversation_id).state == "completed"


def test_completed_plan_candidate_list_supports_one_short_explicit_follow_up(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first_task = growth.add_task("写小说至少三百字")
    second_task = growth.add_task("写小说至少五百字")
    conversation_id = "v22-selected-plan-follow-up"

    clarification = service.handle("我已经完成写小说了", conversation_id)
    first_completion = service.handle("1.", conversation_id)
    second_completion = service.handle("2.这个也完成了", conversation_id)
    tasks = growth.tasks()

    assert clarification.status == "clarification"
    assert first_completion.status == "completed"
    assert second_completion.status == "completed"
    assert [item.tool for item in second_completion.tool_results] == ["complete_plan"]
    assert next(item for item in tasks if item["uid"] == first_task["uid"])["done"] is True
    assert next(item for item in tasks if item["uid"] == second_task["uid"])["done"] is True
    assert service.interaction_coordinator.current(conversation_id).state == "completed"


def test_completed_candidate_list_does_not_consume_unrelated_ordinal_chat(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    growth.add_task("写小说至少三百字")
    growth.add_task("写小说至少五百字")
    conversation_id = "v22-selected-plan-unrelated-follow-up"

    service.handle("我已经完成写小说了", conversation_id)
    service.handle("1.", conversation_id)
    llm = SemanticThenReplyLLM(_chat_decision(), "第二章我们可以接着讨论。")
    service.llm_client = llm
    service.conversation_service.llm_client = llm
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    unrelated = service.handle("第二章也可以讨论了", conversation_id)

    assert unrelated.status == "chat"
    assert unrelated.message == "第二章我们可以接着讨论。"
    assert len([item for item in growth.tasks() if item["done"]]) == 1


@pytest.mark.parametrize("selection", ["都处理", "全部完成", "第一和第二个"])
def test_ambiguous_plan_selection_can_complete_multiple_candidates(
    tmp_path,
    selection,
):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    first_task = growth.add_task("写小说至少三百字")
    second_task = growth.add_task("写小说至少五百字")
    conversation_id = f"v22-multiple-plan-selection-{selection}"

    first = service.handle("我已经完成写小说了", conversation_id)
    second = service.handle(selection, conversation_id)
    tasks = growth.tasks()

    assert first.status == "clarification"
    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == [
        "complete_plan",
        "complete_plan",
    ]
    assert next(item for item in tasks if item["uid"] == first_task["uid"])["done"] is True
    assert next(item for item in tasks if item["uid"] == second_task["uid"])["done"] is True
    assert service.interaction_coordinator.current(conversation_id).state == "completed"


def test_first_three_completed_plans_are_bound_then_confirmed(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    tasks = [
        growth.add_task("整理书桌"),
        growth.add_task("复习机器学习"),
        growth.add_task("写五百字小说"),
        growth.add_task("睡前拉伸"),
    ]
    conversation_id = "v22-first-three-completion"

    first = service.handle("我前三个都完成了", conversation_id)
    before_confirmation = growth.tasks()
    second = service.handle("确认", conversation_id)
    after_confirmation = growth.tasks()

    assert first.status == "clarification"
    assert "整理书桌" in first.message
    assert "复习机器学习" in first.message
    assert "写五百字小说" in first.message
    assert all(item["done"] is False for item in before_confirmation)
    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == ["complete_plan"] * 3
    done_by_uid = {item["uid"]: item["done"] for item in after_confirmation}
    assert [done_by_uid[item["uid"]] for item in tasks] == [True, True, True, False]


def test_all_except_named_plan_is_bound_then_confirmed(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    tasks = [
        growth.add_task("写小说"),
        growth.add_task("写小说至少五百字"),
        growth.add_task("机器学习"),
        growth.add_task("睡前半小时放下手机，做慢呼吸，比平时早半小时睡"),
    ]
    conversation_id = "v22-all-except-sleep-completion"

    first = service.handle("我今日计划除了早睡都已经完成了", conversation_id)
    second = service.handle("确认", conversation_id)
    done_by_uid = {item["uid"]: item["done"] for item in growth.tasks()}

    assert first.status == "clarification"
    assert "早半小时睡" not in first.message
    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == ["complete_plan"] * 3
    assert [done_by_uid[item["uid"]] for item in tasks] == [True, True, True, False]


def test_multiple_named_completed_plans_are_bound_then_confirmed(tmp_path):
    service, growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    tasks = [
        growth.add_task("写小说"),
        growth.add_task("写小说至少五百字"),
        growth.add_task("机器学习"),
    ]
    conversation_id = "v22-named-batch-completion"

    first = service.handle("我写小说和机器学习都完成了", conversation_id)
    second = service.handle("确认", conversation_id)
    done_by_uid = {item["uid"]: item["done"] for item in growth.tasks()}

    assert first.status == "clarification"
    assert "写小说" in first.message
    assert "机器学习" in first.message
    assert "写小说至少五百字" not in first.message
    assert second.status == "completed"
    assert [item.tool for item in second.tool_results] == ["complete_plan"] * 2
    assert [done_by_uid[item["uid"]] for item in tasks] == [True, False, True]


def test_failed_tool_result_does_not_leave_interaction_awaiting_tool_result(tmp_path):
    decision = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": "模拟写入失败"},
        "proposed_tool": "add_plan",
        "confidence": 0.9,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "commitment",
        "request_mode": "execute",
        "explicit_command": True,
    }
    llm = SemanticThenReplyLLM(decision, "不应进入普通聊天回复。")
    service, _growth, _memory, _history = _production_semantic_service(tmp_path, llm)
    service.agent_core.executor.registry.get("add_plan").handler = lambda **_kwargs: ToolResult(
        False,
        "add_plan",
        "failed",
        error="simulated_write_failure",
    )
    conversation_id = "v22-tool-failure-state"

    response = service.handle("把模拟写入失败加入今天计划", conversation_id)
    state = service.interaction_coordinator.current(conversation_id)
    diagnostic = service.diagnostic_snapshot()[-1]

    assert response.status == "failed"
    assert len(response.tool_results) == 1
    assert not response.tool_results[0].success
    assert state.state == "cancelled"
    assert state.consumed is True
    assert diagnostic["interaction_state_after"] == "cancelled"
