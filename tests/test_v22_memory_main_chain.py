from __future__ import annotations

import json

from modules.intent_router import LLMIntentParser
from v18_test_support import NoCallLLM, build_service


def _tools(response):
    return [(item.tool, item.success) for item in response.tool_results]


def test_explicit_save_typed_read_and_exact_forget_form_one_truthful_chain(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "memory-main-chain"

    saved = service.handle("记住：我喜欢吃西瓜", conversation_id)

    assert saved.status == "completed"
    assert _tools(saved) == [("save_formal_memory", True)]
    assert [item["content"] for item in memory.memories()] == ["我喜欢吃西瓜"]

    read = service.handle("我喜欢吃什么", conversation_id)

    assert read.status == "completed"
    assert _tools(read) == [("list_memories", True)]
    assert "西瓜" in read.message

    preview = service.handle("忘记：我喜欢吃西瓜", conversation_id)

    assert preview.status == "confirmation_required"
    assert len(memory.memories()) == 1
    assert len(preview.tool_results) == 1
    assert preview.tool_results[0].tool == "delete_memory"
    assert preview.tool_results[0].success is False
    assert preview.tool_results[0].error == "confirmation_required"
    pending = service.confirmation_manager.pending(scope=conversation_id)
    assert pending is not None
    assert pending["tool"] == "delete_memory"
    assert pending["arguments"] == {"memory_id": memory.memories()[0]["id"]}

    deleted = service.handle("确认", conversation_id)

    assert deleted.status == "completed"
    assert _tools(deleted) == [("delete_memory", True)]
    assert memory.memories() == []

    missing = service.handle("我喜欢吃什么", conversation_id)

    assert missing.status == "completed"
    assert _tools(missing) == [("list_memories", True)]
    assert "没有" in missing.message or "不记得" in missing.message
    assert "西瓜" not in missing.message


def test_exact_forget_requires_an_identical_formal_memory_body(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    service.handle("记住：我喜欢吃西瓜", "memory-exact-body")

    response = service.handle("忘记：喜欢吃西瓜", "memory-exact-body")

    assert response.status == "failed"
    assert response.tool_results == []
    assert "完全一致" in response.message
    assert [item["content"] for item in memory.memories()] == ["我喜欢吃西瓜"]
    assert service.confirmation_manager.pending(scope="memory-exact-body") is None


def test_cancelling_exact_forget_never_deletes_the_record(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "memory-delete-cancel"
    service.handle("记住：我喜欢吃西瓜", conversation_id)
    preview = service.handle("忘记：我喜欢吃西瓜", conversation_id)
    assert preview.status == "confirmation_required"

    cancelled = service.handle("取消", conversation_id)

    assert cancelled.status == "completed"
    assert "取消" in cancelled.message or "不会执行" in cancelled.message
    assert cancelled.tool_results == []
    assert [item["content"] for item in memory.memories()] == ["我喜欢吃西瓜"]
    assert service.confirmation_manager.pending(scope=conversation_id) is None


def test_changed_exact_forget_target_is_rejected_at_confirmation(tmp_path):
    service, _growth, memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "memory-delete-stale"
    service.handle("记住：我喜欢吃西瓜", conversation_id)
    preview = service.handle("忘记：我喜欢吃西瓜", conversation_id)
    assert preview.status == "confirmation_required"
    memory_id = int(memory.memories()[0]["id"])

    changed = service.memory_service.update_memory(
        memory_id,
        content="我喜欢吃西瓜和草莓",
    )
    assert changed.success is True

    rejected = service.handle("确认", conversation_id)

    assert rejected.status == "failed"
    assert not any(item.success for item in rejected.tool_results)
    assert [item["content"] for item in memory.memories()] == ["我喜欢吃西瓜和草莓"]
    assert service.confirmation_manager.pending(scope=conversation_id) is None


class _ChatDecisionLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append([dict(item) for item in messages])
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
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
                    "modality": "question",
                    "request_mode": "discuss",
                    "explicit_command": False,
                },
                ensure_ascii=False,
            )
        return self.reply


class _AdversarialHelpLLM(_ChatDecisionLLM):
    def chat(self, messages):
        self.calls.append([dict(item) for item in messages])
        prompt = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in prompt:
            return json.dumps(
                {
                    "mode": "write",
                    "intent": "delete_plan",
                    "entities": {"task_ref": "all"},
                    "proposed_tool": "delete_plan",
                    "confidence": 0.99,
                    "follow_up_target": None,
                    "needs_confirmation": True,
                    "warnings": [],
                    "clarification_question": "确认删除吗？",
                    "candidate_actions": [
                        {
                            "tool": "delete_plan",
                            "arguments": {"task_ref": "all"},
                        }
                    ],
                    "subject": "self",
                    "polarity": "positive",
                    "modality": "question",
                    "request_mode": "execute",
                    "explicit_command": True,
                },
                ensure_ascii=False,
            )
        return self.reply


def _chat_service(tmp_path, reply):
    llm = _ChatDecisionLLM(reply)
    service, growth, memory, history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth, memory, history


def test_chat_prompt_receives_current_capability_guide(tmp_path):
    llm = _ChatDecisionLLM("我可以说明当前支持的功能和操作方式。")
    service, _growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False

    response = service.handle("你现在能做什么，怎么操作？", "capability-guide")

    assert response.status == "chat"
    chat_calls = [
        call
        for call in llm.calls
        if "You classify one Chinese user message"
        not in str(call[0].get("content", ""))
    ]
    assert chat_calls
    prompt = "\n".join(str(item.get("content", "")) for item in chat_calls[-1])
    assert "当前 RoxyPlan 功能与操作说明" in prompt
    assert "删除第2条" in prompt
    assert "忘记：完整记忆正文" in prompt
    assert "候选记忆不是可靠聊天能力" in prompt


def test_plan_delete_capability_question_explains_supported_single_item_path(tmp_path):
    service, growth, _memory, _history = _chat_service(
        tmp_path,
        "当然可以，请确认删除计划。",
    )
    growth.add_task("整理简历")

    response = service.handle("我不知道怎么删除计划", "plan-delete-help")

    assert response.status == "chat"
    assert response.tool_results == []
    assert "查看今天计划" in response.message
    assert "删除第2条" in response.message
    assert "逐条处理" in response.message
    assert [item["title"] for item in growth.tasks()] == ["整理简历"]
    assert service.confirmation_manager.pending(scope="plan-delete-help") is None


def test_help_purpose_skips_adversarial_write_classification(tmp_path):
    llm = _AdversarialHelpLLM("当然可以，我现在就删除全部计划。")
    service, growth, _memory, _history = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    growth.add_task("整理简历")

    response = service.handle("计划能删除吗，我应该怎么操作？", "help-no-write")

    assert response.status == "chat"
    assert response.tool_results == []
    assert "查看今天计划" in response.message
    assert "删除第2条" in response.message
    assert [item["title"] for item in growth.tasks()] == ["整理简历"]
    assert service.confirmation_manager.pending(scope="help-no-write") is None
    assert not any(
        "You classify one Chinese user message"
        in str(call[0].get("content", ""))
        for call in llm.calls
    )


def test_chat_cannot_claim_formal_memory_is_empty_without_a_read(tmp_path):
    service, _growth, memory, _history = _chat_service(
        tmp_path,
        "正式长期记忆目前是空的，没有你的昵称或偏好。",
    )
    memory.add_memory("我喜欢吃西瓜", category="preference")

    response = service.handle("你能拿到我的什么信息", "memory-absence-guard")

    assert response.status == "chat"
    assert response.tool_results == []
    assert "不能判断它是否为空" in response.message
    assert "目前是空的" not in response.message


def test_memory_delete_capability_question_explains_exact_supported_entry(tmp_path):
    service, _growth, memory, _history = _chat_service(
        tmp_path,
        "请确认是否删除长期记忆。",
    )
    memory.add_memory("我喜欢吃西瓜", category="preference")

    response = service.handle("怎么删除长期记忆", "memory-delete-help")

    assert response.status == "chat"
    assert response.tool_results == []
    assert "忘记：完整记忆正文" in response.message
    assert "确认" in response.message
    assert [item["content"] for item in memory.memories()] == ["我喜欢吃西瓜"]


def test_memory_forget_help_variant_explains_exact_supported_entry(tmp_path):
    service, _growth, memory, _history = _chat_service(
        tmp_path,
        "如果你确认，我就帮你处理。",
    )
    memory.add_memory("我的称呼是小星", category="identity")

    response = service.handle(
        "如果我想让你忘掉一条正式记忆，要怎么说？",
        "memory-forget-help",
    )

    assert response.status == "chat"
    assert response.tool_results == []
    assert "忘记：完整记忆正文" in response.message
    assert service.confirmation_manager.pending(scope="memory-forget-help") is None
    assert [item["content"] for item in memory.memories()] == ["我的称呼是小星"]


def test_unbound_ordinary_chat_plan_reference_gives_supported_fallback(tmp_path):
    service, growth, _memory, _history = _chat_service(
        tmp_path,
        "好，我帮你加进今日计划了。",
    )

    response = service.handle("把你说的加入今日计划", "ordinary-chat-plan-boundary")

    assert response.status == "chat"
    assert response.tool_results == []
    assert growth.tasks() == []
    assert "普通聊天回复没有建立可执行" in response.message
    assert "今天加入三项计划" in response.message
    assert "给我几项今日计划建议" in response.message
