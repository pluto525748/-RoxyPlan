from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from modules.contracts import ToolResult
from modules.intent_router import match_plan_task
from modules.memory_service import MemoryOperationResult, MemoryService


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
    confirmation_policy: str = "conditional"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
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
                    "confirmation_policy": tool.confirmation_policy,
                    "sequential": tool.sequential,
                    "parallel_safe": tool.parallel_safe,
                    "model_visible": tool.model_visible,
                }
            )
        return schemas

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

    def task_list(*, include_cancelled: bool = False):
        try:
            return plan_store.tasks(include_cancelled=include_cancelled)
        except TypeError:
            return plan_store.tasks()

    def resolve_task(reference: object, *, include_cancelled: bool = True):
        value = str(reference or "").strip()
        tasks = task_list(include_cancelled=include_cancelled)
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

    def add_plan(
        title: str,
        allow_duplicate: bool = False,
        date: str = "",
        time_slot: str = "",
        duration_minutes: Optional[int] = None,
    ):
        similar = (
            plan_store.similar_tasks(title)
            if hasattr(plan_store, "similar_tasks")
            else []
        )
        if similar and not allow_duplicate:
            return result(
                "add_plan",
                False,
                "similar_plan",
                {"candidates": similar[:3], "proposed_title": title},
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
            {"task": task} if exists else {},
            None if exists else "postcondition_failed",
        )

    def show_plan():
        return result("show_plan", True, "listed", {"tasks": task_list()})

    def complete_plan(match_text: str):
        if str(match_text).strip().isdigit():
            task, changed = plan_store.complete_by_id(int(str(match_text).strip()))
            if task is None:
                return result("complete_plan", False, "not_found", error="not_found")
            verified = bool(task.get("done"))
            return result(
                "complete_plan",
                verified,
                "completed" if verified else "verification_failed",
                {"task": task, "changed": changed},
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
        verified = bool(task.get("done"))
        return result(
            "complete_plan",
            verified,
            "completed" if verified else "verification_failed",
            {"task": task, "changed": changed},
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
            {"task": task} if task else {},
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
        return result(
            "update_plan",
            bool(task is not None and changed),
            reason,
            {"task": task, "changes": changes} if task else {},
            None if changed else reason,
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
        return result(
            "reschedule_plan",
            bool(updated is not None and changed),
            reason,
            {"task": updated, "changes": changes} if updated else {},
            None if changed else reason,
        )

    def reopen_plan(task_ref: str):
        match = resolve_task(task_ref)
        if match.get("status") != "matched" or not isinstance(match.get("task"), dict):
            return result("reopen_plan", False, "not_found", error="not_found")
        original = match["task"]
        task, changed = plan_store.reopen_task(
            int(original["id"]), date=str(original.get("date") or "") or None
        )
        return result(
            "reopen_plan",
            task is not None,
            "reopened" if changed else "already_open",
            {"task": task, "changed": changed} if task else {},
            None if task else "not_found",
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

    def save_daily_review():
        entry = growth_manager.save_today_review()
        return result("save_daily_review", True, "saved", {"entry": entry})

    def show_growth_log():
        entries = growth_store.entries()
        return result("show_growth_log", True, "listed", {"entries": entries[-7:]})

    def _listed_memories():
        listed = memory_service.list_memories(status="active")
        if listed.success:
            pending = memory_service.list_candidates(status="pending")
            conflicts = memory_service.list_conflicts(status="pending")
            listed.data["pending_candidate_count"] = len(
                pending.data.get("candidates", []) if pending.success else []
            )
            listed.data["unresolved_conflict_count"] = len(
                conflicts.data.get("conflicts", []) if conflicts.success else []
            )
        return listed

    def list_memories():
        return memory_result("list_memories", _listed_memories(), message="listed")

    def show_memory():
        return memory_result("show_memory", _listed_memories(), message="listed")

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
        operation = memory_service.create_candidate(
            content,
            source_text=content,
            source="explicit_request",
            explicit=True,
        )
        raw_status = str(operation.data.get("status", ""))
        return memory_result(
            "request_add_memory",
            operation,
            message=raw_status or operation.status,
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
        return result(
            "show_conversation_history",
            True,
            "listed",
            {
                "sessions": [
                    {
                        "session_id": item.get("session_id"),
                        "title": item.get("title", "新对话"),
                        "updated_at": item.get("updated_at"),
                        "message_count": item.get("message_count", 0),
                    }
                    for item in sessions[:10]
                ]
            },
        )

    def pet_call(tool: str, method: str, success_message: str):
        if pet_controller is None or not hasattr(pet_controller, method):
            return result(tool, False, "unavailable", error="pet_unavailable")
        value = getattr(pet_controller, method)()
        success = value is not False
        return result(tool, success, success_message if success else "unavailable", error=None if success else "action_failed")

    definitions = (
        ToolDefinition(
            "add_plan",
            "把用户明确要求今天要做、安排或放进清单的事项添加为今日计划；仅提供建议或询问建议时不要调用",
            "medium",
            {
                "title": {"type": "string", "required": True, "minLength": 1, "maxLength": 240},
                "allow_duplicate": {"type": "boolean", "required": False},
                "date": {"type": "string", "required": False, "maxLength": 10},
                "time_slot": {"type": "string", "required": False, "enum": ["上午", "下午", "晚上"]},
                "duration_minutes": {"type": "integer", "required": False, "minimum": 1, "maximum": 1440},
            },
            add_plan,
            confirmation_summary=lambda arguments: (
                f"把“{arguments.get('title', '')}”加入今日计划。"
            ),
        ),
        ToolDefinition("show_plan", "查看今日计划", "low", {}, show_plan),
        ToolDefinition("complete_plan", "完成匹配的今日计划", "medium", {"match_text": {"type": "string", "required": True}}, complete_plan),
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
        ),
        ToolDefinition("update_plan", "更新指定计划", "medium", {"task_ref": {"type": "string", "required": True}, "changes": {"type": "object", "required": True}}, update_plan),
        ToolDefinition("reschedule_plan", "调整计划时间", "medium", {"task_ref": {"type": "string", "required": True}, "schedule_text": {"type": "string", "required": True}}, reschedule_plan),
        ToolDefinition("reopen_plan", "重新打开计划", "medium", {"task_ref": {"type": "string", "required": True}}, reopen_plan),
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
        ),
        ToolDefinition("show_action_log", "查看今日行动记录", "low", {}, show_action_log),
        ToolDefinition("generate_daily_review", "生成今日复盘", "low", {}, generate_daily_review),
        ToolDefinition("save_daily_review", "保存今日复盘", "medium", {}, save_daily_review),
        ToolDefinition("show_growth_log", "查看成长日志", "low", {}, show_growth_log),
        ToolDefinition("list_memories", "只读取当前正式且未归档的长期记忆，不修改任何数据", "low", {}, list_memories),
        ToolDefinition("list_archived_memories", "只读取已归档长期记忆，不恢复或修改记忆", "low", {}, list_archived_memories),
        ToolDefinition("search_memories", "按内容搜索正式长期记忆", "low", {"query": {"type": "string", "required": True, "maxLength": 500}}, search_memories),
        ToolDefinition("show_memory", "查看长期记忆（兼容旧命令）", "low", {}, show_memory),
        ToolDefinition("search_memory", "搜索长期记忆（兼容旧命令）", "low", {"query": {"type": "string", "required": True, "maxLength": 500}}, search_memory),
        ToolDefinition("create_memory_candidate", "创建待审核候选，不直接写入正式长期记忆；仅用于用户明确要求记住的信息", "medium", {"content": {"type": "string", "required": True, "maxLength": 1000}, "source_text": {"type": "string", "required": False, "maxLength": 1200}}, create_memory_candidate),
        ToolDefinition("request_add_memory", "用户明确要求记住某项信息时创建待审核记忆；包括敏感信息，绝不直接写入正式长期记忆", "medium", {"content": {"type": "string", "required": True}}, request_add_memory),
        ToolDefinition("queue_memory_candidate", "将稳定表达加入待审核记忆", "medium", {"content": {"type": "string", "required": True}, "source_text": {"type": "string", "required": False}}, queue_memory_candidate),
        ToolDefinition("list_memory_candidates", "只读取当前待审核候选及真实编号，不接受或修改候选", "low", {}, list_memory_candidates),
        ToolDefinition("show_memory_candidates", "查看待审核记忆候选", "low", {}, show_memory_candidates),
        ToolDefinition("accept_memory_candidate", "将一条指定候选正式接受到长期记忆；候选ID必须来自用户或程序上下文", "medium", {"candidate_id": {"type": "integer", "required": True, "minimum": 1}}, accept_memory_candidate),
        ToolDefinition("accept_memory_candidates", "将指定候选正式接受到长期记忆；仅使用程序提供或用户明确给出的真实候选ID", "medium", {"candidate_ids": {"type": "array", "required": True, "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}}}, accept_memory_candidates),
        ToolDefinition("reject_memory_candidate", "忽略一条指定候选，不写入长期记忆；候选ID必须真实存在", "medium", {"candidate_id": {"type": "integer", "required": True, "minimum": 1}}, reject_memory_candidate),
        ToolDefinition("reject_memory_candidates", "忽略指定的多条候选，不写入长期记忆；仅使用程序提供的真实候选ID", "medium", {"candidate_ids": {"type": "array", "required": True, "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}}}, reject_memory_candidates),
        ToolDefinition("accept_all_memory_candidates", "接受当前真实存在的全部待审核候选；工具会先由程序读取候选，模型不得自行生成ID", "medium", {}, accept_all_memory_candidates),
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
        ToolDefinition("delete_memory", "删除长期记忆", "high", {"memory_id": {"type": "integer", "required": True, "minimum": 1}}, delete_memory, True, confirmation_summary=lambda arguments: f"删除长期记忆 {arguments.get('memory_id')}。此操作不可逆。"),
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
            lambda: pet_call("play_dance", "start_dance", "started"),
        ),
        ToolDefinition("sleep_pet", "让桌宠进入睡眠", "medium", {}, lambda: pet_call("sleep_pet", "agent_sleep", "sleeping")),
        ToolDefinition("wake_pet", "唤醒桌宠", "low", {}, lambda: pet_call("wake_pet", "wake", "awake")),
    )
    model_visible_tools = {
        "add_plan",
        "show_plan",
        "complete_plan",
        "delete_plan",
        "update_plan",
        "reschedule_plan",
        "reopen_plan",
        "cancel_plan",
        "add_action_log",
        "show_action_log",
        "generate_daily_review",
        "save_daily_review",
        "show_growth_log",
        "list_memories",
        "list_archived_memories",
        "search_memories",
        "create_memory_candidate",
        "list_memory_candidates",
        "accept_memory_candidate",
        "accept_memory_candidates",
        "reject_memory_candidate",
        "reject_memory_candidates",
        "accept_all_memory_candidates",
        "update_memory",
        "list_memory_conflicts",
        "resolve_memory_conflict",
        "archive_memory",
        "restore_memory",
        "delete_memory",
        "show_conversation_history",
        "pause_reminders",
        "resume_reminders",
        "play_dance",
        "sleep_pet",
        "wake_pet",
    }
    for definition in definitions:
        definition.model_visible = definition.name in model_visible_tools
        definition.side_effect = definition.name not in {
            "show_plan",
            "show_action_log",
            "generate_daily_review",
            "show_growth_log",
            "list_memories",
            "list_archived_memories",
            "search_memories",
            "show_memory",
            "search_memory",
            "list_memory_candidates",
            "show_memory_candidates",
            "list_memory_conflicts",
            "show_memory_conflicts",
            "show_conversation_history",
        }
        definition.sequential = definition.side_effect
        definition.parallel_safe = not definition.side_effect
        definition.confirmation_policy = (
            "always"
            if definition.requires_confirmation or definition.risk_level == "high"
            else "conditional"
            if definition.side_effect
            else "never"
        )
        if definition.name in {
            "create_memory_candidate",
            "request_add_memory",
            "queue_memory_candidate",
            "accept_memory_candidate",
            "accept_memory_candidates",
            "accept_all_memory_candidates",
            "reject_memory_candidate",
            "reject_memory_candidates",
        }:
            definition.requires_confirmation = False
            definition.confirmation_policy = "never"
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
