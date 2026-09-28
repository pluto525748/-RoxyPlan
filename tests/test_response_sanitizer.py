import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.llm.openai_compatible import OpenAICompatibleProvider
from modules.llm.response_sanitizer import (
    sanitize_public_reply,
    sanitize_public_text,
    split_reasoning_content,
)


def test_complete_thinking_block_is_hidden():
    public, reasoning = split_reasoning_content(
        "<think>内部推理不能展示</think>这是最终回答。"
    )
    assert public == "这是最终回答。"
    assert "内部推理" in reasoning


def test_unclosed_thinking_block_is_hidden():
    assert sanitize_public_text("<think>还在内部推理") == ""
    assert "内部推理" not in sanitize_public_reply("<think>还在内部推理")


def test_null_like_reply_uses_friendly_fallback():
    for value in (None, "", "None", "null"):
        reply = sanitize_public_reply(value)
        assert reply
        assert reply.lower() not in {"none", "null"}


def test_public_reply_unwraps_only_paired_markdown_strong_markers():
    assert sanitize_public_reply("**你是一个认真投入的人**") == "你是一个认真投入的人"
    assert sanitize_public_reply("2 * 3 = 6") == "2 * 3 = 6"
    assert sanitize_public_reply("***仍保留三颗星***") == "***仍保留三颗星***"


def test_provider_separates_embedded_and_native_reasoning():
    def transport(_method, _url, _headers, _payload, _timeout):
        return {
            "model": "fake",
            "choices": [
                {
                    "message": {
                        "content": "<think>嵌入推理</think>公开回答",
                        "reasoning_content": "原生推理",
                    }
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        provider_name="fake",
        base_url="http://localhost/v1",
        api_key="test",
        model_name="fake",
        transport=transport,
    )
    response = provider.chat([{"role": "user", "content": "test"}])
    assert response.content == "公开回答"
    assert "嵌入推理" in response.reasoning_content
    assert "原生推理" in response.reasoning_content
    assert "reasoning_content" not in response.to_dict()


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print(f"response sanitizer tests passed ({len(TESTS)} cases)")
