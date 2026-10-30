"""自然语言明确保存请求的回归。

故障形状来自负责人粘贴的只读日志：用户先说“我想让你记住……”，模型已选出
add_memory_request，但把内层愿望的 modality/request_mode 也套在外层保存命令上，
本地以 explicit_command_requires_execute_mode 拒绝，随后只回普通聊天并要求换用
“记住：”口令。这里用同结构的合成语义决定复现，不复制私人对话正文。

所有服务、历史与记忆都位于 pytest 临时目录，不联网、不启动桌面。
"""

from __future__ import annotations

import json

import pytest

from modules.intent_router import LLMIntentParser
from v18_test_support import build_service


def _memory_decision(content: str, **overrides) -> dict:
    """The model annotation shape observed in the failure (synthetic values)."""
    decision = {
        "mode": "write",
        "intent": "add_memory_request",
        "entities": {"content": content},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.93,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "positive",
        "modality": "desire",
        "request_mode": "possible_action",
        "explicit_command": True,
    }
    decision.update(overrides)
    return decision


class MemoryDecisionLLM:
    """Return one semantic decision; plain prompts get a benign chat reply."""

    def __init__(self, decision: dict, reply: str = "好的。"):
        self.decision = decision
        self.reply = reply
        self.semantic_calls = 0

    def chat(self, messages):
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            self.semantic_calls += 1
            return json.dumps(self.decision, ensure_ascii=False)
        return self.reply


def _parser(decision: dict) -> LLMIntentParser:
    return LLMIntentParser(lambda _messages: json.dumps(decision, ensure_ascii=False))


def _production_service(tmp_path, llm):
    """Match the desktop gateway: model-enabled router, no legacy compat path."""
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def _save_operation(response):
    saves = [item for item in response.tool_results if item.tool == "save_formal_memory"]
    assert len(saves) == 1
    assert saves[0].success is True
    return saves[0].data["memory_operation"]


@pytest.mark.parametrize(
    "user_text,content",
    [
        ("我想让你记住我想成为ai应用工程师", "我想成为ai应用工程师"),
        ("我想让你记住我想把今年英语六级过了", "我想把今年英语六级过了"),
        (
            "我想让你记住我还想今年通过某项考试",
            "我还想今年通过某项考试",
        ),
    ],
)
def test_natural_explicit_save_request_executes_once_and_persists_exact_content(
    tmp_path, user_text, content
):
    llm = MemoryDecisionLLM(_memory_decision(content))
    service, _growth, _memory, history = _production_service(tmp_path, llm)

    response = service.handle(user_text, "natural-memory")

    assert response.status == "completed"
    operation = _save_operation(response)
    assert operation["data"]["operation"] == "created"
    assert operation["data"]["final_content"] == content
    assert operation["memory_id"]
    memories = service.memory_service.list_memories().data["memories"]
    assert len(memories) == 1
    assert memories[0]["id"] == operation["memory_id"]
    assert memories[0]["content"] == content
    assert memories[0]["category"] == operation["data"]["category"]
    assert memories[0]["category"]
    # No fallback instruction to repeat a fixed “记住：” passphrase.
    assert "记住：" not in response.message
    assert "记住了" in response.message
    # The final assistant message in history is the same verified reply.
    session = history.get_session("natural-memory")
    assistant = [item for item in session["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["content"] == response.message


def test_explicit_memory_desire_is_normalized_with_observable_diagnostic():
    parser = _parser(_memory_decision("我想成为ai应用工程师"))

    parsed = parser.parse("我想让你记住我想成为ai应用工程师")

    assert parsed is not None
    assert parsed["intent"] == "add_memory_request"
    assert parsed["mode"] == "write"
    assert parsed["request_mode"] == "execute"
    assert parsed["explicit_command"] is True
    # The saved content keeps its desire modality; only the outer command is
    # normalized to an executable request.
    assert parsed["modality"] == "desire"
    assert "explicit_memory_request_mode_repaired" in str(
        parsed["semantic_diagnostic"]
    )


def test_explicit_memory_request_without_modality_is_also_normalized():
    parser = _parser(_memory_decision("我想把今年英语六级过了", modality=""))

    parsed = parser.parse("我想让你记住我想把今年英语六级过了")

    assert parsed is not None
    assert parsed["request_mode"] == "execute"
    assert parsed["explicit_command"] is True


def test_implicit_memory_desire_is_not_forced_to_execute():
    parser = _parser(
        _memory_decision(
            "我喜欢早上学习",
            modality="desire",
            request_mode="possible_action",
            explicit_command=False,
        )
    )

    parsed = parser.parse("我喜欢早上学习")

    assert parsed is not None
    assert parsed["request_mode"] == "possible_action"
    assert parsed["explicit_command"] is False


@pytest.mark.parametrize(
    "annotations",
    [
        {"subject": "other"},
        {"polarity": "negative"},
        {"modality": "hypothetical"},
        {"modality": "question"},
    ],
    ids=["other-person", "negated", "hypothetical", "question"],
)
def test_non_explicit_or_non_first_person_shapes_are_still_rejected(annotations):
    parser = _parser(_memory_decision("我想成为ai应用工程师", **annotations))

    parsed = parser.parse("如果你愿意，也许可以记住我想成为ai应用工程师")

    assert parsed is None
    assert "explicit_command_requires_execute_mode" in str(parser.last_diagnostic)


def test_plan_desire_with_explicit_flag_is_not_repaired_by_memory_rule():
    parser = _parser(
        {
            "mode": "write",
            "intent": "add_plan",
            "entities": {"title": "成为ai应用工程师"},
            "proposed_tool": "add_plan",
            "confidence": 0.9,
            "follow_up_target": None,
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
            "subject": "self",
            "polarity": "positive",
            "modality": "desire",
            "request_mode": "possible_action",
            "explicit_command": True,
        }
    )

    assert parser.parse("我想让你记住我想成为ai应用工程师") is None
    assert "explicit_command_requires_execute_mode" in str(parser.last_diagnostic)


@pytest.mark.parametrize(
    "decision",
    [
        _memory_decision("我想成为ai应用工程师", subject="other"),
        _memory_decision("我想成为ai应用工程师", polarity="negative"),
        _memory_decision("我想成为ai应用工程师", modality="hypothetical"),
        _memory_decision("我想成为ai应用工程师", modality="question"),
    ],
    ids=["other-person", "negated", "hypothetical", "question"],
)
def test_rejected_shapes_never_write_and_never_claim_success(tmp_path, decision):
    llm = MemoryDecisionLLM(decision, reply="好的，我已经记住并保存了。")
    service, _growth, _memory, _history = _production_service(tmp_path, llm)

    response = service.handle("我想让你记住我想成为ai应用工程师", "rejected-memory")

    assert response.tool_results == []
    assert service.memory_service.list_memories().data["memories"] == []
    assert "保存" not in response.message
    assert "已经记住" not in response.message
    assert "记住：" not in response.message


def test_method_question_does_not_authorize_or_claim_a_save(tmp_path):
    decision = _memory_decision(
        "我想成为ai应用工程师",
        modality="question",
        request_mode="discuss",
        explicit_command=False,
    )
    llm = MemoryDecisionLLM(decision, reply="好的，我已经记住并保存了。")
    service, _growth, _memory, _history = _production_service(tmp_path, llm)

    response = service.handle(
        "怎么才能让你记住我想成为ai应用工程师？", "method-question"
    )

    assert response.tool_results == []
    assert service.memory_service.list_memories().data["memories"] == []
    assert "已经记住" not in response.message
    assert "保存了" not in response.message


def test_missing_content_and_unresolved_reference_are_not_saved(tmp_path):
    for index, entities in enumerate(({}, {"content": "previous_assistant_message"})):
        llm = MemoryDecisionLLM(_memory_decision("", entities=entities))
        service, _growth, _memory, _history = _production_service(
            tmp_path / f"case-{index}", llm
        )

        response = service.handle("我想让你记住刚才说的", f"missing-{index}")

        assert response.tool_results == []
        assert service.memory_service.list_memories().data["memories"] == []
        assert response.status in {"clarification", "chat"}


def test_saved_memory_is_not_denied_or_rewritten_by_later_chat(tmp_path):
    decision = _memory_decision("我想成为ai应用工程师")
    llm = MemoryDecisionLLM(decision)
    service, _growth, _memory, _history = _production_service(tmp_path, llm)
    first = service.handle("我想让你记住我想成为ai应用工程师", "keep-memory")
    memory_id = _save_operation(first)["memory_id"]

    llm.decision = _memory_decision(
        "我想成为ai应用工程师", polarity="negative", modality="question"
    )
    denied = service.handle(
        "我从来没说过想成为ai应用工程师，你别记错", "keep-memory"
    )
    assert denied.tool_results == []
    record = service.memory_service.get_memory(memory_id).data["memory"]
    assert record["content"] == "我想成为ai应用工程师"
    assert record["status"] == "active"

    llm.decision = {
        "mode": "chat",
        "intent": "chat",
        "entities": {},
        "proposed_tool": None,
        "confidence": 0.99,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": "self",
        "polarity": "negative",
        "modality": "question",
        "request_mode": "discuss",
        "explicit_command": False,
    }
    chat = service.handle("其实我还没想好要不要成为ai应用工程师", "keep-memory")
    assert chat.tool_results == []
    record = service.memory_service.get_memory(memory_id).data["memory"]
    assert record["content"] == "我想成为ai应用工程师"
    assert record["status"] == "active"


def test_natural_request_duplicate_and_conflict_keep_existing_formal_memory_behavior(
    tmp_path,
):
    decision = _memory_decision("我更适合早上学习")
    llm = MemoryDecisionLLM(decision)
    service, _growth, _memory, _history = _production_service(tmp_path, llm)
    first = service.handle("记住：我更适合早上学习", "conflict-memory")
    assert _save_operation(first)["data"]["operation"] == "created"

    duplicate = service.handle("我想让你记住我更适合早上学习", "conflict-memory")
    assert _save_operation(duplicate)["data"]["operation"] == "duplicate"

    llm.decision = _memory_decision("我现在更适合晚上学习")
    conflict = service.handle("我想让你记住我现在更适合晚上学习", "conflict-memory")
    assert _save_operation(conflict)["data"]["operation"] == "updated"
    memories = service.memory_service.list_memories().data["memories"]
    assert [item["content"] for item in memories] == ["我现在更适合晚上学习"]
