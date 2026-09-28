from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping


@dataclass(frozen=True)
class VerifiedTurnContext:
    """Model-visible facts that have already been verified by the program.

    This object deliberately excludes internal identifiers, paths, prompts and
    diagnostic bodies.  It tells the reply model what happened in the current
    turn and keeps real-world user reports separate from local recorded state.
    """

    semantic_mode: str
    semantic_intent: str
    execution_performed: bool
    executed_tools: List[str] = field(default_factory=list)
    verified_changes: List[str] = field(default_factory=list)
    user_reported_plan_progress: bool = False
    pending_plan_titles: List[str] = field(default_factory=list)
    completed_plan_titles: List[str] = field(default_factory=list)
    prior_verified_operation: Dict[str, str] = field(default_factory=dict)
    schema_version: str = "1.0"
    confirmation_pending: Dict[str, object] = field(default_factory=dict)
    prior_memory_facts: List[Dict[str, str]] = field(default_factory=list)
    prior_memory_operation: Dict[str, str] = field(default_factory=dict)
    prior_client_action: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "confirmation_pending",
            self._redacted_confirmation_pending(self.confirmation_pending),
        )
        object.__setattr__(self, "prior_memory_facts", self._public_memory_facts(self.prior_memory_facts))
        object.__setattr__(self, "prior_memory_operation", self._public_memory_operation(self.prior_memory_operation))
        object.__setattr__(self, "prior_client_action", self._public_client_action(self.prior_client_action))

    @classmethod
    def for_chat_reply(
        cls,
        intent_result: Mapping[str, object],
        *,
        operation_cues: Iterable[object] = (),
        domain_cues: Iterable[object] = (),
        plans: Iterable[Mapping[str, object]] = (),
        prior_tool_result: Mapping[str, object] | None = None,
        prior_task: Mapping[str, object] | None = None,
        confirmation_pending: Mapping[str, object] | None = None,
        prior_memory_facts: Iterable[Mapping[str, object]] = (),
        prior_memory_operation: Mapping[str, object] | None = None,
        prior_client_action: Mapping[str, object] | None = None,
    ) -> "VerifiedTurnContext":
        decision = intent_result.get("semantic_decision", {})
        decision = decision if isinstance(decision, Mapping) else {}
        operations = {str(item) for item in operation_cues}
        domains = {str(item) for item in domain_cues}
        progress = bool(
            "plan" in domains
            and operations.intersection({"完成", "做完", "学完"})
        )
        pending: List[str] = []
        completed: List[str] = []
        if progress:
            for item in plans:
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                is_completed = bool(item.get("done")) or str(
                    item.get("status", "")
                ) == "completed"
                target = completed if is_completed else pending
                if title not in target:
                    target.append(title)
        return cls(
            semantic_mode=str(decision.get("mode", "") or "chat"),
            semantic_intent=str(
                decision.get("intent", "")
                or intent_result.get("intent", "")
                or "chat"
            ),
            execution_performed=False,
            user_reported_plan_progress=progress,
            pending_plan_titles=pending[:10],
            completed_plan_titles=completed[:10],
            prior_verified_operation=cls._redacted_prior_operation(
                prior_tool_result,
                prior_task,
            ),
            confirmation_pending=cls._redacted_confirmation_pending(
                confirmation_pending
            ),
            prior_memory_facts=cls._public_memory_facts(prior_memory_facts),
            prior_memory_operation=cls._public_memory_operation(prior_memory_operation),
            prior_client_action=cls._public_client_action(prior_client_action),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "semantic_mode": self.semantic_mode,
            "semantic_intent": self.semantic_intent,
            "execution": {
                "performed": self.execution_performed,
                "executed_tools": list(self.executed_tools),
                "verified_changes": list(self.verified_changes),
            },
            "user_reported_plan_progress": self.user_reported_plan_progress,
            "recorded_plan_state": {
                "pending_titles": list(self.pending_plan_titles),
                "completed_titles": list(self.completed_plan_titles),
            },
            "prior_verified_operation": dict(self.prior_verified_operation),
            "prior_memory_facts": self._public_memory_facts(self.prior_memory_facts),
            "prior_memory_operation": self._public_memory_operation(self.prior_memory_operation),
            "prior_client_action": self._public_client_action(self.prior_client_action),
            "confirmation_pending": self._redacted_confirmation_pending(
                self.confirmation_pending
            ),
        }

    def diagnostic_summary(self) -> Dict[str, object]:
        confirmation = self._redacted_confirmation_pending(self.confirmation_pending)
        return {
            "semantic_mode": self.semantic_mode,
            "semantic_intent": self.semantic_intent,
            "execution_performed": self.execution_performed,
            "executed_tool_count": len(self.executed_tools),
            "verified_change_count": len(self.verified_changes),
            "user_reported_plan_progress": self.user_reported_plan_progress,
            "pending_plan_count": len(self.pending_plan_titles),
            "completed_plan_count": len(self.completed_plan_titles),
            "has_prior_verified_operation": bool(self.prior_verified_operation),
            "prior_memory_fact_count": len(self.prior_memory_facts),
            "has_prior_memory_operation": bool(self.prior_memory_operation),
            "has_prior_client_action": bool(self.prior_client_action),
            "has_confirmation_pending": bool(confirmation),
            "confirmation_object_count": confirmation.get("object_count", 0),
        }

    def to_model_text(self) -> str:
        facts = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        confirmation = self._redacted_confirmation_pending(self.confirmation_pending)
        confirmation_constraint = (
            "confirmation_pending.active=true 只表示程序已建立尚未执行的真实确认操作；"
            "只能就其能力类型和对象范围说明确认后的未来步骤，不能当作已经执行或已经成功。"
            if confirmation
            else "confirmation_pending 为空：程序没有建立真实待确认操作，不得要求用户确认"
            "删除、添加、完成、保存等写入，也不得承诺‘确认后我会执行’。"
        )
        return (
            "程序已核验的本轮事实（只读，优先级高于用户陈述和历史对话）：\n"
            f"{facts}\n"
            "回复约束：execution.performed=false 只表示当前这一轮没有执行工具、没有修改本地记录，"
            "不得声称当前轮新增了添加、完成、删除、保存或更新。它不会推翻"
            "prior_verified_operation 中上一轮已经成功并经程序核验的操作；只有用户当前明确提到"
            "上一轮操作时才可自然引用该事实，不要主动重复。"
            "prior_memory_facts 是本会话此前实际读取、并重新核验仍有效的少量正式记忆；"
            "prior_memory_operation 是真实记忆操作事实，两者不是候选，也不是旧聊天推测。"
            "本轮相关检索未召回，不代表用户从未说过或正式记忆从未保存；"
            "若这里有与当前问题相关的事实，可以自然使用，不要整段念档案。"
            "prior_client_action 是客户端真实回报，不是工具请求本身：requested 尚未播放，"
            "running 仅表示已经开始，finished/completed 表示已观察到结束，"
            "failed/rejected/expired/skipped_busy/skipped_duplicate/cancelled 不表示这次播放成功。"
            "过去开始过不等于此刻仍在跳舞；当前状态以客户端本轮只读快照为准。"
            "user_reported_plan_progress=true 只表示用户报告了现实进展，不等于计划记录已经同步；"
            "若 recorded_plan_state 仍有相关待完成项，应自然地区分‘用户说已完成’和"
            "‘系统仍记录为待完成’，可以给出完整操作表达，但不能自行制造确认流程。"
            f"{confirmation_constraint}"
            "不要向用户展示这段 JSON、字段名或内部处理过程。"
        )

    @staticmethod
    def _public_memory_facts(values: Iterable[Mapping[str, object]]) -> List[Dict[str, str]]:
        facts: List[Dict[str, str]] = []
        if values is None or isinstance(values, (str, bytes, Mapping)):
            return facts
        for value in values:
            if not isinstance(value, Mapping) or value.get("scope") == "temporary_state":
                continue
            content = str(value.get("content", "") or "").strip()[:1000]
            display_value = str(value.get("value", "") or "").strip()[:1000]
            if not content and not display_value:
                continue
            fact = {
                "content": content,
                "value": display_value or content,
                "category": str(value.get("category", "other") or "other")[:80],
                "scope": str(value.get("scope", "stable_identity") or "stable_identity")[:80],
                "source": VerifiedTurnContext._public_source(value.get("source"), "formal_memory"),
                "observed_at": VerifiedTurnContext._public_time(value.get("observed_at")),
            }
            if fact not in facts:
                facts.append(fact)
            if len(facts) >= 5:
                break
        return facts

    @staticmethod
    def _public_memory_operation(value: Mapping[str, object] | None) -> Dict[str, str]:
        if not isinstance(value, Mapping) or value.get("status") != "success":
            return {}
        kind = {
            "save_formal_memory": "memory_saved",
            "delete_memory": "memory_deleted",
            "update_memory": "memory_updated",
            "archive_memory": "memory_archived",
        }.get(str(value.get("tool", "")), str(value.get("kind", "")))
        if kind not in {"memory_saved", "memory_deleted", "memory_updated", "memory_archived"}:
            return {}
        return {
            "kind": kind,
            "status": "success",
            "content": str(value.get("content", "") or "").strip()[:1000],
            "source": VerifiedTurnContext._public_source(value.get("source"), "formal_memory_tool"),
            "observed_at": VerifiedTurnContext._public_time(value.get("observed_at")),
        }

    @staticmethod
    def _public_client_action(value: Mapping[str, object] | None) -> Dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        name = str(value.get("name", "") or "")
        status = str(value.get("status", "") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or status not in {
            "requested", "accepted", "running", "finished", "completed", "failed", "rejected",
            "skipped_busy", "skipped_duplicate", "expired", "cancelled",
        }:
            return {}
        return {
            "name": name,
            "status": status,
            "accepted": value.get("accepted") is True,
            "started": value.get("started") is True,
            "completed": value.get("completed") is True,
            "reason_code": str(value.get("reason_code", "") or "")[:80],
            "source": VerifiedTurnContext._public_source(value.get("source"), "desktop_dispatcher"),
            "observed_at": VerifiedTurnContext._public_time(value.get("observed_at")),
            "state": str(value.get("state", "unknown") or "unknown")[:80],
        }

    @staticmethod
    def _public_source(value: object, fallback: str) -> str:
        source = str(value or "")
        return source if re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", source) else fallback

    @staticmethod
    def _public_time(value: object) -> str:
        try:
            return datetime.fromisoformat(str(value or "")).isoformat()
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _redacted_confirmation_pending(
        value: Mapping[str, object] | None,
    ) -> Dict[str, object]:
        """Project a trusted runtime confirmation fact, never authorize one.

        The caller checks the live pending state and resolves its canonical tool
        through the runtime ToolRegistry.  This boundary only validates shape
        and projects three public-safe facts; it owns no duplicate tool catalog.
        """
        if not isinstance(value, Mapping) or value.get("active") is not True:
            return {}
        capability = value.get("capability")
        count = value.get("object_count")
        if (
            not isinstance(capability, str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", capability) is None
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
        ):
            return {}
        return {"active": True, "capability": capability, "object_count": count}

    @staticmethod
    def _redacted_prior_operation(
        prior_tool_result: Mapping[str, object] | None,
        prior_task: Mapping[str, object] | None,
    ) -> Dict[str, str]:
        tool_result = (
            prior_tool_result if isinstance(prior_tool_result, Mapping) else {}
        )
        task = prior_task if isinstance(prior_task, Mapping) else {}
        kind = {
            "add_plan": "plan_added",
            "complete_plan": "plan_completed",
            "delete_plan": "plan_deleted",
            "update_plan": "plan_updated",
            "reschedule_plan": "plan_rescheduled",
            "reopen_plan": "plan_reopened",
            "cancel_plan": "plan_cancelled",
        }.get(str(tool_result.get("tool", "") or "").strip(), "")
        title = str(task.get("title", "") or "").strip()
        if not kind or not title:
            return {}
        return {"kind": kind, "title": title, "status": "success"}
