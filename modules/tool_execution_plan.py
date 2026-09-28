from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from modules.action_batch import ActionBatch
from modules.contracts import ToolResult
from modules.tool_executor import ToolExecutor


@dataclass
class BatchExecutionResult:
    status: str
    results: List[ToolResult] = field(default_factory=list)
    skipped_action_ids: List[str] = field(default_factory=list)
    changed_resource_ids: List[str] = field(default_factory=list)


class ToolExecutionPlan:
    """Bounded sequential batch executor over the existing ToolExecutor."""

    def __init__(
        self,
        executor: ToolExecutor,
        *,
        result_validator: Optional[Callable[[ToolResult], ToolResult]] = None,
    ) -> None:
        self.executor = executor
        self.result_validator = result_validator
        self._completed_idempotency_keys = set()

    def execute(
        self,
        batch: ActionBatch,
        *,
        confidence: float,
        authorization_granted: bool = False,
        confirmation_scope: str = "",
        require_confirmation: bool = False,
        negated: bool = False,
        informational: bool = False,
    ) -> BatchExecutionResult:
        results: List[ToolResult] = []
        skipped: List[str] = []
        changed: List[str] = []
        success_by_action: Dict[str, bool] = {}
        for action in batch.ordered_actions:
            if any(not success_by_action.get(item, False) for item in action.depends_on):
                skipped.append(action.action_id)
                success_by_action[action.action_id] = False
                continue
            scoped_key = f"{batch.batch_id}:{action.idempotency_key}"
            if scoped_key in self._completed_idempotency_keys:
                skipped.append(action.action_id)
                success_by_action[action.action_id] = True
                continue
            result = self.executor.execute(
                action.tool_name,
                action.arguments,
                confidence=confidence,
                authorization_granted=authorization_granted,
                confirmation_scope=confirmation_scope,
                require_confirmation=require_confirmation,
                negated=negated,
                informational=informational,
                tool_call_id=action.tool_call_id,
            )
            if self.result_validator is not None:
                result = self.result_validator(result)
            results.append(result)
            success_by_action[action.action_id] = result.success
            if result.success:
                self._completed_idempotency_keys.add(scoped_key)
                changed.extend(self._changed_ids(result))
            if result.error == "confirmation_required":
                batch.status = "confirmation_required"
                return BatchExecutionResult(batch.status, results, skipped, self._unique(changed))
        succeeded = sum(item.success for item in results)
        if succeeded and (succeeded < len(results) or skipped):
            batch.status = "partial_success"
        elif results and succeeded == len(results):
            batch.status = "completed"
        else:
            batch.status = "failed"
        return BatchExecutionResult(batch.status, results, skipped, self._unique(changed))

    @staticmethod
    def _changed_ids(result: ToolResult) -> List[str]:
        data = result.data
        values = []
        for key in (
            "changed_resource_ids",
            "accepted_ids",
            "rejected_ids",
            "memory_id",
            "candidate_id",
        ):
            value = data.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            elif value not in (None, ""):
                values.append(str(value))
        for key in ("task", "memory", "candidate", "record"):
            value = data.get(key)
            if isinstance(value, dict):
                identity = value.get("uid") or value.get("id")
                if identity not in (None, ""):
                    values.append(str(identity))
        return values

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        result = []
        for value in values:
            if value not in result:
                result.append(value)
        return result
