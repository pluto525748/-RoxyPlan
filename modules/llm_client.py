from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request


DEFAULT_TIMEOUT_SECONDS = 120
OLLAMA_PROVIDER = "ollama"


def default_config() -> Dict[str, str]:
    return {
        "provider": "",
        "base_url": "",
        "api_key": "",
        "model": "",
    }


def load_llm_config(config_file: Path) -> Dict[str, str]:
    if not config_file.exists():
        config = default_config()
        save_llm_config(config_file, config)
        return config

    try:
        with config_file.open("r", encoding="utf-8") as file:
            raw_config = json.load(file)
    except (json.JSONDecodeError, OSError):
        config = default_config()
        save_llm_config(config_file, config)
        return config

    if not isinstance(raw_config, dict):
        return default_config()

    provider = str(raw_config.get("provider", "")).strip().lower()
    provider_config = _provider_config(raw_config, provider)

    config = default_config()
    config["provider"] = provider
    for key in ("base_url", "api_key", "model"):
        value = provider_config.get(key, raw_config.get(key, ""))
        config[key] = str(value).strip()

    if config["provider"] == OLLAMA_PROVIDER and not config["api_key"]:
        config["api_key"] = "ollama"

    return config


def _provider_config(raw_config: Dict[str, Any], provider: str) -> Dict[str, Any]:
    providers = raw_config.get("providers", {})
    if not provider or not isinstance(providers, dict):
        return {}

    provider_config = providers.get(provider, {})
    if isinstance(provider_config, dict):
        return provider_config
    return {}


def save_llm_config(config_file: Path, config: Dict[str, str]) -> None:
    with config_file.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)


class LLMClient:
    """OpenAI-compatible chat completions client with provider-aware errors."""

    def __init__(self, config: Dict[str, str], timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.provider = config.get("provider", "").strip().lower()
        self.base_url = config.get("base_url", "").strip()
        self.api_key = config.get("api_key", "").strip()
        self.model = config.get("model", "").strip()
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        if self.provider == OLLAMA_PROVIDER:
            return bool(self.base_url and self.model)
        return bool(self.base_url and self.api_key and self.model)

    def chat(self, messages: List[Dict[str, str]]) -> str:
        if not self.is_configured():
            return "大模型接口尚未配置，请检查 config.json。"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._prepare_messages(messages),
            "temperature": 0.7,
        }
        if self.provider == OLLAMA_PROVIDER:
            payload["stream"] = False

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        http_request = request.Request(
            self.chat_completions_url(),
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            formatted_error = self.format_http_error(exc)
            if self.provider == OLLAMA_PROVIDER:
                return f"本地 Ollama 返回错误，请确认 qwen3:4b 可用。\n\n{formatted_error}"
            return formatted_error
        except error.URLError as exc:
            if self.provider == OLLAMA_PROVIDER:
                return f"本地 Ollama 连接失败：请确认 Ollama 已启动，并且 qwen3:4b 已下载。\n\n详细信息：{exc.reason}"
            return f"大模型接口连接失败：{exc.reason}"
        except TimeoutError:
            if self.provider == OLLAMA_PROVIDER:
                return "本地 Ollama 请求超时：模型可能还在加载，请稍后再试。"
            return "大模型接口请求超时。"

        return self.extract_reply(response_body)

    def format_http_error(self, exc: error.HTTPError) -> str:
        try:
            error_body = exc.read().decode("utf-8", errors="replace").strip()
        except OSError:
            error_body = ""

        if not error_body:
            return f"HTTP {exc.code}"

        return f"HTTP {exc.code}\n\n{error_body}"

    def chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _prepare_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if self.provider != OLLAMA_PROVIDER:
            return messages

        prepared = [
            {
                "role": "system",
                "content": "请用中文直接回答用户。不要展示推理过程。不要使用表情符号。",
            }
        ]
        for message in messages:
            prepared.append(
                {
                    "role": str(message.get("role", "user")),
                    "content": str(message.get("content", "")),
                }
            )

        return prepared

    def extract_reply(self, response_body: str) -> str:
        try:
            data: Dict[str, Any] = json.loads(response_body)
            choices = data.get("choices", [])
            if not choices:
                return "大模型没有返回有效回复。"

            message: Optional[Dict[str, Any]] = choices[0].get("message")
            if not isinstance(message, dict):
                return "大模型返回格式不正确。"

            content = str(message.get("content", "")).strip()
            content = self._strip_thinking_block(content)
            return content or "大模型返回了空回复。"
        except (json.JSONDecodeError, AttributeError, TypeError):
            return "大模型返回内容无法解析。"

    def _strip_thinking_block(self, content: str) -> str:
        end_marker_pattern = r"</think\s*>"
        if re.search(end_marker_pattern, content, flags=re.IGNORECASE):
            return re.split(end_marker_pattern, content, maxsplit=1, flags=re.IGNORECASE)[1].strip()

        if not content.startswith("<think>"):
            return content

        return content
