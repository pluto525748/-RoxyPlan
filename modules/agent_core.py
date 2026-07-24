from __future__ import annotations

import re
from typing import Dict, List, Optional

from modules.action_batch import ActionBatch
from modules.agent_planner import AgentPlanner
from modules.contracts import AgentResponse, ToolResult
from modules.tool_execution_plan import ToolExecutionPlan
from modules.tool_executor import ToolExecutor


class AgentCore:
    """Bounded coordinator for existing internal tools only."""

    def __init__(
        self,
        planner: AgentPlanner,
        executor: ToolExecutor,
        *,
        enabled: bool = True,
        action_batch_enabled: bool = True,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.enabled = bool(enabled)
        self.action_batch_enabled = bool(action_batch_enabled)
        self.execution_plan = ToolExecutionPlan(
            executor,
            result_validator=self._enforce_postcondition_shape,
        )

    def process(
        self,
        user_text: str,
        intent_result: Dict[str, object],
        *,
        confirmation_scope: Optional[str] = None,
    ) -> AgentResponse:
        print("[Agent] request received", flush=True)
        request_id = str(intent_result.get("request_id", "")).strip() or None
        if not self.enabled or str(intent_result.get("intent", "chat")) == "chat":
            return AgentResponse("chat", "", request_id=request_id)

        clarification = str(intent_result.get("clarification_question", "") or "").strip()
        if clarification:
            print("[Agent] clarification required", flush=True)
            return AgentResponse(
                "clarification",
                clarification,
                request_id=request_id,
            )

        confidence = float(intent_result.get("confidence", 0.0))
        if (
            str(intent_result.get("source", "")) == "llm"
            and confidence < 0.65
        ):
            print("[Agent] clarification required", flush=True)
            return AgentResponse(
                "clarification",
                "我不太确定你想让我执行什么。可以再明确一点，或直接说要查看、添加、修改哪项内容。",
                request_id=request_id,
            )

        plan = self.planner.plan(user_text, intent_result)
        if not plan.steps:
            return AgentResponse("chat", "", request_id=request_id)
        print(f"[Agent] plan steps={len(plan.steps)}", flush=True)

        negated = self._is_negated(user_text)
        informational = self._is_informational(user_text)
        require_confirmation = bool(intent_result.get("needs_confirmation", False))
        results: List[ToolResult] = []
        skipped_steps = 0
        steps = [
            {"tool": step.tool, "arguments": dict(step.arguments)} for step in plan.steps
        ]

        if self.action_batch_enabled and len(plan.steps) > 1:
            return self._process_action_batch(
                plan.steps,
                steps,
                confidence=confidence,
                confirmation_scope=confirmation_scope or "default",
                require_confirmation=require_confirmation,
                negated=negated,
                informational=informational,
                request_id=request_id,
            )

        for index, step in enumerate(plan.steps):
            if index and step.depends_on_previous and not results[-1].success:
                skipped_steps += 1
                continue
            result = self.executor.execute(
                step.tool,
                step.arguments,
                confidence=confidence,
                negated=negated,
                informational=informational,
                require_confirmation=require_confirmation,
                confirmation_scope=confirmation_scope,
            )
            result = self._enforce_postcondition_shape(result)
            results.append(result)
            if result.error == "confirmation_required":
                confirmation = result.data.get("confirmation")
                print("[Agent] confirmation required", flush=True)
                return AgentResponse(
                    "confirmation_required",
                    self._confirmation_message(result),
                    steps,
                    [item.to_dict() for item in results],
                    confirmation if isinstance(confirmation, dict) else None,
                    request_id=request_id,
                )
            if result.error == "clarification_required":
                print("[Agent] clarification required", flush=True)
                return AgentResponse(
                    "clarification",
                    self._missing_field_question(str(intent_result.get("intent", ""))),
                    steps,
                    [item.to_dict() for item in results],
                    request_id=request_id,
                )
            if not result.success:
                has_independent_followup = any(
                    not item.depends_on_previous
                    for item in plan.steps[index + 1 :]
                )
                if not has_independent_followup:
                    status = "clarification" if result.error in {"ambiguous", "not_found", "completed_requires_reopen", "cancelled_requires_reopen"} else "failed"
                    message = self._failure_message(result)
                    return AgentResponse(
                        status,
                        message,
                        steps,
                        [item.to_dict() for item in results],
                        request_id=request_id,
                    )

        failed_results = [item for item in results if not item.success]
        if failed_results:
            succeeded = [item for item in results if item.success]
            if succeeded:
                print("[Agent] partial_success", flush=True)
                return AgentResponse(
                    "partial_success",
                    self._partial_message(results, skipped_steps),
                    steps,
                    [item.to_dict() for item in results],
                    request_id=request_id,
                )
            failure = failed_results[0]
            return AgentResponse(
                "failed",
                self._failure_message(failure),
                steps,
                [item.to_dict() for item in results],
                request_id=request_id,
            )
        print("[Agent] completed", flush=True)
        return AgentResponse(
            "completed",
            self._success_message(results),
            steps,
            [item.to_dict() for item in results],
            request_id=request_id,
        )

    def _process_action_batch(
        self,
        plan_steps,
        public_steps: List[Dict[str, object]],
        *,
        confidence: float,
        confirmation_scope: str,
        require_confirmation: bool,
        negated: bool,
        informational: bool,
        request_id: Optional[str],
    ) -> AgentResponse:
        batch = ActionBatch.from_steps(
            confirmation_scope,
            plan_steps,
            execution_policy="best_effort",
            max_actions=3,
        )
        outcome = self.execution_plan.execute(
            batch,
            confidence=confidence,
            confirmation_scope=confirmation_scope,
            require_confirmation=require_confirmation,
            negated=negated,
            informational=informational,
        )
        results = outcome.results
        serialized = [item.to_dict() for item in results]
        confirmation_result = next(
            (item for item in results if item.error_code == "confirmation_required"),
            None,
        )
        if confirmation_result is not None:
            confirmation = confirmation_result.data.get("confirmation")
            return AgentResponse(
                "confirmation_required",
                self._confirmation_message(confirmation_result),
                public_steps,
                serialized,
                confirmation if isinstance(confirmation, dict) else None,
                request_id=request_id,
            )
        clarification_result = next(
            (item for item in results if item.error_code == "clarification_required"),
            None,
        )
        if clarification_result is not None and not any(item.success for item in results):
            return AgentResponse(
                "clarification",
                self._missing_field_question(clarification_result.tool),
                public_steps,
                serialized,
                request_id=request_id,
            )
        succeeded = [item for item in results if item.success]
        failed = [item for item in results if not item.success]
        if succeeded and (failed or outcome.skipped_action_ids):
            print("[Agent] partial_success", flush=True)
            return AgentResponse(
                "partial_success",
                self._partial_message(results, len(outcome.skipped_action_ids)),
                public_steps,
                serialized,
                request_id=request_id,
            )
        if failed:
            status = (
                "clarification"
                if failed[0].error_code
                in {
                    "ambiguous",
                    "not_found",
                    "completed_requires_reopen",
                    "cancelled_requires_reopen",
                }
                else "failed"
            )
            return AgentResponse(
                status,
                self._failure_message(failed[0]),
                public_steps,
                serialized,
                request_id=request_id,
            )
        print("[Agent] completed", flush=True)
        return AgentResponse(
            "completed",
            self._success_message(results),
            public_steps,
            serialized,
            request_id=request_id,
        )

    def handle_confirmation(
        self,
        user_text: str,
        *,
        confirmation_scope: Optional[str] = None,
    ) -> Optional[AgentResponse]:
        text = str(user_text).strip().strip("。.!！?？")
        pending = self.executor.confirmation_manager.pending(
            scope=confirmation_scope
        )
        confirmation_terms = {
            "确认", "确认刚才的操作", "继续执行", "是的", "执行", "添加", "保存", "删除"
        }
        if text in {"取消", "不要执行", "取消刚才的操作", "不用了"}:
            cancelled = self.executor.confirmation_manager.cancel(
                scope=confirmation_scope
            )
            return AgentResponse(
                "completed",
                "好，刚才的操作已经取消。" if cancelled else "现在没有等待确认的操作。",
            )
        if pending is None and text in confirmation_terms:
            return AgentResponse(
                "failed",
                "现在没有等待确认的操作。它可能已经过期，或服务重启后已失效，请重新发起。",
            )
        if pending is None:
            return None
        specific = re.fullmatch(r"确认删除计划\s*(\d+)", text)
        memory_specific = re.fullmatch(r"确认删除记忆\s*(\d+)", text)
        if specific or memory_specific:
            expected_tool = "delete_plan" if specific else "delete_memory"
            expected_key = "task_ref" if specific else "memory_id"
            expected_value = (specific or memory_specific).group(1)
            if (
                pending is None
                or pending.get("tool") != expected_tool
                or str(pending.get("arguments", {}).get(expected_key)) != expected_value
            ):
                return AgentResponse("failed", "这条确认与当前等待的操作不一致，请重新发起操作。")
        elif text not in confirmation_terms:
            # A normal message interrupts the pending operation. A later generic
            # confirmation must not execute stale arguments.
            self.executor.confirmation_manager.cancel(scope=confirmation_scope)
            return None
        result = self.executor.execute_confirmed(
            confirmation_scope=confirmation_scope
        )
        if not result.success:
            return AgentResponse("failed", self._failure_message(result), tool_results=[result.to_dict()])
        print("[Agent] completed", flush=True)
        return AgentResponse("completed", self._success_message([result]), tool_results=[result.to_dict()])

    def execute_model_tool_calls(
        self,
        user_text: str,
        tool_calls: List[Dict[str, object]],
        *,
        confidence: float,
        confirmation_scope: Optional[str] = None,
        require_confirmation_for_writes: bool = False,
    ) -> AgentResponse:
        """Execute model proposals through the same deterministic safety boundary."""
        results: List[ToolResult] = []
        steps: List[Dict[str, object]] = []
        negated = self._is_negated(user_text)
        informational = self._is_informational(user_text)
        write_calls = [
            item
            for item in tool_calls
            if self._is_write_tool(str(item.get("name", "")))
        ]
        confirmable_write_calls = [
            item
            for item in write_calls
            if (
                self.executor.registry.get(str(item.get("name", ""))) is not None
                and self.executor.registry.get(
                    str(item.get("name", ""))
                ).confirmation_policy
                != "never"
            )
        ]
        contains_high_risk = any(
            (
                self.executor.registry.get(str(item.get("name", ""))) is not None
                and self.executor.registry.get(str(item.get("name", ""))).risk_level
                == "high"
            )
            for item in write_calls
        )
        if len(confirmable_write_calls) > 1 and (
            require_confirmation_for_writes or contains_high_risk
        ):
            return AgentResponse(
                "clarification",
                "这句话里包含多个需要确认的写操作。请先告诉我最想执行哪一个，我会逐项确认。",
            )
        for raw_call in tool_calls:
            name = str(raw_call.get("name", "")).strip()
            arguments = raw_call.get("arguments", {})
            args = dict(arguments) if isinstance(arguments, dict) else {}
            tool_call_id = str(raw_call.get("tool_call_id", "")).strip()
            steps.append({"tool": name, "arguments": args})
            result = self.executor.execute(
                name,
                args,
                confidence=confidence,
                negated=negated,
                informational=informational,
                require_confirmation=(
                    require_confirmation_for_writes
                    and self._is_write_tool(name)
                    and self.executor.registry.get(name) is not None
                    and self.executor.registry.get(name).confirmation_policy
                    != "never"
                ),
                confirmation_scope=confirmation_scope,
                tool_call_id=tool_call_id,
            )
            result = self._enforce_postcondition_shape(result)
            results.append(result)
            if result.error == "confirmation_required":
                confirmation = result.data.get("confirmation")
                return AgentResponse(
                    "confirmation_required",
                    self._confirmation_message(result),
                    steps,
                    results,
                    confirmation if isinstance(confirmation, dict) else None,
                )
            if result.error == "clarification_required":
                return AgentResponse(
                    "clarification",
                    self._missing_field_question(name),
                    steps,
                    results,
                )
            if not result.success:
                status = "clarification" if result.error in {
                    "ambiguous",
                    "not_found",
                    "missing_parameter",
                    "invalid_parameter_type",
                    "unexpected_parameter",
                    "invalid_parameter_enum",
                    "parameter_out_of_range",
                } else "failed"
                failure_message = self._failure_message(result)
                if any(item.success for item in results[:-1]):
                    failure_message = (
                        f"前面 {sum(item.success for item in results[:-1])} 项已执行；"
                        f"后续操作停止。{failure_message}"
                    )
                return AgentResponse(
                    status,
                    failure_message,
                    steps,
                    results,
                )
        if not results:
            return AgentResponse("failed", "模型没有提供可执行的工具调用。")
        return AgentResponse(
            "completed",
            self._success_message(results),
            steps,
            results,
        )

    @classmethod
    def success_message(cls, results: List[ToolResult]) -> str:
        return cls._success_message(results) if results else "操作已经完成。"

    @classmethod
    def _partial_message(
        cls,
        results: List[ToolResult],
        skipped_steps: int = 0,
    ) -> str:
        succeeded = [item for item in results if item.success]
        failed = [item for item in results if not item.success]
        parts = []
        if succeeded:
            parts.append(cls._success_message(succeeded))
        for result in failed:
            parts.append(cls._failure_message(result))
        if skipped_steps:
            parts.append(f"另有 {skipped_steps} 项因依赖未完成而跳过。")
        return "\n".join(item for item in parts if item) or "本轮操作只完成了一部分。"

    @staticmethod
    def _is_write_tool(name: str) -> bool:
        return str(name) not in {
            "show_plan",
            "show_action_log",
            "generate_daily_review",
            "show_growth_log",
            "show_memory",
            "list_memories",
            "search_memory",
            "search_memories",
            "show_memory_candidates",
            "list_memory_candidates",
            "show_memory_conflicts",
            "list_memory_conflicts",
            "show_archived_memories",
            "list_archived_memories",
            "show_conversation_history",
        }

    @staticmethod
    def _is_negated(text: str) -> bool:
        return bool(re.search(r"(?:不要|别|取消|不用).{0,8}(?:删除|归档|恢复|添加|完成|记录|保存|执行)", text))

    @staticmethod
    def _is_informational(text: str) -> bool:
        return bool(re.search(r"(?:怎么|如何|怎样|能不能|可以吗).{0,8}(?:删除|归档|恢复|添加|完成|记录|保存)", text))

    @staticmethod
    def _confirmation_message(result: ToolResult) -> str:
        confirmation = result.data.get("confirmation", {})
        summary = str(confirmation.get("summary", "")).strip() if isinstance(confirmation, dict) else ""
        if not summary:
            summary = result.display_message or "执行刚才的操作"
        return f"这项操作需要确认：{summary}\n请回复“确认”或“取消”。"

    @staticmethod
    def _failure_message(result: ToolResult) -> str:
        if result.error == "ambiguous":
            candidates = result.data.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                if result.tool == "add_plan":
                    proposed = str(result.data.get("proposed_title", "")).strip()
                    existing = candidates[0] if isinstance(candidates[0], dict) else {}
                    return (
                        f"今天已有相近计划：{existing.get('id')}. "
                        f"{existing.get('title', '')}\n"
                        "你可以更新原计划、回复“仍然添加："
                        f"{proposed}”保留两条，或说“取消”。"
                    )
                lines = ["我找到几个可能的目标，请告诉我是第几条："]
                lines.extend(
                    f"{item.get('id')}. {item.get('title', item.get('content', ''))}"
                    for item in candidates[:5]
                    if isinstance(item, dict)
                )
                return "\n".join(lines)
            return "我找到了多个可能的目标，还不能确定你指的是哪一个。请说得更具体一点。"
        if result.error == "not_found":
            if result.tool in {
                "accept_memory_candidate",
                "reject_memory_candidate",
                "accept_memory_candidates",
                "reject_memory_candidates",
            }:
                ids = result.data.get("failed_ids", [])
                operation = result.data.get("memory_operation", {})
                if not ids and isinstance(operation, dict):
                    candidate_id = operation.get("candidate_id")
                    ids = [candidate_id] if candidate_id else []
                label = "、".join(str(item) for item in ids if item)
                return (
                    f"没有找到候选{label}，现有记忆没有改变。"
                    if label
                    else "没有找到对应候选，现有记忆没有改变。"
                )
            return "我没有找到对应的内容，可以先查看当前列表再试一次。"
        if result.error == "confirmation_missing_or_expired":
            return "刚才的确认已经不存在或过期了，请重新发起操作。"
        if result.error in {"negated_request", "informational_request"}:
            return "明白，我没有执行这个操作。"
        if result.error == "completed_requires_reopen":
            return "这条计划已经完成。要修改核心内容，请先把它重新打开。"
        if result.error == "cancelled_requires_reopen":
            return "这条计划已经取消。要继续修改，请先重新打开它。"
        if result.error == "postcondition_failed":
            return "操作没有通过结果校验，我没有把它当作成功。原有数据会保留。"
        if result.display_message:
            return result.display_message
        return f"{result.tool} 没有执行成功，现有数据没有被当作成功处理。"

    @staticmethod
    def _missing_field_question(intent: str) -> str:
        questions = {
            "add_plan": "你想加入什么计划？请告诉我具体内容。",
            "complete_plan": "你完成的是哪一条计划？",
            "delete_plan": "你想删除哪一条计划？",
            "update_plan": "你想修改哪一条计划，以及修改什么？",
            "reschedule_plan": "你想把哪一条计划改到什么时间？",
            "add_action_log": "你想把哪件已经发生的事记到行动记录里？",
        }
        return questions.get(intent, "我还缺少执行所需的信息，可以再具体说一点吗？")

    @staticmethod
    def _enforce_postcondition_shape(result: ToolResult) -> ToolResult:
        if not result.success:
            return result
        required_data = {
            "add_plan": "task",
            "complete_plan": "task",
            "update_plan": "task",
            "reschedule_plan": "task",
            "reopen_plan": "task",
            "cancel_plan": "task",
            "add_action_log": "record",
        }
        required = required_data.get(result.tool)
        if required is None or isinstance(result.data.get(required), dict):
            return result
        return ToolResult(
            False,
            result.tool,
            "postcondition_failed",
            {},
            "postcondition_failed",
            status="failed",
            message_code="postcondition_failed",
            display_message="操作结果缺少必要数据。",
        )

    @staticmethod
    def _success_message(results: List[ToolResult]) -> str:
        if len(results) > 1:
            tools = {item.tool for item in results}
            if {"complete_plan", "add_action_log"} <= tools:
                return "计划已经标记完成，也帮你记下了这次行动。"
            if {"generate_daily_review", "save_daily_review"} <= tools:
                review_result = next(item for item in results if item.tool == "generate_daily_review")
                review = review_result.data.get("review", {})
                text = str(review.get("text", "")) if isinstance(review, dict) else ""
                return (text + "\n复盘也已经保存到成长日志了。").strip()
            return "这些步骤已经按顺序完成了。"

        result = results[0]
        data = result.data
        if result.tool == "add_plan":
            return f"好，我帮你把“{data['task']['title']}”加入今天计划了。"
        if result.tool == "show_plan":
            tasks = data.get("tasks", [])
            if not tasks:
                return "今天还没有计划。"
            lines = ["今天的计划："]
            for item in tasks:
                details = " ".join(
                    value
                    for value in (
                        str(item.get("time_slot", "")),
                        (
                            f"{item.get('duration_minutes')}分钟"
                            if item.get("duration_minutes")
                            else ""
                        ),
                    )
                    if value
                )
                lines.append(
                    f"{item['id']}. [{'已完成' if item.get('done') else '待完成'}] "
                    f"{item['title']}{'（' + details + '）' if details else ''}"
                )
            return "\n".join(lines)
        if result.tool == "complete_plan":
            task = data.get("task", {})
            return f"好，已经把“{task.get('title', '')}”标记完成啦。"
        if result.tool == "delete_plan":
            return "这条计划已经删除。"
        if result.tool == "update_plan":
            task = data.get("task", {})
            return f"计划已经更新：{task.get('title', '')}。"
        if result.tool == "reschedule_plan":
            task = data.get("task", {})
            schedule = " ".join(
                value
                for value in (
                    str(task.get("date", "")),
                    str(task.get("time_slot", "")),
                )
                if value
            )
            return f"计划已经调整到 {schedule or '新的时间'}：{task.get('title', '')}。"
        if result.tool == "reopen_plan":
            task = data.get("task", {})
            return f"已经重新打开计划：{task.get('title', '')}。"
        if result.tool == "cancel_plan":
            task = data.get("task", {})
            return f"计划已取消并保留记录：{task.get('title', '')}。"
        if result.tool == "add_action_log":
            if result.message_code == "already_recorded":
                return "这条行动已经记录过了，我没有重复添加。"
            return "好，这一步已经记到行动记录里了。"
        if result.tool == "show_action_log":
            records = data.get("records", [])
            if not records:
                return "今天还没有行动记录。"
            lines = ["今天的行动记录："]
            lines.extend(
                f"{index}. {item.get('content', '')}"
                for index, item in enumerate(records, start=1)
            )
            return "\n".join(lines)
        if result.tool == "generate_daily_review":
            return str(data.get("review", {}).get("text", "今日复盘已经生成。"))
        if result.tool == "save_daily_review":
            return "今天的复盘已经保存到成长日志了。"
        if result.tool == "show_growth_log":
            entries = data.get("entries", [])
            if not entries:
                return "成长日志还是空的。"
            lines = ["成长日志："]
            for entry in entries:
                review = entry.get("review", {}) if isinstance(entry, dict) else {}
                lines.append(
                    f"- {entry.get('date', '')}：计划 {review.get('total', 0)} 件，"
                    f"完成 {review.get('done', 0)} 件，行动 {len(review.get('actions', []))} 条"
                )
            return "\n".join(lines)
        if result.tool in {
            "list_memories",
            "search_memories",
            "show_memory",
            "search_memory",
            "list_archived_memories",
        }:
            memories = data.get("memories", [])
            if not memories:
                if result.tool == "list_archived_memories":
                    return "现在没有已归档记忆。"
                base = "我现在还没有找到符合条件的正式长期记忆。"
                pending = int(data.get("pending_candidate_count", 0) or 0)
                conflicts = int(data.get("unresolved_conflict_count", 0) or 0)
                if pending or conflicts:
                    base += f"另外有 {pending} 条待审核候选、{conflicts} 条未解决冲突。"
                return base
            lines = [
                "已归档记忆："
                if result.tool == "list_archived_memories"
                else "我现在记得："
            ]
            lines.extend(f"{item.get('id')}. [{item.get('category', 'other')}] {item.get('content', '')}" for item in memories)
            pending = int(data.get("pending_candidate_count", 0) or 0)
            conflicts = int(data.get("unresolved_conflict_count", 0) or 0)
            if pending or conflicts:
                lines.append(
                    f"另有 {pending} 条待审核候选、{conflicts} 条未解决冲突，"
                    "它们还不算正式记忆。"
                )
            return "\n".join(lines)
        if result.tool in {"create_memory_candidate", "request_add_memory"}:
            status = result.message
            if status in {"duplicate", "duplicate_candidate"}:
                return "这条和已有记忆或候选很接近，我没有重复加入。"
            if status == "disabled":
                return "当前已关闭自动记忆候选，我没有保存这条信息。"
            candidate = data.get("candidate", {})
            candidate_id = candidate.get("id", "") if isinstance(candidate, dict) else ""
            reason = str(candidate.get("reason", "")) if isinstance(candidate, dict) else ""
            if "临时安排" in reason or "当前问题" in reason:
                return (
                    f"这条更像临时安排，我先按你的明确要求放进待审核记忆 {candidate_id}。"
                    "确认后才会进入长期记忆；如果只是今天要做，也可以改放到今日计划。"
                )
            return f"我先把它放进待审核记忆 {candidate_id}。确认后才会进入长期记忆。"
        if result.tool == "queue_memory_candidate":
            candidate = data.get("candidate", {})
            if result.message in {"duplicate", "duplicate_candidate"}:
                return "这条信息已经在长期记忆或待审核列表里了，我没有重复加入。"
            candidate_id = candidate.get("id", "") if isinstance(candidate, dict) else ""
            return f"这条信息可能长期有用，我先放进待审核记忆 {candidate_id}，不会直接写入长期记忆。"
        if result.tool in {"list_memory_candidates", "show_memory_candidates"}:
            candidates = data.get("candidates", [])
            if not candidates:
                return "现在没有待审核记忆。"
            lines = [f"当前有{len(candidates)}条待审核记忆："]
            lines.extend(
                f"{item.get('id')}. [{item.get('category', 'other')}] {item.get('content', '')}"
                for item in candidates
            )
            return "\n".join(lines)
        if result.tool == "accept_memory_candidate":
            if result.message == "conflict":
                return "这条候选与现有记忆存在冲突，我没有直接覆盖，请到记忆冲突中选择。"
            if result.message == "duplicate":
                return "这条候选与现有记忆重复，没有再次保存。"
            operation = data.get("memory_operation", {})
            candidate_id = operation.get("candidate_id") if isinstance(operation, dict) else None
            return (
                f"已将候选{candidate_id}加入长期记忆。"
                if candidate_id
                else "这条候选已经通过审核并进入长期记忆。"
            )
        if result.tool == "accept_memory_candidates":
            return AgentCore._batch_candidate_message(result, accepted=True)
        if result.tool == "reject_memory_candidate":
            operation = data.get("memory_operation", {})
            candidate_id = operation.get("candidate_id") if isinstance(operation, dict) else None
            return (
                f"已忽略候选{candidate_id}，不会写入长期记忆。"
                if candidate_id
                else "这条候选已经拒绝，不会进入长期记忆。"
            )
        if result.tool == "reject_memory_candidates":
            return AgentCore._batch_candidate_message(result, accepted=False)
        if result.tool == "accept_all_memory_candidates":
            return AgentCore._batch_candidate_message(result, accepted=True)
        if result.tool in {"list_memory_conflicts", "show_memory_conflicts"}:
            conflicts = data.get("conflicts", [])
            if not conflicts:
                return "现在没有待处理的记忆冲突。"
            return "\n".join(
                ["待处理记忆冲突："]
                + [f"{item.get('id')}. [{item.get('category', 'other')}] 等待选择" for item in conflicts]
            )
        if result.tool == "resolve_memory_conflict":
            return "这条记忆冲突已经按你的选择处理。"
        if result.tool == "update_memory":
            return "这条长期记忆已经更新。"
        if result.tool == "show_memory_audit":
            entries = data.get("entries", [])
            return f"本地记忆审计目前有 {len(entries)} 条记录。"
        if result.tool == "archive_memory":
            return "这条长期记忆已经归档。"
        if result.tool == "restore_memory":
            return "这条长期记忆已经恢复。"
        if result.tool == "delete_memory":
            return "这条长期记忆已经删除。"
        if result.tool == "delete_all_memories":
            return f"已删除 {int(data.get('deleted_count', 0))} 条长期记忆。"
        if result.tool == "show_recent_conversation":
            messages = data.get("messages", [])
            if not messages:
                return "当前会话还没有更早的内容。"
            lines = ["当前会话最近聊过："]
            for item in messages:
                if not isinstance(item, dict):
                    continue
                speaker = "你" if item.get("role") == "user" else "Roxy"
                lines.append(f"- {speaker}：{item.get('content', '')}")
            return "\n".join(lines)
        if result.tool == "show_conversation_history":
            sessions = data.get("sessions", [])
            if not sessions:
                return "本地还没有可恢复的历史会话。"
            lines = ["我保存了这些本地会话："]
            lines.extend(
                f"{index}. {item.get('title', '新对话')}（{item.get('message_count', 0)} 条）"
                for index, item in enumerate(sessions, start=1)
                if isinstance(item, dict)
            )
            return "\n".join(lines)
        if result.tool == "pause_reminders":
            return "好，这次运行里我先暂停主动提醒。"
        if result.tool == "resume_reminders":
            return "主动提醒已经恢复。"
        if result.tool == "play_dance":
            return "好，我跳一小段。"
        if result.tool == "sleep_pet":
            return "好，我先安静休息一会儿。"
        if result.tool == "wake_pet":
            return "我醒啦。"
        return result.message

    @staticmethod
    def _batch_candidate_message(result: ToolResult, *, accepted: bool) -> str:
        data = result.data
        completed_key = "accepted_ids" if accepted else "rejected_ids"
        completed_ids = [str(item) for item in data.get(completed_key, [])]
        already_ids = [str(item) for item in data.get("already_processed_ids", [])]
        failed_ids = [str(item) for item in data.get("failed_ids", [])]
        parts: List[str] = []
        if completed_ids:
            joined = "和".join(f"候选{item}" for item in completed_ids)
            parts.append(
                f"已将{joined}加入长期记忆"
                if accepted
                else f"已忽略{joined}"
            )
        if already_ids:
            joined = "、".join(f"候选{item}" for item in already_ids)
            parts.append(f"{joined}已被处理过，没有重复写入")
        if failed_ids:
            joined = "、".join(f"候选{item}" for item in failed_ids)
            parts.append(f"没有找到或无法处理{joined}，对应数据保持不变")
        return "；".join(parts) + "。" if parts else "当前没有需要处理的待审核候选。"
