import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from v18_test_support import NoCallLLM, RecordingLLM, build_service


def test_model_style_plan_wish_is_routed_to_choice_after_validation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        candidate = ActionCandidate(
            domain="plan",
            tool_name="add_plan",
            arguments={"title": "cosplay"},
            request_mode="possible_action",
            confidence=0.92,
            explicit_command=False,
            source_intent="add_plan",
        )
        semantic = SemanticParseResult(
            source="fake_model",
            candidates=[candidate],
            request_mode="possible_action",
            plain_chat_probability=0.0,
            intent_result={
                "intent": "add_plan",
                "confidence": 0.92,
                "entities": {"title": "cosplay"},
                "needs_confirmation": False,
                "source": "fake_model",
                "request_mode": "possible_action",
                "subject": "self",
                "polarity": "positive",
                "modality": "desire",
                "request_id": "req_fake_plan_wish",
                "clarification_question": None,
            },
        )
        parser = service.conversation_service.semantic_action_parser
        parser.parse = lambda *args, **kwargs: semantic
        parser.parse_unified = lambda *args, **kwargs: semantic

        response = service.handle("我想学 cosplay", "wish-model")

        assert response.status == "clarification"
        assert growth.tasks() == []
        state = service.interaction_coordinator.current("wish-model")
        assert state.interaction_kind == "advice_or_action_choice"
        assert state.known_fields["title"] == "cosplay"
        assert "今天计划" in response.message


def test_canonical_execute_add_plan_is_not_reinterpreted_by_local_chinese_template():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        candidate = ActionCandidate(
            domain="plan",
            tool_name="add_plan",
            arguments={"title": "整理发布记录"},
            request_mode="execute",
            confidence=0.7,
            # Reproduce the inconsistent annotation observed in the real
            # diagnostic: execute was canonical, while this hint was false.
            explicit_command=False,
            source_intent="add_plan",
        )
        semantic = SemanticParseResult(
            source="llm",
            candidates=[candidate],
            request_mode="execute",
            plain_chat_probability=0.0,
            intent_result={
                "intent": "add_plan",
                "confidence": 0.7,
                "entities": dict(candidate.arguments),
                "needs_confirmation": False,
                "source": "llm",
                "mode": "write",
                "proposed_tool": "add_plan",
                "request_mode": "execute",
                "explicit_command": False,
                "subject": "self",
                "polarity": "positive",
                "modality": "commitment",
                "request_id": "req_non_template_execute",
                "clarification_question": None,
            },
        )
        parser = service.conversation_service.semantic_action_parser
        parser.parse = lambda *args, **kwargs: semantic
        parser.parse_unified = lambda *args, **kwargs: semantic

        response = service.handle(
            "把整理发布记录列入我的长期计划",
            "non-template-execute",
        )

        assert response.status == "completed"
        assert [item.tool for item in response.tool_results] == ["add_plan"]
        assert [item["title"] for item in growth.tasks()] == ["整理发布记录"]
        assert not service.interaction_coordinator.current(
            "non-template-execute"
        ).pending


def test_explicit_desire_to_add_plan_is_not_intercepted_as_a_wish():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        response = service.handle("我想把 cosplay 加入今天计划", "explicit-wish")

        assert response.status == "completed"
        assert len(growth.tasks()) == 1
        assert "cosplay" in growth.tasks()[0]["title"].lower()
        assert not service.interaction_coordinator.current("explicit-wish").pending


def test_model_annotated_explicit_desire_executes_without_extra_confirmation():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        candidate = ActionCandidate(
            domain="plan",
            tool_name="add_plan",
            arguments={"title": "cosplay"},
            request_mode="execute",
            confidence=0.95,
            explicit_command=True,
            source_intent="add_plan",
        )
        semantic = SemanticParseResult(
            source="fake_model",
            candidates=[candidate],
            request_mode="execute",
            plain_chat_probability=0.0,
            intent_result={
                "intent": "add_plan",
                "confidence": 0.95,
                "entities": {"title": "cosplay"},
                "needs_confirmation": False,
                "source": "llm",
                "mode": "write",
                "proposed_tool": "add_plan",
                "subject": "self",
                "polarity": "positive",
                "modality": "desire",
                "request_id": "req_fake_explicit_desire",
                "clarification_question": None,
            },
        )
        parser = service.conversation_service.semantic_action_parser
        parser.parse = lambda *args, **kwargs: semantic
        parser.parse_unified = lambda *args, **kwargs: semantic

        response = service.handle(
            "我想把 cosplay 加入今天计划",
            "explicit-model-desire",
        )

        assert response.status == "completed"
        assert len(growth.tasks()) == 1
        assert growth.tasks()[0]["title"] == "cosplay"


def test_advice_and_wish_do_not_write_until_explicit_choice():
    with tempfile.TemporaryDirectory() as temp:
        llm = RecordingLLM("可以先了解基础服装结构，再选一个小配件练习。")
        service, growth, _memory, _history = build_service(Path(temp), llm)

        advice = service.handle("帮我安排一下怎么学 cosplay", "advice")
        assert advice.status == "chat"
        assert growth.tasks() == []

        wish = service.handle("我想学 cosplay", "wish")
        assert wish.status == "clarification"
        assert growth.tasks() == []

        added = service.handle("加入今天计划", "wish")
        assert added.status == "completed"
        assert len(growth.tasks()) == 1
        assert "cosplay" in growth.tasks()[0]["title"].lower()
        assert growth.tasks()[0]["duration_minutes"] is None


def test_short_add_plan_choice_preserves_pending_target_and_schedule():
    for choice in ("加入计划", "加入今天计划", "直接加入"):
        with tempfile.TemporaryDirectory() as temp:
            service, growth, _memory, _history = build_service(
                Path(temp), NoCallLLM()
            )
            candidate = ActionCandidate(
                domain="plan",
                tool_name="add_plan",
                arguments={
                    "title": "学习数学",
                    "time_slot": "晚上",
                    "duration_minutes": 45,
                },
                request_mode="possible_action",
                confidence=0.92,
                explicit_command=False,
                source_intent="add_plan",
            )
            semantic = SemanticParseResult(
                source="fake_model",
                candidates=[candidate],
                request_mode="possible_action",
                plain_chat_probability=0.0,
                intent_result={
                    "intent": "add_plan",
                    "confidence": 0.92,
                    "entities": dict(candidate.arguments),
                    "source": "fake_model",
                    "request_id": "req_math_wish",
                },
            )
            parser = service.conversation_service.semantic_action_parser
            parser.parse = lambda *args, **kwargs: semantic
            parser.parse_unified = lambda *args, **kwargs: semantic
            service.conversation_service.interaction_diagnostics.enabled = True

            pending = service.handle("我今天晚上想学数学", "math-choice")
            added = service.handle(choice, "math-choice")

            assert pending.status == "clarification"
            assert added.status == "completed"
            task = growth.tasks()[0]
            assert task["title"] == "学习数学"
            assert task["time_slot"] == "晚上"
            assert task["duration_minutes"] == 45
            diagnostic = service.diagnostic_snapshot()[-1]
            assert diagnostic["interaction_state_before"] == "awaiting_choice"
            assert diagnostic["coordinator_decision"] == "pending_continuation"


def test_unrelated_message_cancels_pending_wish_instead_of_reusing_it():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(
            Path(temp), RecordingLLM("你好呀，今天想聊点什么？")
        )

        wish = service.handle("我想学 cosplay", "wish-topic-change")
        assert wish.status == "clarification"

        greeting = service.handle("你好", "wish-topic-change")
        assert greeting.status == "chat"
        assert "你好" in greeting.message
        assert growth.tasks() == []
        assert not service.interaction_coordinator.current("wish-topic-change").pending


def test_missing_slots_keep_time_and_accept_followup():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        first = service.handle("我下午想学一会儿", "slots")
        assert first.status == "clarification"
        second = service.handle("特征工程，一个小时", "slots")
        assert second.status == "completed"
        task = growth.tasks()[0]
        assert "特征工程" in task["title"]
        assert task["time_slot"] == "下午"
        assert task["duration_minutes"] == 60


def test_advice_choice_can_be_selected_in_a_later_turn():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        service.handle("我下午想学一会儿", "advice-choice")
        suggestions = service.handle("不知道，你给我安排一下可以吗", "advice-choice")
        assert suggestions.status == "clarification"
        assert "2." in suggestions.message
        added = service.handle("第二个，一个小时", "advice-choice")
        assert added.status == "completed"
        task = growth.tasks()[0]
        assert "主程序" in task["title"]
        assert task["time_slot"] == "下午"
        assert task["duration_minutes"] == 60


if __name__ == "__main__":
    test_advice_and_wish_do_not_write_until_explicit_choice()
    test_missing_slots_keep_time_and_accept_followup()
    test_advice_choice_can_be_selected_in_a_later_turn()
    print("advice action boundary tests passed")
