from __future__ import annotations

from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Dict, Optional
from uuid import uuid4

from modules.contracts import PendingConfirmation


class ConfirmationManager:
    """Keeps short-lived, argument-bound confirmations isolated by conversation."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 180,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.ttl_seconds = max(10, int(ttl_seconds))
        self.now_provider = now_provider
        self._pending_by_scope: Dict[str, PendingConfirmation] = {}
        self._lock = RLock()

    def create(
        self,
        tool: str,
        arguments: Dict[str, object],
        summary: str,
        *,
        scope: Optional[str] = None,
        call_id: str = "",
        risk_level: str = "",
        state_fingerprint: str = "",
    ) -> Dict[str, object]:
        now = self.now_provider()
        pending = PendingConfirmation(
            confirmation_id=uuid4().hex[:8],
            tool=str(tool),
            arguments=dict(arguments),
            created_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(timespec="seconds"),
            summary=str(summary).strip(),
            conversation_id=self._scope(scope),
            call_id=str(call_id).strip(),
            risk_level=str(risk_level).strip(),
            state_fingerprint=str(state_fingerprint).strip(),
        )
        with self._lock:
            self._pending_by_scope[self._scope(scope)] = pending
        return pending.to_dict()

    def pending(self, *, scope: Optional[str] = None) -> Optional[Dict[str, object]]:
        with self._lock:
            key = self._scope(scope)
            pending = self._pending_by_scope.get(key)
            if pending is None:
                return None
            if self._is_expired(pending):
                self._pending_by_scope.pop(key, None)
                return None
            return pending.to_dict()

    def consume(
        self,
        confirmation_id: Optional[str] = None,
        *,
        scope: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        with self._lock:
            key = self._scope(scope)
            pending = self.pending(scope=scope)
            if pending is None:
                return None
            if confirmation_id and confirmation_id != pending["confirmation_id"]:
                return None
            self._pending_by_scope.pop(key, None)
            return pending

    def cancel(self, *, scope: Optional[str] = None) -> bool:
        with self._lock:
            key = self._scope(scope)
            existed = self.pending(scope=scope) is not None
            self._pending_by_scope.pop(key, None)
            return existed

    @staticmethod
    def _scope(value: Optional[str]) -> str:
        return str(value or "").strip() or "default"

    def _is_expired(self, pending: PendingConfirmation) -> bool:
        try:
            return self.now_provider() > datetime.fromisoformat(pending.expires_at)
        except ValueError:
            return True
