import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.experiment_ollama_tool_calling import analyze_response, run_experiment


def test_ollama_tool_call_shape_is_normalized():
    raw = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "add_plan",
                        "arguments": {
                            "title": "study machine learning",
                            "duration_minutes": 50,
                        },
                    }
                }
            ],
        },
        "done_reason": "stop",
    }
    result = analyze_response(raw, ["add_plan"])
    assert result["tool_names"] == ["add_plan"]
    assert result["tool_calls"][0]["arguments"]["duration_minutes"] == 50


def test_invented_tool_is_reported():
    raw = {
        "message": {
            "tool_calls": [
                {"function": {"name": "run_shell", "arguments": {}}}
            ]
        }
    }
    result = analyze_response(raw, ["add_plan"])
    assert result["invented_tools"] == ["run_shell"]


def test_invalid_argument_payload_is_reported():
    raw = {
        "message": {
            "tool_calls": [
                {"function": {"name": "add_plan", "arguments": ["bad"]}}
            ]
        }
    }
    result = analyze_response(raw, ["add_plan"])
    assert result["invalid_argument_count"] == 1


def test_real_ollama_contract_is_opt_in():
    if os.getenv("ROXY_RUN_OLLAMA_TOOL_EXPERIMENT") != "1":
        return
    result = run_experiment(
        url="http://127.0.0.1:11434/api/chat",
        model=os.getenv("ROXY_OLLAMA_TEST_MODEL", "qwen3:4b"),
        cases=[
            {
                "id": "explicit_add",
                "prompt": "\u628a\u5b66\u4e60\u673a\u5668\u5b66\u4e6050\u5206\u949f\u52a0\u5165\u8ba1\u5212",
                "expected": ["add_plan"],
            }
        ],
        repeats=1,
        num_predict=800,
        timeout=120,
    )
    assert result["completed_count"] == 1
    assert result["invented_tool_count"] == 0


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"Ollama tool contract tests passed ({len(TESTS)} cases)")
