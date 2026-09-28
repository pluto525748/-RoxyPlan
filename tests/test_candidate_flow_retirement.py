import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from test_unified_semantic_pipeline import (
    _complete_desktop_turn,
    _desktop_runtime_with_raw_decision,
    _service_with_decisions,
)


def _candidate_snapshot(service):
    operation = service.memory_service.list_candidates(status=None)
    assert operation.success
    return json.loads(json.dumps(operation.data["candidates"], ensure_ascii=False))


def test_ordinary_dance_statement_has_no_action_or_memory_side_effect(tmp_path):
    text = "我喜欢看你跳舞"
    service, _growth, memory, _parser = _service_with_decisions(
        tmp_path,
        {text: {"intent": "chat"}},
    )
    before_candidates = _candidate_snapshot(service)

    response = service.handle(text, "ordinary-chat")

    assert response.status == "chat"
    assert response.tool_results == []
    assert memory.memories() == []
    assert _candidate_snapshot(service) == before_candidates == []
    assert service.interaction_coordinator.current("ordinary-chat").state != "awaiting_confirmation"


def test_explicit_memory_writes_formal_once_without_candidate_or_confirmation(tmp_path):
    command = "请记住我喜欢看你跳舞"
    payload = "我喜欢看你跳舞"
    service, _growth, memory, _parser = _service_with_decisions(
        tmp_path,
        {
            command: {
                "intent": "add_memory_request",
                "tool_calls": [
                    {
                        "domain": "memory",
                        "name": "save_formal_memory",
                        "arguments": {"content": payload},
                    }
                ],
            }
        },
    )
    calls = []
    original_add = memory.add_memory

    def counted_add(*args, **kwargs):
        calls.append((args, kwargs))
        return original_add(*args, **kwargs)

    memory.add_memory = counted_add
    response = service.handle(command, "explicit-save")

    assert [result.tool for result in response.tool_results] == ["save_formal_memory"]
    assert response.tool_results[0].success is True
    assert len(calls) == 1
    assert [item["content"] for item in memory.memories()] == [payload]
    assert _candidate_snapshot(service) == []
    assert all(result.tool != "play_dance" for result in response.tool_results)
    assert service.interaction_coordinator.current("explicit-save").state != "awaiting_confirmation"


def test_explicit_goal_is_immediately_available_as_formal_memory(tmp_path):
    command = "完成洛琪希项目是我的长期目标，请记住"
    payload = "完成洛琪希项目是我的长期目标"
    query = "你知道我什么"
    service, _growth, _memory, _parser = _service_with_decisions(
        tmp_path,
        {
            command: {
                "intent": "add_memory_request",
                "tool_calls": [
                    {
                        "domain": "memory",
                        "name": "save_formal_memory",
                        "arguments": {"content": payload, "category": "goal"},
                    }
                ],
            },
            query: {
                "intent": "show_memory",
                "tool_calls": [{"domain": "memory", "name": "list_memories"}],
            },
        },
    )

    saved = service.handle(command, "goal-memory")
    listed = service.handle(query, "goal-memory")

    assert saved.tool_results[0].success is True
    assert [result.tool for result in listed.tool_results] == ["list_memories"]
    assert "完成洛琪希项目是你的长期目标" in listed.message
    assert "待审核" not in listed.message
    assert "候选" not in listed.message


def test_previous_user_message_is_saved_directly_in_same_conversation(tmp_path):
    previous = "我希望这个月完成洛琪希安全交互收尾"
    command = "把刚才说的加入长期记忆"
    service, _growth, memory, _parser = _service_with_decisions(
        tmp_path,
        {
            previous: {"intent": "chat"},
            command: {
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

    service.handle(previous, "same-conversation")
    response = service.handle(command, "same-conversation")

    assert response.tool_results[0].tool == "save_formal_memory"
    assert response.tool_results[0].success is True
    assert [item["content"] for item in memory.memories()] == [previous]
    assert _candidate_snapshot(service) == []


def test_formal_memory_query_does_not_read_or_display_existing_candidates(tmp_path):
    query = "你知道我什么"
    service, _growth, _memory, _parser = _service_with_decisions(
        tmp_path,
        {
            query: {
                "intent": "show_memory",
                "tool_calls": [{"domain": "memory", "name": "list_memories"}],
            }
        },
    )
    formal = service.memory_service.save_formal_memory(
        "我的长期目标是完成洛琪希项目",
        category="goal",
    )
    assert formal.success
    service.memory_service.set_candidates_enabled(True)
    candidate = service.memory_service.create_candidate(
        "我习惯每周复盘一次",
        source="compatibility_test",
        explicit=False,
    )
    assert candidate.success
    before_candidates = _candidate_snapshot(service)

    response = service.handle(query, "formal-query")

    assert response.tool_results[0].tool == "list_memories"
    assert "你的长期目标是完成洛琪希项目" in response.message
    assert "我习惯每周复盘一次" not in response.message
    assert "待审核" not in response.message
    assert "候选" not in response.message
    assert _candidate_snapshot(service) == before_candidates


def test_persistent_diagnostic_records_formal_pipeline_without_private_text(tmp_path):
    command = "请记住我喜欢看你跳舞"
    service, _growth, _memory, _parser = _service_with_decisions(
        tmp_path,
        {
            command: {
                "intent": "add_memory_request",
                "tool_calls": [
                    {
                        "domain": "memory",
                        "name": "save_formal_memory",
                        "arguments": {"content": "我喜欢看你跳舞"},
                    }
                ],
            }
        },
    )

    response = service.handle(command, "diagnostic-save")
    log_path = tmp_path / "logs" / "interaction_diagnostics.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    record = next(item for item in records if item["request_id"] == response.request_id)

    assert record["pipeline"] == [
        "SemanticDecision",
        "Validation",
        "ToolExecution:save_formal_memory",
        "ToolResult:save_formal_memory:success",
        "MemoryAudit:create",
        "FinalResponse",
    ]
    serialized = json.dumps(record, ensure_ascii=False)
    assert command not in serialized
    assert "我喜欢看你跳舞" not in serialized
    assert "MemoryCandidate" not in serialized
    assert "awaiting_confirmation" not in serialized


def test_raw_llm_explicit_save_uses_formal_tool_without_candidate(tmp_path):
    command = "这件事以后会长期影响我，麻烦收好"
    service, _growth, memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "write",
            "intent": "add_memory_request",
            "entities": {"content": "我喜欢看你跳舞"},
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
        },
    )

    response = _complete_desktop_turn(service, command, "raw-formal-save")

    assert llm.semantic_calls == 1
    assert [result.tool for result in response.tool_results] == ["save_formal_memory"]
    assert response.tool_results[0].success is True
    assert [item["content"] for item in memory.memories()] == ["我喜欢看你跳舞"]
    assert _candidate_snapshot(service) == []
    assert service.interaction_coordinator.current("raw-formal-save").state != "awaiting_confirmation"


def test_candidate_wording_is_retired_without_reading_formal_memory(tmp_path):
    service, _growth, _memory, llm = _desktop_runtime_with_raw_decision(
        tmp_path,
        {
            "mode": "read",
            "intent": "show_memory_candidates",
            "entities": {},
            "proposed_tool": "list_memory_candidates",
            "confidence": 0.99,
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
        reply="候选流程不参与普通对话。",
    )
    service.memory_service.set_candidates_enabled(True)
    created = service.memory_service.create_candidate(
        "我习惯每周复盘一次",
        source="compatibility_test",
        explicit=False,
    )
    assert created.success
    saved = service.memory_service.save_formal_memory("我喜欢无压力学习")
    assert saved.success
    before = _candidate_snapshot(service)

    response = service.handle("查看待审核记忆", "candidate-disabled")

    # Candidate wording is no longer a product capability and must not be
    # silently reinterpreted as a formal-memory query.
    assert llm.semantic_calls == 0
    assert response.tool_results == []
    assert response.status == "failed"
    assert "候选记忆功能已经停用" in response.message
    assert "喜欢无压力学习" not in response.message
    assert _candidate_snapshot(service) == before
    diagnostic = service.conversation_service.diagnostic_snapshot()[-1]
    assert diagnostic["route_source"] == "retired_memory_candidate"
    state = service.interaction_coordinator.current("candidate-disabled")
    assert state.state not in {"candidates_listed", "awaiting_confirmation"}
    assert state.pending is False
