from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping
from uuid import uuid4

from modules.contracts import AgentStep


@dataclass
class BatchAction:
    tool_name: str
    arguments: Dict[str, object] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    sequence_index: int = 0
    tool_call_id: str = field(default_factory=lambda: "call_" + uuid4().hex)
    action_id: str = field(default_factory=lambda: "action_" + uuid4().hex)
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            payload = json.dumps(
                {"tool": self.tool_name, "arguments": self.arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            self.idempotency_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, object]:
        return {
            "tool_call_id": self.tool_call_id,
            "action_id": self.action_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "depends_on": list(self.depends_on),
            "sequence_index": self.sequence_index,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class ActionBatch:
    conversation_id: str
    ordered_actions: List[BatchAction]
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    execution_policy: str = "best_effort"
    max_actions: int = 3
    status: str = "pending"
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    batch_id: str = field(default_factory=lambda: "batch_" + uuid4().hex)

    def __post_init__(self) -> None:
        self.max_actions = max(1, min(int(self.max_actions), 3))
        self.ordered_actions = sorted(
            list(self.ordered_actions)[: self.max_actions],
            key=lambda item: item.sequence_index,
        )
        self.dependencies = {
            str(key): [str(item) for item in value]
            for key, value in self.dependencies.items()
            if isinstance(value, list)
        }

    @classmethod
    def from_steps(
        cls,
        conversation_id: str,
        steps: Iterable[AgentStep],
        *,
        execution_policy: str = "best_effort",
        max_actions: int = 3,
        request_id: str = "",
    ) -> "ActionBatch":
        actions = []
        previous_id = ""
        for index, step in enumerate(list(steps)[:max_actions]):
            dependencies = [previous_id] if previous_id and step.depends_on_previous else []
            action = BatchAction(
                step.tool,
                dict(step.arguments),
                depends_on=dependencies,
                sequence_index=index,
                action_id=step.step_id,
            )
            actions.append(action)
            previous_id = action.action_id
        batch = cls(
            conversation_id,
            actions,
            dependencies={item.action_id: list(item.depends_on) for item in actions},
            execution_policy=execution_policy,
            max_actions=max_actions,
        )
        clean_request_id = str(request_id or "").strip()
        if clean_request_id:
            batch.batch_id = "batch_" + hashlib.sha256(
                clean_request_id.encode("utf-8")
            ).hexdigest()[:24]
        return batch

    def to_dict(self) -> Dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "conversation_id": self.conversation_id,
            "ordered_actions": [item.to_dict() for item in self.ordered_actions],
            "dependencies": {key: list(value) for key, value in self.dependencies.items()},
            "execution_policy": self.execution_policy,
            "max_actions": self.max_actions,
            "created_at": self.created_at,
            "status": self.status,
        }
