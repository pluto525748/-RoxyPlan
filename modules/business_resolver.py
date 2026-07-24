from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

from modules.chinese_entity_parser import ChineseEntityParser
from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.local_feature_extractor import LocalFeatures
from modules.memory_service import MemoryService
from modules.plan_service import PlanService
from modules.reference_resolver import ReferenceResolver
from modules.semantic_action_parser import ActionCandidate


@dataclass
class BusinessResolution:
    status: str
    candidate: ActionCandidate
    resolved_action: Optional[ActionCandidate] = None
    candidate_objects: List[Dict[str, object]] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    needs_confirmation: bool = False
    reason_code: str = ""
    safe_prompt: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "candidate": self.candidate.to_dict(),
            "resolved_action": (
                self.resolved_action.to_dict() if self.resolved_action else None
            ),
            "candidate_objects": [dict(item) for item in self.candidate_objects],
            "missing_fields": list(self.missing_fields),
            "needs_confirmation": self.needs_confirmation,
            "reason_code": self.reason_code,
            "safe_prompt": self.safe_prompt,
        }


class BusinessResolver:
    """Resolve untrusted semantic references against real local services."""

    MEMORY_READ_TOOLS = {
        "list_memories",
        "search_memories",
        "list_memory_candidates",
        "list_memory_conflicts",
        "list_archived_memories",
        "show_memory_audit",
    }

    def __init__(
        self,
        *,
        plan_service: PlanService,
        memory_service: MemoryService,
        interaction_coordinator: InteractionStateCoordinator,
        reference_resolver: Optional[ReferenceResolver] = None,
        tool_registry=None,
        entity_parser: Optional[ChineseEntityParser] = None,
        enabled: bool = True,
    ) -> None:
        self.plan_service = plan_service
        self.memory_service = memory_service
        self.interaction_coordinator = interaction_coordinator
        self.reference_resolver = reference_resolver or ReferenceResolver()
        self.tool_registry = tool_registry
        self.entity_parser = entity_parser or ChineseEntityParser()
        self.enabled = bool(enabled)

    def resolve(
        self,
        candidate: ActionCandidate,
        *,
        conversation_id: str,
        user_id: str = "local_user",
        features: Optional[LocalFeatures] = None,
        structured_references: Optional[Mapping[str, object]] = None,
    ) -> BusinessResolution:
        del user_id
        if not self.enabled:
            return BusinessResolution("resolved", candidate, candidate)
        if self.tool_registry is not None and self.tool_registry.get(candidate.tool_name) is None:
            return BusinessResolution(
                "forbidden",
                candidate,
                reason_code="tool_not_registered",
                safe_prompt="这个操作不在允许的工具列表中。",
            )
        if candidate.domain == "memory":
            return self._resolve_memory(candidate, conversation_id, features)
        if candidate.domain == "plan":
            return self._resolve_plan(
                candidate,
                conversation_id,
                features,
                dict(structured_references or {}),
            )
        if candidate.domain == "action_log":
            return self._resolve_action_log(
                candidate,
                conversation_id,
                dict(structured_references or {}),
            )
        return BusinessResolution("resolved", candidate, candidate)

    def resolve_all(
        self,
        candidates: List[ActionCandidate],
        **kwargs,
    ) -> List[BusinessResolution]:
        return [self.resolve(item, **kwargs) for item in candidates]

    def _resolve_memory(
        self,
        candidate: ActionCandidate,
        conversation_id: str,
        features: Optional[LocalFeatures],
    ) -> BusinessResolution:
        tool = candidate.tool_name
        if tool in self.MEMORY_READ_TOOLS or tool in {
            "create_memory_candidate",
            "request_add_memory",
            "queue_memory_candidate",
        }:
            return BusinessResolution("resolved", candidate, candidate)

        args = dict(candidate.arguments)
        explicit_candidate_ids = (
            features.explicit_ids.get("candidate", []) if features else []
        )
        if not explicit_candidate_ids and features:
            # In a candidate-review tool, "memory 1" unambiguously refers to
            # candidate 1. The tool domain supplies the distinction; the model
            # still cannot invent or bypass local ID validation.
            explicit_candidate_ids = features.explicit_ids.get("memory", [])
        interaction = self.interaction_coordinator.current(conversation_id)
        if tool in {
            "accept_memory_candidate",
            "reject_memory_candidate",
            "accept_memory_candidates",
            "reject_memory_candidates",
        }:
            ids = []
            if explicit_candidate_ids:
                ids = [str(item) for item in explicit_candidate_ids]
            elif interaction.selected_object_ids:
                ids = list(interaction.selected_object_ids)
            elif candidate.reference_text or candidate.raw_entities.get("reference_text"):
                resolution = self.reference_resolver.resolve_memory_candidates(
                    candidate.reference_text
                    or str(candidate.raw_entities.get("reference_text", "")),
                    {
                        "conversation_id": conversation_id,
                        "state": interaction.state,
                        "last_listed_candidate_ids": interaction.listed_object_ids,
                        "last_selected_candidate_ids": interaction.selected_object_ids,
                    },
                    conversation_id=conversation_id,
                )
                if resolution.needs_clarification:
                    return BusinessResolution(
                        "ambiguous",
                        candidate,
                        candidate_objects=self._candidate_objects(
                            interaction.listed_object_ids
                        ),
                        reason_code=resolution.reason,
                        safe_prompt="请先查看待审核记忆，再告诉我要处理哪一条。",
                    )
                ids = list(resolution.candidate_ids)
            else:
                raw = args.get("candidate_ids", [])
                if isinstance(raw, list) and raw and candidate.explicit_command:
                    ids = [str(item) for item in raw]
                elif args.get("candidate_id") is not None and candidate.explicit_command:
                    ids = [str(args.get("candidate_id"))]
            ids = self._positive_ids(ids)
            if not ids:
                return BusinessResolution(
                    "missing",
                    candidate,
                    missing_fields=["candidate_id"],
                    reason_code="candidate_reference_missing",
                    safe_prompt="你想处理哪条候选？请先查看待审核记忆或告诉我候选编号。",
                )
            existing = []
            missing = []
            for item in ids:
                result = self.memory_service.get_candidate(item)
                if result.success:
                    existing.append(result.data.get("candidate", {}))
                else:
                    missing.append(item)
            if not existing:
                return BusinessResolution(
                    "not_found",
                    candidate,
                    reason_code="candidate_not_found",
                    safe_prompt=f"没有找到候选{ids[0]}，现有记忆没有改变。",
                )
            resolved = self._clone(candidate)
            if tool.endswith("candidates"):
                resolved.arguments = {"candidate_ids": [int(item) for item in ids]}
            else:
                if len(ids) != 1:
                    resolved.tool_name = (
                        "accept_memory_candidates"
                        if tool.startswith("accept")
                        else "reject_memory_candidates"
                    )
                    resolved.arguments = {"candidate_ids": [int(item) for item in ids]}
                else:
                    resolved.arguments = {"candidate_id": int(ids[0])}
            return BusinessResolution(
                "resolved" if not missing else "partial",
                candidate,
                resolved,
                candidate_objects=[item for item in existing if isinstance(item, dict)],
                reason_code="candidate_ids_resolved",
            )

        key = "memory_id"
        explicit = features.explicit_ids.get("memory", []) if features else []
        value = explicit[0] if explicit else args.get(key)
        if value is None:
            return BusinessResolution(
                "missing",
                candidate,
                missing_fields=[key],
                reason_code="memory_reference_missing",
                safe_prompt="请告诉我要处理的长期记忆编号。",
            )
        found = self.memory_service.get_memory(value)
        if not found.success:
            return BusinessResolution(
                "not_found",
                candidate,
                reason_code="memory_not_found",
                safe_prompt=f"没有找到记忆{value}，现有记忆没有改变。",
            )
        resolved = self._clone(candidate)
        resolved.arguments[key] = int(value)
        return BusinessResolution("resolved", candidate, resolved)

    def _resolve_plan(
        self,
        candidate: ActionCandidate,
        conversation_id: str,
        features: Optional[LocalFeatures],
        references: Dict[str, object],
    ) -> BusinessResolution:
        tool = candidate.tool_name
        args = dict(candidate.arguments)
        if tool == "show_plan":
            return BusinessResolution("resolved", candidate, candidate)
        if tool == "add_plan":
            title = str(args.get("title", "") or "").strip()
            if args.get("duration_minutes") in {None, ""} and title:
                parsed_title = self.entity_parser.parse(title)
                if parsed_title.get("duration_minutes") is not None:
                    args["duration_minutes"] = parsed_title.get("duration_minutes")
                    candidate = self._clone(candidate)
                    candidate.arguments = dict(args)
            missing = []
            if not title or title in {"学一会儿", "学习一会儿", "做一会儿", "安排一下"}:
                missing.append("title")
            if not candidate.explicit_command and args.get("duration_minutes") in {None, ""}:
                missing.append("duration_minutes")
            if missing:
                return BusinessResolution(
                    "missing",
                    candidate,
                    missing_fields=missing,
                    reason_code="plan_fields_missing",
                    safe_prompt="想学什么、准备学多久？告诉我这两点后，我再帮你放进计划。",
                )
            similar = self.plan_service.find_similar_plans(title, threshold=0.82)
            if similar and not args.get("allow_duplicate"):
                return BusinessResolution(
                    "ambiguous",
                    candidate,
                    candidate_objects=similar[:3],
                    reason_code="similar_plan_exists",
                    safe_prompt=(
                        f"今天已有相近计划“{similar[0].get('title', '')}”。"
                        f"你可以更新原计划，或回复“仍然添加：{title}”保留两条。"
                    ),
                )
            return BusinessResolution("resolved", candidate, candidate)

        explicit_ids = features.explicit_ids.get("plan", []) if features else []
        reference = str(args.get("task_ref") or args.get("match_text") or "").strip()
        if explicit_ids:
            reference = str(explicit_ids[0])
        if candidate.reference_text:
            resolution = self.reference_resolver.resolve(
                candidate.reference_text,
                references,
                conversation_id=conversation_id,
                candidates=self.plan_service.list_plans(),
            )
            if resolution.needs_clarification:
                return BusinessResolution(
                    "ambiguous",
                    candidate,
                    candidate_objects=self.plan_service.list_plans()[:5],
                    reason_code=resolution.reason,
                    safe_prompt="我还不能确定你指的是哪条计划，请说计划编号或标题。",
                )
            if resolution.resolved_id:
                reference = resolution.resolved_id
        if not reference:
            return BusinessResolution(
                "missing",
                candidate,
                missing_fields=["plan_reference"],
                reason_code="plan_reference_missing",
                safe_prompt="你想处理哪条计划？请说编号或计划内容。",
            )
        lookup = self.plan_service.resolve(
            reference,
            pending_only=(tool == "complete_plan"),
        )
        if lookup.status == "ambiguous":
            ids = [str(item.get("uid") or item.get("id")) for item in lookup.candidates]
            self.interaction_coordinator.record_candidates(
                conversation_id,
                "plan_target_selection",
                ids,
                action_candidates=[candidate.to_dict()],
                safe_summary="找到多条相近计划，请选择其中一条。",
            )
            return BusinessResolution(
                "ambiguous",
                candidate,
                candidate_objects=lookup.candidates,
                reason_code=lookup.reason_code,
                safe_prompt=self._format_plan_choices(lookup.candidates),
            )
        if lookup.task is None:
            prompt = (
                "没有找到对应的未完成计划。要不要改为记录一条行动？"
                if tool == "complete_plan"
                else "没有找到对应计划，现有计划没有改变。"
            )
            return BusinessResolution(
                "not_found",
                candidate,
                reason_code=lookup.reason_code,
                safe_prompt=prompt,
            )
        resolved = self._clone(candidate)
        task_ref = (
            str(explicit_ids[0])
            if explicit_ids
            else str(lookup.task.get("uid") or lookup.task.get("id"))
        )
        if tool == "complete_plan":
            resolved.arguments = {"match_text": str(lookup.task.get("title", ""))}
        else:
            resolved.arguments["task_ref"] = task_ref
        return BusinessResolution(
            "resolved",
            candidate,
            resolved,
            candidate_objects=[lookup.task],
            reason_code="plan_resolved",
        )

    def _resolve_action_log(
        self,
        candidate: ActionCandidate,
        conversation_id: str,
        references: Dict[str, object],
    ) -> BusinessResolution:
        del conversation_id
        if candidate.tool_name == "show_action_log":
            return BusinessResolution("resolved", candidate, candidate)
        content = str(candidate.arguments.get("content", "") or "").strip()
        if candidate.reference_text == "previous_user_message" or content in {
            "",
            "这个",
            "这段",
            "这件事",
        }:
            content = str(references.get("previous_user_message", "") or "").strip()
        if not content:
            return BusinessResolution(
                "missing",
                candidate,
                missing_fields=["content"],
                reason_code="action_log_content_missing",
                safe_prompt="你想记录哪段进展？可以把内容再说一遍。",
            )
        if candidate.requires_confirmation_hint and not candidate.explicit_command:
            plan_matches = self.plan_service.find_similar_plans(
                content,
                pending_only=True,
                threshold=0.82,
            )
            if len(plan_matches) == 1:
                resolved = self._clone(candidate)
                resolved.domain = "plan"
                resolved.tool_name = "complete_plan"
                resolved.arguments = {
                    "match_text": str(plan_matches[0].get("title", ""))
                }
                resolved.confidence = max(resolved.confidence, 0.95)
                resolved.explicit_command = True
                resolved.requires_confirmation_hint = False
                resolved.ambiguities = []
                return BusinessResolution(
                    "resolved",
                    candidate,
                    resolved,
                    candidate_objects=plan_matches,
                    reason_code="progress_matches_pending_plan",
                )
            if len(plan_matches) > 1:
                return BusinessResolution(
                    "ambiguous",
                    candidate,
                    candidate_objects=plan_matches[:5],
                    reason_code="progress_matches_multiple_plans",
                    safe_prompt=self._format_plan_choices(plan_matches),
                )
        duplicate = next(
            (
                item
            for item in self.plan_service.action_records()
                if self._normalize(item.get("content", "")) == self._normalize(content)
            ),
            None,
        )
        if duplicate is not None:
            return BusinessResolution(
                "forbidden",
                candidate,
                candidate_objects=[duplicate],
                reason_code="duplicate_action_log",
                safe_prompt="这条行动今天已经记录过了，我没有重复写入。",
            )
        resolved = self._clone(candidate)
        resolved.arguments = {"content": content}
        if candidate.requires_confirmation_hint and not candidate.explicit_command:
            return BusinessResolution(
                "confirmation_required",
                candidate,
                resolved,
                needs_confirmation=True,
                reason_code="progress_statement_needs_confirmation",
                safe_prompt="要不要把这段进展记入今天的行动记录？",
            )
        return BusinessResolution("resolved", candidate, resolved)

    def _candidate_objects(self, ids: List[str]) -> List[Dict[str, object]]:
        result = []
        for value in ids:
            operation = self.memory_service.get_candidate(value)
            candidate = operation.data.get("candidate") if operation.success else None
            if isinstance(candidate, dict):
                result.append(candidate)
        return result

    @staticmethod
    def _clone(candidate: ActionCandidate) -> ActionCandidate:
        return ActionCandidate(
            domain=candidate.domain,
            tool_name=candidate.tool_name,
            arguments=dict(candidate.arguments),
            raw_entities=dict(candidate.raw_entities),
            reference_text=candidate.reference_text,
            confidence=candidate.confidence,
            explicit_command=candidate.explicit_command,
            requires_confirmation_hint=candidate.requires_confirmation_hint,
            ambiguities=list(candidate.ambiguities),
            depends_on=list(candidate.depends_on),
            sequence_index=candidate.sequence_index,
            source_intent=candidate.source_intent,
            action_id=candidate.action_id,
        )

    @staticmethod
    def _positive_ids(values) -> List[str]:
        result = []
        for value in values:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and str(parsed) not in result:
                result.append(str(parsed))
        return result

    @staticmethod
    def _format_plan_choices(candidates: List[Dict[str, object]]) -> str:
        lines = ["我找到几条可能的计划，请告诉我要处理哪一条："]
        for index, item in enumerate(candidates[:5], 1):
            lines.append(f"{index}. {item.get('title', '')}")
        return "\n".join(lines)

    @staticmethod
    def _normalize(value: object) -> str:
        return "".join(str(value or "").lower().split()).strip("。.!！?？")
