from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Sequence

from modules.chinese_entity_parser import ChineseEntityParser
from modules.contracts import IntentResult
from modules.memory_data_query_guard import MemoryDataQueryGuard
from modules.reference_resolver import ReferenceResolver


SUPPORTED_INTENTS = {
    "chat", "add_plan", "complete_plan", "delete_plan", "add_action_log",
    "update_plan", "reschedule_plan", "reopen_plan", "cancel_plan",
    "daily_review", "save_review", "add_memory_request", "memory_candidate",
    "show_plan", "show_action_log", "show_growth_log", "reminder_control",
    "show_memory", "search_memory", "archive_memory", "restore_memory",
    "delete_memory", "show_memory_candidates", "accept_memory_candidate",
    "accept_memory_candidates", "reject_memory_candidate",
    "reject_memory_candidates", "accept_all_memory_candidates",
    "show_memory_conflicts", "resolve_memory_conflict",
    "show_memory_audit", "show_archived_memories",
    "show_recent_conversation", "show_conversation_history",
    "multi_action", "dance", "sleep_pet", "wake_pet",
}

_FIXED_EXACT_COMMANDS = {
    "查看计划", "查看记录", "今日复盘", "复盘一下", "今天完成了什么",
    "保存今日复盘", "查看成长日志", "我的记忆", "查看长期记忆",
    "查看待确认记忆", "清空待确认记忆", "查看记忆冲突", "整理记忆",
    "把待确认的都确认", "确认全部待审核记忆", "查看已归档记忆",
}
_FIXED_PREFIXES = (
    "今日计划：", "今日计划:", "添加计划：", "添加计划:", "记录：", "记录:",
    "行动记录：", "行动记录:", "记住：", "记住:", "忘记：", "忘记:", "我完成了",
    "搜索记忆：", "搜索记忆:",
    "仍然添加：", "仍然添加:",
)

_FIXED_NUMBERED_COMMAND = re.compile(
    r"(?:完成计划|完成任务|删除计划|确认删除计划|重新打开计划|重开计划|恢复计划|"
    r"取消计划|确认记忆|保存记忆|确认候选|忽略记忆|忽略候选|删除候选记忆|归档记忆|恢复记忆|"
    r"删除记忆|确认删除记忆|使用新记忆|保留旧记忆|两条都保留记忆)\s*\d+"
)
_FIXED_PLAN_UPDATE = re.compile(r"(?:修改|更新)计划\s*\d+[：:].+")


class LLMIntentParser:
    """Optional, provider-neutral JSON parser. It never performs an action itself."""

    def __init__(self, chat_callable: Optional[Callable[[List[Dict[str, str]]], str]] = None):
        self.chat_callable = chat_callable

    def parse(
        self,
        text: str,
        context: Optional[Dict[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        if self.chat_callable is None:
            return None
        prompt = (
            "You classify one Chinese user message for a local desktop assistant. "
            "Return JSON only with intent, confidence, entities, needs_confirmation, "
            "warnings, clarification_question and candidate_actions. "
            "Allowed intents: " + ", ".join(sorted(SUPPORTED_INTENTS)) + ". "
            "Use chat when uncertain. Deletions, clears, overwrites and conflict resolution "
            "must set needs_confirmation true. A wish is not an instruction: ask a "
            "clarifying question or request confirmation before writing. Never invent an "
            "entity that is not in the message or supplied context. Do not answer the user."
            "\nRecent structured context: "
            + json.dumps(_safe_llm_context(context), ensure_ascii=False)
            + "\nUser message: "
            + text
        )
        try:
            raw = str(self.chat_callable([{"role": "system", "content": prompt}])).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
            data = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or str(data.get("intent", "")) not in SUPPORTED_INTENTS:
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        intent = str(data["intent"])
        entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
        if not _validate_llm_entities(intent, entities):
            return None
        candidate_actions = data.get("candidate_actions", [])
        if not isinstance(candidate_actions, list):
            candidate_actions = []
        safe_actions = []
        for item in candidate_actions[:5]:
            if not isinstance(item, dict):
                continue
            action_intent = str(item.get("intent", ""))
            action_entities = item.get("entities", {})
            if (
                action_intent not in SUPPORTED_INTENTS
                or not isinstance(action_entities, dict)
                or not _validate_llm_entities(action_intent, action_entities)
            ):
                continue
            safe_actions.append(
                {"intent": action_intent, "entities": dict(action_entities)}
            )
        candidate_actions = safe_actions
        needs_confirmation = bool(data.get("needs_confirmation", False))
        if intent in {
            "delete_plan", "delete_memory", "archive_memory", "restore_memory",
            "resolve_memory_conflict",
        }:
            needs_confirmation = True
        if (
            intent in {"add_plan", "add_action_log", "complete_plan", "update_plan", "reschedule_plan"}
            and re.search(r"(?:想|感觉|觉得|应该|也许|可能|有空|抽空)", text)
            and not re.search(r"(?:帮我|请|加入|添加|记录|标记|改成|改到|执行)", text)
        ):
            needs_confirmation = True
        return {
            "intent": intent,
            "confidence": max(0.0, min(confidence, 1.0)),
            "entities": entities,
            "needs_confirmation": needs_confirmation,
            "source": "llm",
            "warnings": [str(item)[:120] for item in data.get("warnings", [])[:5]]
            if isinstance(data.get("warnings", []), list)
            else [],
            "clarification_question": str(data.get("clarification_question", "")).strip()[:240] or None,
            "candidate_actions": candidate_actions,
        }


class IntentRouter:
    """Three-layer intent router with rule fallback and no UI or storage dependency."""

    def __init__(
        self,
        llm_parser: Optional[LLMIntentParser] = None,
        *,
        enable_llm: bool = False,
        entity_parser: Optional[ChineseEntityParser] = None,
        reference_resolver: Optional[ReferenceResolver] = None,
        memory_query_guard: Optional[MemoryDataQueryGuard] = None,
    ):
        self.llm_parser = llm_parser
        self.enable_llm = enable_llm
        self.entity_parser = entity_parser or ChineseEntityParser()
        self.reference_resolver = reference_resolver or ReferenceResolver()
        self.memory_query_guard = memory_query_guard or MemoryDataQueryGuard()

    def configure_llm(self, parser: Optional[LLMIntentParser], enabled: bool) -> None:
        self.llm_parser = parser
        self.enable_llm = bool(enabled)

    def route(
        self,
        user_text: str,
        context: Optional[Dict[str, object]] = None,
        *,
        allow_llm: bool = True,
    ) -> Dict[str, object]:
        text = _clean_text(user_text)
        print(f"[Intent] input_chars={len(text)}", flush=True)
        if not text:
            return self._fallback("empty")
        if is_fixed_command(text):
            fixed = self._route_fixed(text)
            return self._enrich_entities(
                text,
                fixed or self._result("chat", 1.0, source="fixed_command"),
                context or {},
            )

        result = self._route_rules(text, context or {})
        if result is not None:
            return self._enrich_entities(text, result, context or {})

        if allow_llm and self.enable_llm and self.llm_parser is not None:
            parsed = self.llm_parser.parse(text, context or {})
            if parsed is not None:
                print(f"[Intent] source: llm", flush=True)
                print(f"[Intent] matched: {parsed['intent']} confidence={parsed['confidence']:.2f}", flush=True)
                return self._enrich_entities(
                    text, self._with_legacy_slots(parsed), context or {}
                )
        return self._fallback("no_rule")

    def _route_rules(
        self,
        text: str,
        context: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        if re.search(r"(?:不要|别|不用).{0,8}(?:删除|归档|恢复|执行)", text):
            return None
        if re.search(r"(?:怎么|如何|怎样).{0,8}(?:删除|归档|恢复)", text):
            return None

        history_query = self._route_history_query(text)
        if history_query is not None:
            return self._matched(
                str(history_query["intent"]),
                **dict(history_query.get("entities", {})),
            )

        memory_query = self.memory_query_guard.route(text)
        if memory_query is not None:
            intent = str(memory_query.get("intent", "show_memory"))
            confidence = float(memory_query.get("confidence", 0.96))
            print("[Intent] source: memory_query_guard", flush=True)
            print(f"[Intent] matched: {intent} confidence={confidence:.2f}", flush=True)
            return self._result(
                intent,
                confidence,
                entities=dict(memory_query.get("entities", {})),
                needs_confirmation=False,
                source="memory_query_guard",
                clarification_question=memory_query.get("clarification_question"),
            )

        contextual_memory = self._route_contextual_memory_request(text, context)
        if contextual_memory is not None:
            return contextual_memory

        memory_review = self._route_memory_review(text, context)
        if memory_review is not None:
            return memory_review

        if re.search(r"(?:删除|清空|移除).*(?:所有|全部|全部的).*(?:长期)?记忆", text) or re.search(
            r"(?:删除|清空|移除)(?:所有|全部)(?:长期)?记忆", text
        ):
            return self._matched(
                "delete_memory",
                scope="all",
                needs_confirmation=True,
            )

        contextual_plan = self._route_contextual_plan(text, context)
        if contextual_plan is not None:
            return contextual_plan
        multi = re.fullmatch(
            r"(?:我)?(?:(.+?)(?:学完|做完|完成)|(?:学完|做完|完成)(.+?))了?"
            r"[，,、\s]*(?:顺便|再|然后)(?:帮我)?(?:记一下|记录一下)[：:\s]*(.+)",
            text,
        )
        if multi:
            return self._matched(
                "multi_action",
                completed_task=_clean_slot(multi.group(1) or multi.group(2)),
                action_log=_clean_slot(multi.group(3)),
            )
        if re.search(r"复盘.+(?:并|然后|顺便).*(?:保存|存下来)", text):
            return self._matched("multi_action", operation="review_and_save")
        if self._route_dance(text, context):
            return self._matched("dance")
        if _matches_any(text, ("进入睡眠", "睡一会儿", "去睡吧")):
            return self._matched("sleep_pet")
        if _matches_any(text, ("唤醒", "醒一醒", "起来吧")):
            return self._matched("wake_pet")
        if _matches_any(text, ("恢复提醒", "继续提醒我", "重新开启提醒")):
            return self._matched("reminder_control", action="resume")
        if _matches_any(text, ("先别提醒我", "暂停提醒", "等会儿再说", "晚点提醒我", "今天不想学了")):
            return self._matched("reminder_control", action="pause")

        memory_view = self._route_memory_view(text)
        if memory_view:
            return self._matched(memory_view["intent"], **memory_view.get("entities", {}))
        contextual_memory = self._route_contextual_memory_request(text, context)
        if contextual_memory is not None:
            return contextual_memory
        memory_request = self._route_memory(text)
        if memory_request:
            return self._matched("add_memory_request", content=memory_request)
        candidate = self._route_memory_candidate(text)
        if candidate:
            return self._matched("memory_candidate", content=text, category=candidate, source_text=text)

        if _matches_any(text, ("保存今天的复盘", "把这个复盘存下来", "记入成长日志")):
            return self._matched("save_review")
        if _matches_any(text, ("查看成长日志", "看看成长日志", "最近的成长日志")):
            return self._matched("show_growth_log")
        if _matches_any(text, ("查看记录", "看看行动记录", "今天记录了什么")):
            return self._matched("show_action_log")
        if _matches_any(text, ("我今天还有什么没做", "今天还有什么没做", "看看今天任务", "看看今天的任务", "我今天要做什么", "看看我的计划", "看看计划")):
            return self._matched("show_plan")

        action = self._route_explicit_action(text, context)
        if action:
            return self._matched("add_action_log", content=action)
        if re.fullmatch(r"今天推进不少[，,]?(?:帮我)?记一下", text):
            return self._matched(
                "add_action_log",
                clarification_question="你今天具体推进了什么？我会按实际发生的内容记录。",
            )
        plans = self._route_add_plan(text)
        if plans:
            vague = self._vague_plan_request(text, plans)
            if vague:
                return self._matched(
                    "add_plan",
                    tasks=plans,
                    needs_confirmation=vague == "confirmation",
                    clarification_question=(
                        "你想学什么内容，大概安排多久？"
                        if vague == "clarification"
                        else None
                    ),
                    candidate_actions=(
                        [{"intent": "add_plan", "entities": {"tasks": plans}}]
                        if vague == "confirmation"
                        else []
                    ),
                )
            return self._matched("add_plan", tasks=plans)
        deletion = self._route_delete_plan(text)
        if deletion:
            return self._matched("delete_plan", query=deletion, needs_confirmation=True)
        completed = self._route_complete_plan(text)
        if completed:
            return self._matched("complete_plan", query=completed)
        action = self._route_implicit_action(text)
        if action:
            return self._matched("add_action_log", content=action)
        if _matches_any(text, ("今天复盘", "复盘一下", "帮我看看今天完成了什么", "今天状态怎么样", "看看今天完成了什么")):
            return self._matched("daily_review")
        return None

    def _route_fixed(self, text: str) -> Optional[Dict[str, object]]:
        exact = {
            "查看计划": ("show_plan", {}),
            "查看记录": ("show_action_log", {}),
            "今日复盘": ("daily_review", {}),
            "复盘一下": ("daily_review", {}),
            "今天完成了什么": ("daily_review", {}),
            "保存今日复盘": ("save_review", {}),
            "查看成长日志": ("show_growth_log", {}),
            "我的记忆": ("show_memory", {}),
            "查看长期记忆": ("show_memory", {}),
            "查看待确认记忆": ("show_memory_candidates", {}),
            "查看待审核记忆": ("show_memory_candidates", {}),
            "把待确认的都确认": ("accept_all_memory_candidates", {}),
            "确认全部待审核记忆": ("accept_all_memory_candidates", {}),
            "查看记忆冲突": ("show_memory_conflicts", {}),
            "查看已归档记忆": ("show_archived_memories", {}),
        }
        if text in exact:
            intent, entities = exact[text]
            return self._result(intent, 1.0, entities=entities, source="fixed_command")
        prefix_map = {
            "今日计划：": "add_plan", "今日计划:": "add_plan",
            "添加计划：": "add_plan", "添加计划:": "add_plan",
            "记录：": "add_action_log", "记录:": "add_action_log",
            "行动记录：": "add_action_log", "行动记录:": "add_action_log",
            "记住：": "add_memory_request", "记住:": "add_memory_request",
            "搜索记忆：": "search_memory", "搜索记忆:": "search_memory",
            "仍然添加：": "add_plan_force", "仍然添加:": "add_plan_force",
        }
        for prefix, intent in prefix_map.items():
            if text.startswith(prefix):
                content = text[len(prefix):].strip()
                if intent == "add_plan_force":
                    return self._result(
                        "add_plan",
                        1.0,
                        entities={"tasks": [content], "allow_duplicate": True},
                        source="fixed_command",
                    )
                entities = {"tasks": [content]} if intent == "add_plan" else {
                    "content" if intent in {"add_action_log", "add_memory_request"} else "query": content
                }
                return self._result(intent, 1.0, entities=entities, source="fixed_command")
        match = re.fullmatch(r"(?:完成计划|完成任务)\s*(\d+)", text)
        if match:
            return self._result("complete_plan", 1.0, entities={"query": match.group(1)}, source="fixed_command")
        match = re.fullmatch(r"删除计划\s*(\d+)", text)
        if match:
            return self._result("delete_plan", 1.0, entities={"task_id": int(match.group(1))}, needs_confirmation=True, source="fixed_command")
        match = re.fullmatch(r"(?:重新打开|重开|恢复)计划\s*(\d+)", text)
        if match:
            return self._result(
                "reopen_plan",
                1.0,
                entities={"task_id": int(match.group(1))},
                source="fixed_command",
            )
        match = re.fullmatch(r"取消计划\s*(\d+)", text)
        if match:
            return self._result(
                "cancel_plan",
                1.0,
                entities={"task_id": int(match.group(1))},
                needs_confirmation=True,
                source="fixed_command",
            )
        match = re.fullmatch(r"(?:修改|更新)计划\s*(\d+)[：:]\s*(.+)", text)
        if match:
            return self._result(
                "update_plan",
                1.0,
                entities={
                    "task_id": int(match.group(1)),
                    "changes": _parse_plan_changes(match.group(2)),
                },
                source="fixed_command",
            )
        for command, intent in (("归档记忆", "archive_memory"), ("恢复记忆", "restore_memory"), ("删除记忆", "delete_memory")):
            match = re.fullmatch(rf"{command}\s*(\d+)", text)
            if match:
                return self._result(intent, 1.0, entities={"memory_id": int(match.group(1))}, needs_confirmation=True, source="fixed_command")
        match = re.fullmatch(r"(?:确认记忆|保存记忆|确认候选)\s*(\d+)", text)
        if match:
            return self._result(
                "accept_memory_candidate",
                1.0,
                entities={"candidate_id": int(match.group(1))},
                source="fixed_command",
            )
        match = re.fullmatch(r"(?:忽略记忆|忽略候选)\s*(\d+)", text)
        if match:
            return self._result(
                "reject_memory_candidate",
                1.0,
                entities={"candidate_id": int(match.group(1))},
                source="fixed_command",
            )
        match = re.fullmatch(r"(使用新|保留旧|两条都保留)记忆\s*(\d+)", text)
        if match:
            resolutions = {
                "使用新": "use_new",
                "保留旧": "keep_old",
                "两条都保留": "keep_both",
            }
            return self._result(
                "resolve_memory_conflict",
                1.0,
                entities={
                    "conflict_id": int(match.group(2)),
                    "resolution": resolutions[match.group(1)],
                },
                needs_confirmation=True,
                source="fixed_command",
            )
        return None

    def _route_memory_review(
        self,
        text: str,
        context: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        if re.search(r"(?:怎么|如何|为什么|是什么意思).{0,8}(?:确认|忽略|候选)", text):
            return None
        memory_state = context.get("memory_interaction", {})
        memory_state = memory_state if isinstance(memory_state, dict) else {}
        pending_content = str(memory_state.get("pending_candidate_content", "")).strip()
        if (
            pending_content
            and str(memory_state.get("state", "")) == "awaiting_candidate_confirmation"
            and text.strip("。.!！?？") in {"确认", "是的", "加入候选", "放入候选", "记下来"}
        ):
            return self._matched(
                "add_memory_request",
                content=pending_content,
                source_text=str(memory_state.get("pending_source_text", "")) or pending_content,
            )

        accept_action = bool(
            re.search(
                r"(?:确认|接受|归入(?:长期)?记忆|加入(?:长期)?记忆|保存为(?:长期)?记忆)",
                text,
            )
        )
        reject_action = bool(re.search(r"(?:忽略|拒绝|不保存|不要这条)", text))
        if accept_action == reject_action:
            return None

        conversation_id = str(context.get("conversation_id", ""))
        resolution = self.reference_resolver.resolve_memory_candidates(
            text,
            memory_state,
            conversation_id=conversation_id,
        )
        ids = []
        for value in resolution.candidate_ids:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in ids:
                ids.append(parsed)
        if not ids and resolution.resolved_id:
            try:
                parsed = int(resolution.resolved_id)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                ids.append(parsed)

        intent = "accept_memory_candidates" if accept_action else "reject_memory_candidates"
        single_intent = "accept_memory_candidate" if accept_action else "reject_memory_candidate"
        if resolution.needs_clarification or not ids:
            transition = "awaiting_candidate_selection"
            if resolution.reason == "memory_reference_expired":
                question = "刚才的候选引用已经过期，请先重新查看待审核记忆。"
            elif resolution.reason == "candidate_selection_required" and len(ids) == 1:
                transition = "awaiting_candidate_confirmation"
                question = (
                    f"你是要确认候选{ids[0]}吗？回复“确认”后我再处理。"
                    if accept_action
                    else f"你是要忽略候选{ids[0]}吗？回复“确认”后我再处理。"
                )
            elif resolution.reason in {
                "candidate_selection_required",
                "multiple_candidate_reference",
            }:
                question = (
                    "你要确认全部，还是其中一条？请说“全部确认”或“第二个确认”。"
                    if accept_action
                    else "你要忽略全部，还是其中一条？请说“全部忽略”或“忽略第一个”。"
                )
            else:
                question = "我还没有可引用的候选，请先查看待审核记忆或直接说候选编号。"
            return self._matched(
                intent,
                candidate_ids=ids,
                memory_state_transition=transition,
                clarification_question=question,
            )
        if len(ids) == 1:
            return self._matched(single_intent, candidate_id=ids[0])
        return self._matched(intent, candidate_ids=ids)

    @staticmethod
    def _route_memory_view(text: str) -> Optional[Dict[str, object]]:
        normalized = _normalize_match_text(text)
        if re.fullmatch(
            r"你(?:都|还)?(?:记得|记住|知道|了解)(?:了)?(?:关于)?我(?:的)?"
            r"(?:什么|哪些)(?:信息|事情|事)?",
            text,
        ) or re.fullmatch(
            r"(?:关于我|我的长期信息)(?:你)?(?:记得|知道|了解)(?:什么|哪些)?",
            text,
        ) or re.fullmatch(
            r"你(?:现在|还|真的)?(?:认识|熟悉)(?:我|关于我)(?:吗|多少|到什么程度)?",
            text,
        ) or re.fullmatch(
            r"你对我(?:有|算有)?(?:多少)?(?:了解|认识)(?:吗|多少|到什么程度)?",
            text,
        ) or re.fullmatch(
            r"(?:我们认识吗|你知道我是谁吗)",
            text,
        ):
            return {"intent": "show_memory", "entities": {}}
        recall_patterns = (
            "你都记住了我的什么信息", "你记得我什么", "你还记得什么", "你了解我什么",
            "我之前告诉过你哪些事", "看看关于我的长期信息", "你知道我的哪些信息",
            "查看我的记忆", "我的长期记忆", "看看我的记忆", "你记住了哪些信息",
        )
        if any(_normalize_match_text(item) in normalized for item in recall_patterns):
            return {"intent": "show_memory", "entities": {}}
        if text.startswith(("搜索记忆", "找一下记忆")):
            query = re.split(r"[：:]", text, maxsplit=1)
            if len(query) == 2 and query[1].strip():
                return {"intent": "search_memory", "entities": {"query": query[1].strip()}}
        match = re.fullmatch(r"(?:归档|先收起)记忆\s*(\d+)", text)
        if match:
            return {"intent": "archive_memory", "entities": {"memory_id": int(match.group(1)), "needs_confirmation": True}}
        match = re.fullmatch(r"(?:恢复|重新启用)记忆\s*(\d+)", text)
        if match:
            return {"intent": "restore_memory", "entities": {"memory_id": int(match.group(1))}}
        match = re.fullmatch(r"(?:删除|移除)记忆\s*(\d+)", text)
        if match:
            return {"intent": "delete_memory", "entities": {"memory_id": int(match.group(1)), "needs_confirmation": True}}
        if _matches_any(
            text,
            (
                "查看候选记忆",
                "看看待确认记忆",
                "有哪些待确认记忆",
                "待审核记忆有哪些",
                "哪些记忆还没确认",
                "看看记忆候选",
            ),
        ):
            return {"intent": "show_memory_candidates", "entities": {}}
        if _matches_any(
            text,
            ("把待确认的都确认", "确认全部待审核记忆", "全部确认这些记忆"),
        ):
            return {"intent": "accept_all_memory_candidates", "entities": {}}
        match = re.fullmatch(r"(?:(?:确认|保存)记忆|确认候选)\s*(\d+)", text)
        if match:
            return {"intent": "accept_memory_candidate", "entities": {"candidate_id": int(match.group(1))}}
        match = re.fullmatch(r"(?:忽略记忆|忽略候选|删除候选记忆)\s*(\d+)", text)
        if match:
            return {"intent": "reject_memory_candidate", "entities": {"candidate_id": int(match.group(1))}}
        if _matches_any(text, ("查看记忆冲突", "看看记忆冲突")):
            return {"intent": "show_memory_conflicts", "entities": {}}
        match = re.fullmatch(r"(使用新|保留旧|两条都保留)记忆\s*(\d+)", text)
        if match:
            resolution = {"使用新": "use_new", "保留旧": "keep_old", "两条都保留": "keep_both"}
            return {
                "intent": "resolve_memory_conflict",
                "entities": {"conflict_id": int(match.group(2)), "resolution": resolution[match.group(1)]},
            }
        return None

    @staticmethod
    def _route_memory(text: str) -> str:
        return _first_capture(
            text,
            (
                r"^帮我记住[：:，,\s]*(.+)$",
                r"^以后你要记得[：:，,\s]*(.+)$",
                r"^(?:这点|这件事)?请记住[：:，,\s]*(.+)$",
                r"^记住[：:，,\s]*(.+)$",
                r"^把[：:\s]*(.+?)\s*记住$",
                r"^(.+?)\s*以后提醒我$",
            ),
        )

    def _route_contextual_memory_request(
        self,
        text: str,
        context: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        if not re.fullmatch(
            r"(?:把)?(?:你(?:刚才)?说的|刚才你说的|上面(?:说的)?|这条|这个)"
            r"(?:这条|这个|内容|建议|信息|安排)?(?:加入|放进|保存到|存入)(?:我的)?(?:长期)?记忆(?:里)?",
            text,
        ):
            return None
        assistant_text = str(context.get("last_assistant_message", "")).strip()
        content = _suggestion_title(assistant_text)
        if not content:
            return self._matched(
                "add_memory_request",
                clarification_question="你想长期保存的是哪一条信息？可以把内容再说一遍。",
            )
        return self._matched(
            "add_memory_request",
            content=content,
            source_text=text,
            warnings=["content_referenced_from_previous_assistant"],
        )

    @staticmethod
    def _route_memory_candidate(text: str) -> str:
        if re.fullmatch(r"^(?:以后\s*)?codex\s*提示词不要.+$|^roxyplan\s*不要.+$|^不要自动生图$", text, flags=re.IGNORECASE):
            return "project_preference"
        if re.fullmatch(r"^我以后想(?:做|成为|把).+$|^我想成为.+$|^我想把.+(?:长期|一直).*(?:做下去|坚持下去)$", text, flags=re.IGNORECASE):
            return "long_term_goal"
        if re.fullmatch(r"^我(?:肠胃|胃|睡眠|身体|皮肤)(?:比较|很|有点|不太|容易).+$|^我不太适合吃.+$|^我晚上容易.+$", text):
            return "health_lifestyle"
        if re.fullmatch(r"^我一般.+(?:效率|习惯|适合|会).*$|^我周末适合.+$", text):
            return "stable_habit"
        match = re.fullmatch(r"^我(?:更)?喜欢\s*(.+)$|^我不喜欢\s*(.+)$|^我更适合(?:用|在)?\s*(.+)$", text, flags=re.IGNORECASE)
        if match and len(_clean_slot(next(item for item in match.groups() if item is not None))) >= 2:
            return "user_preference"
        return ""

    @staticmethod
    def _route_explicit_action(text: str, context: Dict[str, object]) -> str:
        standalone_reference = re.fullmatch(
            r"(?:把)?(?:这个|这件事|刚才(?:那件事)?)(?:也)?"
            r"(?:记录为|记成|记到)(?:今天的)?行动(?:记录)?(?:里)?",
            text,
        )
        if standalone_reference:
            previous = str(context.get("last_user_message", "")).strip()
            return previous if previous and previous != text else ""

        inline = re.fullmatch(
            r"(.+?)[，,。；;\s]*(?:把)?(?:这个|这件事)?(?:也)?"
            r"(?:记录为|记成|记到)(?:今天的)?行动(?:记录)?(?:里)?",
            text,
        )
        if inline:
            return _clean_slot(inline.group(1))
        return _first_capture(
            text,
            (
                r"^记录(?:一下)?[：:\s]*(.+)$",
                r"^把[：:\s]*(.+?)\s*记到行动记录里$",
                r"^(.+?)\s*记到行动记录里$",
            ),
        )

    @staticmethod
    def _route_add_plan(text: str) -> List[str]:
        explicit_target = re.fullmatch(
            r"(.+?)[，,。；;\s]*(?:把)?(?:这个|这件事|它)?"
            r"(?:放进|加入|添加到)(?:我)?(?:今天)?(?:的)?"
            r"(?:计划|任务|要做的事)(?:里)?",
            text,
        )
        plan_text = _clean_slot(explicit_target.group(1)) if explicit_target else text
        reserved = re.fullmatch(
            r"(?P<slot>今天|今天上午|今天下午|今晚|上午|下午|晚上)?"
            r"(?:帮我|给我)?(?:留|安排)(?:出)?\s*"
            r"(?P<duration>\d+(?:\.\d+)?\s*(?:个)?(?:分钟|小时))"
            r"(?:的时间)?(?:用来)?\s*(?P<subject>.+)",
            plan_text,
        )
        if reserved:
            slot = _clean_slot(reserved.group("slot") or "")
            duration = _clean_slot(reserved.group("duration"))
            subject = _clean_slot(reserved.group("subject"))
            if subject:
                return [" ".join(item for item in (slot, subject, duration) if item)]

        listed = _first_capture(text, (r"^帮我安排一下今天[：:]\s*(.+)$", r"^今天要做(?:[一二三四五六七八九十\d]+件事)?[：:]\s*(.+)$"))
        if listed:
            return _split_plan_items(listed)
        arranged = re.fullmatch(
            r"^(?:我)?(?P<slot>今天|上午|下午|晚上|今晚)?(?:要|想|打算|计划|准备)?"
            r"安排\s*(?P<duration>\d+(?:\.\d+)?\s*(?:个)?(?:分钟|小时))\s*(?:的)?\s*"
            r"(?P<subject>.+)$",
            text,
        )
        if arranged:
            slot = _clean_slot(arranged.group("slot") or "")
            duration = _clean_slot(arranged.group("duration"))
            subject = re.sub(
                r"(?:的)?时间$",
                "",
                _clean_slot(arranged.group("subject")),
            ).strip()
            if subject:
                return [" ".join(item for item in (slot, subject, duration) if item)]
        item = _first_capture(text, (
            r"^(?:一会儿|待会儿)(?:我)?要去完成\s*(.+)$",
            r"^(?:我)?(?:今天)?(?:上午|下午|晚上|今晚)?(?:一会儿|待会儿)?(?:想|要|打算|计划|准备)(?:先|要|去)?\s*((?:学|学习|看|写|整理|测试|完成|做|练习|复习|处理|阅读|运动|跑|背|准备).+)$",
            r"^(?:我)?(?:今天)?(?:上午|下午|晚上|今晚)(?:要|准备)?(?:先|去)?\s*((?:学|学习|看|写|整理|测试|完成|做|练习|复习|处理|阅读|运动|跑|背).+)$",
            r"^(?:我)?今天(?:想)?抽点时间\s*((?:学|学习|看|写|整理|测试|练习|复习|阅读).+)$",
        ))
        return _split_plan_items(item) if item else []

    @staticmethod
    def _route_history_query(text: str) -> Optional[Dict[str, object]]:
        current_terms = r"刚才|刚刚|这段|当前|今天刚才"
        past_terms = r"以前|之前|过去|更早|历史|不是今天"
        conversation_terms = r"聊|聊天|对话|说(?:了)?什么|聊过|聊到哪|会话"
        if re.search(current_terms, text) and re.search(conversation_terms, text):
            return {"intent": "show_recent_conversation", "entities": {}}
        if re.search(past_terms, text) and re.search(conversation_terms, text):
            return {
                "intent": "show_conversation_history",
                "entities": {"exclude_today": "不是今天" in text},
            }
        return None

    @staticmethod
    def _route_dance(text: str, context: Dict[str, object]) -> bool:
        lowered = text.strip().lower()
        if re.search(r"(?:不要|别|不用|不想).{0,6}(?:跳|表演|展示).{0,4}(?:舞|一段)", lowered):
            return False
        if re.search(r"跳舞|舞蹈|舞步|dance", lowered) and re.search(
            r"怎么|如何|为什么|好处|教程|代码|制作|实现|推荐",
            lowered,
        ):
            return False
        if re.fullmatch(r"你(?:会|能)(?:不会|不能)?跳舞吗", lowered):
            return False
        if re.search(
            r"(?:给我|帮我|请|现在)?(?:跳|表演|展示)(?:个|一支|一段|一下)?(?:舞|舞蹈)?(?:给我)?(?:看看|一下)?"
            r"|(?:给我|现在)?来(?:个|一支|一段)舞"
            r"|dance\s+for\s+me",
            lowered,
        ):
            return bool(
                re.search(r"舞|舞蹈|dance|给我跳一个|跳一段.*看看", lowered)
            )
        if re.fullmatch(r"能(?:给我)?来一段(?:吗|么)?", lowered):
            previous = " ".join(
                str(context.get(key, ""))
                for key in ("last_user_message", "last_assistant_message")
            ).lower()
            return any(term in previous for term in ("跳舞", "舞蹈", "dance"))
        return False

    @staticmethod
    def _route_delete_plan(text: str) -> str:
        return _first_capture(text, (r"^(?:帮我)?把\s*(.+?)(?:这个)?(?:计划|任务)\s*(?:删掉|删除|取消)(?:了)?$", r"^(?:帮我)?(?:删除|取消)\s*(?:今天的)?(.+?)(?:计划|任务)?$"))

    @staticmethod
    def _route_complete_plan(text: str) -> str:
        return _first_capture(text, (r"^(?:帮我)?把\s*(.+?)\s*标记(?:为)?(?:已)?完成(?:了)?$", r"^(?:我)?(?:刚才|刚刚)?把\s*(.+?)\s*(?:做完|整理完|处理完|学完|写完|看完|测试完|完成)(?:了)?$", r"^(?:我)?(?:刚才|刚刚)?(?:已经)?(?:学完|做完|整理完|处理完|写完|看完|测试完|完成)\s*(.+?)(?:了)?$", r"^(?:我)?(?:刚才|刚刚)?\s*(.+?)\s*(?:做完|学完|整理完|处理完|写完|看完|测试完|完成)(?:了)?$"))

    @staticmethod
    def _route_implicit_action(text: str) -> str:
        return _first_capture(text, (
            r"^今天\s*(推进了.+)$",
            r"^今天\s*(推进不少)(?:，|,)?(?:帮我)?记一下$",
            r"^(?:我)?刚刚\s*((?:测试|学习|推进|整理|练习|复习|阅读|处理)了?.+)$",
            r"^(?:我)?刚才\s*((?:测试|学习|推进|整理|练习|复习|阅读|处理)了?.+)$",
        ))

    def _route_contextual_plan(
        self,
        text: str,
        context: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        last_task = context.get("last_task")
        last_task = last_task if isinstance(last_task, dict) else {}
        resolution = self.reference_resolver.resolve(
            text,
            context,
            conversation_id=str(context.get("conversation_id", "")),
        )
        task_ref = str(
            resolution.resolved_id
            or last_task.get("uid")
            or last_task.get("id")
            or last_task.get("title")
            or ""
        ).strip()

        if re.fullmatch(
            r"(?:完成|做完|学完)?(?:前一个|上一个|前面的)(?:计划|任务)?(?:了)?",
            text,
        ):
            if resolution.needs_clarification or not resolution.resolved_id:
                return self._matched(
                    "complete_plan",
                    clarification_question="我不能唯一确定“前一个计划”是哪条，请说计划编号或标题。",
                )
            return self._matched("complete_plan", query=resolution.resolved_id)

        temporal = self.entity_parser.parse(text)
        if temporal.get("shift_minutes") is not None and re.search(
            r"(?:推迟|延后|往后挪|往后推)", text
        ):
            return self._matched(
                "reschedule_plan",
                **({"query": task_ref} if task_ref else {}),
                clarification_question=str(
                    temporal.get("clarification_question", "")
                )
                or "你想调整哪一条计划，它原来几点开始？",
            )
        if temporal.get("event_anchor") and re.search(
            r"(?:改到|挪到|安排到|推迟到)", text
        ):
            return self._matched(
                "reschedule_plan",
                **({"query": task_ref} if task_ref else {}),
                clarification_question=str(
                    temporal.get("clarification_question", "")
                ),
            )

        arrangement = re.search(
            r"(?:^|[，,。；;])(?:帮我|给我)(?P<slot>今天|上午|下午|晚上|今晚)?"
            r"安排(?:一下)?\s*(?P<body>[^：:，,。；;]+)$",
            text,
        )
        if arrangement:
            slot = _clean_slot(arrangement.group("slot") or "")
            body = re.sub(
                r"(?:计划|任务)$",
                "",
                _clean_slot(arrangement.group("body")),
            ).strip()
            if body in {"", "学习", "今天学习", "日常学习"}:
                prefix = f"{slot}" if slot else "这段时间"
                return self._matched(
                    "add_plan",
                    clarification_question=(
                        f"{prefix}想安排哪类学习内容？告诉我主题和大概时长，"
                        "我再帮你加入计划。"
                    ),
                )

        learning_wish = re.fullmatch(
            r"(?:我)?(?:要|想|准备)(?:开始)?(?:学习|练习|学)\s*(.+)",
            text,
        )
        if learning_wish and not re.search(
            r"今天|上午|下午|晚上|今晚|明天|\d+\s*(?:个)?(?:分钟|小时)|计划|安排",
            text,
        ):
            subject = _clean_slot(learning_wish.group(1))
            return self._matched(
                "add_plan",
                clarification_question=(
                    f"你是想现在开始学“{subject}”，还是把它加入今天计划？"
                    "如果现在开始，可以再说说想先练哪一部分。"
                ),
            )

        if re.fullmatch(r"(?:我)?感觉.*(?:应该|可以).*(?:学|学习)(?:一下|一会儿)?", text):
            return self._matched(
                "add_plan",
                clarification_question="你想学什么内容，大概安排多久？",
            )

        match = re.fullmatch(r"(?:我)?今天(?:想)?抽点时间(?:学|学习)\s*(.+)", text)
        if match:
            title = "学习" + _clean_slot(match.group(1))
            return self._matched(
                "add_plan",
                tasks=[title],
                needs_confirmation=True,
                candidate_actions=[{"intent": "add_plan", "entities": {"tasks": [title]}}],
            )

        if re.fullmatch(r"(?:我)?准备开始(?:学|学习)(?:了)?", text):
            return self._matched(
                "add_plan",
                clarification_question="你准备学什么？需要我把具体内容加入今天计划吗？",
            )

        if re.fullmatch(r"(?:把)?(?:这个|这件事|刚才那个)(?:添加|加入|放进)(?:今天)?计划", text):
            suggestion = _suggestion_title(str(context.get("last_assistant_message", "")))
            if not suggestion:
                return self._matched(
                    "add_plan",
                    clarification_question="你指的是哪件事？可以直接说要加入计划的内容。",
                )
            return self._matched(
                "add_plan",
                tasks=[suggestion],
                needs_confirmation=True,
                candidate_actions=[
                    {"intent": "add_plan", "entities": {"tasks": [suggestion]}}
                ],
            )

        duration = re.fullmatch(
            r"(?:把)?(?:刚才那个|这个|这件事|它)(?:的?时长)?(?:改成|调整为)\s*(\d+(?:\.\d+)?)\s*(?:个)?\s*(分钟|小时)",
            text,
        )
        explicit_duration = None if duration else re.fullmatch(
            r"(?:把)?(.+?)(?:计划|任务)?(?:的?时长)?(?:改成|调整为)\s*(\d+(?:\.\d+)?)\s*(?:个)?\s*(分钟|小时)",
            text,
        )
        duration = duration or explicit_duration
        if duration:
            contextual = explicit_duration is None
            query = task_ref if contextual else _clean_slot(duration.group(1) or "")
            if not query:
                return self._matched(
                    "update_plan",
                    clarification_question="你想修改哪一条计划？",
                )
            number_group = 1 if contextual else 2
            unit_group = 2 if contextual else 3
            value = float(duration.group(number_group))
            minutes = int(value * 60) if duration.group(unit_group) == "小时" else int(value)
            return self._matched(
                "update_plan",
                query=query,
                changes={"duration_minutes": minutes},
            )

        title_change = re.fullmatch(
            r"(?:把)?(?:刚才那个|这个|这件事|它)(?:改成|改为)\s*(.+)", text
        )
        if title_change:
            if not task_ref:
                return self._matched(
                    "update_plan",
                    clarification_question="你想修改哪一条计划？",
                )
            return self._matched(
                "update_plan",
                query=task_ref,
                changes={"title": _clean_slot(title_change.group(1))},
            )

        reschedule = re.fullmatch(
            r"(?:把)?(?:刚才那个|这个|这件事|它)?(?:推迟到?|改到|挪到|安排到)\s*(上午|下午|晚上|今晚|明天|明天上午|明天下午|明天晚上)",
            text,
        )
        explicit_reschedule = None if reschedule else re.fullmatch(
            r"(?:把)?(.+?)(?:计划|任务)?(?:推迟到?|改到|挪到|安排到)\s*(上午|下午|晚上|今晚|明天|明天上午|明天下午|明天晚上)",
            text,
        )
        reschedule = reschedule or explicit_reschedule
        if reschedule:
            contextual = explicit_reschedule is None
            query = task_ref if contextual else _clean_slot(reschedule.group(1) or "")
            if not query:
                return self._matched(
                    "reschedule_plan",
                    clarification_question="你想改期哪一条计划？",
                )
            return self._matched(
                "reschedule_plan",
                query=query,
                schedule_text=reschedule.group(1 if contextual else 2),
            )

        if re.fullmatch(r"(?:算了[，,]?)?(?:不做|取消)(?:这个|这件事|刚才那个|它)(?:了)?", text):
            if not task_ref:
                return self._matched(
                    "cancel_plan",
                    clarification_question="你想取消哪一条计划？",
                )
            return self._matched("cancel_plan", query=task_ref, needs_confirmation=True)

        if re.fullmatch(r"(?:把)?(?:这个|这件事|刚才那个|它)(?:计划|任务)?(?:删(?:掉|除)?|移除)(?:了)?", text):
            if not task_ref:
                return self._matched(
                    "delete_plan",
                    clarification_question="你想删除哪一条计划？",
                )
            return self._matched(
                "delete_plan",
                query=task_ref,
                needs_confirmation=True,
            )

        if re.fullmatch(r"(?:这个|这件事|刚才那个|它)(?:做完|完成)(?:了)?", text):
            if not task_ref:
                return self._matched(
                    "complete_plan",
                    clarification_question="你完成的是哪一条计划？",
                )
            return self._matched("complete_plan", query=task_ref)

        if re.fullmatch(r"(?:重新打开|重新开始|恢复)(?:这个|这件事|刚才那个|它)(?:计划)?", text):
            if not task_ref:
                return self._matched(
                    "reopen_plan",
                    clarification_question="你想重新打开哪一条计划？",
                )
            return self._matched("reopen_plan", query=task_ref)
        return None

    @staticmethod
    def _vague_plan_request(text: str, plans: Sequence[str]) -> str:
        joined = "".join(plans)
        if re.search(r"(?:学|学习)(?:一下|一会儿)$", joined) and not re.search(
            r"机器学习|英语|数学|编程|代码|课程|文档|项目", joined
        ):
            return "clarification"
        if re.search(r"感觉|应该|抽点时间|有空|也许|可能", text):
            return "confirmation" if len(joined) >= 4 else "clarification"
        return ""

    def _enrich_entities(
        self,
        text: str,
        result: Dict[str, object],
        context: Dict[str, object],
    ) -> Dict[str, object]:
        parsed = self.entity_parser.parse(text)
        entities = result.setdefault("entities", {})
        if not isinstance(entities, dict):
            entities = {}
            result["entities"] = entities
        intent = str(result.get("intent", "chat"))
        for source, target in (
            ("date", "date"),
            ("time_period", "time_slot"),
            ("start_time", "start_time"),
            ("duration_minutes", "duration_minutes"),
            ("reference_text", "reference_text"),
            ("operation", "operation"),
            ("scope", "scope"),
            ("raw_time_text", "raw_time_text"),
            ("event_anchor", "event_anchor"),
            ("shift_minutes", "shift_minutes"),
        ):
            value = parsed.get(source)
            if value not in (None, "") and target not in entities:
                entities[target] = value

        reference = self.reference_resolver.resolve(
            text,
            context,
            conversation_id=str(context.get("conversation_id", "")),
        )
        if reference.resolved_id and "task_id" not in entities and intent in {
            "complete_plan",
            "delete_plan",
            "update_plan",
            "reschedule_plan",
            "reopen_plan",
            "cancel_plan",
        }:
            entities["task_id"] = reference.resolved_id

        if intent == "add_plan":
            tasks = entities.get("tasks")
            if isinstance(tasks, list):
                entities["tasks"] = [
                    str(item).strip() for item in tasks if str(item).strip()
                ]
            elif isinstance(entities.get("title"), str):
                entities["title"] = str(entities["title"]).strip()
        if intent == "update_plan" and parsed.get("duration_minutes") is not None:
            changes = entities.get("changes")
            changes = dict(changes) if isinstance(changes, dict) else {}
            changes.setdefault("duration_minutes", parsed["duration_minutes"])
            entities["changes"] = changes

        if bool(parsed.get("ambiguous")) and intent in {
            "add_plan",
            "update_plan",
            "reschedule_plan",
        }:
            result["needs_confirmation"] = False
            result["clarification_question"] = str(
                parsed.get("clarification_question", "")
            ) or "这个时间表达还不够明确，请换成具体日期、时段或时长。"
        result["slots"] = dict(entities)
        return result

    def _matched(self, intent: str, **entities: object) -> Dict[str, object]:
        confirmation = bool(entities.pop("needs_confirmation", False)) or intent in {
            "delete_plan",
            "delete_memory",
            "resolve_memory_conflict",
        }
        clarification_question = str(entities.pop("clarification_question", "") or "").strip() or None
        candidate_actions = entities.pop("candidate_actions", [])
        warnings = entities.pop("warnings", [])
        confidence = float(entities.pop("confidence", 0.9))
        print("[Intent] source: rule", flush=True)
        print(f"[Intent] matched: {intent} confidence={confidence:.2f}", flush=True)
        return self._result(
            intent,
            confidence,
            entities=entities,
            needs_confirmation=confirmation,
            source="rule",
            warnings=warnings if isinstance(warnings, list) else [],
            clarification_question=clarification_question,
            candidate_actions=(
                candidate_actions if isinstance(candidate_actions, list) else []
            ),
        )

    @staticmethod
    def _result(
        intent: str,
        confidence: float,
        *,
        entities: Optional[Dict[str, object]] = None,
        needs_confirmation: bool = False,
        source: str = "fallback",
        warnings: Optional[List[str]] = None,
        clarification_question: Optional[str] = None,
        candidate_actions: Optional[List[Dict[str, object]]] = None,
    ) -> Dict[str, object]:
        values = entities or {}
        result = IntentResult(
            intent=intent,
            confidence=confidence,
            entities=values,
            needs_confirmation=needs_confirmation,
            source=source,
            warnings=warnings or [],
            clarification_question=clarification_question,
            candidate_actions=candidate_actions or [],
        ).to_dict()
        result.update({"slots": dict(values), "reason": source})
        return result

    def _with_legacy_slots(self, parsed: Dict[str, object]) -> Dict[str, object]:
        return self._result(
            str(parsed["intent"]),
            float(parsed["confidence"]),
            entities=dict(parsed["entities"]),
            needs_confirmation=bool(parsed["needs_confirmation"]),
            source="llm",
            warnings=list(parsed.get("warnings", [])),
            clarification_question=parsed.get("clarification_question"),
            candidate_actions=list(parsed.get("candidate_actions", [])),
        )

    def _fallback(self, reason: str) -> Dict[str, object]:
        print("[Intent] fallback: chat", flush=True)
        return self._result("chat", 0.0, source="fallback" if reason != "fixed_command" else "fixed_command")


def is_fixed_command(user_text: str) -> bool:
    text = _clean_text(user_text)
    if text in _FIXED_EXACT_COMMANDS or text.startswith(_FIXED_PREFIXES):
        return True
    return bool(
        _FIXED_NUMBERED_COMMAND.fullmatch(text)
        or _FIXED_PLAN_UPDATE.fullmatch(text)
    )


def match_plan_task(query: str, tasks: Sequence[Dict[str, object]], threshold: float = 0.56) -> Dict[str, object]:
    valid_tasks = [task for task in tasks if str(task.get("title", "")).strip()]
    if not valid_tasks or not _normalize_match_text(query):
        return {"status": "not_found", "task": None, "candidates": [], "score": 0.0}
    scored = sorted(((_task_similarity(query, str(task.get("title", ""))), task) for task in valid_tasks), key=lambda item: item[0], reverse=True)
    direct = [item for item in scored if item[0] >= 0.96]
    pending = [item for item in direct if not item[1].get("done", False)]
    if pending:
        direct = pending
    if len(direct) == 1:
        return _match_result("matched", direct[0], [])
    if len(direct) > 1:
        return _match_result("matched", direct[0], []) if direct[0][0] - direct[1][0] >= 0.12 else _match_result("ambiguous", None, [item[1] for item in direct[:3]], direct[0][0])
    top_score, top_task = scored[0]
    if top_score < threshold:
        return {"status": "not_found", "task": None, "candidates": [], "score": top_score}
    if len(scored) == 1 or top_score - scored[1][0] >= 0.10:
        return _match_result("matched", (top_score, top_task), [])
    return _match_result("ambiguous", None, [task for score, task in scored if top_score - score < 0.10][:3], top_score)


def _match_result(status, scored_item, candidates, score=0.0):
    if scored_item is not None:
        score, task = scored_item
        return {"status": status, "task": dict(task), "candidates": [], "score": score}
    return {"status": status, "task": None, "candidates": [dict(task) for task in candidates], "score": score}


def _task_similarity(query: str, title: str) -> float:
    query_text, title_text = _normalize_match_text(query), _normalize_match_text(title)
    query_core, title_core = _semantic_core(query_text), _semantic_core(title_text)
    if query_text == title_text or query_core == title_core:
        return 1.0
    if query_text in title_text or title_text in query_text or (len(query_core) >= 2 and len(title_core) >= 2 and (query_core in title_core or title_core in query_core)):
        return 0.98
    if query_text in {"学习计划", "学习任务"} and re.search(
        r"学习|复习|练习|阅读|机器学习",
        title_text,
    ):
        return 0.82
    return max(SequenceMatcher(None, query_text, title_text).ratio(), SequenceMatcher(None, query_core, title_core).ratio())


def _semantic_core(text: str) -> str:
    core = text.replace("roxyplan", "roxy")
    core = re.sub(r"[0-9一二三四五六七八九十半]+(?:个)?(?:分钟|小时)", "", core)
    core = re.sub(r"今天|上午|下午|晚上|今晚|计划|任务|整理|测试|学习|练习|复习|处理|阅读|完成|做|学|写|看", "", core)
    return core or text


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(text)).lower())


def _clean_text(text: str) -> str:
    return str(text).strip().strip("。.!！?？")


def _first_capture(text: str, patterns: Sequence[str]) -> str:
    for pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_slot(match.group(1))
    return ""


def _clean_slot(text: str) -> str:
    return str(text).strip(" \t\r\n，,。.!！?？：:；;")


def _split_plan_items(text: str) -> List[str]:
    parts = re.split(r"[、，,；;\n]+", text) if text else []
    result = []
    for part in parts:
        item = _clean_slot(re.sub(r"^[一二三四五六七八九十\d]+[.、)）]\s*", "", part))
        if item and item not in result:
            result.append(item)
    return result


def _matches_any(text: str, phrases: Sequence[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _suggestion_title(text: str) -> str:
    compact = " ".join(str(text).split()).strip()
    if not compact:
        return ""
    compact = re.sub(r"^(?:可以|建议|不妨|要不要|今天可以这样安排)[：:，,\s]*", "", compact)
    parts = [
        _clean_slot(item)
        for item in re.split(r"[。！？!?；;\n]+", compact)
        if _clean_slot(item)
    ]
    for part in parts:
        if re.search(r"学习|复习|练习|整理|测试|阅读|写|完成|处理|运动", part):
            return part[:80]
    return ""


def _safe_llm_context(context: Optional[Dict[str, object]]) -> Dict[str, object]:
    if not isinstance(context, dict):
        return {}
    last_task = context.get("last_task")
    task = {}
    if isinstance(last_task, dict):
        task = {
            key: last_task.get(key)
            for key in ("id", "title", "done", "status")
            if key in last_task
        }
    return {
        "last_task": task or None,
        "last_user_message": str(context.get("last_user_message", ""))[-180:],
        "last_assistant_message": str(context.get("last_assistant_message", ""))[-240:],
        "current_facts": (
            context.get("current_facts", {})
            if isinstance(context.get("current_facts", {}), dict)
            else {}
        ),
    }


def _validate_llm_entities(intent: str, entities: Dict[str, object]) -> bool:
    if not isinstance(entities, dict):
        return False
    allowed_change_fields = {
        "title", "date", "time_slot", "duration_minutes", "priority", "note"
    }
    changes = entities.get("changes")
    if changes is not None and (
        not isinstance(changes, dict)
        or not set(changes).issubset(allowed_change_fields)
    ):
        return False
    if intent == "add_plan":
        tasks = entities.get("tasks")
        return (
            isinstance(tasks, list)
            and all(isinstance(item, str) and item.strip() for item in tasks)
        ) or isinstance(entities.get("title"), str)
    if intent in {"complete_plan", "delete_plan", "reopen_plan", "cancel_plan"}:
        return any(key in entities for key in ("query", "task_id"))
    if intent in {"update_plan", "reschedule_plan"}:
        return any(key in entities for key in ("query", "task_id")) and (
            isinstance(changes, dict)
            or isinstance(entities.get("schedule_text"), str)
        )
    if intent == "add_action_log":
        return isinstance(entities.get("content"), str)
    if intent in {"search_memory", "add_memory_request", "memory_candidate"}:
        return isinstance(
            entities.get("query" if intent == "search_memory" else "content"), str
        )
    if intent in {"archive_memory", "restore_memory"}:
        return isinstance(entities.get("memory_id"), int)
    if intent == "delete_memory":
        return isinstance(entities.get("memory_id"), int) or entities.get("scope") == "all"
    if intent in {"accept_memory_candidate", "reject_memory_candidate"}:
        return isinstance(entities.get("candidate_id"), int)
    if intent in {"accept_memory_candidates", "reject_memory_candidates"}:
        values = entities.get("candidate_ids")
        return (
            isinstance(values, list)
            and bool(values)
            and all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in values)
        )
    if intent == "resolve_memory_conflict":
        return isinstance(entities.get("conflict_id"), int) and isinstance(
            entities.get("resolution"), str
        )
    return True


def _parse_plan_changes(text: str) -> Dict[str, object]:
    value = _clean_slot(text)
    duration = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(分钟|小时)", value)
    if duration and re.search(r"时长|改成|调整为|^\d", value):
        number = float(duration.group(1))
        return {
            "duration_minutes": (
                int(number * 60) if duration.group(2) == "小时" else int(number)
            )
        }
    priority = re.search(r"优先级(?:改成|改为|设为|是)?\s*(高|中|低|high|medium|low)", value, re.I)
    if priority:
        return {"priority": priority.group(1)}
    note = re.match(r"备注(?:改成|改为|设为|是)?[：:\s]*(.+)", value)
    if note:
        return {"note": _clean_slot(note.group(1))}
    slot = next((item for item in ("上午", "下午", "晚上", "今晚") if item in value), "")
    if slot and re.search(r"时间|时间段|改到|安排到|推迟", value):
        return {"time_slot": "晚上" if slot == "今晚" else slot}
    return {"title": value}
