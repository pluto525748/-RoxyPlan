import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.context_builder import ContextBuilder
from modules.intent_router import IntentRouter, LLMIntentParser
from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from v18_test_support import RecordingLLM, build_service


class StubSemanticParser:
    """Deterministic semantic decision double; it never calls an online model."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, text, context, *, allow_llm=False):
        self.calls.append((text, dict(context), allow_llm))
        return self.result(text)


class StructuredIntentStub:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)


def _semantic_result(text, *, mode, intent, tool_name="", arguments=None):
    decision = {
        "mode": mode,
        "intent": intent,
        "entities": dict(arguments or {}),
        "proposed_tool": tool_name or None,
        "confidence": 0.92,
        "follow_up_target": None,
        "needs_confirmation": False,
        "source": "stub_semantic",
    }
    candidates = []
    if tool_name:
        candidates = [
            ActionCandidate(
                domain="plan" if "plan" in tool_name else "memory",
                tool_name=tool_name,
                arguments=dict(arguments or {}),
                request_mode="query" if mode == "read" else "execute",
                confidence=0.92,
                source_intent=intent,
            )
        ]
    return SemanticParseResult(
        source="stub_semantic",
        candidates=candidates,
        request_mode="query" if mode == "read" else "discuss" if mode == "chat" else "execute",
        intent_result=decision,
    )


def test_broad_natural_language_reaches_structured_semantic_decision():
    stub = StructuredIntentStub(
        {
            "mode": "read",
            "intent": "show_plan",
            "entities": {},
            "proposed_tool": "show_plan",
            "confidence": 0.95,
            "follow_up_target": None,
        }
    )
    router = IntentRouter(LLMIntentParser(stub.chat), enable_llm=True)

    result = router.route("我今天还有什么没完成？")

    assert len(stub.calls) == 1
    assert result["source"] == "llm"
    assert result["mode"] == "read"
    assert result["proposed_tool"] == "show_plan"


def test_read_decision_executes_only_read_tool_and_never_creates_pending():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        growth.add_task("复习随机森林")
        stub = StubSemanticParser(
            lambda text: _semantic_result(text, mode="read", intent="show_plan", tool_name="show_plan")
        )
        service.conversation_service.semantic_action_parser = stub

        response = service.handle("我今天还有什么没完成？", "read-only")

        assert stub.calls and stub.calls[0][0] == "我今天还有什么没完成？"
        assert response.status == "completed"
        assert [item.tool for item in response.tool_results] == ["show_plan"]
        state = service.interaction_coordinator.current("read-only")
        assert state.state not in {"awaiting_confirmation", "awaiting_clarification"}
        assert [item["title"] for item in growth.tasks()] == ["复习随机森林"]


def test_goal_read_decision_filters_out_non_goal_memories():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, memory, _history = build_service(Path(temp), RecordingLLM())
        memory.add_memory("我的长期目标是完成洛琪希项目", category="goal")
        memory.add_memory("我的肠胃比较敏感", category="health_lifestyle")
        stub = StubSemanticParser(
            lambda text: _semantic_result(
                text,
                mode="read",
                intent="show_memory",
                tool_name="list_memories",
                arguments={"category": "goal"},
            )
        )
        service.conversation_service.semantic_action_parser = stub

        response = service.handle("你还记得我的长期目标吗？", "goal-read")

        result = next(item for item in response.tool_results if item.tool == "list_memories")
        assert [item["content"] for item in result.data["memories"]] == ["我的长期目标是完成洛琪希项目"]
        assert service.interaction_coordinator.current("goal-read").state not in {
            "awaiting_confirmation", "awaiting_clarification"
        }


def test_unrelated_chat_cancels_pending_silently_and_is_not_consumed_as_parameter():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM("普通聊天回复"))
        service.interaction_coordinator.awaiting_clarification(
            "pending-chat",
            "missing_slots",
            domain="plan",
            missing_fields=["duration_minutes"],
            known_fields={"title": "学习机器学习"},
            immutable_arguments={"tool_name": "add_plan", "title": "学习机器学习"},
        )
        stub = StubSemanticParser(
            lambda text: _semantic_result(text, mode="chat", intent="chat")
        )
        service.conversation_service.semantic_action_parser = stub

        response = service.handle("我怕我没有钱", "pending-chat")

        assert stub.calls and stub.calls[0][0] == "我怕我没有钱"
        assert response.status == "chat"
        assert "刚才等待处理的操作已取消" not in response.message
        assert service.interaction_coordinator.current("pending-chat").pending is False
        assert growth.tasks() == []


def test_only_a_duration_shaped_reply_is_compatible_with_a_duration_pending_field():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        state = service.interaction_coordinator.awaiting_clarification(
            "duration",
            "missing_slots",
            domain="plan",
            missing_fields=["duration_minutes"],
            known_fields={"title": "学习机器学习"},
            immutable_arguments={"tool_name": "add_plan", "title": "学习机器学习"},
        )

        assert service.conversation_service._is_pending_input_compatible(state, "半小时")
        for text in ("你好", "你确定吗", "我怕我没有钱", "逻辑回归是什么"):
            assert not service.conversation_service._is_pending_input_compatible(state, text)


def test_no_model_fallback_never_turns_plan_status_questions_into_writes():
    router = IntentRouter(enable_llm=False)

    for text in ("我今天还有什么没完成？", "哪些已经做完了？", "我今天是不是已经学完机器学习了？"):
        result = router.route(text)
        assert result["intent"] == "show_plan"
        assert result["mode"] == "read"


def test_read_follow_up_rechecks_the_same_read_tool_without_writing():
    from modules.contracts import ToolResult

    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        calls = []
        latest = [[{"id": 1, "title": "复习随机森林", "done": False}]]

        def fake_show_plan(**arguments):
            calls.append(dict(arguments))
            return ToolResult(
                True,
                "show_plan",
                "listed",
                data={"tasks": list(latest[0])},
                display_message="计划已读取。",
            )

        service.tool_registry.get("show_plan").handler = fake_show_plan
        service.conversation_service.semantic_action_parser = StubSemanticParser(
            lambda text: _semantic_result(
                text, mode="read", intent="show_plan", tool_name="show_plan"
            )
        )

        first = service.handle("我今天还有什么没完成？", "read-follow-up")
        latest[0] = [{"id": 2, "title": "补充线性代数", "done": False}]
        second = service.handle("你确定吗？", "read-follow-up")

        assert first.status == second.status == "completed"
        assert calls == [{}, {}]
        assert second.tool_results[0].data["tasks"][0]["title"] == "补充线性代数"
        state = service.interaction_coordinator.current("read-follow-up")
        assert state.last_read_mode == "read"
        assert state.last_read_tool == "show_plan"
        assert state.state not in {"awaiting_confirmation", "awaiting_clarification"}


def test_read_follow_up_after_chat_stays_on_the_chat_semantic_path():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(
            Path(temp), RecordingLLM("普通聊天回复")
        )
        stub = StubSemanticParser(
            lambda text: _semantic_result(text, mode="chat", intent="chat")
        )
        service.conversation_service.semantic_action_parser = stub

        service.handle("你好", "chat-follow-up")
        response = service.handle("你确定吗？", "chat-follow-up")

        assert [item[0] for item in stub.calls] == ["你好", "你确定吗？"]
        assert response.status == "chat"
        assert response.tool_results == []
        assert service.interaction_coordinator.current("chat-follow-up").pending is False


def test_new_conversation_cannot_reuse_another_window_read_result():
    from modules.contracts import ToolResult

    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        calls = []

        def fake_show_plan(**arguments):
            calls.append(dict(arguments))
            return ToolResult(True, "show_plan", "listed", data={"tasks": []})

        service.tool_registry.get("show_plan").handler = fake_show_plan
        service.conversation_service.semantic_action_parser = StubSemanticParser(
            lambda text: _semantic_result(
                text, mode="read", intent="show_plan", tool_name="show_plan"
            )
            if text == "我今天还有什么没完成？"
            else _semantic_result(text, mode="chat", intent="chat")
        )

        service.handle("我今天还有什么没完成？", "old-read-window")
        response = service.handle("你确定吗？", "new-read-window")

        assert calls == [{}]
        assert response.status == "chat"
        assert response.tool_results == []
        assert service.interaction_coordinator.last_read_result("new-read-window") is None


def test_real_reply_chain_renders_successful_show_plan_for_query_variants():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        growth.add_task("完成洛琪希计划")

        for index, text in enumerate(
            ("今天还有什么计划", "我今天还有什么计划", "我今天还有什么没完成？")
        ):
            response = service.handle(text, f"show-plan-{index}")

            assert response.tool_results[0].tool == "show_plan"
            assert response.tool_results[0].success is True
            assert "完成洛琪希计划" in response.message
            assert "目前还没有执行这项操作" not in response.message


def test_successful_show_plan_does_not_enter_candidate_selection_or_override_recheck():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), RecordingLLM())
        growth.add_task("完成洛琪希计划")

        first = service.handle("我今天还有什么没完成？", "show-plan-recheck")
        growth.add_task("补充测试")
        second = service.handle("你确定吗？", "show-plan-recheck")

        assert first.tool_results[0].tool == second.tool_results[0].tool == "show_plan"
        assert first.tool_results[0].success and second.tool_results[0].success
        assert "补充测试" in second.message
        assert "目前还没有执行这项操作" not in second.message
        state = service.interaction_coordinator.current("show-plan-recheck")
        assert state.state != "candidates_listed"


def test_context_keeps_old_summaries_as_system_references_and_never_replays_current_input():
    builder = ContextBuilder(recent_message_limit=8)
    result = builder.build(
        personality_context="人格",
        memory_context="",
        session_summary="上一轮已回答：随机森林复习安排。",
        relevant_conversation_summaries_context="旧会话参考：讨论过人格包。",
        recent_messages=[
            {"role": "user", "content": "上一轮已回答：随机森林复习安排。"},
            {"role": "assistant", "content": "已经回答过这个问题。"},
            {"role": "user", "content": "逻辑回归的 Sigmoid 是什么？"},
        ],
        knowledge_context="",
        current_user_input="逻辑回归的 Sigmoid 是什么？",
    )

    assert result[-1] == {"role": "user", "content": "逻辑回归的 Sigmoid 是什么？"}
    assert sum(item["content"] == "逻辑回归的 Sigmoid 是什么？" for item in result) == 1
    assert all(item["role"] != "user" or "旧会话参考" not in item["content"] for item in result)
    assert sum("上一轮已回答：随机森林复习安排。" in item["content"] for item in result) == 1


def test_failed_write_tool_result_never_claims_memory_was_saved():
    # This remains deterministic: the response composer sees a failed ToolResult,
    # not an LLM-authored Chinese sentence.
    from modules.contracts import ToolResult
    from modules.response_composer import ResponseComposer

    from modules.contracts import AgentResponse

    response = ResponseComposer().compose(
        AgentResponse(
            "failed",
            "",
            tool_results=[ToolResult(False, "save_formal_memory", "write_failed", error="write_failed")],
        ),
        user_text="记住完成洛琪希计划",
    )

    assert "记住了" not in response.message
    assert "保存成功" not in response.message
