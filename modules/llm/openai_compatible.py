from __future__ import annotations

import json
import socket
import time
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional
from urllib import error, request

from modules.llm.base import LLMProvider
from modules.llm.contracts import (
    ProviderError,
    ProviderHealth,
    ProviderResponse,
    parse_tool_calls,
)
from modules.llm.response_sanitizer import split_reasoning_content


Transport = Callable[
    [str, str, Dict[str, str], Optional[Dict[str, object]], int], Dict[str, object]
]


class OpenAICompatibleProvider(LLMProvider):
    supports_tools = True

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int = 120,
        max_retries: int = 0,
        health_ttl_seconds: int = 60,
        transport: Optional[Transport] = None,
    ) -> None:
        super().__init__(model_name)
        self.provider_name = str(provider_name).strip().lower()
        self.base_url = str(base_url).strip().rstrip("/")
        self.api_key = str(api_key).strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.max_retries = max(0, min(int(max_retries), 2))
        self.health_ttl_seconds = max(10, int(health_ttl_seconds))
        self.transport = transport
        self._health_checked_monotonic = 0.0
        self._health_cache: Optional[ProviderHealth] = None
        self._last_success_at = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model_name and self._credentials_ready())

    def _credentials_ready(self) -> bool:
        return bool(self.api_key)

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
        selected_model = str(model or self.model_name).strip()
        if not self.configured:
            return ProviderResponse(
                self.provider_name,
                selected_model,
                error=ProviderError(
                    "not_configured",
                    f"{self.provider_name} 尚未配置。",
                ),
            )
        payload: Dict[str, object] = {
            "model": selected_model,
            "messages": [dict(item) for item in messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if max_tokens is not None:
            payload["max_tokens"] = max(64, int(max_tokens))
        self._apply_mode(payload, thinking=thinking)

        started = time.monotonic()
        raw, provider_error = self._request_json(
            "POST",
            self._chat_url(),
            payload,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if provider_error is not None:
            return ProviderResponse(
                self.provider_name,
                selected_model,
                latency_ms=latency_ms,
                error=provider_error,
                thinking=thinking,
            )
        try:
            choices = raw.get("choices", []) if isinstance(raw, Mapping) else []
            choice = choices[0]
            message = choice.get("message", {})
            if not isinstance(message, Mapping):
                raise ValueError("message is not an object")
            usage = raw.get("usage", {})
            usage = dict(usage) if isinstance(usage, Mapping) else {}
            public_content, hidden_reasoning = split_reasoning_content(
                message.get("content"),
                message.get("reasoning_content"),
            )
            response = ProviderResponse(
                provider=self.provider_name,
                model=str(raw.get("model", selected_model)),
                content=public_content,
                reasoning_content=hidden_reasoning,
                tool_calls=parse_tool_calls(message.get("tool_calls")),
                usage=usage,
                finish_reason=str(choice.get("finish_reason") or ""),
                latency_ms=latency_ms,
                thinking=thinking,
            )
            self._last_success_at = datetime.now().isoformat(timespec="seconds")
            return response
        except (IndexError, KeyError, TypeError, ValueError):
            return ProviderResponse(
                self.provider_name,
                selected_model,
                latency_ms=latency_ms,
                error=ProviderError(
                    "invalid_response",
                    "模型返回格式无法解析。",
                    False,
                ),
                thinking=thinking,
            )

    def health_check(self, *, force: bool = False) -> ProviderHealth:
        now = time.monotonic()
        if (
            not force
            and self._health_cache is not None
            and now - self._health_checked_monotonic < self.health_ttl_seconds
        ):
            return self._health_cache
        checked_at = datetime.now().isoformat(timespec="seconds")
        if not self.configured:
            health = ProviderHealth(
                self.provider_name,
                "not_configured",
                False,
                self.model_name,
                checked_at,
                self._last_success_at,
                "not_configured",
            )
        else:
            _raw, provider_error = self._request_json(
                "GET",
                self._health_url(),
                None,
                allow_retry=False,
            )
            health = ProviderHealth(
                self.provider_name,
                "online" if provider_error is None else "offline",
                True,
                self.model_name,
                checked_at,
                self._last_success_at,
                provider_error.code if provider_error is not None else "",
            )
        self._health_checked_monotonic = now
        self._health_cache = health
        return health

    def _apply_mode(self, payload: Dict[str, object], *, thinking: bool) -> None:
        if not thinking:
            payload["temperature"] = 0.7

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _health_url(self) -> str:
        base = self.base_url
        if base.endswith("/v1"):
            return f"{base}/models"
        return f"{base}/models"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, object]],
        *,
        allow_retry: bool = True,
    ):
        attempts = 1 + (self.max_retries if allow_retry else 0)
        for attempt in range(attempts):
            try:
                if self.transport is not None:
                    return (
                        self.transport(
                            method,
                            url,
                            self._headers(),
                            payload,
                            self.timeout_seconds,
                        ),
                        None,
                    )
                body = (
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if payload is not None
                    else None
                )
                http_request = request.Request(
                    url,
                    data=body,
                    headers=self._headers(),
                    method=method,
                )
                with request.urlopen(
                    http_request, timeout=self.timeout_seconds
                ) as response:
                    return json.loads(response.read().decode("utf-8")), None
            except error.HTTPError as http_error:
                provider_error = self._http_error(http_error.code)
                if (
                    provider_error.retryable
                    and attempt + 1 < attempts
                    and http_error.code >= 500
                ):
                    time.sleep(0.25 * (attempt + 1))
                    continue
                return {}, provider_error
            except (error.URLError, TimeoutError, socket.timeout):
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                return {}, ProviderError(
                    "network_error",
                    "模型服务当前无法连接。",
                    True,
                )
            except (OSError, ValueError, json.JSONDecodeError):
                return {}, ProviderError(
                    "invalid_response",
                    "模型服务返回了无法解析的响应。",
                    False,
                )
        return {}, ProviderError("network_error", "模型服务当前无法连接。", True)

    def _http_error(self, status: int) -> ProviderError:
        mapping = {
            400: ("bad_request", "模型请求参数不被服务接受。", False),
            401: ("unauthorized", "模型服务认证失败，请检查 API Key。", False),
            402: ("insufficient_balance", "模型账户余额不足。", False),
            403: ("forbidden", "模型服务拒绝了当前请求。", False),
            404: ("model_not_found", "配置的模型或接口不存在。", False),
            408: ("timeout", "模型服务请求超时。", True),
            429: ("rate_limited", "模型服务请求过于频繁，请稍后再试。", True),
        }
        code, message, retryable = mapping.get(
            int(status),
            (
                "server_error" if int(status) >= 500 else "http_error",
                "模型服务暂时不可用。",
                int(status) >= 500,
            ),
        )
        return ProviderError(code, message, retryable, int(status))
