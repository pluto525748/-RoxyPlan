from __future__ import annotations

import re
from typing import Iterable

from modules.contracts import ToolResult


ACTION_CLAIM_PATTERN = re.compile(
    r"(?:(?:已经|已).{0,20}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|进入|完成|删除|记录|更新|归档|恢复)"
    r"|(?:我|帮你|给你).{0,14}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|进入|完成|删除|记录|更新|归档|恢复).{0,8}(?:了|成功)"
    r"|(?:添加|加入|设置|保存|创建|确认|忽略|进入|删除|记录|更新|归档|恢复)(?:成功|完成))"
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

DATA_ABSENCE_CLAIM_PATTERN = re.compile(
    r"(?:今天|现在|目前)?(?:没有|还没有)(?:任何|相关)?"
    r"(?:计划|任务|行动记录|成长日志)"
)

MEMORY_WRITE_TOOLS = {
    "create_memory_candidate",
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


class ActionClaimGuard:
    """Prevent model prose from claiming writes without a verified ToolResult."""

    def validate(
        self,
        text: str,
        tool_results: Iterable[ToolResult],
        *,
        user_text: str = "",
    ) -> str:
        content = str(text).strip()
        results = list(tool_results)
        if MEMORY_ABSENCE_CLAIM_PATTERN.search(content):
            if self._verified_memory_absence(content, results):
                return content
            print("[ActionClaimGuard] blocked unverified memory absence claim", flush=True)
            return "我需要先读取真实记忆数据，才能回答目前是否为空。"
        if DATA_ABSENCE_CLAIM_PATTERN.search(content):
            if self._verified_data_absence(content, results):
                return content
            print("[ActionClaimGuard] blocked unverified data absence claim", flush=True)
            return "我需要先读取真实数据，才能回答目前是否为空。"
        if not ACTION_CLAIM_PATTERN.search(content):
            return content
        if MEMORY_CLAIM_PATTERN.search(content) or MEMORY_BATCH_CLAIM_PATTERN.search(content):
            verified = any(
                result.success
            and result.status in {"success", "completed", "partial_success"}
                and result.tool in MEMORY_WRITE_TOOLS
                for result in results
            )
        else:
            verified = self._verified_action_claim(content, results)
        if verified:
            return content
        if self._is_user_progress_echo(content, user_text):
            return content
        print("[ActionClaimGuard] blocked unverified completion claim", flush=True)
        return "目前还没有执行这项操作。你可以明确告诉我要执行什么，我会在实际成功后再确认结果。"

    @staticmethod
    def contains_action_claim(text: object) -> bool:
        value = str(text or "")
        return bool(
            ACTION_CLAIM_PATTERN.search(value)
            or MEMORY_ABSENCE_CLAIM_PATTERN.search(value)
        )

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
                result.success and result.tool == "generate_daily_review"
                for result in result_items
            )
        if "本地会话" in content and "保存" in content:
            return any(
                result.success
                and result.tool == "show_conversation_history"
                and isinstance(result.data.get("sessions"), list)
                for result in result_items
            )
        if "取消" in content:
            return any(
                result.success and result.tool == "cancel_plan"
                for result in result_items
            )
        verb_tools = (
            (r"添加|加入|创建", {"add_plan", "add_action_log", "create_memory_candidate"}),
            (r"记录", {"add_action_log", "save_daily_review"}),
            (r"更新|修改|设置", {"update_plan", "reschedule_plan", "update_memory"}),
            (r"完成", {"complete_plan", "save_daily_review"}),
            (r"删除", {"delete_plan", "delete_memory", "delete_all_memories"}),
            (r"归档", {"archive_memory"}),
            (r"恢复", {"restore_memory", "reopen_plan"}),
            (r"确认|忽略", {
                "accept_memory_candidate", "accept_memory_candidates",
                "accept_all_memory_candidates", "reject_memory_candidate",
                "reject_memory_candidates",
            }),
            (r"保存", {"save_daily_review", "create_memory_candidate"}),
        )
        allowed = set()
        for pattern, tools in verb_tools:
            if re.search(pattern, content):
                allowed.update(tools)
        return bool(allowed) and any(
            result.success and result.tool in allowed for result in result_items
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
