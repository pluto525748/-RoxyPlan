import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.local_feature_extractor import LocalFeatureExtractor
from modules.intent_router import LLMIntentParser
from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from tests.semantic_contract_fixtures import build_current_semantic_payload
from v18_test_support import RecordingLLM, build_service


class StubSemanticDecisionParser:
    """Offline decision double; downstream services remain real."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.feature_extractor = LocalFeatureExtractor()
        self.calls = []

    def parse_unified(
        self,
        text,
        context,
        *,
        allow_llm,
        allow_legacy_fallback=False,
    ):
        del context, allow_llm, allow_legacy_fallback
        self.calls.append(text)
        decision = dict(self.decisions.get(text, {"intent": "chat"}))
        tool_calls = list(decision.pop("tool_calls", []))
        candidates = [
            ActionCandidate(
                domain=str(item["domain"]),
                tool_name=str(item["name"]),
                arguments=dict(item.get("arguments", {})),
                request_mode=str(item.get("request_mode", "execute")),
                confidence=float(decision.get("confidence", 0.95)),
                source_intent=str(decision.get("intent", "chat")),
                sequence_index=index,
            )
            for index, item in enumerate(tool_calls)
        ]
        return SemanticParseResult(
            source="stub_semantic_decision",
            candidates=candidates,
            request_mode="execute" if candidates else "discuss",
            intent_result={
                "intent": str(decision.get("intent", "chat")),
                "confidence": float(decision.get("confidence", 0.95)),
                "source": "llm",
                "entities": {},
                "candidate_actions": [],
                "clarification_question": decision.get("clarification_question"),
                "follow_up_target": decision.get("follow_up_target"),
            },
            local_features=self.feature_extractor.extract(text),
        )


class RawDesktopDecisionLLM:
    """DeepSeek-shaped raw JSON first, normal assistant content second."""

    def __init__(self, decision, reply="这是正常聊天回复。"):
        self.decision = dict(decision)
        self.reply = reply
        self.semantic_calls = 0
        self.reply_calls = 0

    def chat(self, messages):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls += 1
            return json.dumps(self.decision, ensure_ascii=False)
        self.reply_calls += 1
        return self.reply


def _desktop_runtime_with_raw_decision(tmp_path, decision, reply="这是正常聊天回复。"):
    payload = build_current_semantic_payload(
        decision,
        case_id="raw-desktop-semantic-decision",
    )
    llm = RawDesktopDecisionLLM(payload, reply=reply)
    service, growth, memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, llm


def _complete_desktop_turn(service, text, conversation_id):
    turn = service.conversation_service.prepare(
        text,
        conversation_id,
        record_history=False,
        allow_llm_intent=False,
    )
    assert turn.resolve_intent_in_worker is True
    return service.conversation_service.complete(turn)


def _service_with_decisions(tmp_path, decisions):
    service, growth, memory, _history = build_service(tmp_path, RecordingLLM("自然回复。"))
    parser = StubSemanticDecisionParser(decisions)
    service.conversation_service.semantic_action_parser = parser
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, parser


def test_chat_only_does_not_execute_tools_or_reclassify_plan_words(tmp_path):
    service, growth, _memory, parser = _service_with_decisions(
        tmp_path,
        {
            "我怕我没有钱": {"intent": "chat"},
            "我正在做一个 AI 陪伴计划桌宠": {"intent": "chat"},
            "我喜欢看你跳舞": {"intent": "chat"},
            "逻辑回归是什么": {"intent": "chat"},
        },
    )

    for index, text in enumerate(parser.decisions):
        response = service.handle(text, f"chat-only-{index}")
        assert response.status == "chat"
        assert response.tool_results == []
    assert growth.tasks() == []
    assert parser.calls == list(parser.decisions)


def test_read_decision_uses_show_plan_and_renders_facts(tmp_path):
    service, growth, _memory, _parser = _service_with_decisions(
        tmp_path,
        {
            "今天还有什么计划": {
                "intent": "show_plan",
                "tool_calls": [{"domain": "plan", "name": "show_plan"}],
            }
        },
    )
    growth.add_task("今晚学习机器学习")

    response = service.handle("今天还有什么计划", "read-plan")

    assert [item.tool for item in response.tool_results] == ["show_plan"]
    assert "今晚学习机器学习" in response.message


def test_explicit_goal_save_uses_one_formal_memory_tool(tmp_path):
    text = "完成洛琪希项目是我的长期目标"
    service, _growth, memory, _parser = _service_with_decisions(
        tmp_path,
        {
            text: {
                "intent": "add_memory_request",
                "tool_calls": [
                    {
                        "domain": "memory",
                        "name": "save_formal_memory",
                        "arguments": {"content": text, "category": "goal"},
                    }
                ],
            }
        },
    )

    response = service.handle(text, "goal-save")

    assert [item.tool for item in response.tool_results] == ["save_formal_memory"]
    assert response.tool_results[0].success is True
    assert memory.memories()[0]["content"] == text


def test_previous_user_reference_saves_formal_memory_not_candidate(tmp_path):
    first = "我想把洛琪希计划做得更可靠"
    reference = "把刚才说的加入长期记忆"
    service, _growth, memory, _parser = _service_with_decisions(
        tmp_path,
        {
            first: {"intent": "chat"},
            reference: {
                "intent": "add_memory_request",
                "tool_calls": [
                    {
                        "domain": "memory",
                        "name": "save_formal_memory",
                        "arguments": {"content": "previous_user_message"},
                    }
                ],
            },
        },
    )

    service.handle(first, "same-window")
    response = service.handle(reference, "same-window")

    assert [item.tool for item in response.tool_results] == ["save_formal_memory"]
    assert response.tool_results[0].success is True
    assert memory.memories()[0]["content"] == first


def test_exact_dance_bypasses_semantic_model_decision(tmp_path):
    service, _growth, _memory, parser = _service_with_decisions(tmp_path, {})
    service.conversation_service.semantic_action_parser = __import__(
        "modules.semantic_action_parser", fromlist=["SemanticActionParser"]
    ).SemanticActionParser(service.intent_router)

    response = service.handle("跳舞", "exact-dance")

    assert [item.tool for item in response.tool_results] == ["play_dance"]
    assert parser.calls == []


def test_chat_decision_never_reopens_model_tool_fallback(tmp_path):
    service, _growth, _memory, _parser = _service_with_decisions(
        tmp_path, {"你好": {"intent": "chat"}}
    )

    class FailingToolLoop:
        def complete(self, **_kwargs):
            raise AssertionError("a semantic chat decision must not reopen tool routing")

    service.conversation_service.model_action_adapter = FailingToolLoop()
    response = service.handle("你好", "one-decision")

    assert response.status == "chat"
    assert response.tool_results == []


@pytest.mark.parametrize("text", ["你好", "我怕我没有钱", "逻辑回归是什么"])
def test_raw_deepseek_chat_with_null_clarification_reaches_real_reply(
    tmp_path, text, capsys
):
    service, _growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "chat",
            "intent": "chat",
            "entities": {},
            "proposed_tool": None,
            "confidence": 0.97,
            "follow_up_target": None,
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
            "subject": "",
            "polarity": "",
            "modality": "",
            "request_mode": "discuss",
            "explicit_command": False,
        },
    )

    response = _complete_desktop_turn(service, text, f"raw-chat-{text}")

    assert response.status == "chat"
    assert response.message == "这是正常聊天回复。"
    assert response.tool_results == []
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 1
    state = service.interaction_coordinator.current(response.conversation_id)
    assert state.state != "awaiting_clarification"
    output = capsys.readouterr().out
    assert "source=unavailable" not in output
    assert output.count("source=llm intent=chat") == 1


def test_raw_chat_cannot_become_clarification_without_a_question(tmp_path):
    service, _growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "clarify",
            "intent": "chat",
            "entities": {},
            "proposed_tool": None,
            "confidence": 0.9,
            "follow_up_target": None,
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
            "subject": "",
            "polarity": "",
            "modality": "",
            "request_mode": "possible_action",
            "explicit_command": False,
        },
    )

    response = _complete_desktop_turn(service, "你好", "raw-chat-clarify")

    assert response.status == "chat"
    assert response.tool_results == []
    assert llm.semantic_calls == 1
    assert service.interaction_coordinator.current(
        response.conversation_id
    ).state != "awaiting_clarification"


def test_invalid_clarify_read_decision_is_rejected_without_executing_tool(tmp_path):
    service, _growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "clarify",
            "intent": "show_plan",
            "entities": {},
            "proposed_tool": "show_plan",
            "confidence": 0.71,
            "follow_up_target": None,
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
            "subject": "self",
            "polarity": "positive",
            "modality": "question",
            "request_mode": "possible_action",
            "explicit_command": False,
        },
    )

    response = _complete_desktop_turn(
        service, "我怕我没有钱", "invalid-read-clarify"
    )

    assert response.status == "chat"
    assert response.tool_results == []
    assert llm.semantic_calls == 1
    assert llm.reply_calls == 1
    assert service.interaction_coordinator.current(
        response.conversation_id
    ).state != "awaiting_clarification"


def test_management_read_guard_executes_show_plan_without_semantic_llm(tmp_path):
    service, growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "read",
            "intent": "show_plan",
            "entities": {},
            "proposed_tool": "show_plan",
            "confidence": 0.98,
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
    )
    growth.add_task("复习逻辑回归")

    response = _complete_desktop_turn(
        service, "今天还有什么计划", "raw-read-plan"
    )

    assert [item.tool for item in response.tool_results] == ["show_plan"]
    assert "复习逻辑回归" in response.message
    assert llm.semantic_calls == 0
    assert llm.reply_calls == 0


def test_plan_write_with_review_word_is_not_hijacked_by_review_read_guard(tmp_path):
    service, growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "write",
            "intent": "add_plan",
            "entities": {"title": "复盘修复验收"},
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
        },
    )

    response = _complete_desktop_turn(
        service,
        "今天加一项计划：复盘修复验收",
        "plan-title-with-review-word",
    )

    assert response.status == "completed"
    assert [(item.tool, item.success) for item in response.tool_results] == [
        ("add_plan", True)
    ]
    assert [item["title"] for item in growth.tasks()] == ["复盘修复验收"]
    assert llm.semantic_calls == 1


def test_semantic_schema_failure_logs_reason_without_user_text(capsys):
    parser = LLMIntentParser(lambda _messages: "not-json")

    assert parser.parse("这是不应出现在诊断日志里的私密内容") is None

    output = capsys.readouterr().out
    assert "reason=json_parse_error" in output
    assert "私密内容" not in output
