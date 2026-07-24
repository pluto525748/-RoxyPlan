from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from modules.contracts import AgentStep
from modules.tool_registry import ToolRegistry


@dataclass
class AgentPlan:
    goal: str
    steps: List[AgentStep]
    source: str = "rule"


INTENT_TOOL_MAP = {
    "add_plan": "add_plan",
    "show_plan": "show_plan",
    "complete_plan": "complete_plan",
    "delete_plan": "delete_plan",
    "update_plan": "update_plan",
    "reschedule_plan": "reschedule_plan",
    "reopen_plan": "reopen_plan",
    "cancel_plan": "cancel_plan",
    "add_action_log": "add_action_log",
    "show_action_log": "show_action_log",
    "daily_review": "generate_daily_review",
    "save_review": "save_daily_review",
    "show_growth_log": "show_growth_log",
    "show_memory": "list_memories",
    "search_memory": "search_memories",
    "add_memory_request": "create_memory_candidate",
    "memory_candidate": "queue_memory_candidate",
    "archive_memory": "archive_memory",
    "restore_memory": "restore_memory",
    "delete_memory": "delete_memory",
    "show_memory_candidates": "list_memory_candidates",
    "accept_memory_candidate": "accept_memory_candidate",
    "accept_memory_candidates": "accept_memory_candidates",
    "reject_memory_candidate": "reject_memory_candidate",
    "reject_memory_candidates": "reject_memory_candidates",
    "accept_all_memory_candidates": "accept_all_memory_candidates",
    "show_memory_conflicts": "list_memory_conflicts",
    "show_archived_memories": "list_archived_memories",
    "resolve_memory_conflict": "resolve_memory_conflict",
    "show_memory_audit": "show_memory_audit",
    "show_recent_conversation": "show_recent_conversation",
    "show_conversation_history": "show_conversation_history",
}


class LLMPlanner:
    def __init__(self, chat_callable: Optional[Callable] = None) -> None:
        self.chat_callable = chat_callable

    def plan(self, text: str, allowed_tools: List[str], max_steps: int) -> Optional[AgentPlan]:
        if self.chat_callable is None:
            return None
        prompt = (
            "Return JSON only: {goal:string,steps:[{tool:string,arguments:object}]}. "
            f"Use at most {max_steps} steps and only these tools: {', '.join(allowed_tools)}. "
            "Do not answer the user and do not invent parameters. User: " + text
        )
        try:
            raw = str(self.chat_callable([{"role": "system", "content": prompt}])).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
            data = json.loads(raw)
            steps = data.get("steps") if isinstance(data, dict) else None
            if not isinstance(steps, list) or not steps:
                return None
            parsed = []
            for item in steps[:max_steps]:
                if not isinstance(item, dict) or item.get("tool") not in allowed_tools:
                    return None
                arguments = item.get("arguments", {})
                if not isinstance(arguments, dict):
                    return None
                parsed.append(AgentStep(str(item["tool"]), arguments))
            return AgentPlan(str(data.get("goal", "完成用户请求")), parsed, "llm")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None


class AgentPlanner:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_steps: int = 3,
        enable_multi_step: bool = True,
        llm_planner: Optional[LLMPlanner] = None,
        enable_llm: bool = False,
    ) -> None:
        self.registry = registry
        self.max_steps = max(1, min(int(max_steps), 3))
        self.enable_multi_step = bool(enable_multi_step)
        self.llm_planner = llm_planner
        self.enable_llm = bool(enable_llm)

    def configure(self, *, max_steps: int, enable_multi_step: bool, enable_llm: bool) -> None:
        self.max_steps = max(1, min(int(max_steps), 3))
        self.enable_multi_step = bool(enable_multi_step)
        self.enable_llm = bool(enable_llm)

    def plan(self, user_text: str, intent_result: Dict[str, object]) -> AgentPlan:
        rule = self._rule_plan(user_text, intent_result)
        if rule.steps:
            print("[Planner] source=rule", flush=True)
            return self._bounded(rule)
        if self.enable_llm and self.llm_planner is not None:
            llm = self.llm_planner.plan(user_text, self.registry.names(enabled_only=True), self.max_steps)
            if llm is not None:
                print("[Planner] source=llm", flush=True)
                return self._bounded(llm)
            print("[Planner] fallback=rule", flush=True)
        return rule

    def _rule_plan(self, text: str, result: Dict[str, object]) -> AgentPlan:
        entities = result.get("entities", {})
        entities = dict(entities) if isinstance(entities, dict) else {}
        intent = str(result.get("intent", "chat"))

        resolved_actions = result.get("resolved_actions", [])
        if isinstance(resolved_actions, list) and resolved_actions:
            steps = []
            for item in resolved_actions[: self.max_steps]:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool_name", "")).strip()
                arguments = item.get("arguments", {})
                if not tool_name or not isinstance(arguments, dict):
                    continue
                dependencies = item.get("depends_on", [])
                steps.append(
                    AgentStep(
                        tool_name,
                        dict(arguments),
                        depends_on_previous=bool(dependencies),
                    )
                )
            if steps:
                return AgentPlan("执行已解析的业务动作", steps, "business_resolver")

        if self.enable_multi_step:
            multi = self._multi_step(text, entities)
            if multi:
                return AgentPlan("完成多个相关操作", multi)
            if intent == "add_plan" and isinstance(entities.get("tasks"), list):
                tasks = [str(item).strip() for item in entities["tasks"] if str(item).strip()]
                if len(tasks) > 1:
                    return AgentPlan(
                        "添加多个今日计划",
                        [AgentStep("add_plan", {"title": title}) for title in tasks],
                    )
        tool = INTENT_TOOL_MAP.get(intent)
        if intent == "delete_memory" and entities.get("scope") == "all":
            tool = "delete_all_memories"
        if (
            intent == "memory_candidate"
            and str(entities.get("category", "")) in {"health_lifestyle", "relationship"}
        ):
            tool = None
        if tool is None:
            if intent == "reminder_control":
                tool = "pause_reminders" if entities.get("action") == "pause" else "resume_reminders"
            elif intent == "dance":
                tool = "play_dance"
            elif intent == "sleep_pet":
                tool = "sleep_pet"
            elif intent == "wake_pet":
                tool = "wake_pet"
        if tool is None:
            return AgentPlan("普通聊天", [])
        arguments = self._arguments_for(tool, entities)
        return AgentPlan(intent, [AgentStep(tool, arguments)])

    def _multi_step(self, text: str, entities: Dict[str, object]) -> List[AgentStep]:
        if entities.get("completed_task") and entities.get("action_log"):
            return [
                AgentStep("complete_plan", {"match_text": str(entities["completed_task"])}),
                AgentStep("add_action_log", {"content": str(entities["action_log"])}),
            ]
        if re.search(r"复盘.+(?:并|然后|顺便).*(?:保存|存下来)", text):
            return [AgentStep("generate_daily_review"), AgentStep("save_daily_review")]
        return []

    @staticmethod
    def _arguments_for(tool: str, entities: Dict[str, object]) -> Dict[str, object]:
        if tool == "add_plan":
            tasks = entities.get("tasks", [])
            title = tasks[0] if isinstance(tasks, list) and tasks else entities.get("title", "")
            arguments = {
                "title": title,
                **(
                    {"allow_duplicate": True}
                    if entities.get("allow_duplicate") is True
                    else {}
                ),
            }
            # GrowthManager owns the authoritative meaning of "today". Passing an
            # ISO date produced by a separately-clocked parser can move a task to
            # the wrong day in tests or after a midnight boundary.
            for key in ("time_slot", "duration_minutes"):
                if entities.get(key) not in (None, ""):
                    arguments[key] = entities[key]
            return arguments
        mapping = {
            "complete_plan": ("match_text", "query"),
            "delete_plan": ("task_ref", "task_id" if entities.get("task_id") is not None else "query"),
            "reopen_plan": ("task_ref", "task_id" if entities.get("task_id") is not None else "query"),
            "cancel_plan": ("task_ref", "task_id" if entities.get("task_id") is not None else "query"),
            "add_action_log": ("content", "content"),
            "search_memories": ("query", "query"),
            "search_memory": ("query", "query"),
            "create_memory_candidate": ("content", "content"),
            "request_add_memory": ("content", "content"),
            "archive_memory": ("memory_id", "memory_id"),
            "restore_memory": ("memory_id", "memory_id"),
            "delete_memory": ("memory_id", "memory_id"),
            "accept_memory_candidate": ("candidate_id", "candidate_id"),
            "reject_memory_candidate": ("candidate_id", "candidate_id"),
        }
        if tool in {"accept_memory_candidates", "reject_memory_candidates"}:
            values = entities.get("candidate_ids", [])
            return {
                "candidate_ids": list(values) if isinstance(values, list) else []
            }
        if tool == "queue_memory_candidate":
            return {
                "content": entities.get("content", ""),
                "source_text": entities.get("source_text", ""),
            }
        if tool == "resolve_memory_conflict":
            return {
                "conflict_id": entities.get("conflict_id"),
                "resolution": entities.get("resolution"),
                "merged_content": entities.get("merged_content", ""),
            }
        if tool == "update_plan":
            reference = entities.get("task_id") if entities.get("task_id") is not None else entities.get("query")
            return {"task_ref": str(reference or ""), "changes": dict(entities.get("changes", {}))}
        if tool == "reschedule_plan":
            reference = entities.get("task_id") if entities.get("task_id") is not None else entities.get("query")
            return {"task_ref": str(reference or ""), "schedule_text": str(entities.get("schedule_text", ""))}
        if tool == "delete_all_memories":
            return {"scope": "all"}
        if tool == "show_recent_conversation":
            return {
                "conversation_id": str(entities.get("conversation_id", "")),
                "current_message": str(entities.get("current_message", "")),
            }
        if tool == "show_conversation_history":
            return {
                "current_conversation_id": str(
                    entities.get("current_conversation_id", "")
                ),
                "exclude_today": bool(entities.get("exclude_today", False)),
            }
        if tool in mapping:
            target, source = mapping[tool]
            value = entities.get(source)
            if target == "task_ref" and value is not None:
                value = str(value)
            return {target: value}
        return {}

    def _bounded(self, plan: AgentPlan) -> AgentPlan:
        allowed = set(self.registry.names(enabled_only=True))
        steps = [step for step in plan.steps if step.tool in allowed][: self.max_steps]
        return AgentPlan(plan.goal, steps, plan.source)
