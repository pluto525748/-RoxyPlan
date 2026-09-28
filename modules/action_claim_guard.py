from __future__ import annotations

import re
from typing import Iterable

from modules.contracts import ToolResult


ACTION_CLAIM_PATTERN = re.compile(
    r"(?:(?:已经|已).{0,20}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|进入|完成|删除|记录|更新|归档|恢复|安排|记住|记下|记进)"
    r"|(?:我|帮你|给你).{0,14}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|进入|完成|删除|记录|更新|归档|恢复|安排|记住|记下|记进).{0,8}(?:了|成功|啦|了哦)"
    r"|(?:添加|加入|设置|保存|创建|确认|忽略|进入|删除|记录|更新|归档|恢复|安排)(?:成功|完成|好了|完了))"
)

MEMORY_CLAIM_PATTERN = re.compile(
    r"(?:记忆|记住|长期保存|待审核|待确认).{0,18}"
    r"(?:添加|加入|保存|创建|确认|忽略|进入|删除|更新|归档|恢复|完成|成功)"
    r"|(?:添加|加入|保存|创建|确认|忽略|进入|删除|更新|归档|恢复).{0,18}"
    r"(?:记忆|记住|长期保存|待审核|待确认)"
)

MEMORY_ABSENCE_CLAIM_PATTERN = re.compile(
    r"(?:我)?(?:目前|现在)?(?:没有|还没有)(?:正式)?(?:记住|长期记忆|保存).{0,10}(?:内容|信息|任何)?"
    r"|(?:没有|不存在)(?:任何)?待审核(?:记忆|候选)"
    r"|(?:现在|目前)?没有待审核候选"
)

MEMORY_BATCH_CLAIM_PATTERN = re.compile(
    r"(?:已经|已)(?:把)?(?:候选)?(?:全部|都)?(?:确认|加入长期记忆|忽略)"
)

MEMORY_PERSISTENCE_ASSURANCE_PATTERN = re.compile(
    r"(?:已经|已|我会|我将|会帮你|会为你).{0,18}"
    r"(?:记住|保存|加入|放进|存入).{0,18}"
    r"(?:长期记忆|记忆里|这条内容|这段内容|上一条|刚才)"
)

DATA_ABSENCE_CLAIM_PATTERN = re.compile(
    r"(?:今天|现在|目前)?(?:没有|还没有)(?:任何|相关)?"
    r"(?:计划|任务|行动记录|成长日志)"
)

MEMORY_WRITE_TOOLS = {
    "create_memory_candidate",
    "save_formal_memory",
    "request_add_memory",
    "queue_memory_candidate",
    "accept_memory_candidate",
    "accept_memory_candidates",
    "accept_all_memory_candidates",
    "reject_memory_candidate",
    "reject_memory_candidates",
    "update_memory",
    "archive_memory",
    "restore_memory",
    "delete_memory",
    "delete_all_memories",
    "resolve_memory_conflict",
}

# A successful read can establish facts such as a plan's completed state.  It
# cannot establish that this turn added, saved, or otherwise mutated data.
EXPLICIT_MUTATION_CLAIM_PATTERN = re.compile(
    r"(?:已经|已).{0,20}(?:添加|加入|设置|保存|创建|确认|忽略|进入|删除|记录|更新|归档|恢复)"
    r"|(?:我|帮你|给你).{0,14}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|进入|完成|删除|记录|更新|归档|恢复)"
    r"|(?:添加|加入|设置|保存|创建|确认|忽略|进入|删除|记录|更新|归档|恢复)(?:成功|完成)"
    r"|(?:标记|设置).{0,12}(?:完成|已完成)"
)

# ── Additional patterns discovered during real desktop V2.1 acceptance ──────

# "到时候会提醒你" / "我会提醒你" / "到时候叫你"
REMINDER_PROMISE_PATTERN = re.compile(
    r"(?:到时候|时间到了|我会|会).{0,12}"
    r"(?:提醒|叫你|通知|提示)(?:你)?"
)

# Chat reply that impersonates a completed business flow after schema rejection
SCHEMA_REJECTED_BUSINESS_PATTERN = re.compile(
    r"(?:几点|多久|多长时间|什么时候|什么时间).{0,8}[？?]"
    r"|(?:我会|帮你|给你).{0,12}"
    r"(?:加入|添加|保存|记住|安排|提醒|记录)"
)


class ActionClaimGuard:
    """Diagnostic/test-only assertion helper for verifying ToolResult alignment.

    This class is NO LONGER part of the production response path.  The
    production final-reply authority is :class:`ResponseComposer`, which
    derives :class:`ResponseOutcome` exclusively from ToolResult facts.

    Keep this class for:
    - Test assertions that verify Guard patterns independently
    - Diagnostic tooling that audits historical reply traces
    - One-off manual investigation of suspicious model output

    Do NOT add new keyword patches here.  Do NOT reintroduce this into
    the production compose() path.
    """

    def validate(
        self,
        text: str,
        tool_results: Iterable[ToolResult],
        *,
        user_text: str = "",
        schema_rejected: bool = False,
    ) -> str:
        content = str(text).strip()
        results = list(tool_results)
        has_write_success = any(
            result.success and result.operation_kind == "write"
            for result in results
        )
        # After schema rejection with zero tool execution, any business
        # impersonation must be blocked before the per-pattern checks.
        if schema_rejected and not has_write_success:
            if SCHEMA_REJECTED_BUSINESS_PATTERN.search(content):
                print(
                    "[ActionClaimGuard] blocked business impersonation after schema rejection",
                    flush=True,
                )
                return "抱歉，这次没有成功解析你的请求。你可以用更简单的方式再说一次，我再试试。"
        if (
            MEMORY_PERSISTENCE_ASSURANCE_PATTERN.search(content)
            and self._is_explicit_memory_save_request(user_text)
        ):
            verified = any(
                result.success
                and result.operation_kind == "write"
                and result.tool == "save_formal_memory"
                and self._memory_id(result) is not None
                for result in results
            )
            if not verified:
                print(
                    "[ActionClaimGuard] blocked unverified formal memory promise",
                    flush=True,
                )
                return "这次没有成功保存长期记忆。"
        if REMINDER_PROMISE_PATTERN.search(content) and not any(
            result.success
            and result.operation_kind == "write"
            and result.tool in {"add_reminder", "create_reminder"}
            for result in results
        ):
            print(
                "[ActionClaimGuard] blocked reminder promise without reminder ToolResult",
                flush=True,
            )
            return "我现在还没有设置提醒，所以不能承诺到时候提醒你。"
        if MEMORY_ABSENCE_CLAIM_PATTERN.search(content):
            if self._verified_memory_absence(content, results):
                return content
            print("[ActionClaimGuard] blocked unverified memory absence claim", flush=True)
            return "我现在不能确认这件事，所以不想随便回答。"
        if DATA_ABSENCE_CLAIM_PATTERN.search(content):
            if self._verified_data_absence(content, results):
                return content
            print("[ActionClaimGuard] blocked unverified data absence claim", flush=True)
            return "我需要先读取真实数据，才能回答目前是否为空。"
        claim_text = self._without_verified_read_statuses(content, results)
        if not ACTION_CLAIM_PATTERN.search(claim_text):
            return content
        if self._is_verified_read_fact(claim_text, results):
            return content
        if MEMORY_CLAIM_PATTERN.search(claim_text) or MEMORY_BATCH_CLAIM_PATTERN.search(claim_text):
            verified = any(
                result.success
                and result.status in {"success", "completed", "partial_success"}
                and result.operation_kind == "write"
                and result.tool in MEMORY_WRITE_TOOLS
                for result in results
            )
        else:
            verified = self._verified_action_claim(claim_text, results)
        if verified:
            return content
        if self._is_user_progress_echo(content, user_text):
            return content
        print("[ActionClaimGuard] blocked unverified completion claim", flush=True)
        return "目前还没有执行这项操作。你可以明确告诉我要执行什么，我会在实际成功后再确认结果。"

    @staticmethod
    def _without_verified_read_statuses(
        content: str,
        results: Iterable[ToolResult],
    ) -> str:
        """Do not confuse verified list status labels with write claims."""
        if any(
            result.success and result.operation_kind == "read"
            for result in results
        ):
            return re.sub(r"\[(?:已完成|待完成|已取消)\]", "", content)
        return content

    @staticmethod
    def _is_verified_read_fact(
        content: str,
        results: Iterable[ToolResult],
    ) -> bool:
        """Read facts may contain completion words, but not mutation claims."""
        has_successful_read = any(
            result.success and result.operation_kind == "read"
            for result in results
        )
        return has_successful_read and not EXPLICIT_MUTATION_CLAIM_PATTERN.search(content)

    @staticmethod
    def contains_action_claim(text: object) -> bool:
        value = str(text or "")
        return bool(
            ACTION_CLAIM_PATTERN.search(value)
            or MEMORY_ABSENCE_CLAIM_PATTERN.search(value)
            or MEMORY_PERSISTENCE_ASSURANCE_PATTERN.search(value)
        )

    @staticmethod
    def _is_explicit_memory_save_request(user_text: str) -> bool:
        text = re.sub(r"\s+", "", str(user_text or ""))
        return bool(
            re.search(r"(?:记住|保存|加入|放进|存入).{0,14}(?:长期)?记忆", text)
            or re.search(r"(?:记住|保存).{0,10}(?:刚才|上一条)", text)
            or re.search(r"(?:刚才|上一条).{0,12}(?:记住|保存)", text)
        )

    @staticmethod
    def _memory_id(result: ToolResult):
        operation = result.data.get("memory_operation", {})
        operation = operation if isinstance(operation, dict) else {}
        return operation.get("memory_id")

    @staticmethod
    def _verified_memory_absence(
        content: str,
        results: Iterable[ToolResult],
    ) -> bool:
        candidate_claim = bool(re.search(r"待审核|待确认|候选", content))
        allowed_tools = (
            {"list_memory_candidates", "show_memory_candidates"}
            if candidate_claim
            else {
                "list_memories",
                "search_memories",
                "show_memory",
                "search_memory",
                "list_archived_memories",
            }
        )
        data_key = "candidates" if candidate_claim else "memories"
        return any(
            result.success
            and result.operation_kind == "read"
            and result.tool in allowed_tools
            and isinstance(result.data.get(data_key), list)
            and not result.data.get(data_key)
            for result in results
        )

    @staticmethod
    def _verified_data_absence(
        content: str,
        results: Iterable[ToolResult],
    ) -> bool:
        if re.search(r"计划|任务", content):
            tool, key = "show_plan", "tasks"
        elif "行动记录" in content:
            tool, key = "show_action_log", "records"
        else:
            tool, key = "show_growth_log", "entries"
        return any(
            result.success
            and result.operation_kind == "read"
            and result.tool == tool
            and isinstance(result.data.get(key), list)
            and not result.data.get(key)
            for result in results
        )

    @staticmethod
    def _verified_action_claim(
        content: str,
        results: Iterable[ToolResult],
    ) -> bool:
        result_items = list(results)
        if "今天你计划了" in content:
            return any(
                result.success
                and result.operation_kind == "read"
                and result.tool == "generate_daily_review"
                for result in result_items
            )
        if "本地会话" in content and "保存" in content:
            return any(
                result.success
                and result.operation_kind == "read"
                and result.tool == "show_conversation_history"
                and isinstance(result.data.get("sessions"), list)
                for result in result_items
            )
        if "取消" in content:
            return any(
                result.success
                and result.operation_kind == "write"
                and result.tool == "cancel_plan"
                for result in result_items
            )
        verb_tools = (
            (r"添加|加入|创建", {"add_plan", "add_action_log", "create_memory_candidate", "save_formal_memory"}),
            (r"记录", {"add_action_log", "save_daily_review"}),
            (r"更新|修改|设置", {"update_plan", "reschedule_plan", "update_memory"}),
            (r"合并", {"merge_plan"}),
            (r"完成", {"complete_plan", "save_daily_review"}),
            (r"删除", {"delete_plan", "delete_memory", "delete_all_memories"}),
            (r"归档", {"archive_memory"}),
            (r"恢复", {"restore_memory", "reopen_plan"}),
            (r"确认|忽略", {
                "accept_memory_candidate", "accept_memory_candidates",
                "accept_all_memory_candidates", "reject_memory_candidate",
                "reject_memory_candidates",
            }),
            (r"保存|记住|记下", {"save_daily_review", "create_memory_candidate", "save_formal_memory"}),
            (r"安排", {"add_plan", "reschedule_plan"}),
        )
        allowed = set()
        for pattern, tools in verb_tools:
            if re.search(pattern, content):
                allowed.update(tools)
        return bool(allowed) and any(
            result.success
            and result.operation_kind == "write"
            and result.tool in allowed
            for result in result_items
        )

    @staticmethod
    def _is_user_progress_echo(content: str, user_text: str) -> bool:
        user_progress = re.search(
            r"(?:我|刚才|刚刚).{0,12}(?:完成|做完|弄完|学完)", str(user_text)
        )
        if not user_progress:
            return False
        claimed_actions = set(
            re.findall(
                r"添加|加入|设置|保存|完成|删除|记录|更新|归档|恢复", content
            )
        )
        return claimed_actions == {"完成"} and not re.search(
            r"(?:计划|任务|清单|状态).{0,6}(?:已|已经)?完成|"
            r"(?:完成|标记).{0,6}(?:计划|任务|清单)",
            content,
        )
