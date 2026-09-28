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
    "update_plan", "merge_plan", "reschedule_plan", "reopen_plan", "cancel_plan",
    "daily_review", "save_review", "add_memory_request", "memory_candidate",
    "show_plan", "inspect_plan_duplicates", "show_action_log", "show_growth_log", "reminder_control",
    "show_memory", "search_memory", "archive_memory", "restore_memory",
    "delete_memory", "show_memory_candidates", "accept_memory_candidate",
    "accept_memory_candidates", "reject_memory_candidate",
    "reject_memory_candidates", "accept_all_memory_candidates",
    "show_memory_conflicts", "resolve_memory_conflict",
    "show_memory_audit", "show_archived_memories",
    "show_recent_conversation", "show_conversation_history",
    "multi_action", "dance", "sleep_pet", "wake_pet",
}
DISABLED_MAIN_CHAT_CANDIDATE_INTENTS = {
    "memory_candidate",
    "show_memory_candidates",
    "accept_memory_candidate",
    "accept_memory_candidates",
    "reject_memory_candidate",
    "reject_memory_candidates",
    "accept_all_memory_candidates",
}
MAIN_CHAT_INTENTS = SUPPORTED_INTENTS - DISABLED_MAIN_CHAT_CANDIDATE_INTENTS

SEMANTIC_DECISION_POLICY = (
    "Decision policy:\n"
    "1. Decide the communicative act of the current utterance, not isolated domain "
    "keywords. Greetings, feelings, explanations, hypothetical questions, quoted "
    "commands and capability questions are chat.\n"
    "2. read means the user asks to inspect existing application data. write means "
    "the user explicitly asks to create, change, complete, archive, delete or save "
    "application data. clarify is allowed only for an explicit read/write request "
    "whose required object or content is missing.\n"
    "3. Respect the scope of negation and correction. A cancelled clause must not "
    "become an action. For example, '把复习加入计划，不对，先别加' is chat with "
    "no tool, and '我不想跳舞' is chat.\n"
    "4. Resolve ellipsis or anaphora only from the supplied same-conversation "
    "structured context. If a reference such as '这个/那些/第二个/刚才那条' is not "
    "unique, use clarify and do not invent content or IDs.\n"
    "5. Text inside quotes or an explicit payload belongs only to that tool's "
    "arguments. Words such as 跳舞、删除、完成、计划 inside the payload must not "
    "create another action.\n"
    "6. candidate_actions is only for intent=multi_action with two or more separately "
    "explicit tasks in the same utterance. For a single task it must be empty. Never "
    "add an unrelated tool or duplicate an action.\n"
    "7. For add_plan, time_slot must be one of 上午/下午/晚上 and duration_minutes "
            "must be an integer number of minutes. Keep clock wording out of time_slot because "
            "the current tool contract does not store an exact start clock. "
            "For add_plan, entities.title must contain only the concise actionable task, "
            "not a greeting, acknowledgement, thanks, explanation, assistant wording, "
            "or the entire conversational sentence.\n"
    "8. When recent structured context contains suggestion_snapshot and the user "
    "explicitly asks to add an ordinal item such as '第二个也加入', choose write/add_plan "
    "and copy that item's exact title into entities.title. Do not put task_ref, an "
    "ordinal, snapshot ID or any other unsupported field into add_plan arguments.\n"
    "9. Questions about the user's stored name, preferences, habits, goals, projects, "
    "current information or another explicitly named stable fact use read/show_memory "
    "with proposed_tool=list_memories. "
    "Select query_mode=attribute and the matching registered attribute; add topic "
    "only to narrow the domain (food, learning, work, location or a concrete topic). "
    "For a named property outside the registered profile categories, select attribute=fact "
    "and copy the property name into topic instead of forcing it into current_state. "
    "Use existence to check whether a specific fact is stored and provenance to "
    "ask its source, with query grounded in the current question. Use overview only "
    "for a broad request to inspect the user's memory. Questions about the assistant "
    "herself and refusals to disclose memory remain chat. Use only legal parameter "
    "types and enum values from the tool contracts.\n"
    "Minimal contrasts:\n"
    "- '跳舞' => write/dance; '我喜欢看你跳舞' => chat/chat (pet compliment).\n"
    "- '今天还有什么计划' => read/show_plan; '我在做一个陪伴计划桌宠' => chat/chat.\n"
    "- '列出我的今日计划' => read/show_plan with request_mode=query; "
    "'帮我列几条今日计划建议' => chat/chat with request_mode=advice and no tool.\n"
    "- '把复习随机森林加入今天计划' => write/add_plan; "
    "'怎么安排复习更合理' => chat/chat.\n"
    "- '我想学 cosplay' => write/add_plan with entities.title='cosplay', "
    "subject=self, polarity=positive, modality=desire. This is a plan candidate "
    "even when time is missing; the application will ask whether to start, hear "
    "advice, or add it, so do not downgrade it to chat.\n"
    "- '我想把 cosplay 加入今天计划' => write/add_plan with "
    "entities.title='cosplay' and modality=desire. The explicit 加入计划 action "
    "must not be treated as a generic wish.\n"
    "- '我今天晚上一定要早睡' => write/add_plan (self+positive+commitment, "
    "title=早睡 time_slot=晚上); "
    "'我今天晚上想早睡' => write/add_plan (self+positive+desire, "
    "title=早睡 time_slot=晚上).\n"
    "- '你记得刚才说什么' => read/show_recent_conversation; "
    "'你记得以前聊过什么' => read/show_conversation_history.\n"
    "- '请记住：我喜欢早上学习' => write/add_memory_request.\n"
    "- A concrete statement about an action the user has actually completed, such "
    "as '我今天看了一会儿小说', may use write/add_action_log only as a proposed "
    "action-log offer with entities.content grounded in the user's words, "
    "request_mode=possible_action, explicit_command=false and needs_confirmation=true. "
    "The application will create a typed confirmation state; never treat a later "
    "generic '好' as authorization unless that state exists. Feelings or vague states "
    "such as '我好累' remain chat.\n"
    "- A stable non-sensitive first-person preference such as '我喜欢羊肉串' "
    "may use write/add_memory_request only as a proposed memory offer with "
    "explicit_command=false. The application will ask the user and must not "
    "save anything before confirmation. An ordinary sensitive statement is "
    "chat and must not trigger an unsolicited memory offer.\n"
    "- Broad aspirations such as '我要努力学习' or '我要成为百万富翁' are "
    "chat/chat when they do not name a concrete executable action. The normal "
    "reply may help the user unpack the aspiration, but must not create a plan.\n"
    "- A request to summarize the conversation and save it is chat/chat. The "
    "normal reply should summarize only user-confirmed facts and provide one to "
    "three explicit '请记住：...' statements for the user to send separately; "
    "never save the assistant's summary or the whole conversation directly.\n"
    "- '你记得我什么' => read/show_memory; '记忆是怎么形成的' => chat/chat.\n"
)

_FIXED_EXACT_COMMANDS = {
    "查看计划", "今日计划", "明日计划", "我的计划", "今天的计划", "列出今日计划",
    "今天要做什么", "我的行动记录", "查看记录", "今日复盘", "复盘一下", "今天完成了什么",
    "保存今日复盘", "查看成长日志", "我的记忆", "查看长期记忆",
    "查看待确认记忆", "清空待确认记忆", "查看记忆冲突", "整理记忆",
    "把待确认的都确认", "确认全部待审核记忆", "查看已归档记忆",
    "先别提醒我", "暂停提醒", "恢复提醒", "继续提醒我", "重新开启提醒",
}
_FIXED_PREFIXES = (
    "今日计划：", "今日计划:", "添加计划：", "添加计划:", "记录：", "记录:",
    "行动记录：", "行动记录:", "记住：", "记住:", "忘记：", "忘记:", "我完成了",
    "搜索记忆：", "搜索记忆:",
    "仍然添加：", "仍然添加:",
)
_FIXED_REVIEW_SAVE = re.compile(
    r"^(?:请)?(?:帮我|给我)?(?:"
    r"(?:生成(?:并|并且|后|然后)?保存|生成|保存)(?:今天|今日)(?:的)?(?:复盘|成长复盘)"
    r"|生成(?:今天|今日)(?:的)?(?:复盘|成长复盘)(?:并|并且|后|然后|顺便)?保存"
    r")$"
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
        self.last_diagnostic = ""
        self.parameter_docs: str = ""

    def _reject(self, reason: str) -> None:
        self.last_diagnostic = self._append_diagnostic(reason)
        print(f"[SemanticDecision] rejected reason={self.last_diagnostic}", flush=True)
        return None

    def _note(self, reason: str) -> None:
        self.last_diagnostic = self._append_diagnostic(reason)
        print(f"[SemanticDecision] normalized reason={reason}", flush=True)

    def _append_diagnostic(self, reason: str) -> str:
        value = str(reason).strip()
        if not value:
            return self.last_diagnostic
        existing = [item for item in self.last_diagnostic.split(";") if item]
        if value not in existing:
            existing.append(value)
        return ";".join(existing)

    def parse(
        self,
        text: str,
        context: Optional[Dict[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        self.last_diagnostic = ""
        if self.chat_callable is None:
            return self._reject("semantic_provider_unavailable")
        prompt = (
            "You classify one Chinese user message for a local desktop assistant. "
            "Return JSON only with mode, intent, entities, proposed_tool, confidence, "
            "follow_up_target, needs_confirmation, warnings, clarification_question, "
            "candidate_actions, subject, polarity, modality, request_mode and "
            "explicit_command. "
            "mode must be one of chat/read/write/clarify. "
            "Allowed intents: " + ", ".join(sorted(MAIN_CHAT_INTENTS)) + ". "
            "subject is 'self' when the user speaks about their own action/state, "
            "'other' when quoting or describing someone else, '' otherwise. "
            "polarity is 'positive' for affirmative/desired, 'negative' for "
            "negated/refused, '' otherwise. "
            "modality is 'commitment' for definite/certain intent, 'desire' for "
            "wish/want/preference, 'hypothetical' for if/would/imagined, "
            "'question' for interrogative, '' otherwise. "
            "request_mode must be query for reads, execute only for an explicit "
            "application command, possible_action for a concrete wish that still "
            "needs a user choice, and discuss for chat. explicit_command is true "
            "only when the current message explicitly asks the application to "
            "perform the proposed write. "
            "For first-person future plan statements that name a concrete action "
            "and time (e.g. '我今天晚上一定要早睡', '我明天想学数学'), set "
            "intent=add_plan with proposed_tool=add_plan, populate entities.title "
            "from the action and entities.time_slot from the time word.  Always "
            "include subject/polarity/modality so the downstream authorization "
            "policy can decide direct-execute, create-pending, or no-action. "
            "Use chat for greetings, emotions, knowledge questions and ordinary "
            "statements that do not contain a concrete future action. "
            "Only select a read or write intent when the current message actually asks to "
            "read or change application data; domain words alone are not a tool request. "
            "The current message is authoritative; use recent context only for an explicit "
            "follow-up reference. If the user explicitly asks to remember or save your "
            "previous reply/answer, choose add_memory_request with mode=write, "
            "proposed_tool=save_formal_memory and entities.content exactly "
            "'previous_assistant_message'. If the user explicitly refers to their own "
            "previous message, use entities.content='previous_user_message'. Never copy "
            "the referenced message text into entities. Deletions, clears, overwrites and conflict resolution "
            "must set needs_confirmation true. Never invent an "
            "entity that is not in the message or supplied context. Do not answer the user.\n"
            + SEMANTIC_DECISION_POLICY
            + ("\n" + self.parameter_docs + "\n" if self.parameter_docs else "")
            + "\nRecent structured context: "
            + json.dumps(_safe_llm_context(context), ensure_ascii=False)
            + "\nUser message: "
            + text
        )
        try:
            raw = str(self.chat_callable([{"role": "system", "content": prompt}])).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
            data = json.loads(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            return self._reject(f"json_parse_error:{type(error).__name__}")
        if not isinstance(data, dict):
            return self._reject("schema_root_not_object")
        if str(data.get("intent", "")) in DISABLED_MAIN_CHAT_CANDIDATE_INTENTS:
            return self._reject("candidate_intent_disabled_in_main_chat")
        if str(data.get("intent", "")) not in MAIN_CHAT_INTENTS:
            return self._reject("schema_unsupported_intent")
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return self._reject("schema_invalid_confidence")
        intent = str(data["intent"])
        mode = str(data.get("mode", "")).strip().lower()
        clarification_value = data.get("clarification_question")
        if clarification_value is not None and not isinstance(clarification_value, str):
            return self._reject("schema_invalid_clarification_question")
        clarification_question = str(clarification_value or "").strip()[:240] or None
        if mode not in {"chat", "read", "write", "clarify"}:
            return self._reject("schema_missing_or_invalid_mode")
        if intent != "chat" and clarification_question and mode != "clarify":
            self._note("clarification_question_forced_clarify")
            mode = "clarify"
        if mode == "clarify" and not clarification_question:
            return self._reject("clarify_without_question")
        entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
        if not _validate_llm_entities(
            intent,
            entities,
            allow_incomplete=(mode == "clarify" and bool(clarification_question)),
        ):
            return self._reject("schema_invalid_entities")
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
                action_intent not in MAIN_CHAT_INTENTS
                or action_intent in {"chat", "multi_action"}
                or not isinstance(action_entities, dict)
                or not _validate_llm_entities(action_intent, action_entities)
            ):
                continue
            safe_actions.append(
                {"intent": action_intent, "entities": dict(action_entities)}
            )
        candidate_actions = safe_actions
        if intent == "multi_action":
            if len(candidate_actions) < 2:
                return self._reject("multi_action_requires_two_actions")
        elif candidate_actions:
            self._note("candidate_actions_removed_for_single_intent")
            candidate_actions = []
        proposed_tool_value = data.get("proposed_tool")
        if proposed_tool_value is not None and not isinstance(proposed_tool_value, str):
            return self._reject("schema_invalid_proposed_tool")
        proposed_tool = str(proposed_tool_value or "").strip()
        # ── Tool resolution ──────────────────────────────────────────
        # Reject semantic contradictions before they become executable
        # candidates. The downstream pipeline validates the selected tool's
        # registered schema; it cannot recover intent that was contradictory.
        canonical_tool = _proposed_tool_for_intent(intent)
        if proposed_tool and canonical_tool and proposed_tool != canonical_tool:
            return self._reject("proposed_tool_intent_mismatch")
        if not proposed_tool:
            proposed_tool = canonical_tool
        follow_up_target = data.get("follow_up_target")
        if follow_up_target is not None and not isinstance(follow_up_target, str):
            return self._reject("schema_invalid_follow_up_target")
        # Bare affirmatives must only consume structured pending operations.
        # They must never create new write intents with raw
        # previous_assistant_message content — that would save the assistant's
        # entire reply as a memory.
        bare_affirmative = bool(
            re.fullmatch(
                r"(?:对|对的|嗯|好|好的|行|可以|就这样|是的|没错|确认)[。！？!?]?",
                str(text).strip(),
            )
        )
        if (
            bare_affirmative
            and mode == "write"
            and entities.get("content") == "previous_assistant_message"
        ):
            return self._reject(
                "affirmative_cannot_use_previous_assistant_message_as_content"
            )

        if intent == "chat":
            if mode != "chat" or clarification_question or proposed_tool or candidate_actions:
                self._note("chat_decision_stripped_action_fields")
            mode = "chat"
            clarification_question = None
            proposed_tool = ""
            candidate_actions = []
        elif mode != "clarify":
            canonical_mode = _mode_for_intent(intent)
            if mode != canonical_mode:
                return self._reject("mode_intent_mismatch")
        needs_confirmation = bool(data.get("needs_confirmation", False))
        if intent in {
            "delete_plan", "merge_plan", "delete_memory", "archive_memory", "restore_memory",
            "resolve_memory_conflict",
        }:
            needs_confirmation = True
        if (
            intent in {"add_plan", "add_action_log", "complete_plan", "update_plan", "reschedule_plan"}
            and re.search(r"(?:想|感觉|觉得|应该|也许|可能|有空|抽空)", text)
            and not re.search(r"(?:帮我|请|加入|添加|记录|标记|改成|改到|执行)", text)
        ):
            needs_confirmation = True
        # ── Structured semantic annotation ─────────────────────────
        _subject = str(data.get("subject", "") or "").strip().lower()
        subject = _subject if _subject in {"self", "other"} else ""
        _polarity = str(data.get("polarity", "") or "").strip().lower()
        polarity = _polarity if _polarity in {"positive", "negative"} else ""
        _modality = str(data.get("modality", "") or "").strip().lower()
        modality = _modality if _modality in {"commitment", "desire", "hypothetical", "question"} else ""
        request_mode_value = str(data.get("request_mode", "") or "").strip().lower()
        request_mode = request_mode_value if request_mode_value in {
            "query", "execute", "possible_action", "advice", "discuss"
        } else (
            "query" if mode == "read" else "discuss" if mode == "chat" else "possible_action"
        )
        explicit_value = data.get("explicit_command", False)
        if not isinstance(explicit_value, bool):
            return self._reject("schema_invalid_explicit_command")
        explicit_command = bool(explicit_value)
        if mode != "write":
            explicit_command = False
        if explicit_command and request_mode != "execute":
            if (
                subject == "self"
                and polarity == "positive"
                and modality == "commitment"
            ):
                # Repair one internally contradictory model annotation without
                # reading keywords from the user text.  A positive commitment
                # plus an explicit command is executable by definition; desire,
                # hypothetical and question modalities remain non-writing.
                request_mode = "execute"
                self._note("explicit_commitment_request_mode_repaired")
            else:
                return self._reject("explicit_command_requires_execute_mode")

        return {
            "intent": intent,
            "confidence": max(0.0, min(confidence, 1.0)),
            "entities": entities,
            "needs_confirmation": needs_confirmation,
            "source": "llm",
            "warnings": [str(item)[:120] for item in data.get("warnings", [])[:5]]
            if isinstance(data.get("warnings", []), list)
            else [],
            "clarification_question": clarification_question,
            "candidate_actions": candidate_actions,
            "mode": mode,
            "proposed_tool": proposed_tool or None,
            "follow_up_target": str(follow_up_target or "").strip() or None,
            "semantic_diagnostic": self.last_diagnostic,
            "subject": subject,
            "polarity": polarity,
            "modality": modality,
            "request_mode": request_mode,
            "explicit_command": explicit_command,
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

    def route_exact_command(
        self,
        user_text: str,
        context: Optional[Dict[str, object]] = None,
        *,
        payload_text: Optional[str] = None,
    ) -> Optional[Dict[str, object]]:
        """Recognize only non-conversational commands owned by the program.

        Natural-language business requests deliberately do not enter this gate.
        They are handled by ``route_semantic_decision`` so a keyword cannot
        become an accidental plan, memory, or pet action.
        """
        literal = str(user_text or "").strip()
        if literal.startswith(("忘记：", "忘记:")):
            return self._result(
                "delete_memory", 1.0,
                entities={"forget_content": literal[3:].strip()},
                needs_confirmation=True, source="fixed_command",
                request_mode="execute",
            )
        original_text = _clean_text(user_text)
        text = _normalize_utterance_shell(original_text)
        if not text:
            return None
        if self.memory_query_guard.is_retired_candidate_request(text):
            return self._result(
                "chat",
                1.0,
                source="retired_memory_candidate",
            )
        protected_payload = str(payload_text or "").strip()
        if protected_payload:
            envelope = self._route_payload_command(text, protected_payload)
            if envelope is not None:
                return self._enrich_entities(text, envelope, context or {})
        if text == "跳舞":
            return self._result("dance", 1.0, source="exact_command")
        reminder_action = reminder_control_action(original_text)
        if reminder_action == "pause":
            return self._result(
                "reminder_control",
                1.0,
                entities={"action": "pause"},
                source="fixed_command",
                request_mode="execute",
            )
        if reminder_action == "resume":
            return self._result(
                "reminder_control",
                1.0,
                entities={"action": "resume"},
                source="fixed_command",
                request_mode="execute",
            )
        if is_explicit_pet_sleep_request(original_text):
            return self._result(
                "sleep_pet",
                1.0,
                source="fixed_command",
                request_mode="execute",
            )
        if _FIXED_REVIEW_SAVE.fullmatch(text):
            result = self._matched("save_review")
            return self._enrich_entities(text, result, context or {})
        if re.fullmatch(r"重新生成(?:昨天|昨日)(?:的)?(?:复盘|成长复盘)", text):
            result = self._matched("save_review")
            return self._enrich_entities(text, result, context or {})
        return None

    def route_semantic_decision(
        self,
        user_text: str,
        context: Optional[Dict[str, object]] = None,
        *,
        allow_llm: bool = True,
        payload_text: Optional[str] = None,
    ) -> Dict[str, object]:
        """The main route: exact program commands or one model decision.

        A model-unavailable turn intentionally becomes chat rather than falling
        through to broad keyword rules.  The old ``route`` method remains as a
        compatibility API until the later cleanup commit, but ConversationService
        no longer uses it for the normal natural-language path.
        """
        if str(user_text or "").strip().startswith(("忘记：", "忘记:")):
            # Preserve punctuation and empty literal payloads before utterance
            # normalization. Exact maintenance never needs model fallback.
            exact_forget = self.route_exact_command(user_text, context)
            if exact_forget is not None:
                return exact_forget
        original_text = _clean_text(user_text)
        text = _normalize_utterance_shell(original_text)
        if not original_text:
            return self._fallback("empty")
        # Addressed pet actions carry meaning in the addressee itself.  Check
        # them before the generic utterance shell removes “洛琪希/你”.
        if is_explicit_pet_sleep_request(original_text):
            return self._result(
                "sleep_pet",
                1.0,
                source="fixed_command",
                request_mode="execute",
            )
        if self.memory_query_guard.is_retired_candidate_request(text):
            return self._result(
                "chat",
                1.0,
                source="retired_memory_candidate",
            )
        exact = self.route_exact_command(
            text, context, payload_text=payload_text
        )
        if exact is not None:
            print(f"[ExactCommand] matched: {exact['intent']}", flush=True)
            return exact
        review_query = self._route_daily_review_query(text)
        if review_query:
            print(
                "[SemanticDecision] source=business_read_guard "
                "intent=daily_review",
                flush=True,
            )
            return self._result(
                "daily_review",
                0.99,
                source="business_read_guard",
            )
        fixed_read = self._route_fixed(text)
        if fixed_read is not None and _mode_for_intent(
            str(fixed_read.get("intent", "chat"))
        ) == "read":
            return {**fixed_read, "source": "business_read_guard"}
        stable_read = self._route_stable_business_query(text)
        if stable_read is not None:
            print(
                "[SemanticDecision] source=business_read_guard "
                f"intent={stable_read}",
                flush=True,
            )
            return self._result(
                stable_read,
                0.98,
                source="business_read_guard",
            )
        history_query = self._route_history_query(text)
        if history_query is not None:
            intent = str(history_query["intent"])
            print(
                "[SemanticDecision] source=history_query_guard "
                f"intent={intent}",
                flush=True,
            )
            return self._result(
                intent,
                0.98,
                entities=dict(history_query.get("entities", {})),
                source="history_query_guard",
            )
        formal_memory_query = self.memory_query_guard.route(text)
        if (
            formal_memory_query is not None
            and str(formal_memory_query.get("intent", "")) == "show_memory"
        ):
            confidence = float(formal_memory_query.get("confidence", 0.96))
            print(
                "[SemanticDecision] source=memory_query_guard "
                "intent=show_memory",
                flush=True,
            )
            return self._result(
                "show_memory",
                confidence,
                entities=dict(formal_memory_query.get("entities", {})),
                needs_confirmation=False,
                source="memory_query_guard",
                clarification_question=formal_memory_query.get(
                    "clarification_question"
                ),
            )
        if allow_llm and self.enable_llm and self.llm_parser is not None:
            parsed = self.llm_parser.parse(text, context or {})
            if parsed is not None:
                parsed = self._validate_preference_memory_decision(text, parsed)
                print(f"[SemanticDecision] source=llm intent={parsed['intent']}", flush=True)
                return self._enrich_entities(
                    text, self._with_legacy_slots(parsed), context or {}
                )
        print("[SemanticDecision] source=unavailable intent=chat", flush=True)
        fallback = self._fallback("semantic_unavailable")
        if self.llm_parser is not None:
            fallback["semantic_diagnostic"] = str(
                self.llm_parser.last_diagnostic or "semantic_unavailable"
            )
        return fallback

    def _validate_preference_memory_decision(
        self,
        text: str,
        decision: Dict[str, object],
    ) -> Dict[str, object]:
        """Validate model-selected implicit preference writes without creating one."""
        if str(decision.get("intent", "")) != "add_memory_request":
            return decision
        if bool(decision.get("explicit_command", False)):
            return decision
        category = self._route_memory_candidate(text)
        if not category:
            return {
                **decision,
                "intent": "chat",
                "mode": "chat",
                "entities": {},
                "proposed_tool": None,
                "needs_confirmation": False,
                "request_mode": "discuss",
                "explicit_command": False,
            }
        # The current user message is the only trusted source for an implicit
        # memory suggestion.  A model-provided paraphrase must never become the
        # value written after confirmation.
        entities = decision.get("entities", {})
        entities = dict(entities) if isinstance(entities, dict) else {}
        entities["category"] = category
        entities["content"] = self._preference_content(text, category)
        return {
            **decision,
            "entities": entities,
            "implicit_memory_policy": (
                "suppress_sensitive"
                if category in {"health_lifestyle"}
                else "confirm_before_save"
            ),
        }

    def route(
        self,
        user_text: str,
        context: Optional[Dict[str, object]] = None,
        *,
        allow_llm: bool = True,
        payload_text: Optional[str] = None,
    ) -> Dict[str, object]:
        original_text = _clean_text(user_text)
        text = _normalize_utterance_shell(original_text)
        print(f"[Intent] input_chars={len(original_text)}", flush=True)
        if not original_text:
            return self._fallback("empty")
        if self.memory_query_guard.is_retired_candidate_request(text):
            return self._result(
                "chat",
                1.0,
                source="retired_memory_candidate",
            )
        protected_payload = str(payload_text or "").strip()
        if protected_payload:
            envelope_result = self._route_payload_command(text, protected_payload)
            if envelope_result is not None:
                return self._enrich_entities(text, envelope_result, context or {})
        exact = self.route_exact_command(text, context or {})
        if exact is not None:
            return exact
        if is_fixed_command(text):
            fixed = self._route_fixed(text)
            return self._enrich_entities(
                text,
                fixed or self._result("chat", 1.0, source="fixed_command"),
                context or {},
            )

        if allow_llm and self.enable_llm and self.llm_parser is not None:
            parsed = self.llm_parser.parse(text, context or {})
            if parsed is not None:
                print(f"[Intent] source: llm", flush=True)
                print(f"[Intent] matched: {parsed['intent']} confidence={parsed['confidence']:.2f}", flush=True)
                return self._enrich_entities(
                    text, self._with_legacy_slots(parsed), context or {}
                )
        result = self._route_rules(text, context or {})
        if result is not None:
            return self._enrich_entities(text, result, context or {})
        return self._fallback("no_rule")

    def _route_payload_command(
        self,
        command_text: str,
        payload_text: str,
    ) -> Optional[Dict[str, object]]:
        """Route an explicit envelope without inspecting user-provided payload words."""
        command = _normalize_match_text(command_text)
        payload = str(payload_text).strip()
        if not command or not payload:
            return None
        if command == "忘记":
            return self._result(
                "delete_memory", 1.0,
                entities={"forget_content": payload},
                needs_confirmation=True, source="fixed_command",
                request_mode="execute",
            )
        if command in {"记住", "帮我记住", "请记住", "记得", "保存记忆", "保存长期记忆"}:
            return self._result(
                "add_memory_request",
                1.0,
                entities={"content": payload, "source_text": payload},
                source="command_envelope",
            )
        if re.fullmatch(r"(?:把)?(?:加到|加入|加进|添加到|放进)(?:我)?(?:今天)?(?:的)?(?:计划|任务|要做的事)(?:里)?", command_text):
            return self._result(
                "add_plan",
                1.0,
                entities={"tasks": [payload]},
                source="command_envelope",
            )
        if re.fullmatch(r"(?:记录(?:一下)?|记(?:一下|一笔)|记到(?:今天的)?行动(?:记录)?里)", command_text):
            return self._result(
                "add_action_log",
                1.0,
                entities={"content": payload},
                source="command_envelope",
            )
        return None

    def _route_rules(
        self,
        text: str,
        context: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        if re.search(r"(?:不要|别|不用).{0,8}(?:删除|归档|恢复|执行)", text):
            return None
        if re.search(r"(?:怎么|如何|怎样).{0,8}(?:删除|归档|恢复)", text):
            return None

        # A status question can contain 完成 but never asks to complete a plan.
        # This is a safe deterministic fallback only when the semantic model is
        # unavailable; normal natural-language routing reaches the LLM first.
        if _is_plan_status_query(text):
            return self._matched("show_plan", status_filter=(
                "completed" if re.search(r"已经.*(?:做完|完成)|哪些.*(?:做完|完成)", text)
                else "pending"
            ))

        if self._route_daily_review_query(text):
            return self._matched("daily_review")

        history_query = self._route_history_query(text)
        if history_query is not None:
            return self._matched(
                str(history_query["intent"]),
                **dict(history_query.get("entities", {})),
            )

        stable_query = self._route_stable_business_query(text)
        if stable_query is not None:
            return self._matched(stable_query)

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
        if is_explicit_pet_sleep_request(text):
            return self._matched("sleep_pet")
        if _matches_any(text, ("唤醒", "醒一醒", "起来吧")):
            return self._matched("wake_pet")
        reminder_action = reminder_control_action(text)
        if reminder_action:
            return self._matched("reminder_control", action=reminder_action)
        if _matches_any(text, ("等会儿再说", "晚点提醒我", "今天不想学了")):
            return self._matched("reminder_control", action="pause")

        memory_view = self._route_memory_view(text)
        if memory_view:
            return self._matched(memory_view["intent"], **memory_view.get("entities", {}))
        contextual_memory = self._route_contextual_memory_request(text, context)
        if contextual_memory is not None:
            return contextual_memory
        memory_target = _first_capture(
            text,
            (
                r"^(?:把)?(.+?)(?:放进|加入|加进|保存到|存入)(?:我的)?(?:长期)?记忆(?:里)?$",
            ),
        )
        if memory_target:
            return self._matched("add_memory_request", content=memory_target)
        memory_request = self._route_memory(text)
        if memory_request:
            return self._matched("add_memory_request", content=memory_request)
        # Ordinary statements stay in chat.  Automatic discovery keeps using the
        # candidate service from its dedicated state/governance paths instead of
        # making every preference-like sentence a chat interruption.

        if _matches_any(text, ("保存今天的复盘", "把这个复盘存下来", "记入成长日志")):
            return self._matched("save_review")
        if _matches_any(text, ("查看成长日志", "看看成长日志", "最近的成长日志", "查看本月成长日志", "看看本月成长日志")):
            return self._matched("show_growth_log")
        if _matches_any(text, ("查看记录", "看看行动记录", "今天记录了什么")):
            return self._matched("show_action_log")
        if _matches_any(text, (
            "我今天还有什么没做", "今天还有什么没做", "看看今天任务", "看看今天的任务",
            "我今天要做什么", "今天要做什么", "看看我的计划", "看看计划",
            "看看今天安排了什么", "我今天还有哪些任务",
        )):
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
        if re.fullmatch(r"重新生成(?:昨天|昨日)(?:的)?(?:复盘|成长复盘)", text):
            return self._enrich_entities(
                text,
                self._matched("save_review"),
                {},
            )
        exact = {
            "查看计划": ("show_plan", {}),
            "今日计划": ("show_plan", {}),
            "明日计划": ("show_plan", {}),
            "我的计划": ("show_plan", {}),
            "今天的计划": ("show_plan", {}),
            "列出今日计划": ("show_plan", {}),
            "今天要做什么": ("show_plan", {}),
            "我的行动记录": ("show_action_log", {}),
            "查看记录": ("show_action_log", {}),
            "今日复盘": ("daily_review", {}),
            "复盘一下": ("daily_review", {}),
            "今天完成了什么": ("daily_review", {}),
            "保存今日复盘": ("save_review", {}),
            "查看成长日志": ("show_growth_log", {}),
            "查看本月成长日志": ("show_growth_log", {}),
            "看看本月成长日志": ("show_growth_log", {}),
            "我的记忆": ("show_memory", {}),
            "查看长期记忆": ("show_memory", {}),
            "查看待确认记忆": ("show_memory", {"query_mode": "overview"}),
            "查看待审核记忆": ("show_memory", {"query_mode": "overview"}),
            "把待确认的都确认": ("accept_all_memory_candidates", {}),
            "确认全部待审核记忆": ("accept_all_memory_candidates", {}),
            "查看记忆冲突": ("show_memory_conflicts", {}),
            "查看已归档记忆": ("show_archived_memories", {}),
        }
        if text in exact:
            intent, entities = exact[text]
            return self._result(intent, 1.0, entities=entities, source="fixed_command")
        if text.startswith("我完成了") and text[len("我完成了"):].strip():
            return self._result(
                "complete_plan",
                1.0,
                entities={"query": text[len("我完成了"):].strip()},
                source="fixed_command",
            )
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

        # A generic word such as “确认” inside an unrelated question must not
        # open the retired candidate-memory flow.  Short confirmations remain
        # valid only when a real typed memory interaction is already pending;
        # otherwise the user must explicitly name memory/candidate management.
        has_candidate_context = bool(
            memory_state.get("candidate_ids")
            or memory_state.get("last_candidate_ids")
            or str(memory_state.get("state", "")).startswith("awaiting_candidate")
        )
        explicit_memory_review = bool(
            re.search(r"(?:候选|记忆|待审核|待确认)", text)
        )
        if not has_candidate_context and not explicit_memory_review:
            return None

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
        if re.search(
            r"(?:记得|知道|了解).{0,6}我.{0,6}(?:什么|哪些|多少)",
            text,
        ):
            return {"intent": "show_memory", "entities": {}}
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
            return {
                "intent": "show_memory",
                "entities": {"query_mode": "overview", "query": text},
            }
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
                r"^(?:以后)?记得[：:，,\s]*(.+)$",
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
        # A compliment about the pet performing an action is not a stable user
        # preference and must remain ordinary chat.
        if re.fullmatch(r"^我(?:更)?喜欢你(?:跳舞|表演|展示)(?:.+)?$", text):
            return ""
        if re.fullmatch(r"^(?:以后\s*)?codex\s*提示词不要.+$|^roxyplan\s*不要.+$|^不要自动生图$", text, flags=re.IGNORECASE):
            return "project_preference"
        if re.fullmatch(r"^我以后想(?:做|成为|把).+$|^我想成为.+$|^我想把.+(?:长期|一直).*(?:做下去|坚持下去)$", text, flags=re.IGNORECASE):
            return "long_term_goal"
        if re.fullmatch(r"^我(?:肠胃|胃|睡眠|身体|皮肤)(?:比较|很|有点|不太|容易).+$|^我不太适合吃.+$|^我晚上容易.+$", text):
            return "health_lifestyle"
        if re.fullmatch(r"^我一般.+(?:效率|习惯|适合|会).*$|^我周末适合.+$", text):
            return "stable_habit"
        match = re.fullmatch(
            r"^我(?:平时)?(?:更)?喜欢\s*(.+)$|^我不喜欢\s*(.+)$|^我更适合(?:用|在)?\s*(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if match and len(_clean_slot(next(item for item in match.groups() if item is not None))) >= 2:
            return "user_preference"
        return ""

    def _route_preference_to_memory(
        self, text: str
    ) -> Optional[Dict[str, object]]:
        """Route clear self-preference statements to formal memory save.

        Reuses :meth:`_route_memory_candidate` for category detection,
        then converts affirmative categories into an add_memory_request
        intent result.  Negations and pet compliments are excluded.
        """
        # Pet compliments about the assistant — ordinary chat, never a
        # user preference.  Covers: 我喜欢看你跳舞, 我喜欢你跳舞, etc.
        if re.fullmatch(
            r"^我(?:更)?喜欢(?:看|听)?你(?:跳舞|表演|展示|唱歌|说话)(?:.+)?$", text
        ):
            return None
        # Negated preferences — chat
        if re.fullmatch(
            r"^我不喜欢\s*.+$|^我不太喜欢\s*.+$|^我不适合\s*.+$",
            text,
            re.IGNORECASE,
        ):
            return None
        # Hypotheticals and quotes of others — chat
        if re.search(
            r"^(?:如果|要是|假如|假设|听说|据说|他说|她说|有人说)", text
        ):
            return None

        category = self._route_memory_candidate(text)
        if not category:
            return None

        # Derive content from the matched pattern
        content = self._preference_content(text, category)
        if not content or len(_clean_slot(content)) < 2:
            return None

        return self._result(
            "add_memory_request",
            1.0,
            entities={
                "content": content,
                "category": category,
                "source_text": text,
            },
            source="preference_guard",
        )

    @staticmethod
    def _preference_content(text: str, category: str) -> str:
        """Extract the content body from a preference statement."""
        # For goal/health/habit categories the full text is the content
        if category in {"goal", "health_lifestyle", "stable_habit",
                         "project_preference"}:
            return str(text).strip()
        # For user_preference, extract the object of 喜欢/更适合
        match = re.fullmatch(
            r"^我(?:平时)?(?:更)?喜欢\s*(.+)$|^我更适合(?:用|在)?\s*(.+)$",
            str(text),
            re.IGNORECASE,
        )
        if match:
            content = next(
                item for item in match.groups() if item is not None
            )
            return str(content).strip()
        return str(text).strip()

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
                r"^记(?:一下|一笔)[：:\s]*(.+)$",
                r"^把[：:\s]*(.+?)\s*记到行动记录里$",
                r"^(.+?)\s*记到行动记录里$",
            ),
        )

    @staticmethod
    def _route_add_plan(text: str) -> List[str]:
        direct_target = re.fullmatch(
            r"(?:把)?(.+?)(?:加到|加入|加进|添加到|放进)(?:我)?(?:今天)?(?:的)?"
            r"(?:计划|任务|要做的事)(?:里)?",
            text,
        )
        if direct_target:
            item = _clean_slot(direct_target.group(1))
            return [item] if item else []
        explicit_target = re.fullmatch(
            r"(.+?)[，,。；;\s]*(?:把)?(?:这个|这件事|它)?"
            r"(?:放进|加入|加进|添加到)(?:我)?(?:今天)?(?:的)?"
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

        listed = _first_capture(text, (r"^(?:帮我)?安排一下今天[：:]\s*(.+)$", r"^今天要做(?:[一二三四五六七八九十\d]+件事)?[：:]\s*(.+)$"))
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
            topic = _history_topic_query(text)
            return {
                "intent": "show_conversation_history",
                "entities": {
                    **({"query": topic} if topic else {}),
                    "exclude_today": "不是今天" in text,
                },
            }
        return None

    @staticmethod
    def _route_stable_business_query(text: str) -> Optional[str]:
        """Recognize read-only business queries before ambiguous write wording.

        These predicates combine a view operation and a domain object. They are
        intentionally capability-level rules, not full-sentence paraphrase lists.
        """
        view_operation = r"查看|看看|列出|展示|给我.{0,6}(?:看|瞅)"

        # A rejected display clause is a safety boundary, especially when the
        # user is correcting a previous misunderstanding ("不是让你查看计划")
        # or the actual purpose is advice.  Let the semantic model answer that
        # purpose instead of forcing a local read.
        if re.search(
            rf"(?:不要|别|不用|无需|不想|不需要).{{0,10}}(?:{view_operation})|"
            rf"(?:{view_operation}).{{0,10}}(?:不要|别|不用|无需|不需要)|"
            rf"(?<!是)(?:不是|并非)(?:想|要|让你|叫你|请你)?.{{0,8}}(?:{view_operation})|"
            rf"(?:我)?(?:没|没有)(?:想|要|让你|叫你|请你).{{0,8}}(?:{view_operation})",
            text,
        ):
            return None

        # Planning/advice requests can contain both a plan noun and an unrelated
        # interrogative, for example "安排今日计划，上午没什么时间".  They are
        # not reads.  This check is purpose-based so a literal read such as
        # "查看今天安排了什么" remains supported.
        planning_or_advice_request = bool(
            re.search(
                r"(?:帮我|替我|给我).{0,5}(?:安排|规划|制定|拆分|拆解)|"
                r"(?:安排|规划|制定)(?:一下)?(?:今天|今日|明天)(?:的)?(?:计划|任务)|"
                r"(?:怎么|如何|怎样).{0,8}(?:安排|规划|制定|开始|先做)|"
                r"我该.{0,8}(?:做|开始|安排)|(?:建议|推荐).{0,8}(?:计划|任务|先做)",
                text,
            )
        )
        if planning_or_advice_request:
            return None

        explicit_view_request = bool(re.search(view_operation, text))
        write_request = bool(
            re.search(
                r"加入|添加|加进|加到|放进|放到|塞进|写进|写入|纳入|安排到|"
                r"记到|记住|保存|标记|改成|修改|更新|删除|清空|归档|恢复",
                text,
            )
        )
        if write_request:
            return None

        plan_domain = bool(
            re.search(
                r"计划|任务|要做的事|(?:今天|今日|明天|昨天|昨日).{0,8}安排",
                text,
            )
        )
        action_domain = bool(
            re.search(
                r"行动记录|行动日志|进展记录|(?:今天|今日)(?:的)?行动",
                text,
            )
        )

        question_word = r"(?:什么|啥|哪些|哪几个|多少(?:个|项|条)?|几(?:个|项|条))"
        plan_object = r"(?:计划|任务|要做的事|安排)"
        plan_read_question = bool(
            re.search(
                rf"{plan_object}(?:里|中|内)?(?:都|一共)?"
                rf"(?:是|有|包括|包含|还剩)?{question_word}",
                text,
            )
            or re.search(
                rf"(?:今天|今日|明天|昨天|昨日)(?:的)?(?:都|还)?(?:有|还有)"
                rf"{question_word}(?:个|项|条)?{plan_object}",
                text,
            )
            or re.search(
                rf"(?:今天|今日|明天|昨天|昨日)(?:的)?安排了{question_word}",
                text,
            )
        )
        action_read_question = bool(
            re.search(
                rf"(?:行动记录|行动日志|进展记录)(?:里|中|内)?(?:都|一共)?"
                rf"(?:是|有|包括|包含|还剩)?{question_word}",
                text,
            )
        )

        if (
            re.search(r"重复|相同|相近|一样|雷同", text)
            and plan_domain
            and (
                explicit_view_request
                or re.search(r"有没有|是否|哪些|什么|多少|吗", text)
            )
        ):
            return "inspect_plan_duplicates"
        if plan_domain and (explicit_view_request or plan_read_question):
            return "show_plan"
        if action_domain and (explicit_view_request or action_read_question):
            return "show_action_log"
        return None

    @staticmethod
    def _route_daily_review_query(text: str) -> bool:
        """Recognize reflective reads that need plans and action facts together."""
        normalized = _normalize_match_text(text)
        if not normalized:
            return False
        if re.search(
            r"加入|添加|放进|记到|标记|改成|修改|更新|删除|清空|归档|保存",
            normalized,
        ):
            return False
        # A plan title may legitimately contain words such as "复盘"、"总结"
        # or "回顾".  When the same sentence also carries a plan-write cue,
        # this read-only guard must step aside and let the semantic action
        # pipeline validate the requested plan operation.
        if (
            re.search(r"(?:计划|任务|待办)", normalized)
            and re.search(r"(?:加|添|放进|记到|安排)", normalized)
        ):
            return False
        if re.search(r"(?:今日|今天).{0,6}(?:复盘|总结|回顾)", normalized):
            return True
        if re.search(r"(?:复盘|总结|回顾).{0,6}(?:今日|今天)", normalized):
            return True
        if re.search(
            r"(?:今日|今天).{0,8}(?:状态|进展|成果|收获).{0,5}(?:怎么样|如何|什么|哪些)",
            normalized,
        ):
            return True
        accomplishment = r"(?:完成(?:了)?|做(?:到|完|成|了)|推进(?:了)?|实现(?:了)?|达成(?:了)?)"
        question = r"(?:什么|哪些|多少|怎么样|如何)"
        if re.search(
            rf"(?:我)?(?:今日|今天).{{0,6}}{accomplishment}.{{0,5}}{question}",
            normalized,
        ):
            return True
        return bool(
            re.search(
                rf"^我(?:都|已经)?{accomplishment}.{{0,4}}{question}(?:了)?$",
                normalized,
            )
        )

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
        last_tool_result = context.get("last_tool_result")
        previous_dance = (
            isinstance(last_tool_result, dict)
            and str(last_tool_result.get("tool", "")) == "play_dance"
        )
        previous = " ".join(
            str(context.get(key, ""))
            for key in ("last_user_message", "last_assistant_message")
        ).lower()
        previous_dance = previous_dance or any(
            term in previous for term in ("跳舞", "舞蹈", "跳一小段", "dance")
        )
        if re.fullmatch(
            r"再(?:跳(?:一次|一遍|一个舞|一支舞|一段舞)?|来(?:一个|一段|一次))",
            lowered,
        ):
            return previous_dance or "舞" in lowered
        if re.fullmatch(
            r"(?:能(?:给我)?|可以(?:给我)?|给我|帮我|请|现在)?(?:开始)?(?:跳|表演|展示)"
            r"(?:一?个|一支|一段|一下)?(?:你的)?(?:舞|舞蹈)?(?:给我)?(?:看看|一下)?(?:吗|么)?"
            r"|(?:给我|现在)?来(?:一?个|一支|一段|段)舞"
            r"|dance\s+for\s+me",
            lowered,
        ):
            return True
        if re.fullmatch(r"能(?:给我)?来一段(?:吗|么)?", lowered):
            return previous_dance
        return False

    @staticmethod
    def _route_delete_plan(text: str) -> str:
        return _first_capture(text, (
            r"^(?:帮我)?(?:把)?(?:计划|任务)\s*(\d+)\s*(?:删掉|删除)(?:了)?$",
            r"^(?:帮我)?把\s*(.+?)(?:这个)?(?:计划|任务)\s*(?:删掉|删除|取消)(?:了)?$",
            r"^(?:帮我)?(?:删除|取消)\s*(?:今天的)?(.+?)(?:计划|任务)?$",
        ))

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
        has_reference = any(
            term in text for term in self.reference_resolver.REFERENCE_TERMS
        )
        task_ref = str(resolution.resolved_id or "").strip()
        if not task_ref and not has_reference:
            task_ref = str(
                last_task.get("uid")
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
                    request_mode="execute",
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
                request_mode="possible_action",
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

        explicit_duration_target = bool(
            re.fullmatch(
                r"(?:把)?(.+?)(?:计划|任务)?(?:的?时长)?(?:改成|调整为)\s*"
                r"\d+(?:\.\d+)?\s*(?:个)?\s*(?:分钟|小时)",
                text,
            )
        )
        if (
            temporal.get("duration_minutes") is not None
            and re.search(r"(?:改成|调整为|设为).*(?:分钟|小时)", text)
            and not explicit_duration_target
            and not re.search(r"[，,；;].*(?:再|然后|同时|顺便|并)", text)
        ):
            if not task_ref:
                return self._matched(
                    "update_plan",
                    clarification_question="你想修改哪一条计划？请说计划编号或标题。",
                )
            return self._matched(
                "update_plan",
                query=task_ref,
                changes={"duration_minutes": temporal["duration_minutes"]},
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
        if intent == "update_plan":
            raw_changes = entities.get("changes")
            changes = dict(raw_changes) if isinstance(raw_changes, dict) else {}
            if parsed.get("duration_minutes") is not None:
                changes.setdefault("duration_minutes", parsed["duration_minutes"])
            parsed_slot = str(parsed.get("time_period", "") or "").strip()
            if parsed_slot:
                changes.setdefault(
                    "time_slot",
                    "晚上" if parsed_slot == "今晚" else parsed_slot,
                )
            if changes:
                entities["changes"] = changes
                if raw_changes is not None and not isinstance(raw_changes, dict):
                    warnings = result.get("warnings", [])
                    warnings = list(warnings) if isinstance(warnings, list) else []
                    if "invalid_changes_recovered_from_user_text" not in warnings:
                        warnings.append("invalid_changes_recovered_from_user_text")
                    result["warnings"] = warnings
            elif raw_changes is not None and not isinstance(raw_changes, dict):
                # The model selected the right operation but did not provide a
                # usable change object, and the user's text contains no stable
                # local value to recover. Preserve the intent as a bounded
                # clarification instead of executing an empty or invented edit.
                entities.pop("changes", None)
                result["mode"] = "clarify"
                result["needs_confirmation"] = False
                result["clarification_question"] = (
                    "这条计划具体要修改标题、时间还是时长？"
                )
                warnings = result.get("warnings", [])
                warnings = list(warnings) if isinstance(warnings, list) else []
                if "invalid_changes_requires_clarification" not in warnings:
                    warnings.append("invalid_changes_requires_clarification")
                result["warnings"] = warnings

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
        request_mode = str(entities.pop("request_mode", "") or "").strip()
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
            request_mode=request_mode,
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
        subject: str = "",
        polarity: str = "",
        modality: str = "",
        request_mode: str = "",
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
        result.update(
            {
                "mode": _mode_for_intent(
                    intent, clarification=bool(clarification_question)
                ),
                "proposed_tool": _proposed_tool_for_intent(intent) or None,
                "follow_up_target": None,
                "subject": str(subject or "").strip(),
                "polarity": str(polarity or "").strip(),
                "modality": str(modality or "").strip(),
                "request_mode": str(request_mode or "").strip(),
            }
        )
        return result

    def _with_legacy_slots(self, parsed: Dict[str, object]) -> Dict[str, object]:
        result = self._result(
            str(parsed["intent"]),
            float(parsed["confidence"]),
            entities=dict(parsed["entities"]),
            needs_confirmation=bool(parsed["needs_confirmation"]),
            source="llm",
            warnings=list(parsed.get("warnings", [])),
            clarification_question=parsed.get("clarification_question"),
            candidate_actions=list(parsed.get("candidate_actions", [])),
            subject=str(parsed.get("subject", "") or ""),
            polarity=str(parsed.get("polarity", "") or ""),
            modality=str(parsed.get("modality", "") or ""),
        )
        result["mode"] = str(parsed.get("mode", result["mode"]))
        result["proposed_tool"] = parsed.get("proposed_tool") or result["proposed_tool"]
        result["follow_up_target"] = parsed.get("follow_up_target")
        result["semantic_diagnostic"] = str(
            parsed.get("semantic_diagnostic", "") or ""
        )
        result["request_mode"] = str(
            parsed.get("request_mode", result.get("request_mode", "")) or ""
        )
        result["explicit_command"] = bool(
            parsed.get("explicit_command", False)
        )
        implicit_memory_policy = str(
            parsed.get("implicit_memory_policy", "") or ""
        ).strip()
        if implicit_memory_policy:
            result["implicit_memory_policy"] = implicit_memory_policy
        return result

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
        or _FIXED_REVIEW_SAVE.fullmatch(text)
        or re.fullmatch(
            r"重新生成(?:昨天|昨日)(?:的)?(?:复盘|成长复盘)", text
        )
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


def _normalize_utterance_shell(text: str) -> str:
    """Remove generic conversational wrapping without deciding semantics.

    This deliberately handles reusable discourse markers only. It must not grow
    into a catalog of complete user sentences or choose a capability/tool.
    """
    value = _clean_text(text)
    if not value:
        return value
    preserve_addressee = bool(
        re.match(
            r"^麻烦你(?:记得|知道|了解)我.*(?:什么|哪些|多少|吗)"
            r"(?:可以吗|吧|谢谢|哈|一下)?$",
            value,
        )
    )
    if preserve_addressee:
        value = value[len("麻烦"):].lstrip("，,：: ")
    previous = None
    while value and value != previous:
        previous = value
        value = re.sub(
            r"^(?:(?:查询|命令)[：:]\s*|洛琪希[，,：:\s]*|麻烦(?:你)?[，,：:\s]*|"
            r"能不能[，,：:\s]*|可不可以[，,：:\s]*|请[，,：:\s]*|"
            r"帮我[，,：:\s]*|现在[，,：:\s]*)",
            "",
            value,
            count=1,
        ).strip()
    value = re.sub(
        r"(?:[，,：:\s]*(?:可以吗|行吗|好吗|谢谢|哈|吧))+$",
        "",
        value,
    ).strip()
    preserve_yixia = bool(re.search(r"(?:复盘|帮我记)一下$", value))
    if not preserve_yixia:
        value = re.sub(r"[，,：:\s]*一下$", "", value).strip()
    return _clean_text(value) or _clean_text(text)


def _first_capture(text: str, patterns: Sequence[str]) -> str:
    for pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_slot(match.group(1))
    return ""


def _clean_slot(text: str) -> str:
    return str(text).strip(" \t\r\n，,。.!！?？：:；;")


def _history_topic_query(text: str) -> str:
    """Extract an optional topic from an already-classified history query."""
    value = _clean_text(text)
    value = re.sub(r"(?:不是今天(?:的)?|不包括今天(?:的)?)", " ", value)
    about = re.search(
        r"(?:关于|有关)\s*(.+?)(?:的)?(?:事|内容|话题|对话|会话)?$",
        value,
    )
    if about:
        value = about.group(1)
    else:
        value = re.sub(r"^.*?(?:以前|之前|过去|更早|历史)", "", value)
        value = re.sub(
            r"^(?:(?:我|我们|咱们)?(?:跟你|和你)?(?:的)?|"
            r"(?:聊天|对话|会话)(?:里|中)?)",
            "",
            value,
        )
        value = re.sub(
            r"^(?:曾经|曾)?(?:聊过|聊到|聊的|说过|说的|提到|提过|"
            r"谈过|谈的|讨论过|讨论的|记得|找找|查找)\s*",
            "",
            value,
        )
    value = re.sub(
        r"^(?:哪些|什么|有没有|是否|都)?\s*(?:关于|有关)?\s*",
        "",
        value,
    )
    value = _clean_slot(value)
    value = re.sub(
        r"(?:的)?(?:事|内容|话题|聊天|对话|会话)?(?:吗|呢|啊|呀)?$",
        "",
        value,
    )
    topic = _clean_slot(value)
    if topic in {"", "什么", "哪些", "多少", "有没有", "跟你", "和你"}:
        return ""
    return topic[:200]


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


def _compact_command_text(text: str) -> str:
    return re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))


def reminder_control_action(text: str) -> str:
    """Return a reminder state change only for a complete explicit request."""
    value = _compact_command_text(text)
    prefix = r"(?:(?:接下来|从现在开始|现在开始|现在|今天|暂时|这段时间)(?:先)?)?"
    if re.fullmatch(
        rf"{prefix}(?:请)?(?:先)?(?:(?:别|不要|不用)(?:再)?提醒我(?:了)?|暂停(?:主动)?提醒)",
        value,
    ):
        return "pause"
    if re.fullmatch(
        rf"{prefix}(?:请)?(?:恢复提醒|继续提醒我|重新开启提醒|恢复主动提醒)",
        value,
    ):
        return "resume"
    return ""


def is_explicit_pet_sleep_request(text: str) -> bool:
    """Keep user-rest chat separate from commands addressed to the pet."""
    value = _compact_command_text(text)
    if value in {"进入睡眠", "睡一会儿", "去睡吧"}:
        return True
    return bool(
        re.fullmatch(
            r"(?:洛琪希(?:你)?|roxy(?:你)?|你)(?:先|去)?(?:休息一下|休息一会儿|睡一会儿|睡觉吧|进入睡眠)",
            value,
            flags=re.IGNORECASE,
        )
    )


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
    suggestion_snapshot = context.get("suggestion_snapshot", {})
    suggestion_snapshot = (
        suggestion_snapshot if isinstance(suggestion_snapshot, dict) else {}
    )
    raw_objects = suggestion_snapshot.get("objects", [])
    suggestion_objects = []
    if isinstance(raw_objects, list):
        for item in raw_objects[:5]:
            if not isinstance(item, dict):
                continue
            suggestion_objects.append(
                {
                    key: item.get(key)
                    for key in ("stable_id", "display_order", "title", "consumed")
                    if key in item
                }
            )
    return {
        "last_task": task or None,
        "last_user_message": str(context.get("last_user_message", ""))[-180:],
        "last_assistant_message": str(context.get("last_assistant_message", ""))[-240:],
        "current_facts": (
            context.get("current_facts", {})
            if isinstance(context.get("current_facts", {}), dict)
            else {}
        ),
        "suggestion_snapshot": (
            {
                "snapshot_id": str(suggestion_snapshot.get("snapshot_id", "")),
                "source_kind": str(suggestion_snapshot.get("source_kind", "")),
                "objects": suggestion_objects,
            }
            if suggestion_objects
            else None
        ),
    }


def _mode_for_intent(intent: str, *, clarification: bool = False) -> str:
    if clarification:
        return "clarify"
    if intent == "chat":
        return "chat"
    if intent in {
        "show_plan", "inspect_plan_duplicates", "show_action_log", "show_growth_log", "daily_review",
        "show_memory", "search_memory", "show_memory_candidates",
        "show_memory_conflicts", "show_memory_audit", "show_archived_memories",
        "show_recent_conversation", "show_conversation_history",
    }:
        return "read"
    return "write"


def _proposed_tool_for_intent(intent: str) -> str:
    # This is an explanatory, untrusted proposal. AgentPlanner still owns the
    # canonical intent-to-tool mapping and ToolExecutor still validates execution.
    return {
        "show_plan": "show_plan",
        "inspect_plan_duplicates": "inspect_plan_duplicates",
        "show_action_log": "show_action_log",
        "show_growth_log": "show_growth_log",
        "show_memory": "list_memories",
        "search_memory": "search_memories",
        "add_plan": "add_plan",
        "complete_plan": "complete_plan",
        "add_action_log": "add_action_log",
        "add_memory_request": "save_formal_memory",
    }.get(intent, "")


def _is_plan_status_query(text: str) -> bool:
    return bool(
        re.fullmatch(r"(?:我)?今天(?:还有)?什么没完成", text)
        or re.fullmatch(r"(?:哪些|什么)已经(?:做完|完成)(?:了)?", text)
        or re.fullmatch(r"(?:我)?今天是不是已经学完.+", text)
    )


def _validate_llm_entities(
    intent: str,
    entities: Dict[str, object],
    *,
    allow_incomplete: bool = False,
) -> bool:
    """Basic structural guard only.  Field-level validation is now owned by
    the SemanticPipeline (normalize → validate → repair).

    This function MUST NOT reject a decision based on missing or unexpected
    field names — the pipeline's SchemaValidator, informed by the
    ToolRegistry, is the single source of truth for per-tool contracts.
    """
    if not isinstance(entities, dict):
        return False
    # Field-level shape validation is deferred to the semantic pipeline. A
    # malformed nested field must not erase an otherwise usable intent before
    # locally parsed user-text facts have had a chance to repair or clarify it.
    return True


def _parse_plan_changes(text: str) -> Dict[str, object]:
    value = _clean_slot(text)
    if re.fullmatch(
        r"(?:半小时|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:个)?小时半?|"
        r"(?:\d+|[一二两三四五六七八九十]+)分钟)",
        value,
    ):
        # ChineseEntityParser adds the normalized duration later. Returning no
        # title here prevents a duration-only update from renaming the task.
        return {}
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
