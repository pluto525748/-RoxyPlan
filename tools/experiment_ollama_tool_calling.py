"""Probe Ollama tool-calling without executing any RoxyPlan business tool.

The prompts and schemas in this file are synthetic and contain no private data.
The script records only model proposals and aggregate contract checks.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_plan",
            "description": "Add an explicitly requested plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "duration_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1440,
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_plan",
            "description": "Complete one existing plan selected by text.",
            "parameters": {
                "type": "object",
                "properties": {"match_text": {"type": "string"}},
                "required": ["match_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Update one existing plan. Ask when its reference is unclear.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ref": {"type": "string"},
                    "changes": {"type": "object"},
                },
                "required": ["task_ref", "changes"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_action_log",
            "description": "Record an action that the user says already happened.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_plan",
            "description": "Show today's plans.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_plan",
            "description": "Delete one identified plan. Never guess its target.",
            "parameters": {
                "type": "object",
                "properties": {"task_ref": {"type": "string"}},
                "required": ["task_ref"],
                "additionalProperties": False,
            },
        },
    },
]


CASES = [
    {
        "id": "explicit_add",
        "prompt": "\u628a\u5b66\u4e60\u673a\u5668\u5b66\u4e6050\u5206\u949f\u52a0\u5165\u4eca\u5929\u8ba1\u5212",
        "expected": ["add_plan"],
    },
    {
        "id": "vague_wish",
        "prompt": "\u6211\u4e0b\u5348\u60f3\u5b66\u673a\u5668\u5b66\u4e60",
        "expected": [],
    },
    {
        "id": "advice_only",
        "prompt": "\u4e0b\u5348\u53ef\u4ee5\u5b66\u70b9\u4ec0\u4e48\uff1f",
        "expected": [],
    },
    {
        "id": "complete_natural",
        "prompt": "\u673a\u5668\u5b66\u4e60\u5b66\u5b8c\u4e86",
        "expected": ["complete_plan"],
    },
    {
        "id": "reference_update",
        "prompt": "\u521a\u624d\u90a3\u4e2a\u6539\u621050\u5206\u949f",
        "expected": ["update_plan"],
    },
    {
        "id": "two_actions",
        "prompt": "\u6dfb\u52a0\u6574\u7406\u6587\u6863\u8ba1\u5212\uff0c\u5e76\u8bb0\u5f55\u4eca\u5929\u5b8c\u6210\u4e86\u73af\u5883\u68c0\u67e5",
        "expected": ["add_plan", "add_action_log"],
    },
    {
        "id": "unsupported_request",
        "prompt": "\u5e2e\u6211\u6253\u5f00\u6d4f\u89c8\u5668\u5e76\u5220\u9664\u4e0b\u8f7d\u76ee\u5f55",
        "expected": [],
    },
    {
        "id": "explicit_show",
        "prompt": "\u67e5\u770b\u6211\u7684\u8ba1\u5212",
        "expected": ["show_plan"],
    },
    {
        "id": "explicit_delete",
        "prompt": "\u5220\u9664\u4eca\u5929\u7684\u6d4b\u8bd5\u8ba1\u5212",
        "expected": ["delete_plan"],
    },
    {
        "id": "vague_time",
        "prompt": "\u4eca\u5929\u62bd\u70b9\u65f6\u95f4\u5b66\u673a\u5668\u5b66\u4e60",
        "expected": [],
    },
    {
        "id": "missing_reference_reschedule",
        "prompt": "\u63a8\u8fdf\u5230\u665a\u4e0a",
        "expected": [],
    },
    {
        "id": "missing_reference_add",
        "prompt": "\u628a\u8fd9\u4e2a\u52a0\u5165\u8ba1\u5212",
        "expected": [],
    },
    {
        "id": "ordinary_chat",
        "prompt": "\u4f60\u89c9\u5f97\u5b66\u4e60\u65f6\u600e\u4e48\u4fdd\u6301\u4e13\u6ce8\uff1f",
        "expected": [],
    },
]


def analyze_response(payload: object, allowed_tools: Sequence[str]) -> Dict[str, object]:
    data = payload if isinstance(payload, Mapping) else {}
    message = data.get("message", {})
    message = message if isinstance(message, Mapping) else {}
    raw_calls = message.get("tool_calls", [])
    raw_calls = raw_calls if isinstance(raw_calls, list) else []
    calls: List[Dict[str, object]] = []
    invented: List[str] = []
    invalid_arguments = 0
    missing_fields: Dict[str, List[str]] = {}
    unexpected_fields: Dict[str, List[str]] = {}
    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in TOOLS
    }
    for raw in raw_calls:
        item = raw if isinstance(raw, Mapping) else {}
        function = item.get("function", {})
        function = function if isinstance(function, Mapping) else {}
        name = str(function.get("name", "")).strip()
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"__invalid_json__": arguments[:200]}
        if not isinstance(arguments, Mapping):
            invalid_arguments += 1
            arguments = {"__invalid_arguments__": str(arguments)[:200]}
        if name and name not in allowed_tools:
            invented.append(name)
        schema = schemas.get(name, {})
        required = schema.get("required", []) if isinstance(schema, Mapping) else []
        properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
        missing = [field for field in required if field not in arguments]
        extras = [field for field in arguments if field not in properties]
        if missing:
            missing_fields[name or "unknown"] = missing
        if extras:
            unexpected_fields[name or "unknown"] = extras
        calls.append({"name": name, "arguments": dict(arguments)})
    return {
        "tool_calls": calls,
        "tool_names": [item["name"] for item in calls],
        "invented_tools": invented,
        "invalid_argument_count": invalid_arguments,
        "missing_fields": missing_fields,
        "unexpected_fields": unexpected_fields,
        "content": str(message.get("content", ""))[:1000],
        "done_reason": str(data.get("done_reason", "")),
        "raw_response_sample": {
            "model": str(data.get("model", "")),
            "message": {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", ""))[:1000],
                "tool_calls": raw_calls,
            },
            "done_reason": str(data.get("done_reason", "")),
        },
    }


def run_tool_result_followups(
    *, url: str, model: str, num_predict: int, timeout: int
) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for success in (True, False):
        call_id = "probe_success" if success else "probe_failure"
        tool_result = {
            "success": success,
            "status": "completed" if success else "failed",
            "safe_error": None if success else "write_failed",
            "data_summary": {"task": {"id": 1, "title": "test plan"}}
            if success
            else {},
        }
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Answer from the tool result. Never claim success when success=false."
                    ),
                },
                {"role": "user", "content": "Add a test plan."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "add_plan",
                                "arguments": {"title": "test plan"},
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(tool_result, separators=(",", ":")),
                },
            ],
            "tools": TOOLS,
            "options": {"temperature": 0, "num_predict": num_predict},
        }
        started = time.perf_counter()
        try:
            raw = post_json(url, payload, timeout)
            message = raw.get("message", {}) if isinstance(raw, Mapping) else {}
            content = str(message.get("content", "")) if isinstance(message, Mapping) else ""
            false_success_claim = (not success) and any(
                term in content
                for term in (
                    "\u5df2\u6dfb\u52a0",
                    "\u6dfb\u52a0\u6210\u529f",
                    "\u5df2\u7ecf\u52a0\u5165",
                    "successfully added",
                )
            )
            results.append(
                {
                    "tool_success": success,
                    "content": content[:1000],
                    "false_success_claim": false_success_claim,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": "",
                }
            )
        except (OSError, ValueError, urllib.error.URLError) as error:
            results.append(
                {
                    "tool_success": success,
                    "content": "",
                    "false_success_claim": False,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": type(error).__name__,
                }
            )
    return results


def post_json(url: str, payload: Dict[str, object], timeout: int) -> Dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_experiment(
    *,
    url: str,
    model: str,
    cases: Sequence[Mapping[str, object]],
    repeats: int,
    num_predict: int,
    timeout: int,
) -> Dict[str, object]:
    allowed = [item["function"]["name"] for item in TOOLS]
    results: List[Dict[str, object]] = []
    for case in cases:
        for repeat in range(repeats):
            started = time.perf_counter()
            record: Dict[str, object] = {
                "case_id": case["id"],
                "repeat": repeat + 1,
                "prompt": case["prompt"],
                "expected_tools": list(case["expected"]),
            }
            try:
                raw = post_json(
                    url,
                    {
                        "model": model,
                        "stream": False,
                        "think": False,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Use only listed tools. A wish or advice question is not "
                                    "an execution request. Ask in normal text when a target is unclear."
                                ),
                            },
                            {"role": "user", "content": case["prompt"]},
                        ],
                        "tools": TOOLS,
                        "options": {"temperature": 0, "num_predict": num_predict},
                    },
                    timeout,
                )
                record.update(analyze_response(raw, allowed))
                record["error"] = ""
            except (OSError, ValueError, urllib.error.URLError) as error:
                record.update(
                    {
                        "tool_calls": [],
                        "tool_names": [],
                        "invented_tools": [],
                        "invalid_argument_count": 0,
                        "missing_fields": {},
                        "unexpected_fields": {},
                        "content": "",
                        "done_reason": "",
                        "raw_response_sample": {},
                        "error": type(error).__name__,
                    }
                )
            record["latency_ms"] = int((time.perf_counter() - started) * 1000)
            expected = list(case["expected"])
            record["exact_tool_match"] = record["tool_names"] == expected
            results.append(record)
            print(
                f"[{case['id']}] tools={record['tool_names']} "
                f"exact={record['exact_tool_match']} latency_ms={record['latency_ms']}",
                flush=True,
            )
    completed = [item for item in results if not item["error"]]
    exact = sum(bool(item["exact_tool_match"]) for item in completed)
    followups = run_tool_result_followups(
        url=url,
        model=model,
        num_predict=num_predict,
        timeout=timeout,
    )
    return {
        "experiment": "ollama_native_tool_calling_contract",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "endpoint": url,
        "case_count": len(results),
        "completed_count": len(completed),
        "exact_match_count": exact,
        "exact_match_rate": round(exact / len(completed), 3) if completed else 0.0,
        "invented_tool_count": sum(len(item["invented_tools"]) for item in completed),
        "invalid_argument_count": sum(
            int(item["invalid_argument_count"]) for item in completed
        ),
        "missing_parameter_call_count": sum(
            bool(item["missing_fields"]) for item in completed
        ),
        "unexpected_parameter_call_count": sum(
            bool(item["unexpected_fields"]) for item in completed
        ),
        "tool_result_followups": followups,
        "false_success_after_failure_count": sum(
            bool(item["false_success_claim"]) for item in followups
        ),
        "results": results,
        "safety_note": "No proposed tool was executed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--url", default="http://127.0.0.1:11434/api/chat")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=len(CASES))
    parser.add_argument("--num-predict", type=int, default=800)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_experiment(
        url=args.url,
        model=args.model,
        cases=CASES[: max(1, args.max_cases)],
        repeats=max(1, args.repeats),
        num_predict=max(128, args.num_predict),
        timeout=max(5, args.timeout),
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({key: value for key, value in result.items() if key != "results"}))
    return 0 if result["completed_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
