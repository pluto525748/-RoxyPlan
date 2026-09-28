"""V2.1 full-chain assertions for key Chinese context cases.

Each test verifies the complete production chain:
  SemanticDecision → Validation → ReferenceResolution → BusinessResolver
  → SafetyPolicy → ToolExecution → ToolResult → FinalResponse
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.intent_router import IntentRouter, LLMIntentParser
from modules.semantic_action_parser import SemanticActionParser
from semantic_contract_fixtures import adapt_legacy_v21_semantic_payload
from v18_test_support import build_service


MATRIX_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "v21_chinese_context_matrix.json"
)
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
CASES = {item["id"]: item for item in MATRIX["cases"]}


class OneDecisionLLM:
    def __init__(self, case, *, reply="这是全链路断言测试的自然回复。"):
        self.case = dict(case)
        self.reply = str(reply)
        self.semantic_calls = []
        self.reference_extraction_calls = 0
        self.reply_calls = 0

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls.append(system_text)
            payload = adapt_legacy_v21_semantic_payload(self.case)
            return json.dumps(payload, ensure_ascii=False)
        if "助手回复只是引用来源，不等于最终记忆正文" in system_text:
            self.reference_extraction_calls += 1
            return json.dumps(
                {
                    "status": "extracted",
                    "content": "我的长期目标是完成隔离验收。",
                },
                ensure_ascii=False,
            )
        self.reply_calls += 1
        return self.reply


# ── full-chain cases ──────────────────────────────────────────────

FULLCHAIN_CASES = [
    # (case_id, expected_tools, expected_status, pre_seed)
    ("chat_004", [], "chat", None),
    ("chat_005", [], "chat", None),
    ("chat_007", [], "chat", None),
    ("chat_020", [], "chat", None),
    ("chat_025", [], "chat", None),
    ("read_001", ["show_plan"], "completed", "add_task"),
    ("read_005", ["list_memories"], "completed", "add_memory"),
    ("read_014", ["show_plan"], "completed", "add_task"),
    ("write_002", ["play_dance"], "completed", None),
    ("write_003", ["add_plan"], "completed", None),
    ("write_006", ["save_formal_memory"], "completed", None),
    ("write_008", ["save_formal_memory"], "completed", None),
    ("write_013", ["save_formal_memory"], "completed", None),
    ("write_019", ["save_formal_memory"], "completed", None),
    ("clarify_001", [], "clarification", None),
    ("clarify_004", [], "failed", None),
    ("multi_001", ["show_plan", "add_plan"], "completed", "add_task"),
    ("multi_004", ["save_formal_memory"], "completed", None),
    ("multi_007", [], "chat", None),
]


def _seed(runtime, growth, memory, seed_type):
    if seed_type == "add_task":
        growth.add_task("隔离数据：复习逻辑回归")
    elif seed_type == "add_memory":
        memory.add_memory("我的长期目标是完成隔离测试", category="goal")


@pytest.mark.parametrize(
    "case_id, expected_tools, expected_status, pre_seed",
    FULLCHAIN_CASES,
    ids=[item[0] for item in FULLCHAIN_CASES],
)
def test_fullchain_semantic_decision_single_call(
    tmp_path, case_id, expected_tools, expected_status, pre_seed
):
    """Verify exactly one SemanticDecision per message, correct tools, no forbidden tools."""
    case = CASES[case_id]
    llm = OneDecisionLLM(case)
    runtime, growth, memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    if pre_seed:
        _seed(runtime, growth, memory, pre_seed)

    # Seed assistant message for write_008 (assistant_message_reference)
    if case_id == "write_008":
        runtime.conversation_service.state_manager.observe_assistant(
            "fullchain-" + case_id,
            "请记住：我的长期目标是完成隔离验收。"
            "你回复‘请记住’后，我再正式保存。",
        )

    turn = runtime.conversation_service.prepare(
        case["text"],
        "fullchain-" + case_id,
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    # 1. Single semantic call
    assert len(llm.semantic_calls) <= 1, (
        f"Expected ≤1 semantic call, got {len(llm.semantic_calls)}"
    )
    if case.get("expect_semantic_calls") is not None:
        assert len(llm.semantic_calls) == int(case["expect_semantic_calls"])
    assert llm.reference_extraction_calls == int(case_id == "write_008")

    # 2. Intent matches
    assert str(response.status) == expected_status, (
        f"Status: expected={expected_status} actual={response.status}"
    )

    # 3. Tools match
    actual_tools = [item.tool for item in response.tool_results]
    assert actual_tools == expected_tools, (
        f"Tools: expected={expected_tools} actual={actual_tools}"
    )

    if case_id == "clarify_004":
        # The unbound legacy delete proposal is not an executable confirmation.
        # Retain the source fixture and semantic-call assertions, but require a
        # closed failure rather than a fabricated pending operation.
        scope = "fullchain-" + case_id
        assert response.tool_results == []
        assert runtime.interaction_coordinator.current(scope).pending is False
        assert runtime.confirmation_manager.pending(scope=scope) is None
        assert growth.tasks() == []

    # 4. No forbidden tools
    forbidden = set(case.get("forbidden_tools", []))
    assert not forbidden.intersection(actual_tools), (
        f"Forbidden tools executed: {forbidden.intersection(actual_tools)}"
    )

    # 5. Diagnostics integrity
    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(
        item for item in records if item["request_id"] == response.request_id
    )
    assert record["pipeline"].count("SemanticDecision") == 1, (
        f"Multiple SemanticDecision in pipeline: {record['pipeline']}"
    )

    # 6. Tool results consistency
    logged_tools = [item["tool"] for item in record["tool_calls"]]
    assert logged_tools == expected_tools

    # 7. chat_only → zero tool calls
    if expected_status == "chat":
        assert response.tool_results == []
        assert record["tool_calls"] == []

    # 8. Semantic decision logged
    sd = record.get("semantic_decision", {})
    assert sd.get("intent") == case["intent"], (
        f"Logged intent: expected={case['intent']} actual={sd.get('intent')}"
    )
    assert sd.get("mode") == case["mode"], (
        f"Logged mode: expected={case['mode']} actual={sd.get('mode')}"
    )

    # 9. No post-FinalResponse tools
    if "FinalResponse" in record["pipeline"]:
        fr_idx = len(record["pipeline"]) - 1 - record["pipeline"][::-1].index(
            "FinalResponse"
        )
        pipeline_after = record["pipeline"][fr_idx + 1 :]
        tool_phases_after = [
            p for p in pipeline_after if p.startswith("Tool")
        ]
        assert not tool_phases_after, (
            f"Tool phases after FinalResponse: {tool_phases_after}"
        )

    # 10. write tools must have success=True unless clarification
    if expected_status == "completed":
        assert all(item.success for item in response.tool_results), (
            f"Write tool failed: {[item.tool for item in response.tool_results if not item.success]}"
        )


def test_fullchain_chat_only_never_produces_tool_calls(tmp_path):
    """chat_only decisions must not produce any tool execution or pending state."""
    chat_cases = [
        cid
        for cid in ["chat_002", "chat_007", "chat_020", "chat_025"]
    ]
    for case_id in chat_cases:
        case = CASES[case_id]
        llm = OneDecisionLLM(case)
        runtime, growth, _memory, _history = build_service(
            tmp_path / case_id, llm
        )
        runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
        runtime.conversation_service.semantic_decision_compatibility_enabled = False

        turn = runtime.conversation_service.prepare(
            case["text"],
            "chat-only-" + case_id,
            record_history=False,
            allow_llm_intent=False,
        )
        response = runtime.conversation_service.complete(turn)

        assert response.status == "chat", f"{case_id}: status={response.status}"
        assert response.tool_results == [], (
            f"{case_id}: unexpected tools={[t.tool for t in response.tool_results]}"
        )
        # No pending state should be created
        coordinator = runtime.interaction_coordinator
        state = coordinator.current("chat-only-" + case_id)
        # state='idle' with empty interaction_kind means no active pending
        assert state is None or state.state == "idle", (
            f"{case_id}: chat created pending state: {state}"
        )


def test_fullchain_write_diagnostics_contain_tool_result_evidence(tmp_path):
    """Write operations must record ToolResult evidence in diagnostics."""
    case = CASES["write_006"]
    llm = OneDecisionLLM(case)
    runtime, growth, memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        case["text"],
        "tool-evidence",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(
        item for item in records if item["request_id"] == response.request_id
    )

    assert response.status == "completed"
    assert record["semantic_decision"]["mode"] == "write"
    assert record["semantic_decision"]["intent"] == "add_memory_request"
    assert len(record["tool_calls"]) == 1
    assert record["tool_calls"][0]["tool"] == "save_formal_memory"
    assert len(record["tool_results"]) == 1
    assert record["tool_results"][0]["success"] is True
    # Verify pipeline contains ToolExecution phase
    assert any("ToolExecution" in p for p in record["pipeline"])
    # Verify validation notes present
    assert "validation_notes" in record


def test_fullchain_multi_action_produces_separate_tool_results(tmp_path):
    """Multi-action decisions must produce distinct ToolResults per action."""
    case = CASES["multi_001"]
    llm = OneDecisionLLM(case)
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False
    growth.add_task("隔离数据：复习逻辑回归")

    turn = runtime.conversation_service.prepare(
        case["text"],
        "multi-tool-evidence",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    actual_tools = [item.tool for item in response.tool_results]
    assert actual_tools == ["show_plan", "add_plan"]
    assert all(item.success for item in response.tool_results)

    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(
        item for item in records if item["request_id"] == response.request_id
    )
    assert record["pipeline"].count("SemanticDecision") == 1
    assert len(record["tool_calls"]) == 2
    assert len(record["tool_results"]) == 2


def test_fullchain_clarify_never_executes_tools(tmp_path):
    """Clarification decisions must produce zero tool executions."""
    case = CASES["clarify_001"]
    llm = OneDecisionLLM(case)
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        case["text"],
        "clarify-no-tools",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == "clarification"
    assert response.tool_results == []

    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(
        item for item in records if item["request_id"] == response.request_id
    )
    assert not record["tool_calls"]
    # status is "clarification" — no tools should have executed
    assert response.status == "clarification"


def test_fullchain_duplicate_actions_deduplicated(tmp_path):
    """Duplicate semantic actions must be executed at most once."""
    case = {
        "mode": "write",
        "intent": "multi_action",
        "entities": {},
        "candidate_actions": [
            {"intent": "add_plan", "entities": {"title": "复习随机森林"}},
            {"intent": "add_plan", "entities": {"title": "复习随机森林"}},
        ],
    }
    llm = OneDecisionLLM(case)
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        "安排复习随机森林",
        "dedup-test",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    actual_tools = [item.tool for item in response.tool_results]
    assert actual_tools == ["add_plan"]
    assert len(actual_tools) == 1
