"""Run a bounded, isolated DeepSeek acceptance set against the semantic pipeline.

This script deliberately does not load RoxyPlan's private configuration.  The API
key must be supplied through an environment variable, while every mutable
repository is constructed under a fresh temporary project root.  The generated
report contains synthetic fixture text and the provider's semantic JSON only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.intent_router import LLMIntentParser
from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.settings import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL
from v18_test_support import RecordingLLM, build_service


MATRIX_PATH = TESTS / "fixtures" / "v21_chinese_context_matrix.json"
CURRENT_CONTRACT_PATH = TESTS / "fixtures" / "v22_online_semantic_contract.json"
SELECTED_CASE_IDS = (
    "chat_002",      # 情绪表达中的“钱”
    "chat_004",      # 普通陈述中的“计划”
    "chat_005",      # 普通陈述中的“跳舞”
    "chat_007",      # 否定动作
    "chat_013",      # 引用中的命令
    "chat_014",      # 假设性询问
    "chat_017",      # 反问/反对
    "chat_020",      # 自我纠正后取消
    "chat_022",      # 显式纠正为咨询
    "chat_025",      # “你知道我……”最小负对照
    "read_001",      # 口语计划查询
    "read_010",      # 当前会话省略引用
    "read_015",      # 方言化计划查询
    "write_002",     # 礼貌桌宠动作请求
    "write_004",     # 时间和时长实体
    "write_019",     # 目标陈述 + 尾部正式记忆授权
    "write_008",     # 上一条 assistant 回复引用
    "clarify_001",   # 明确写意图但缺内容
    "multi_001",     # 明确读写多意图
    "multi_007",     # 条件表达不得立即写入
)

class CapturingOnlineChat:
    def __init__(self, provider: DeepSeekProvider, *, max_tokens: int) -> None:
        self.provider = provider
        self.max_tokens = max(128, int(max_tokens))
        self.responses = []
        self.requests: List[Dict[str, object]] = []

    def chat(self, messages, **_kwargs) -> str:
        safe_messages = [dict(item) for item in messages]
        serialized = json.dumps(safe_messages, ensure_ascii=False, sort_keys=True)
        system_text = str(safe_messages[0].get("content", "")) if safe_messages else ""
        request_kind = (
            "memory_reference_extraction"
            if "助手回复只是引用来源，不等于最终记忆正文" in system_text
            else "semantic_decision"
        )
        self.requests.append(
            {
                "request_kind": request_kind,
                "character_count": len(serialized),
                "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            }
        )
        response = self.provider.chat(
            safe_messages,
            thinking=False,
            max_tokens=self.max_tokens,
        )
        self.responses.append(response)
        return response.content if response.ok else ""


class AcceptanceConversationLLM:
    """Use the provider only for the bounded reference-extraction contract."""

    def __init__(self, online: CapturingOnlineChat) -> None:
        self.online = online
        self.fallback = RecordingLLM("隔离验收的普通聊天回复。")

    def chat(self, messages, **kwargs) -> str:
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "助手回复只是引用来源，不等于最终记忆正文" in system_text:
            return self.online.chat(messages, **kwargs)
        return self.fallback.chat(messages)


class CapturingIntentParser(LLMIntentParser):
    def __init__(self, chat_callable) -> None:
        super().__init__(chat_callable)
        self.last_parsed = None
        self.parse_calls = 0

    def parse(self, text, context=None):
        self.parse_calls += 1
        self.last_parsed = super().parse(text, context)
        return self.last_parsed


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (
        ROOT.parent
        / "Roxyplan-test-runs"
        / f"online-chinese-semantic-acceptance-{stamp}.json"
    )


def _load_cases() -> List[Dict[str, object]]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    current_contract = json.loads(CURRENT_CONTRACT_PATH.read_text(encoding="utf-8"))
    if str(current_contract.get("schema_version", "")) != "2.2":
        raise ValueError("online semantic contract must use schema_version 2.2")
    contract_cases = current_contract.get("cases", {})
    if not isinstance(contract_cases, dict):
        raise ValueError("online semantic contract cases must be an object")
    raw_cases = matrix.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ValueError("matrix cases must be a list")
    by_id = {}
    for item in raw_cases:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise ValueError("every matrix case must have an id")
        case_id = str(item["id"])
        if case_id in by_id:
            raise ValueError(f"duplicate matrix case id: {case_id}")
        by_id[case_id] = item
    missing = [case_id for case_id in SELECTED_CASE_IDS if case_id not in by_id]
    if missing:
        raise ValueError("selected matrix cases missing: " + ", ".join(missing))
    missing_contract = [
        case_id for case_id in SELECTED_CASE_IDS if case_id not in contract_cases
    ]
    if missing_contract:
        raise ValueError(
            "selected V2.2 contract cases missing: " + ", ".join(missing_contract)
        )
    selected = []
    for case_id in SELECTED_CASE_IDS:
        case = dict(by_id[case_id])
        overlay = contract_cases[case_id]
        if not isinstance(overlay, dict):
            raise ValueError(f"V2.2 contract case must be an object: {case_id}")
        case.update(overlay)
        selected.append(case)
    return selected


def _seed_virtual_state(runtime, growth, case_id: str, conversation_id: str) -> None:
    if case_id in {"read_001", "multi_001"}:
        growth.add_task("隔离验收：阅读一篇虚构论文")
    if case_id == "read_010":
        runtime.conversation_service.state_manager.observe_user(
            conversation_id,
            "隔离虚构问题：怎样验证同一窗口里的省略指代？",
        )
    if case_id == "write_008":
        runtime.conversation_service.state_manager.observe_assistant(
            conversation_id,
            "请记住：我的长期目标是完成隔离验收。"
            "你回复‘请记住’后，我再正式保存。",
        )


def _path_values(objects: Iterable[object]) -> List[Path]:
    result: List[Path] = []
    for item in objects:
        if item is None:
            continue
        for value in vars(item).values():
            if isinstance(value, Path):
                result.append(value)
    return result


def _assert_isolated_runtime(runtime, growth, memory, history, root: Path) -> List[str]:
    resolved_root = root.resolve()
    storage_objects = [
        growth,
        getattr(growth, "repository", None),
        memory,
        getattr(memory, "repository", None),
        history,
        getattr(history, "repository", None),
        getattr(runtime, "memory_candidate_manager", None),
        getattr(getattr(runtime, "memory_candidate_manager", None), "repository", None),
        getattr(runtime, "knowledge_manager", None),
        getattr(runtime.conversation_service, "interaction_diagnostics", None),
    ]
    verified = []
    for path in _path_values(storage_objects):
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise RuntimeError(f"isolated repository path escaped temporary root: {resolved}")
        verified.append(str(resolved.relative_to(resolved_root)))
    if not verified:
        raise RuntimeError("no isolated repository paths were discovered")
    return sorted(set(verified))


def _expected(case: Mapping[str, object]) -> Dict[str, object]:
    mode = str(case["mode"])
    candidate_tools = list(case.get("expected_candidates", []))
    return {
        "mode": mode,
        "intent": str(case["intent"]),
        "entities": dict(case.get("entities", {})),
        "candidate_tools": candidate_tools,
        "executed_tools": [] if mode in {"chat", "clarify"} else candidate_tools,
        "forbidden_tools": list(case.get("forbidden_tools", [])),
        "semantic_calls": int(case.get("expected_semantic_calls", 1)),
        "subject": str(case["subject"]),
        "polarity": str(case["polarity"]),
        "modality": str(case["modality"]),
        "request_mode": str(case["request_mode"]),
        "explicit_command": bool(case["explicit_command"]),
        "final_status": {
            "chat": "chat",
            "clarify": "clarification",
        }.get(mode, "completed"),
    }


def _expected_subset(expected: object, actual: object, path: str = "entities") -> List[str]:
    failures: List[str] = []
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{path} expected object"]
        for key, value in expected.items():
            if key not in actual:
                failures.append(f"{path}.{key} missing")
            else:
                failures.extend(_expected_subset(value, actual[key], f"{path}.{key}"))
        return failures
    if isinstance(expected, list):
        if expected != actual:
            failures.append(f"{path} expected={expected!r} actual={actual!r}")
        return failures
    if expected != actual:
        failures.append(f"{path} expected={expected!r} actual={actual!r}")
    return failures


def _normalized_usage(response, request_meta: Mapping[str, object]) -> Dict[str, object]:
    raw = dict(response.usage) if response is not None else {}
    input_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    output_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    total_tokens = int(raw.get("total_tokens", input_tokens + output_tokens) or 0)
    usage_available = bool(input_tokens or output_tokens or total_tokens)
    output_chars = len(str(getattr(response, "content", "") or ""))
    heuristic = max(
        1,
        (int(request_meta.get("character_count", 0)) + output_chars + 1) // 2,
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_available": usage_available,
        "approximate_total_tokens": total_tokens if usage_available else heuristic,
        "usage_scope": "final_provider_response_only",
    }


def _provider_error(response) -> Optional[Dict[str, object]]:
    if response is None or response.error is None:
        return None
    return response.error.to_dict()


def _evaluate_case(
    case: Mapping[str, object],
    provider: DeepSeekProvider,
    root: Path,
    max_tokens: int,
) -> Dict[str, object]:
    online = CapturingOnlineChat(provider, max_tokens=max_tokens)
    parser = CapturingIntentParser(online.chat)
    runtime, growth, memory, history = build_service(
        root,
        AcceptanceConversationLLM(online),
    )
    runtime.intent_router.configure_llm(parser, True)
    runtime.conversation_service.semantic_decision_compatibility_enabled = False
    verified_paths = _assert_isolated_runtime(runtime, growth, memory, history, root)
    conversation_id = "online-" + str(case["id"])
    _seed_virtual_state(runtime, growth, str(case["id"]), conversation_id)

    turn = runtime.conversation_service.prepare(
        str(case["text"]),
        conversation_id,
        record_history=False,
        allow_llm_intent=False,
    )
    response = runtime.conversation_service.complete(turn)
    semantic_indexes = [
        index
        for index, item in enumerate(online.requests)
        if item.get("request_kind") == "semantic_decision"
    ]
    extraction_indexes = [
        index
        for index, item in enumerate(online.requests)
        if item.get("request_kind") == "memory_reference_extraction"
    ]
    semantic_index = semantic_indexes[-1] if semantic_indexes else None
    provider_response = (
        online.responses[semantic_index] if semantic_index is not None else None
    )
    request_meta = (
        online.requests[semantic_index] if semantic_index is not None else {}
    )
    parsed = dict(parser.last_parsed or {})
    records = runtime.conversation_service.diagnostic_snapshot()
    diagnostic = records[-1] if records else {}
    candidate_tools = [
        str(item.get("tool_name", ""))
        for item in diagnostic.get("action_candidates", [])
        if isinstance(item, Mapping)
    ]
    executed_tools = [item.tool for item in response.tool_results]
    expected = _expected(case)
    reasons: List[str] = []
    expected_semantic_calls = int(expected["semantic_calls"])
    expected_extraction_calls = int(
        case.get("entities", {}).get("content") == "previous_assistant_message"
        if isinstance(case.get("entities"), Mapping)
        else 0
    )
    if (
        parser.parse_calls != expected_semantic_calls
        or len(semantic_indexes) != expected_semantic_calls
    ):
        reasons.append(
            "semantic_calls "
            f"expected={expected_semantic_calls} parser={parser.parse_calls} "
            f"provider={len(semantic_indexes)}"
        )
    if len(extraction_indexes) != expected_extraction_calls:
        reasons.append(
            "memory_reference_extraction_calls "
            f"expected={expected_extraction_calls} actual={len(extraction_indexes)}"
        )
    observed_decision = (
        parsed
        if expected_semantic_calls
        else dict(diagnostic.get("semantic_decision", {}))
    )
    if observed_decision.get("mode") != expected["mode"]:
        reasons.append(
            f"mode expected={expected['mode']} actual={observed_decision.get('mode')}"
        )
    if observed_decision.get("intent") != expected["intent"]:
        reasons.append(
            f"intent expected={expected['intent']} actual={observed_decision.get('intent')}"
        )
    reasons.extend(
        _expected_subset(expected["entities"], observed_decision.get("entities", {}))
    )
    if expected_semantic_calls:
        for field in (
            "subject",
            "polarity",
            "modality",
            "request_mode",
            "explicit_command",
        ):
            if observed_decision.get(field) != expected[field]:
                reasons.append(
                    f"{field} expected={expected[field]!r} "
                    f"actual={observed_decision.get(field)!r}"
                )
    if candidate_tools != expected["candidate_tools"]:
        reasons.append(
            f"candidate_tools expected={expected['candidate_tools']} actual={candidate_tools}"
        )
    if executed_tools != expected["executed_tools"]:
        reasons.append(
            f"executed_tools expected={expected['executed_tools']} actual={executed_tools}"
        )
    if response.status != expected["final_status"]:
        reasons.append(
            f"status expected={expected['final_status']} actual={response.status}"
        )
    if any(not item.success for item in response.tool_results):
        reasons.append("one_or_more_tool_results_failed")
    forbidden = set(expected["forbidden_tools"])
    if forbidden.intersection(candidate_tools) or forbidden.intersection(executed_tools):
        reasons.append("forbidden_tool_proposed_or_executed")
    if expected_semantic_calls and provider_response is None:
        reasons.append("online_semantic_call_not_observed")
    elif provider_response is not None and not provider_response.ok:
        reasons.append("provider_error=" + str(provider_response.error.code))

    return {
        "case_id": case["id"],
        "phenomenon": case["phenomenon"],
        "input_provenance": "synthetic_reviewed_fixture",
        "text": case["text"],
        "expected": expected,
        "provider": provider_response.provider if provider_response else "",
        "model": provider_response.model if provider_response else provider.model_name,
        "thinking": bool(getattr(provider_response, "thinking", False)),
        "finish_reason": str(getattr(provider_response, "finish_reason", "") or ""),
        "raw_response": provider_response.content if provider_response else "",
        "semantic_request": dict(request_meta),
        "usage": _normalized_usage(provider_response, request_meta),
        "latency_ms": int(getattr(provider_response, "latency_ms", 0) or 0),
        "provider_error": _provider_error(provider_response),
        "parser_diagnostic": parser.last_diagnostic,
        "parsed_decision": {
            "mode": parsed.get("mode"),
            "intent": parsed.get("intent"),
            "entities": parsed.get("entities", {}),
            "proposed_tool": parsed.get("proposed_tool"),
            "follow_up_target": parsed.get("follow_up_target"),
            "clarification_question": parsed.get("clarification_question"),
            "candidate_actions": parsed.get("candidate_actions", []),
            "subject": parsed.get("subject", ""),
            "polarity": parsed.get("polarity", ""),
            "modality": parsed.get("modality", ""),
            "request_mode": parsed.get("request_mode", ""),
            "explicit_command": parsed.get("explicit_command", False),
        },
        "validated_decision": diagnostic.get("semantic_decision", {}),
        "validation_notes": diagnostic.get("validation_notes", []),
        "candidate_tools": candidate_tools,
        "resolver_result": diagnostic.get("resolver_result", []),
        "executed_tools": executed_tools,
        "tool_results": [
            {
                "tool": item.tool,
                "success": item.success,
                "status": item.status,
                "error_code": item.error_code,
            }
            for item in response.tool_results
        ],
        "clarification": {
            "expected": expected["mode"] == "clarify",
            "question": parsed.get("clarification_question"),
            "final_status": response.status,
        },
        "final_response": response.message,
        "request_id": response.request_id,
        "pipeline": diagnostic.get("pipeline", []),
        "semantic_call_count": parser.parse_calls,
        "memory_reference_extraction_call_count": len(extraction_indexes),
        "isolation_paths_verified": verified_paths,
        "passed": not reasons,
        "failure_reasons": reasons,
    }


def _safe_base_url(value: str) -> str:
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if parsed.port is not None:
        host += f":{parsed.port}"
    return f"{parsed.scheme}://{host}{parsed.path}".rstrip("/")


def _summarize(results: List[Mapping[str, object]]) -> Dict[str, object]:
    return {
        "case_count": len(results),
        "passed": sum(bool(item.get("passed")) for item in results),
        "failed": sum(not bool(item.get("passed")) for item in results),
        "usage": {
            "input_tokens": sum(int(item["usage"]["input_tokens"]) for item in results),
            "output_tokens": sum(int(item["usage"]["output_tokens"]) for item in results),
            "total_tokens": sum(int(item["usage"]["total_tokens"]) for item in results),
            "approximate_total_tokens": sum(
                int(item["usage"]["approximate_total_tokens"]) for item in results
            ),
        },
    }


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _report(
    *,
    args,
    results: List[Mapping[str, object]],
    run_status: str,
    run_error: str = "",
) -> Dict[str, object]:
    summary = _summarize(results)
    return {
        "schema_version": "1.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_status": run_status,
        "run_error": run_error,
        "provider": "deepseek",
        "requested_model": str(args.model),
        "base_url": _safe_base_url(str(args.base_url)),
        "acceptance_scope": "isolated_semantic_provider_and_shared_business_pipeline",
        "isolated_virtual_data": True,
        "project_private_config_loaded": False,
        "project_private_data_loaded": False,
        **summary,
        "results": list(results),
    }


def main() -> int:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--output", type=Path, default=_default_output())
    arguments.add_argument("--base-url", default=DEFAULT_DEEPSEEK_BASE_URL)
    arguments.add_argument("--model", default=DEFAULT_DEEPSEEK_MODEL)
    arguments.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    arguments.add_argument("--max-tokens", type=int, default=700)
    arguments.add_argument("--limit", type=int, default=len(SELECTED_CASE_IDS))
    arguments.add_argument("--token-budget", type=int, default=160000)
    arguments.add_argument("--delay-seconds", type=float, default=0.2)
    args = arguments.parse_args()

    if int(args.limit) < 1:
        arguments.error("--limit must be at least 1")
    api_key = str(os.environ.get(args.api_key_env, "")).strip()
    if not api_key:
        print(
            f"online acceptance blocked: environment variable {args.api_key_env} is not set",
            file=sys.stderr,
        )
        return 2

    provider = DeepSeekProvider(
        api_key=api_key,
        base_url=str(args.base_url),
        model_name=str(args.model),
        timeout_seconds=120,
        max_retries=1,
    )
    cases = _load_cases()[: int(args.limit)]
    results: List[Mapping[str, object]] = []
    run_status = "completed"
    run_error = ""
    with tempfile.TemporaryDirectory(prefix="roxy-online-semantic-") as temp:
        base = Path(temp)
        for index, case in enumerate(cases, 1):
            print(f"[{index}/{len(cases)}] {case['id']}", flush=True)
            try:
                item = _evaluate_case(
                    case,
                    provider,
                    base / str(case["id"]),
                    args.max_tokens,
                )
            except Exception as error:  # preserve already completed evidence
                item = {
                    "case_id": case["id"],
                    "phenomenon": case.get("phenomenon", ""),
                    "input_provenance": "synthetic_reviewed_fixture",
                    "text": case.get("text", ""),
                    "passed": False,
                    "failure_reasons": [f"runner_exception:{type(error).__name__}:{error}"],
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "approximate_total_tokens": 0,
                    },
                }
            results.append(item)
            report = _report(args=args, results=results, run_status="running")
            _write_report(args.output, report)
            reasons = item.get("failure_reasons") or []
            if reasons and str(reasons[0]).startswith("provider_error="):
                run_status = "blocked"
                run_error = str(reasons[0])
                break
            used = _summarize(results)["usage"]["approximate_total_tokens"]
            if int(used) >= int(args.token_budget):
                run_status = "partial"
                run_error = "token_budget_reached"
                break
            if index < len(cases):
                time.sleep(max(0.0, float(args.delay_seconds)))

    final_report = _report(
        args=args,
        results=results,
        run_status=run_status,
        run_error=run_error,
    )
    _write_report(args.output, final_report)
    print(
        json.dumps(
            {
                key: final_report[key]
                for key in ("run_status", "case_count", "passed", "failed", "usage")
            },
            ensure_ascii=False,
        )
    )
    print(f"report={args.output.resolve()}")
    return 0 if run_status == "completed" and final_report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
