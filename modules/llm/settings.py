from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_COMPLEX_MODEL = "deepseek-v4-pro"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _integer(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@dataclass(frozen=True)
class ModelSettings:
    online_enabled: bool = True
    online_provider: str = "deepseek"
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    default_model: str = DEFAULT_DEEPSEEK_MODEL
    complex_model: str = DEFAULT_DEEPSEEK_COMPLEX_MODEL
    model_mode: str = "auto"
    enable_auto_escalation: bool = True
    enable_thinking_for_complex_tasks: bool = True
    offline_fallback_enabled: bool = True
    offline_provider: str = "ollama"
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    offline_model: str = DEFAULT_OLLAMA_MODEL
    request_timeout_seconds: int = 120
    max_provider_retries: int = 1
    max_output_tokens: int = 2048

    @classmethod
    def from_mapping(
        cls,
        value: Optional[Mapping[str, object]],
        *,
        legacy_config: Optional[Mapping[str, object]] = None,
    ) -> "ModelSettings":
        source = dict(value or {})
        legacy = dict(legacy_config or {})
        mode = str(source.get("model_mode", "auto")).strip().lower()
        if mode not in {"economy", "auto", "quality"}:
            mode = "auto"

        legacy_provider = str(legacy.get("provider", "")).strip().lower()
        legacy_base_url = str(legacy.get("base_url", "")).strip()
        legacy_model = str(legacy.get("model", "")).strip()
        ollama_base_url = str(
            source.get("ollama_base_url", "")
            or (legacy_base_url if legacy_provider == "ollama" else "")
            or DEFAULT_OLLAMA_BASE_URL
        ).strip()
        offline_model = str(
            source.get("offline_model", "")
            or source.get("model_name", "")
            or (legacy_model if legacy_provider == "ollama" else "")
            or DEFAULT_OLLAMA_MODEL
        ).strip()
        return cls(
            online_enabled=_bool(source.get("online_model_enabled"), True),
            online_provider="deepseek",
            deepseek_base_url=str(
                source.get("deepseek_base_url", DEFAULT_DEEPSEEK_BASE_URL)
            ).strip().rstrip("/") or DEFAULT_DEEPSEEK_BASE_URL,
            default_model=str(
                source.get("default_model", DEFAULT_DEEPSEEK_MODEL)
            ).strip() or DEFAULT_DEEPSEEK_MODEL,
            complex_model=str(
                source.get("complex_model", DEFAULT_DEEPSEEK_COMPLEX_MODEL)
            ).strip() or DEFAULT_DEEPSEEK_COMPLEX_MODEL,
            model_mode=mode,
            enable_auto_escalation=_bool(
                source.get("enable_auto_escalation"), True
            ),
            enable_thinking_for_complex_tasks=_bool(
                source.get("enable_thinking_for_complex_tasks"), True
            ),
            offline_fallback_enabled=_bool(
                source.get("offline_fallback_enabled"), True
            ),
            offline_provider="ollama",
            ollama_base_url=ollama_base_url.rstrip("/"),
            offline_model=offline_model,
            request_timeout_seconds=_integer(
                source.get("request_timeout_seconds"), 120, 5, 600
            ),
            max_provider_retries=_integer(
                source.get("max_provider_retries"), 1, 0, 2
            ),
            max_output_tokens=_integer(
                source.get("max_output_tokens"), 2048, 128, 8192
            ),
        )

    def to_public_dict(self) -> dict:
        return {
            "online_enabled": self.online_enabled,
            "online_provider": self.online_provider,
            "deepseek_base_url": self.deepseek_base_url,
            "default_model": self.default_model,
            "complex_model": self.complex_model,
            "model_mode": self.model_mode,
            "enable_auto_escalation": self.enable_auto_escalation,
            "enable_thinking_for_complex_tasks": self.enable_thinking_for_complex_tasks,
            "offline_fallback_enabled": self.offline_fallback_enabled,
            "offline_provider": self.offline_provider,
            "ollama_base_url": self.ollama_base_url,
            "offline_model": self.offline_model,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_provider_retries": self.max_provider_retries,
            "max_output_tokens": self.max_output_tokens,
        }
