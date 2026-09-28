import json
from pathlib import Path

import pytest

from modules.llm.deepseek_provider import DeepSeekProvider
from scripts import run_online_chinese_semantic_acceptance as acceptance
from tests.semantic_contract_fixtures import build_current_semantic_payload


def _provider_for(case):
    decision = build_current_semantic_payload({
        "mode": case["mode"],
        "intent": case["intent"],
        "entities": dict(case.get("entities", {})),
        "proposed_tool": case.get("proposed_tool"),
        "confidence": 0.96,
        "follow_up_target": case.get("follow_up_target"),
        "needs_confirmation": bool(case.get("needs_confirmation", False)),
        "warnings": [],
        "clarification_question": case.get("clarification_question"),
        "candidate_actions": list(case.get("candidate_actions", [])),
        "subject": case["subject"],
        "polarity": case["polarity"],
        "modality": case["modality"],
        "request_mode": case["request_mode"],
        "explicit_command": case["explicit_command"],
    }, case_id=str(case["id"]))

    def transport(_method, _url, _headers, _payload, _timeout):
        serialized = json.dumps(_payload, ensure_ascii=False)
        content = (
            json.dumps(
                {
                    "status": "extracted",
                    "content": "我的长期目标是完成隔离验收。",
                },
                ensure_ascii=False,
            )
            if "助手回复只是引用来源，不等于最终记忆正文" in serialized
            else json.dumps(decision, ensure_ascii=False)
        )
        return {
            "model": "isolated-provider-stub",
            "choices": [
                {
                    "message": {
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            },
        }

    return DeepSeekProvider(
        api_key="isolated-test-key",
        base_url="https://example.invalid",
        model_name="isolated-provider-stub",
        transport=transport,
    )


@pytest.mark.parametrize("case_id", acceptance.SELECTED_CASE_IDS)
def test_acceptance_runner_checks_real_shared_pipeline_with_isolated_paths(
    tmp_path,
    case_id,
):
    case = next(item for item in acceptance._load_cases() if item["id"] == case_id)

    result = acceptance._evaluate_case(
        case,
        _provider_for(case),
        tmp_path / case_id,
        700,
    )

    assert result["passed"] is True, result["failure_reasons"]
    expected_calls = int(case.get("expected_semantic_calls", 1))
    assert result["semantic_call_count"] == expected_calls
    if expected_calls:
        assert result["parsed_decision"]["intent"] == case["intent"]
        assert result["usage"]["total_tokens"] == 160
    else:
        assert result["validated_decision"]["intent"] == case["intent"]
        assert result["usage"]["total_tokens"] == 0
    expected_extraction_calls = int(
        case.get("entities", {}).get("content") == "previous_assistant_message"
    )
    assert (
        result["memory_reference_extraction_call_count"]
        == expected_extraction_calls
    )
    assert result["isolation_paths_verified"]
    assert all(
        not Path(value).is_absolute() and ".." not in Path(value).parts
        for value in result["isolation_paths_verified"]
    )


def test_acceptance_report_url_never_keeps_credentials_or_query_parameters():
    safe = acceptance._safe_base_url(
        "https://user:secret@example.com/v1/chat?api_key=hidden"
    )

    assert safe == "https://example.com/v1/chat"
    assert "secret" not in safe
    assert "api_key" not in safe
