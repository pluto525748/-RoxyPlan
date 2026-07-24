from __future__ import annotations

from typing import Optional

from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.ollama_provider import OllamaProvider
from modules.llm.openai_compatible import Transport
from modules.llm.settings import ModelSettings


class ProviderFactory:
    @staticmethod
    def create_deepseek(
        settings: ModelSettings,
        api_key: str,
        *,
        transport: Optional[Transport] = None,
    ) -> DeepSeekProvider:
        return DeepSeekProvider(
            api_key=api_key,
            base_url=settings.deepseek_base_url,
            model_name=settings.default_model,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_provider_retries,
            transport=transport,
        )

    @staticmethod
    def create_ollama(
        settings: ModelSettings,
        *,
        transport: Optional[Transport] = None,
    ) -> OllamaProvider:
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model_name=settings.offline_model,
            timeout_seconds=settings.request_timeout_seconds,
            transport=transport,
        )
