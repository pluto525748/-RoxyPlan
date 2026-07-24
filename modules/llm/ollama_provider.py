from __future__ import annotations

from typing import Dict, List, Optional

from modules.llm.contracts import ProviderHealth, ProviderResponse
from modules.llm.openai_compatible import OpenAICompatibleProvider, Transport


class OllamaProvider(OpenAICompatibleProvider):
    supports_tools = True
    supports_thinking = False

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        api_key: str = "ollama",
        timeout_seconds: int = 120,
        health_ttl_seconds: int = 30,
        transport: Optional[Transport] = None,
    ) -> None:
        super().__init__(
            provider_name="ollama",
            base_url=base_url,
            api_key=api_key or "ollama",
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            max_retries=0,
            health_ttl_seconds=health_ttl_seconds,
            transport=transport,
        )

    def chat_with_tools(
        self,
        messages: List[Dict[str, object]],
        tools: Optional[List[Dict[str, object]]],
        *,
        tool_choice: object = "auto",
        model: Optional[str] = None,
        thinking: bool = False,
        max_tokens: Optional[int] = None,
    ) -> ProviderResponse:
        prepared = [
            {
                "role": "system",
                "content": "请用中文直接回答用户。不要展示推理过程。不要使用表情符号。",
            }
        ]
        prepared.extend(dict(item) for item in messages)
        return super().chat_with_tools(
            prepared,
            tools,
            tool_choice=tool_choice,
            model=model,
            thinking=False,
            max_tokens=max_tokens,
        )

    def _health_url(self) -> str:
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base.rstrip('/')}/api/tags"

    def health_check(self, *, force: bool = False) -> ProviderHealth:
        return super().health_check(force=force)
