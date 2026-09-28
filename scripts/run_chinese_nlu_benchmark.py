from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.intent_router import IntentRouter
from modules.semantic_action_parser import SemanticActionParser


BENCHMARK = ROOT / "tests" / "nlu_benchmark"


def load_jsonl(path: Path) -> List[Dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_frozen_test() -> None:
    expected = (BENCHMARK / "final_test.sha256").read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256((BENCHMARK / "test.jsonl").read_bytes()).hexdigest()
    if expected != actual:
        raise RuntimeError("Frozen final test hash mismatch")


def classify_actual(result) -> tuple[str, str, List[str], str]:
    intent = str(result.intent_result.get("intent", "chat"))
    tools = [item.tool_name for item in result.candidates]
    if tools:
        capability = DEFAULT_CAPABILITY_REGISTRY.for_tool(tools[0])
        capability_id = capability.capability_id if capability else "unknown"
    elif result.request_mode == "advice":
        capability_id = "advice_request"
    else:
        capability_id = "ordinary_chat"
    return capability_id, result.request_mode, tools, intent


def evaluate(split: str, *, include_failures: bool = True) -> Dict[str, object]:
    rows = load_jsonl(BENCHMARK / f"{split}.jsonl")
    router = IntentRouter(enable_llm=False)
    parser = SemanticActionParser(router, enabled=True)
    counts = Counter()
    errors = Counter()
    capability_totals = Counter()
    capability_passed = Counter()
    failed_families = Counter()
    examples = []
    query_cases = 0
    query_tool_passed = 0
    for row in rows:
        if row.get("needs_review"):
            continue
        # IntentRouter emits useful interactive diagnostics. Keep benchmark output
        # machine-readable without changing runtime logging behaviour.
        with contextlib.redirect_stdout(io.StringIO()):
            result = parser.parse(
                str(row["text"]),
                dict(row.get("conversation_context", {})),
                allow_llm=False,
            )
        capability, request_mode, tools, intent = classify_actual(result)
        expected_tools = list(row.get("expected_tool_names", []))
        forbidden = set(row.get("forbidden_tools", []))
        expected_intents = set(row.get("expected_intents", []))
        checks = {
            "capability": capability == row["capability_id"],
            "request_mode": request_mode == row["request_mode"],
            "tool": tools == expected_tools,
            "action_count": len(tools) == int(row["expected_action_count"]),
            "intent": intent in expected_intents,
            "negative_safety": not bool(forbidden.intersection(tools)),
            "false_write": bool(row["side_effect_allowed"]) or not any(
                definition.side_effect
                for tool in tools
                if (definition := DEFAULT_CAPABILITY_REGISTRY.for_tool(tool))
                is not None
            ),
        }
        expected_capability = str(row["capability_id"])
        capability_totals[expected_capability] += 1
        capability_passed[expected_capability] += int(checks["capability"])
        if row.get("request_mode") == "query":
            query_cases += 1
            query_tool_passed += int(checks["tool"])
        counts["cases"] += 1
        for name, passed in checks.items():
            counts[f"{name}_passed"] += int(passed)
            if not passed:
                errors[name] += 1
        if not checks["capability"]:
            failed_families[str(row["semantic_family_id"])] += 1
        if include_failures and not all(checks.values()) and len(examples) < 50:
            examples.append(
                {
                    "case_id": row["case_id"],
                    "style": row["style"],
                    "expected_capability": row["capability_id"],
                    "actual_capability": capability,
                    "expected_mode": row["request_mode"],
                    "actual_mode": request_mode,
                    "expected_tools": expected_tools,
                    "actual_tools": tools,
                    "failed_checks": [name for name, passed in checks.items() if not passed],
                }
            )
    total = max(1, counts["cases"])
    metrics = {
        name: round(counts[f"{name}_passed"] / total, 6)
        for name in ("capability", "request_mode", "tool", "action_count", "intent", "negative_safety", "false_write")
    }
    metrics["explicit_query_tool_accuracy"] = round(
        query_tool_passed / max(1, query_cases), 6
    )
    report = {
        "schema_version": "1.0",
        "split": split,
        "scope": "CapabilityRegistry 已有功能闭集",
        "evaluated_cases": counts["cases"],
        "metrics": metrics,
        "error_categories": dict(errors),
        "capability_accuracy_by_label": {
            name: round(capability_passed[name] / total_for_label, 6)
            for name, total_for_label in sorted(capability_totals.items())
        },
        "failed_semantic_families": (
            dict(failed_families.most_common()) if include_failures else {}
        ),
        "failure_examples": examples if include_failures else [],
        "frozen_test_details_hidden": not include_failures,
    }
    return report


def main() -> int:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    arguments.add_argument("--report", type=Path)
    arguments.add_argument("--frozen-test", action="store_true")
    args = arguments.parse_args()
    if args.split == "test" or args.frozen_test:
        verify_frozen_test()
    report = evaluate(args.split, include_failures=not args.frozen_test)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
