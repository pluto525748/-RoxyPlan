from __future__ import annotations

import json
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from modules.contracts import ToolResult
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.intent_router import match_plan_task
from modules.memory_service import MemoryOperationResult, MemoryService
from modules.plan_service import PlanService


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    parameters_schema: Dict[str, Dict[str, object]]
    handler: Callable[..., ToolResult]
    requires_confirmation: bool = False
    enabled: bool = True
    confirmation_summary: Optional[Callable[[Dict[str, object]], str]] = None
    model_visible: bool = False
    side_effect: bool = True
    sequential: bool = True
    parallel_safe: bool = False
    confirmation_policy: str = "when_ambiguous"
    reversible: bool = False
    operation_kind: str = ""
    # Deterministic alias maps. The registry is the single source of truth;
    # Normalizer, Validator and Repair prompts consume these rather than
    # maintaining their own copies.
    aliases: List[str] = field(default_factory=list)
    field_aliases: Dict[str, str] = field(default_factory=dict)
    # Fields safe to drop silently during programmatic repair because they
    # carry no business semantics (e.g. model hallucinated metadata).
    safe_ignorable_fields: List[str] = field(default_factory=list)
    # Per-parameter enum aliases for deterministic resolution before model
    # repair.  Keyed by parameter name, each value is {alias: canonical}.
    enum_aliases: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Process-local target state for confirmation guards; never exported as
    # model parameters or persisted alongside the confirmation fingerprint.
    confirmation_state: Optional[Callable[[Dict[str, object]], object]] = None
    confirmation_lock: Optional[Callable[[], AbstractContextManager]] = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        if tool.operation_kind not in {"read", "write"}:
            tool.operation_kind = "write" if tool.side_effect else "read"
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(str(name))

    def names(self, *, enabled_only: bool = False) -> List[str]:
        return [
            name
            for name, tool in self._tools.items()
            if not enabled_only or tool.enabled
        ]

    def model_tool_schemas(self) -> List[Dict[str, object]]:
        return [
            {
                "type": "function",
                "function": dict(item["function"]),
            }
            for item in self.model_tool_contracts()
        ]

    def model_tool_contracts(self) -> List[Dict[str, object]]:
        """Export JSON-only metadata without exposing handlers or managers."""
        schemas: List[Dict[str, object]] = []
        for tool in self._tools.values():
            if not tool.enabled or not tool.model_visible:
                continue
            properties: Dict[str, object] = {}
            required: List[str] = []
            for name, rules in tool.parameters_schema.items():
                item: Dict[str, object] = {"type": str(rules.get("type", "string"))}
                description = str(rules.get("description", "")).strip()
                if description:
                    item["description"] = description
                for source, target in (
                    ("enum", "enum"),
                    ("minimum", "minimum"),
                    ("maximum", "maximum"),
                    ("min_length", "minLength"),
                    ("max_length", "maxLength"),
                    ("minLength", "minLength"),
                    ("maxLength", "maxLength"),
                    ("pattern", "pattern"),
                    ("minItems", "minItems"),
                    ("maxItems", "maxItems"),
                    ("uniqueItems", "uniqueItems"),
                ):
                    if source in rules:
                        item[target] = rules[source]
                if item["type"] == "array" and isinstance(rules.get("items"), dict):
                    item_rules = dict(rules["items"])
                    item_schema = {"type": str(item_rules.get("type", "string"))}
                    for key in ("minimum", "maximum", "enum"):
                        if key in item_rules:
                            item_schema[key] = item_rules[key]
                    item["items"] = item_schema
                properties[name] = item
                if bool(rules.get("required")):
                    required.append(name)
            parameters: Dict[str, object] = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                parameters["required"] = required
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters,
                    },
                    "risk_level": tool.risk_level,
                    "side_effect": tool.side_effect,
                    "operation_kind": tool.operation_kind,
                    "confirmation_policy": tool.confirmation_policy,
                    "reversible": tool.reversible,
                    "sequential": tool.sequential,
                    "parallel_safe": tool.parallel_safe,
                    "model_visible": tool.model_visible,
                }
            )
        return schemas

    def tool_name_aliases(self) -> Dict[str, str]:
        """Return {alias: canonical_name} for every registered tool.

        The Normalizer consumes this map; Validator and Repair prompts
        derive their allowed-name lists from the same registry so there
        is never a hand-maintained copy.
        """
        result: Dict[str, str] = {}
        for tool in self._tools.values():
            for alias in tool.aliases:
                existing = result.get(alias)
                if existing is not None and existing != tool.name:
                    raise ValueError(
                        f"alias conflict: {alias!r} maps to both "
                        f"{existing!r} and {tool.name!r}"
                    )
                result[alias] = tool.name
        return result

    def field_aliases_for(self, tool_name: str) -> Dict[str, str]:
        """Return {alias_field: canonical_field} for *tool_name* only."""
        tool = self._tools.get(str(tool_name))
        if tool is None:
            return {}
        return dict(tool.field_aliases)

    def all_field_aliases(self) -> Dict[str, Dict[str, str]]:
        """Return {tool_name: {alias_field: canonical_field}} for every tool."""
        return {
            name: dict(tool.field_aliases)
            for name, tool in self._tools.items()
            if tool.field_aliases
        }

    def model_visible_parameter_docs(self) -> str:
        """Generate read/write contracts from enabled model-visible schemas.

        Returns a single compact string suitable for inclusion in the
        semantic-decision prompt.  This is the single source of truth —
        no hand-written parameter lists anywhere else.
        """
        lines = []
        for tool in self._tools.values():
            if not tool.enabled or not tool.model_visible:
                continue
            params = tool.parameters_schema
            if not params:
                continue
            param_descs = []
            for name, schema in params.items():
                if not isinstance(schema, dict):
                    continue
                desc = schema.get("description", "")
                req = "required" if schema.get("required") else "optional"
                constraints = [req, str(schema.get("type", "string"))]
                if "enum" in schema:
                    constraints.append(
                        "enum=" + json.dumps(schema["enum"], ensure_ascii=False)
                    )
                param_descs.append(
                    f"{name}({','.join(constraints)})"
                    + (f":{desc}" if desc else "")
                )
            aliases = tool.field_aliases
            alias_note = ""
            if aliases:
                alias_list = ", ".join(
                    f"{a}->{c}" for a, c in sorted(aliases.items())
                )
                alias_note = f" [aliases: {alias_list}]"
            lines.append(
                f"  {tool.name}: {', '.join(param_descs)}{alias_note}"
            )
        return (
            "Tool parameter contracts:\n" + "\n".join(lines)
        ) if lines else ""

    def safe_ignorable_fields_for(self, tool_name: str) -> List[str]:
        """Return fields safe to drop silently for *tool_name*."""
        tool = self._tools.get(str(tool_name))
        if tool is None:
            return []
        return list(tool.safe_ignorable_fields)

    def enum_aliases_for(self, tool_name: str) -> Dict[str, Dict[str, str]]:
        """Return {param_name: {alias_value: canonical_value}} for *tool_name*."""
        tool = self._tools.get(str(tool_name))
        if tool is None:
            return {}
        return {
            param: dict(aliases)
            for param, aliases in tool.enum_aliases.items()
        }

    def json_fallback_instruction(self) -> str:
        compact = [
            {
                "name": item["function"]["name"],
                "description": item["function"]["description"],
                "parameters": item["function"]["parameters"],
            }
            for item in self.model_tool_contracts()
        ]
        return (
            "只返回 JSON，不要输出解释或 Markdown。先判断用户是在要求立即执行已有能力，"
            "还是只在询问、讨论或征求建议。要求执行时必须选择已注册工具，不能因为你"
            "自己无法操作界面就返回普通聊天。Choose exactly one of: "
            '{"kind":"chat"}, '
            '{"kind":"clarification","question":"..."}, or '
            '{"kind":"action","actions":[{"call_id":"call_1",'
            '"tool_name":"...","arguments":{}}]}. '
            "Never invent a tool or parameter. A wish or discussion is not an "
            "instruction; use chat or clarification. Available tools: "
            + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        )


def create_roxy_tool_registry(
    growth_manager,
    memory_manager,
    pet_controller=None,
    memory_governance=None,
    chat_history_manager=None,
    memory_service=None,
    plan_postcondition_enabled: bool = True,
) -> ToolRegistry:
    registry = ToolRegistry()
    memory_service = memory_service or MemoryService(
        memory_manager,
        getattr(memory_governance, "candidate_manager", None),
        memory_governance,
    )
    memory_manager = memory_service.memory_manager
    memory_governance = memory_service.governance
    plan_store = growth_manager.plan_store
    plan_service = PlanService(growth_manager)
    action_store = growth_manager.action_store
    growth_store = growth_manager.growth_store

    def result(tool, success, message, data=None, error=None, display_message=""):
        return ToolResult(
            success,
            tool,
            message,
            data or {},
            error,
            status="completed" if success else "failed",
            message_code=message,
            display_message=display_message or message,
        )

    def memory_result(
        tool: str,
        operation_result: MemoryOperationResult,
        *,
        message: str = "",
    ) -> ToolResult:
        data = dict(operation_result.data)
        data["memory_operation"] = operation_result.to_dict()
        return ToolResult(
            operation_result.success,
            tool,
            message or operation_result.status,
            data,
            operation_result.error_code,
            status=operation_result.status,
            message_code=operation_result.status,
            display_message=operation_result.safe_message,
        )

    def task_list(date: str = "", *, include_cancelled: bool = False):
        try:
            return plan_store.tasks(
                str(date).strip() or None,
                include_cancelled=include_cancelled,
            )
        except TypeError:
            return plan_store.tasks()

    def resolve_task(
        reference: object,
        *,
        include_cancelled: bool = True,
        date: str = "",
    ):
        value = str(reference or "").strip()
        tasks = task_list(date, include_cancelled=include_cancelled)
        if value.startswith("task_"):
            task = next(
                (item for item in tasks if str(item.get("uid", "")) == value),
                None,
            )
            return {
                "status": "matched" if task else "not_found",
                "task": task,
                "candidates": [],
            }
        if value.isdigit():
            task = next(
                (item for item in tasks if int(item.get("id", 0)) == int(value)),
                None,
            )
            return {"status": "matched" if task else "not_found", "task": task, "candidates": []}
        return match_plan_task(value, tasks)

    def _task_identity(task) -> str:
        if not isinstance(task, dict):
            return ""
        return str(task.get("uid") or task.get("id") or "")

    def _read_task(task):
        if not isinstance(task, dict):
            return None
        uid = str(task.get("uid", "")).strip()
        task_id = task.get("id")
        task_date = str(task.get("date", "")).strip()
        for item in task_list(task_date, include_cancelled=True):
            if not isinstance(item, dict):
                continue
            if uid and str(item.get("uid", "")) == uid:
                return dict(item)
            if not uid and item.get("id") == task_id and (
                not task_date or str(item.get("date", "")) == task_date
            ):
                return dict(item)
        return None

    def _matches_changes(task, changes) -> bool:
        return bool(
            isinstance(task, dict)
            and all(task.get(key) == value for key, value in dict(changes).items())
        )

    def _verify_task_changes(task, changes):
        verified = _read_task(task)
        return verified if _matches_changes(verified, changes) else None

    def add_plan(
        title: str,
        allow_duplicate: bool = False,
        date: str = "",
        time_slot: str = "",
        duration_minutes: Optional[int] = None,
    ):
        normalize_title = getattr(plan_store, "_normalize_title", None)
        if not callable(normalize_title):
            normalize_title = lambda value: str(value or "").strip().casefold()
        target_title = normalize_title(title)
        exact = [
            dict(item)
            for item in task_list()
            if isinstance(item, dict)
            and normalize_title(item.get("title", "")) == target_title
        ]
        if exact and not allow_duplicate:
            return result(
                "add_plan",
                False,
                "similar_plan",
                {"candidates": exact[:3], "proposed_title": title},
                "ambiguous",
                "已经有一条很接近的计划。",
            )
        fields = _plan_fields_from_text(title)
        if str(date).strip():
            fields["date"] = str(date).strip()
        if str(time_slot).strip():
            fields["time_slot"] = str(time_slot).strip()
        if duration_minutes is not None:
            fields["duration_minutes"] = int(duration_minutes)
        try:
            task = plan_store.add_task(title, **fields)
        except TypeError:
            task = plan_store.add_task(title)
        task_uid = task.get("uid") if isinstance(task, dict) else None
        task_id = task.get("id") if isinstance(task, dict) else None
        task_title = task.get("title") if isinstance(task, dict) else None
        exists = any(
            (
                task_uid is not None
                and item.get("uid") == task_uid
            )
            or (
                task_uid is None
                and task_id is not None
                and item.get("id") == task_id
                and item.get("title") == task_title
            )
            for item in task_list()
            if isinstance(item, dict)
        )
        return result(
            "add_plan",
            exists,
            "added" if exists else "verification_failed",
            {
                "task": task,
                "changed_resource_ids": [str(task_uid or task_id)],
                "postcondition_verified": True,
            }
            if exists
            else {},
            None if exists else "postcondition_failed",
        )

    def show_plan(date: str = ""):
        clean_date = str(date).strip()
        data = {"tasks": task_list(clean_date)}
        if clean_date:
            data["date"] = clean_date
        return result(
            "show_plan",
            True,
            "listed",
            data,
        )

    def inspect_plan_duplicates(date: str = ""):
        clean_date = str(date).strip()
        groups = plan_service.find_duplicate_groups(date=clean_date or None)
        return result(
            "inspect_plan_duplicates",
            True,
            "duplicates_found" if groups else "no_duplicates",
            {"groups": groups, **({"date": clean_date} if clean_date else {})},
        )

    def complete_plan(match_text: str):
        if str(match_text).strip().isdigit():
            task, changed = plan_store.complete_by_id(int(str(match_text).strip()))
            if task is None:
                return result("complete_plan", False, "not_found", error="not_found")
            if (
                changed
                and pet_controller is not None
                and hasattr(pet_controller, "notify_plan_completed")
            ):
                pet_controller.notify_plan_completed()
            verified_task = _read_task(task) if plan_postcondition_enabled else task
            verified = bool(verified_task and verified_task.get("done"))
            return result(
                "complete_plan",
                verified,
                "completed" if verified else "verification_failed",
                {
                    "task": verified_task or task,
                    "changed": changed,
                    "changed_resource_ids": [_task_identity(task)],
                    "postcondition_verified": verified,
                },
                None if verified else "postcondition_failed",
            )
        match = resolve_task(match_text, include_cancelled=False)
        if match["status"] != "matched" or not isinstance(match.get("task"), dict):
            return result(
                "complete_plan", False, "not_completed",
                {"candidates": match.get("candidates", [])}, str(match["status"]),
            )
        task, changed = plan_store.complete_by_id(int(match["task"]["id"]))
        if task is None:
            return result("complete_plan", False, "not_found", error="not_found")
        if changed and pet_controller is not None and hasattr(pet_controller, "notify_plan_completed"):
            pet_controller.notify_plan_completed()
        verified_task = _read_task(task) if plan_postcondition_enabled else task
        verified = bool(verified_task and verified_task.get("done"))
        return result(
            "complete_plan",
            verified,
            "completed" if verified else "verification_failed",
            {
                "task": verified_task or task,
                "changed": changed,
                "changed_resource_ids": [_task_identity(task)],
                "postcondition_verified": verified,
            },
            None if verified else "postcondition_failed",
        )

    def delete_plan(task_ref: str):
        reference = str(task_ref).strip()
        task = None
        if reference.isdigit():
            task = plan_store.delete_by_id(int(reference))
        else:
            match = resolve_task(reference)
            if match["status"] == "matched" and isinstance(match.get("task"), dict):
                task = plan_store.delete_by_id(int(match["task"]["id"]))
        task_uid = task.get("uid") if isinstance(task, dict) else None
        task_id = task.get("id") if isinstance(task, dict) else None
        verified = task is not None and all(
            (
                str(item.get("uid")) != str(task_uid)
                if task_uid is not None
                else item.get("id") != task_id
            )
            for item in task_list(include_cancelled=True)
            if isinstance(item, dict)
        )
        return result(
            "delete_plan",
            verified,
            "deleted" if verified else "not_found",
            {
                "task": task,
                "changed_resource_ids": [_task_identity(task)],
                "postcondition_verified": verified,
            }
            if task
            else {},
            None if verified else "not_found",
        )

    def update_plan(task_ref: str, changes: Dict[str, object]):
        match = resolve_task(task_ref)
        if match.get("status") != "matched" or not isinstance(match.get("task"), dict):
            return result(
                "update_plan",
                False,
                str(match.get("status", "not_found")),
                {"candidates": match.get("candidates", [])},
                str(match.get("status", "not_found")),
            )
        original = match["task"]
        task, changed, reason = plan_store.update_task(
            int(original["id"]),
            changes,
            date=str(original.get("date") or "") or None,
        )
        verified = _verify_task_changes(task, changes) if plan_postcondition_enabled else task
        return result(
            "update_plan",
            bool(verified is not None and (changed or _matches_changes(verified, changes))),
            reason if verified is not None else "verification_failed",
            {
                "task": verified,
                "changes": changes,
                "changed_resource_ids": [_task_identity(verified)],
                "postcondition_verified": True,
            }
            if verified is not None
            else {},
            None if verified is not None else "postcondition_failed",
        )

    def merge_plan(
        target_ref: str,
        duplicate_refs: Optional[List[str]] = None,
        changes: Optional[Dict[str, object]] = None,
        date: str = "",
    ):
        clean_date = str(date).strip()
        target_match = resolve_task(target_ref, date=clean_date)
        if target_match.get("status") != "matched" or not isinstance(
            target_match.get("task"), dict
        ):
            return result(
                "merge_plan", False, "target_not_found", error="not_found"
            )
        target = dict(target_match["task"])
        duplicates = []
        seen = {_task_identity(target)}
        for reference in list(duplicate_refs or []):
            match = resolve_task(reference, date=clean_date)
            task = match.get("task")
            identity = _task_identity(task)
            if match.get("status") != "matched" or not isinstance(task, dict):
                return result(
                    "merge_plan", False, "duplicate_not_found", error="not_found"
                )
            if identity in seen:
                continue
            seen.add(identity)
            duplicates.append(dict(task))
        target_date = str(target.get("date") or "")
        if any(str(item.get("date") or "") != target_date for item in duplicates):
            return result(
                "merge_plan", False, "different_dates", error="merge_conflict"
            )
        merge = getattr(plan_store, "merge_tasks", None)
        if not callable(merge):
            return result(
                "merge_plan", False, "unsupported", error="merge_unsupported"
            )
        merged, removed, changed, reason = merge(
            int(target["id"]),
            [int(item["id"]) for item in duplicates],
            dict(changes or {}),
            date=target_date or None,
        )
        verified = _read_task(merged)
        removed_ids = {_task_identity(item) for item in removed}
        remaining_ids = {
            _task_identity(item)
            for item in task_list(target_date, include_cancelled=True)
            if isinstance(item, dict)
        }
        postcondition_verified = bool(
            changed
            and verified is not None
            and not (removed_ids & remaining_ids)
        )
        return result(
            "merge_plan",
            postcondition_verified,
            reason if postcondition_verified else "verification_failed",
            {
                "task": verified,
                "removed_tasks": removed,
                "changes": dict(changes or {}),
                "changed_resource_ids": [
                    _task_identity(verified),
                    *[_task_identity(item) for item in removed],
                ],
                "postcondition_verified": postcondition_verified,
            }
            if verified is not None
            else {},
            None if postcondition_verified else "postcondition_failed",
        )

    def reschedule_plan(task_ref: str, schedule_text: str):
        match = resolve_task(task_ref)
        if match.get("status") != "matched" or not isinstance(match.get("task"), dict):
            return result(
                "reschedule_plan",
                False,
                str(match.get("status", "not_found")),
                {"candidates": match.get("candidates", [])},
                str(match.get("status", "not_found")),
            )
        task = match["task"]
        changes = _schedule_changes(schedule_text, growth_manager.now_provider())
        updated, changed, reason = plan_store.update_task(
            int(task["id"]),
            changes,
            date=str(task.get("date") or "") or None,
        )
        verified = _verify_task_changes(updated, changes) if plan_postcondition_enabled else updated
        return result(
            "reschedule_plan",
            bool(verified is not None and (changed or _matches_changes(verified, changes))),
            reason if verified is not None else "verification_failed",
            {
                "task": verified,
                "changes": changes,
                "changed_resource_ids": [_task_identity(verified)],
                "postcondition_verified": True,
            }
            if verified is not None
            else {},
            None if verified is not None else "postcondition_failed",
        )

    def reopen_plan(task_ref: str):
        match = resolve_task(task_ref)
        if match.get("status") != "matched" or not isinstance(match.get("task"), dict):
            return result("reopen_plan", False, "not_found", error="not_found")
        original = match["task"]
        task, changed = plan_store.reopen_task(
            int(original["id"]), date=str(original.get("date") or "") or None
        )
        verified = _read_task(task) if plan_postcondition_enabled else task
        is_open = bool(verified and not verified.get("done") and verified.get("status") != "cancelled")
        return result(
            "reopen_plan",
            is_open,
            "reopened" if changed else "already_open",
            {
                "task": verified,
                "changed": changed,
                "changed_resource_ids": [_task_identity(verified)],
                "postcondition_verified": is_open,
            }
            if verified
            else {},
            None if is_open else "postcondition_failed",
        )

    def cancel_plan(task_ref: str):
        match = resolve_task(task_ref)
        if match.get("status") != "matched" or not isinstance(match.get("task"), dict):
            return result("cancel_plan", False, "not_found", error="not_found")
        original = match["task"]
        task, changed = plan_store.cancel_task(
            int(original["id"]), date=str(original.get("date") or "") or None
        )
        return result(
            "cancel_plan",
            task is not None,
            "cancelled" if changed else "already_cancelled",
            {"task": task, "changed": changed} if task else {},
            None if task else "not_found",
        )

    def add_action_log(content: str):
        record = action_store.add_record(content, source="chat")
        return result(
            "add_action_log",
            True,
            "already_recorded" if record.get("duplicate") else "recorded",
            {"record": record},
        )

    def show_action_log():
        return result(
            "show_action_log",
            True,
            "listed",
            {"records": action_store.records_for_date()},
        )

    def generate_daily_review():
        review = growth_manager.generate_review()
        return result("generate_daily_review", True, "generated", {"review": review})

    def save_daily_review(date: str = ""):
        selected_date = str(date).strip()
        # Legacy GrowthService accepts no date argument; the V2.2 manager adds
        # an optional date for explicit manual regeneration. Keep both desktop
        # composition roots compatible without weakening the new path.
        entry = (
            growth_manager.save_today_review(selected_date)
            if selected_date
            else growth_manager.save_today_review()
        )
        return result("save_daily_review", True, "saved", {"entry": entry})

    def show_growth_log(month: str = ""):
        selected_month = str(month).strip()
        if selected_month in {"本月", "这个月", "当前月", "当月"}:
            selected_month = ""
        if hasattr(growth_store, "entries_for_month"):
            entries = growth_store.entries_for_month(selected_month or None)
            statistics = growth_store.monthly_statistics(selected_month or None)
            return result(
                "show_growth_log",
                True,
                "listed",
                {
                    "month": statistics["month"],
                    "statistics": statistics,
                    "entries": entries,
                },
            )
        entries = growth_store.entries()
        return result("show_growth_log", True, "listed", {"entries": entries[-7:]})

    def _listed_memories(
        category: str = "",
        query_mode: str = "overview",
        attribute: str = "",
        topic: str = "",
        query: str = "",
    ):
        return memory_service.read_typed_memory(
            category=str(category or "").strip() or None,
            query_mode=query_mode,
            attribute=attribute,
            topic=topic,
            query=query,
        )

    def list_memories(
        category: str = "",
        query_mode: str = "overview",
        attribute: str = "",
        topic: str = "",
        query: str = "",
    ):
        return memory_result(
            "list_memories",
            _listed_memories(category, query_mode, attribute, topic, query),
            message="listed",
        )

    def show_memory(category: str = ""):
        return memory_result("show_memory", _listed_memories(category), message="listed")

    def search_memories(query: str):
        return memory_result(
            "search_memories",
            memory_service.search_memories(query),
            message="listed",
        )

    def search_memory(query: str):
        return memory_result(
            "search_memory",
            memory_service.search_memories(query),
            message="listed",
        )

    def create_memory_candidate(content: str, source_text: str = ""):
        operation = memory_service.create_candidate(
            content,
            source_text=source_text or content,
            source="explicit_request",
            explicit=True,
        )
        raw_status = str(operation.data.get("status", ""))
        return memory_result(
            "create_memory_candidate",
            operation,
            message=raw_status or operation.status,
        )

    def request_add_memory(content: str):
        operation = memory_service.save_formal_memory(
            content,
            source="explicit_request",
        )
        return memory_result(
            "request_add_memory",
            operation,
            message=operation.status,
        )

    def save_formal_memory(
        content: str,
        category: str = "",
        source: str = "explicit_user_command",
        confirmed: bool = False,
    ):
        operation = memory_service.save_formal_memory(
            content,
            category=category or None,
            source=source or "explicit_user_command",
            confirmed=bool(confirmed),
        )
        return memory_result(
            "save_formal_memory",
            operation,
            message=operation.status,
        )

    def queue_memory_candidate(content: str, source_text: str = ""):
        operation = memory_service.create_candidate(
            content,
            source_text=source_text or content,
            source="conversation",
            explicit=False,
        )
        raw_status = str(operation.data.get("status", ""))
        return memory_result(
            "queue_memory_candidate",
            operation,
            message=raw_status or operation.status,
        )

    def list_memory_candidates():
        return memory_result(
            "list_memory_candidates",
            memory_service.list_candidates(status="pending"),
            message="listed",
        )

    def list_archived_memories():
        return memory_result(
            "list_archived_memories",
            memory_service.list_memories(status="archived"),
            message="listed",
        )

    def show_memory_candidates():
        return memory_result(
            "show_memory_candidates",
            memory_service.list_candidates(status="pending"),
            message="listed",
        )

    def accept_memory_candidate(candidate_id: int):
        operation = memory_service.accept_candidate(
            candidate_id,
            source="chat_confirmation",
        )
        raw_status = str(operation.data.get("status", ""))
        return memory_result(
            "accept_memory_candidate",
            operation,
            message=raw_status or operation.status,
        )

    def reject_memory_candidate(candidate_id: int):
        operation = memory_service.reject_candidate(
            candidate_id,
            source="chat_confirmation",
        )
        return memory_result(
            "reject_memory_candidate",
            operation,
            message="rejected" if operation.success else operation.status,
        )

    def accept_memory_candidates(candidate_ids: List[int]):
        return memory_result(
            "accept_memory_candidates",
            memory_service.accept_candidates(
                candidate_ids,
                source="chat_confirmation",
            ),
        )

    def reject_memory_candidates(candidate_ids: List[int]):
        return memory_result(
            "reject_memory_candidates",
            memory_service.reject_candidates(
                candidate_ids,
                source="chat_confirmation",
            ),
        )

    def accept_all_memory_candidates():
        return memory_result(
            "accept_all_memory_candidates",
            memory_service.accept_all_pending(source="chat_confirmation"),
        )

    def update_memory(memory_id: int, changes: Dict[str, object]):
        return memory_result(
            "update_memory",
            memory_service.update_memory(memory_id, **changes),
        )

    def list_memory_conflicts():
        return memory_result(
            "list_memory_conflicts",
            memory_service.list_conflicts(status="pending"),
            message="listed",
        )

    def show_memory_conflicts():
        return memory_result(
            "show_memory_conflicts",
            memory_service.list_conflicts(status="pending"),
            message="listed",
        )

    def resolve_memory_conflict(conflict_id: int, resolution: str, merged_content: str = ""):
        return memory_result(
            "resolve_memory_conflict",
            memory_service.resolve_conflict(
                conflict_id,
                resolution,
                merged_content=merged_content or None,
                source="chat_confirmation",
            ),
        )

    def show_memory_audit():
        return memory_result(
            "show_memory_audit",
            memory_service.list_audit(),
            message="listed",
        )

    def archive_memory(memory_id: int):
        return memory_result(
            "archive_memory",
            memory_service.archive_memory(memory_id),
            message="archived",
        )

    def restore_memory(memory_id: int):
        return memory_result(
            "restore_memory",
            memory_service.restore_memory(memory_id),
            message="restored",
        )

    def delete_memory(memory_id: int):
        return memory_result(
            "delete_memory",
            memory_service.delete_memory(memory_id),
            message="deleted",
        )

    def delete_memory_confirmation_state(arguments: Dict[str, object]):
        operation = memory_service.get_memory(arguments.get("memory_id"))
        if not operation.success:
            if operation.status == "not_found":
                raise LookupError("confirmation_target_missing")
            raise RuntimeError("confirmation_target_unavailable")
        memory = operation.data.get("memory")
        if not isinstance(memory, dict) or not str(memory.get("content", "")).strip():
            raise RuntimeError("confirmation_target_invalid")
        # Reading a fact during an unrelated reply may legitimately update its
        # usage metadata. Only actual identity/content/business-state changes
        # should invalidate the user's deletion preview.
        return {
            key: value
            for key, value in memory.items()
            if key not in {"use_count", "last_used", "last_used_at"}
        }

    def delete_memory_confirmation_summary(arguments: Dict[str, object]) -> str:
        memory = delete_memory_confirmation_state(arguments)
        return (
            f"删除长期记忆 {memory.get('id')}：{memory['content']}。"
            "此操作不可逆，不会清除旧会话或备份。"
        )

    def delete_all_memories(scope: str):
        if str(scope) != "all":
            return result("delete_all_memories", False, "invalid_scope", error="invalid_scope")
        return memory_result(
            "delete_all_memories",
            memory_service.delete_all_memories(),
            message="deleted_all",
        )

    def show_recent_conversation(conversation_id: str, current_message: str = ""):
        if chat_history_manager is None:
            return result("show_recent_conversation", False, "unavailable", error="history_unavailable")
        messages = chat_history_manager.recent_messages(conversation_id, 8)
        if messages and messages[-1].get("role") == "user" and str(
            messages[-1].get("content", "")
        ).strip() == str(current_message).strip():
            messages = messages[:-1]
        return result(
            "show_recent_conversation",
            True,
            "listed",
            {"messages": messages[-6:]},
        )

    def show_conversation_history(
        current_conversation_id: str = "",
        exclude_today: bool = False,
        query: str = "",
        limit: int = 5,
    ):
        if chat_history_manager is None:
            return result("show_conversation_history", False, "unavailable", error="history_unavailable")
        sessions = chat_history_manager.sessions()
        if current_conversation_id:
            sessions = [
                item
                for item in sessions
                if str(item.get("session_id", "")) != current_conversation_id
            ]
        if exclude_today:
            today = chat_history_manager.now_provider().date().isoformat()
            sessions = [
                item
                for item in sessions
                if not str(
                    item.get("updated_at") or item.get("started_at") or ""
                ).startswith(today)
            ]
        bounded_limit = max(1, min(int(limit), 10))
        clean_query = str(query or "").strip()
        sessions_by_id = {
            str(item.get("session_id", "")): item
            for item in sessions
            if isinstance(item, dict) and str(item.get("session_id", ""))
        }
        entries = []
        if clean_query:
            summaries = chat_history_manager.relevant_summaries(
                clean_query,
                exclude_session_id=current_conversation_id,
                limit=bounded_limit,
                char_budget=1600,
            )
            for summary in summaries:
                session_id = str(summary.get("session_id", ""))
                session = sessions_by_id.get(session_id)
                if session is None:
                    continue
                time_range = summary.get("time_range", {})
                time_range = time_range if isinstance(time_range, dict) else {}
                source_time = str(
                    time_range.get("end")
                    or time_range.get("start")
                    or session.get("updated_at")
                    or session.get("started_at")
                    or ""
                )
                if exclude_today and source_time.startswith(
                    chat_history_manager.now_provider().date().isoformat()
                ):
                    continue
                summary_text = str(summary.get("summary", "")).strip()
                if not summary_text:
                    continue
                entries.append(
                    {
                        "session_id": session_id,
                        "title": session.get("title", "新对话"),
                        "source_date": source_time[:10],
                        "source": "local_conversation_summary",
                        "summary": summary_text,
                        "snippet": summary_text[:180].rstrip(),
                        "message_count": session.get("message_count", 0),
                    }
                )
        else:
            for session in sessions[:bounded_limit]:
                session_id = str(session.get("session_id", ""))
                summary_text = chat_history_manager.get_summary(session_id)
                messages = chat_history_manager.messages(session_id)
                latest_message = next(
                    (
                        item
                        for item in reversed(messages)
                        if isinstance(item, dict)
                        and str(item.get("content", "")).strip()
                    ),
                    {},
                )
                snippet = str(latest_message.get("content", "")).strip()[:180]
                if not summary_text and not snippet:
                    continue
                source_time = str(
                    latest_message.get("created_at")
                    or session.get("updated_at")
                    or session.get("started_at")
                    or ""
                )
                entries.append(
                    {
                        "session_id": session_id,
                        "title": session.get("title", "新对话"),
                        "source_date": source_time[:10],
                        "source": (
                            "local_conversation_summary"
                            if summary_text
                            else "local_conversation_message"
                        ),
                        "summary": summary_text,
                        "snippet": snippet,
                        "message_count": session.get("message_count", 0),
                    }
                )
        return result(
            "show_conversation_history",
            True,
            "listed",
            {
                "query": clean_query,
                "sessions": entries,
            },
        )

    def pet_call(tool: str, method: str, success_message: str):
        if pet_controller is None or not hasattr(pet_controller, method):
            return result(tool, False, "unavailable", error="pet_unavailable")
        value = getattr(pet_controller, method)()
        success = value is not False
        return result(tool, success, success_message if success else "unavailable", error=None if success else "action_failed")

    def client_action_request(tool: str, name: str, arguments=None):
        """Return a declarative desktop request without touching Qt objects."""
        return result(
            tool,
            True,
            "requested",
            {
                "client_action": {
                    "name": name,
                    "arguments": dict(arguments or {}),
                    "source": "tool_registry",
                }
            },
            display_message="",
        )

    def merge_confirmation_summary(arguments: Dict[str, object]) -> str:
        clean_date = str(arguments.get("date", "")).strip()
        target_match = resolve_task(arguments.get("target_ref"), date=clean_date)
        target = target_match.get("task")
        target = dict(target) if isinstance(target, dict) else {}
        changes = arguments.get("changes", {})
        changes = dict(changes) if isinstance(changes, dict) else {}
        preview = {**target, **changes}
        duplicate_refs = arguments.get("duplicate_refs", [])
        duplicate_refs = list(duplicate_refs) if isinstance(duplicate_refs, list) else []
        source_tasks = [target] if target else []
        for reference in duplicate_refs:
            match = resolve_task(reference, date=clean_date)
            if isinstance(match.get("task"), dict):
                source_tasks.append(dict(match["task"]))
        all_completed = bool(source_tasks) and bool(duplicate_refs) and all(
            bool(item.get("done")) and item.get("status") == "completed"
            for item in source_tasks
        )
        details = " ".join(
            item
            for item in (
                str(preview.get("time_slot", "")).strip(),
                f"{preview.get('duration_minutes')}分钟"
                if preview.get("duration_minutes")
                else "",
            )
            if item
        )
        return (
            f"合并预览：保留“{preview.get('title', '')}”"
            f"{'（' + details + '）' if details else ''}；"
            f"合并后为{'已完成' if all_completed else '待完成'}；"
            f"将移除 {len(duplicate_refs)} 条重复计划。"
        )

    definitions = (
        ToolDefinition(
            "add_plan",
            "把用户明确要求今天要做、安排或放进清单的事项添加为今日计划；仅提供建议或询问建议时不要调用",
            "medium",
            {
                "title": {"type": "string", "required": True, "minLength": 1, "maxLength": 240},
                "allow_duplicate": {"type": "boolean", "required": False},
                "date": {"type": "string", "required": False, "maxLength": 10},
                "time_slot": {"type": "string", "required": False, "enum": ["上午", "中午", "下午", "晚上"]},
                "duration_minutes": {"type": "integer", "required": False, "minimum": 1, "maximum": 1440},
            },
            add_plan,
            confirmation_summary=lambda arguments: (
                f"把“{arguments.get('title', '')}”加入今日计划。"
            ),
            aliases=["add_plan_form", "create_plan", "new_plan"],
            field_aliases={"plan_name": "title", "task": "title", "name": "title", "content": "title", "task_title": "title"},
            safe_ignorable_fields=["call_id", "_index", "_order"],
            enum_aliases={"time_slot": {"今晚": "晚上", "今夜": "晚上"}},
        ),
        ToolDefinition(
            "show_plan",
            "查看指定日期计划，未提供日期时查看今日计划",
            "low",
            {"date": {"type": "string", "required": False, "maxLength": 10}},
            show_plan,
        ),
        ToolDefinition(
            "inspect_plan_duplicates",
            "只读检查指定日期中内容相近、可由用户决定是否合并的计划",
            "low",
            {"date": {"type": "string", "required": False, "maxLength": 10}},
            inspect_plan_duplicates,
        ),
        ToolDefinition("complete_plan", "完成匹配的今日计划", "medium", {"match_text": {"type": "string", "required": True, "description": "要匹配并完成的现有计划名称或关键词"}}, complete_plan, aliases=["mark_plan_complete", "finish_plan", "complete_task"], field_aliases={"task_id": "match_text", "id": "match_text", "plan_id": "match_text", "task_ref": "match_text", "query": "match_text", "title": "match_text"}),
        ToolDefinition(
            "delete_plan",
            "删除指定计划",
            "high",
            {"task_ref": {"type": "string", "required": True}},
            delete_plan,
            True,
            confirmation_summary=lambda arguments: _task_confirmation_summary(
                resolve_task, arguments.get("task_ref"), "删除"
            ),
            aliases=["remove_plan", "erase_plan"],
            field_aliases={"task_id": "task_ref", "id": "task_ref", "plan_id": "task_ref", "query": "task_ref"},
        ),
        ToolDefinition("update_plan", "更新指定计划", "medium", {"task_ref": {"type": "string", "required": True}, "changes": {"type": "object", "required": True}}, update_plan, aliases=["edit_plan", "modify_plan"], field_aliases={"task_id": "task_ref", "id": "task_ref"}),
        ToolDefinition(
            "merge_plan",
            "把已经确认属于同一事项的计划合并；必须先展示合并预览并得到用户确认",
            "high",
            {
                "target_ref": {"type": "string", "required": True},
                "duplicate_refs": {
                    "type": "array",
                    "required": False,
                    "maxItems": 10,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "changes": {"type": "object", "required": False},
                "date": {"type": "string", "required": False, "maxLength": 10},
            },
            merge_plan,
            True,
            confirmation_summary=merge_confirmation_summary,
            aliases=["combine_plans", "deduplicate_plans"],
            field_aliases={"task_ref": "target_ref", "source_refs": "duplicate_refs"},
        ),
        ToolDefinition("reschedule_plan", "调整计划时间", "medium", {"task_ref": {"type": "string", "required": True}, "schedule_text": {"type": "string", "required": True}}, reschedule_plan, aliases=["move_plan"], field_aliases={"task_id": "task_ref", "new_time": "schedule_text", "time": "schedule_text"}),
        ToolDefinition("reopen_plan", "重新打开计划", "medium", {"task_ref": {"type": "string", "required": True}}, reopen_plan, aliases=["restore_plan", "uncomplete_plan"], field_aliases={"task_id": "task_ref", "id": "task_ref"}),
        ToolDefinition(
            "cancel_plan",
            "取消但保留指定计划",
            "high",
            {"task_ref": {"type": "string", "required": True}},
            cancel_plan,
            True,
            confirmation_summary=lambda arguments: (
                f"取消计划 {arguments.get('task_ref')}。计划会保留为已取消，可稍后重新打开。"
            ),
            aliases=["abort_plan"],
            field_aliases={"task_id": "task_ref", "id": "task_ref"},
        ),
        ToolDefinition(
            "add_action_log",
            "记录用户已经真实完成或推进的事情；没有匹配计划的新完成事项优先使用此工具",
            "medium",
            {"content": {"type": "string", "required": True}},
            add_action_log,
            confirmation_summary=lambda arguments: (
                f"把“{arguments.get('content', '')}”加入行动记录。"
            ),
            aliases=["log_action", "record_action", "log_progress", "add_action"],
            field_aliases={"text": "content", "message": "content", "description": "content", "summary": "content"},
            safe_ignorable_fields=["call_id", "_index", "_order"],
        ),
        ToolDefinition("show_action_log", "查看今日行动记录", "low", {}, show_action_log),
        ToolDefinition("generate_daily_review", "生成今日复盘", "low", {}, generate_daily_review, aliases=["daily_review", "review_today", "today_review"]),
        ToolDefinition("save_daily_review", "保存或明确重新生成指定日期复盘", "medium", {"date": {"type": "string", "required": False, "maxLength": 10}}, save_daily_review, aliases=["save_review"]),
        ToolDefinition("show_growth_log", "按自然月查看成长日志和确定性统计", "low", {"month": {"type": "string", "required": False, "maxLength": 7, "description": "YYYY-MM；查询当前自然月时留空"}}, show_growth_log),
        ToolDefinition(
            "list_memories",
            "按类型只读取当前正式且未归档的长期记忆，不修改任何数据",
            "low",
            {
                "category": {"type": "string", "required": False, "maxLength": 80},
                "query_mode": {"type": "string", "required": False, "enum": ["overview", "attribute", "existence", "provenance"]},
                "attribute": {"type": "string", "required": False, "enum": ["preferred_name", "preference", "habit", "goal", "project", "current_state", "fact"]},
                "topic": {"type": "string", "required": False, "maxLength": 80},
                "query": {"type": "string", "required": False, "maxLength": 500},
            },
            list_memories,
            aliases=["show_memories", "list_memory", "get_memories"],
            field_aliases={"type": "category", "filter": "category"},
        ),
        ToolDefinition("list_archived_memories", "只读取已归档长期记忆，不恢复或修改记忆", "low", {}, list_archived_memories, aliases=["show_archived_memories", "get_archived_memories"]),
        ToolDefinition("search_memories", "按内容搜索正式长期记忆", "low", {"query": {"type": "string", "required": True, "maxLength": 500}}, search_memories, aliases=["find_memories", "find_memory"], field_aliases={"text": "query", "search": "query", "keyword": "query", "keywords": "query"}),
        ToolDefinition("show_memory", "查看长期记忆（兼容旧命令）", "low", {}, show_memory),
        ToolDefinition("search_memory", "搜索长期记忆（兼容旧命令）", "low", {"query": {"type": "string", "required": True, "maxLength": 500}}, search_memory, field_aliases={"text": "query", "search": "query", "keyword": "query"}),
        ToolDefinition("save_formal_memory", "将用户明确授权保存的完整内容直接写入正式长期记忆；内容不明确时由解析和校验层澄清", "medium", {"content": {"type": "string", "required": True, "maxLength": 1000}, "category": {"type": "string", "required": False, "maxLength": 80}, "source": {"type": "string", "required": False, "maxLength": 80}, "confirmed": {"type": "boolean", "required": False}}, save_formal_memory, aliases=["save_memory", "add_memory", "remember", "create_memory"], field_aliases={"text": "content", "memory_content": "content", "value": "content", "memory_text": "content"}),
        ToolDefinition("create_memory_candidate", "内部兼容：写入旧候选结构；不属于普通聊天用户能力", "medium", {"content": {"type": "string", "required": True, "maxLength": 1000}, "source_text": {"type": "string", "required": False, "maxLength": 1200}}, create_memory_candidate),
        ToolDefinition("request_add_memory", "兼容旧入口：保存用户明确要求记住的正式长期记忆", "medium", {"content": {"type": "string", "required": True}}, request_add_memory),
        ToolDefinition("queue_memory_candidate", "内部兼容：将记录写入旧候选结构", "medium", {"content": {"type": "string", "required": True}, "source_text": {"type": "string", "required": False}}, queue_memory_candidate),
        ToolDefinition("list_memory_candidates", "内部兼容：只读取旧候选及真实编号", "low", {}, list_memory_candidates),
        ToolDefinition("show_memory_candidates", "内部兼容：查看旧候选记录", "low", {}, show_memory_candidates),
        ToolDefinition("accept_memory_candidate", "内部兼容：接受一条旧候选记录", "medium", {"candidate_id": {"type": "integer", "required": True, "minimum": 1}}, accept_memory_candidate),
        ToolDefinition("accept_memory_candidates", "内部兼容：接受多条旧候选记录", "medium", {"candidate_ids": {"type": "array", "required": True, "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}}}, accept_memory_candidates),
        ToolDefinition("reject_memory_candidate", "内部兼容：忽略一条旧候选记录", "medium", {"candidate_id": {"type": "integer", "required": True, "minimum": 1}}, reject_memory_candidate),
        ToolDefinition("reject_memory_candidates", "内部兼容：忽略多条旧候选记录", "medium", {"candidate_ids": {"type": "array", "required": True, "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}}}, reject_memory_candidates),
        ToolDefinition("accept_all_memory_candidates", "内部兼容：接受当前真实存在的全部旧候选", "medium", {}, accept_all_memory_candidates),
        ToolDefinition("update_memory", "更新指定的正式长期记忆", "high", {"memory_id": {"type": "integer", "required": True, "minimum": 1}, "changes": {"type": "object", "required": True}}, update_memory, True, confirmation_summary=lambda arguments: f"更新长期记忆 {arguments.get('memory_id')}。"),
        ToolDefinition("list_memory_conflicts", "列出待处理的记忆冲突", "low", {}, list_memory_conflicts),
        ToolDefinition("show_memory_conflicts", "查看记忆冲突", "low", {}, show_memory_conflicts),
        ToolDefinition(
            "resolve_memory_conflict",
            "解决记忆冲突",
            "high",
            {"conflict_id": {"type": "integer", "required": True, "minimum": 1}, "resolution": {"type": "string", "required": True, "enum": ["keep_old", "use_new", "merge", "keep_both", "defer"]}, "merged_content": {"type": "string", "required": False, "maxLength": 1000}},
            resolve_memory_conflict,
            True,
            confirmation_summary=lambda arguments: (
                f"按 {arguments.get('resolution')} 处理记忆冲突 "
                f"{arguments.get('conflict_id')}。"
            ),
        ),
        ToolDefinition("show_memory_audit", "查看记忆治理审计", "low", {}, show_memory_audit),
        ToolDefinition("archive_memory", "归档长期记忆", "high", {"memory_id": {"type": "integer", "required": True, "minimum": 1}}, archive_memory, True, confirmation_summary=lambda arguments: f"归档长期记忆 {arguments.get('memory_id')}。"),
        ToolDefinition("restore_memory", "恢复已归档记忆", "high", {"memory_id": {"type": "integer", "required": True, "minimum": 1}}, restore_memory, True, confirmation_summary=lambda arguments: f"恢复已归档记忆 {arguments.get('memory_id')}。"),
        ToolDefinition(
            "delete_memory", "删除长期记忆", "high",
            {"memory_id": {"type": "integer", "required": True, "minimum": 1}},
            delete_memory, True,
            confirmation_summary=delete_memory_confirmation_summary,
            confirmation_state=delete_memory_confirmation_state,
            confirmation_lock=lambda: memory_service.repository.transaction("memory"),
        ),
        ToolDefinition(
            "delete_all_memories",
            "删除全部长期记忆",
            "high",
            {"scope": {"type": "string", "required": True}},
            delete_all_memories,
            True,
            confirmation_summary=lambda _arguments: (
                "删除全部长期记忆，共 "
                f"{len(memory_service.list_memories(status=None).data.get('memories', []))} 条。"
                "此操作不可逆。"
            ),
        ),
        ToolDefinition("show_recent_conversation", "查看当前会话最近内容", "low", {"conversation_id": {"type": "string", "required": True}, "current_message": {"type": "string", "required": False}}, show_recent_conversation),
        ToolDefinition(
            "show_conversation_history",
            "查看本地历史会话摘要，不要让模型自行猜测过去聊过什么",
            "low",
            {
                "current_conversation_id": {"type": "string", "required": False},
                "exclude_today": {"type": "boolean", "required": False},
                "query": {
                    "type": "string",
                    "required": False,
                    "maxLength": 500,
                    "description": "要在旧会话摘要中模糊匹配的主题；泛查历史时可省略",
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            show_conversation_history,
        ),
        ToolDefinition("pause_reminders", "暂停主动提醒", "medium", {}, lambda: pet_call("pause_reminders", "pause_proactive_reminders", "paused")),
        ToolDefinition("resume_reminders", "恢复主动提醒", "medium", {}, lambda: pet_call("resume_reminders", "resume_proactive_reminders", "resumed")),
        ToolDefinition(
            "play_dance",
            "当用户要求桌宠现在跳舞、来一段或展示舞蹈时播放已有舞蹈动画；讨论舞蹈、询问是否会跳或否定表达时不要调用",
            "low",
            {},
            lambda: client_action_request(
                "play_dance", "play_dance", {"dance_id": None}
            ),
        ),
        ToolDefinition("sleep_pet", "让桌宠进入睡眠", "medium", {}, lambda: client_action_request("sleep_pet", "sleep")),
        ToolDefinition("wake_pet", "唤醒桌宠", "low", {}, lambda: client_action_request("wake_pet", "wake")),
    )
    for definition in definitions:
        capability = DEFAULT_CAPABILITY_REGISTRY.for_tool(definition.name)
        if capability is None:
            raise ValueError(
                f"Tool is missing from capability registry: {definition.name}"
            )
        # CapabilityRegistry is the sole authority for model scope and policy.
        # Compatibility aliases remain callable locally, but only a capability's
        # canonical tool name may be exposed to the model.
        definition.model_visible = (
            capability.model_visible
            and definition.name == capability.canonical_tool_name
        )
        definition.side_effect = capability.side_effect
        definition.parallel_safe = not capability.side_effect
        definition.sequential = capability.side_effect
        definition.risk_level = capability.risk_level
        definition.confirmation_policy = capability.confirmation_policy
        definition.requires_confirmation = (
            capability.confirmation_policy == "always"
        )
        definition.reversible = capability.reversible
        definition.operation_kind = "write" if definition.side_effect else "read"
        registry.register(definition)
    return registry


def _plan_fields_from_text(text: str) -> Dict[str, object]:
    value = str(text)
    fields: Dict[str, object] = {}
    duration = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(分钟|小时)", value)
    if duration:
        amount = float(duration.group(1))
        fields["duration_minutes"] = (
            int(amount * 60) if duration.group(2) == "小时" else int(amount)
        )
    for slot in ("上午", "下午", "晚上", "今晚"):
        if slot in value:
            fields["time_slot"] = "晚上" if slot == "今晚" else slot
            break
    return fields


def _task_confirmation_summary(resolve_task, reference: object, action: str) -> str:
    match = resolve_task(reference)
    task = match.get("task") if isinstance(match, dict) else None
    if isinstance(task, dict):
        return (
            f"{action}计划 {task.get('id')}：“{task.get('title', '')}”。"
            "删除后无法从当前计划恢复。"
        )
    return f"{action}计划 {reference}。删除后无法从当前计划恢复。"


def _schedule_changes(schedule_text: str, now) -> Dict[str, object]:
    text = str(schedule_text).strip()
    changes: Dict[str, object] = {}
    if "明天" in text:
        changes["date"] = (now.date() + timedelta(days=1)).isoformat()
    if "上午" in text:
        changes["time_slot"] = "上午"
    elif "下午" in text:
        changes["time_slot"] = "下午"
    elif "晚上" in text or "今晚" in text:
        changes["time_slot"] = "晚上"
    return changes
