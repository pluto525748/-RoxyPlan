import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner
from modules.confirmation_manager import ConfirmationManager
from modules.contracts import ToolResult
from modules.growth_manager import GrowthManager
from modules.llm.base import LLMProvider
from modules.llm.contracts import ProviderError, ProviderHealth, ProviderResponse, ProviderToolCall
from modules.llm.routed_client import RoutedLLMClient
from modules.memory_manager import MemoryManager
from modules.model_action_adapter import ModelToolCallLoop
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry


class ScriptedProvider(LLMProvider):
    provider_name = "deepseek"
    supports_tools = True
    supports_thinking = True

    def __init__(self, responses, model_name="fake-default"):
        super().__init__(model_name)
        self.responses = list(responses)
        self.calls = []

    @property
    def configured(self):
        return True

    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        return self.responses.pop(0)

    def health_check(self, force=False):
        return ProviderHealth("deepseek", "online", True, self.model_name)


class OfflineProvider(ScriptedProvider):
    provider_name = "ollama"
    supports_tools = False
    supports_thinking = False

    def __init__(self, responses=None):
        super().__init__(responses or [], "fake-local")


def build_adapter(root, responses, offline_responses=None):
    growth = GrowthManager(root / "private")
    memory = MemoryManager(root / "memory.json")
    registry = create_roxy_tool_registry(growth, memory)
    confirmation = ConfirmationManager()
    executor = ToolExecutor(registry, SafetyPolicy(), confirmation)
    core = AgentCore(AgentPlanner(registry), executor)
    deepseek = ScriptedProvider(responses)
    client = RoutedLLMClient(
        root,
        settings={
            "default_model": "fake-default",
            "complex_model": "fake-complex",
            "offline_model": "fake-local",
        },
        deepseek_provider=deepseek,
        ollama_provider=OfflineProvider(offline_responses),
    )
    adapter = ModelToolCallLoop(client, registry, core)
    return adapter, growth, registry, deepseek


def test_tool_schema_only_contains_explicit_model_visible_tools():
    with tempfile.TemporaryDirectory() as temp:
        adapter, _growth, registry, _provider = build_adapter(Path(temp), [])
        names = {
            item["function"]["name"] for item in registry.model_tool_schemas()
        }
        assert "add_plan" in names
        assert "delete_plan" in names
        assert "delete_all_memories" not in names
        assert "show_memory_audit" not in names
        assert all(
            schema["function"]["parameters"]["additionalProperties"] is False
            for schema in registry.model_tool_schemas()
        )


def test_tool_result_is_returned_with_matching_call_id():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[
                ProviderToolCall(
                    "add_plan", {"title": "学习机器学习50分钟"}, "call_add_1"
                )
            ],
            finish_reason="tool_calls",
        ),
        ProviderResponse(
            "deepseek", "fake-default", content="已经添加这条计划。"
        ),
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="把学习机器学习50分钟加入计划",
            messages=[{"role": "user", "content": "把学习机器学习50分钟加入计划"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.9},
        )
        assert result.status == "completed"
        assert growth.tasks()[0]["title"] == "学习机器学习50分钟"
        assert result.tool_results[0].tool_call_id == "call_add_1"
        second_messages = provider.calls[1][0]
        assistant = next(item for item in second_messages if item["role"] == "assistant")
        tool_message = next(item for item in second_messages if item["role"] == "tool")
        assert assistant["tool_calls"][0]["id"] == "call_add_1"
        assert isinstance(assistant["tool_calls"][0]["function"]["arguments"], str)
        assert tool_message["tool_call_id"] == "call_add_1"
        assert json.loads(tool_message["content"])["success"] is True


def test_multiple_write_tools_execute_in_order():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[
                ProviderToolCall("add_plan", {"title": "整理文档"}, "call_1"),
                ProviderToolCall("add_action_log", {"content": "完成环境检查"}, "call_2"),
            ],
        ),
        ProviderResponse("deepseek", "fake-default", content="两项操作已经完成。"),
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="加入整理文档计划，并记录完成环境检查",
            messages=[{"role": "user", "content": "加入整理文档计划，并记录完成环境检查"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.95},
        )
        assert result.status == "completed"
        assert [item.tool for item in result.tool_results] == ["add_plan", "add_action_log"]
        assert len(growth.tasks()) == 1
        assert len(growth.records_for_date()) == 1


def test_unknown_tool_is_rejected_without_execution():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[ProviderToolCall("run_shell", {"command": "whoami"}, "bad")],
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="执行命令",
            messages=[{"role": "user", "content": "执行命令"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.99},
        )
        assert result.status == "failed"
        assert result.tool_results[0].error == "tool_not_found"
        assert growth.tasks() == []


def test_high_risk_tool_requires_confirmation_and_does_not_delete():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[ProviderToolCall("delete_plan", {"task_ref": "1"}, "delete_1")],
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        growth.add_task("保留这条")
        result = adapter.complete(
            user_text="删除第一条计划",
            messages=[{"role": "user", "content": "删除第一条计划"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.99},
        )
        assert result.status == "confirmation_required"
        assert len(growth.tasks()) == 1


def test_failed_tool_cannot_be_polished_into_success():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[ProviderToolCall("add_plan", {"title": "会失败"}, "call_fail")],
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, registry, _provider = build_adapter(Path(temp), responses)
        registry.get("add_plan").handler = lambda title, allow_duplicate=False: ToolResult(
            False, "add_plan", "write_failed", error="write_failed"
        )
        result = adapter.complete(
            user_text="加入计划",
            messages=[{"role": "user", "content": "加入计划"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.99},
        )
        assert result.status == "failed"
        assert "加入今天计划" not in result.message
        assert growth.tasks() == []


def test_unverified_plain_chat_claim_is_blocked():
    responses = [
        ProviderResponse(
            "deepseek", "fake-default", content="我已经帮你添加计划了。"
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="下午学习怎么样",
            messages=[{"role": "user", "content": "下午学习怎么样"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.2},
        )
        assert result.status == "chat"
        assert "还没有执行" in result.message
        assert growth.tasks() == []


def test_user_completion_echo_is_not_mistaken_for_a_tool_claim():
    responses = [
        ProviderResponse(
            "deepseek", "fake-default", content="你已经完成作业了，这一步做得很扎实。"
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="我刚刚完成作业了",
            messages=[{"role": "user", "content": "我刚刚完成作业了"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.2},
        )
        assert result.message == "你已经完成作业了，这一步做得很扎实。"
        assert growth.tasks() == []


def test_vague_model_write_requires_confirmation():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            tool_calls=[ProviderToolCall("add_plan", {"title": "学习机器学习"}, "vague_1")],
        )
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(Path(temp), responses)
        result = adapter.complete(
            user_text="我想找时间学习机器学习",
            messages=[{"role": "user", "content": "我想找时间学习机器学习"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.2, "source": "fallback"},
        )
        assert result.status == "confirmation_required"
        assert growth.tasks() == []


def test_online_failure_falls_back_to_chat_without_local_tool_execution():
    responses = [
        ProviderResponse(
            "deepseek",
            "fake-default",
            error=ProviderError("network_error", "offline"),
        )
    ]
    offline = [
        ProviderResponse("ollama", "fake-local", content="我已经帮你添加计划。")
    ]
    with tempfile.TemporaryDirectory() as temp:
        adapter, growth, _registry, _provider = build_adapter(
            Path(temp), responses, offline
        )
        result = adapter.complete(
            user_text="帮我处理下午学习",
            messages=[{"role": "user", "content": "帮我处理下午学习"}],
            conversation_id="session",
            intent_result={"intent": "chat", "confidence": 0.2},
        )
        assert result.status == "chat"
        assert "还没有执行" in result.message
        assert growth.tasks() == []


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"model tool calling tests passed ({len(TESTS)} cases)")
