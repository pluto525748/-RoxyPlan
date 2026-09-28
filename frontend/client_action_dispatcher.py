from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Dict, Iterable, List, Optional

from modules.client_action_policy import ClientActionPolicy
from modules.client_action_result import ClientActionResult
from modules.contracts import ClientAction


class DesktopClientActionDispatcher:
    """Validate and execute declarative Agent actions on the Qt UI thread."""

    def __init__(
        self,
        pet_controller,
        *,
        policy: Optional[ClientActionPolicy] = None,
        dedup_window_seconds: int = 30,
    ) -> None:
        self.pet_controller = pet_controller
        self.policy = policy or ClientActionPolicy()
        self.dedup_window_seconds = max(1, int(dedup_window_seconds))
        self._consumed_action_ids: Dict[str, float] = {}
        self._consumed_idempotency_keys: Dict[str, float] = {}
        self.results: Dict[str, ClientActionResult] = {}

    def dispatch_all(
        self, actions: Iterable[ClientAction]
    ) -> List[ClientActionResult]:
        return [self.dispatch(action) for action in actions]

    def dispatch(self, raw_action) -> ClientActionResult:
        allowed, action, reason = self.policy.validate(raw_action)
        if not allowed or action is None:
            status = "expired" if reason == "action_expired" else "rejected"
            result = ClientActionResult.rejected(
                action,
                status=status,
                reason_code=reason,
                display_message=self._failure_message(status),
            )
            self._log(action, result, "validation")
            return result

        self._prune_dedup()
        if (
            action.action_id in self._consumed_action_ids
            or action.idempotency_key in self._consumed_idempotency_keys
        ):
            result = ClientActionResult.rejected(
                action,
                status="skipped_duplicate",
                reason_code="duplicate_replay",
                display_message="这次动作请求已经处理过，没有重复执行。",
            )
            self._log(action, result, "duplicate")
            return result

        now = monotonic()
        self._consumed_action_ids[action.action_id] = now
        self._consumed_idempotency_keys[action.idempotency_key] = now
        if self.pet_controller is None:
            return self._store_rejection(
                action, "failed", "pet_unavailable", "桌宠当前不可用，动作没有执行。"
            )

        manager = getattr(self.pet_controller, "action_manager", None)
        state_before = str(getattr(manager, "current_state", "unknown"))
        try:
            if action.name == "show_bubble":
                self.pet_controller.show_bubble(
                    str(action.arguments["text"]),
                    int(action.arguments.get("duration_ms", 6000)),
                )
                accepted, reason_code, completed = True, "completed", True
            else:
                action_name = {
                    "play_dance": "dance",
                    "sleep": "sleep",
                    "wake": "wake",
                }.get(action.name, action.name)
                if manager is None or not hasattr(manager, "try_play_action"):
                    return self._store_rejection(
                        action,
                        "failed",
                        "manager_unavailable",
                        "桌宠动作管理器不可用，动作没有执行。",
                    )
                accepted, reason_code = manager.try_play_action(action_name)
                completed = False
            if not accepted:
                status = "skipped_busy" if reason_code == "busy" else "failed"
                message = (
                    "我刚才的动作还没结束，稍等一下再跳。"
                    if status == "skipped_busy"
                    else "这次舞蹈没有成功播放，我需要检查一下动作系统。"
                )
                return self._store_rejection(
                    action, status, reason_code, message, state_before=state_before
                )
            timestamp = datetime.now(timezone.utc).isoformat()
            result = ClientActionResult(
                action_id=action.action_id,
                name=action.name,
                status="completed" if completed else "running",
                accepted=True,
                started=True,
                completed=completed,
                reason_code=reason_code,
                display_message=(
                    "好，我跳一小段。" if action.name == "play_dance" else ""
                ),
                started_at=timestamp,
                completed_at=timestamp if completed else None,
            )
            self.results[action.action_id] = result
            self._log(action, result, state_before)
            return result
        except Exception as error:
            return self._store_rejection(
                action,
                "failed",
                "dispatcher_exception",
                "这次舞蹈没有成功播放，我需要检查一下动作系统。",
                error=type(error).__name__,
                state_before=state_before,
            )

    def _store_rejection(
        self,
        action: ClientAction,
        status: str,
        reason_code: str,
        message: str,
        *,
        error: Optional[str] = None,
        state_before: str = "unknown",
    ) -> ClientActionResult:
        result = ClientActionResult.rejected(
            action,
            status=status,
            reason_code=reason_code,
            display_message=message,
            error=error,
        )
        self.results[action.action_id] = result
        self._log(action, result, state_before)
        return result

    def _prune_dedup(self) -> None:
        cutoff = monotonic() - self.dedup_window_seconds
        self._consumed_action_ids = {
            key: value for key, value in self._consumed_action_ids.items() if value >= cutoff
        }
        self._consumed_idempotency_keys = {
            key: value
            for key, value in self._consumed_idempotency_keys.items()
            if value >= cutoff
        }

    @staticmethod
    def _failure_message(status: str) -> str:
        if status == "expired":
            return "这次动作请求已经过期了，可以再叫我一次。"
        return "这次动作请求没有通过本地安全校验。"

    @staticmethod
    def _log(action, result: ClientActionResult, state_before: str) -> None:
        action_id = action.action_id if action is not None else "unknown"
        name = action.name if action is not None else "unknown"
        state_after = "unknown"
        print(
            "[CLIENT_ACTION] "
            f"action_id={action_id} name={name} dispatcher_received=true "
            f"state_before={state_before} result={result.status} "
            f"reason_code={result.reason_code} state_after={state_after}",
            flush=True,
        )
