"""A learning start continues its topic without becoming a plan write."""

import pytest

from modules.conversation_service import ConversationService
from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from v18_test_support import RecordingLLM, build_service


TOPIC = "Transformer 注意力机制原理"
NORMALIZED_TOPIC = "Transformer注意力机制原理"


def _runtime(tmp_path):
    llm = RecordingLLM("先看一个例子：注意力用 query 与 key 的相关性决定 value 的权重。")
    agent, growth, memory, history = build_service(tmp_path, llm)
    session = history.ensure_session(restore_latest=False)["session_id"]
    return agent.conversation_service, growth, memory, history, llm, session


def _prepare_wish(service, session):
    """Reproduce the validated model decision from the actual failed turn."""
    candidate = ActionCandidate(
        domain="plan", tool_name="add_plan", arguments={"title": TOPIC},
        request_mode="possible_action", confidence=0.95,
        explicit_command=False, source_intent="add_plan",
    )
    semantic = SemanticParseResult(
        source="llm", candidates=[candidate], request_mode="possible_action",
        plain_chat_probability=0.0,
        intent_result={
            "intent": "add_plan", "mode": "write", "confidence": 0.95,
            "entities": {"title": TOPIC}, "needs_confirmation": False,
            "source": "llm", "request_mode": "possible_action",
            "subject": "self", "polarity": "positive", "modality": "desire",
            "request_id": "req_learning_wish", "clarification_question": None,
        },
    )
    parser = service.semantic_action_parser
    original_parse, original_unified = parser.parse, parser.parse_unified
    parser.parse = lambda *args, **kwargs: semantic
    parser.parse_unified = lambda *args, **kwargs: semantic
    try:
        response = service.handle("通过 GitHub 文档学习第三章大模型 Transformer 注意力机制原理", session)
    finally:
        parser.parse, parser.parse_unified = original_parse, original_unified
    assert response.status == "clarification"
    assert "现在开始" in response.message
    assert service.interaction_coordinator.current(session).known_fields["title"] == NORMALIZED_TOPIC


@pytest.mark.parametrize("start", ["开始", "现在开始", "开始吧"])
def test_wish_start_keeps_topic_and_guided_learning_requests_actual_content(tmp_path, start):
    service, growth, _memory, _history, llm, session = _runtime(tmp_path)
    _prepare_wish(service, session)

    choice = service.handle(start, session)
    assert "我带着你学" in choice.message
    state = service.interaction_coordinator.current(session)
    assert state.interaction_kind == "learning_mode_choice"
    assert state.known_fields["learning_topic"] == NORMALIZED_TOPIC
    assert llm.calls == []

    turn = service.prepare("带我学", session)
    assert turn.requires_llm
    assert turn.intent_result["request_mode"] == "guided_learning"
    assert turn.preserve_advice_as_plan_options is False
    assert NORMALIZED_TOPIC in turn.llm_messages[-1]["content"]
    instruction = "\n".join(item["content"] for item in turn.llm_messages if item["role"] == "system")
    assert "直接讲一个具体概念或第一步" in instruction
    assert "不假称看过指定文档" in instruction
    response = service.complete(turn)
    assert "query" in response.message
    assert len(llm.calls) == 1
    assert response.tool_results == []
    assert growth.tasks() == []
    assert growth.records_for_date() == []
    assert not service.interaction_coordinator.current(session).last_suggestion_snapshot


def test_independent_learning_does_not_complete_or_time_a_plan(tmp_path):
    service, growth, _memory, _history, llm, session = _runtime(tmp_path)
    task = growth.add_task("学习注意力机制")
    service.handle("开始学习注意力机制", session)
    response = service.handle("我按自己的安排学", session)
    assert response.status == "chat"
    assert "完成后回来" in response.message
    assert "勾选对应计划" in response.message
    assert llm.calls == []
    assert response.tool_results == []
    assert not growth.tasks()[0]["done"]
    assert growth.tasks()[0]["uid"] == task["uid"]
    assert growth.records_for_date() == []
    assert not service.interaction_coordinator.current(session).pending


def test_direct_guided_learning_skips_choice_and_never_creates_plan_snapshot(tmp_path):
    service, growth, _memory, _history, llm, session = _runtime(tmp_path)
    llm.reply = "1. 注意力权重来自 query 和 key 的匹配。\n2. 加权求和得到输出。"
    turn = service.prepare("带我学注意力机制", session)
    assert turn.requires_llm
    assert "注意力机制" in turn.llm_messages[-1]["content"]
    service.complete(turn)
    assert growth.tasks() == []
    assert not service.interaction_coordinator.current(session).last_suggestion_snapshot


def test_choose_advice_keeps_existing_plan_advice_route(tmp_path):
    service, growth, _memory, _history, _llm, session = _runtime(tmp_path)
    _prepare_wish(service, session)
    turn = service.prepare("建议", session)
    assert turn.requires_llm
    assert turn.preserve_advice_as_plan_options
    assert NORMALIZED_TOPIC in turn.llm_messages[-1]["content"]
    assert growth.tasks() == []


def test_polite_guided_reply_keeps_topic_from_learning_mode_choice(tmp_path):
    service, growth, _memory, _history, llm, session = _runtime(tmp_path)
    _prepare_wish(service, session)
    service.handle("开始", session)
    turn = service.prepare("请你带着我学吧", session)
    assert turn.intent_result["request_mode"] == "guided_learning"
    assert NORMALIZED_TOPIC in turn.llm_messages[-1]["content"]
    assert "还没有说主题" not in turn.llm_messages[-1]["content"]
    assert llm.calls == []
    assert growth.tasks() == []


def test_polite_guided_reply_keeps_topic_from_advice_or_action_choice(tmp_path):
    service, growth, _memory, _history, llm, session = _runtime(tmp_path)
    _prepare_wish(service, session)
    turn = service.prepare("能不能带我学", session)
    assert turn.intent_result["request_mode"] == "guided_learning"
    assert NORMALIZED_TOPIC in turn.llm_messages[-1]["content"]
    assert "还没有说主题" not in turn.llm_messages[-1]["content"]
    assert llm.calls == []
    assert growth.tasks() == []


@pytest.mark.parametrize("message", ["取消", "你好", "查看今日计划"])
def test_cancel_or_new_topic_does_not_consume_old_learning_topic(tmp_path, message):
    service, growth, _memory, _history, _llm, session = _runtime(tmp_path)
    service.handle("开始学习注意力机制", session)
    turn = service.prepare(message, session)
    assert turn.intent_result.get("request_mode") != "guided_learning"
    assert not service.interaction_coordinator.current(session).pending
    assert growth.tasks() == []


def test_explicit_add_today_plan_keeps_original_execution(tmp_path):
    service, growth, _memory, _history, _llm, session = _runtime(tmp_path)
    response = service.handle("今天添加计划：学习注意力机制", session)
    assert response.status == "completed"
    assert len(growth.tasks()) == 1
    assert response.tool_results[0].tool == "add_plan"


@pytest.mark.parametrize("message", [
    "不要带我学注意力机制", "我已经学完了注意力机制", "记住我喜欢有人带我学",
    "今天添加计划：开始学习注意力机制", "请解释开始学习是什么意思",
    "现在开始", "开始",
])
def test_learning_start_match_does_not_capture_negation_memory_or_unscoped_start(message):
    assert ConversationService._explicit_learning_start(message) is None
