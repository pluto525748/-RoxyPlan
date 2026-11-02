"""Real routed client + DeepSeek response parsing, with offline HTTP transport."""
import json
from copy import deepcopy

import pytest

from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.routed_client import RoutedLLMClient
from v18_test_support import build_service


THIRD_PARTY_RAW = {
    "mode": "write", "intent": "add_action_log",
    "entities": {"content": "室友今天跑了10公里", "duration_minutes": None, "date": "今天"},
    "proposed_tool": "add_action_log", "confidence": 0.82,
    "follow_up_target": "", "needs_confirmation": True,
    "warnings": ["记录对象是室友而非用户本人，属于代他人记录",
                 "动作日志默认记录用户自身行为，需用户确认是否记录他人行为"],
    "clarification_question": "", "candidate_actions": [],
    "subject": "other", "polarity": "positive", "modality": "desire",
    "request_mode": "possible_action", "explicit_command": False,
}


class CapturingTransport:
    def __init__(self, decision):
        self.decision = decision
        self.payloads = []
        self.raw_responses = []

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and url.startswith("https://example.invalid/")
        self.payloads.append(deepcopy(payload))
        is_semantic = any("You classify one Chinese user message" in m["content"]
                          for m in payload["messages"])
        # A bare confirmation with no valid pending must not create another offer.
        current = payload["messages"][-1]["content"]
        decision = self.decision if current != "确认" else {
            **self.decision, "mode": "chat", "intent": "chat", "entities": {},
            "proposed_tool": None, "needs_confirmation": False,
            "subject": "", "modality": "question", "request_mode": "discuss",
            "explicit_command": False,
        }
        content = json.dumps(decision, ensure_ascii=False) if is_semantic else "我们可以继续聊。"
        response = {"model": "isolated-deepseek-transport", "choices": [
            {"finish_reason": "stop", "message": {"content": content, "tool_calls": []}}
        ], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        self.raw_responses.append(deepcopy(response))
        return response


def routed_service(root, monkeypatch, decision):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "isolated-synthetic-key")
    transport = CapturingTransport(decision)
    provider = DeepSeekProvider(api_key="isolated-synthetic-key", base_url="https://example.invalid",
                                model_name="isolated-deepseek-transport", transport=transport)
    client = RoutedLLMClient(root, settings={
        "online_enabled": True, "offline_fallback_enabled": False, "model_mode": "economy",
        "default_model": "isolated-deepseek-transport", "enable_auto_escalation": False,
    }, deepseek_provider=provider)
    service, growth, _, _ = build_service(root, client)
    assert service.conversation_service.semantic_decision_compatibility_enabled is False
    return service, growth, transport


def test_original_third_party_raw_cannot_create_offer_or_write_after_confirmation(tmp_path, monkeypatch):
    service, growth, transport = routed_service(tmp_path, monkeypatch, deepcopy(THIRD_PARTY_RAW))
    response = service.handle("我室友说他今天跑了10公里，我觉得应该帮他记录一下", "third-party")
    pending = service.interaction_coordinator.current("third-party").to_dict()
    before_confirm = deepcopy(service.plan_service.action_records())
    confirmed = service.handle("确认", "third-party")

    raw = json.loads(transport.raw_responses[0]["choices"][0]["message"]["content"])
    assert raw == THIRD_PARTY_RAW
    assert service.plan_service.action_records() == [], (pending, confirmed.to_dict())
    assert before_confirm == []
    assert pending["state"] == "cancelled"
    assert response.tool_results == confirmed.tool_results == []
    assert service.confirmation_manager.pending(scope="third-party") is None


@pytest.mark.parametrize("content,text", [
    ("同事今天练了20分钟钢琴", "同事练了20分钟钢琴，想帮忙记录这段进展"),
    ("朋友今天读完了一章", "朋友今天读完了一章，我想帮他记一笔"),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_observed_other_subject_blocks_both_offer_and_direct_write(tmp_path, monkeypatch, content, text, explicit):
    decision = deepcopy(THIRD_PARTY_RAW)
    decision.update(explicit_command=explicit, needs_confirmation=not explicit,
                    request_mode="execute" if explicit else "possible_action")
    decision["entities"]["content"] = content
    service, _, transport = routed_service(tmp_path, monkeypatch, decision)
    first = service.handle(text, "other")
    second = service.handle("确认", "other")
    assert transport.payloads
    assert service.plan_service.action_records() == []
    assert first.tool_results == second.tool_results == []
    assert service.interaction_coordinator.current("other").state != "awaiting_confirmation"


@pytest.mark.parametrize("subject", ["self", ""])
def test_self_or_unspecified_subject_keeps_existing_confirmed_action_path(tmp_path, monkeypatch, subject):
    decision = deepcopy(THIRD_PARTY_RAW)
    decision.update(subject=subject, warnings=[])
    decision["entities"]["content"] = "今天跑了10公里"
    service, _, transport = routed_service(tmp_path, monkeypatch, decision)
    first = service.handle("今天跑了10公里，想把这段进展记一下", "own")
    assert first.tool_results == []
    assert service.plan_service.action_records() == []
    assert service.interaction_coordinator.current("own").state == "awaiting_confirmation"
    assert service.interaction_coordinator.current("own").known_fields.get("subject", "") == subject
    confirmed = service.handle("确认", "own")
    assert [(r.tool, r.success) for r in confirmed.tool_results] == [("add_action_log", True)]
    records = service.plan_service.action_records()
    assert len(records) == 1 and records[0]["content"] == "今天跑了10公里"
    assert records[0]["source"] == "chat"
    assert len(transport.payloads) == 1  # confirmation consumes the bound local operation


def test_confirmation_preserves_bound_other_subject_and_cancels_forbidden_offer(tmp_path, monkeypatch):
    service, _, transport = routed_service(tmp_path, monkeypatch, deepcopy(THIRD_PARTY_RAW))
    # A pre-existing runtime pending retains its recorded subject; confirmation
    # is consent to an operation, never evidence that the actor became self.
    service.interaction_coordinator.awaiting_confirmation(
        "bound-other", "action_log_offer", domain="action_log",
        immutable_arguments={"tool_name": "add_action_log", "content": "同事今天练了钢琴"},
        known_fields={"subject": "other"}, original_user_text="同事练了钢琴，想帮忙记录",
    )
    confirmed = service.handle("确认", "bound-other")
    assert confirmed.tool_results == []
    assert service.plan_service.action_records() == []
    assert service.interaction_coordinator.current("bound-other").state == "cancelled"
    assert transport.payloads == []
