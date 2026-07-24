import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.llm.base import LLMProvider
from modules.llm.contracts import ProviderError, ProviderHealth, ProviderResponse
from modules.llm.model_router import ModelRouter
from modules.llm.routed_client import RoutedLLMClient
from modules.llm.settings import ModelSettings


class FakeProvider(LLMProvider):
    def __init__(self, name, model, responses, configured=True, tools=True):
        super().__init__(model)
        self.provider_name = name
        self.responses = list(responses)
        self._configured = configured
        self.supports_tools = tools
        self.calls = []

    @property
    def configured(self):
        return self._configured

    def chat_with_tools(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        return self.responses.pop(0)

    def health_check(self, force=False):
        return ProviderHealth(
            self.provider_name,
            "online" if self._configured else "not_configured",
            self._configured,
            self.model_name,
        )


def test_economy_uses_default_without_escalation():
    router = ModelRouter(ModelSettings.from_mapping({"model_mode": "economy"}))
    decision = router.route(
        {"complex_reference": True}, online_available=True, tools_requested=False
    )
    assert decision.model == router.settings.default_model
    assert decision.thinking is False


def test_auto_escalates_complex_reference_once():
    router = ModelRouter(ModelSettings.from_mapping({"model_mode": "auto"}))
    decision = router.route(
        {"complex_reference": True}, online_available=True, tools_requested=False
    )
    assert decision.model == router.settings.complex_model
    assert decision.reason == "complex_task"


def test_quality_prefers_complex_model():
    router = ModelRouter(ModelSettings.from_mapping({"model_mode": "quality"}))
    decision = router.route({}, online_available=True)
    assert decision.model == router.settings.complex_model


def test_tool_calling_defaults_to_non_thinking():
    router = ModelRouter(ModelSettings.from_mapping({"model_mode": "quality"}))
    decision = router.route(
        {"memory_conflict": True}, online_available=True, tools_requested=True
    )
    assert decision.thinking is False
    assert decision.allow_tools is True


def test_deep_planning_can_use_thinking():
    router = ModelRouter(ModelSettings.from_mapping({"model_mode": "quality"}))
    decision = router.route(
        {"deep_planning": True}, online_available=True, tools_requested=False
    )
    assert decision.thinking is True


def test_unconfigured_online_routes_to_ollama():
    router = ModelRouter(ModelSettings.from_mapping({}))
    decision = router.route({}, online_available=False, offline_available=True)
    assert decision.provider == "ollama"


def test_online_failure_falls_back_for_chat_only():
    with tempfile.TemporaryDirectory() as temp:
        deepseek = FakeProvider(
            "deepseek",
            "fake-default",
            [ProviderResponse("deepseek", "fake-default", error=ProviderError("network_error", "offline"))],
        )
        ollama = FakeProvider(
            "ollama",
            "fake-local",
            [ProviderResponse("ollama", "fake-local", content="本地回复")],
        )
        client = RoutedLLMClient(
            Path(temp),
            settings={"default_model": "fake-default", "offline_model": "fake-local"},
            deepseek_provider=deepseek,
            ollama_provider=ollama,
        )
        response = client.chat_response([{"role": "user", "content": "你好"}])
        assert response.provider == "ollama"
        assert response.content == "本地回复"
        assert response.route_reason.startswith("fallback:")


def test_online_tool_failure_does_not_replay_through_ollama():
    with tempfile.TemporaryDirectory() as temp:
        deepseek = FakeProvider(
            "deepseek",
            "fake-default",
            [ProviderResponse("deepseek", "fake-default", error=ProviderError("network_error", "offline"))],
        )
        ollama = FakeProvider(
            "ollama", "fake-local", [ProviderResponse("ollama", "fake-local", content="错误成功声明")]
        )
        client = RoutedLLMClient(
            Path(temp),
            settings={"default_model": "fake-default", "offline_model": "fake-local"},
            deepseek_provider=deepseek,
            ollama_provider=ollama,
        )
        response = client.chat_response(
            [{"role": "user", "content": "添加计划"}],
            tools=[{"type": "function", "function": {"name": "add_plan", "parameters": {"type": "object"}}}],
        )
        assert response.error.code == "network_error"
        assert ollama.calls == []


def test_invalid_default_response_escalates_once_before_any_tool_execution():
    with tempfile.TemporaryDirectory() as temp:
        deepseek = FakeProvider(
            "deepseek",
            "fake-default",
            [
                ProviderResponse(
                    "deepseek",
                    "fake-default",
                    error=ProviderError("invalid_response", "bad"),
                ),
                ProviderResponse("deepseek", "fake-complex", content="澄清问题"),
            ],
        )
        client = RoutedLLMClient(
            Path(temp),
            settings={
                "model_mode": "auto",
                "default_model": "fake-default",
                "complex_model": "fake-complex",
            },
            deepseek_provider=deepseek,
            ollama_provider=FakeProvider("ollama", "fake-local", []),
        )
        response = client.chat_response(
            [{"role": "user", "content": "处理这个"}],
            tools=[{"type": "function", "function": {"name": "show_plan", "parameters": {"type": "object"}}}],
        )
        assert response.ok
        assert response.model == "fake-complex"
        assert len(deepseek.calls) == 2
        assert deepseek.calls[0][2]["model"] == "fake-default"
        assert deepseek.calls[1][2]["model"] == "fake-complex"


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"model routing tests passed ({len(TESTS)} cases)")
