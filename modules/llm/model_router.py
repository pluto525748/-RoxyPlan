from __future__ import annotations

from typing import Mapping, Optional

from modules.llm.contracts import ModelRouteDecision
from modules.llm.settings import ModelSettings


COMPLEX_INTENTS = {
    "resolve_memory_conflict",
    "daily_review",
    "save_review",
    "multi_action",
}
THINKING_INTENTS = {"resolve_memory_conflict", "daily_review", "long_planning"}


class ModelRouter:
    def __init__(self, settings: ModelSettings) -> None:
        self.settings = settings

    def route(
        self,
        context: Optional[Mapping[str, object]] = None,
        *,
        online_available: bool,
        offline_available: bool = True,
        tools_requested: bool = False,
    ) -> ModelRouteDecision:
        data = dict(context or {})
        intent = str(data.get("intent", "chat")).strip()
        if self.settings.online_enabled and online_available:
            complex_task = self._is_complex(data, intent)
            model = self.settings.default_model
            reason = "online_default"
            can_escalate = False
            if self.settings.model_mode == "quality":
                model = self.settings.complex_model
                reason = "quality_mode"
            elif (
                self.settings.model_mode == "auto"
                and complex_task
                and self.settings.enable_auto_escalation
            ):
                model = self.settings.complex_model
                reason = "complex_task"
            elif self.settings.model_mode == "auto":
                can_escalate = self.settings.enable_auto_escalation
            else:
                reason = "economy_mode"
            thinking = (
                not tools_requested
                and model == self.settings.complex_model
                and self.settings.enable_thinking_for_complex_tasks
                and self._needs_thinking(data, intent)
            )
            return ModelRouteDecision(
                "deepseek",
                model,
                self.settings.model_mode,
                reason,
                thinking=thinking,
                allow_tools=bool(tools_requested),
                can_escalate=can_escalate,
            )
        if self.settings.offline_fallback_enabled and offline_available:
            reason = "online_not_configured" if not online_available else "online_disabled"
            return ModelRouteDecision(
                "ollama",
                self.settings.offline_model,
                self.settings.model_mode,
                reason,
                thinking=False,
                allow_tools=False,
                can_escalate=False,
            )
        return ModelRouteDecision(
            "none",
            "",
            self.settings.model_mode,
            "no_provider_available",
        )

    @staticmethod
    def _is_complex(data: Mapping[str, object], intent: str) -> bool:
        return bool(
            intent in COMPLEX_INTENTS
            or data.get("multiple_actions")
            or data.get("multi_tool")
            or data.get("complex_reference")
            or data.get("memory_conflict")
            or data.get("long_review")
            or data.get("first_parse_failed")
        )

    @staticmethod
    def _needs_thinking(data: Mapping[str, object], intent: str) -> bool:
        return bool(
            intent in THINKING_INTENTS
            or data.get("memory_conflict")
            or data.get("long_review")
            or data.get("deep_planning")
        )
