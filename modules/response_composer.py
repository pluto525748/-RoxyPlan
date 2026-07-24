from __future__ import annotations

from typing import Iterable, List, Optional

from modules.action_claim_guard import ActionClaimGuard
from modules.contracts import AgentResponse, ToolResult
from modules.llm.response_sanitizer import sanitize_public_reply


class ResponseComposer:
    """Compose a public response from verified state and ToolResult facts."""

    def __init__(
        self,
        *,
        claim_guard: Optional[ActionClaimGuard] = None,
        enabled: bool = True,
    ) -> None:
        self.claim_guard = claim_guard or ActionClaimGuard()
        self.enabled = bool(enabled)

    def compose(
        self,
        response: AgentResponse,
        *,
        user_text: str = "",
    ) -> AgentResponse:
        results = list(response.tool_results)
        raw_message = str(response.message or "").strip()
        message = sanitize_public_reply(raw_message) if raw_message else ""
        if self.enabled:
            message = self._deterministic_message(
                response.status,
                message,
                results,
            )
        response.message = self.claim_guard.validate(
            message,
            results,
            user_text=user_text,
        )
        if not response.message:
            response.message = "这次没有得到可显示的结果，请稍后再试。"
        return response

    def from_resolution(
        self,
        status: str,
        safe_prompt: str,
        *,
        conversation_id: str = "",
        request_id: str = "",
    ) -> AgentResponse:
        mapped = "clarification" if status in {
            "missing",
            "ambiguous",
            "confirmation_required",
        } else "failed"
        message = sanitize_public_reply(safe_prompt)
        return AgentResponse(
            mapped,
            message or "我还不能安全确定要执行的内容，请再说明一下。",
            request_id=request_id or None,
            conversation_id=conversation_id,
        )

    def compose_batch(
        self,
        results: Iterable[ToolResult],
        *,
        skipped_count: int = 0,
        base_message: str = "",
    ) -> str:
        items = list(results)
        succeeded = [item for item in items if item.success]
        failed = [item for item in items if not item.success]
        if not failed and not skipped_count:
            return sanitize_public_reply(base_message) or self._join_successes(succeeded)
        parts: List[str] = []
        for result in succeeded:
            parts.append(self._result_line(result, True))
        for result in failed:
            parts.append(self._result_line(result, False))
        if skipped_count:
            parts.append(f"另有 {skipped_count} 项因依赖未完成而跳过。")
        return "\n".join(item for item in parts if item) or "本轮操作没有完成。"

    def _deterministic_message(
        self,
        status: str,
        message: str,
        results: List[ToolResult],
    ) -> str:
        if status == "partial_success":
            return self.compose_batch(results, base_message=message)
        if results and not message:
            if all(item.success for item in results):
                return self._join_successes(results)
            return self.compose_batch(results)
        if not message:
            if status == "confirmation_required":
                return "这项操作需要确认后才会执行。"
            if status == "clarification":
                return "我还需要一点信息才能继续。"
            if status == "failed":
                return "这次操作没有完成，现有数据没有改变。"
            return "这次没有得到可显示的回复，请稍后再试。"
        return message

    def _join_successes(self, results: List[ToolResult]) -> str:
        lines = [self._result_line(item, True) for item in results]
        return "\n".join(item for item in lines if item) or "操作已经完成。"

    @staticmethod
    def _result_line(result: ToolResult, success: bool) -> str:
        display = sanitize_public_reply(result.display_message or result.message)
        operation = result.data.get("memory_operation", {})
        operation = operation if isinstance(operation, dict) else {}
        candidate_id = operation.get("candidate_id")
        if success and candidate_id and result.tool == "accept_memory_candidate":
            return f"候选 {candidate_id} 已加入长期记忆。"
        if success and candidate_id and result.tool == "reject_memory_candidate":
            return f"候选 {candidate_id} 已忽略。"
        technical_markers = {
            "completed",
            "listed",
            "added",
            "updated",
            "deleted",
            "rejected",
            "failed",
            "error",
        }
        if display in technical_markers:
            display = ""
        if display:
            return display
        name = result.tool
        if success:
            return {
                "add_plan": "计划已加入今天的清单。",
                "update_plan": "计划已经更新。",
                "reschedule_plan": "计划时间已经调整。",
                "complete_plan": "对应计划已标记完成。",
                "add_action_log": "这段进展已记入今天的行动记录。",
                "create_memory_candidate": "这条信息已加入待审核记忆。",
                "accept_memory_candidate": "候选已加入长期记忆。",
                "reject_memory_candidate": "候选已忽略。",
            }.get(name, f"{name} 已完成。")
        return display or f"{name} 没有完成，现有数据没有改变。"
