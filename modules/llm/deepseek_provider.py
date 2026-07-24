from __future__ import annotations

from typing import Dict, Optional

from modules.llm.openai_compatible import OpenAICompatibleProvider, Transport


class DeepSeekProvider(OpenAICompatibleProvider):
    supports_tools = True
    supports_thinking = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        timeout_seconds: int = 120,
        max_retries: int = 0,
        health_ttl_seconds: int = 60,
        transport: Optional[Transport] = None,
    ) -> None:
        super().__init__(
            provider_name="deepseek",
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            health_ttl_seconds=health_ttl_seconds,
            transport=transport,
        )

    def _apply_mode(self, payload: Dict[str, object], *, thinking: bool) -> None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            payload["reasoning_effort"] = "high"
        else:
            payload["temperature"] = 0.7
