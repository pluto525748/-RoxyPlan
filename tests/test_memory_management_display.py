"""Copyable management reads stay distinct from natural memory conversation."""
from __future__ import annotations

import json

import pytest

from modules.memory_read import is_memory_management_display
from v18_test_support import RecordingLLM, build_service


def _seed(memory, entries):
    records = [
        {
            "id": memory_id,
            "uid": f"memory_management_fixture_{memory_id}",
            "content": content,
            "category": "preference",
            "status": "active",
            "importance": 3,
            "created_at": "2026-10-01T09:00:00",
            "updated_at": "2026-10-01T09:00:00",
            "use_count": 0,
        }
        for memory_id, content in entries
    ]
    assert memory.repository.save_memories(
        {"version": 2, "profile": {}, "memories": records}
    )


@pytest.mark.parametrize(
    "user_text",
    ["展示长期记忆", "查看长期记忆", "查看记忆", "列出长期记忆", "请展示一下我的长期记忆。"],
)
def test_management_aliases_show_record_ids_and_copyable_originals_without_llm(
    tmp_path, user_text,
):
    llm = RecordingLLM("模型不应参与管理原文展示")
    service, _growth, memory, _history = build_service(tmp_path, llm)
    entries = [(3, "我喜欢吃梨"), (9, "我希望学习时先听例子。\n再自己做练习。")]
    _seed(memory, entries)
    before = (tmp_path / "memory.json").read_bytes()

    response = service.handle(user_text, "copyable-memory-management")

    assert [(item.tool, item.success) for item in response.tool_results] == [("list_memories", True)]
    assert llm.calls == []
    for memory_id, content in entries:
        assert f"记录编号 {memory_id}：\n{content}" in response.message
    assert "你喜欢吃梨" not in response.message
    assert "忘记：完整记忆正文" in response.message
    assert "不要带编号" in response.message
    assert "不支持按编号删除" in response.message
    assert (tmp_path / "memory.json").read_bytes() == before


def test_management_display_does_not_deduplicate_or_truncate(tmp_path):
    llm = RecordingLLM()
    service, _growth, memory, _history = build_service(tmp_path, llm)
    entries = [(number * 2, f"我喜欢第{number}种阅读练习") for number in range(1, 8)]
    entries.append((19, entries[0][1]))
    _seed(memory, entries)

    response = service.handle("展示长期记忆", "all-original-records")

    assert llm.calls == []
    for memory_id, content in entries:
        assert f"记录编号 {memory_id}：\n{content}" in response.message
    assert response.message.count(entries[0][1]) == 2


def test_empty_management_display_is_truthful_and_does_not_call_llm(tmp_path):
    llm = RecordingLLM("我记得你喜欢吃梨")
    service, _growth, memory, _history = build_service(tmp_path, llm)
    _seed(memory, [])

    response = service.handle("展示长期记忆", "empty-management-display")

    assert [(item.tool, item.success) for item in response.tool_results] == [("list_memories", True)]
    assert "没有可展示" in response.message
    assert "吃梨" not in response.message
    assert llm.calls == []


def test_management_display_read_failure_is_not_an_empty_memory_claim(tmp_path, monkeypatch):
    llm = RecordingLLM()
    service, _growth, _memory, _history = build_service(tmp_path, llm)
    memory_service = service.memory_service
    monkeypatch.setattr(
        memory_service, "read_typed_memory",
        lambda **kwargs: memory_service._failed(
            "list_memories", "memory_read_failed", "长期记忆暂时无法读取。",
        ),
    )

    response = service.handle("展示长期记忆", "failed-management-display")

    assert response.tool_results and not any(item.success for item in response.tool_results)
    assert "没有可展示" not in response.message
    assert "无法读取" in response.message or "读取失败" in response.message
    assert llm.calls == []


def test_natural_memory_question_still_uses_verified_facts_with_llm(tmp_path):
    llm = RecordingLLM(json.dumps(
        {"answer": "我记得你喜欢吃梨。", "used_memory_ids": [3]}, ensure_ascii=False,
    ))
    service, _growth, memory, _history = build_service(tmp_path, llm)
    _seed(memory, [(3, "我喜欢吃梨")])

    response = service.handle("你知道我什么", "natural-memory-chat")

    assert llm.calls
    assert "我记得你喜欢吃梨" in response.message
    assert "记录编号" not in response.message
    assert "忘记：" not in response.message


def test_displayed_original_can_be_copied_into_existing_confirmed_delete_path(tmp_path):
    llm = RecordingLLM()
    service, _growth, memory, _history = build_service(tmp_path, llm)
    _seed(memory, [(3, "我喜欢吃梨"), (9, "我习惯晚上阅读")])
    conversation_id = "display-then-exact-forget"

    displayed = service.handle("查看记忆", conversation_id)
    assert "记录编号 3：\n我喜欢吃梨" in displayed.message

    pending = service.handle("忘记：我喜欢吃梨", conversation_id)
    assert pending.status == "confirmation_required"
    assert len(memory.memories()) == 2
    service.handle("取消", conversation_id)
    assert len(memory.memories()) == 2

    pending = service.handle("忘记：我喜欢吃梨", conversation_id)
    assert pending.status == "confirmation_required"
    deleted = service.handle("确认", conversation_id)
    assert [(item.tool, item.success) for item in deleted.tool_results] == [("delete_memory", True)]
    assert [item["content"] for item in memory.memories()] == ["我习惯晚上阅读"]
    assert llm.calls == []


@pytest.mark.parametrize(
    "user_text",
    ["你知道我什么", "怎么展示长期记忆", "不要展示长期记忆", "展示长期记忆的原理", "查看候选记忆"],
)
def test_management_display_grammar_does_not_take_over_other_intents(user_text):
    assert not is_memory_management_display(user_text)
