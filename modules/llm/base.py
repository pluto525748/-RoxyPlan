from __future__ import annotations

from typing import Dict, List, Optional

from modules.llm.contracts import ProviderHealth, ProviderResponse


class LLMProvider:
    provider_name = "unknown"
    supports_tools = False
    supports_thinking = False

    def __init__(self, model_name: str) -> None:
        self.model_name = str(model_name).strip()

    @property
    def configured(self) -> bool:
        return bool(self.model_name)

    def chat(
        self,
        messages: List[Dict[str, object]],
        *,
        model: Optional[str] = None,
        thinking: bool = False,
        max_tokens: Optional[int] = None,
    ) -> ProviderResponse:
        return self.chat_with_tools(
            messages,
            tools=None,
            model=model,
            thinking=thinking,
            max_tokens=max_tokens,
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
        raise NotImplementedError

    def health_check(self, *, force: bool = False) -> ProviderHealth:
        raise NotImplementedError
