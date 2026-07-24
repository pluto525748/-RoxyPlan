from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Dict, List, Mapping, Optional
from uuid import uuid4

from modules.contracts import ensure_json_value


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class ProviderError:
    code: str
    message: str
    retryable: bool = False
    http_status: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": str(self.code),
            "message": str(self.message),
            "retryable": bool(self.retryable),
            "http_status": self.http_status,
        }

@dataclass
class ProviderToolCall:
    name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    tool_call_id: str = field(default_factory=lambda: _identifier("call"))

    def __post_init__(self) -> None:
        self.name = str(self.name).strip()
        self.arguments = dict(
            ensure_json_value(dict(self.arguments), "ProviderToolCall.arguments")
        )
        self.tool_call_id = str(self.tool_call_id).strip() or _identifier("call")

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.tool_call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": ensure_json_value(
                    self.arguments, "ProviderToolCall.arguments"
                ),
            },
        }

    def to_wire_dict(self) -> Dict[str, object]:
        return {
            "id": self.tool_call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(
                    self.arguments, ensure_ascii=False, separators=(",", ":")
                ),
            },
        }


@dataclass
class ProviderResponse:
    provider: str
    model: str
    content: str = ""
    tool_calls: List[ProviderToolCall] = field(default_factory=list)
    usage: Dict[str, object] = field(default_factory=dict)
    finish_reason: str = ""
    latency_ms: int = 0
    error: Optional[ProviderError] = None
    reasoning_content: str = ""
    route_reason: str = ""
    thinking: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, object]:
        # reasoning_content is deliberately excluded from serializable user-facing
        # results. It is available only for the provider's immediate tool loop.
        return {
            "provider": str(self.provider),
            "model": str(self.model),
            "content": str(self.content),
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "usage": ensure_json_value(self.usage, "ProviderResponse.usage"),
            "finish_reason": str(self.finish_reason),
            "latency_ms": int(self.latency_ms),
            "error": self.error.to_dict() if self.error is not None else None,
            "route_reason": str(self.route_reason),
            "thinking": bool(self.thinking),
        }

    def assistant_message(self, *, include_reasoning: bool = False) -> Dict[str, object]:
        message: Dict[str, object] = {
            "role": "assistant",
            "content": str(self.content),
        }
        if self.tool_calls:
            message["tool_calls"] = [item.to_wire_dict() for item in self.tool_calls]
        if include_reasoning and self.reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        return message


@dataclass
class ProviderHealth:
    provider: str
    status: str
    configured: bool
    model: str = ""
    checked_at: str = ""
    last_success_at: str = ""
    error_code: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "status": self.status,
            "configured": self.configured,
            "model": self.model,
            "checked_at": self.checked_at,
            "last_success_at": self.last_success_at,
            "error_code": self.error_code,
        }


@dataclass
class ModelRouteDecision:
    provider: str
    model: str
    mode: str
    reason: str
    thinking: bool = False
    allow_tools: bool = False
    can_escalate: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "reason": self.reason,
            "thinking": self.thinking,
            "allow_tools": self.allow_tools,
            "can_escalate": self.can_escalate,
        }


def parse_tool_calls(value: object) -> List[ProviderToolCall]:
    if not isinstance(value, list):
        return []
    result: List[ProviderToolCall] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        function = raw.get("function")
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name", "")).strip()
        if not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError, json.JSONDecodeError):
                arguments = {"__invalid_json__": arguments[:240]}
        if not isinstance(arguments, Mapping):
            arguments = {"__invalid_arguments__": str(arguments)[:240]}
        result.append(
            ProviderToolCall(
                name=name,
                arguments=dict(arguments),
                tool_call_id=str(raw.get("id", "")).strip() or _identifier("call"),
            )
        )
    return result
