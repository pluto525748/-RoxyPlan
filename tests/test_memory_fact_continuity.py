"""Synthetic, Qt-free coverage of formal-read -> ordinary reply continuity."""
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

from v18_test_support import build_service


class ContinuityLLM:
    def __init__(self, reply="我这边没有关于你的正式记忆，刚才只是随口编的。"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if any("已核验正式记忆 JSON：" in m["content"] for m in messages):
            return json.dumps({"answer": "我记得你喜欢拼图。", "used_memory_ids": [1]}, ensure_ascii=False)
        return self.reply


def desktop_memory_provider(memory):
    """Execute the actual pure formatter without importing Qt or creating a window."""
    path = Path(__file__).resolve().parents[1] / "frontend/pet_app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChatWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "build_memory_context")
    namespace = {"Optional": Optional, "List": List, "Dict": Dict}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return lambda memories: namespace[method.name](SimpleNamespace(memory_manager=memory), memories)


def seed_service(tmp_path, *, desktop=False, reply="我听到了，我们可以继续聊。"):
    llm = ContinuityLLM(reply)
    service, _, memory, _ = build_service(tmp_path, llm)
    memory.add_memory("我喜欢拼图", category="preference", source="explicit_user_command")
    if desktop:
        service.conversation_service.memory_context_provider = desktop_memory_provider(memory)
    return service, memory, llm


def verified_facts(messages):
    for message in messages:
        for line in message["content"].splitlines():
            if line.startswith('{"schema_version":'):
                return json.loads(line)
    raise AssertionError("missing required verified-turn context")


def test_read_then_unrelated_challenge_keeps_verified_formal_fact(tmp_path):
    llm = ContinuityLLM()
    service, _, memory, _ = build_service(tmp_path, llm)
    memory.add_memory("我喜欢拼图", category="preference", source="explicit_user_command")
    first = service.handle("你记得我什么", "continuity")
    assert first.message == "我记得你喜欢拼图。"
    assert [r.tool for r in first.tool_results] == ["list_memories"]
    core = service.conversation_service
    refs = core.state_manager.reference_context("continuity")
    assert refs["prior_memory_facts"], first.tool_results[0].data
    facts, operation = core._verified_recent_memory_context("continuity", refs, "真的吗")
    assert [f["content"] for f in facts] == ["我喜欢拼图"], refs
    second = service.handle("你这回答有点奇怪", "continuity")
    assert not second.tool_results
    prompt = "\n".join(m["content"] for m in llm.calls[-1] if m["role"] == "system")
    assert '"prior_memory_facts":[{"content":"我喜欢拼图"' in prompt
    assert "随口编" not in second.message
    assert "没有关于你的正式记忆" not in second.message
    assert "仍有有效记录" in second.message
    assert "拼图" not in second.message  # no unsolicited personal body in the correction
    third = service.handle("你说的和刚才不一样", "continuity")
    assert not third.tool_results
    assert "随口编" not in third.message
    assert "仍有有效记录" in third.message


@pytest.mark.parametrize("desktop", [False, True], ids=["server", "desktop-formatter"])
def test_unrelated_reply_is_unchanged_and_prompt_does_not_assert_empty_store(tmp_path, desktop):
    service, memory, llm = seed_service(tmp_path, desktop=desktop)
    service.handle("你记得我什么", "ordinary")
    second = service.handle("今天的云很好看", "ordinary")
    prompt = "\n".join(m["content"] for m in llm.calls[-1] if m["role"] == "system")
    assert second.message == llm.reply  # ordinary unrelated chat need not repeat a fact
    assert "记忆内容：暂无" not in prompt
    assert "本轮未召回" in prompt
    assert "不代表全库" in prompt


@pytest.mark.parametrize("raw", [
    "我没有关于你的正式记忆。",
    "刚才确实是随口编的。我这边没有存下关于你的正式记忆。",
    "我的正式长期记忆是空的。",
    "我还没形成任何正式记忆。",
    "我没有保存过你的长期记忆。",
    "“我没有关于你的正式记忆。”",
])
def test_absence_variants_cannot_overturn_live_formal_fact(tmp_path, raw):
    service, _, llm = seed_service(tmp_path, reply=raw)
    service.handle("你记得我什么", "raw-replay")
    response = service.handle("你这回答有点奇怪", "raw-replay")
    assert llm.calls  # the recorded raw response is produced by the reply stage
    assert response.message != raw
    assert "仍有有效记录" in response.message
    assert not response.tool_results


@pytest.mark.parametrize("raw", [
    "不能说我没有关于你的正式记忆。",
    "你说“没有正式记忆”，但记录仍然存在。",
    "我没有删除你的正式记忆。",
    "你现在不喜欢拼图，旧正式记忆还没有更新。",
    "刚才那道算术题我讲错了。",
    "我还没有保存你这次更正到正式记忆。",
    "正式记忆里没有你现在的偏好。",
    "刚才措辞有点生硬，对不起。",
])
def test_no_false_block_for_quote_negation_correction_or_unrelated_error(tmp_path, raw):
    service, _, _ = seed_service(tmp_path, reply=raw)
    service.handle("你记得我什么", "counterexample")
    response = service.handle("我现在不喜欢拼图了", "counterexample")
    assert response.message == raw
    assert not response.tool_results


@pytest.mark.parametrize("desktop", [False, True])
def test_empty_subset_does_not_assert_empty_store_or_inject_every_record(tmp_path, desktop):
    service, memory, _ = seed_service(tmp_path)
    memory.add_memory("我住在合成城", category="other")
    provider = desktop_memory_provider(memory) if desktop else service._build_memory_context
    text = provider([])
    assert "记忆内容：暂无" not in text
    assert "本轮未召回" in text
    assert "不代表全库" in text
    assert "我喜欢拼图" not in text
    assert "合成城" not in text


def test_current_correction_has_priority_without_rewriting_formal_record(tmp_path):
    service, memory, llm = seed_service(tmp_path, reply="明白，你现在不喜欢拼图了。")
    service.handle("你记得我什么", "correction")
    before = memory.memory_file.read_bytes()
    response = service.handle("我现在不喜欢拼图了，之前的偏好变了", "correction")
    assert response.message == llm.reply
    assert not response.tool_results
    assert memory.memory_file.read_bytes() == before
    prompt = "\n".join(m["content"] for m in llm.calls[-1] if m["role"] == "system")
    assert "用户陈述和历史对话" not in prompt
    assert "用户本轮明确更正" in prompt


@pytest.mark.parametrize("mutation", ["delete", "archive", "update", "expire", "conflict"])
def test_live_change_removes_cached_fact_from_next_model_evidence(tmp_path, mutation):
    service, memory, llm = seed_service(tmp_path)
    service.handle("你记得我什么", "changed")
    if mutation == "delete":
        result = service.memory_service.delete_memory(1)
    elif mutation == "archive":
        result = service.memory_service.archive_memory(1)
    elif mutation == "update":
        result = service.memory_service.update_memory(1, content="我现在喜欢陶艺")
    elif mutation == "expire":
        assert memory.update_memory(1, valid_until="2000-01-01T00:00:00+00:00")
        result = None
    else:
        conflict = memory.create_relation_conflict(memory.memories()[0], "我不喜欢拼图", "preference")
        assert conflict["old_memory_id"] == 1
        result = None
    assert result is None or result.success
    before = memory.memory_file.read_bytes()
    response = service.handle("继续聊聊吧", "changed")
    assert not response.tool_results
    assert verified_facts(llm.calls[-1])["prior_memory_facts"] == []
    assert memory.memory_file.read_bytes() == before
    # A replayed absence must not use an invalidated cache to claim active facts.
    llm.reply = "我没有关于你的正式记忆。"
    corrected = service.handle("你刚才的说法不对吧", "changed")
    assert "仍有有效记录" not in corrected.message


def test_cache_expires_and_does_not_cross_day_session_or_instance(tmp_path):
    service, memory, llm = seed_service(tmp_path / "one")
    core = service.conversation_service
    clock = [datetime(2026, 10, 23, 12)]
    core.now_provider = lambda: clock[0]
    core.state_manager.now_provider = lambda: clock[0]
    service.handle("你记得我什么", "day-one")
    refs = core.state_manager.reference_context("day-one")
    assert core._verified_recent_memory_context("day-one", refs, "继续")[0]
    service.handle("继续聊聊吧", "other-session")
    assert verified_facts(llm.calls[-1])["prior_memory_facts"] == []
    clock[0] += timedelta(seconds=core.state_manager.memory_reference_ttl_seconds + 1)
    service.handle("继续聊聊吧", "day-one")
    assert verified_facts(llm.calls[-1])["prior_memory_facts"] == []
    clock[0] += timedelta(days=1)
    service.handle("继续聊聊吧", "day-two")
    assert verified_facts(llm.calls[-1])["prior_memory_facts"] == []
    other, _, _, _ = build_service(tmp_path / "two", ContinuityLLM())
    assert other.conversation_service.state_manager.reference_context("day-one")["prior_memory_facts"] == []


def test_empty_store_has_no_verified_personal_facts_and_management_stays_verbatim(tmp_path):
    llm = ContinuityLLM("你好，我们慢慢聊。")
    service, _, memory, _ = build_service(tmp_path, llm)
    response = service.handle("你好呀", "empty")
    assert response.message == llm.reply
    assert not response.tool_results
    assert verified_facts(llm.calls[-1])["prior_memory_facts"] == []
    memory.add_memory("我喜欢拼图", category="preference", source="explicit_user_command")
    count = len(llm.calls)
    displayed = service.handle("展示长期记忆", "empty")
    assert "我喜欢拼图" in displayed.message
    assert "你喜欢拼图" not in displayed.message
    assert len(llm.calls) == count


def test_empty_formal_read_rejects_model_invented_memory_id(tmp_path):
    llm = ContinuityLLM()
    service, _, memory, _ = build_service(tmp_path, llm)
    response = service.handle("你记得我什么", "empty-read")
    assert [r.tool for r in response.tool_results] == ["list_memories"]
    assert response.tool_results[0].data["memory_read"]["facts"] == []
    assert "拼图" not in response.message
    assert memory.memories() == []


def test_recent_context_is_bounded_and_excludes_sensitive_and_suppressed_facts(tmp_path):
    service, memory, _ = seed_service(tmp_path)
    for n in range(2, 8):
        memory.add_memory(f"合成长期项目{n}", category="project", allow_similar=True, allow_conflict=True)
    memory.add_memory("合成身体不舒服的说明", category="health", allow_conflict=True)
    service.handle("你记得我什么", "bounded")
    core = service.conversation_service
    refs = core.state_manager.reference_context("bounded")
    assert len(refs["prior_memory_facts"]) <= 5
    facts, _ = core._verified_recent_memory_context("bounded", refs, "聊聊云吧")
    assert len(facts) <= 5
    assert all(fact["category"] != "health" for fact in facts)
