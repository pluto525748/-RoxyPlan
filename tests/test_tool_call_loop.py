import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner
from modules.confirmation_manager import ConfirmationManager
from modules.growth_manager import GrowthManager
from modules.llm.base import LLMProvider
from modules.llm.contracts import (
    ProviderError,
    ProviderHealth,
    ProviderResponse,
    ProviderToolCall,
)
from modules.llm.routed_client import RoutedLLMClient
from modules.memory_manager import MemoryManager
from modules.model_action_adapter import ModelToolCallLoop
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry
from tests.isolation_support import build_isolated_memory_manager


class ScriptedProvider(LLMProvider):
    provider_name = "deepseek"
    supports_tools = True
    supports_thinking = True

    def __init__(self, responses):
        super().__init__("fake-deepseek")
        self.responses = list(responses)
        self.calls = []

    @property
    def configured(self):
        return True

    def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append((list(messages), tools, dict(kwargs)))
        return self.responses.pop(0)

    def health_check(self, force=False):
        return ProviderHealth("deepseek", "online", True, self.model_name)


class OfflineProvider(LLMProvider):
    provider_name = "ollama"
    supports_tools = False

    def __init__(self):
        super().__init__("fake-local")

    @property
    def configured(self):
        return False

    def health_check(self, force=False):
        return ProviderHealth("ollama", "offline", False, self.model_name)


class LocalScriptedProvider(ScriptedProvider):
    provider_name = "ollama"
    supports_tools = False

    def __init__(self, responses):
        super().__init__(responses)
        self.model_name = "fake-local"


def build_loop(root, responses, pet_controller=None):
    growth = GrowthManager(root / "private")
    memory = build_isolated_memory_manager(root)
    registry = create_roxy_tool_registry(growth, memory, pet_controller)
    confirmation = ConfirmationManager()
    executor = ToolExecutor(registry, SafetyPolicy(), confirmation)
    core = AgentCore(AgentPlanner(registry), executor)
    provider = ScriptedProvider(responses)
    client = RoutedLLMClient(
        root,
        settings={
            "default_model": "fake-deepseek",
            "complex_model": "fake-deepseek",
            "offline_model": "fake-local",
        },
        deepseek_provider=provider,
        ollama_provider=OfflineProvider(),
    )
    return ModelToolCallLoop(client, registry, core), growth, provider, executor


def complete(loop, text="add a study plan"):
    return loop.complete(
        user_text=text,
        messages=[{"role": "user", "content": text}],
        conversation_id="session_a",
        intent_result={
            "intent": "chat",
            "confidence": 0.95,
            "source": "rule",
            "request_id": "req_tool_test",
        },
    )


def test_invalid_parameters_receive_one_repair_then_execute():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[ProviderToolCall("add_plan", {}, "repair_1")],
        ),
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[
                ProviderToolCall("add_plan", {"title": "study math"}, "repair_1")
            ],
        ),
        ProviderResponse("deepseek", "fake-deepseek", content="done"),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, provider, _executor = build_loop(Path(temp), responses)
        result = complete(loop)
        assert result.status == "completed"
        assert len(growth.tasks()) == 1
        assert len(provider.calls) == 3
        repair_messages = provider.calls[1][0]
        assert any(item.get("role") == "tool" for item in repair_messages)


def test_second_invalid_parameters_stop_after_one_repair():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[ProviderToolCall("add_plan", {}, "repair_1")],
        ),
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[ProviderToolCall("add_plan", {}, "repair_2")],
        ),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, provider, _executor = build_loop(Path(temp), responses)
        result = complete(loop)
        assert result.status == "clarification"
        assert growth.tasks() == []
        assert len(provider.calls) == 2


def test_repeated_call_id_does_not_repeat_write():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[
                ProviderToolCall("add_plan", {"title": "study math"}, "same_call")
            ],
        ),
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            tool_calls=[
                ProviderToolCall(
                    "add_action_log", {"content": "finished setup"}, "same_call"
                )
            ],
        ),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, _provider, _executor = build_loop(Path(temp), responses)
        result = complete(loop)
        assert result.status == "failed"
        assert len(growth.tasks()) == 1
        assert growth.records_for_date() == []


def test_tool_call_budget_stops_before_any_execution():
    calls = [
        ProviderToolCall("add_plan", {"title": f"task {index}"}, f"call_{index}")
        for index in range(4)
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, _provider, _executor = build_loop(
            Path(temp), [ProviderResponse("deepseek", "fake", tool_calls=calls)]
        )
        result = complete(loop)
        assert result.status == "failed"
        assert growth.tasks() == []


def test_json_fallback_write_requires_confirmation():
    fallback_payload = (
        '{"kind":"action","actions":[{"call_id":"json_1",'
        '"tool_name":"add_plan","arguments":{"title":"study math"}}]}'
    )
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            error=ProviderError("bad_request", "tools unsupported"),
        ),
        ProviderResponse("deepseek", "fake-deepseek", content=fallback_payload),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, _provider, _executor = build_loop(Path(temp), responses)
        result = complete(loop)
        assert result.status == "confirmation_required"
        assert growth.tasks() == []


def test_valid_prose_without_tool_call_does_not_reopen_json_action_routing():
    fallback_payload = (
        '{"kind":"action","actions":[{"call_id":"json_claim_1",'
        '"tool_name":"add_plan","arguments":{"title":"学习特征工程"}}]}'
    )
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            content="已经帮你加入今天计划了。",
        ),
        ProviderResponse("deepseek", "fake-deepseek", content=fallback_payload),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, growth, provider, _executor = build_loop(Path(temp), responses)
        result = complete(loop, "下午帮我留 50 分钟学特征工程，放进今天要做的事里")
        assert result.status == "chat"
        assert growth.tasks() == []
        assert len(provider.calls) == 1
        assert "已经帮你加入" not in result.message


def test_dance_prose_without_tool_call_does_not_reopen_json_action_routing():
    class Pet:
        def __init__(self):
            self.dance_calls = 0

        def start_dance(self):
            self.dance_calls += 1
            return True

    pet = Pet()
    fallback_payload = (
        '{"kind":"action","actions":[{"call_id":"json_dance_1",'
        '"tool_name":"play_dance","arguments":{}}]}'
    )
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-deepseek",
            content="作为模型我不能真的跳舞。",
        ),
        ProviderResponse("deepseek", "fake-deepseek", content=fallback_payload),
    ]
    with tempfile.TemporaryDirectory() as temp:
        loop, _growth, provider, _executor = build_loop(
            Path(temp), responses, pet
        )
        result = complete(loop, "跳个舞看看")
        assert result.status == "chat"
        # The Agent layer must never call Qt/Pet methods directly. The desktop
        # UI consumes the declarative action on its own thread.
        assert pet.dance_calls == 0
        assert result.client_actions == []
        assert len(provider.calls) == 1
        assert result.message == responses[0].content


def test_local_provider_can_supply_constrained_json_fallback():
    payload = (
        '{"kind":"action","actions":[{"call_id":"local_1",'
        '"tool_name":"add_plan","arguments":{"title":"学习机器学习"}}]}'
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        memory = build_isolated_memory_manager(root)
        registry = create_roxy_tool_registry(growth, memory)
        executor = ToolExecutor(
            registry,
            SafetyPolicy(),
            ConfirmationManager(),
        )
        core = AgentCore(AgentPlanner(registry), executor)
        local = LocalScriptedProvider(
            [ProviderResponse("ollama", "fake-local", content=payload)]
        )
        client = RoutedLLMClient(
            root,
            settings={
                "online_model_enabled": False,
                "offline_fallback_enabled": True,
                "offline_model": "fake-local",
            },
            deepseek_provider=OfflineProvider(),
            ollama_provider=local,
        )
        loop = ModelToolCallLoop(client, registry, core)
        result = complete(loop, "把学习机器学习放进今天计划")
        assert result.status == "confirmation_required"
        assert growth.tasks() == []
        assert len(local.calls) == 1


def test_executor_call_id_is_idempotent():
    with tempfile.TemporaryDirectory() as temp:
        _loop, growth, _provider, executor = build_loop(Path(temp), [])
        first = executor.execute(
            "add_plan",
            {"title": "study math"},
            confidence=1.0,
            tool_call_id="stable_call",
        )
        second = executor.execute(
            "add_plan",
            {"title": "different title"},
            confidence=1.0,
            tool_call_id="stable_call",
        )
        assert first.success is True
        assert second.to_dict() == first.to_dict()
        assert len(growth.tasks()) == 1


def test_confirmation_arguments_are_immutable_and_state_bound():
    with tempfile.TemporaryDirectory() as temp:
        _loop, growth, _provider, executor = build_loop(Path(temp), [])
        task = growth.add_task("original title")
        pending = executor.execute(
            "delete_plan",
            {"task_ref": str(task["id"])},
            confidence=1.0,
            confirmation_scope="session_a",
            tool_call_id="delete_call",
        )
        assert pending.error == "confirmation_required"
        growth.plan_store.update_task(task["id"], {"title": "changed title"})
        confirmed = executor.execute_confirmed(confirmation_scope="session_a")
        assert confirmed.error == "confirmation_state_changed"
        assert len(growth.tasks()) == 1


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"tool call loop tests passed ({len(TESTS)} cases)")
