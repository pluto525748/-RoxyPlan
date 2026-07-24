from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional

from modules.llm.base import LLMProvider
from modules.llm.contracts import ModelRouteDecision, ProviderError, ProviderResponse
from modules.llm.model_router import ModelRouter
from modules.llm.provider_factory import ProviderFactory
from modules.llm.response_sanitizer import sanitize_public_reply
from modules.llm.secret_store import SecretStore
from modules.llm.settings import ModelSettings
from modules.llm.usage_store import ModelUsageStore


class RoutedLLMClient:
    """Shared desktop/Web client with controlled online-to-local fallback."""

    def __init__(
        self,
        project_root: Path,
        *,
        settings: Optional[Mapping[str, object]] = None,
        legacy_config: Optional[Mapping[str, object]] = None,
        secret_store: Optional[SecretStore] = None,
        usage_store: Optional[ModelUsageStore] = None,
        deepseek_provider: Optional[LLMProvider] = None,
        ollama_provider: Optional[LLMProvider] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.secret_store = secret_store or SecretStore(self.project_root)
        self.usage_store = usage_store or ModelUsageStore(self.project_root)
        self._legacy_config = dict(legacy_config or {})
        self.settings = ModelSettings.from_mapping(
            settings, legacy_config=self._legacy_config
        )
        key, _source = self.secret_store.get_deepseek_key()
        self.deepseek_provider = deepseek_provider or ProviderFactory.create_deepseek(
            self.settings, key
        )
        self.ollama_provider = ollama_provider or ProviderFactory.create_ollama(
            self.settings
        )
        self.router = ModelRouter(self.settings)
        self.last_response: Optional[ProviderResponse] = None
        self.last_route: Optional[ModelRouteDecision] = None

    @property
    def provider(self) -> str:
        return self.last_response.provider if self.last_response else ""

    @property
    def model(self) -> str:
        return self.last_response.model if self.last_response else self.settings.offline_model

    def is_configured(self) -> bool:
        return bool(
            self.deepseek_provider.configured
            or (
                self.settings.offline_fallback_enabled
                and self.ollama_provider.configured
            )
        )

    def can_use_online_tools(self) -> bool:
        return bool(
            self.settings.online_enabled
            and self.deepseek_provider.configured
            and self.deepseek_provider.supports_tools
        )

    def can_use_json_actions(self) -> bool:
        return bool(
            (self.settings.online_enabled and self.deepseek_provider.configured)
            or (
                self.settings.offline_fallback_enabled
                and self.ollama_provider.configured
            )
        )

    def reconfigure(
        self,
        settings: Optional[Mapping[str, object]] = None,
        *,
        legacy_config: Optional[Mapping[str, object]] = None,
    ) -> None:
        if legacy_config is not None:
            self._legacy_config = dict(legacy_config)
        self.settings = ModelSettings.from_mapping(
            settings, legacy_config=self._legacy_config
        )
        key, _source = self.secret_store.get_deepseek_key()
        self.deepseek_provider = ProviderFactory.create_deepseek(self.settings, key)
        self.ollama_provider = ProviderFactory.create_ollama(self.settings)
        self.router = ModelRouter(self.settings)

    def chat(
        self,
        messages: List[Dict[str, object]],
        route_context: Optional[Mapping[str, object]] = None,
    ) -> str:
        response = self.chat_response(messages, route_context=route_context)
        if response.ok:
            return sanitize_public_reply(response.content)
        return self._friendly_error(response.error)

    def chat_response(
        self,
        messages: List[Dict[str, object]],
        *,
        route_context: Optional[Mapping[str, object]] = None,
        tools: Optional[List[Dict[str, object]]] = None,
        tool_choice: object = "auto",
    ) -> ProviderResponse:
        tools_requested = bool(tools)
        request_id = str((route_context or {}).get("request_id", "")).strip()
        decision = self.router.route(
            route_context,
            online_available=self.deepseek_provider.configured,
            offline_available=self.ollama_provider.configured,
            tools_requested=tools_requested,
        )
        self.last_route = decision
        if decision.provider == "deepseek":
            response = self.deepseek_provider.chat_with_tools(
                messages,
                tools,
                tool_choice=tool_choice,
                model=decision.model,
                thinking=decision.thinking,
                max_tokens=self.settings.max_output_tokens,
            )
            response.route_reason = decision.reason
            self._record(response, request_id=request_id)
            if (
                not response.ok
                and decision.can_escalate
                and response.error is not None
                and response.error.code in {"invalid_response", "bad_request"}
                and decision.model != self.settings.complex_model
            ):
                response = self.deepseek_provider.chat_with_tools(
                    messages,
                    tools,
                    tool_choice=tool_choice,
                    model=self.settings.complex_model,
                    thinking=False if tools_requested else decision.thinking,
                    max_tokens=self.settings.max_output_tokens,
                )
                response.route_reason = f"auto_escalation:{decision.reason}"
                self._record(response, request_id=request_id)
            if response.ok or tools_requested:
                self.last_response = response
                return response
            return self._fallback_chat(
                messages,
                reason=response.error.code if response.error else "online_error",
                request_id=request_id,
            )
        if decision.provider == "ollama":
            return self._call_ollama(messages, decision, request_id=request_id)
        response = ProviderResponse(
            "none",
            "",
            error=ProviderError(
                "no_provider_available",
                "在线模型和本地模型当前都不可用。",
            ),
            route_reason=decision.reason,
        )
        self.last_response = response
        return response

    def continue_tool_loop(
        self,
        messages: List[Dict[str, object]],
        *,
        model: str,
        thinking: bool,
        tools: List[Dict[str, object]],
        route_reason: str,
        request_id: str = "",
    ) -> ProviderResponse:
        # A tool loop must stay on the same provider/model. Falling back here could
        # replay or falsely narrate a write that has already happened.
        response = self.deepseek_provider.chat_with_tools(
            messages,
            tools,
            model=model,
            thinking=thinking,
            max_tokens=self.settings.max_output_tokens,
        )
        response.route_reason = route_reason
        self._record(response, request_id=request_id)
        self.last_response = response
        return response

    def json_action_response(
        self,
        messages: List[Dict[str, object]],
        *,
        request_id: str = "",
    ) -> ProviderResponse:
        """Run one provider-neutral constrained JSON proposal pass."""
        if not self.can_use_json_actions():
            response = ProviderResponse(
                "none",
                "",
                error=ProviderError(
                    "not_configured", "动作解析服务尚未配置。"
                ),
                route_reason="json_fallback_unavailable",
            )
            self.last_response = response
            return response
        response: Optional[ProviderResponse] = None
        if self.settings.online_enabled and self.deepseek_provider.configured:
            model = (
                self.settings.complex_model
                if self.settings.model_mode == "quality"
                else self.settings.default_model
            )
            response = self.deepseek_provider.chat(
                messages,
                model=model,
                thinking=False,
                max_tokens=min(self.settings.max_output_tokens, 1200),
            )
            response.route_reason = "json_action_fallback:deepseek"
            self._record(response, request_id=request_id)
            if response.ok:
                self.last_response = response
                return response

        if (
            self.settings.offline_fallback_enabled
            and self.ollama_provider.configured
        ):
            local_response = self.ollama_provider.chat(
                messages,
                model=self.settings.offline_model,
                thinking=False,
                max_tokens=min(self.settings.max_output_tokens, 1200),
            )
            local_response.route_reason = "json_action_fallback:ollama"
            self._record(local_response, request_id=request_id)
            self.last_response = local_response
            return local_response

        if response is None:
            response = ProviderResponse(
                "none",
                "",
                error=ProviderError("not_configured", "动作解析服务尚未配置。"),
                route_reason="json_fallback_unavailable",
            )
        self.last_response = response
        return response

    def provider_status(self, *, force: bool = False) -> Dict[str, object]:
        deepseek = self.deepseek_provider.health_check(force=force)
        ollama = self.ollama_provider.health_check(force=force)
        if self.last_response is not None:
            current = self.last_response.provider
        elif deepseek.status == "online":
            current = "deepseek"
        elif self.settings.offline_fallback_enabled and ollama.status == "online":
            current = "ollama"
        else:
            current = "deepseek" if deepseek.configured else "ollama"
        return {
            "mode": self.settings.model_mode,
            "current_provider": current,
            "current_model": self.last_response.model if self.last_response else (
                self.settings.default_model if current == "deepseek" else self.settings.offline_model
            ),
            "fallback_active": (
                current == "ollama"
                and self.settings.online_enabled
                and deepseek.status != "online"
            ),
            "deepseek": deepseek.to_dict(),
            "ollama": ollama.to_dict(),
            "api_key": self.secret_store.status(),
            "last_route_reason": self.last_route.reason if self.last_route else "",
        }

    def offline_chat(
        self,
        messages: List[Dict[str, object]],
        *,
        reason: str = "online_unavailable",
        request_id: str = "",
    ) -> ProviderResponse:
        return self._fallback_chat(
            messages, reason=reason, request_id=request_id
        )

    def usage_summary(self) -> Dict[str, object]:
        return self.usage_store.summary()

    def clear_usage(self) -> bool:
        return self.usage_store.clear()

    def _fallback_chat(
        self,
        messages: List[Dict[str, object]],
        *,
        reason: str,
        request_id: str = "",
    ) -> ProviderResponse:
        if not self.settings.offline_fallback_enabled or not self.ollama_provider.configured:
            response = ProviderResponse(
                "deepseek",
                self.settings.default_model,
                error=ProviderError(
                    "fallback_unavailable",
                    "在线模型不可用，本地降级模型也未就绪。",
                ),
                route_reason=f"fallback_unavailable:{reason}",
            )
            self.last_response = response
            return response
        decision = ModelRouteDecision(
            "ollama",
            self.settings.offline_model,
            self.settings.model_mode,
            f"fallback:{reason}",
        )
        return self._call_ollama(messages, decision, request_id=request_id)

    def _call_ollama(
        self,
        messages: List[Dict[str, object]],
        decision: ModelRouteDecision,
        *,
        request_id: str = "",
    ) -> ProviderResponse:
        response = self.ollama_provider.chat(
            messages,
            model=decision.model,
            thinking=False,
            max_tokens=self.settings.max_output_tokens,
        )
        response.route_reason = decision.reason
        self._record(response, request_id=request_id)
        self.last_response = response
        return response

    def _record(self, response: ProviderResponse, *, request_id: str = "") -> None:
        self.usage_store.record(response, route_reason=response.route_reason)
        route = str(response.route_reason or "direct")
        fallback = response.provider == "ollama" and (
            route.startswith("fallback:")
            or route.startswith("json_action_fallback:")
        )
        print(
            f"[LLM] request_id={request_id or 'untracked'} "
            f"provider={response.provider or 'none'} "
            f"model={response.model or 'unknown'} route={route}",
            flush=True,
        )
        print(
            f"[LLM] tool_calls={len(response.tool_calls)} "
            f"thinking={str(bool(response.thinking or response.reasoning_content)).lower()} "
            f"fallback={str(bool(fallback)).lower()}",
            flush=True,
        )

    @staticmethod
    def _friendly_error(error: Optional[ProviderError]) -> str:
        if error is None:
            return "模型服务没有返回有效结果。"
        return error.message or "模型服务当前不可用，请稍后再试。"
