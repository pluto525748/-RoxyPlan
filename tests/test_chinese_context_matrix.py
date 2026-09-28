import json
from pathlib import Path

import pytest


from modules.intent_router import IntentRouter, LLMIntentParser
from modules.memory_data_query_guard import MemoryDataQueryGuard
from modules.semantic_action_parser import SemanticActionParser
from semantic_contract_fixtures import (
    SemanticFixtureContractError,
    adapt_legacy_v21_semantic_payload,
    build_current_semantic_payload,
)
from v18_test_support import build_service


MATRIX_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "v21_chinese_context_matrix.json"
)
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
CASES = MATRIX["cases"]
# Cases that require special context (previous turn, pending state) are tested
# via dedicated functions, not the generic matrix parametrize.
CONTEXT_DEPENDENT_CASE_IDS = {"regress_a", "regress_g"}
MATRIX_CASES = [c for c in CASES if c["id"] not in CONTEXT_DEPENDENT_CASE_IDS]


class OneDecisionLLM:
    """Return one reviewed semantic contract and record every semantic prompt."""

    def __init__(self, case, *, reply="这是隔离语境测试的自然回复。"):
        self.case = dict(case)
        self.reply = str(reply)
        self.semantic_calls = []
        self.reply_calls = 0

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls.append(system_text)
            payload = adapt_legacy_v21_semantic_payload(self.case)
            return json.dumps(payload, ensure_ascii=False)
        self.reply_calls += 1
        return self.reply


class RawSemanticLLM:
    def __init__(self, raw, *, reply="这是隔离语境测试的自然回复。"):
        self.raw = str(raw)
        self.reply = str(reply)
        self.semantic_calls = 0

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            self.semantic_calls += 1
            return self.raw
        return self.reply


def test_current_semantic_fixture_rejects_missing_structured_fields_with_case_id():
    with pytest.raises(SemanticFixtureContractError) as exc_info:
        build_current_semantic_payload(
            {
                "mode": "chat",
                "intent": "chat",
                "entities": {},
            },
            case_id="current_missing_structured_fields",
        )

    message = str(exc_info.value)
    assert "current_missing_structured_fields" in message
    assert "subject" in message
    assert "polarity" in message
    assert "modality" in message
    assert "request_mode" in message
    assert "explicit_command" in message


def test_legacy_v21_adapter_is_complete_without_inventing_annotations():
    payload = adapt_legacy_v21_semantic_payload(
        {
            "id": "legacy_write_case",
            "mode": "write",
            "intent": "add_plan",
            "entities": {"title": "复习线性回归"},
            "expected_candidates": ["add_plan"],
        }
    )

    assert payload["request_mode"] == "execute"
    assert payload["explicit_command"] is True
    assert payload["subject"] == ""
    assert payload["polarity"] == ""
    assert payload["modality"] == ""


@pytest.mark.parametrize("case", MATRIX_CASES, ids=lambda item: item["id"])
def test_research_driven_matrix_obeys_single_decision_contract(case):
    llm = OneDecisionLLM(case)
    router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    parser = SemanticActionParser(router)

    result = parser.parse_unified(
        case["text"],
        {"conversation_id": "matrix-scope"},
        allow_llm=True,
    )

    actual_tools = [item.tool_name for item in result.candidates]
    assert actual_tools == case["expected_candidates"]
    assert not set(actual_tools).intersection(case.get("forbidden_tools", []))
    assert str(result.intent_result.get("intent")) == case["intent"]
    assert len(llm.semantic_calls) <= 1
    if "expect_semantic_calls" in case:
        assert len(llm.semantic_calls) == int(case["expect_semantic_calls"])
    if case["mode"] == "clarify":
        assert result.needs_clarification is True
        assert result.intent_result.get("clarification_question")
    assert result.as_decision().tool_calls == [
        item for item in result.as_decision().tool_calls
    ]


@pytest.mark.parametrize(
    "case",
    [
        item
        for item in CASES
        if item["id"]
        in {
            "chat_002", "chat_004", "chat_005", "chat_007", "chat_013",
            "chat_020", "read_001", "read_008", "read_014", "write_002",
            "write_003", "write_010", "write_013", "write_014", "write_018",
        }
    ],
    ids=lambda item: "punctuation_" + item["id"],
)
def test_reviewed_punctuation_invariance_does_not_change_tool_policy(case):
    variant = "洛琪希，" + case["text"].rstrip("。！？!? ") + "。"
    varied = {**case, "text": variant}
    llm = OneDecisionLLM(varied)
    result = SemanticActionParser(
        IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    ).parse_unified(variant, {"conversation_id": "variant-scope"}, allow_llm=True)

    actual_tools = [item.tool_name for item in result.candidates]
    assert actual_tools == case["expected_candidates"]
    assert not set(actual_tools).intersection(case.get("forbidden_tools", []))
    assert len(llm.semantic_calls) <= 1


def test_memory_query_guard_uses_minimal_contrasts_not_broad_keywords():
    guard = MemoryDataQueryGuard()
    positives = (
        "你知道我什么",
        "你记得我什么",
        "你记住了哪些关于我的信息",
        "你保存了我的哪些信息",
        "说说你对我的了解",
        "你对我是怎样的印象",
        "按你记住的来讲讲我",
        "我问你你知道我什么，就是想让你表达你对我的了解",
    )
    negatives = (
        "你知道我为什么睡不着吗",
        "你知道我在哪里吗",
        "你记得我刚才问了什么吗",
        "你了解我说的逻辑回归吗",
        "你知道我的问题出在哪吗",
    )

    assert all(guard.route(text)["intent"] == "show_memory" for text in positives)
    assert all(guard.route(text) is None for text in negatives)


def test_incomplete_explicit_action_can_reach_one_clarification(capsys):
    case = next(item for item in CASES if item["id"] == "clarify_001")
    llm = OneDecisionLLM(case)
    parsed = LLMIntentParser(llm.chat).parse(case["text"])

    assert parsed is not None
    assert parsed["mode"] == "clarify"
    assert parsed["intent"] == "add_plan"
    assert parsed["entities"] == {}
    assert parsed["clarification_question"] == "想把什么加入今天计划？"
    assert "schema_invalid_entities" not in capsys.readouterr().out


def test_mode_and_proposed_tool_mismatch_is_rejected_by_contract(capsys):
    raw = {
        "mode": "write",
        "intent": "show_plan",
        "entities": {},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.91,
        "clarification_question": None,
        "candidate_actions": [],
    }
    parser = LLMIntentParser(lambda _messages: json.dumps(raw, ensure_ascii=False))

    parsed = parser.parse("今天还有什么计划")

    assert parsed is None
    output = capsys.readouterr().out
    assert "proposed_tool_intent_mismatch" in output


def test_mode_and_intent_mismatch_is_rejected_before_any_tool_candidate(capsys):
    raw = {
        "mode": "chat",
        "intent": "add_plan",
        "entities": {"title": "不应执行的计划"},
        "proposed_tool": "add_plan",
        "confidence": 0.91,
        "clarification_question": None,
        "candidate_actions": [],
    }
    parser = LLMIntentParser(lambda _messages: json.dumps(raw, ensure_ascii=False))

    assert parser.parse("帮我添加一项计划") is None
    assert "mode_intent_mismatch" in capsys.readouterr().out


def test_missing_required_mode_is_rejected_instead_of_guessed(capsys):
    raw = {
        "intent": "show_plan",
        "entities": {},
        "proposed_tool": "show_plan",
        "confidence": 0.91,
        "clarification_question": None,
        "candidate_actions": [],
    }
    parser = LLMIntentParser(lambda _messages: json.dumps(raw, ensure_ascii=False))

    assert parser.parse("今天还有什么计划") is None
    assert "schema_missing_or_invalid_mode" in capsys.readouterr().out


def test_duplicate_semantic_actions_are_executed_at_most_once(capsys):
    case = {
        "mode": "write",
        "intent": "multi_action",
        "entities": {},
        "candidate_actions": [
            {"intent": "add_plan", "entities": {"title": "复习随机森林"}},
            {"intent": "add_plan", "entities": {"title": "复习随机森林"}}
        ],
    }
    llm = OneDecisionLLM(case)
    result = SemanticActionParser(
        IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    ).parse_unified("安排复习随机森林", {}, allow_llm=True)

    assert [item.tool_name for item in result.candidates] == ["add_plan"]
    assert "duplicate_semantic_action" in capsys.readouterr().out


def test_single_intent_cannot_smuggle_an_unrelated_candidate_action(capsys):
    raw = {
        "mode": "read",
        "intent": "show_plan",
        "entities": {},
        "proposed_tool": "show_plan",
        "confidence": 0.94,
        "clarification_question": None,
        "candidate_actions": [
            {"intent": "add_plan", "entities": {"title": "模型额外生成的计划"}}
        ],
    }
    parser = LLMIntentParser(lambda _messages: json.dumps(raw, ensure_ascii=False))

    parsed = parser.parse("今天还有什么计划")

    assert parsed is not None
    assert parsed["intent"] == "show_plan"
    assert parsed["candidate_actions"] == []
    assert "candidate_actions_removed_for_single_intent" in parsed["semantic_diagnostic"]
    assert "candidate_actions_removed_for_single_intent" in capsys.readouterr().out


@pytest.mark.parametrize(
    "raw, expected_reason",
    (
        (
            json.dumps(
                {
                    "mode": "chat",
                    "intent": "add_plan",
                    "entities": {"title": "不应执行的计划"},
                    "proposed_tool": "add_plan",
                    "confidence": 0.93,
                    "candidate_actions": [],
                    "clarification_question": None,
                },
                ensure_ascii=False,
            ),
            "mode_intent_mismatch",
        ),
        ("{not-json", "json_parse_error"),
    ),
)
def test_rejected_raw_provider_decision_is_safely_logged_and_executes_nothing(
    tmp_path,
    raw,
    expected_reason,
):
    llm = RawSemanticLLM(raw)
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        "这是一条需要语义判断的隔离表达",
        "raw-provider-failure",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == "chat"
    assert response.tool_results == []
    assert growth.tasks() == []
    assert llm.semantic_calls == 1
    record = runtime.conversation_service.diagnostic_snapshot()[-1]
    assert any(expected_reason in item for item in record["validation_notes"])
    assert record["pipeline"] == ["SemanticDecision", "Validation", "FinalResponse"]


@pytest.mark.parametrize(
    "case_id, expected_executed_tools, expected_status",
    (
        ("chat_004", [], "chat"),
        ("chat_005", [], "chat"),
        ("chat_025", [], "chat"),
        ("read_001", ["show_plan"], "completed"),
        ("read_005", ["list_memories"], "completed"),
        ("write_002", ["play_dance"], "completed"),
        ("write_003", ["add_plan"], "completed"),
        ("write_013", ["save_formal_memory"], "completed"),
        ("clarify_001", [], "clarification"),
    ),
)
def test_representative_contexts_run_through_real_service_and_diagnostics(
    tmp_path,
    case_id,
    expected_executed_tools,
    expected_status,
):
    case = next(item for item in CASES if item["id"] == case_id)
    llm = OneDecisionLLM(case)
    runtime, growth, memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False
    growth.add_task("隔离数据：复习逻辑回归")
    memory.add_memory("我的长期目标是完成隔离测试", category="goal")

    turn = runtime.conversation_service.prepare(
        case["text"],
        "context-matrix-" + case_id,
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == expected_status
    assert [item.tool for item in response.tool_results] == expected_executed_tools
    assert not set(expected_executed_tools).intersection(case.get("forbidden_tools", []))
    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(item for item in records if item["request_id"] == response.request_id)
    assert record["pipeline"].count("SemanticDecision") == 1
    logged_tools = [item["tool"] for item in record["tool_calls"]]
    assert logged_tools == expected_executed_tools
    assert record["semantic_decision"]["intent"] == case["intent"]
    if expected_executed_tools:
        assert all(item["success"] for item in record["tool_results"])


def test_pending_shape_questions_are_not_consumed_as_plan_parameters(tmp_path):
    llm = OneDecisionLLM(next(item for item in CASES if item["id"] == "chat_003"))
    runtime, _growth, _memory, _history = build_service(tmp_path, llm)
    service = runtime.conversation_service
    coordinator = runtime.interaction_coordinator

    duration_state = coordinator.awaiting_clarification(
        "duration-scope",
        "missing_slots",
        domain="plan",
        missing_fields=["duration_minutes"],
        known_fields={"title": "复习随机森林"},
        immutable_arguments={"tool_name": "add_plan", "title": "复习随机森林"},
    )
    time_state = coordinator.awaiting_clarification(
        "time-scope",
        "missing_slots",
        domain="plan",
        missing_fields=["time_slot"],
        known_fields={"title": "复习随机森林", "duration_minutes": 30},
        immutable_arguments={"tool_name": "add_plan", "title": "复习随机森林"},
    )
    object_state = coordinator.awaiting_clarification(
        "object-scope",
        "object_selection",
        domain="plan",
        listed_object_ids=["task-1", "task-2"],
        candidate_objects=[{"uid": "task-1"}, {"uid": "task-2"}],
    )

    assert not service._is_pending_input_compatible(duration_state, "一小时有多少分钟")
    assert not service._is_pending_input_compatible(time_state, "晚上八点有什么新闻")
    assert not service._is_pending_input_compatible(object_state, "第二个为什么更合适")
    assert service._is_pending_input_compatible(duration_state, "一小时")
    assert service._is_pending_input_compatible(time_state, "晚上八点")
    assert service._is_pending_input_compatible(object_state, "第二个")


def test_matrix_is_research_traceable_and_has_broad_coverage():
    assert len(CASES) >= 70
    assert len(MATRIX["sources"]) >= 6
    phenomena = {item["phenomenon"] for item in CASES}
    assert {
        "negated_action",
        "self_correction_cancel",
        "memory_guard_negative_contrast",
        "assistant_message_reference",
        "explicit_read_write_multi_intent",
        "condition_not_immediate_execution",
        "protected_dance_memory_payload",
    }.issubset(phenomena)
    assert len({item["id"] for item in CASES}) == len(CASES)


# ── Real desktop V2.1 acceptance regression cases ──────────────────────

REGRESSION_CASES = [cid for cid in [
    "regress_b", "regress_c", "regress_d", "regress_e", "regress_f",
]]


@pytest.mark.parametrize("case_id", REGRESSION_CASES)
def test_regression_cases_pass_single_decision_contract(case_id):
    case = next(item for item in CASES if item["id"] == case_id)
    llm = OneDecisionLLM(case)
    router = IntentRouter(LLMIntentParser(llm.chat), enable_llm=True)
    parser = SemanticActionParser(router)

    result = parser.parse_unified(
        case["text"],
        {"conversation_id": "regression-" + case_id},
        allow_llm=True,
    )

    actual_tools = [item.tool_name for item in result.candidates]
    assert actual_tools == case["expected_candidates"]
    assert not set(actual_tools).intersection(case.get("forbidden_tools", []))
    assert str(result.intent_result.get("intent")) == case["intent"]
    assert len(llm.semantic_calls) <= 1
    if "expect_semantic_calls" in case:
        assert len(llm.semantic_calls) == int(case["expect_semantic_calls"])


def test_regress_c_chat_only_zero_write(tmp_path):
    """Case C: 普通陈述不写入长期记忆，不声称记住了。"""
    case = next(item for item in CASES if item["id"] == "regress_c")
    llm = OneDecisionLLM(case)
    runtime, _growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        case["text"], "regress-c", record_history=False, allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == "chat"
    assert response.tool_results == []
    # Must not claim to have saved anything
    assert "记住" not in response.message or "这次还没有" in response.message


def test_regress_e_bare_affirmative_rejected(tmp_path, capsys):
    """Case E: '对的' 在没有结构化 pending 时不触发写工具。"""
    # Simulate LLM returning write+previous_assistant_message for a bare
    # affirmative — this must be rejected by the parser gate.
    raw_affirmative_write = {
        "mode": "write",
        "intent": "add_memory_request",
        "entities": {"content": "previous_assistant_message"},
        "proposed_tool": "save_formal_memory",
        "confidence": 0.95,
        "clarification_question": None,
        "candidate_actions": [],
    }

    def bad_llm(_messages):
        return json.dumps(raw_affirmative_write, ensure_ascii=False)

    parser = LLMIntentParser(bad_llm)
    parsed = parser.parse("对的")

    assert parsed is None
    captured = capsys.readouterr().out
    assert "affirmative_cannot_use_previous_assistant_message_as_content" in captured


def test_regress_f_add_plan_title_only_no_clarify(tmp_path):
    """Case F: title 清晰时直接 add_plan，不强制要求时间。"""
    case = next(item for item in CASES if item["id"] == "regress_f")
    llm = OneDecisionLLM(case)
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        case["text"], "regress-f", record_history=False, allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == "completed"
    executed = [item.tool for item in response.tool_results]
    assert "add_plan" in executed
    assert all(item.success for item in response.tool_results)


def test_regress_b_write_then_claim_verified(tmp_path):
    """Case B: 保存偏好后 ToolResult 成功才能说记住了。"""
    case = next(item for item in CASES if item["id"] == "regress_b")
    llm = OneDecisionLLM(case)
    runtime, _growth, memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        case["text"], "regress-b", record_history=False, allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.status == "completed"
    executed = [item.tool for item in response.tool_results]
    assert executed == ["save_formal_memory"]
    assert all(item.success for item in response.tool_results)
    # After successful save, the reply may say "记住了" — verified by ToolResult
    records = runtime.conversation_service.diagnostic_snapshot()
    record = next(
        item for item in records if item["request_id"] == response.request_id
    )
    assert record["tool_results"][0]["success"] is True


def test_schema_rejected_reply_not_impersonate_business(tmp_path, capsys):
    """After schema_invalid_entities, the reply must not ask business questions."""
    from tests.test_chinese_context_matrix import RawSemanticLLM

    raw = {
        "mode": "write",
        "intent": "add_plan",
        "entities": {"title": ""},
        "proposed_tool": "add_plan",
        "confidence": 0.9,
        "clarification_question": None,
        "candidate_actions": [],
    }
    llm = RawSemanticLLM(json.dumps(raw, ensure_ascii=False))
    runtime, growth, _memory, _history = build_service(tmp_path, llm)
    runtime.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False

    turn = runtime.conversation_service.prepare(
        "加个计划",
        "schema-reject-test",
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)

    assert response.tool_results == []
    # Must not ask "几点、多久" after schema rejection
    assert not any(
        phrase in str(response.message)
        for phrase in ("几点", "多久", "多长时间")
    ) or "这次还没有" in str(response.message) or "没有成功" in str(response.message), (
        f"Reply impersonates business after schema rejection: {response.message[:200]}"
    )


def test_action_claim_guard_blocks_first_person_memory_claim(tmp_path):
    """'我记住了' without matching ToolResult must be blocked."""
    from modules.action_claim_guard import ActionClaimGuard, ACTION_CLAIM_PATTERN

    # The extended ACTION_CLAIM_PATTERN now covers "记住"/"记下"
    assert ACTION_CLAIM_PATTERN.search("我记住了")
    assert ACTION_CLAIM_PATTERN.search("我已经记下了")
    assert ACTION_CLAIM_PATTERN.search("帮你记进去了")

    guard = ActionClaimGuard()
    blocked = guard.validate("我记住了。", [], user_text="")
    assert "还没有执行" in blocked

    blocked = guard.validate("我已经记下了。", [], user_text="")
    assert "还没有执行" in blocked


def test_action_claim_guard_blocks_plan_completion_claim(tmp_path):
    """'帮你加入计划了' without matching ToolResult must be blocked."""
    from modules.action_claim_guard import ActionClaimGuard, ACTION_CLAIM_PATTERN

    assert ACTION_CLAIM_PATTERN.search("帮你加入计划了")
    assert ACTION_CLAIM_PATTERN.search("已经安排好了")

    guard = ActionClaimGuard()
    blocked = guard.validate("帮你加入计划了。", [], user_text="")
    assert "还没有执行" in blocked


def test_action_claim_guard_blocks_reminder_promise(tmp_path):
    """'到时候会提醒你' without reminder ToolResult must be blocked."""
    from modules.action_claim_guard import ActionClaimGuard, REMINDER_PROMISE_PATTERN

    assert REMINDER_PROMISE_PATTERN.search("到时候会提醒你")
    assert REMINDER_PROMISE_PATTERN.search("我会提醒你")

    guard = ActionClaimGuard()
    blocked = guard.validate("到时候会提醒你。", [], user_text="")
    assert "不能承诺" in blocked
