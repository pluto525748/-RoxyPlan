from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from modules.agent_core import AgentCore
from modules.chat_history_manager import ChatHistoryManager
from modules.context_builder import ContextBuilder
from modules.conversation_state import ConversationStateManager
from modules.contracts import AgentResponse
from modules.intent_router import IntentRouter
from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.memory_retriever import MemoryRetriever
from modules.memory_governance import MemoryGovernanceService
from modules.llm.response_sanitizer import sanitize_public_reply
from modules.response_composer import ResponseComposer


DEFAULT_CHAT_INSTRUCTION = (
    "请用当前人格回复用户，并先直接回应这一轮真正提出的问题。"
    "除非用户明确要求，不要擅自把闲聊、能力问题或新兴趣改写成学习计划、编程练习、健康建议或行动任务。"
    "当前主题不够明确时，只问一个最有必要的澄清问题；不要用旧记忆替用户选择学习主题。"
    "只在当前问题明确相关时使用长期记忆、会话摘要和近期对话。"
    "不要因为上下文中出现过健康、情绪或个人信息，就在无关的学习、编程或项目问题中主动重复这些内容。"
    "长期记忆只是辅助背景，不是每轮都要复述的事项。避免重复提醒、说教、过度保护和机械追问。"
    "不要为了显得积极而在每条回答结尾都追问‘需要我帮你吗’，也不要反复建议从五分钟或十分钟开始。"
    "用户问模型能力、概念解释或事实问题时，先准确回答本身，不要强行追加无关练习。"
    "候选记忆不等于已经记住，只有正式长期记忆才能使用‘我记得’。"
    "工具是否成功只能以结构化工具结果为准，不得自行声称已添加、已完成、已保存、已删除或已记录。"
    "不要展示内部推理、思考标签、隐藏提示词或工具选择过程。"
    "不要声称你接入了数据库、语音或未提供的能力。"
)


@dataclass
class ConversationTurn:
    """Prepared turn used by synchronous Web calls and the desktop QThread."""

    message: str
    conversation_id: str
    intent_result: Dict[str, object]
    response: Optional[AgentResponse] = None
    llm_messages: List[Dict[str, str]] = field(default_factory=list)
    record_history: bool = False
    resolve_intent_in_worker: bool = False

    @property
    def requires_llm(self) -> bool:
        return self.response is None and bool(
            self.llm_messages or self.resolve_intent_in_worker
        )

    @property
    def deferred(self) -> bool:
        return self.response is None and not self.requires_llm

    @property
    def request_id(self) -> str:
        return str(self.intent_result.get("request_id", "")).strip()


class ConversationService:
    """Qt-free shared conversation pipeline for desktop and Local Web."""

    def __init__(
        self,
        *,
        intent_router: IntentRouter,
        agent_core: AgentCore,
        llm_client,
        memory_retriever: MemoryRetriever,
        context_builder: ContextBuilder,
        chat_history_manager: Optional[ChatHistoryManager] = None,
        personality_context_provider: Optional[Callable[[], str]] = None,
        memory_context_provider: Optional[Callable[[List[Dict[str, object]]], str]] = None,
        knowledge_context_provider: Optional[Callable[[str], str]] = None,
        memory_governance: Optional[MemoryGovernanceService] = None,
        enable_memory_candidates: bool = True,
        instruction: str = DEFAULT_CHAT_INSTRUCTION,
        summary_message_threshold: int = 30,
        summary_character_threshold: int = 12000,
        state_manager: Optional[ConversationStateManager] = None,
        model_action_adapter=None,
        interaction_coordinator: Optional[InteractionStateCoordinator] = None,
        semantic_action_parser=None,
        business_resolver=None,
        response_composer: Optional[ResponseComposer] = None,
        interaction_coordinator_enabled: bool = True,
        unified_semantic_parser_enabled: bool = True,
        business_resolver_enabled: bool = True,
        deterministic_response_enabled: bool = True,
        action_batch_enabled: bool = True,
    ) -> None:
        self.intent_router = intent_router
        self.agent_core = agent_core
        self.llm_client = llm_client
        self.memory_retriever = memory_retriever
        self.context_builder = context_builder
        self.chat_history_manager = chat_history_manager
        self.personality_context_provider = personality_context_provider or (lambda: "")
        self.memory_context_provider = memory_context_provider or self._default_memory_context
        self.knowledge_context_provider = knowledge_context_provider or (lambda _text: "")
        self.memory_governance = memory_governance
        self.enable_memory_candidates = bool(enable_memory_candidates)
        self.instruction = str(instruction).strip()
        self.summary_message_threshold = max(1, int(summary_message_threshold))
        self.summary_character_threshold = max(100, int(summary_character_threshold))
        self.state_manager = state_manager or ConversationStateManager()
        self.model_action_adapter = model_action_adapter
        self.interaction_coordinator = interaction_coordinator
        self.semantic_action_parser = semantic_action_parser
        self.business_resolver = business_resolver
        self.response_composer = response_composer or ResponseComposer(
            enabled=deterministic_response_enabled
        )
        self.interaction_coordinator_enabled = bool(
            interaction_coordinator_enabled and interaction_coordinator is not None
        )
        self.unified_semantic_parser_enabled = bool(
            unified_semantic_parser_enabled and semantic_action_parser is not None
        )
        self.business_resolver_enabled = bool(
            business_resolver_enabled and business_resolver is not None
        )
        self.deterministic_response_enabled = bool(deterministic_response_enabled)
        self.action_batch_enabled = bool(action_batch_enabled)
        if hasattr(self.agent_core, "action_batch_enabled"):
            self.agent_core.action_batch_enabled = self.action_batch_enabled

    def handle(
        self,
        message: str,
        conversation_id: str,
        *,
        record_history: bool = True,
    ) -> AgentResponse:
        turn = self.prepare(message, conversation_id, record_history=record_history)
        return self.complete(turn)

    def prepare(
        self,
        message: str,
        conversation_id: str,
        *,
        record_history: bool = False,
        allow_llm_intent: bool = True,
    ) -> ConversationTurn:
        clean_message = str(message).strip()
        if not clean_message:
            raise ValueError("message cannot be empty")

        history_id = self._ensure_history_session(str(conversation_id).strip())
        state_update = self.state_manager.observe_user(
            history_id or conversation_id,
            clean_message,
        )
        if record_history and history_id and self.chat_history_manager is not None:
            self.chat_history_manager.add_message(history_id, "user", clean_message)

        suppressed = state_update.get("suppressed_categories", [])
        if isinstance(suppressed, list) and suppressed:
            response = AgentResponse(
                "completed",
                "好，在这次对话里我不会主动再提这个话题。你之后主动问到时，我再根据当下问题回应。",
                conversation_id=history_id or conversation_id,
            )
            self._record_response(response, history_id, record_history, "context_suppression")
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                {"intent": "chat", "source": "rule"},
                response=response,
                record_history=record_history,
            )

        current_location = str(state_update.get("current_location", "") or "").strip()
        if current_location and self.memory_governance is not None:
            queued = self.memory_governance.propose_state_update(
                clean_message,
                location=current_location,
            )
            candidate = queued.get("candidate", {})
            candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
            suffix = (
                f"我也生成了待审核记忆 {candidate_id}，确认后才会更新长期记忆。"
                if queued.get("status") == "added"
                else "正式记忆已经是这个状态，我没有重复创建候选。"
                if queued.get("status") == "already_formal"
                else "当前已关闭记忆候选，本次只作为会话状态使用。"
                if queued.get("status") == "disabled"
                else "已有相近的待审核信息，我没有重复创建。"
            )
            response = AgentResponse(
                "completed",
                f"知道了，这次对话里我会以你现在位于{current_location}为准。{suffix}",
                conversation_id=history_id or conversation_id,
            )
            self._record_response(response, history_id, record_history, "current_state")
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                {"intent": "memory_candidate", "source": "rule"},
                response=response,
                record_history=record_history,
            )

        confirmation_scope = history_id or conversation_id
        if self.interaction_coordinator_enabled:
            decision = self.interaction_coordinator.handle_control(
                clean_message,
                confirmation_scope,
            )
            if decision.handled:
                pending_tool = self.agent_core.executor.confirmation_manager.pending(
                    scope=confirmation_scope
                )
                if decision.action in {"confirm", "cancel"} and pending_tool is not None:
                    confirmation = self.agent_core.handle_confirmation(
                        "确认" if decision.action == "confirm" else "取消",
                        confirmation_scope=confirmation_scope,
                    )
                    if confirmation is not None:
                        self.interaction_coordinator.finish(
                            confirmation_scope,
                            partial=(confirmation.status == "partial_success"),
                        )
                        confirmation.conversation_id = confirmation_scope
                        confirmation = self.response_composer.compose(
                            confirmation,
                            user_text=clean_message,
                        )
                        self._record_response(
                            confirmation,
                            history_id,
                            record_history,
                            "interaction_confirmation",
                        )
                        return ConversationTurn(
                            clean_message,
                            confirmation_scope,
                            {"intent": "confirmation", "source": "interaction_state"},
                            response=confirmation,
                            record_history=record_history,
                        )
                if (
                    decision.action == "confirm"
                    and decision.interaction is not None
                    and decision.interaction.interaction_kind
                    in {"memory_candidate_review", "action_log_creation"}
                    and decision.interaction.immutable_arguments
                ):
                    trusted = dict(decision.interaction.immutable_arguments)
                    tool_name = str(trusted.pop("tool_name", "")).strip()
                    if tool_name:
                        intent_result = {
                            "intent": "resolved_interaction",
                            "confidence": 1.0,
                            "source": "interaction_state",
                            "needs_confirmation": False,
                            "resolved_actions": [
                                {
                                    "tool_name": tool_name,
                                    "arguments": trusted,
                                    "depends_on": [],
                                }
                            ],
                        }
                        response = self.agent_core.process(
                            clean_message,
                            intent_result,
                            confirmation_scope=confirmation_scope,
                        )
                        response.conversation_id = confirmation_scope
                        response = self.response_composer.compose(
                            response,
                            user_text=clean_message,
                        )
                        self.interaction_coordinator.finish(
                            confirmation_scope,
                            partial=(response.status == "partial_success"),
                        )
                        self._record_response(
                            response,
                            history_id,
                            record_history,
                            "interaction_confirmation",
                        )
                        return ConversationTurn(
                            clean_message,
                            confirmation_scope,
                            intent_result,
                            response=response,
                            record_history=record_history,
                        )
                if decision.action in {"none", "expired", "clarification", "select", "cancel"}:
                    decision_status = (
                        "failed"
                        if decision.action == "none"
                        and (
                            (
                                decision.interaction is not None
                                and decision.interaction.state == "cancelled"
                            )
                            or clean_message.strip("。.!！?？")
                            in {"确认", "是的", "执行", "继续"}
                        )
                        else "clarification"
                        if decision.action in {"none", "expired", "clarification", "select"}
                        else "completed"
                    )
                    response = AgentResponse(
                        decision_status,
                        decision.message,
                        conversation_id=confirmation_scope,
                    )
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "interaction_control",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        {"intent": "interaction_control", "source": "interaction_state"},
                        response=response,
                        record_history=record_history,
                    )
            current_interaction = self.interaction_coordinator.current(
                confirmation_scope
            )
            if current_interaction.state == "awaiting_confirmation":
                self.interaction_coordinator.cancel(confirmation_scope)
        pending_confirmation = self.agent_core.executor.confirmation_manager.pending(
            scope=confirmation_scope
        )
        if pending_confirmation is not None:
            confirmation = self.agent_core.handle_confirmation(
                clean_message,
                confirmation_scope=confirmation_scope,
            )
            if confirmation is not None:
                confirmation.conversation_id = history_id or conversation_id
                self._record_response(confirmation, history_id, record_history, "confirmation")
                return ConversationTurn(
                    clean_message,
                    history_id or conversation_id,
                    {"intent": "confirmation", "source": "fixed_command"},
                    response=confirmation,
                    record_history=record_history,
                )

        memory_interaction = self.state_manager.memory_interaction_context(
            confirmation_scope
        )
        if (
            str(memory_interaction.get("state", ""))
            == "awaiting_candidate_confirmation"
            and str(memory_interaction.get("pending_candidate_content", "")).strip()
            and clean_message.strip("。.!！?？")
            in {"取消", "不用了", "先不记", "不要加入", "忽略"}
        ):
            self.state_manager.complete_memory_interaction(confirmation_scope)
            response = AgentResponse(
                "completed",
                "好，这条信息没有加入待审核记忆。",
                conversation_id=confirmation_scope,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "cancel_memory_candidate",
            )
            return ConversationTurn(
                clean_message,
                confirmation_scope,
                {"intent": "chat", "source": "memory_interaction"},
                response=response,
                record_history=record_history,
            )

        reference_context = self.state_manager.reference_context(
            history_id or conversation_id
        )
        semantic_result = None
        if self.unified_semantic_parser_enabled:
            semantic_result = self.semantic_action_parser.parse(
                clean_message,
                reference_context,
                allow_llm=(allow_llm_intent and self.model_action_adapter is None),
            )
            intent_result = dict(semantic_result.intent_result)
            print(
                "[Semantic] source={} candidates={}".format(
                    semantic_result.source,
                    ",".join(item.tool_name for item in semantic_result.candidates)
                    or "none",
                ),
                flush=True,
            )
        else:
            intent_result = self.intent_router.route(
                clean_message,
                reference_context,
                allow_llm=(allow_llm_intent and self.model_action_adapter is None),
            )
        if str(intent_result.get("intent", "")) == "memory_candidate":
            entities = intent_result.get("entities", {})
            entities = entities if isinstance(entities, dict) else {}
            content = str(entities.get("content", clean_message)).strip()
            self.state_manager.prepare_memory_candidate_creation(
                confirmation_scope,
                content,
                source_text=str(entities.get("source_text", clean_message)),
                originating_turn_id=str(intent_result.get("request_id", "")),
            )
            if self.interaction_coordinator_enabled:
                self.interaction_coordinator.awaiting_confirmation(
                    confirmation_scope,
                    "memory_candidate_review",
                    immutable_arguments={
                        "tool_name": "create_memory_candidate",
                        "content": content,
                    },
                    safe_summary="确认后只会创建待审核候选，不会直接写入长期记忆。",
                    originating_turn_id=str(intent_result.get("request_id", "")),
                )
            response = AgentResponse(
                "clarification",
                "这条信息看起来可能长期有用。要把它加入待审核记忆吗？回复“确认”或“取消”。",
                conversation_id=confirmation_scope,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "memory_candidate",
            )
            return ConversationTurn(
                clean_message,
                confirmation_scope,
                intent_result,
                response=response,
                record_history=record_history,
            )
        if (
            not allow_llm_intent
            and self.intent_router.enable_llm
            and self.model_action_adapter is None
            and str(intent_result.get("source", "")) == "fallback"
        ):
            # Desktop preparation runs on the Qt thread. The optional model-based
            # intent pass belongs in the existing reply worker with the chat call.
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                intent_result,
                record_history=record_history,
                resolve_intent_in_worker=True,
            )
        if str(intent_result.get("intent", "")) == "show_recent_conversation":
            entities = intent_result.setdefault("entities", {})
            if isinstance(entities, dict):
                entities["conversation_id"] = history_id or conversation_id
                entities["current_message"] = clean_message
        if str(intent_result.get("intent", "")) == "show_conversation_history":
            entities = intent_result.setdefault("entities", {})
            if isinstance(entities, dict):
                entities["current_conversation_id"] = history_id or conversation_id
        semantic_clarification = str(
            intent_result.get("clarification_question", "") or ""
        ).strip()
        if semantic_result is not None and semantic_clarification:
            if self.interaction_coordinator_enabled:
                self.interaction_coordinator.awaiting_clarification(
                    confirmation_scope,
                    "missing_slot",
                    missing_fields=semantic_result.global_ambiguities,
                    candidates=[item.to_dict() for item in semantic_result.candidates],
                    immutable_arguments={},
                    safe_summary=semantic_clarification,
                    originating_turn_id=str(intent_result.get("request_id", "")),
                )
            response = AgentResponse(
                "clarification",
                semantic_clarification,
                conversation_id=confirmation_scope,
                request_id=str(intent_result.get("request_id", "")) or None,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                str(intent_result.get("intent", "")),
            )
            return ConversationTurn(
                clean_message,
                confirmation_scope,
                intent_result,
                response=response,
                record_history=record_history,
            )
        if (
            semantic_result is not None
            and self.business_resolver_enabled
            and semantic_result.candidates
        ):
            for candidate in semantic_result.candidates:
                if candidate.tool_name == "show_recent_conversation":
                    candidate.arguments.update(
                        {
                            "conversation_id": history_id or conversation_id,
                            "current_message": clean_message,
                        }
                    )
                elif candidate.tool_name == "show_conversation_history":
                    candidate.arguments.update(
                        {
                            "current_conversation_id": history_id or conversation_id,
                            "exclude_today": bool(
                                intent_result.get("entities", {}).get("exclude_today", False)
                                if isinstance(intent_result.get("entities"), dict)
                                else False
                            ),
                        }
                    )
            resolutions = self.business_resolver.resolve_all(
                semantic_result.candidates,
                conversation_id=confirmation_scope,
                features=semantic_result.local_features,
                structured_references=reference_context,
            )
            unresolved = next(
                (
                    item
                    for item in resolutions
                    if item.status
                    not in {"resolved", "partial"}
                ),
                None,
            )
            if unresolved is not None:
                kind = {
                    "plan": "plan_target_selection",
                    "memory": "memory_target_selection",
                    "action_log": "action_log_creation",
                }.get(unresolved.candidate.domain, "missing_slot")
                if unresolved.status == "confirmation_required":
                    resolved = unresolved.resolved_action
                    immutable = (
                        {
                            "tool_name": resolved.tool_name,
                            **dict(resolved.arguments),
                        }
                        if resolved is not None
                        else {}
                    )
                    if self.interaction_coordinator_enabled:
                        self.interaction_coordinator.awaiting_confirmation(
                            confirmation_scope,
                            kind,
                            immutable_arguments=immutable,
                            safe_summary=unresolved.safe_prompt,
                            originating_turn_id=str(intent_result.get("request_id", "")),
                        )
                elif self.interaction_coordinator_enabled:
                    candidate_ids = [
                        item.get("uid") or item.get("id")
                        for item in unresolved.candidate_objects
                        if isinstance(item, dict)
                        and (item.get("uid") is not None or item.get("id") is not None)
                    ]
                    self.interaction_coordinator.awaiting_clarification(
                        confirmation_scope,
                        kind,
                        missing_fields=unresolved.missing_fields,
                        candidates=[item for item in unresolved.candidate_objects if isinstance(item, dict)],
                        listed_object_ids=candidate_ids,
                        immutable_arguments={
                            "tool_name": unresolved.candidate.tool_name,
                            **dict(unresolved.candidate.arguments),
                        },
                        safe_summary=unresolved.safe_prompt,
                        originating_turn_id=str(intent_result.get("request_id", "")),
                    )
                response = self.response_composer.from_resolution(
                    unresolved.status,
                    unresolved.safe_prompt,
                    conversation_id=confirmation_scope,
                    request_id=str(intent_result.get("request_id", "")),
                )
                if unresolved.status == "forbidden" and unresolved.reason_code == "duplicate_action_log":
                    response.status = "completed"
                self._record_response(
                    response,
                    history_id,
                    record_history,
                    str(intent_result.get("intent", "")),
                )
                return ConversationTurn(
                    clean_message,
                    confirmation_scope,
                    intent_result,
                    response=response,
                    record_history=record_history,
                )
            resolved_actions = [
                item.resolved_action.to_dict()
                for item in resolutions
                if item.resolved_action is not None
            ]
            if resolved_actions:
                intent_result = {
                    **intent_result,
                    "intent": "resolved_actions",
                    "resolved_actions": resolved_actions,
                    "clarification_question": None,
                    "confidence": min(
                        (item.resolved_action or item.candidate).confidence
                        for item in resolutions
                    ),
                    "needs_confirmation": any(
                        item.needs_confirmation
                        or (
                            item.resolved_action or item.candidate
                        ).requires_confirmation_hint
                        for item in resolutions
                    ),
                    "source": semantic_result.source,
                }
        if str(intent_result.get("intent", "")) in {
            "accept_memory_candidate",
            "accept_memory_candidates",
            "accept_all_memory_candidates",
            "reject_memory_candidate",
            "reject_memory_candidates",
        } and not intent_result.get("clarification_question"):
            entities = intent_result.get("entities", {})
            entities = entities if isinstance(entities, dict) else {}
            candidate_ids = entities.get("candidate_ids", [])
            if not isinstance(candidate_ids, list):
                candidate_ids = []
            if entities.get("candidate_id") is not None:
                candidate_ids = [entities.get("candidate_id")]
            self.state_manager.begin_memory_candidate_review(
                confirmation_scope,
                candidate_ids,
                originating_turn_id=str(intent_result.get("request_id", "")),
            )
        if (
            self.interaction_coordinator_enabled
            and isinstance(intent_result.get("resolved_actions"), list)
        ):
            self.interaction_coordinator.start(
                confirmation_scope,
                "multi_action_review"
                if len(intent_result.get("resolved_actions", [])) > 1
                else "missing_slot",
                "awaiting_tool_result",
                originating_turn_id=str(intent_result.get("request_id", "")),
                action_candidates=intent_result.get("resolved_actions", []),
            )
        response = self.agent_core.process(
            clean_message,
            intent_result,
            confirmation_scope=confirmation_scope,
        )
        response.conversation_id = history_id or conversation_id
        if self.deterministic_response_enabled:
            response = self.response_composer.compose(
                response,
                user_text=clean_message,
            )
        if self.interaction_coordinator_enabled:
            if response.status == "confirmation_required":
                pending = response.pending_confirmation or {}
                if hasattr(pending, "to_dict"):
                    pending = pending.to_dict()
                pending = pending if isinstance(pending, dict) else {}
                self.interaction_coordinator.awaiting_confirmation(
                    confirmation_scope,
                    "dangerous_tool",
                    immutable_arguments={
                        "tool_name": pending.get("tool", ""),
                        **(
                            dict(pending.get("arguments", {}))
                            if isinstance(pending.get("arguments"), dict)
                            else {}
                        ),
                    },
                    safe_summary=response.message,
                    originating_turn_id=str(intent_result.get("request_id", "")),
                )
            elif response.status in {"completed", "partial_success"}:
                self.interaction_coordinator.finish(
                    confirmation_scope,
                    partial=(response.status == "partial_success"),
                )
        if (
            response.status == "clarification"
            and str(intent_result.get("intent", ""))
            in {"accept_memory_candidates", "reject_memory_candidates"}
        ):
            entities = intent_result.get("entities", {})
            entities = entities if isinstance(entities, dict) else {}
            ids = entities.get("candidate_ids", [])
            self.state_manager.set_memory_candidate_selection(
                confirmation_scope,
                ids if isinstance(ids, list) else [],
                awaiting_confirmation=(
                    str(entities.get("memory_state_transition", ""))
                    == "awaiting_candidate_confirmation"
                ),
                originating_turn_id=str(intent_result.get("request_id", "")),
            )
        if response.status != "chat":
            self._record_response(
                response,
                history_id,
                record_history,
                str(intent_result.get("intent", "")),
            )
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                intent_result,
                response=response,
                record_history=record_history,
            )

        intent_name = str(intent_result.get("intent", "chat"))
        intent_source = str(intent_result.get("source", "fallback"))
        if intent_name not in {"chat", "memory_candidate"} or intent_source == "fixed_command":
            # UI-specific legacy commands can still be handled by the desktop shell.
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                intent_result,
                record_history=record_history,
            )

        messages = self.build_llm_messages(clean_message, history_id or conversation_id)
        return ConversationTurn(
            clean_message,
            history_id or conversation_id,
            intent_result,
            llm_messages=messages,
            record_history=record_history,
        )

    def complete(self, turn: ConversationTurn) -> AgentResponse:
        if turn.response is not None:
            turn.response.message = sanitize_public_reply(turn.response.message)
            return turn.response
        if turn.resolve_intent_in_worker:
            resolved = self.prepare(
                turn.message,
                turn.conversation_id,
                record_history=turn.record_history,
                allow_llm_intent=True,
            )
            return self.complete(resolved)
        if not turn.llm_messages:
            response = AgentResponse(
                status="failed",
                message="这个功能暂时只能在桌面界面中完成。",
                request_id=str(turn.intent_result.get("request_id", "")) or None,
                conversation_id=turn.conversation_id,
            )
            self._record_response(
                response,
                turn.conversation_id,
                turn.record_history,
                str(turn.intent_result.get("intent", "")),
            )
            return response

        if self.model_action_adapter is not None:
            model_response = self.model_action_adapter.complete(
                user_text=turn.message,
                messages=turn.llm_messages,
                conversation_id=turn.conversation_id,
                intent_result=turn.intent_result,
            )
            if model_response is not None:
                model_response.request_id = turn.request_id or model_response.request_id
                model_response.message = sanitize_public_reply(model_response.message)
                if self.deterministic_response_enabled:
                    model_response = self.response_composer.compose(
                        model_response,
                        user_text=turn.message,
                    )
                self._record_response(
                    model_response,
                    turn.conversation_id,
                    turn.record_history,
                    str(turn.intent_result.get("intent", "chat")),
                )
                if model_response.status == "chat":
                    self._capture_memory_candidate(turn)
                return model_response

        reply = self._chat(turn.llm_messages, turn.request_id)
        response = AgentResponse(
            status="chat",
            message=sanitize_public_reply(reply),
            request_id=turn.request_id or None,
            conversation_id=turn.conversation_id,
        )
        if self.deterministic_response_enabled:
            response = self.response_composer.compose(
                response,
                user_text=turn.message,
            )
        self._record_response(
            response,
            turn.conversation_id,
            turn.record_history,
            str(turn.intent_result.get("intent", "chat")),
        )
        self._capture_memory_candidate(turn)
        return response

    def set_memory_candidates_enabled(self, enabled: bool) -> None:
        self.enable_memory_candidates = bool(enabled)
        if self.memory_governance is not None:
            self.memory_governance.set_enabled(enabled)

    def build_llm_messages(
        self,
        user_text: str,
        conversation_id: str,
    ) -> List[Dict[str, str]]:
        session_summary = ""
        recent_messages: List[Dict[str, object]] = []
        if self.chat_history_manager is not None and conversation_id:
            session_summary = self.chat_history_manager.get_summary(conversation_id)
            recent_messages = self.chat_history_manager.recent_messages(
                conversation_id,
                self.context_builder.recent_message_limit + 1,
            )

        suppressed = self.state_manager.suppressed_categories(conversation_id)
        retrieved = self.memory_retriever.retrieve(
            user_text,
            session_summary,
            limit=5,
            suppressed_categories=suppressed,
        )
        memories = [
            item["memory"]
            for item in retrieved
            if isinstance(item.get("memory"), dict)
        ]
        categories = [str(item.get("category", "other")) for item in memories]
        current_facts = self.state_manager.current_facts(conversation_id)
        if self._uses_current_state(user_text):
            location = current_facts.get("location")
            if isinstance(location, dict) and str(location.get("value", "")).strip():
                # A location stated in this conversation is authoritative for current-
                # state questions. Keep older identity/history memories out of the same
                # prompt so a small model cannot blend incompatible places together.
                memories = [
                    item
                    for item in memories
                    if not str(item.get("location", "")).strip()
                ]
                categories = [str(item.get("category", "other")) for item in memories]
                memories.insert(
                    0,
                    {
                        "id": 0,
                        "content": f"用户在当前对话中明确表示现在位于{location['value']}",
                        "category": "other",
                        "scope": "current_state",
                        "location": str(location["value"]),
                        "source": "current_conversation",
                    },
                )
                categories.insert(0, "other")
        return self.context_builder.build(
            personality_context=self.personality_context_provider(),
            memory_context=self.memory_context_provider(memories),
            memory_count=len(memories),
            memory_categories=categories,
            skipped_sensitive_categories=(
                self.memory_retriever.last_skipped_sensitive_categories
            ),
            suppressed_categories=suppressed,
            session_summary=session_summary,
            recent_messages=recent_messages,
            knowledge_context=self.knowledge_context_provider(user_text),
            current_user_input=user_text,
            instruction=self.instruction,
        )

    def _ensure_history_session(self, conversation_id: str) -> str:
        if self.chat_history_manager is None:
            return conversation_id
        if conversation_id and self.chat_history_manager.get_session(conversation_id):
            return conversation_id
        if conversation_id:
            session = self.chat_history_manager.new_session(
                title="新对话",
                session_id=conversation_id,
            )
        else:
            session = self.chat_history_manager.ensure_session(restore_latest=True)
        return str(session["session_id"])

    def _record_response(
        self,
        response: AgentResponse,
        conversation_id: str,
        record_history: bool,
        intent: str,
    ) -> None:
        response.message = sanitize_public_reply(response.message)
        if conversation_id:
            self.state_manager.observe_assistant(conversation_id, response.message)
            for result in response.tool_results:
                self.state_manager.observe_tool_result(
                    conversation_id, result.to_dict()
                )
                self._observe_interaction_tool_result(conversation_id, result)
        if not record_history or self.chat_history_manager is None or not conversation_id:
            return
        self.chat_history_manager.add_message(
            conversation_id,
            "assistant",
            response.message,
            intent=intent or None,
            metadata={"status": response.status},
        )
        self._maybe_generate_rule_summary(conversation_id)

    def _observe_interaction_tool_result(self, conversation_id: str, result) -> None:
        if not self.interaction_coordinator_enabled or not result.success:
            return
        data = result.data if isinstance(result.data, dict) else {}
        if result.tool in {"list_memory_candidates", "show_memory_candidates"}:
            candidates = data.get("candidates", [])
            ids = [
                item.get("id")
                for item in candidates
                if isinstance(item, dict) and item.get("id") is not None
            ] if isinstance(candidates, list) else []
            self.interaction_coordinator.record_candidates(
                conversation_id,
                "memory_candidate_review",
                ids,
                action_candidates=[item for item in candidates if isinstance(item, dict)]
                if isinstance(candidates, list)
                else [],
                originating_turn_id=result.tool_call_id,
                safe_summary="待审核记忆已经列出，请按编号或顺序选择。",
            )
        elif result.tool == "show_plan":
            tasks = data.get("tasks", [])
            ids = [
                item.get("uid") or item.get("id")
                for item in tasks
                if isinstance(item, dict)
                and (item.get("uid") is not None or item.get("id") is not None)
            ] if isinstance(tasks, list) else []
            self.interaction_coordinator.record_candidates(
                conversation_id,
                "plan_target_selection",
                ids,
                action_candidates=[item for item in tasks if isinstance(item, dict)]
                if isinstance(tasks, list)
                else [],
                originating_turn_id=result.tool_call_id,
                safe_summary="今天的计划已经列出，请按编号或标题选择。",
            )

    def _chat(self, messages: List[Dict[str, str]], request_id: str) -> str:
        try:
            from modules.llm.routed_client import RoutedLLMClient

            if isinstance(self.llm_client, RoutedLLMClient):
                return self.llm_client.chat(
                    messages,
                    route_context={"request_id": request_id},
                )
        except ImportError:
            pass
        return self.llm_client.chat(messages)

    def _maybe_generate_rule_summary(self, conversation_id: str) -> None:
        if self.chat_history_manager is None:
            return
        if not self.chat_history_manager.should_summarize(
            conversation_id,
            message_threshold=self.summary_message_threshold,
            character_threshold=self.summary_character_threshold,
        ):
            return
        print("[Summary] generate", flush=True)
        summary = self.chat_history_manager.generate_rule_summary(conversation_id)
        if self.chat_history_manager.save_summary(conversation_id, summary):
            print("[Summary] fallback", flush=True)

    def _capture_memory_candidate(self, turn: ConversationTurn) -> None:
        if not self.enable_memory_candidates or self.memory_governance is None:
            return
        try:
            result = self.memory_governance.propose_from_text(
                turn.message,
                explicit=False,
                source="conversation",
            )
            if result.get("status") == "added":
                candidate = result.get("candidate", {})
                candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
                print(f"[MemoryGovernance] candidate queued id={candidate_id}", flush=True)
        except Exception as error:
            print(
                f"[MemoryGovernance] candidate generation failed: {type(error).__name__}",
                flush=True,
            )

    @staticmethod
    def _default_memory_context(memories: List[Dict[str, object]]) -> str:
        contents = [
            str(item.get("content", "")).strip()
            for item in memories
            if str(item.get("content", "")).strip()
        ]
        if not contents:
            return ""
        return "相关长期记忆：\n" + "\n".join(f"- {item}" for item in contents)

    @staticmethod
    def _uses_current_state(text: str) -> bool:
        return any(
            term in str(text)
            for term in ("现在", "目前", "当前", "附近", "周末", "通勤", "天气", "吃饭", "去哪", "哪里")
        )
