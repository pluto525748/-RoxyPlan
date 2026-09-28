import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.intent_router import IntentRouter
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.semantic_action_parser import SemanticActionParser


MEMORY_CANDIDATE_TOOLS = {
    "list_memory_candidates",
    "accept_memory_candidates",
    "reject_memory_candidates",
}


def _load_cases():
    return json.loads(
        (Path(__file__).with_name("interaction_golden_cases.json")).read_text(
            encoding="utf-8"
        )
    )


def test_interaction_golden_cases():
    cases = [
        case for case in _load_cases() if not case.get("retired_from_main_chat")
    ]
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


def test_retired_memory_candidate_cases_are_auditable_but_not_in_current_contract():
    retired_cases = [
        case for case in _load_cases() if case.get("retired_from_main_chat")
    ]
    assert len(retired_cases) == 4

    for case in retired_cases:
        assert case["tools"] == []
        assert not set(case["tools"]) & MEMORY_CANDIDATE_TOOLS
        assert case["legacy_tools"]
        assert set(case["legacy_tools"]).issubset(MEMORY_CANDIDATE_TOOLS)

        # The legacy parser/service code remains for file compatibility, but
        # retired candidate tools are absent from the current model-visible
        # capability surface and are intercepted by the main chat service.
        for tool_name in case["legacy_tools"]:
            definition = DEFAULT_CAPABILITY_REGISTRY.for_tool(tool_name)
            assert definition is not None
            assert definition.model_visible is False


if __name__ == "__main__":
    test_interaction_golden_cases()
    print("interaction golden cases passed")
