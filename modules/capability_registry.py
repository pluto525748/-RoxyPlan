from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


VALID_REQUEST_MODES = {
    "chat",
    "discuss",
    "advice",
    "possible_action",
    "query",
    "execute",
    "correction",
    "cancellation",
    "confirmation",
}
VALID_CONFIRMATION_POLICIES = {"never", "when_ambiguous", "always"}


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    domain: str
    description: str
    user_value: str
    request_modes: Tuple[str, ...]
    tool_names: Tuple[str, ...] = ()
    required_fields: Tuple[str, ...] = ()
    optional_fields: Tuple[str, ...] = ()
    read_only: bool = False
    side_effect: bool = False
    reversible: bool = False
    risk_level: str = "low"
    confirmation_policy: str = "never"
    supports_batch: bool = False
    supports_reference: bool = False
    supports_clarification: bool = False
    model_visible: bool = False
    deterministic_routes: Tuple[str, ...] = ()
    example_positive: Tuple[str, ...] = ()
    example_negative: Tuple[str, ...] = ()
    user_guidance: str = ""

    @property
    def canonical_tool_name(self) -> str:
        return self.tool_names[0] if self.tool_names else ""


class CapabilityRegistry:
    """Authoritative catalog for existing product capabilities and policies."""

    def __init__(self, definitions: Iterable[CapabilityDefinition]) -> None:
        self._by_id: Dict[str, CapabilityDefinition] = {}
        self._by_tool: Dict[str, CapabilityDefinition] = {}
        for definition in definitions:
            self._register(definition)

    def _register(self, definition: CapabilityDefinition) -> None:
        if definition.capability_id in self._by_id:
            raise ValueError(f"Duplicate capability: {definition.capability_id}")
        if not set(definition.request_modes).issubset(VALID_REQUEST_MODES):
            raise ValueError(f"Invalid request mode: {definition.capability_id}")
        if definition.confirmation_policy not in VALID_CONFIRMATION_POLICIES:
            raise ValueError(f"Invalid confirmation policy: {definition.capability_id}")
        for tool_name in definition.tool_names:
            if tool_name in self._by_tool:
                raise ValueError(f"Tool belongs to multiple capabilities: {tool_name}")
            self._by_tool[tool_name] = definition
        self._by_id[definition.capability_id] = definition

    def get(self, capability_id: str) -> Optional[CapabilityDefinition]:
        return self._by_id.get(str(capability_id))

    def for_tool(self, tool_name: str) -> Optional[CapabilityDefinition]:
        return self._by_tool.get(str(tool_name))

    def definitions(self) -> List[CapabilityDefinition]:
        return list(self._by_id.values())

    def capability_ids(self) -> List[str]:
        return list(self._by_id)

    def guidance_for_tool(self, tool_name: str) -> str:
        definition = self.for_tool(tool_name)
        return str(definition.user_guidance if definition is not None else "").strip()

    def user_help_docs(self) -> str:
        """Render the user-facing product guide from this authoritative catalog."""
        lines = [
            str(item.user_guidance).strip()
            for item in self.definitions()
            if str(item.user_guidance).strip()
        ]
        return (
            "当前 RoxyPlan 功能与操作说明（回答能力或用法问题时必须以此为准）：\n- "
            + "\n- ".join(lines)
            + "\n- 当前边界：开始学习计时、复杂计划修改／合并／改期、"
            "批量删除计划、批量删除记忆和候选记忆不是可靠聊天能力。"
            "用户问到时应明确说明限制，并提供上面已有的单项或面板路径；"
            "不要虚构命令、入口或成功结果。\n"
            "用户只是在询问功能或操作方法时，只说明步骤，不执行工具、不建立确认操作。"
        )

    @staticmethod
    def is_user_help_query(text: str) -> bool:
        """Recognize capability/how-to purpose without choosing an action."""
        value = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
        if not value:
            return False
        if re.search(
            r"(?:你|洛琪希|Roxy)?(?:现在)?(?:能做什么|会做什么|可以做什么|"
            r"能帮我(?:做|处理)什么|可以帮我(?:做|处理)什么|"
            r"有哪些功能|支持(?:哪些|什么)功能|支持什么|介绍.{0,8}功能|"
            r"功能介绍|操作说明|使用说明|怎么用|如何使用)",
            value,
            re.IGNORECASE,
        ):
            return True
        operation_or_domain = re.search(
            r"(?:计划|任务|待办|记忆|行动记录|复盘|成长日志|历史会话|"
            r"提醒|桌宠|跳舞|休息|唤醒|添加|完成|删除|修改|保存|查看|记录)",
            value,
        )
        operation_word = (
            r"(?:操作|使用|添加|加入|完成|删除|删掉|修改|保存|查看|记录|"
            r"暂停|恢复|跳舞|休息|唤醒)"
        )
        explicit_how_to = re.search(
            rf"(?:(?:怎么|如何|怎样){operation_word}|"
            rf"(?:不会|不知道|不清楚)(?:该)?(?:怎么|如何|怎样)?{operation_word}|"
            r"操作方法|操作步骤|该说什么|有这个功能吗|支持吗)",
            value,
        )
        # Modal questions are help only when they ask whether a named
        # capability supports an operation.  A generic polite suffix such as
        # “可以吗” is not enough: it also appears in real requests including
        # “请今日计划可以吗” and “能把你说的加入今日计划吗”.
        operation = r"(?:添加|加入|完成|删除|修改|保存|查看|记录|暂停|恢复|跳舞|休息|唤醒)"
        domain = r"(?:今日计划|今天计划|计划|任务|待办|长期记忆|正式记忆|记忆|行动记录|复盘|成长日志|历史会话|提醒|桌宠)"
        modal_capability_question = re.search(
            rf"(?:{domain}.{{0,8}}(?:能不能|可不可以|可以|能否|是否支持){operation}吗$|"
            rf"^(?:能不能|可不可以|能否|是否支持){operation}.{{0,8}}{domain}吗$)",
            value,
        )
        help_purpose = explicit_how_to or modal_capability_question
        return bool(operation_or_domain and help_purpose)

    def evaluation_catalog(self) -> List[Dict[str, object]]:
        return [
            {
                "capability_id": item.capability_id,
                "domain": item.domain,
                "request_modes": list(item.request_modes),
                "tool_names": list(item.tool_names),
                "required_fields": list(item.required_fields),
                "optional_fields": list(item.optional_fields),
                "read_only": item.read_only,
                "side_effect": item.side_effect,
                "risk_level": item.risk_level,
                "confirmation_policy": item.confirmation_policy,
            }
            for item in self.definitions()
        ]


def _cap(
    capability_id: str,
    domain: str,
    description: str,
    modes: Tuple[str, ...],
    tools: Tuple[str, ...] = (),
    *,
    required: Tuple[str, ...] = (),
    optional: Tuple[str, ...] = (),
    read_only: bool = False,
    side_effect: bool = False,
    reversible: bool = False,
    risk: str = "low",
    confirm: str = "never",
    batch: bool = False,
    reference: bool = False,
    clarification: bool = False,
    model_visible: bool = False,
    routes: Tuple[str, ...] = (),
    positive: Tuple[str, ...] = (),
    negative: Tuple[str, ...] = (),
    guidance: str = "",
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        domain=domain,
        description=description,
        user_value=description,
        request_modes=modes,
        tool_names=tools,
        required_fields=required,
        optional_fields=optional,
        read_only=read_only,
        side_effect=side_effect,
        reversible=reversible,
        risk_level=risk,
        confirmation_policy=confirm,
        supports_batch=batch,
        supports_reference=reference,
        supports_clarification=clarification,
        model_visible=model_visible,
        deterministic_routes=routes,
        example_positive=positive,
        example_negative=negative,
        user_guidance=str(guidance).strip(),
    )


DEFAULT_CAPABILITIES = (
    _cap("ordinary_chat", "chat", "普通陪伴聊天", ("chat", "discuss"), guidance="可以正常闲聊、询问程序能力、请求学习建议；这些对话不会自动写入计划或长期记忆。桌宠菜单还可打开聊天、设置、成长和正式记忆面板。"),
    _cap("identity_query", "chat", "回答洛琪希身份", ("query",), routes=("你是谁",)),
    _cap("personality_impression", "chat", "基于已核验正式记忆自然总结用户印象", ("advice", "query")),
    _cap("advice_request", "chat", "提供建议而不写入业务数据", ("advice", "discuss")),
    _cap("list_plans", "plan", "查询指定日期计划", ("query",), ("show_plan",), optional=("date",), read_only=True, model_visible=True, routes=("今日计划", "明日计划"), positive=("我今天干啥",), guidance="查询计划可说“查看今天计划”“查看昨天计划”或“明天有什么计划”。"),
    _cap("inspect_plan_duplicates", "plan", "检查相近计划并提供可确认的合并候选", ("query",), ("inspect_plan_duplicates",), optional=("date",), read_only=True, model_visible=False, positive=("今天的计划有重复吗",), guidance="计划查重和自动合并当前不作为可靠聊天能力；请先查看今天计划，再自行指定要保留或删除的单项。"),
    _cap("add_plan", "plan", "新增计划", ("execute", "possible_action"), ("add_plan",), required=("title",), optional=("date", "time_slot", "duration_minutes", "allow_duplicate"), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", batch=True, clarification=True, model_visible=True, positive=("把随机森林复习加到今天",), negative=("你觉得我下午学啥",), guidance="新增计划要说明日期和具体事项，例如“今天加入三项计划：英语阅读、整理简历、复习 Python”；后续也可说“再加一项今天计划：……”。"),
    _cap("update_plan", "plan", "修改计划字段", ("execute", "correction"), ("update_plan",), required=("task_ref",), optional=("title", "date", "time_slot", "duration_minutes", "priority", "note"), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", batch=True, reference=True, clarification=True, model_visible=False, guidance="复杂计划修改、合并、改期和重排当前不作为可靠聊天能力；可先查看计划，删除明确单项后按完整新内容重新添加。"),
    _cap("merge_plan", "plan", "确认后合并相近计划并保留信息更完整的一条", ("execute", "confirmation"), ("merge_plan",), required=("target_ref",), optional=("duplicate_refs", "changes", "date"), side_effect=True, risk="high", confirm="always", batch=True, reference=True, clarification=True, model_visible=False, guidance="计划合并当前不作为可靠聊天能力；请先查看计划，删除不需要的明确单项。"),
    _cap("reschedule_plan", "plan", "调整计划日期或时段", ("execute", "correction"), ("reschedule_plan",), required=("task_ref",), optional=("date", "time_slot", "schedule_text"), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", batch=True, reference=True, clarification=True, model_visible=False, guidance="计划改期当前不作为可靠聊天能力；可先删除明确单项，再按新日期和完整内容重新添加。"),
    _cap("complete_plan", "plan", "完成计划", ("execute",), ("complete_plan",), required=("task_ref",), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", batch=True, reference=True, clarification=True, model_visible=True, guidance="完成计划可说完整标题，或先查看计划后说“完成第2条”“刚才展示的都完成了”；多项操作会先核对真实目标。"),
    _cap("reopen_plan", "plan", "重新打开已完成计划", ("execute",), ("reopen_plan",), required=("task_ref",), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", reference=True, model_visible=False, guidance="重新打开已完成计划当前不作为可靠聊天能力；需要继续做时可添加一条新的今日计划。"),
    _cap("cancel_plan", "plan", "取消计划", ("execute", "cancellation"), ("cancel_plan",), required=("task_ref",), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", reference=True, model_visible=False, guidance="取消计划当前不作为可靠聊天能力；如需移除，请先查看计划并逐条删除明确单项。"),
    _cap("delete_plan", "plan", "永久删除计划", ("execute",), ("delete_plan",), required=("task_ref",), side_effect=True, risk="high", confirm="always", reference=True, model_visible=False, guidance="删除计划时先说“查看今天计划”，再说“删除第2条”或给出完整标题，核对目标后回复“确认”；当前不支持聊天中一次删除全部计划。"),
    _cap("list_action_logs", "action_log", "查询行动记录", ("query",), ("show_action_log",), optional=("date",), read_only=True, model_visible=True, guidance="查看实际行动可说“查看今天行动记录”。"),
    _cap("add_action_log", "action_log", "记录已经发生的行动", ("execute",), ("add_action_log",), required=("content",), optional=("date",), side_effect=True, reversible=True, risk="medium", confirm="when_ambiguous", batch=True, clarification=True, model_visible=True, guidance="计划外已经完成的事情可说“记录今天的计划外行动：学会了一道菜”；行动记录不会自动变成计划或长期记忆。"),
    _cap("list_memories", "memory", "按类型查询正式长期记忆", ("query",), ("list_memories", "show_memory"), optional=("category", "query_mode", "attribute", "topic", "query"), read_only=True, model_visible=True, guidance="读取正式记忆可说“查看长期记忆”“我叫什么”“看看项目记忆”或“你了解我什么”；编辑、归档、恢复和冲突处理请使用正式记忆面板。"),
    _cap("search_memories", "memory", "搜索正式长期记忆", ("query",), ("search_memories", "search_memory"), required=("query",), read_only=True, model_visible=True),
    _cap("list_memory_candidates", "memory", "内部兼容：读取旧待审核记忆", ("query",), ("list_memory_candidates", "show_memory_candidates"), read_only=True, model_visible=False),
    _cap("save_formal_memory", "memory", "保存用户明确要求记住的正式长期记忆", ("execute",), ("save_formal_memory", "request_add_memory"), required=("content",), optional=("category", "source", "confirmed"), side_effect=True, reversible=True, risk="medium", confirm="never", clarification=True, model_visible=True, guidance="保存正式长期记忆要说“记住：完整内容”；普通闲聊不会自动保存，候选记忆功能已经停用。"),
    _cap("create_memory_candidate", "memory", "内部兼容：写入旧候选结构（非用户能力）", ("execute",), ("create_memory_candidate", "queue_memory_candidate"), required=("content",), optional=("category",), side_effect=True, reversible=True, risk="medium", confirm="never", clarification=True, model_visible=False),
    _cap("accept_memory_candidate", "memory", "内部兼容：接受旧待审核记录", ("execute", "confirmation"), ("accept_memory_candidate", "accept_memory_candidates", "accept_all_memory_candidates"), required=("candidate_id",), side_effect=True, reversible=True, risk="medium", confirm="never", batch=True, reference=True, model_visible=False),
    _cap("reject_memory_candidate", "memory", "内部兼容：忽略旧待审核记录", ("execute", "cancellation"), ("reject_memory_candidate", "reject_memory_candidates"), required=("candidate_id",), side_effect=True, reversible=True, risk="medium", confirm="never", batch=True, reference=True, model_visible=False),
    _cap("update_memory", "memory", "修改正式长期记忆", ("execute", "correction"), ("update_memory",), required=("memory_id", "content"), side_effect=True, risk="high", confirm="always", reference=True, model_visible=False),
    _cap("archive_memory", "memory", "归档长期记忆", ("execute",), ("archive_memory",), required=("memory_id",), side_effect=True, reversible=True, risk="medium", confirm="never", reference=True, model_visible=False),
    _cap("restore_memory", "memory", "恢复已归档记忆", ("execute",), ("restore_memory",), required=("memory_id",), side_effect=True, reversible=True, risk="medium", confirm="never", reference=True, model_visible=False),
    _cap("delete_memory", "memory", "永久删除长期记忆", ("execute",), ("delete_memory", "delete_all_memories"), required=("memory_id",), side_effect=True, risk="high", confirm="always", reference=True, model_visible=False, guidance="删除一条正式长期记忆时先查看准确原文，再发送“忘记：完整记忆正文”，核对真实目标后回复“确认”；不支持关键词、指代或批量删除。"),
    _cap("list_archived_memories", "memory", "查询已归档长期记忆", ("query",), ("list_archived_memories",), read_only=True, model_visible=False),
    _cap("list_memory_conflicts", "memory", "查询记忆冲突", ("query",), ("list_memory_conflicts", "show_memory_conflicts"), read_only=True, model_visible=False),
    _cap("show_memory_audit", "memory", "查看记忆审计记录", ("query",), ("show_memory_audit",), read_only=True, model_visible=False),
    _cap("resolve_memory_conflict", "memory", "处理记忆冲突", ("execute",), ("resolve_memory_conflict",), required=("conflict_id", "resolution"), side_effect=True, risk="high", confirm="always", reference=True, model_visible=False),
    _cap("daily_review", "growth", "生成今日复盘", ("query",), ("generate_daily_review",), read_only=True, model_visible=True, guidance="查看当天完成、未完成和计划外行动可说“查看今日复盘”。"),
    _cap("save_review", "growth", "保存或明确重新生成指定日期复盘", ("execute",), ("save_daily_review",), optional=("date",), side_effect=True, reversible=True, risk="medium", confirm="never", model_visible=True, guidance="保存可说“生成并保存今天的复盘”；修订昨天可说“重新生成昨天的复盘”。"),
    _cap("show_growth_log", "growth", "按自然月查询成长日志", ("query",), ("show_growth_log",), optional=("month",), read_only=True, model_visible=True, guidance="查看成长记录可说“查看本月成长日志”或指定自然月；总结只能依据已核验记录。"),
    _cap("show_recent_conversation", "conversation", "查看当前会话最近内容", ("query",), ("show_recent_conversation",), required=("conversation_id",), optional=("current_message",), read_only=True, model_visible=True),
    _cap("show_conversation_history", "conversation", "模糊查询有证据的历史会话", ("query",), ("show_conversation_history",), optional=("query", "exclude_today", "limit"), read_only=True, model_visible=True, guidance="查旧会话可说“之前我们聊过简历吗”；只会返回最相关的真实片段、日期和来源，没有证据就明确说未找到。"),
    _cap("pause_reminders", "reminder", "暂停主动提醒", ("execute",), ("pause_reminders",), side_effect=True, reversible=True, risk="medium", confirm="never", model_visible=True, guidance="提醒控制可说“先别提醒我”；恢复时说“恢复提醒”。"),
    _cap("resume_reminders", "reminder", "恢复主动提醒", ("execute",), ("resume_reminders",), side_effect=True, reversible=True, risk="medium", confirm="never", model_visible=True),
    _cap("play_dance", "pet", "播放现有舞蹈动画", ("execute",), ("play_dance",), side_effect=True, risk="low", confirm="never", model_visible=True, positive=("来段舞",), negative=("我不想跳舞", "你跳得真棒"), guidance="桌宠动作可说“来段舞”“洛琪希，你休息一下”或“醒醒”；只有客户端实际接受动作后才能说已执行。"),
    _cap("sleep", "pet", "让桌宠休息", ("execute",), ("sleep_pet",), side_effect=True, reversible=True, risk="medium", confirm="never", model_visible=True),
    _cap("wake", "pet", "唤醒桌宠", ("execute",), ("wake_pet",), side_effect=True, reversible=True, risk="low", confirm="never", model_visible=True),
    _cap("jump", "pet", "桌宠跳跃动作", ("execute",)),
    _cap("shake", "pet", "桌宠摇晃动作", ("execute",)),
    _cap("scale", "pet", "桌宠缩放提醒动作", ("execute",)),
    _cap("show_bubble", "pet", "显示安全文本气泡", ("execute",)),
)


DEFAULT_CAPABILITY_REGISTRY = CapabilityRegistry(DEFAULT_CAPABILITIES)
