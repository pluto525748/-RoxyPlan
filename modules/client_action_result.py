from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from modules.contracts import SCHEMA_VERSION, ClientAction


VALID_CLIENT_ACTION_STATUSES = {
    "requested",
    "accepted",
    "running",
    "completed",
    "rejected",
    "skipped_busy",
    "skipped_duplicate",
    "expired",
    "failed",
    "cancelled",
}


@dataclass
class ClientActionResult:
    action_id: str
    name: str
    status: str
    accepted: bool = False
    started: bool = False
    completed: bool = False
    reason_code: str = ""
    display_message: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in VALID_CLIENT_ACTION_STATUSES:
            raise ValueError(f"Unknown client action status: {self.status}")

    @classmethod
    def rejected(
        cls,
        action: Optional[ClientAction],
        *,
        status: str,
        reason_code: str,
        display_message: str,
        error: Optional[str] = None,
    ) -> "ClientActionResult":
        return cls(
            action_id=action.action_id if action else "",
            name=action.name if action else "unknown",
            status=status,
            reason_code=reason_code,
            display_message=display_message,
            error=error,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "name": self.name,
            "status": self.status,
            "accepted": self.accepted,
            "started": self.started,
            "completed": self.completed,
            "reason_code": self.reason_code,
            "display_message": self.display_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }
