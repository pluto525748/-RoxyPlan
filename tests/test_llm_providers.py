import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.llm.contracts import ProviderResponse
from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.provider_factory import ProviderFactory
from modules.llm.secret_store import SecretStore
from modules.llm.settings import ModelSettings


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        self.calls.append((method, url, headers, payload, timeout))
        return self.responses.pop(0)


def completion(message, model="fake-model", finish_reason="stop", usage=None):
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": usage or {},
    }


def test_no_key_is_safe_and_does_not_call_network():
    transport = RecordingTransport([])
    provider = DeepSeekProvider(
        api_key="",
        base_url="https://api.deepseek.com",
        model_name="fake-model",
        transport=transport,
    )
    response = provider.chat([{"role": "user", "content": "你好"}])
    assert response.error.code == "not_configured"
    assert transport.calls == []


def test_native_tool_call_is_parsed_and_payload_contains_schema():
    transport = RecordingTransport(
        [
            completion(
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "add_plan",
                                "arguments": json.dumps(
                                    {"title": "学习机器学习"}, ensure_ascii=False
                                ),
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
            )
        ]
    )
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model_name="fake-model",
        transport=transport,
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_plan",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    response = provider.chat_with_tools(
        [{"role": "user", "content": "加入计划"}], tools
    )
    assert response.tool_calls[0].name == "add_plan"
    assert response.tool_calls[0].arguments["title"] == "学习机器学习"
    assert response.tool_calls[0].tool_call_id == "call_1"
    assert transport.calls[0][3]["tools"] == tools
    assert transport.calls[0][3]["tool_choice"] == "auto"


def test_thinking_content_is_not_exposed_in_public_contract():
    transport = RecordingTransport(
        [completion({"content": "最终答案", "reasoning_content": "内部推理"})]
    )
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model_name="fake-model",
        transport=transport,
    )
    response = provider.chat(
        [{"role": "user", "content": "复杂复盘"}], thinking=True
    )
    assert response.content == "最终答案"
    assert response.reasoning_content == "内部推理"
    assert "reasoning_content" not in response.to_dict()
    assert transport.calls[0][3]["thinking"] == {"type": "enabled"}


def test_provider_error_codes_are_controlled():
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model_name="fake-model",
    )
    assert provider._http_error(401).code == "unauthorized"
    assert provider._http_error(402).code == "insufficient_balance"
    assert provider._http_error(429).code == "rate_limited"
    assert provider._http_error(500).retryable is True


def test_secret_priority_and_private_storage():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = SecretStore(root)
        assert store.save_deepseek_key("private-test-key")
        assert store.get_deepseek_key() == ("private-test-key", "private_file")
        original = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "environment-test-key"
        try:
            assert store.get_deepseek_key() == (
                "environment-test-key",
                "environment",
            )
            status = store.status()
            assert "environment-test-key" not in json.dumps(status)
            assert status["configured"] is True
        finally:
            if original is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = original


def test_provider_factory_uses_central_settings():
    settings = ModelSettings.from_mapping(
        {"default_model": "fake-default", "request_timeout_seconds": 33}
    )
    provider = ProviderFactory.create_deepseek(settings, "test-key")
    assert provider.model_name == "fake-default"
    assert provider.timeout_seconds == 33


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"llm provider tests passed ({len(TESTS)} cases)")
