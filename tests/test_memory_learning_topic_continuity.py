"""The model's topical facet must not erase a freshly verified saved fact."""
import json

import pytest

from modules.memory_read import MemoryReadRequest, TypedMemoryReader
from test_beta_online_failure_replay import _build_service, _provider_body


@pytest.mark.parametrize("topic", ["学习时间", "学习时段", "学习偏好"])
def test_updated_preference_is_read_after_challenge(tmp_path, monkeypatch, topic):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    calls = []
    decision = {
        "mode": "read", "intent": "show_memory", "confidence": 0.9,
        "entities": {"query_mode": "attribute", "attribute": "preference",
                     "topic": topic, "query": "我更适合早上还是晚上学习"},
        "proposed_tool": "list_memories", "request_mode": "query",
        "subject": "self", "polarity": "positive", "modality": "question",
        "candidate_actions": [], "explicit_command": False,
    }

    def transport(method, url, headers, payload, timeout):
        calls.append(payload)
        assert "You classify one Chinese user message" not in payload["messages"][0]["content"]
        content = {
            "answer": "按刚更新的记录，你现在更适合晚上学习。", "used_memory_ids": [1],
        }
        return _provider_body({"content": json.dumps(content, ensure_ascii=False)})

    service = _build_service(tmp_path, transport)
    service.handle("保存长期记忆：我更适合早上学习", "topic")
    updated = service.handle("记住我现在更适合晚上学习", "topic")
    assert updated.tool_results[0].data["operation"] == "updated"
    # Also verify the model-shaped topical request directly; the full user
    # query now uses the existing typed local reader before classification.
    snapshot = TypedMemoryReader(service.memory_manager).read(MemoryReadRequest.from_arguments(decision["entities"]))
    assert [(f.memory_id, f.content) for f in snapshot.facts] == [(1, "我现在更适合晚上学习")]
    response = service.handle("你确定吗？刚才不是这么说的。我现在更适合早上还是晚上学习？", "topic")
    facts = response.tool_results[0].data["memory_read"]["facts"]
    assert [(f["memory_id"], f["content"]) for f in facts] == [(1, "我现在更适合晚上学习")]
    assert "现在更适合晚上学习" in response.message
    assert len(calls) == 1
    # Reading may update usage metadata but must not replace the saved content.
    assert service.memory_manager.memories()[0]["content"] == "我现在更适合晚上学习"


@pytest.mark.parametrize("topic,content,matched", [
    ("学习时间", "我现在更适合晚上学习", True),
    ("学习时间", "我喜欢清淡饮食", False),
    ("饮食偏好", "我喜欢吃清淡的菜", True),
    ("工作习惯", "我习惯先写代码再整理笔记", True),
    ("Python", "我更适合晚上学习", False),
    ("Python", "我喜欢Python", True),
])
def test_domain_topic_aliases_keep_unrelated_and_named_topics_separate(topic, content, matched):
    assert TypedMemoryReader._matches_topic({"content": content}, topic) is matched
