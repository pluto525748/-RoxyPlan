from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, Mapping, Optional, Tuple, Union

from modules.contracts import ClientAction


ACTION_SCHEMAS: Dict[str, Dict[str, Dict[str, object]]] = {
    "nod": {},
    "jump": {},
    "shake": {},
    "show_bubble": {
        "text": {"type": "string", "required": True, "max_length": 300},
        "duration_ms": {"type": "integer", "required": False, "minimum": 1000, "maximum": 15000},
    },
    "play_dance": {
        "dance_id": {"type": "nullable_string", "required": False, "max_length": 80},
    },
    "sleep": {},
    "wake": {},
    "scale": {},
}


class ClientActionPolicy:
    """Shared allowlist for Agent responses and local desktop execution."""

    def __init__(
        self, now_provider: Optional[Callable[[], datetime]] = None
    ) -> None:
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def validate(
        self,
        action: Union[ClientAction, Mapping[str, object]],
        *,
        check_expiry: bool = True,
    ) -> Tuple[bool, Optional[ClientAction], str]:
        try:
            item = action if isinstance(action, ClientAction) else ClientAction.from_dict(action)
        except (TypeError, ValueError):
            return False, None, "invalid_action_contract"

        schema = ACTION_SCHEMAS.get(item.name)
        if schema is None:
            return False, None, "action_not_allowed"
        valid, reason = self._validate_arguments(schema, item.arguments)
        if not valid:
            return False, None, reason
        if check_expiry and self.is_expired(item):
            return False, None, "action_expired"
        return True, item, "allowed"

    def require_valid(
        self,
        action: Union[ClientAction, Mapping[str, object]],
        *,
        check_expiry: bool = True,
    ) -> ClientAction:
        allowed, normalized, reason = self.validate(action, check_expiry=check_expiry)
        if not allowed or normalized is None:
            raise ValueError(f"Client action rejected: {reason}")
        return normalized

    def is_expired(self, action: ClientAction) -> bool:
        if not action.expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(action.expires_at)
            now = self.now_provider()
            if expires_at.tzinfo is not None and now.tzinfo is None:
                # A naive provider represents local wall time. Converting with
                # astimezone preserves that instant; replace(tzinfo=UTC) would
                # make UTC+8 actions appear eight hours old immediately.
                now = now.astimezone()
            elif expires_at.tzinfo is None and now.tzinfo is not None:
                expires_at = expires_at.astimezone()
            return now > expires_at
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _validate_arguments(
        schema: Dict[str, Dict[str, object]], arguments: Dict[str, object]
    ) -> Tuple[bool, str]:
        extras = set(arguments) - set(schema)
        if extras:
            return False, "unexpected_parameter"
        for name, rules in schema.items():
            if bool(rules.get("required")) and name not in arguments:
                return False, "missing_parameter"
            if name not in arguments:
                continue
            value = arguments[name]
            expected = rules.get("type")
            if expected == "string":
                if not isinstance(value, str) or not value.strip():
                    return False, "invalid_parameter_type"
                if len(value) > int(rules.get("max_length", 10000)):
                    return False, "parameter_out_of_range"
            elif expected == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    return False, "invalid_parameter_type"
                if value < int(rules.get("minimum", value)) or value > int(rules.get("maximum", value)):
                    return False, "parameter_out_of_range"
            elif expected == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return False, "invalid_parameter_type"
                if float(value) < float(rules.get("minimum", value)) or float(value) > float(rules.get("maximum", value)):
                    return False, "parameter_out_of_range"
            elif expected == "nullable_string":
                if value is None:
                    continue
                if not isinstance(value, str):
                    return False, "invalid_parameter_type"
                if len(value) > int(rules.get("max_length", 10000)):
                    return False, "parameter_out_of_range"
            else:
                return False, "unsupported_schema_type"
        return True, "allowed"
