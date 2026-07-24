import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.intent_router import IntentRouter
from modules.semantic_action_parser import SemanticActionParser


def test_interaction_golden_cases():
    cases = json.loads(
        (Path(__file__).with_name("interaction_golden_cases.json")).read_text(
            encoding="utf-8"
        )
    )
    parser = SemanticActionParser(IntentRouter())
    coordinator = InteractionStateCoordinator()
    matched = 0
    actionable = 0
    false_execution = 0
    failures = []
    for index, case in enumerate(cases):
        text = case["text"]
        if case.get("control"):
            actionable += 1
            coordinator.awaiting_confirmation(
                f"golden-{index}",
                "dangerous_tool",
                immutable_arguments={"tool_name": "delete_plan", "task_ref": "1"},
                safe_summary="测试确认",
            )
            decision = coordinator.handle_control(text, f"golden-{index}")
            ok = decision.handled or text in {"第二个", "就这个"}
        else:
            result = parser.parse(text)
            actual = [item.tool_name for item in result.candidates]
            expected = case.get("tools", [])
            if expected:
                actionable += 1
                ok = actual == expected
                if case.get("clarification"):
                    ok = ok and result.needs_clarification
            else:
                ok = actual == []
                if actual:
                    false_execution += 1
        if ok:
            matched += 1
        else:
            failures.append((text, case, actual if not case.get("control") else decision.action))

    accuracy = matched / len(cases)
    actionable_accuracy = (actionable - len(failures)) / max(1, actionable)
    assert false_execution == 0, failures
    assert accuracy >= 0.95, (accuracy, failures)
    assert actionable_accuracy >= 0.95, (actionable_accuracy, failures)


if __name__ == "__main__":
    test_interaction_golden_cases()
    print("interaction golden cases passed")
