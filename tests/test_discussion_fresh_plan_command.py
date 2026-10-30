"""A teaching list must not consume a complete, independent daily command."""
import json

import pytest

from test_beta_online_failure_replay import _build_service, _provider_body


TEACHING = "先认识三个角色：\n1. 查询 Q：现在想找什么\n2. 键 K：每个位置提供的线索\n3. 值 V：每个位置贡献的内容"


@pytest.mark.parametrize("command,title,should_add", [
    ("把学习注意力机制加入今天的计划。", "学习注意力机制", True),
    ("把复习Python函数加入今天计划。", "复习Python函数", True),
    ("把第二项加入今天计划。", "键 K", False),
    ("把这个加入今天计划。", "查询 Q", False),
    ("把刚才的内容加入今天计划。", "注意力机制", False),
])
def test_fresh_command_after_teaching_uses_normal_route(tmp_path, monkeypatch, command, title, should_add):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    calls = []
    decision = {
        "intent": "add_plan", "confidence": 0.99, "entities": {"title": title},
        "mode": "write", "request_mode": "execute", "subject": "self",
        "polarity": "positive", "modality": "command", "warnings": [],
    }

    def transport(method, url, headers, payload, timeout):
        calls.append(payload)
        content = TEACHING if len(calls) == 1 else json.dumps(decision, ensure_ascii=False)
        return _provider_body({"content": content})

    service = _build_service(tmp_path, transport)
    first = service.handle("带我学注意力机制", "fresh")
    assert first.tool_results == []
    assert service.interaction_coordinator.current("fresh").interaction_kind == "discussion_choice"
    response = service.handle(command, "fresh")
    tasks = service.growth_manager.tasks()
    if should_add:
        assert [(r.tool, r.success) for r in response.tool_results] == [("add_plan", True)]
        assert [t["title"] for t in tasks] == [title]
        assert len(calls) == 2
    else:
        assert not tasks and not response.tool_results
        assert len(calls) == 1
