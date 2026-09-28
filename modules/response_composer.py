from __future__ import annotations

import re
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional

from modules.chinese_entity_parser import ChineseEntityParser
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.contracts import AgentResponse, ClientAction, ResponseOutcome, ToolResult
from modules.development_log import get_development_log
from modules.intent_router import (
    is_explicit_pet_sleep_request,
    reminder_control_action,
)
from modules.llm.response_sanitizer import sanitize_public_reply


# ── Minimal structural guard for CHAT messages ──────────────────────────
# Without ToolResults, no execution happened.  Any claim of completion is
# structurally impossible — this is not a keyword heuristic; it is a logical
# constraint enforced at the response boundary.

_FALSE_EXECUTION_CLAIM = re.compile(
    r"(?:(?:我|这边|系统)(?:已经|已)|(?:已经|已)(?:帮你|替你|为你|把|将)).{0,20}"
    r"(?:添加|加入|设置|保存|创建|确认|忽略|完成|删除|记录|更新|归档|恢复|安排|记住|记下|记进)"
    r"|(?:添加|加入|设置|保存|创建|确认|忽略|进入|删除|记录|更新|归档|恢复|安排)(?:成功|完成|好了)"
    r"|(?:我|这边)(?:已经)?(?:帮你)?(?:把.{0,20})?(?:记下|记进|记录|保存|加入|添加|安排)(?:了|好了)"
    r"|(?:我|这边)(?:现在)?(?:帮你)?把.{0,24}"
    r"(?:记进|记录(?:到|在)|保存(?:到|进)|加入|添加(?:到|进)|放进)"
    r".{0,6}(?:今天|今日)?(?:的)?"
    r"(?:行动记录|计划|长期记忆|记忆|待办|清单|本地记录|系统记录)"
    r"(?:了|啦|好)?(?:[。！!]|$)"
)

# Modal ability, completed execution and a committed transaction are distinct
# reply assertions.  These patterns inspect assistant prose only; they never
# infer a user's intent or authorize a tool call.
_MEMORY_SAVE_ASSERTION = re.compile(
    r"(?:我|这边)(?:已经|已|刚刚|刚才)?(?:帮你)?"
    r"(?:记住|记下|记牢)(?:了|啦|好了)"
)
_FORMAL_MEMORY_SAVE_ASSERTION = re.compile(
    r"(?:我|这边)(?:已经|已|刚刚|刚才)?(?:帮你)?"
    r"(?:记住|记牢)(?:了|啦|好了)"
)
_REPLY_APP_DOMAIN = re.compile(
    r"(?:今日|今天)?(?:计划|待办|清单)|行动记录|长期记忆|正式记忆|本地记录|系统记录"
)
_REPLY_MUTATION_FAMILIES = (
    ("delete", re.compile(r"删除|删掉|清空")),
    ("add", re.compile(r"加入|添加|加进|放进")),
    ("save", re.compile(r"保存|记住|记牢")),
    ("record", re.compile(r"记录|记下|记进")),
    ("update", re.compile(r"更新|修改|改期|重排|合并")),
    ("complete", re.compile(r"标记完成|完成|做完")),
)
_PENDING_MUTATION_FAMILIES = {
    "add_plan": "add",
    "complete_plan": "complete",
    "save_formal_memory": "save",
    "add_action_log": "record",
    "delete_plan": "delete",
    "delete_memory": "delete",
    "update_memory": "update",
    "update_plan": "update",
    "reschedule_plan": "update",
    "merge_plans": "update",
}
_TRANSACTION_CONFIRMATION_REQUEST = re.compile(
    r"你(?:确认|同意|确定)|请(?:你)?确认|回复.{0,4}确认|"
    r"确认(?:后|一下|删除|添加|保存|完成)"
)
_COMMITTED_REPLY_WRITE = re.compile(
    r"(?:我|这边|系统)(?:现在)?(?:会|将|就|再)(?:帮你|替你|为你)?"
    r".{0,32}(?:删除|删掉|清空|加入|添加|保存|记住|记录|记下|记进|标记完成|更新|修改)"
    r"|(?:我|这边|系统)(?:现在)?可以(?:帮你|替你|为你)?把"
    r".{0,24}(?:删除|加入|添加|保存|记录|记下|记进|标记完成)"
)


def _reply_mutation_family(message: str) -> str:
    return next(
        (name for name, pattern in _REPLY_MUTATION_FAMILIES if pattern.search(message)),
        "",
    )


def _has_matching_confirmation_pending(
    message: str,
    verified: Mapping[str, object],
) -> bool:
    """Only program-verified typed pending state can authorize confirmation."""
    raw = verified.get("confirmation_pending", {})
    if not isinstance(raw, Mapping) or raw.get("active") is not True:
        return False
    count = raw.get("object_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return False
    capability = str(raw.get("capability", "") or "")
    family = _reply_mutation_family(message)
    if not family or _PENDING_MUTATION_FAMILIES.get(capability, "") != family:
        return False
    # An enabled transaction of the same verb is not authority for another
    # data domain (for example, a plan deletion cannot authorize memory deletion).
    if re.search(r"长期记忆|正式记忆", message) and capability not in {
        "save_formal_memory", "delete_memory", "update_memory"
    }:
        return False
    if re.search(r"(?:今日|今天)?(?:计划|待办|清单)", message) and capability not in {
        "add_plan", "complete_plan", "delete_plan", "update_plan",
        "reschedule_plan", "merge_plans"
    }:
        return False
    if "行动记录" in message and capability != "add_action_log":
        return False
    return True


def _requests_mutation_confirmation(message: str) -> bool:
    return bool(
        _TRANSACTION_CONFIRMATION_REQUEST.search(message)
        and _reply_mutation_family(message)
        and (
            _REPLY_APP_DOMAIN.search(message)
            or _COMMITTED_REPLY_WRITE.search(message)
        )
    )


def _claims_committed_app_write(message: str) -> bool:
    if not _COMMITTED_REPLY_WRITE.search(message):
        return False
    return bool(
        _REPLY_APP_DOMAIN.search(message)
        or re.search(r"把(?:这|那|它|该).{0,12}(?:保存|记住|记下|记进|加入|添加)", message)
    )


_AMBIGUOUS_DEICTIC_EXECUTION_CLAIM = re.compile(
    r"(?:(?:这|那)(?:项|条|件事?|个)|该(?:项|条|件)|它)"
    r".{0,8}(?:算|已|已经)?"
    r"(?P<action>完成|加入|记录|保存)(?:了|好了)"
)

_FALSE_DATA_ABSENCE = re.compile(
    r"(?:今天|现在|目前)?(?:没有|还没有)(?:任何|相关)?"
    r"(?:计划|任务|行动记录|成长日志|记忆|长期记忆|正式记忆)"
)

_UNVERIFIED_MEMORY_ABSENCE = re.compile(
    r"(?:正式(?:长期)?记忆|长期记忆).{0,28}"
    r"(?:是空的|为空|没有(?:任何|你的|相关)?(?:记录|内容|信息|昵称|偏好|记忆))"
    r"|(?:没有|不存在)(?:任何|相关)?(?:正式(?:长期)?记忆|长期记忆)"
)


def _is_delete_help_query(text: str, domain_pattern: str) -> bool:
    value = re.sub(r"\s+", "", str(text or "")).strip("。！!？?")
    return bool(
        re.search(r"(?:怎么|如何|怎样|怎么办|操作方法|操作步骤|功能|该说什么)", value)
        and re.search(r"(?:删除|删掉|删|移除|忘记|忘掉)", value)
        and re.search(domain_pattern, value)
    )


def _is_memory_delete_help_query(text: str) -> bool:
    return _is_delete_help_query(text, r"(?:长期记忆|正式记忆|记忆)")


def _is_plan_delete_help_query(text: str) -> bool:
    return _is_delete_help_query(text, r"(?:今日计划|今天计划|计划|任务|待办)")


def _is_unbound_assistant_plan_add_request(text: str) -> bool:
    value = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
    has_reference = bool(
        re.search(
            r"(?:你(?:刚才|刚刚|方才|上面|前面)?(?:说的|给的|整理的|列的|建议的|安排的)"
            r"|(?:刚才|刚刚|上面|前面)(?:说的|这些|那些|建议|安排|内容))",
            value,
        )
    )
    has_destination = bool(
        re.search(
            r"(?:加入|添加|加进|放进|放入|纳入|加到|放到)"
            r"(?:我)?(?:今天|今日)?(?:的)?(?:计划|任务|待办|清单)",
            value,
        )
    )
    return has_reference and has_destination

_UNVERIFIED_RECORDED_PLAN_COMPLETION = re.compile(
    r"(?:计划|任务|待办|清单).{0,16}(?:一项项|都|全部|基本)?"
    r"(?:已经|已)?(?:完成|做完)"
    r"|(?:把|将).{0,4}(?:计划|任务|待办|清单).{0,16}(?:完成|做完)"
)

_ACKNOWLEDGES_UNCHANGED_RECORDED_STATE = re.compile(
    r"(?:本地|系统|计划)?记录.{0,12}(?:还没有|没有|还没|尚未)(?:同步|更新|修改)"
    r"|(?:还没有|没有|还没|尚未)(?:同步|更新|修改).{0,8}(?:本地|系统|计划)?记录"
)

_PLAN_WRITE_OPERATIONS = {
    "add",
    "complete",
    "delete",
    "update",
    "reschedule",
    "reopen",
    "cancel",
    "merge",
}
_ASSERTED_CHANGE_MARKER = re.compile(r"(?:已经|已|成功|好了|完成啦|完成了)")
_QUESTION_MARKER = re.compile(r"(?:吗|么|呢|是否|有没有|要不要|能否|可不可以)[？?]?$|[？?]$")
_REPLY_ENTITY_PARSER = ChineseEntityParser()

_PRIOR_OPERATION_TERMS = {
    "plan_added": ("添加", "加入", "加进", "放进", "记进"),
    "plan_completed": ("完成", "做完", "标记"),
    "plan_deleted": ("删除", "删掉"),
    "plan_updated": ("更新", "修改", "改好"),
    "plan_rescheduled": ("改期", "调整", "改到"),
    "plan_reopened": ("重新打开", "恢复"),
    "plan_cancelled": ("取消",),
}

_NEGATED_APP_INTENT = re.compile(
    r"(?:不是说|并不是说|我没说).{0,30}"
    r"(?:添加|加入|加进|放进|完成|删除|更新|修改)?"
    r".{0,12}(?:今日|今天)?(?:计划|任务|待办|清单)|"
    r"(?:我是说|而是|说的是).{0,30}(?:不是|并非).{0,20}"
    r"(?:今日|今天)?(?:计划|任务|待办|清单)"
)


def _ambiguous_deictic_claim_requires_guard(
    message: str,
    user_text: str,
    verified: Mapping[str, object],
) -> bool:
    matches = list(_AMBIGUOUS_DEICTIC_EXECUTION_CLAIM.finditer(message))
    if not matches:
        return False
    # Saving, recording and adding are application mutations in this response
    # shape.  Inspect every match so a later false write cannot hide behind an
    # earlier harmless completion acknowledgement.
    if any(
        str(match.groupdict().get("action", "") or "") != "完成"
        for match in matches
    ):
        return True
    if bool(verified.get("user_reported_plan_progress")) or str(
        verified.get("semantic_intent", "") or ""
    ) == "complete_plan":
        return True
    # Outside a verified plan-completion context, “这件事已经完成了” describes
    # reality rather than a RoxyPlan write.  With no turn context at all, keep
    # the conservative fallback used by direct/system callers.
    return not str(user_text or "").strip() and not bool(verified)


def _claims_unverified_plan_change(message: str) -> bool:
    """Detect a completed plan mutation using the shared semantic parser.

    This keeps the response boundary aligned with the application's supported
    plan operations instead of maintaining another list of user utterances.
    Questions and suggestions remain normal chat; only an asserted, completed
    mutation is impossible when the verified turn contains no ToolResult.
    """
    if not message or re.search(
        r"(?:吗|么|是否|有没有|要不要|能否|可不可以)[？?]?|[？?]",
        message,
    ):
        return False
    if _NEGATED_APP_INTENT.search(message) or re.search(
        r"(?:没有必要|不用|无需|不需要).{0,12}(?:重新)?"
        r"(?:添加|加入|加进|放进|完成|删除|更新|修改)",
        message,
    ):
        return False
    parsed = _REPLY_ENTITY_PARSER.parse(message)
    operation = str(parsed.get("operation", "") or "").strip()
    if operation not in _PLAN_WRITE_OPERATIONS:
        return False
    assistant_or_system_actor = bool(
        re.search(
            r"(?:我|这边|系统|本地)(?:已经|已|刚刚|会|将|可以|帮你|替你|为你)"
            r"|(?:帮你|替你|为你)",
            message,
        )
    )
    asserted_change = bool(_ASSERTED_CHANGE_MARKER.search(message)) or bool(
        assistant_or_system_actor
        and re.search(r"(?:了|好了)[。！？!?]?$", message.strip())
    )
    if not asserted_change:
        return False
    user_real_world_actor = bool(
        re.search(
            r"(?:听起来|看起来|看来|恭喜|原来|所以)?你"
            r"(?:现在|刚刚|刚才|已经|已|还没有|还没|把|将)",
            message,
        )
    )
    if user_real_world_actor and not assistant_or_system_actor:
        return False
    app_domain = bool(
        re.search(
            r"(?:今日|今天)?(?:计划|任务|待办|清单)|"
            r"(?:本地|系统|计划|行动|长期记忆)(?:记录|记忆)|"
            r"(?:时长|时间段)",
            message,
        )
    )
    return assistant_or_system_actor or app_domain


def _verified_prior_operation(
    context: Mapping[str, object],
) -> Dict[str, str]:
    raw = context.get("prior_verified_operation", {})
    raw = raw if isinstance(raw, Mapping) else {}
    kind = str(raw.get("kind", "") or "").strip()
    title = " ".join(str(raw.get("title", "") or "").split()).strip()
    status = str(raw.get("status", "") or "").strip()
    if kind not in _PRIOR_OPERATION_TERMS or not title or status != "success":
        return {}
    return {"kind": kind, "title": title, "status": status}


def _prior_memory_save_acknowledgement(
    user_text: str,
    verified: Mapping[str, object],
) -> str:
    """Answer a past-save query from revalidated facts, never license LLM prose.

    ConversationService has already checked the same-session operation's age,
    live record identity and content.  A new save request cannot borrow that
    success, and an arbitrary reply cannot add another fact or transaction.
    """
    raw = verified.get("prior_memory_operation", {})
    if (
        not isinstance(raw, Mapping) or raw.get("kind") != "memory_saved"
        or raw.get("status") != "success"
    ):
        return ""
    content = str(raw.get("content", "") or "").strip()
    if not content or not raw.get("observed_at"):
        return ""
    text = re.sub(r"\s+", "", str(user_text or "")).strip("。！!")
    if (
        not text
        or re.search(r"(?:请|帮我|帮忙|现在|重新|再|以后|接下来).{0,8}(?:记住|保存)", text)
        or re.search(r"记住[：:]|保存[：:]|加入|添加|删除|更新|标记完成", text)
    ):
        return ""
    short_query = bool(re.fullmatch(
        r"(?:你|洛琪希|Roxy)?(?:刚才|刚刚|之前|上一轮)?(?:已经|已)?"
        r"(?:记住|保存)(?:了)?(?:吗|么|没|没有|了没有)[？?]?",
        text,
        re.IGNORECASE,
    ))
    if not short_query and (
        re.match(r"(?:记住|保存)", text)
        or len(re.findall(r"记住|保存", text)) != 1
    ):
        return ""
    past_query = bool(
        (
            _QUESTION_MARKER.search(text)
            or re.search(r"(?:什么|哪些|什么内容|了没|了没有)[？?]?$", text)
        )
        and re.search(r"(?:刚才|刚刚|之前|上一轮)", text)
        and re.search(r"记住|保存", text)
        and (
            re.search(r"(?:你|洛琪希|Roxy|那条|这条|那项|这项|记忆)", text, re.IGNORECASE)
            or re.sub(r"\s+", "", content) in text
        )
    )
    if not short_query and not past_query:
        return ""
    return f"刚才已经保存了这条长期记忆：{content}。这一轮没有新增保存。"


def _user_references_prior_success(
    user_text: str,
    prior: Mapping[str, str],
) -> bool:
    text = re.sub(r"\s+", "", str(user_text or ""))
    if not text:
        return False
    contains_question = bool(_QUESTION_MARKER.search(text))
    if _NEGATED_APP_INTENT.search(text) and not contains_question:
        return False
    title = re.sub(r"\s+", "", str(prior.get("title", "") or ""))
    if (
        str(prior.get("kind", "")) == "plan_added"
        and title
        and title in text
        and re.search(
            r"(?:已经|已).{0,6}(?:显示|出现在).{0,12}"
            r"(?:今日|今天)(?:计划|待办)",
            text,
        )
        and not re.search(r"(?:没有|还没|并未|尚未|不再|未显示)", text)
    ):
        return True
    terms = _PRIOR_OPERATION_TERMS.get(str(prior.get("kind", "")), ())
    matched_terms = [term for term in terms if term in text]
    if not matched_terms:
        return False
    operation = "(?:" + "|".join(re.escape(term) for term in terms) + ")"
    if re.search(
        rf"(?:没有|还没|并未|尚未|不再|未(?=.{{0,6}}{operation}))|"
        rf"{operation}.{{0,6}}(?:失败|没成功|未成功)",
        text,
    ):
        return False
    if re.search(
        r"(?:明天|后天|以后|之后|稍后|待会)|"
        r"(?:想|要|准备|打算|希望|请|帮).{0,8}"
        r"(?:添加|加入|加进|放进|记进|完成|做完|标记|删除|删掉|更新|修改|"
        r"改好|改期|调整|改到|重新打开|恢复|取消)",
        text,
    ):
        return False

    # Preserve the exact elliptical UI-correction shape from the incident.
    # The operation must be sentence-final, so a new external destination
    # after “加入/完成/删除” cannot be mistaken for RoxyPlan state.
    if re.fullmatch(
        rf"(?:我)?(?:看到|看见|明明|不是说)"
        rf"(?:它|这个|那个|{re.escape(title)})?"
        rf"(?:已经|已|都|也)?{operation}(?:到|进)?"
        r"(?:今天|今日)?(?:的)?(?:计划|待办|任务)?"
        r"(?:了|啊|呀|呢|嘛|吧|的)*[。！？!?]?",
        text,
    ):
        return True

    if not title or title not in text:
        return False
    remaining_text = text.replace(title, "", 1)
    explicit_roxy_domain = bool(
        re.search(
            r"(?:RoxyPlan|洛琪希|今日计划|今天计划|今日待办|今天待办|"
            r"待办清单|计划记录|本地记录|系统记录)",
            remaining_text,
        )
    )
    asserted_success = bool(
        _ASSERTED_CHANGE_MARKER.search(text)
        or re.search(r"(?:显示|出现在).{0,12}(?:今日|今天)(?:计划|待办)", text)
    )
    prior_time_marker = bool(re.search(r"(?:刚才|刚刚|之前|上一轮|上次)", text))
    if explicit_roxy_domain and asserted_success and (
        not contains_question or prior_time_marker
    ):
        return True

    # A direct question to the assistant about the immediately preceding
    # operation can use the verified fact even when the user only says “计划”.
    return bool(
        contains_question
        and prior_time_marker
        and re.search(r"(?:你|洛琪希|Roxy)", text, re.IGNORECASE)
        and re.search(
            rf"{operation}(?:到|进)?(?:今天|今日)?(?:的)?"
            r"(?:计划|任务|待办|清单)(?:了)?(?:吗|么)?[？?]?$",
            remaining_text,
        )
    )


def _user_disputes_prior_success(
    user_text: str,
    prior: Mapping[str, str],
) -> bool:
    """Detect a reported UI/state mismatch without deciding which side is current."""
    text = re.sub(r"\s+", "", str(user_text or ""))
    title = re.sub(r"\s+", "", str(prior.get("title", "") or ""))
    if not text or not title or _NEGATED_APP_INTENT.search(text):
        return False
    explicit_app_scope = bool(
        re.search(
            r"(?:RoxyPlan|洛琪希|今日计划|今天计划|计划记录|本地记录|"
            r"系统记录|这个计划|该计划|待完成|进行中|已取消|已删除)",
            text.replace(title, "", 1),
        )
    )
    references_target = title in text or bool(
        re.search(r"(?:看到|看见|显示).{0,8}(?:它|这个|那个|该项|这个计划)", text)
    )
    if not explicit_app_scope or not references_target:
        return False
    kind = str(prior.get("kind", ""))
    denial_patterns = {
        "plan_added": (
            r"(?:没有|没|还没|未|并未|尚未).{0,8}(?:添加|加入|加进|放进|"
            r"显示|出现)|(?:不在|没在|未在).{0,8}(?:计划|待办|清单)"
        ),
        "plan_completed": (
            r"(?:没有|没|还没|未|并未|尚未).{0,6}(?:完成|做完|标记)|"
            r"(?:还是|仍是|依然是).{0,4}待完成"
        ),
        "plan_deleted": (
            r"(?:没有|没|还没|未|并未|尚未).{0,6}(?:删除|删掉)|"
            r"(?:还在|仍在|依然在|继续存在)"
        ),
        "plan_updated": r"(?:没有|没|未|并未|尚未).{0,6}(?:更新|修改)|(?:还是|仍是).{0,6}(?:原来|之前)",
        "plan_rescheduled": r"(?:没有|没|未|并未|尚未).{0,6}(?:改期|调整|改到)|(?:时间|日期).{0,6}(?:没变|未变)",
        "plan_reopened": r"(?:没有|没|未|并未|尚未).{0,6}(?:重新打开|恢复)|(?:还是|仍是).{0,4}(?:关闭|完成)",
        "plan_cancelled": r"(?:没有|没|未|并未|尚未).{0,6}取消|(?:还是|仍是).{0,4}(?:有效|待完成)",
    }
    pattern = denial_patterns.get(kind, "")
    return bool(pattern and re.search(pattern, text))


def _prior_success_discrepancy_message(prior: Mapping[str, str]) -> str:
    title = str(prior.get("title", "") or "").strip()
    return (
        f"刚才系统返回“{title}”处理成功，但你现在看到的状态与它不一致。"
        "这一轮我还没有重新读取记录，所以不能断言当前状态。"
        "你可以说：“查看今日计划”，我再按实际记录核对。"
    )


def _reply_denies_prior_state(message: str, prior: Mapping[str, str]) -> bool:
    """Recognize status-word contradictions that omit the operation verb."""
    text = re.sub(r"\s+", "", str(message or ""))
    title = re.sub(r"\s+", "", str(prior.get("title", "") or ""))
    if not text or not title or title not in text:
        return False
    if re.search(r"(?:吗|么|是否)[？?]?|[？?]", text):
        return False
    kind = str(prior.get("kind", ""))
    patterns = {
        "plan_added": (
            r"(?:不在|没在|未在).{0,10}(?:今日|今天)(?:计划|待办)|"
            r"(?:没有|没|未|并未).{0,6}(?:显示|出现).{0,10}"
            r"(?:今日|今天)(?:计划|待办)"
        ),
        "plan_completed": r"(?:还是|仍是|依然是).{0,4}待完成|(?:状态)?(?:未完成|没完成)",
        "plan_deleted": r"(?:其实|实际|目前|现在)?.{0,8}(?:还在|仍在|依然在|继续存在)",
        "plan_updated": r"(?:还是|仍是).{0,6}(?:原来|之前)|(?:没有|没|未).{0,4}变化",
        "plan_rescheduled": r"(?:时间|日期).{0,6}(?:没变|未变)|(?:还是|仍是).{0,6}(?:原时间|原日期)",
        "plan_reopened": r"(?:还是|仍是).{0,4}(?:关闭|完成)",
        "plan_cancelled": r"(?:还是|仍是).{0,4}(?:有效|待完成)|(?:没有|没|未).{0,4}取消",
    }
    pattern = patterns.get(kind, "")
    return bool(pattern and re.search(pattern, text))


def _prior_success_acknowledgement(prior: Mapping[str, str]) -> str:
    title = str(prior.get("title", ""))
    kind = str(prior.get("kind", ""))
    action = {
        "plan_added": "加入今日计划",
        "plan_completed": "标记完成",
        "plan_deleted": "删除",
        "plan_updated": "更新",
        "plan_rescheduled": "调整时间",
        "plan_reopened": "重新打开",
        "plan_cancelled": "取消",
    }.get(kind, "处理")
    return (
        f"对，你看到的是对的：刚才已经成功把“{title}”{action}了。"
        "这轮聊天没有改变它。"
    )


def _denies_prior_success(
    message: str,
    prior: Mapping[str, str],
    *,
    user_text: str = "",
) -> bool:
    text = re.sub(r"\s+", "", str(message or ""))
    if not text:
        return False
    if _reply_denies_prior_state(message, prior):
        return True
    terms = _PRIOR_OPERATION_TERMS.get(str(prior.get("kind", "")), ())
    mentions_operation = any(term in text for term in terms)
    if not mentions_operation:
        return False
    operation = "(?:" + "|".join(re.escape(term) for term in terms) + ")"
    if re.search(
        r"(?:吗|么|是否|有没有|要不要|能否|可不可以)[？?]?|[？?]",
        text,
    ):
        return False
    # “不是说它没有加入” affirms the prior success; it is not a retraction.
    if re.search(
        rf"(?:不是说|并非说|不是指).{{0,30}}"
        rf"(?:没有|没|未|并未|尚未|还没).{{0,8}}{operation}",
        text,
    ):
        return False
    if re.search(
        rf"(?:没有必要|不用|无需).{{0,12}}(?:重新)?{operation}.{{0,30}}"
        r"(?:已经|已).{0,12}(?:在|存在|保留)",
        text,
    ):
        return False
    title = re.sub(r"\s+", "", str(prior.get("title", "") or ""))
    mentions_same_title = bool(title and title in text)
    remaining_text = text.replace(title, "", 1) if mentions_same_title else text
    prior_marker = bool(re.search(r"(?:刚才|之前|上一轮|上次)", text))
    app_scope = bool(
        re.search(
            r"(?:今日|今天)?(?:计划|任务|待办|清单)|"
            r"(?:本地|系统|计划)(?:记录|状态)",
            remaining_text,
        )
    )
    generic_execution_retraction = bool(
        prior_marker and re.search(r"(?:执行|操作|工具调用)", text)
    )
    pronominal_reference = bool(
        re.search(
            r"(?:它|这个|那个|这项|那项|该项|这条|那条|加进去|放进去)",
            text,
        )
    )
    elliptical_failure = bool(
        prior_marker
        and re.search(
            r"(?:添加|加入|加进|放进|完成|删除|更新|修改)"
            r".{0,6}(?:没有生效|没生效|失败|没成功)|"
            r"(?:计划|任务|待办|清单)(?:里|中).{0,8}(?:空|没有)",
            text,
        )
    )
    related_to_prior = bool(
        (mentions_same_title and app_scope)
        or generic_execution_retraction
        or (app_scope and pronominal_reference)
        or elliptical_failure
    )
    current_text = re.sub(r"\s+", "", str(user_text or ""))
    current_mentions_operation = any(term in current_text for term in terms)
    current_mentions_prior_title = bool(title and title in current_text)
    current_has_prior_reference = bool(
        re.search(
            r"(?:刚才|刚刚|之前|上一轮|上次|这个|那个|它)",
            current_text,
        )
    )
    if (
        current_mentions_operation
        and not current_mentions_prior_title
        and not current_has_prior_reference
        and not mentions_same_title
        and not generic_execution_retraction
    ):
        # A pronoun in the reply belongs to the fresh target introduced by the
        # current user, not automatically to the prior verified plan.
        related_to_prior = False
    if not related_to_prior:
        return False
    # A reply may truthfully say that the current turn changed nothing while
    # preserving the earlier success.  Do not mistake that for a retraction.
    if re.search(
        r"(?:仍然|依然)(?:有效|存在|保留)|(?:没有|不会)改变|不影响|"
        r"(?:之前|原来|刚才).{0,16}(?:还在|仍在|依然在|继续保留|仍然有效)",
        text,
    ):
        return False
    return bool(
        elliptical_failure
        or re.search(
            rf"(?:没有|没|未|并未|尚未|还没).{{0,8}}{operation}|"
            rf"{operation}.{{0,8}}(?:失败|没成功|未成功|没有生效|没生效)",
            text,
        )
        or
        re.search(
            r"(?:刚才|之前).{0,24}(?:更正|不准确|说错|没有真的执行|并未执行)",
            text,
        )
        or re.search(
            r"(?:其实|实际|事实上|目前|现在).{0,16}"
            r"(?:没有|没|还没有|尚未|并未).{0,24}"
            r"(?:执行|添加|加入|加进|完成|删除|记录|保存|更新|计划)",
            text,
        )
    )


def _strip_prior_success_denial(
    message: str,
    prior: Mapping[str, str],
    *,
    user_text: str = "",
) -> str:
    if not prior or not _denies_prior_success(
        message,
        prior,
        user_text=user_text,
    ):
        return message
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", str(message or ""))
        if item.strip()
    ]
    kept: List[str] = []
    for paragraph in paragraphs:
        if _denies_prior_success(paragraph, prior, user_text=user_text):
            break
        kept.append(paragraph)
    if kept:
        return "\n\n".join(kept)
    marker = re.search(
        r"(?:不过|但是|但)?(?:刚才|之前).{0,12}(?:更正|不准确|说错)",
        str(message or ""),
    )
    if marker and str(message or "")[: marker.start()].strip():
        return str(message or "")[: marker.start()].strip()
    return _prior_success_acknowledgement(prior)


class ResponseComposer:
    """Single authority for composing the final public response.

    ResponseOutcome is derived exclusively from status + ToolResult facts.
    No post-hoc text scanner may override a ToolResult-backed SUCCESS.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)

    # ── Public API ──────────────────────────────────────────────────────

    def compose(
        self,
        response: AgentResponse,
        *,
        user_text: str = "",
        schema_rejected: bool = False,
        verified_turn_context: Optional[Mapping[str, object]] = None,
    ) -> AgentResponse:
        self._attach_client_actions(response)
        results = list(response.tool_results)
        raw_message = str(response.message or "").strip()
        message = sanitize_public_reply(raw_message) if raw_message else ""

        if self.enabled:
            outcome = self._determine_outcome(response.status, results)
            message = self._compose_by_outcome(
                outcome,
                message,
                results,
                user_text=user_text,
                schema_rejected=schema_rejected,
                verified_turn_context=verified_turn_context,
            )

        if not message:
            message = "这次没有得到可显示的结果，请稍后再试。"
        response.message = message
        get_development_log().event(
            None, "reply_composed", changed=message != raw_message,
            raw_model_response_hash=hashlib.sha256(raw_message.encode("utf-8")).hexdigest(),
            response_hash=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            status=response.status,
        )
        return response

    def from_resolution(
        self,
        status: str,
        safe_prompt: str,
        *,
        conversation_id: str = "",
        request_id: str = "",
    ) -> AgentResponse:
        """Create a CLARIFY/FAILURE response from business-resolver status.

        The returned response is NOT composed yet — the caller must still
        route it through :meth:`compose` for the single final-reply path.
        """
        mapped = (
            "clarification"
            if status
            in {
                "missing",
                "ambiguous",
                "clarification_required",
                "confirmation_required",
            }
            else "failed"
        )
        message = sanitize_public_reply(safe_prompt)
        return AgentResponse(
            mapped,
            message or "我还不能安全确定要执行的内容，请再说明一下。",
            request_id=request_id or None,
            conversation_id=conversation_id,
        )

    # ── Outcome determination ───────────────────────────────────────────

    @staticmethod
    def _determine_outcome(
        status: str,
        results: List[ToolResult],
    ) -> ResponseOutcome:
        if status == "clarification":
            return ResponseOutcome.CLARIFY
        if any(r.success for r in results):
            return ResponseOutcome.SUCCESS
        if results and not any(r.success for r in results):
            return ResponseOutcome.FAILURE
        if status in {"failed", "error"}:
            return ResponseOutcome.FAILURE
        # "completed" / "partial_success" without any ToolResult is a
        # deterministic system message (duplicate detection, context
        # suppression, etc.).  Trust it — the LLM only ever produces
        # status="chat" for unreviewed prose.
        if status in {"completed", "partial_success"}:
            return ResponseOutcome.SUCCESS
        return ResponseOutcome.CHAT

    # ── Per-outcome composition ─────────────────────────────────────────

    def _compose_by_outcome(
        self,
        outcome: ResponseOutcome,
        message: str,
        results: List[ToolResult],
        *,
        user_text: str = "",
        schema_rejected: bool = False,
        verified_turn_context: Optional[Mapping[str, object]] = None,
    ) -> str:
        if outcome == ResponseOutcome.SUCCESS:
            return self._compose_success(
                message,
                results,
                user_text,
                verified_turn_context=verified_turn_context,
            )
        if outcome == ResponseOutcome.CLARIFY:
            return message or "我还需要一点信息才能继续。"
        if outcome == ResponseOutcome.FAILURE:
            return message or "这次操作没有完成，现有数据没有改变。"
        # CHAT — no tools executed; LLM prose is allowed but structurally
        # impossible execution claims are blocked.
        return self._compose_chat(
            message,
            user_text,
            schema_rejected,
            verified_turn_context,
        )

    def _compose_success(
        self,
        message: str,
        results: List[ToolResult],
        user_text: str,
        *,
        verified_turn_context: Optional[Mapping[str, object]] = None,
    ) -> str:
        """Compose a SUCCESS message from verified ToolResult data only."""
        # Formal memory read → portrait presentation
        formal_read = next(
            (
                r
                for r in results
                if r.success
                and r.tool
                in {
                    "list_memories",
                    "show_memory",
                    "search_memories",
                    "search_memory",
                }
            ),
            None,
        )
        if formal_read is not None:
            context = (
                verified_turn_context
                if isinstance(verified_turn_context, Mapping)
                else {}
            )
            preferences = context.get("response_preferences", {})
            preferences = preferences if isinstance(preferences, Mapping) else {}
            return self._formal_memory_message(
                formal_read,
                user_text=user_text,
                response_preferences=preferences,
            )

        # Formal memory save
        formal_save = next(
            (
                r
                for r in results
                if r.success and r.tool == "save_formal_memory"
            ),
            None,
        )
        if formal_save is not None:
            return self._formal_memory_save_message(formal_save)

        # A successful plan/action write is not evidence of a formal-memory
        # save.  Keep SUCCESS, but rebuild this explicit cross-domain assertion
        # from the real ToolResults rather than repeating unsupported prose.
        if results and _FORMAL_MEMORY_SAVE_ASSERTION.search(message):
            return self.compose_batch(results)

        # Structural rule: when ToolResults exist but none are writes,
        # the message MUST be built from the read data — arbitrary prose
        # (including LLM-generated claims of writes that didn't happen)
        # cannot be trusted.
        if results:
            has_write_success = any(
                r.success and r.operation_kind == "write" for r in results
            )
            if not has_write_success:
                return self._join_successes(results)

        # Generic placeholder → recompute from results
        if message in {"success", "completed", "added", "updated"} and results:
            message = ""

        if not message:
            if all(r.success for r in results):
                return self._join_successes(results)
            return self.compose_batch(results)

        return message

    @staticmethod
    def _compose_chat(
        message: str,
        user_text: str,
        schema_rejected: bool,
        verified_turn_context: Optional[Mapping[str, object]] = None,
    ) -> str:
        """CHAT messages pass through but structurally impossible claims are blocked."""
        if schema_rejected:
            get_development_log().event(
                None, "reply_guard_applied", rule_code="schema_rejected",
            )
            return (
                "抱歉，这次没有成功解析你的请求。"
                "你可以用更简单的方式再说一次，我再试试。"
            )
        verified = (
            verified_turn_context
            if isinstance(verified_turn_context, Mapping)
            else {}
        )
        if _is_unbound_assistant_plan_add_request(user_text):
            return (
                "刚才的普通聊天回复没有建立可执行的计划建议列表，这次没有添加计划。"
                "你可以直接说“今天加入三项计划：事项一、事项二、事项三”；"
                "或者先说“给我几项今日计划建议”，再按编号选择。"
            )
        if _is_memory_delete_help_query(user_text):
            return (
                "聊天中只支持精确删除一条正式长期记忆：先查看记忆原文，"
                "再发送“忘记：完整记忆正文”；我会展示真实目标，等你回复“确认”后才删除。"
                "也可以打开“记忆”面板选择记录删除。"
            )
        if _is_plan_delete_help_query(user_text):
            return (
                "删除计划时，先说“查看今天计划”，再说“删除第2条”或给出完整计划标题；"
                "我会展示真实目标，等你回复“确认”后才删除。"
                "当前聊天不支持一次删除全部计划，需要逐条处理。"
            )
        reminder_action = reminder_control_action(user_text)
        if reminder_action:
            get_development_log().event(
                None,
                "reply_guard_applied",
                rule_code="reminder_control_without_tool",
            )
            expected = "先别提醒我" if reminder_action == "pause" else "恢复提醒"
            return (
                "这次没有成功更改提醒状态，所以我不能说已经完成。"
                f"请再说一次“{expected}”；只有实际执行成功后我才会确认状态已改变。"
            )
        if is_explicit_pet_sleep_request(user_text):
            get_development_log().event(
                None,
                "reply_guard_applied",
                rule_code="pet_sleep_without_tool",
            )
            return (
                "这次没有成功让桌宠进入睡眠，所以我不能说已经休息。"
                "你可以再说一次“洛琪希，你休息一下”。"
            )
        if _UNVERIFIED_MEMORY_ABSENCE.search(message):
            get_development_log().event(
                None,
                "reply_guard_applied",
                rule_code="memory_absence_without_read",
            )
            return (
                "这一轮没有实际读取正式长期记忆，所以我不能判断它是否为空。"
                "如果要核验，请说“查看长期记忆”；我只会根据真实读取结果回答。"
            )
        prior_memory_acknowledgement = _prior_memory_save_acknowledgement(user_text, verified)
        if prior_memory_acknowledgement:
            return prior_memory_acknowledgement
        if _MEMORY_SAVE_ASSERTION.search(message):
            get_development_log().event(
                None, "reply_guard_applied", rule_code="memory_save_without_tool",
            )
            return (
                "这轮还没有保存长期记忆，但我可以在当前对话中按你的偏好回应。"
                "若要长期保存，请明确说“记住：……”。"
            )
        if DEFAULT_CAPABILITY_REGISTRY.is_user_help_query(user_text):
            # Capability explanations legitimately mention future operations
            # and confirmation steps. They are neither an execution claim nor
            # a pending transaction. Impossible past-tense success is still
            # blocked before returning the model's grounded explanation.
            if _FALSE_EXECUTION_CLAIM.search(message):
                return (
                    "我可以陪你聊天，也能管理今日计划、记录计划外行动、"
                    "查看或保存复盘、读取和明确保存正式长期记忆、查询旧会话，"
                    "以及控制提醒和已有桌宠动作。你可以直接问我“某个功能怎么操作”，"
                    "我会按当前真正支持的步骤告诉你；询问方法本身不会执行操作。"
                )
            return message
        has_pending = _has_matching_confirmation_pending(message, verified)
        if _requests_mutation_confirmation(message) and not has_pending:
            get_development_log().event(
                None, "reply_guard_applied", rule_code="confirmation_without_pending",
            )
            return (
                "这次没有建立可执行的确认操作，也没有修改记录。"
                "不需要继续回复确认；这项操作当前不能在这里安全执行。"
            )
        prior = _verified_prior_operation(verified)
        if prior and _user_disputes_prior_success(user_text, prior):
            return _prior_success_discrepancy_message(prior)
        if prior and _user_references_prior_success(user_text, prior):
            return _prior_success_acknowledgement(prior)
        if prior and _denies_prior_success(
            message,
            prior,
            user_text=user_text,
        ):
            message = _strip_prior_success_denial(
                message,
                prior,
                user_text=user_text,
            )
            if message == _prior_success_acknowledgement(prior):
                return message
        execution = verified.get("execution", {})
        execution = execution if isinstance(execution, Mapping) else {}
        recorded = verified.get("recorded_plan_state", {})
        recorded = recorded if isinstance(recorded, Mapping) else {}
        pending_titles = recorded.get("pending_titles", [])
        pending_titles = (
            [str(item).strip() for item in pending_titles if str(item).strip()]
            if isinstance(pending_titles, list)
            else []
        )
        if (
            bool(verified.get("user_reported_plan_progress"))
            and not bool(execution.get("performed"))
            and pending_titles
            and _UNVERIFIED_RECORDED_PLAN_COMPLETION.search(message)
        ):
            pending_text = "、".join(pending_titles[:3])
            get_development_log().event(None, "reply_guard_applied", rule_code="completion_without_verified_write")
            return (
                "我听到你说这些已经做完了，不过这轮还没有更新计划记录。"
                f"目前仍显示待完成：{pending_text}。"
                "如果你希望同步记录，请告诉我需要标记哪些计划。"
            )
        if (
            bool(verified.get("user_reported_plan_progress"))
            and not bool(execution.get("performed"))
            and pending_titles
            and _ACKNOWLEDGES_UNCHANGED_RECORDED_STATE.search(message)
        ):
            return message
        # Only unambiguous completed-operation claims are corrected here. Broad
        # phrases such as "陪你完成目标" remain ordinary chat and must not turn
        # into a tool-failure response.
        ambiguous_deictic = bool(
            _AMBIGUOUS_DEICTIC_EXECUTION_CLAIM.search(message)
        )
        ambiguous_deictic_requires_guard = _ambiguous_deictic_claim_requires_guard(
            message,
            user_text,
            verified,
        )
        semantic_plan_claim = _claims_unverified_plan_change(message)
        if ambiguous_deictic and not ambiguous_deictic_requires_guard:
            # The generic phrase is grounded as a real-world acknowledgement;
            # a noun such as “工作任务” must not make the broad plan parser
            # reinterpret it as an application write.
            semantic_plan_claim = False
        if (
            _FALSE_EXECUTION_CLAIM.search(message)
            or semantic_plan_claim
            or ambiguous_deictic_requires_guard
            or (_claims_committed_app_write(message) and not has_pending)
        ):
            get_development_log().event(None, "reply_guard_applied", rule_code="false_execution_claim")
            return "我刚才的表达不够准确。我们可以继续聊聊你真正想推进的内容。"
        if _FALSE_DATA_ABSENCE.search(message):
            return "这个问题需要先读取实际记录，才能准确回答。"
        return message

    # ── Batch / partial ─────────────────────────────────────────────────

    def compose_batch(
        self,
        results: Iterable[ToolResult],
        *,
        skipped_count: int = 0,
        base_message: str = "",
    ) -> str:
        items = list(results)
        succeeded = [r for r in items if r.success]
        failed = [r for r in items if not r.success]
        if not failed and not skipped_count:
            # The sanitizer intentionally gives empty input a public fallback;
            # an absent optional base message must instead use verified results.
            return (
                sanitize_public_reply(base_message) if base_message else ""
            ) or self._join_successes(succeeded)
        parts: List[str] = []
        for r in succeeded:
            parts.append(self._result_line(r, True))
        for r in failed:
            parts.append(self._result_line(r, False))
        if skipped_count:
            parts.append(f"另有 {skipped_count} 项因依赖未完成而跳过。")
        return (
            "\n".join(p for p in parts if p) or "本轮操作没有完成。"
        )

    # ── Formal memory presentation ──────────────────────────────────────

    @classmethod
    def _formal_memory_message(
        cls,
        result: ToolResult,
        *,
        user_text: str = "",
        response_preferences: Optional[Mapping[str, object]] = None,
    ) -> str:
        natural_answer = str(result.data.get("natural_answer", "") or "").strip()
        if natural_answer:
            return sanitize_public_reply(natural_answer)
        memory_read = result.data.get("memory_read", {})
        memory_read = memory_read if isinstance(memory_read, Mapping) else {}
        request = memory_read.get("request", {})
        request = request if isinstance(request, Mapping) else {}
        query_mode = str(request.get("query_mode", "overview") or "overview")
        attribute = str(request.get("attribute", "") or "")
        if query_mode != "overview" or attribute:
            raw_facts = memory_read.get("facts", [])
            facts = (
                [dict(item) for item in raw_facts if isinstance(item, Mapping)]
                if isinstance(raw_facts, list)
                else []
            )
            return cls._typed_memory_message(request, facts)

        memories = result.data.get("memories", [])
        memories = memories if isinstance(memories, list) else []
        visible = [item for item in memories if isinstance(item, dict)]
        facts = cls._select_memory_facts(visible)
        if not facts:
            return (
                '我现在还没有记住多少关于你的长期信息。'
                '如果你愿意，可以直接告诉我“请记住：……”。'
            )

        preferences = (
            response_preferences
            if isinstance(response_preferences, Mapping)
            else {}
        )
        if bool(preferences.get("avoid_fixed_memory_format")):
            return cls._concise_memory_message(facts, user_text=user_text)

        reflective = cls._is_memory_understanding_follow_up(user_text)
        paragraphs = cls._memory_portrait_paragraphs(facts, reflective=reflective)
        if reflective:
            paragraphs.insert(
                0,
                "明白，刚才我说得像在念一份档案。你想听的是我眼中的你。",
            )
        return "\n\n".join(paragraphs[:4])

    @classmethod
    def _typed_memory_message(
        cls,
        request: Mapping[str, object],
        facts: List[Dict[str, object]],
    ) -> str:
        """Render one typed question from verified facts, without a profile dump."""

        mode = str(request.get("query_mode", "attribute") or "attribute")
        attribute = str(request.get("attribute", "") or "")
        topic = str(request.get("topic", "") or "")
        values = []
        for item in facts:
            value = str(item.get("value") or item.get("content") or "").strip()
            if value and value not in values:
                values.append(value)

        if not values:
            subject = {
                "preferred_name": "你的称呼",
                "preference": "这项偏好",
                "habit": "这项习惯",
                "goal": "这项目标",
                "project": "这个项目",
                "current_state": "这项当前信息",
                "fact": "这项信息",
            }.get(attribute, "相关长期信息")
            return f"我查了正式长期记忆，目前没有找到{subject}。"

        if mode == "existence":
            return "我在正式长期记忆里找到了相关记录：" + cls._join_memory_values(values) + "。"

        if mode == "provenance":
            first = facts[0]
            authority = str(first.get("authority") or first.get("source") or "formal_memory")
            source_text = "兼容用户资料" if authority == "legacy_profile" else "正式长期记忆"
            updated = str(first.get("updated_at") or first.get("created_at") or "").strip()
            date_text = updated[:10] if len(updated) >= 10 else ""
            suffix = f"，记录日期是{date_text}" if date_text else ""
            return f"我是从{source_text}中读到的：{values[0]}{suffix}。"

        if attribute == "preferred_name":
            return f"你希望我叫你{values[0]}。"
        joined = cls._join_memory_values(values)
        if attribute == "preference":
            if topic == "food":
                return f"我记得你喜欢吃{joined}。"
            return f"我记得你的偏好包括{joined}。"
        if attribute == "habit":
            return f"我记得你的习惯包括{joined}。"
        if attribute == "goal":
            return f"我记得你的长期目标包括{joined}。"
        if attribute == "project":
            return f"我记得你在推进{joined}。"
        if attribute == "current_state":
            return f"我目前记得的相关信息是：{joined}。"
        if attribute == "fact":
            return f"我记得：{joined}。"
        return f"我在正式长期记忆里找到：{joined}。"

    @staticmethod
    def _join_memory_values(values: List[str]) -> str:
        clean = [str(item).strip().rstrip("。.!！?？") for item in values if str(item).strip()]
        if not clean:
            return ""
        if len(clean) == 1:
            return clean[0]
        return "、".join(clean[:-1]) + "和" + clean[-1]

    @classmethod
    def _concise_memory_message(
        cls,
        facts: List[Dict[str, object]],
        *,
        user_text: str = "",
    ) -> str:
        selected = [
            str(item.get("content", "") or "").strip()
            for item in facts[:4]
            if str(item.get("content", "") or "").strip()
        ]
        if not selected:
            return "我记得一些关于你的事，不过这次没有找到适合直接说出的完整内容。"
        compact = re.sub(r"\s+", "", str(user_text or ""))
        if "了解" in compact or "认识" in compact:
            prefix = "目前我最先想到的是："
        elif "知道" in compact:
            prefix = "我现在知道的是："
        else:
            prefix = "我还记得："
        ending = "你想问哪一块，我再顺着说。" if len(facts) > len(selected) else ""
        return prefix + cls._join_fact_sentences(selected) + ending

    @classmethod
    def _select_memory_facts(
        cls,
        memories: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """Build a presentation-only fact view without mutating ToolResult data."""
        selected: List[Dict[str, object]] = []
        for item in memories:
            raw = str(item.get("content", "") or "").strip()
            if not raw or cls._is_contextless_memory_fragment(raw):
                continue
            content = cls._naturalize_memory_fact(raw)
            if not content:
                continue
            fact = {
                "raw": raw,
                "content": content,
                "category": str(item.get("category", "other") or "other"),
                "importance": cls._safe_importance(item.get("importance", 3)),
                "created_at": str(item.get("created_at", "") or ""),
            }
            duplicate_index = next(
                (
                    index
                    for index, existing in enumerate(selected)
                    if cls._same_presented_fact(existing, fact)
                ),
                None,
            )
            if duplicate_index is None:
                selected.append(fact)
                continue
            existing = selected[duplicate_index]
            if len(str(fact["content"])) > len(str(existing["content"])):
                if cls._is_goal_fact(existing) or cls._is_goal_fact(fact):
                    fact["category"] = "goal"
                selected[duplicate_index] = fact

        selected.sort(key=cls._memory_fact_sort_key)
        return selected

    @classmethod
    def _memory_portrait_paragraphs(
        cls,
        facts: List[Dict[str, object]],
        *,
        reflective: bool,
    ) -> List[str]:
        consumed: set[int] = set()
        goals: List[str] = []
        learning: List[str] = []
        health: List[str] = []
        preferences: List[str] = []
        other: List[str] = []
        morning_learning = False

        for index, fact in enumerate(facts):
            raw = str(fact.get("raw", ""))
            content = str(fact.get("content", ""))
            category = str(fact.get("category", "other"))
            if cls._is_goal_fact(fact):
                goals.append(content)
                consumed.add(index)
                continue
            if "早上" in raw and "学习" in raw:
                morning_learning = True
                consumed.add(index)
                continue
            if category in {"learning", "project"} or any(
                term in raw for term in ("机器学习", "随机森林", "特征工程")
            ):
                learning.append(content)
                consumed.add(index)
                continue
            if category in {"health", "health_lifestyle"}:
                health.append(content)
                consumed.add(index)
                continue
            if category in {
                "preference",
                "user_preference",
                "habit",
                "stable_habit",
            }:
                preferences.append(content)
                consumed.add(index)

        for index, fact in enumerate(facts):
            if index not in consumed:
                other.append(str(fact.get("content", "")))

        paragraphs: List[str] = []
        if goals:
            prefix = "在我看来，" if reflective else "我记得，"
            paragraphs.append(prefix + cls._join_fact_sentences(goals))

        learning_parts = list(learning)
        if morning_learning:
            learning_parts.append("你更适合在安静的早晨学习，那时效率会更高")
        if learning_parts:
            prefix = "我也留意到，" if not paragraphs else ""
            paragraphs.append(prefix + cls._join_fact_sentences(learning_parts))

        personal_parts = list(health)
        personal_parts.extend(preferences)
        personal_parts.extend(other)
        if personal_parts:
            prefix = "我还记得，" if not paragraphs else ""
            paragraphs.append(prefix + cls._join_fact_sentences(personal_parts))

        if not paragraphs:
            paragraphs.append(
                "我记得，"
                + cls._join_fact_sentences(
                    [str(fact.get("content", "")) for fact in facts]
                )
            )

        if not reflective and len(facts) >= 3:
            if goals and (learning or morning_learning):
                paragraphs.append(
                    "从这些事情里，我觉得你对想完成的事很认真，也在努力把目标落到具体的学习和项目实践里。"
                    "这是我根据你告诉我的内容形成的理解。"
                )
            else:
                paragraphs.append(
                    "从这些事情里，我觉得你愿意认真观察自己的状态，也在寻找适合自己的节奏。"
                    "这是我根据你告诉我的内容形成的理解。"
                )
        return paragraphs

    @classmethod
    def _naturalize_memory_fact(cls, content: str) -> str:
        text = cls._memory_from_roxy_perspective(content)
        text = re.sub(
            r"^\s*\d{4}年\d{1,2}月\d{1,2}日[，,、\s]*",
            "",
            text,
        )
        duration_change = re.fullmatch(
            r"(?:(上午|下午|晚上))?学习([^，,。；]+)[，,]"
            r"计划时长[^（(。；]+[（(]后改为([^）)]+)[）)]。?",
            text,
        )
        if duration_change:
            period, subject, duration = duration_change.groups()
            return (
                f"你曾安排过{period or ''}学习{subject}，"
                f"后来把时长调整为{duration}"
            )
        text = re.sub(r"的两个核心概念（[^）]+）", "的核心思路", text)
        text = text.replace("你已理解", "你已经理解")
        text = text.replace("；计划把", "，还计划把")
        text = text.replace("加入学习计划", "纳入学习")
        return text.strip().rstrip("。！!?？")

    @staticmethod
    def _is_contextless_memory_fragment(content: str) -> bool:
        text = re.sub(r"[\s　-〿＀-￯‘’“”«»]", "", str(content))
        if len(text) < 2:
            return True
        return bool(
            re.fullmatch(
                r"(?:第?[一二三四五六七八九十百千万\d]+个|"
                r"这个|那个|这些|那些|它|这件事|那件事|"
                r"刚才(?:的)?(?:这个|那个|这些|那些)?|"
                r"(?:你|我)(?:刚才|上面)?说的(?:这个|那个|这些|那些|长期目标)?)",
                text,
            )
        )

    @classmethod
    def _same_presented_fact(
        cls,
        first: Dict[str, object],
        second: Dict[str, object],
    ) -> bool:
        first_key = cls._presentation_fact_key(first.get("content", ""))
        second_key = cls._presentation_fact_key(second.get("content", ""))
        if not first_key or not second_key:
            return False
        if first_key == second_key:
            return True
        if min(len(first_key), len(second_key)) >= 8 and (
            first_key in second_key or second_key in first_key
        ):
            return True
        first_goal = cls._goal_subject(str(first.get("content", "")))
        second_goal = cls._goal_subject(str(second.get("content", "")))
        return bool(first_goal and first_goal == second_goal)

    @staticmethod
    def _presentation_fact_key(content: object) -> str:
        text = str(content or "").lower()
        # Strip punctuation, whitespace, and common Chinese function words
        # to produce a comparable content key.
        text = re.sub(r"[\s　-〿＀-￯‘’“”«»（）()]", "", text)
        text = re.sub(r"[的了着正在目前最近用户我你]", "", text)
        return text

    @staticmethod
    def _goal_subject(content: str) -> str:
        if "长期目标" not in content:
            return ""
        match = re.search(
            r"(?:完成|做|推进|开发)([^，。；：]{2,16}?(?:项目|计划))",
            content,
        )
        if not match:
            match = re.search(
                r"([^，。；：]{2,16}?(?:项目|计划))是[^，。；：]*长期目标",
                content,
            )
        if not match:
            return ""
        return re.sub(r"^(?:你|我)(?:正在)?", "", match.group(1))

    @classmethod
    def _is_goal_fact(cls, fact: Dict[str, object]) -> bool:
        return str(fact.get("category", "")) in {"goal", "long_term_goal"} or bool(
            cls._goal_subject(str(fact.get("content", "")))
        )

    @classmethod
    def _memory_fact_sort_key(cls, fact: Dict[str, object]):
        category = str(fact.get("category", "other"))
        raw = str(fact.get("raw", ""))
        if cls._is_goal_fact(fact):
            priority = 0
        elif category == "learning" and not re.match(r"^\d{4}年", raw):
            priority = 1
        elif "早上" in raw and "学习" in raw:
            priority = 2
        elif category in {"health", "health_lifestyle"}:
            priority = 3
        elif category in {
            "preference",
            "user_preference",
            "habit",
            "stable_habit",
        }:
            priority = 4
        else:
            priority = 5
        return (
            priority,
            -int(fact.get("importance", 3)),
            str(fact.get("created_at", "")),
        )

    @staticmethod
    def _safe_importance(value: object) -> int:
        try:
            return max(1, min(int(value), 5))
        except (TypeError, ValueError):
            return 3

    @classmethod
    def _join_fact_sentences(cls, contents: List[str]) -> str:
        return "".join(
            cls._sentence(content) for content in contents if content
        )

    @staticmethod
    def _is_memory_understanding_follow_up(user_text: str) -> bool:
        text = re.sub(r"\s+", "", str(user_text or ""))
        return bool(
            ("我问你" in text and "表达" in text)
            or (
                "刚才" in text
                and any(term in text for term in ("档案", "了解", "表达"))
            )
        )

    @classmethod
    def _formal_memory_save_message(cls, result: ToolResult) -> str:
        operation = result.data.get("memory_operation", {})
        operation = operation if isinstance(operation, dict) else {}
        details = operation.get("data", {})
        details = details if isinstance(details, dict) else {}
        content = cls._memory_from_roxy_perspective(
            details.get("final_content", "")
        )
        if str(details.get("operation", "")) == "duplicate":
            return (
                f"这件事我已经记得了：{cls._sentence(content)}"
                if content
                else "这件事我已经记得了。"
            )
        return (
            f"好，我记住了：{cls._sentence(content)}"
            if content
            else "好，我已经保存了这条长期记忆。"
        )

    @staticmethod
    def _memory_from_roxy_perspective(content: object) -> str:
        text = str(content or "").strip()
        if not text:
            return ""
        text = text.replace("用户的", "__ROXY_USER__的").replace(
            "用户", "__ROXY_USER__"
        )
        replacements = {
            "我们": "__ROXY_SHARED_WE__",
            "你们": "__ROXY_USER_PLURAL__",
        }
        for original, placeholder in replacements.items():
            text = text.replace(original, placeholder)
        text = text.replace("我", "__ROXY_USER__")
        text = text.replace("你", "__ROXY_ASSISTANT__")
        return (
            text.replace("__ROXY_USER__", "你")
            .replace("__ROXY_ASSISTANT__", "我")
            .replace("__ROXY_SHARED_WE__", "我们")
            .replace("__ROXY_USER_PLURAL__", "你们")
        )

    @staticmethod
    def _sentence(content: str) -> str:
        text = str(content or "").strip()
        return (
            text if text.endswith(("。", "！", "？", "!", "?")) else text + "。"
        )

    def _join_successes(self, results: List[ToolResult]) -> str:
        add_results = [
            result
            for result in results
            if result.success and result.tool == "add_plan"
        ]
        complete_results = [
            result
            for result in results
            if result.success and result.tool == "complete_plan"
        ]
        lines: List[str] = []
        if len(add_results) > 1:
            titles = []
            for result in add_results:
                task = result.data.get("task", {})
                if not isinstance(task, dict):
                    continue
                title = str(task.get("title", "")).strip().rstrip("。.!！？?")
                if title and title not in titles:
                    titles.append(title)
            detail = f"：{'、'.join(titles)}" if titles else ""
            lines.append(f"已加入 {len(add_results)} 项今日计划{detail}。")
        if len(complete_results) > 1:
            titles = []
            for result in complete_results:
                task = result.data.get("task", {})
                if not isinstance(task, dict):
                    continue
                title = str(task.get("title", "")).strip().rstrip("。.!！？?")
                if title and title not in titles:
                    titles.append(title)
            detail = f"：{'、'.join(titles)}" if titles else ""
            lines.append(f"已完成 {len(complete_results)} 项计划{detail}。")
        for result in results:
            if len(add_results) > 1 and result in add_results:
                continue
            if len(complete_results) > 1 and result in complete_results:
                continue
            lines.append(self._result_line(result, True))
        return "\n".join(p for p in lines if p) or "操作已经完成。"

    @staticmethod
    def _plan_date_label(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return "今天"
        try:
            target = datetime.fromisoformat(text).date()
        except ValueError:
            return text
        today = datetime.now().date()
        if target == today:
            return "今天"
        if target == today - timedelta(days=1):
            return "昨天"
        if target == today + timedelta(days=1):
            return "明天"
        return target.isoformat()

    @staticmethod
    def _result_line(result: ToolResult, success: bool) -> str:
        raw_display = str(result.display_message or result.message or "").strip()
        technical_markers = {
            "completed",
            "listed",
            "added",
            "updated",
            "deleted",
            "rejected",
            "failed",
            "error",
            "generated",
            "saved",
            "duplicates_found",
            "no_duplicates",
            "merged",
            "already_merged",
        }
        display = "" if raw_display in technical_markers else sanitize_public_reply(raw_display)
        operation = result.data.get("memory_operation", {})
        operation = operation if isinstance(operation, dict) else {}
        formal = operation.get("data", {})
        formal = formal if isinstance(formal, dict) else {}
        if success and result.tool == "save_formal_memory":
            formal_operation = str(formal.get("operation", ""))
            if formal_operation == "duplicate":
                return "这件事我已经记得了。"
            if formal_operation in {"created", "merged", "updated"}:
                return display or "好，我已经保存这条长期记忆。"
        candidate_id = operation.get("candidate_id")
        if success and candidate_id and result.tool == "accept_memory_candidate":
            return f"候选 {candidate_id} 已加入长期记忆。"
        if success and candidate_id and result.tool == "reject_memory_candidate":
            return f"候选 {candidate_id} 已忽略。"
        if display in technical_markers:
            display = ""
        if display:
            return display
        name = result.tool
        if success:
            if name == "show_plan":
                tasks = result.data.get("tasks", [])
                date_label = ResponseComposer._plan_date_label(
                    result.data.get("date", "")
                )
                if not isinstance(tasks, list) or not tasks:
                    return f"{date_label}还没有计划。"
                lines = [f"{date_label}的计划："]
                for index, item in enumerate(tasks, 1):
                    if not isinstance(item, dict):
                        continue
                    task_id = item.get("id") or item.get("uid") or index
                    title = (
                        str(item.get("title", "")).strip() or "未命名计划"
                    )
                    status = "已完成" if item.get("done") else "待完成"
                    details = " ".join(
                        value
                        for value in (
                            str(item.get("time_slot", "")).strip(),
                            (
                                f"{item.get('duration_minutes')}分钟"
                                if item.get("duration_minutes")
                                else ""
                            ),
                        )
                        if value
                    )
                    lines.append(
                        f"{task_id}. [{status}] {title}"
                        + (f"（{details}）" if details else "")
                    )
                return "\n".join(lines)
            if name == "show_action_log":
                records = result.data.get("records", [])
                if not isinstance(records, list) or not records:
                    return "今天还没有行动记录。"
                lines = ["今天的行动记录："]
                for index, item in enumerate(records, 1):
                    if not isinstance(item, dict):
                        continue
                    content = str(item.get("content", "")).strip()
                    if content:
                        lines.append(f"{index}. {content}")
                return "\n".join(lines) if len(lines) > 1 else "今天还没有行动记录。"
            if name == "generate_daily_review":
                review = result.data.get("review", {})
                if isinstance(review, dict):
                    natural_raw = str(review.get("natural_summary", "")).strip()
                    if natural_raw:
                        return sanitize_public_reply(natural_raw)
                    review_text = str(review.get("text", "")).strip()
                    if review_text:
                        return review_text
                return "今天暂无可总结的内容。"
            if name == "show_growth_log":
                entries = result.data.get("entries", [])
                natural_summary = str(
                    result.data.get("natural_summary", "") or ""
                ).strip()
                if natural_summary:
                    return sanitize_public_reply(natural_summary)
                if not isinstance(entries, list) or not entries:
                    return "成长日志还是空的。"
                statistics = result.data.get("statistics", {})
                statistics = statistics if isinstance(statistics, dict) else {}
                month = str(
                    statistics.get("month") or result.data.get("month") or ""
                ).strip()
                if statistics:
                    lines = [
                        f"{month or '本月'}成长日志：记录 {statistics.get('logged_days', len(entries))} 天，"
                        f"计划完成 {statistics.get('completed_plans', 0)}/{statistics.get('total_plans', 0)}，"
                        f"计划外行动 {statistics.get('action_count', 0)} 条。"
                    ]
                else:
                    lines = ["成长日志："]
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    review = entry.get("review", {})
                    review = review if isinstance(review, dict) else {}
                    lines.append(
                        f"- {entry.get('date', '')}：计划 {review.get('total', 0)} 件，"
                        f"完成 {review.get('done', 0)} 件，行动 {len(review.get('actions', []))} 条"
                    )
                return "\n".join(lines)
            if name == "inspect_plan_duplicates":
                groups = result.data.get("groups", [])
                if not isinstance(groups, list) or not groups:
                    return "今天的计划中没有发现需要合并的相近项。"
                lines = ["我发现这些相近计划："]
                for group_index, group in enumerate(groups, 1):
                    if not isinstance(group, dict):
                        continue
                    candidates = group.get("candidates", [])
                    lines.append(f"第{group_index}组：")
                    for item in candidates if isinstance(candidates, list) else []:
                        if isinstance(item, dict):
                            status = "已完成" if item.get("done") else "待完成"
                            lines.append(f"- [{status}] {item.get('title', '')}")
                lines.append("如果它们确实是同一件事，可以回复“合并计划”；我会先展示合并确认，不会直接修改。")
                return "\n".join(lines)
            if name == "merge_plan":
                task = result.data.get("task", {})
                task = task if isinstance(task, dict) else {}
                title = str(task.get("title", "")).strip() or "保留的计划"
                status = "已完成" if task.get("done") else "待完成"
                removed = result.data.get("removed_tasks", [])
                removed_count = len(removed) if isinstance(removed, list) else 0
                if removed_count:
                    return (
                        f"已把{removed_count + 1}条相近计划合并为“{title}”，"
                        f"当前状态为{status}。"
                    )
                return f"已把新增信息合并进“{title}”，当前状态为{status}。"
            if name == "show_recent_conversation":
                messages = result.data.get("messages", [])
                if not isinstance(messages, list) or not messages:
                    return "当前会话还没有更早的内容。"
                lines = ["当前会话最近聊过："]
                for item in messages:
                    if not isinstance(item, dict):
                        continue
                    speaker = "你" if item.get("role") == "user" else "Roxy"
                    lines.append(f"- {speaker}：{item.get('content', '')}")
                return "\n".join(lines)
            if name == "show_conversation_history":
                sessions = result.data.get("sessions", [])
                query = str(result.data.get("query", "")).strip()
                if not isinstance(sessions, list) or not sessions:
                    if query:
                        return f"没有找到与“{query[:40]}”匹配的旧会话。"
                    return "本地还没有可恢复的历史会话。"
                lines = [
                    (
                        f"找到与“{query[:40]}”相关的旧会话："
                        if query
                        else "我保存了这些本地会话："
                    )
                ]
                for index, item in enumerate(sessions, start=1):
                    if not isinstance(item, dict):
                        continue
                    source_date = str(item.get("source_date", "")).strip() or "日期未知"
                    title = str(item.get("title", "新对话")).strip() or "新对话"
                    evidence = str(
                        item.get("summary") or item.get("snippet") or ""
                    ).strip()
                    source = (
                        "本地会话摘要"
                        if item.get("source") == "local_conversation_summary"
                        else "本地会话片段"
                    )
                    lines.append(f"{index}. {source_date}｜{title}")
                    if evidence:
                        lines.append(f"   {evidence}（来源：{source}）")
                return "\n".join(lines)
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

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _attach_client_actions(response: AgentResponse) -> None:
        if response.client_actions:
            return
        actions: List[ClientAction] = []
        for result in response.tool_results:
            if not result.success:
                continue
            raw = result.data.get("client_action")
            if not isinstance(raw, dict):
                continue
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            actions.append(
                ClientAction(
                    name=str(raw.get("name", "")),
                    arguments=(
                        dict(raw.get("arguments", {}))
                        if isinstance(raw.get("arguments", {}), dict)
                        else {}
                    ),
                    expires_at=expires_at.isoformat(),
                    request_id=response.request_id,
                    conversation_id=response.conversation_id or "",
                    source=str(raw.get("source", "tool_registry")),
                )
            )
        response.client_actions = actions
