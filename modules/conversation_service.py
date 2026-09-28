from __future__ import annotations

import re
import json
import hashlib
from dataclasses import asdict, dataclass, field
from functools import wraps
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional
from uuid import uuid4

from modules.agent_core import AgentCore
from modules.action_preview import build_action_preview
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.chat_history_manager import ChatHistoryManager
from modules.context_builder import ContextBuilder
from modules.conversation_state import ConversationStateManager
from modules.contracts import AgentResponse
from modules.development_log import TraceContext, get_development_log
from modules.intent_router import IntentRouter
from modules.interaction_diagnostics import InteractionDiagnostics
from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.memory_retriever import MemoryRetriever
from modules.memory_manager import SENSITIVE_CATEGORIES
from modules.memory_governance import MemoryGovernanceService
from modules.llm.response_sanitizer import sanitize_public_reply
from modules.response_composer import ResponseComposer
from modules.reference_resolver import ReferenceResolver
from modules.semantic_action_parser import ActionCandidate, SemanticParseResult
from modules.plan_authorization_policy import (
    PlanAuthorization,
    PlanAuthorizationPolicy,
)
from modules.read_snapshot import ReadSnapshot
from modules.semantic_pipeline import PipelineOutcome
from modules.suggestion_snapshot import SuggestionSnapshot
from modules.verified_turn_context import VerifiedTurnContext


def _pipeline_diagnostics_to_dict(diag) -> dict:
    """Convert PipelineDiagnostics dataclass to a plain dict for JSON."""
    try:
        result = asdict(diag)
        # Convert nested ValidationError objects to plain dicts.
        if "validation_errors_before_repair" in result:
            result["validation_errors_before_repair"] = [
                asdict(e) if hasattr(e, "__dataclass_fields__") else e
                for e in diag.validation_errors_before_repair
            ]
        if "validation_errors_after_repair" in result:
            result["validation_errors_after_repair"] = [
                asdict(e) if hasattr(e, "__dataclass_fields__") else e
                for e in diag.validation_errors_after_repair
            ]
        return result
    except Exception:
        return {"outcome": getattr(diag, "outcome", ""),
                "outcome_reason": getattr(diag, "outcome_reason", "")}


DEFAULT_CHAT_INSTRUCTION = (
    "请用当前人格回复用户，并先直接回应这一轮真正提出的问题。"
    "除非用户明确要求，不要擅自把闲聊、能力问题或新兴趣改写成学习计划、编程练习、健康建议或行动任务。"
    "当前主题不够明确时，只问一个最有必要的澄清问题；不要用旧记忆替用户选择学习主题。"
    "只在当前问题明确相关时使用长期记忆、会话摘要和近期对话。"
    "不要因为上下文中出现过健康、情绪或个人信息，就在无关的学习、编程或项目问题中主动重复这些内容。"
    "长期记忆只是辅助背景，不是每轮都要复述的事项。避免重复提醒、说教、过度保护和机械追问。"
    "不要为了显得积极而在每条回答结尾都追问‘需要我帮你吗’，也不要反复建议从五分钟或十分钟开始。"
    "每轮都以最后一条当前用户输入为最高优先级；如果用户是在问候、纠正你或开启新话题，先直接回应当前输入，不要继续上一轮建议，也不要把它当成对上一轮提问的确认。"
    "先判断用户的情绪再决定是否进入教学或建议；用户说‘数学很难’等受挫表达时，先承接感受，给一句具体而克制的鼓励，再询问最具体的困难，不要立刻切换成讲义模式。"
    "可以偶尔使用少量自然的动作描写（例如‘（轻轻点头）’）来保持陪伴感，但不要每轮固定添加，也不要用动作描写替代实际回应。"
    "避免客服式固定结尾和完全相同的模板；语气应随用户状态在温柔、专注、轻松之间调整。"
    "用户问模型能力、概念解释或事实问题时，先准确回答本身，不要强行追加无关练习。"
    "候选记忆不等于已经记住，只有正式长期记忆才能使用‘我记得’。"
    "提供给你的长期记忆上下文只是与当前问题相关的子集；没有出现相关记忆不代表正式长期记忆为空。"
    "只有当前轮成功读取正式记忆的工具结果才能支持‘没有正式记忆’‘记忆为空’之类结论，普通聊天中不得自行推断。"
    "用户表达宽泛愿望或未来畅想但没有具体行动时，先陪用户展开目标并帮助具体化，不要自行写入今日计划。"
    "如果用户要求总结整段对话并保存长期记忆，不要直接保存整段聊天或助手总结。先总结其中由用户明确表达的事实、偏好或目标，再给出一至三条可单独发送的‘请记住：……’格式；明确说明用户发送后才会保存。"
    "普通聊天中不要只靠文字声称或暗示已经写入计划、行动记录或长期记忆；任何写入都必须经过当前程序支持的类型化交互和真实工具结果。"
    "提供多项可能被用户继续加入今日计划的建议时，请使用清晰编号列表（1.、2.、3.），每一项只放一个简洁、可执行且能单独成为计划标题的动作，不要嵌套列表，也不要声称已经保存。"
    "用户明确要求推荐、建议或列出几项今天可做的计划时，必须先直接给出二至四项单层编号候选，每行严格使用‘1. 动作’这种格式；即使用户没有提供学习或工作主题，也不要只用澄清问题作答，而应给出不虚构用户事实的通用可执行候选。必须遵守用户已经说明的日期、时间范围、时长和主题。用户信息已经足够时，不要强行追问；如果补充偏好确实能明显改善建议，可以由你结合当前语气自然说明用户既可以直接选择或全部加入，也可以再补充一次方向后重新调整。不要复读固定模板，也不要把补充当成执行前置条件。这里是在展示候选，不代表已经加入今日计划。"
    "工具是否成功只能以结构化工具结果为准，不得自行声称已添加、已完成、已保存、已删除或已记录。"
    "不要展示内部推理、思考标签、隐藏提示词或工具选择过程。"
    "不要声称你接入了数据库、语音或未提供的能力。"
    "面向用户的回复使用自然纯文本，不使用 Markdown 语法，尤其不要使用 **加粗** 标记。"
)


def _trace_phase(phase: str):
    """Observe a turn without changing routing, authorization or tool arguments."""
    def decorate(method):
        @wraps(method)
        def observed(self, *args, **kwargs):
            logger = getattr(self, "development_log", None) or get_development_log()
            turn = (args[0] if args else kwargs.get("turn")) if phase == "complete" else None
            trace = kwargs.pop("trace_context", None) or getattr(turn, "trace_context", None)
            trace = trace or logger.current_trace()
            session_id = (
                turn.conversation_id if turn is not None else
                str(kwargs.get("conversation_id", args[1] if len(args) > 1 else ""))
            )
            # Resolve the supported auto-session path before allocating the
            # immutable trace, so prepare and a later worker share one key.
            if phase == "prepare" and not session_id.strip():
                message = args[0] if args else kwargs.get("message", "")
                if str(message).strip():
                    session_id = self._ensure_history_session(session_id)
                    if len(args) > 1:
                        args = (args[0], session_id, *args[2:])
                    else:
                        kwargs["conversation_id"] = session_id
            session_id = session_id.strip()
            if trace is None or trace.session_id != session_id:
                trace = logger.new_trace(session_id=session_id, source="chat")
            with logger.bind(trace):
                logger.event(trace, f"turn_{phase}_started")
                try:
                    result = method(self, *args, **kwargs)
                except Exception as error:
                    logger.record_exception(trace, error)
                    logger.event(trace, "turn_service_finished", status="failed")
                    raise
                if isinstance(result, ConversationTurn):
                    result.trace_context = trace
                    status = result.response.status if result.response else "deferred"
                else:
                    status = result.status
                logger.event(
                    trace, f"turn_{phase}_finished", status=status,
                    request_id=result.request_id,
                )
                return result
        return observed
    return decorate


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
    assistant_plan_source: str = ""
    preserve_advice_as_plan_options: bool = False
    plan_suggestion_source_text: str = ""
    plan_suggestion_refinement_used: bool = False
    verified_turn_context: Dict[str, object] = field(default_factory=dict)
    memory_evidence: List[Dict[str, object]] = field(default_factory=list)
    trace_context: Optional[TraceContext] = None

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

    MAX_CLARIFICATION_ROUNDS = 2
    _V22_DEGRADED_PLAN_TOOLS = {
        "inspect_plan_duplicates",
        "update_plan",
        "merge_plan",
        "reschedule_plan",
        "reopen_plan",
        "cancel_plan",
    }

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
        persona_registry=None,
        context_sections_provider: Optional[Callable[[], Dict[str, object]]] = None,
        memory_context_provider: Optional[Callable[[List[Dict[str, object]]], str]] = None,
        knowledge_context_provider: Optional[Callable[[str], str]] = None,
        memory_governance: Optional[MemoryGovernanceService] = None,
        enable_memory_candidates: bool = False,
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
        action_preview_enabled: bool = True,
        legacy_intent_path_enabled: bool = False,
        semantic_decision_compatibility_enabled: bool = False,
        interaction_diagnostics_enabled: bool = False,
        interaction_diagnostics_path=None,
        now_provider: Callable[[], datetime] = datetime.now,
        development_log=None,
    ) -> None:
        self.development_log = development_log or get_development_log()
        self.intent_router = intent_router
        self.agent_core = agent_core
        self.llm_client = llm_client
        self.memory_retriever = memory_retriever
        self.context_builder = context_builder
        self.chat_history_manager = chat_history_manager
        self.personality_context_provider = personality_context_provider or (lambda: "")
        self.persona_registry = persona_registry
        self.context_sections_provider = context_sections_provider or (lambda: {})
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
        self.action_preview_enabled = bool(action_preview_enabled)
        self.legacy_intent_path_enabled = bool(legacy_intent_path_enabled)
        self.semantic_decision_compatibility_enabled = bool(
            semantic_decision_compatibility_enabled
        )
        self.interaction_diagnostics = InteractionDiagnostics(
            enabled=interaction_diagnostics_enabled,
            persist_path=interaction_diagnostics_path,
        )
        self.now_provider = now_provider
        self._marked_memory_tool_calls: set[str] = set()
        if hasattr(self.agent_core, "action_batch_enabled"):
            self.agent_core.action_batch_enabled = self.action_batch_enabled

    def _action_preview(
        self,
        immutable_arguments: Mapping[str, object],
        *,
        safe_summary: str,
    ) -> Dict[str, object]:
        if not self.action_preview_enabled:
            return {}
        return build_action_preview(
            immutable_arguments,
            safe_summary=safe_summary,
        ).to_dict()

    def _make_repair_llm_callable(self):
        """Return a fn(prompt: str) -> str for the pipeline's model repair step.

        Uses the conversation service's own LLM client when available,
        falling back to the intent router's semantic parser.
        """
        # Prefer the direct LLM client.
        llm = getattr(self, "llm_client", None)
        if llm is not None and hasattr(llm, "chat"):
            return lambda prompt: str(
                llm.chat([{"role": "user", "content": prompt}])
            )
        # Fall back through the intent router's semantic parser.
        router = getattr(self, "intent_router", None)
        if router is not None:
            parser = getattr(router, "llm_parser", None)
            if parser is not None:
                chat_fn = getattr(parser, "chat_callable", None)
                if chat_fn is not None:
                    return lambda prompt: str(
                        chat_fn([{"role": "user", "content": prompt}])
                    )
        return None

    def _tool_registry(self):
        """Return the ToolRegistry for the pipeline's schema validation."""
        # Try agent_core executor first.
        executor = getattr(self.agent_core, "executor", None)
        if executor is not None:
            reg = getattr(executor, "registry", None)
            if reg is not None:
                return reg
        # Fall back through semantic_action_parser.
        adapter = getattr(self.semantic_action_parser, "proposal_adapter", None)
        if adapter is not None:
            return getattr(adapter, "registry", None)
        return None

    _plan_policy: Optional[PlanAuthorizationPolicy] = None

    def _evaluate_plan_policy(
        self,
        semantic_result,
        intent_result: Dict[str, object],
    ) -> Optional[AgentResponse]:
        """Evaluate PlanAuthorizationPolicy on the current semantic decision.

        Returns an AgentResponse for NO_ACTION (early return), None for
        EXECUTE (proceed to AgentCore), or sets needs_confirmation=True
        on intent_result for PENDING (proceed to AgentCore which will
        create the confirmation via the existing chain).
        """
        if semantic_result is None:
            return None
        decision = semantic_result.as_decision()
        if not decision.tool_calls:
            return None

        if self._plan_policy is None:
            self._plan_policy = PlanAuthorizationPolicy()
        plan_candidates = [
            item
            for item in semantic_result.candidates
            if item.domain == "plan"
            and item.tool_name
            in {
                "add_plan",
                "complete_plan",
                "update_plan",
                "reschedule_plan",
                "delete_plan",
                "reopen_plan",
                "cancel_plan",
            }
        ]
        request_mode = (
            "execute"
            if plan_candidates
            and all(item.request_mode == "execute" for item in plan_candidates)
            else "possible_action"
            if plan_candidates
            else ""
        )
        explicit_command = bool(plan_candidates) and all(
            item.explicit_command for item in plan_candidates
        )
        result = self._plan_policy.evaluate(
            decision,
            request_mode=request_mode,
            explicit_command=explicit_command,
        )

        if result.authorization == PlanAuthorization.NO_ACTION:
            confirmation_scope = str(
                intent_result.get("conversation_id", "")
            ) or "default"
            return AgentResponse(
                "completed",
                "这次没有执行计划操作。",
                conversation_id=confirmation_scope,
                request_id=str(intent_result.get("request_id", "")) or None,
            )
        if result.authorization == PlanAuthorization.PENDING:
            intent_result["needs_confirmation"] = True
            print(
                "[PlanPolicy] pending subject={} polarity={} modality={}".format(
                    decision.subject, decision.polarity, decision.modality
                ),
                flush=True,
            )
        elif result.authorization == PlanAuthorization.EXECUTE:
            intent_result["execution_authorized"] = (
                self._resolved_execution_is_locally_authorized(intent_result)
            )
        return None

    def _resolved_execution_is_locally_authorized(
        self,
        intent_result: Dict[str, object],
    ) -> Optional[str]:
        """Trust the validated local chain without lowering global thresholds."""
        actions = intent_result.get("resolved_actions")
        if not isinstance(actions, list) or not actions:
            return None
        registry = self._tool_registry()
        if registry is None:
            return None
        for action in actions:
            if not isinstance(action, dict):
                return False
            tool = registry.get(str(action.get("tool_name", "")))
            if (
                tool is None
                or not tool.enabled
                or tool.risk_level not in {"low", "medium"}
                or not tool.reversible
                or tool.confirmation_policy == "always"
            ):
                return False
        return True

    def handle(
        self,
        message: str,
        conversation_id: str,
        *,
        record_history: bool = True,
    ) -> AgentResponse:
        turn = self.prepare(message, conversation_id, record_history=record_history)
        return self.complete(turn)

    @_trace_phase("prepare")
    def prepare(
        self,
        message: str,
        conversation_id: str,
        *,
        record_history: bool = False,
        allow_llm_intent: bool = True,
        state_prepared: bool = False,
        trace_context: Optional[TraceContext] = None,
    ) -> ConversationTurn:
        clean_message = str(message).strip()
        if not clean_message:
            raise ValueError("message cannot be empty")

        history_id = self._ensure_history_session(str(conversation_id).strip())
        confirmation_scope = history_id or conversation_id
        literal_forget_target: Dict[str, object] = {}
        exact_forget_command = clean_message.startswith(("忘记：", "忘记:"))
        if exact_forget_command:
            self.agent_core.executor.confirmation_manager.cancel(scope=confirmation_scope)
            if self.interaction_coordinator_enabled:
                self.interaction_coordinator.cancel(confirmation_scope)
        pending_state_note = self._pause_incompatible_pending(
            clean_message,
            confirmation_scope,
        )
        diagnostic_conversation_id = history_id or conversation_id
        diagnostic_state = "idle"
        if self.interaction_coordinator_enabled:
            diagnostic_state = self.interaction_coordinator.current(
                diagnostic_conversation_id
            ).state
        self.interaction_diagnostics.begin(
            diagnostic_conversation_id,
            clean_message,
            interaction_state_before=diagnostic_state,
        )
        state_update = (
            {}
            if state_prepared
            else self.state_manager.observe_user(
                history_id or conversation_id,
                clean_message,
            )
        )
        if (
            not state_prepared
            and record_history
            and history_id
            and self.chat_history_manager is not None
        ):
            trace = self.development_log.current_trace()
            saved = self.chat_history_manager.add_message(
                history_id, "user", clean_message,
                metadata=trace.to_metadata() if trace and self.development_log.enabled else None,
            )
            self.development_log.event(
                trace, "history_written", status="success" if saved else "unavailable",
                message_id=str(saved.get("id", "")) if saved else "",
            )

        if self._is_candidate_memory_scope(clean_message):
            self.interaction_diagnostics.update(
                diagnostic_conversation_id,
                route_source="retired_memory_candidate",
                coordinator_decision="retired_capability",
            )
            self.state_manager.complete_memory_interaction(confirmation_scope)
            if self.interaction_coordinator_enabled:
                candidate_state = self.interaction_coordinator.current(
                    confirmation_scope
                )
                if candidate_state.interaction_kind == "memory_candidate_review":
                    self.interaction_coordinator.cancel(confirmation_scope)
            pending_candidate = (
                self.agent_core.executor.confirmation_manager.pending(
                    scope=confirmation_scope
                )
            )
            if pending_candidate and str(pending_candidate.get("tool", "")) in {
                "accept_memory_candidate",
                "accept_memory_candidates",
                "accept_all_memory_candidates",
                "reject_memory_candidate",
                "reject_memory_candidates",
                "create_memory_candidate",
                "queue_memory_candidate",
            }:
                self.agent_core.executor.confirmation_manager.cancel(
                    scope=confirmation_scope
                )
            response = AgentResponse(
                "failed",
                self._candidate_review_unavailable_reply(),
                conversation_id=confirmation_scope,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "retired_memory_candidate",
            )
            return ConversationTurn(
                clean_message,
                confirmation_scope,
                {
                    "intent": "chat",
                    "source": "retired_memory_candidate",
                },
                response=response,
                record_history=record_history,
            )

        if self._is_unsupported_timer_request(clean_message):
            return self._unsupported_timer_turn(
                clean_message,
                confirmation_scope,
                history_id,
                record_history,
            )

        read_follow_up = self._handle_read_result_follow_up(
            clean_message,
            confirmation_scope,
            history_id,
            record_history,
        )
        if read_follow_up is not None:
            return read_follow_up

        undo_turn = self._handle_scoped_memory_undo(
            clean_message,
            confirmation_scope,
            history_id,
            record_history,
        )
        if undo_turn is not None:
            return undo_turn

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
        if current_location:
            response = AgentResponse(
                "completed",
                f"知道了，这次对话里我会以你现在位于{current_location}为准。",
                conversation_id=history_id or conversation_id,
            )
            self._record_response(response, history_id, record_history, "current_state")
            return ConversationTurn(
                clean_message,
                history_id or conversation_id,
                {"intent": "chat", "source": "current_state"},
                response=response,
                record_history=record_history,
            )

        resumed_semantic_result = None
        if self.interaction_coordinator_enabled and not exact_forget_command:
            resumed_turn, resumed_semantic_result = self._resume_pending_interaction(
                clean_message,
                confirmation_scope,
                history_id,
                record_history,
            )
            if resumed_turn is not None or resumed_semantic_result is not None:
                self.interaction_diagnostics.update(
                    diagnostic_conversation_id,
                    coordinator_decision="pending_continuation",
                )
            if resumed_turn is not None:
                return resumed_turn
            if resumed_semantic_result is None:
                snapshot_turn, snapshot_semantic = self._plan_snapshot_follow_up(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                )
                if snapshot_turn is not None:
                    return snapshot_turn
                if snapshot_semantic is not None:
                    resumed_semantic_result = snapshot_semantic
        pending_continuation = resumed_semantic_result is not None
        if self.interaction_coordinator_enabled and not pending_continuation and not exact_forget_command:
            decision = self.interaction_coordinator.handle_control(
                clean_message,
                confirmation_scope,
            )
            if decision.handled:
                self.interaction_diagnostics.update(
                    diagnostic_conversation_id,
                    coordinator_decision=f"control:{decision.action}",
                )
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
                    in {
                        "memory_candidate_review",
                        "action_log_creation",
                        "action_log_offer",
                        "formal_memory_save",
                    }
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
                        self._remember_formal_memory_from_response(
                            response,
                            confirmation_scope,
                            str(decision.interaction.originating_turn_id),
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
            if (
                self.interaction_coordinator_enabled
                and self.agent_core.executor.confirmation_manager.pending(
                    scope=confirmation_scope
                )
                is None
            ):
                # An unrelated message invalidates immutable confirmation
                # arguments. Keep the coordinator in sync with that boundary.
                self.interaction_coordinator.cancel(confirmation_scope)

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

        plan_batch_turn = (
            None if exact_forget_command else self._start_plan_batch_completion(
                clean_message,
                confirmation_scope,
                history_id,
                record_history,
            )
        )
        if plan_batch_turn is not None:
            return plan_batch_turn

        reference_context = self.state_manager.reference_context(
            history_id or conversation_id
        )
        suggestion_snapshot = self._recent_suggestion_snapshot(
            history_id or conversation_id
        )
        if suggestion_snapshot is not None:
            reference_context["suggestion_snapshot"] = (
                suggestion_snapshot.to_model_dict()
            )
            collection_control = self._collection_control(
                clean_message, "add_plan"
            )
            if collection_control:
                # The displayed snapshot is verified local evidence. Consume
                # an explicit whole-list/remaining-list reply deterministically
                # even when the model labels the short follow-up as chat.
                return self._execute_suggestion_plan_batch(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                )
        capability_help_query = DEFAULT_CAPABILITY_REGISTRY.is_user_help_query(
            clean_message
        )
        if (
            not allow_llm_intent
            and self.intent_router.enable_llm
            and not self.semantic_decision_compatibility_enabled
            and not capability_help_query
        ):
            feature_extractor = getattr(
                self.semantic_action_parser, "feature_extractor", None
            )
            features = (
                feature_extractor.extract(clean_message)
                if feature_extractor is not None
                else None
            )
            routing_text = clean_message
            routing_text_builder = getattr(
                self.semantic_action_parser, "_routing_text", None
            )
            if callable(routing_text_builder) and features is not None:
                routing_text = routing_text_builder(clean_message, features)
            exact = self.intent_router.route_exact_command(
                routing_text,
                reference_context,
                payload_text=(
                    str(getattr(features, "payload_text", "") or "") or None
                ),
            )
            if exact is None:
                print(
                    "[SemanticDispatch] deferred_to_worker reason=llm_required",
                    flush=True,
                )
                return ConversationTurn(
                    clean_message,
                    history_id or conversation_id,
                    {
                        "intent": "pending_semantic_decision",
                        "source": "deferred_to_worker",
                    },
                    record_history=record_history,
                    resolve_intent_in_worker=True,
                )
        semantic_result = resumed_semantic_result
        semantic_authoritative = False
        if capability_help_query:
            # Asking what the product supports or how to operate it is a chat
            # purpose, never authorization to run the mentioned operation.
            # Keep the natural final reply on the model path, but do not let a
            # probabilistic semantic proposal create a write or confirmation.
            semantic_result = None
            intent_result = {
                "intent": "chat",
                "confidence": 1.0,
                "entities": {},
                "needs_confirmation": False,
                "source": "capability_help",
                "request_mode": "discuss",
                "explicit_command": False,
            }
        elif semantic_result is not None:
            intent_result = dict(semantic_result.intent_result)
        elif self.unified_semantic_parser_enabled:
            unified_parse = getattr(self.semantic_action_parser, "parse_unified", None)
            if (
                callable(unified_parse)
                and not self.semantic_decision_compatibility_enabled
            ):
                semantic_result = unified_parse(
                    clean_message,
                    reference_context,
                    allow_llm=allow_llm_intent,
                )
                semantic_authoritative = True
            else:
                # Offline and legacy composition roots retain their existing
                # deterministic parser while the production RoutedLLMClient
                # uses the authoritative semantic-decision path.
                semantic_result = self.semantic_action_parser.parse(
                    clean_message,
                    reference_context,
                    allow_llm=allow_llm_intent,
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
        elif self.legacy_intent_path_enabled:
            intent_result = self.intent_router.route(
                clean_message,
                reference_context,
                allow_llm=(allow_llm_intent and self.model_action_adapter is None),
            )
        else:
            intent_result = {
                "intent": "chat",
                "confidence": 0.0,
                "entities": {},
                "needs_confirmation": False,
                "source": "intent_paths_disabled",
                "warnings": ["all_intent_paths_disabled"],
            }
        if semantic_result is not None:
            semantic_result = self._repair_grounded_pending_plan_completion(
                clean_message,
                semantic_result,
            )
            intent_result = dict(semantic_result.intent_result)
            intent_result["semantic_authoritative"] = semantic_authoritative
            forget_turn = self._bind_exact_memory_forget(
                semantic_result, intent_result,
                conversation_id=confirmation_scope, user_text=clean_message,
                history_id=history_id, record_history=record_history,
                target_guard=literal_forget_target,
            )
            if forget_turn is not None:
                return forget_turn
            self._hydrate_runtime_semantic_arguments(
                semantic_result,
                intent_result,
                conversation_id=history_id or conversation_id,
                current_message=clean_message,
            )
            self._bind_snapshot_ordinal_completion(
                semantic_result,
                intent_result,
                conversation_id=confirmation_scope,
                current_message=clean_message,
            )
            self._bind_snapshot_ordinal_delete(
                semantic_result,
                intent_result,
                conversation_id=confirmation_scope,
                current_message=clean_message,
            )
            if (
                str(intent_result.get("intent", "")) == "complete_plan"
                and self._collection_control(clean_message, "complete_plan") == "all"
            ):
                collection_turn = self._start_plan_batch_completion(
                    clean_message, confirmation_scope, history_id, record_history,
                    selected_all=True,
                )
                if collection_turn is not None:
                    return collection_turn
            if (
                semantic_result.candidates
                and all(item.tool_name == "add_plan" for item in semantic_result.candidates)
                and self._collection_control(clean_message, "add_plan")
                and not self._has_grounded_plan_batch_candidates(
                    semantic_result,
                    clean_message,
                )
            ):
                return self._execute_suggestion_plan_batch(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                )
            self._bind_suggestion_snapshot_reference(
                semantic_result,
                intent_result,
                conversation_id=confirmation_scope,
                current_message=clean_message,
            )
            self._promote_committed_plan_add(
                semantic_result,
                intent_result,
                current_message=clean_message,
            )
            if self._uses_degraded_v22_plan_capability(semantic_result):
                degraded_tools = [
                    candidate.tool_name
                    for candidate in semantic_result.candidates
                    if candidate.tool_name in self._V22_DEGRADED_PLAN_TOOLS
                ]
                if self.interaction_coordinator_enabled:
                    current_interaction = self.interaction_coordinator.current(
                        confirmation_scope
                    )
                    if current_interaction.pending:
                        self.interaction_coordinator.cancel(confirmation_scope)
                self._update_semantic_diagnostics(
                    confirmation_scope,
                    intent_result,
                    semantic_result,
                )
                response = AgentResponse(
                    "failed",
                    self._degraded_feature_reply(degraded_tools),
                    conversation_id=confirmation_scope,
                    request_id=str(intent_result.get("request_id", "")) or None,
                )
                self._record_response(
                    response,
                    history_id,
                    record_history,
                    "degraded_capability_gate",
                )
                return ConversationTurn(
                    clean_message,
                    confirmation_scope,
                    intent_result,
                    response=response,
                    record_history=record_history,
                )
            delete_confirmation_turn = self._bridge_explicit_plan_delete(
                semantic_result,
                intent_result,
                conversation_id=confirmation_scope,
                user_text=clean_message,
                history_id=history_id,
                record_history=record_history,
            )
            if delete_confirmation_turn is not None:
                return delete_confirmation_turn
            if semantic_result.source == "llm" and (
                str(intent_result.get("intent", "")) == "delete_plan"
                or str(intent_result.get("proposed_tool", "")) == "delete_plan"
                or any(item.tool_name == "delete_plan" for item in semantic_result.candidates)
            ):
                # A delete suggestion without a unique locally bound pending
                # operation is not a confirmation workflow.
                intent_result["semantic_decision"] = semantic_result.as_decision().to_dict()
                self._update_semantic_diagnostics(confirmation_scope, intent_result, semantic_result)
                self.interaction_coordinator.cancel(confirmation_scope)
                self.agent_core.executor.confirmation_manager.cancel(scope=confirmation_scope)
                delete_reply = (
                    "抱歉，当前一次只支持删除一条计划，这次没有完成，也没有删除任何计划。"
                    "请先说“查看今天计划”，再逐条说“删除第2条”，每条确认后执行。"
                    if self._multiple_plan_delete_scope(
                        clean_message,
                        semantic_result.local_features,
                    )
                    else self._degraded_feature_reply(["delete_plan"])
                )
                return self._snapshot_failure_turn(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                    delete_reply,
                )
            implicit_memory_turn = self._maybe_implicit_memory_turn(
                clean_message,
                confirmation_scope,
                history_id,
                record_history,
                semantic_result,
                intent_result,
            )
            if implicit_memory_turn is not None:
                return implicit_memory_turn
            if not pending_continuation:
                destination_turn = self._maybe_time_bounded_memory_destination_turn(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                    semantic_result,
                    request_id=str(intent_result.get("request_id", "")),
                )
                if destination_turn is not None:
                    return destination_turn
            semantic_decision = semantic_result.as_decision()
            intent_result["semantic_decision"] = semantic_decision.to_dict()
            print(
                "[SemanticDecision] mode={} intent={} tools={}".format(
                    semantic_decision.mode,
                    semantic_decision.intent,
                    ",".join(item.name for item in semantic_decision.tool_calls)
                    or "none",
                ),
                flush=True,
            )
            # ── Normalize → Validate → Repair pipeline ─────────────────
            pipeline_validator = getattr(
                self.semantic_action_parser, "validate_decision", None
            )
            if (
                callable(pipeline_validator)
                and semantic_decision.tool_calls
                and semantic_decision.mode != "chat_only"
            ):
                pipeline_result = pipeline_validator(
                    semantic_decision,
                    llm_callable=self._make_repair_llm_callable(),
                    registry=self._tool_registry(),
                    require_model_visible=(semantic_result.source == "llm"),
                )
                intent_result["pipeline_diagnostics"] = (
                    _pipeline_diagnostics_to_dict(pipeline_result.diagnostics)
                )
                intent_result["pipeline_outcome"] = (
                    pipeline_result.outcome.value
                )
                print(
                    "[Pipeline] outcome={} reason={}".format(
                        pipeline_result.outcome.value,
                        pipeline_result.diagnostics.outcome_reason,
                    ),
                    flush=True,
                )
                # Update diagnostics before early returns.
                self._update_semantic_diagnostics(
                    confirmation_scope,
                    intent_result,
                    semantic_result,
                )
                if pipeline_result.outcome == PipelineOutcome.CLARIFY:
                    clarification_message = (
                        pipeline_result.clarification_message
                        or "请更明确地说明你想执行的操作。"
                    )
                    clarification_message = self._register_semantic_clarification(
                        semantic_result,
                        conversation_id=confirmation_scope,
                        user_text=clean_message,
                        clarification_message=clarification_message,
                        request_id=str(intent_result.get("request_id", "")),
                    )
                    response = AgentResponse(
                        "clarification",
                        clarification_message,
                        conversation_id=confirmation_scope,
                        request_id=str(
                            intent_result.get("request_id", "")
                        ) or None,
                    )
                    self._apply_pending_state_note(
                        response, pending_state_note
                    )
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "pipeline_clarify",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        intent_result,
                        response=response,
                        record_history=record_history,
                    )
                if pipeline_result.outcome == PipelineOutcome.REJECT:
                    reject_message = (
                        pipeline_result.reject_reason
                        or "无法安全执行该操作，本轮没有执行任何操作。"
                    )
                    if (
                        pipeline_result.diagnostics.outcome_reason
                        == "tool_not_model_visible"
                    ):
                        reject_message = (
                            self._candidate_review_unavailable_reply()
                            if self._is_candidate_memory_scope(clean_message)
                            else self._degraded_feature_reply(
                                call.name for call in semantic_decision.tool_calls
                            )
                        )
                    response = AgentResponse(
                        "failed",
                        reject_message,
                        conversation_id=confirmation_scope,
                        request_id=str(
                            intent_result.get("request_id", "")
                        ) or None,
                    )
                    self._apply_pending_state_note(
                        response, pending_state_note
                    )
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "pipeline_reject",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        intent_result,
                        response=response,
                        record_history=record_history,
                    )
                # EXECUTE — use the validated decision
                semantic_decision = pipeline_result.decision
                intent_result["semantic_decision"] = (
                    semantic_decision.to_dict()
                )
                # ── Back-propagate normalized arguments ────────────
                # The Normalizer resolved field aliases.  Update both
                # intent_result["entities"] (for AgentCore) and
                # semantic_result.candidates (for BusinessResolver).
                if semantic_decision.tool_calls:
                    normalized_args = dict(
                        semantic_decision.tool_calls[0].arguments
                    )
                    if normalized_args:
                        entities = intent_result.setdefault("entities", {})
                        if isinstance(entities, dict):
                            entities.update(normalized_args)
                    # Replace candidate arguments with validated canonical
                    # names so BusinessResolver sees the normalized data.
                    if semantic_result is not None:
                        remaining_calls = list(semantic_decision.tool_calls)
                        for candidate in semantic_result.candidates:
                            for call_index, call in enumerate(remaining_calls):
                                if call.name == candidate.tool_name:
                                    candidate.arguments = dict(call.arguments)
                                    remaining_calls.pop(call_index)
                                    break
            assistant_plan_reference = (
                None
                if pending_continuation
                else self._assistant_plan_reference_source(
                    semantic_result,
                    reference_context,
                    current_message=clean_message,
                )
            )
            if assistant_plan_reference is not None:
                return self._assistant_plan_reference_turn(
                    clean_message,
                    confirmation_scope,
                    history_id,
                    record_history,
                    assistant_plan_reference,
                    intent_result,
                )
            plan_choice_candidates = self._plan_choice_candidates(
                semantic_result,
            )
            if plan_choice_candidates:
                batch_arguments = [
                    {
                        key: value
                        for key, value in candidate.arguments.items()
                        if value not in (None, "", [])
                    }
                    for candidate in plan_choice_candidates
                ]
                batch_arguments = [item for item in batch_arguments if item.get("title")]
                unresolved = semantic_result.intent_result.get(
                    "schedule_unresolved_items", []
                )
                overflow = int(
                    semantic_result.intent_result.get("schedule_overflow_count", 0)
                    or 0
                )
                if len(batch_arguments) > 1 or unresolved or overflow:
                    known_fields = {}
                    plan_choice_message = "我识别到这些明确安排：\n" + "\n".join(
                        f"{index}. {item['title']}（{item.get('duration_minutes', '时长未定')}分钟）"
                        for index, item in enumerate(batch_arguments, 1)
                    )
                    if unresolved:
                        plan_choice_message += (
                            f"\n另有 {len(unresolved)} 项包含未确定的选择，暂不加入。"
                        )
                    if overflow:
                        plan_choice_message += (
                            f"\n另有 {overflow} 项超过单次 3 项上限，暂不加入。"
                        )
                    plan_choice_message += "\n要把上面这些明确安排加入今天计划吗？"
                    request_mode = (
                        plan_choice_candidates[0].request_mode
                        or semantic_result.request_mode
                        or "possible_action"
                    )
                    self._update_semantic_diagnostics(
                        confirmation_scope,
                        intent_result,
                        semantic_result,
                    )
                    self.interaction_coordinator.awaiting_choice(
                        confirmation_scope,
                        "advice_or_action_choice",
                        domain="plan",
                        request_mode=request_mode,
                        original_user_text=clean_message,
                        known_fields=known_fields,
                        options=batch_arguments,
                        immutable_arguments={"tool_name": "add_plan"},
                        safe_summary=plan_choice_message,
                        originating_turn_id=str(intent_result.get("request_id", "")),
                    )
                    intent_result["clarification_question"] = plan_choice_message
                    response = AgentResponse(
                        "clarification",
                        plan_choice_message,
                        conversation_id=confirmation_scope,
                        request_id=str(intent_result.get("request_id", "")) or None,
                    )
                    self._apply_pending_state_note(response, pending_state_note)
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "plan_choice_clarify",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        intent_result,
                        response=response,
                    )
                plan_choice_candidate = plan_choice_candidates[0]
                title = self._specific_plan_title(
                    plan_choice_candidate.arguments.get("title", ""),
                    clean_message,
                )
                if not title:
                    known_fields = {
                        key: value
                        for key, value in plan_choice_candidate.arguments.items()
                        if key != "title" and value not in (None, "", [])
                    }
                    question = "想加入什么具体事项？"
                    # Preserve a locally-derived domain clarification such as
                    # “学什么、多久”; the generic title prompt is for an empty
                    # command shell or an untrusted model clarification.
                    if semantic_result.source != "llm":
                        local_question = str(
                            semantic_result.intent_result.get(
                                "clarification_question", ""
                            )
                            or ""
                        ).strip()
                        if local_question:
                            question = local_question
                    self._update_semantic_diagnostics(
                        confirmation_scope,
                        intent_result,
                        semantic_result,
                    )
                    self.interaction_coordinator.awaiting_clarification(
                        confirmation_scope,
                        "missing_slots",
                        missing_fields=["title"],
                        immutable_arguments={
                            "tool_name": "add_plan",
                            **known_fields,
                        },
                        domain="plan",
                        request_mode="execute",
                        original_user_text=clean_message,
                        known_fields=known_fields,
                        safe_summary=question,
                        originating_turn_id=str(
                            intent_result.get("request_id", "")
                        ),
                    )
                    intent_result["clarification_question"] = question
                    response = AgentResponse(
                        "clarification",
                        question,
                        conversation_id=confirmation_scope,
                        request_id=str(intent_result.get("request_id", "")) or None,
                    )
                    self._apply_pending_state_note(response, pending_state_note)
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "plan_title_clarify",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        intent_result,
                        response=response,
                        record_history=record_history,
                    )
                if title:
                    known_fields = {
                        key: value
                        for key, value in plan_choice_candidate.arguments.items()
                        if value not in (None, "", [])
                    }
                    known_fields["title"] = title
                    plan_choice_message = (
                        "你想现在开始、先听建议，还是把它加入今天计划？"
                    )
                    self._update_semantic_diagnostics(
                        confirmation_scope,
                        intent_result,
                        semantic_result,
                    )
                    self.interaction_coordinator.awaiting_choice(
                        confirmation_scope,
                        "advice_or_action_choice",
                        domain="plan",
                        request_mode=(
                            plan_choice_candidate.request_mode
                            or semantic_result.request_mode
                            or "possible_action"
                        ),
                        original_user_text=clean_message,
                        known_fields=known_fields,
                        immutable_arguments={
                            "tool_name": "add_plan",
                            **known_fields,
                        },
                        safe_summary=plan_choice_message,
                        originating_turn_id=str(
                            intent_result.get("request_id", "")
                        ),
                    )
                    intent_result["clarification_question"] = plan_choice_message
                    response = AgentResponse(
                        "clarification",
                        plan_choice_message,
                        conversation_id=confirmation_scope,
                        request_id=str(intent_result.get("request_id", "")) or None,
                    )
                    self._apply_pending_state_note(response, pending_state_note)
                    self._record_response(
                        response,
                        history_id,
                        record_history,
                        "plan_choice_clarify",
                    )
                    return ConversationTurn(
                        clean_message,
                        confirmation_scope,
                        intent_result,
                        response=response,
                        record_history=record_history,
                    )
        if pending_state_note:
            intent_result["pending_state_note"] = pending_state_note
        self._update_semantic_diagnostics(
            confirmation_scope,
            intent_result,
            semantic_result,
        )
        if str(intent_result.get("intent", "")) == "memory_candidate":
            print(
                "[Validation] rejected reason=candidate_intent_disabled_in_main_chat",
                flush=True,
            )
            intent_result.update(
                {
                    "intent": "chat",
                    "entities": {},
                    "clarification_question": None,
                    "needs_confirmation": False,
                }
            )
            if semantic_result is not None:
                semantic_result.candidates = []
                semantic_result.needs_clarification = False
                semantic_result.global_ambiguities = []
        if (
            not allow_llm_intent
            and self.intent_router.enable_llm
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
            semantic_clarification = self._register_semantic_clarification(
                semantic_result,
                conversation_id=confirmation_scope,
                user_text=clean_message,
                clarification_message=semantic_clarification,
                request_id=str(intent_result.get("request_id", "")),
            )
            response = AgentResponse(
                "clarification",
                semantic_clarification,
                conversation_id=confirmation_scope,
                request_id=str(intent_result.get("request_id", "")) or None,
            )
            self._apply_pending_state_note(response, pending_state_note)
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
            self._hydrate_memory_message_reference(
                semantic_result.candidates,
                reference_context,
                conversation_id=confirmation_scope,
                current_message=clean_message,
                request_id=str(intent_result.get("request_id", "")),
            )
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
            self._update_resolution_diagnostics(confirmation_scope, resolutions)
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
                if unresolved.reason_code in {"plan_already_completed", "target_deleted"}:
                    # These are terminal state facts, not missing object slots.
                    # In particular, never turn a stale/done plan into an
                    # unrelated pending plan or an unplanned-action proposal.
                    self.interaction_coordinator.cancel(confirmation_scope)
                    self.agent_core.executor.confirmation_manager.cancel(scope=confirmation_scope)
                    response = AgentResponse(
                        "completed" if unresolved.reason_code == "plan_already_completed" else "failed",
                        unresolved.safe_prompt,
                        conversation_id=confirmation_scope,
                    )
                    if len(resolutions) > 1:
                        response.status = "failed"
                        response.message += " 这次没有执行其他操作，请分别说明剩余请求。"
                    self._record_response(response, history_id, record_history, "plan_state_unchanged")
                    return ConversationTurn(
                        clean_message, confirmation_scope, intent_result,
                        response=response, record_history=record_history,
                    )
                unresolved_writes = [
                    item
                    for item in resolutions
                    if item.status not in {"resolved", "partial"}
                    and self._is_persistent_write(item.candidate)
                ]
                if unresolved_writes:
                    # A compound write request is atomic at the conversation
                    # boundary: no resolved write may run while another write
                    # clause is still ambiguous or incomplete.
                    unresolved = unresolved_writes[0]
                    unresolved.safe_prompt = self._batch_clarification_prompt(
                        unresolved_writes,
                        total_write_count=sum(
                            1
                            for item in resolutions
                            if self._is_persistent_write(item.candidate)
                        ),
                    )
                unresolved_ids = {
                    item.candidate.action_id
                    for item in resolutions
                    if item.status not in {"resolved", "partial"}
                }
                independent_actions = [
                    item.resolved_action.to_dict()
                    for item in resolutions
                    if item.resolved_action is not None
                    and not any(
                        dependency in unresolved_ids
                        for dependency in item.candidate.depends_on
                    )
                ]
                if (
                    independent_actions
                    and unresolved.status != "confirmation_required"
                    and not unresolved_writes
                ):
                    partial_intent = {
                        **intent_result,
                        "intent": "resolved_actions",
                        "resolved_actions": independent_actions,
                        "clarification_question": None,
                        "confidence": min(
                            float(item.get("confidence", 1.0) or 1.0)
                            for item in independent_actions
                        ),
                        "needs_confirmation": False,
                    }
                    partial = self.agent_core.process(
                        clean_message,
                        partial_intent,
                        confirmation_scope=confirmation_scope,
                    )
                    if any(result.success for result in partial.tool_results):
                        partial.status = "partial_success"
                        partial.message = (
                            f"{unresolved.safe_prompt.rstrip('。')}；{partial.message}"
                        )
                        partial.conversation_id = confirmation_scope
                        partial = self.response_composer.compose(
                            partial,
                            user_text=clean_message,
                        )
                        self.interaction_coordinator.finish(
                            confirmation_scope,
                            partial=True,
                        )
                        self._record_response(
                            partial,
                            history_id,
                            record_history,
                            str(intent_result.get("intent", "")),
                        )
                        return ConversationTurn(
                            clean_message,
                            confirmation_scope,
                            partial_intent,
                            response=partial,
                            record_history=record_history,
                        )
                action_log_offer = (
                    unresolved.status == "not_found"
                    and unresolved.candidate.domain == "plan"
                    and unresolved.candidate.tool_name == "complete_plan"
                )
                kind = "action_log_offer" if action_log_offer else {
                    "plan": (
                        "duplicate_plan_resolution"
                        if unresolved.reason_code == "similar_plan_exists"
                        else "object_selection"
                        if unresolved.candidate_objects
                        else "missing_slots"
                    ),
                    "memory": "object_selection",
                    "action_log": "action_log_offer"
                    if unresolved.status == "confirmation_required"
                    else "missing_slots",
                }.get(unresolved.candidate.domain, "missing_slots")
                if unresolved.status == "confirmation_required" or action_log_offer:
                    resolved = unresolved.resolved_action
                    immutable = (
                        {
                            "tool_name": resolved.tool_name,
                            **dict(resolved.arguments),
                        }
                        if resolved is not None
                        else {}
                    )
                    if action_log_offer:
                        immutable = {
                            "tool_name": "add_action_log",
                            "content": clean_message,
                        }
                    if self.interaction_coordinator_enabled:
                        self.interaction_coordinator.awaiting_confirmation(
                            confirmation_scope,
                            kind,
                            immutable_arguments=immutable,
                            action_preview=self._action_preview(
                                immutable,
                                safe_summary=unresolved.safe_prompt,
                            ),
                            domain=unresolved.candidate.domain,
                            request_mode=unresolved.candidate.request_mode,
                            original_user_text=clean_message,
                            last_user_fact=(
                                str(immutable.get("content", ""))
                                if kind == "action_log_offer"
                                else ""
                            ),
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
                        candidate_objects=[
                            item
                            for item in unresolved.candidate_objects
                            if isinstance(item, dict)
                        ],
                        listed_object_ids=candidate_ids,
                        immutable_arguments={
                            "tool_name": unresolved.candidate.tool_name,
                            **dict(unresolved.candidate.arguments),
                        },
                        domain=unresolved.candidate.domain,
                        request_mode=unresolved.candidate.request_mode,
                        original_user_text=clean_message,
                        known_fields={
                            **{
                                key: value
                                for key, value in unresolved.candidate.arguments.items()
                                if value not in (None, "", [])
                            },
                            **(
                                {
                                    "suggestion_snapshot_selection": dict(
                                        intent_result["suggestion_snapshot_selection"]
                                    )
                                }
                                if isinstance(intent_result.get("suggestion_snapshot_selection"), Mapping)
                                else {}
                            ),
                        },
                        safe_summary=unresolved.safe_prompt,
                        originating_turn_id=str(intent_result.get("request_id", "")),
                    )
                mapped_status = (
                    "clarification"
                    if action_log_offer
                    or unresolved.status
                    in {
                        "missing",
                        "ambiguous",
                        "clarification_required",
                        "confirmation_required",
                    }
                    else "failed"
                )
                response = AgentResponse(
                    mapped_status,
                    sanitize_public_reply(unresolved.safe_prompt)
                    or "我还不能安全确定要执行的内容，请再说明一下。",
                    request_id=str(intent_result.get("request_id", "")) or None,
                    conversation_id=confirmation_scope,
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
            existing_interaction = self.interaction_coordinator.current(
                confirmation_scope
            )
            resolved_action_ids = [
                str(item.get("action_id", ""))
                for item in intent_result.get("resolved_actions", [])
                if isinstance(item, dict) and str(item.get("action_id", ""))
            ]
            if (
                existing_interaction.pending
                and existing_interaction.interaction_kind
                in {"object_selection", "plan_batch_completion"}
            ):
                # Preserve the displayed candidate identities while the chosen
                # object is executed.  A short follow-up such as "2.这个也完成了"
                # can then still refer to the same numbered list.
                self.interaction_coordinator.mark_tool_pending(
                    confirmation_scope,
                    resolved_action_ids,
                )
            else:
                self.interaction_coordinator.start(
                    confirmation_scope,
                    "multi_action_review"
                    if len(intent_result.get("resolved_actions", [])) > 1
                    else "missing_slot",
                    "awaiting_tool_result",
                    originating_turn_id=str(intent_result.get("request_id", "")),
                    action_candidates=intent_result.get("resolved_actions", []),
                )
        # ── PlanAuthorizationPolicy ─────────────────────────────────
        # Evaluate structured semantic fields (subject/polarity/modality)
        # on plan-affecting write tools.  NO_ACTION returns early;
        # PENDING sets needs_confirmation so the existing
        # ConfirmationManager + InteractionStateCoordinator chain
        # creates the pending.
        plan_auth = self._evaluate_plan_policy(semantic_result, intent_result)
        if plan_auth is not None:
            response = plan_auth
        else:
            memory_guard_arguments = (
                {"expected_confirmation_state": literal_forget_target}
                if literal_forget_target else {}
            )
            response = self.agent_core.process(
                clean_message,
                intent_result,
                confirmation_scope=confirmation_scope,
                **memory_guard_arguments,
            )
        response.conversation_id = history_id or conversation_id
        self._maybe_generate_formal_memory_answer(
            response, clean_message, confirmation_scope
        )
        self._maybe_generate_daily_review_summary(response, clean_message, confirmation_scope)
        self._maybe_generate_growth_log_summary(
            response, clean_message, confirmation_scope
        )
        if self.deterministic_response_enabled:
            response_preferences = self.state_manager.reference_context(
                confirmation_scope
            ).get("response_preferences", {})
            response = self.response_composer.compose(
                response,
                user_text=clean_message,
                verified_turn_context={
                    "response_preferences": (
                        dict(response_preferences)
                        if isinstance(response_preferences, Mapping)
                        else {}
                    )
                },
            )
        self._consume_suggestion_selection(
            response,
            confirmation_scope,
            intent_result,
        )
        self._apply_pending_state_note(response, pending_state_note)
        if self.interaction_coordinator_enabled:
            if response.status == "confirmation_required":
                pending = response.pending_confirmation or {}
                if hasattr(pending, "to_dict"):
                    pending = pending.to_dict()
                pending = pending if isinstance(pending, dict) else {}
                immutable = {
                    "tool_name": pending.get("tool", ""),
                    **(
                        dict(pending.get("arguments", {}))
                        if isinstance(pending.get("arguments"), dict)
                        else {}
                    ),
                }
                interaction_kind = (
                    "formal_memory_save"
                    if immutable.get("tool_name") == "save_formal_memory"
                    else "dangerous_tool"
                )
                self.interaction_coordinator.awaiting_confirmation(
                    confirmation_scope,
                    interaction_kind,
                    immutable_arguments=immutable,
                    action_preview=self._action_preview(
                        immutable,
                        safe_summary=response.message,
                    ),
                    safe_summary=response.message,
                    originating_turn_id=str(intent_result.get("request_id", "")),
                )
            elif response.status in {"completed", "partial_success"}:
                self._remember_read_result_from_response(
                    response,
                    confirmation_scope,
                    intent_result,
                )
                self._remember_formal_memory_from_response(
                    response,
                    confirmation_scope,
                    str(intent_result.get("request_id", "")),
                )
                self.interaction_coordinator.finish(
                    confirmation_scope,
                    partial=(response.status == "partial_success"),
                )
                self._start_duplicate_plan_review_from_response(
                    response,
                    confirmation_scope,
                    original_user_text=clean_message,
                    originating_turn_id=str(intent_result.get("request_id", "")),
                )
            elif response.status == "clarification":
                self.interaction_coordinator.update(
                    confirmation_scope,
                    state="awaiting_clarification",
                    safe_summary=response.message,
                    consumed=False,
                )
            elif response.status in {"failed", "error"}:
                self.interaction_coordinator.cancel(confirmation_scope)
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

        verified_turn = self._verified_chat_turn_context(
            intent_result,
            semantic_result,
            history_id or conversation_id,
            user_text=clean_message,
        )
        memory_evidence: List[Dict[str, object]] = []
        messages = self.build_llm_messages(
            clean_message,
            history_id or conversation_id,
            summary_mode=self._is_conversation_memory_summary_request(clean_message),
            verified_turn_context=verified_turn.to_model_text(),
            memory_evidence_sink=memory_evidence,
        )
        self.interaction_diagnostics.update(
            diagnostic_conversation_id,
            verified_turn_context=verified_turn.diagnostic_summary(),
        )
        return ConversationTurn(
            clean_message,
            history_id or conversation_id,
            intent_result,
            llm_messages=messages,
            record_history=record_history,
            verified_turn_context=verified_turn.to_dict(),
            memory_evidence=memory_evidence,
        )

    @_trace_phase("complete")
    def complete(self, turn: ConversationTurn) -> AgentResponse:
        if turn.response is not None:
            self._maybe_generate_formal_memory_answer(
                turn.response, turn.message, turn.conversation_id
            )
            self._maybe_generate_daily_review_summary(turn.response, turn.message, turn.conversation_id)
            self._maybe_generate_growth_log_summary(
                turn.response, turn.message, turn.conversation_id
            )
            if self.deterministic_response_enabled:
                response_preferences = self.state_manager.reference_context(
                    turn.conversation_id
                ).get("response_preferences", {})
                turn.response = self.response_composer.compose(
                    turn.response,
                    user_text=turn.message,
                    verified_turn_context={
                        "response_preferences": (
                            dict(response_preferences)
                            if isinstance(response_preferences, Mapping)
                            else {}
                        )
                    },
                )
            turn.response.message = sanitize_public_reply(turn.response.message)
            self._apply_pending_state_note(
                turn.response,
                str(turn.intent_result.get("pending_state_note", "")),
            )
            return turn.response
        if turn.resolve_intent_in_worker:
            resolved = self.prepare(
                turn.message,
                turn.conversation_id,
                record_history=turn.record_history,
                allow_llm_intent=True,
                state_prepared=True,
            )
            return self.complete(resolved)
        if turn.assistant_plan_source:
            raw_options = self._chat(turn.llm_messages, turn.request_id)
            model_options = self._assistant_plan_options(
                raw_options,
                turn.assistant_plan_source,
            )
            fallback_options = []
            if len(model_options) < 2:
                fallback_options = self._fallback_assistant_plan_options(
                    turn.assistant_plan_source
                )
            options = (
                fallback_options
                if len(fallback_options) >= 2
                else model_options
            )
            option_source = (
                "assistant_plan_fallback"
                if options is fallback_options
                else "assistant_plan_model"
                if options
                else "assistant_plan_empty"
            )
            self.interaction_diagnostics.update(
                turn.conversation_id,
                route_source="reference_resolution",
                semantic_parse_source=option_source,
                validation_notes=[
                    f"assistant_plan_model_options:{len(model_options)}",
                    f"assistant_plan_fallback_options:{len(fallback_options)}",
                    f"assistant_plan_selected_options:{len(options)}",
                ],
            )
            if not options:
                response = AgentResponse(
                    "clarification",
                    "我明白你指的是我刚才的回复，但里面没有能安全拆成今日计划的具体事项，所以这次没有修改计划。请直接告诉我想加入哪一件事。",
                    request_id=turn.request_id or None,
                    conversation_id=turn.conversation_id,
                )
                self._record_response(
                    response,
                    turn.conversation_id,
                    turn.record_history,
                    "assistant_plan_reference",
                )
                return response
            self.interaction_coordinator.awaiting_choice(
                turn.conversation_id,
                "assistant_plan_selection",
                options=options,
                listed_object_ids=[str(item["id"]) for item in options],
                immutable_arguments={"tool_name": "add_plan"},
                domain="plan",
                request_mode="execute",
                original_user_text=turn.message,
                safe_summary="请从刚才的安排中选择一项加入今天计划。",
                originating_turn_id=turn.request_id,
            )
            self.interaction_coordinator.remember_suggestion_snapshot(
                turn.conversation_id,
                SuggestionSnapshot.for_options(
                    conversation_id=turn.conversation_id,
                    options=options,
                    source_kind="assistant_plan_selection",
                    source_turn_id=turn.request_id,
                    captured_at=self.interaction_coordinator.now_provider(),
                ),
            )
            lines = [
                "我明白，你指的是我刚才给你的安排。为了不把整段建议混成一条计划，请选择今天先加入的一项："
            ]
            lines.extend(
                f"{index}. {item['title']}"
                for index, item in enumerate(options, 1)
            )
            lines.append("回复“第一个”“第二个”或直接说具体事项即可。")
            response = AgentResponse(
                "clarification",
                "\n".join(lines),
                request_id=turn.request_id or None,
                conversation_id=turn.conversation_id,
            )
            self._record_response(
                response,
                turn.conversation_id,
                turn.record_history,
                "assistant_plan_reference",
            )
            return response
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

        # Natural-language tool selection is complete before this method is
        # entered.  Keep ``model_action_adapter`` as an inert constructor
        # compatibility slot for older embedders, but never reopen its
        # JSON/native tool loop after a SemanticDecision has been validated.

        reply = self._chat(turn.llm_messages, turn.request_id)
        response = AgentResponse(
            status="chat",
            message=sanitize_public_reply(reply),
            request_id=turn.request_id or None,
            conversation_id=turn.conversation_id,
        )
        # Detect schema rejection to prevent chat replies from impersonating
        # business operations when the semantic decision was invalid.
        semantic_diagnostic = str(
            turn.intent_result.get("semantic_diagnostic", "")
        )
        schema_rejected = bool(
            semantic_diagnostic
            and any(
                marker in semantic_diagnostic
                for marker in (
                    "schema_invalid",
                    "json_parse_error",
                    "schema_missing",
                    "unsupported_intent",
                    "multi_action_requires_two_actions",
                    "clarify_without_question",
                )
            )
        )
        if self.deterministic_response_enabled:
            self._maybe_generate_daily_review_summary(response, turn.message, turn.conversation_id)
            response = self.response_composer.compose(
                response,
                user_text=turn.message,
                schema_rejected=schema_rejected,
                verified_turn_context=turn.verified_turn_context,
            )
        self._apply_pending_state_note(
            response,
            str(turn.intent_result.get("pending_state_note", "")),
        )
        self._mark_chat_memory_use(turn.memory_evidence, response.message)
        if self._is_plan_suggestion_turn(turn, response.message):
            if turn.preserve_advice_as_plan_options:
                self._remember_advice_plan_options(
                    response.message,
                    turn.conversation_id,
                    turn.request_id,
                    turn.plan_suggestion_source_text or turn.message,
                    refinement_used=turn.plan_suggestion_refinement_used,
                )
            else:
                self._remember_numbered_plan_suggestions(
                    response.message,
                    turn.conversation_id,
                    turn.request_id,
                    turn.plan_suggestion_source_text or turn.message,
                    refinement_used=turn.plan_suggestion_refinement_used,
                )
        self._record_response(
            response,
            turn.conversation_id,
            turn.record_history,
            str(turn.intent_result.get("intent", "chat")),
        )
        return response

    def _extract_approved_memory_content(
        self,
        source_message: str,
        current_message: str,
        *,
        request_id: str,
    ) -> str:
        """Extract grounded memory content without persisting assistant dialogue."""
        messages = [
            {
                "role": "system",
                "content": (
                    "你负责从上一条助手回复中提取经用户当前消息明确授权保存的长期记忆内容。"
                    "助手回复只是引用来源，不等于最终记忆正文。只保留其中由用户此前提供、"
                    "并被助手总结出的事实、目标、偏好、约束或计划；不要保留助手的提问、"
                    "确认说明、能力声明、承诺、建议邀请或‘回复后才会保存’之类交互话术。"
                    "不得新增、改写或推断信息。content 必须是来源回复中连续出现的原文片段；"
                    "如果没有可独立保存的内容，status 使用 no_stable_content。"
                    "只能输出 JSON："
                    '{"status":"extracted|no_stable_content","content":"..."}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_user_authorization": str(current_message or ""),
                        "assistant_source": str(source_message or ""),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = self._chat(messages, f"{request_id}:memory_reference_extract")
        value = str(raw or "").strip()
        if value.startswith("```"):
            value = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                value,
                flags=re.IGNORECASE,
            )
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return ""
        if not isinstance(data, dict) or data.get("status") != "extracted":
            return ""
        content = str(data.get("content", "") or "").strip()
        source = str(source_message or "").strip()
        if not content or self._compact(content) not in self._compact(source):
            return ""
        return content

    def _hydrate_memory_message_reference(
        self,
        candidates,
        reference_context: Dict[str, object],
        *,
        conversation_id: str,
        current_message: str,
        request_id: str,
    ) -> None:
        """Resolve only canonical message references from SemanticDecision."""
        resolver = self.business_resolver.reference_resolver
        for candidate in candidates:
            if candidate.tool_name != "save_formal_memory":
                continue
            content = str(candidate.arguments.get("content", "") or "").strip()
            marker = (
                "previous_user_message"
                if content in {"刚才说的", "刚才那句话"}
                else content
            )
            if marker not in {"previous_user_message", "previous_assistant_message"}:
                completed = self._complete_memory_time_slot(
                    content,
                    reference_context,
                )
                if completed:
                    candidate.arguments["content"] = completed
                    candidate.raw_entities["reference_resolution_reason"] = (
                        "previous_user_time_slot_completed"
                    )
                    candidate.raw_entities["source_text"] = completed
                    print(
                        "[ReferenceResolution] source=previous_user_time_slot "
                        "status=resolved",
                        flush=True,
                    )
                continue
            resolution = resolver.resolve_message_content(
                marker,
                reference_context,
                conversation_id=conversation_id,
            )
            candidate.raw_entities["message_reference"] = marker
            candidate.raw_entities["reference_resolution_reason"] = resolution.reason
            candidate.raw_entities["reference_role"] = resolution.role
            if resolution.content:
                resolved_content = resolution.content
                if marker == "previous_assistant_message":
                    resolved_content = self._extract_approved_memory_content(
                        resolution.content,
                        current_message,
                        request_id=request_id,
                    )
                    candidate.raw_entities["reference_content_status"] = (
                        "extracted" if resolved_content else "unresolved"
                    )
                candidate.arguments["content"] = resolved_content
                candidate.raw_entities["source_text"] = resolution.content
                print(
                    f"[ReferenceResolution] source={marker} status="
                    f"{'resolved' if resolved_content else 'clarification_required'}",
                    flush=True,
                )
            else:
                candidate.arguments["content"] = ""
                print(
                    f"[ReferenceResolution] source={marker} status=clarification_required "
                    f"reason={resolution.reason}",
                    flush=True,
                )

    @staticmethod
    def _complete_memory_time_slot(
        content: str,
        reference_context: Dict[str, object],
    ) -> str:
        """Join a bare remembered time to the fact it answers in this session."""
        value = str(content or "").strip().strip("。.!！?？")
        if not re.fullmatch(
            r"(?:上午|早上|中午|下午|晚上|今晚)?\s*"
            r"(?:[01]?\d|2[0-3]|[一二两三四五六七八九十]+)"
            r"(?:点|时)(?:半|\d{1,2}分)?",
            value,
        ):
            return ""
        previous = str(reference_context.get("last_user_message", "") or "").strip()
        assistant = str(
            reference_context.get("last_assistant_message", "") or ""
        ).strip()
        if (
            not previous
            or previous.strip("。.!！?？") == value
            or re.search(r"[吗么呢？?]$", previous.strip())
            or not re.search(r"(?:几点|什么时候|何时|几时)", assistant)
        ):
            return ""
        previous = previous.rstrip("。.!！?？；;，,")
        return f"{previous}，时间是{value}"

    def _assistant_plan_reference_source(
        self,
        semantic_result: Optional[SemanticParseResult],
        reference_context: Dict[str, object],
        *,
        current_message: str,
    ) -> Optional[str]:
        """Resolve an explicit reference to the preceding assistant plan.

        The returned message is source material for option extraction only. It
        is never used as an ``add_plan`` title or sent directly to a tool.
        """
        if semantic_result is None:
            return None
        plan_candidates = [
            candidate
            for candidate in semantic_result.candidates
            if candidate.domain == "plan" and candidate.tool_name == "add_plan"
        ]
        if (
            str(semantic_result.intent_result.get("intent", "")) != "add_plan"
            or semantic_result.request_mode != "execute"
            or len(plan_candidates) != 1
            or plan_candidates[0].request_mode != "execute"
        ):
            return None
        candidate_title = ""
        for candidate in plan_candidates:
            candidate_title = str(
                candidate.arguments.get("title", "") or ""
            ).strip()
            if candidate_title:
                break
        if not (
            self._contains_assistant_plan_reference(current_message)
            and self._contains_plan_write_destination(current_message)
        ):
            if not ReferenceResolver.is_previous_assistant_plan_reference(
                candidate_title
            ):
                return None
        conversation_id = str(reference_context.get("conversation_id", "") or "")
        if self.chat_history_manager is not None and conversation_id:
            recent = self.chat_history_manager.recent_messages(
                conversation_id,
                limit=12,
            )
            for item in reversed(recent):
                if str(item.get("role", "")) != "assistant":
                    continue
                content = str(item.get("content", "") or "").strip()
                intent = str(item.get("intent", "") or "").strip()
                # A user asking for an earlier "planning" reply normally means
                # a natural assistant turn, not a later tool acknowledgement.
                if content and intent in {"", "chat"}:
                    return content
        return str(reference_context.get("last_assistant_message", "") or "").strip()

    def _assistant_plan_reference_turn(
        self,
        clean_message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        source_message: str,
        intent_result: Dict[str, object],
    ) -> ConversationTurn:
        reference_intent = {
            **intent_result,
            "intent": "assistant_plan_reference",
            "source": "reference_resolution",
            "entities": {},
            "needs_confirmation": False,
            "request_id": str(intent_result.get("request_id", ""))
            or "req_"
            + uuid4().hex,
        }
        if not source_message:
            response = AgentResponse(
                "clarification",
                "我知道你指的是我刚才的安排，但当前对话里没有可展开的具体步骤，所以这次没有修改计划。你可以直接告诉我想加入哪一项。",
                conversation_id=conversation_id,
                request_id=str(reference_intent["request_id"]),
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "assistant_plan_reference",
            )
            return ConversationTurn(
                clean_message,
                conversation_id,
                reference_intent,
                response=response,
                record_history=record_history,
            )
        # A validated suggestion snapshot is stronger evidence than a second
        # model extraction.  Re-extracting an already displayed five-item list
        # used to shrink it to two or three items before the user could choose.
        # Reuse the full ordered snapshot only when every remaining title is
        # grounded in the assistant message currently being referenced.
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        source = self._compact(source_message)
        snapshot_options = []
        if snapshot is not None:
            snapshot_options = [
                {"id": item.stable_id, "title": item.title}
                for item in snapshot.objects
                if not item.consumed
                and item.title
                and self._is_grounded_assistant_plan_title(item.title, source)
            ]
        if len(snapshot_options) >= 2:
            if self._implicit_whole_suggestion_reference(clean_message):
                return self._execute_suggestion_plan_batch(
                    clean_message,
                    conversation_id,
                    history_id,
                    record_history,
                )
            self.interaction_coordinator.awaiting_choice(
                conversation_id,
                "assistant_plan_selection",
                options=snapshot_options,
                listed_object_ids=[str(item["id"]) for item in snapshot_options],
                immutable_arguments={"tool_name": "add_plan"},
                domain="plan",
                request_mode="execute",
                original_user_text=clean_message,
                safe_summary="请从刚才的安排中选择要加入今天计划的项目。",
                originating_turn_id=str(reference_intent["request_id"]),
            )
            lines = [
                "我明白，你指的是我刚才给你的安排。请选择要加入今天计划的项目："
            ]
            lines.extend(
                f"{index}. {item['title']}"
                for index, item in enumerate(snapshot_options, 1)
            )
            lines.append("可以说“第一个”“1、2、3”或“全部加入今日计划”。")
            response = AgentResponse(
                "clarification",
                "\n".join(lines),
                request_id=str(reference_intent["request_id"]),
                conversation_id=conversation_id,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "assistant_plan_reference_snapshot",
            )
            return ConversationTurn(
                clean_message,
                conversation_id,
                reference_intent,
                response=response,
                record_history=record_history,
            )
        return ConversationTurn(
            clean_message,
            conversation_id,
            reference_intent,
            llm_messages=self._assistant_plan_option_messages(source_message),
            record_history=record_history,
            assistant_plan_source=source_message,
        )

    @staticmethod
    def _contains_assistant_plan_reference(text: str) -> bool:
        """Detect a contextual reference independently from its write target."""
        compact = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
        return bool(
            re.search(
                r"(?:你(?:刚才|刚刚|方才|上面|前面)?"
                r"(?:说的|说|给我(?:的)?|对我(?:的)?|梳理的|整理的|整理出来的|"
                r"规划的|列的|提到的|安排的)"
                r"|你上面(?:对我(?:的)?|给我(?:的)?)"
                r"|(?:上面|前面|刚才|刚刚|方才)"
                r"(?:说的|的|梳理的|整理的|整理出来的|规划的|列的|提到的|安排的))"
                r"(?:这?(?:段|个))?(?:规划|安排|建议|回复|内容|步骤)?",
                compact,
            )
        )

    @staticmethod
    def _contains_plan_write_destination(text: str) -> bool:
        """Detect a plan destination without depending on semantic parsing."""
        compact = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
        return bool(
            re.search(
                r"(?:加入|添加|加进|放进|放入|纳入|安排到|安排进|塞进|放到|加到)"
                r"(?:我)?(?:今天|今日)?(?:的)?(?:计划|任务|清单|待办)(?:里)?",
                compact,
            )
        )

    @staticmethod
    def _assistant_plan_option_messages(
        assistant_text: str,
    ) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你负责从上一条助手回复中提取可执行的今日计划候选。"
                    "只提取助手回复中明确出现的具体步骤或任务，不要创造新任务，"
                    "不要把解释、鼓励、提问或泛泛建议写进去。最多输出3项，"
                    "每项标题简洁、可执行、适合直接放入今日计划。"
                    "如果没有明确事项，输出 {\"options\":[]}。"
                    "只能输出JSON，格式是："
                    "{\"options\":[{\"id\":\"1\",\"title\":\"具体事项\"}]}"
                ),
            },
            {"role": "user", "content": str(assistant_text or "")},
        ]

    def _assistant_plan_options(
        self,
        raw: str,
        assistant_text: str,
    ) -> List[Dict[str, str]]:
        """Validate extracted options before putting them into pending state."""
        value = str(raw or "").strip()
        if value.startswith("```"):
            value = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                value,
                flags=re.IGNORECASE,
            )
        try:
            data = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        raw_options = data.get("options") if isinstance(data, dict) else None
        if not isinstance(raw_options, list):
            return []
        entity_parser = getattr(
            getattr(self.semantic_action_parser, "feature_extractor", None),
            "entity_parser",
            None,
        )
        source = self._compact(assistant_text)
        options: List[Dict[str, str]] = []
        seen = set()
        for item in raw_options[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or "").strip()
            if entity_parser is not None:
                title = entity_parser.clean_plan_title(title)
            compact = self._compact(title)
            if (
                not title
                or len(title) > 120
                or compact in seen
                or ReferenceResolver.is_previous_assistant_plan_reference(title)
                or not self._is_grounded_assistant_plan_title(title, source)
            ):
                continue
            seen.add(compact)
            options.append({"id": str(len(options) + 1), "title": title})
        return options

    def _remember_advice_plan_options(
        self,
        assistant_reply: str,
        conversation_id: str,
        request_id: str,
        original_user_text: str,
        *,
        refinement_used: bool = False,
    ) -> None:
        # Prefer the exact displayed list.  A pending advice turn can still
        # return natural prose, though, so use the same grounded, schema-checked
        # extractor as an explicit previous-assistant-plan reference.  Only
        # titles anchored in the actual reply can enter the snapshot.
        options = SuggestionSnapshot.numbered_options(assistant_reply)
        if not options:
            raw_options = self._chat(
                self._assistant_plan_option_messages(assistant_reply),
                request_id,
            )
            options = self._assistant_plan_options(raw_options, assistant_reply)
        snapshot_text = assistant_reply
        if options:
            snapshot_text = "\n".join(
                f"{index}. {item['title']}"
                for index, item in enumerate(options, 1)
            )
        self._remember_numbered_plan_suggestions(
            snapshot_text,
            conversation_id,
            request_id,
            original_user_text,
            refinement_used=refinement_used,
        )

    def _is_plan_suggestion_turn(
        self,
        turn: ConversationTurn,
        assistant_reply: str = "",
    ) -> bool:
        """Keep advice purpose/provenance, not every numbered chat answer."""
        if not self.interaction_coordinator_enabled:
            return False
        if turn.preserve_advice_as_plan_options:
            return True
        intent = turn.intent_result
        if (
            str(intent.get("intent", "")) != "chat"
            or str(intent.get("pipeline_outcome", "")) == "reject"
            or str(intent.get("mode", "")) in {"read", "write", "clarify"}
        ):
            return False
        extractor = getattr(self.semantic_action_parser, "feature_extractor", None)
        if extractor is None:
            return False
        features = extractor.extract(turn.message)
        semantic_advice = str(intent.get("request_mode", "")) == "advice"
        advice = semantic_advice or bool(features.advice_cues)
        if not advice:
            return False
        normalized_message = str(features.normalized_text or "")
        declines_advice = bool(
            re.search(
                r"(?:不要|别|不用|不想|先不).{0,8}(?:给|提供|推荐|建议)"
                r"|(?:计划)?建议(?:先)?(?:不要了|不用了|取消)",
                normalized_message,
            )
        )
        if (
            (features.cancellation_cues or declines_advice)
            and not self._suggestion_collection_negation(turn.message)
        ):
            # "先不要给建议" cancels the advice turn.  In contrast,
            # "给我建议，先不要加入计划" only rejects an immediate write;
            # retaining the numbered list is read-only and lets a later,
            # explicit selection use exactly what the user saw.
            return False
        state = self.interaction_coordinator.current(turn.conversation_id)
        if (
            state.domain == "plan"
            and state.interaction_kind in {
                "missing_slots",
                "missing_slot",
                "advice_choice",
                "advice_or_action_choice",
                "assistant_plan_offer",
                "assistant_plan_refinement",
                "assistant_plan_selection",
            }
            and SuggestionSnapshot.numbered_options(assistant_reply)
        ):
            # A short slot follow-up such as “我能学习三个小时” does not repeat
            # the word “建议”. Preserve the still-typed plan context when the
            # assistant visibly returns a validated numbered option list.
            return True
        if semantic_advice:
            return True
        plan_terms = getattr(extractor, "DOMAIN_TERMS", {}).get("plan", ())
        plan_topic = "plan" in features.domain_cues or any(
            str(term) in normalized_message for term in plan_terms
        )
        if plan_topic or features.date_expressions:
            return True
        return bool(
            state.domain == "plan"
            and state.interaction_kind in {"advice_choice", "advice_or_action_choice"}
            and str(intent.get("source", "")) == "interaction_state"
        )

    def _remember_numbered_plan_suggestions(
        self,
        assistant_reply: str,
        conversation_id: str,
        request_id: str,
        original_user_text: str,
        *,
        refinement_used: bool = False,
    ) -> None:
        if not self.interaction_coordinator_enabled:
            return
        options = SuggestionSnapshot.numbered_options(assistant_reply)
        self.interaction_diagnostics.update(
            conversation_id,
            validation_notes=[f"advice_plan_displayed_options:{len(options)}"],
        )
        if not options:
            self.interaction_coordinator.update(conversation_id, last_suggestion_snapshot={})
            state = self.interaction_coordinator.current(conversation_id)
            if state.interaction_kind in {
                "assistant_plan_offer",
                "assistant_plan_refinement",
                "assistant_plan_selection",
                "advice_or_action_choice",
            }:
                self.interaction_coordinator.cancel(conversation_id)
            return
        now = self.interaction_coordinator.now_provider()
        parsed = self.semantic_action_parser.feature_extractor.extract(original_user_text).parsed_time
        target_date = str(parsed.get("date", "") or now.date().isoformat())
        if target_date != now.date().isoformat():
            # A dated suggestion for another day must not silently become a
            # default-today shortcut in a later reply.
            self.interaction_coordinator.update(conversation_id, last_suggestion_snapshot={})
            return
        self.interaction_coordinator.awaiting_choice(
            conversation_id,
            "assistant_plan_selection" if refinement_used else "assistant_plan_offer",
            options=options,
            listed_object_ids=[str(item["id"]) for item in options],
            immutable_arguments={"tool_name": "add_plan"},
            domain="plan",
            request_mode="execute",
            original_user_text=original_user_text,
            known_fields={"suggestion_refinement_used": bool(refinement_used)},
            safe_summary="已保留刚才建议中的可执行事项，等待用户决定是否加入计划。",
            originating_turn_id=request_id,
        )
        self.interaction_coordinator.remember_suggestion_snapshot(
            conversation_id,
            SuggestionSnapshot.for_options(
                conversation_id=conversation_id,
                options=options,
                source_kind="assistant_advice",
                source_turn_id=request_id,
                captured_at=now,
                target_date=target_date,
            ),
        )

    def _fallback_assistant_plan_options(
        self,
        assistant_text: str,
    ) -> List[Dict[str, str]]:
        """Extract explicitly ordered task headings when model JSON is unusable."""
        source = str(assistant_text or "").strip()
        if not source:
            return []
        entity_parser = self.semantic_action_parser.feature_extractor.entity_parser
        schedule = entity_parser.extract_plan_schedule(source)
        schedule_items = schedule.get("items", [])
        if len(schedule_items) >= 2:
            return [
                {"id": str(index), "title": str(item["title"])}
                for index, item in enumerate(schedule_items[:3], 1)
                if item.get("title")
            ]
        matches = re.findall(
            r"(?:"
            r"第[一二三123](?:步|条|项)(?:\s*[、，,:：.)）])?"
            r"|第[一二三123]\s*[、，,:：.)）]"
            r"|[一二三123]\s*[、，,:：.)）]"
            r")\s*"
            r"([^。！？!?；;\n]+)",
            source,
        )
        if len(matches) < 2:
            matches = re.findall(
                r"(?:先|首先)\s*([^，。；;]+).*?"
                r"(?:再|然后|接着)\s*([^，。；;]+).*?"
                r"最后\s*([^，。；;]+)",
                source,
                flags=re.DOTALL,
            )
            matches = list(matches[0]) if matches else []
        options: List[Dict[str, str]] = []
        seen = set()
        for value in matches[:3]:
            title = re.sub(r"^[：:，,\s]+|[：:，,\s]+$", "", str(value))
            title = self._compact(title)
            if not title or title in seen or len(title) > 80:
                continue
            seen.add(title)
            options.append({"id": str(len(options) + 1), "title": title})
        return options

    @staticmethod
    def _is_grounded_assistant_plan_title(title: str, source: str) -> bool:
        """Accept a concise option only when it can be anchored in the reply.

        The model may normalize wording such as "装一个叫 scikit-learn 的库"
        into "安装 scikit-learn".  A literal-substring rule would reject that
        useful compression, while an ungrounded title could invent a task.
        """
        candidate = ConversationService._compact(title)
        if candidate and candidate in source:
            return True
        identifiers = re.findall(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,}",
            candidate.lower(),
        )
        return bool(identifiers) and all(
            token in source.lower() for token in identifiers
        )

    def set_memory_candidates_enabled(self, enabled: bool) -> None:
        self.enable_memory_candidates = bool(enabled)
        if self.memory_governance is not None:
            self.memory_governance.set_enabled(enabled)

    def _resume_pending_interaction(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
    ):
        """Continue a bounded pending task before starting fresh semantic routing."""
        state = self.interaction_coordinator.current(conversation_id)
        if not state.pending:
            recent_selection = self._resume_recent_completed_plan_selection(
                message,
                conversation_id,
                state,
            )
            if recent_selection is not None:
                return None, recent_selection
            return None, None
        clean = str(message).strip()
        normalized = self._compact(clean)
        if normalized in {"取消", "算了", "不用了", "不要了", "先不"}:
            return None, None
        # A complete explicit command must start a fresh route even while an
        # older interaction is missing a slot.  Otherwise the pending state
        # consumes the new command as if it were a title or duration answer.
        confirms_pending_plan_choice = (
            (
                state.interaction_kind == "advice_or_action_choice"
                and self._is_plan_add_choice(
                    normalized,
                    scoped_plan_pending=(state.domain == "plan"),
                )
            )
            or (
                state.interaction_kind
                in {"assistant_plan_offer", "assistant_plan_selection"}
                and bool(self._collection_control(clean, "add_plan"))
            )
        )
        if self._is_complete_plan_command(clean) and not confirms_pending_plan_choice:
            self.interaction_coordinator.cancel(conversation_id)
            return None, None
        if self._is_fresh_query(normalized):
            self.interaction_coordinator.cancel(conversation_id)
            return None, None

        if state.interaction_kind == "action_log_offer":
            if self._confirms_action_log_offer(normalized):
                arguments = dict(state.immutable_arguments)
                tool_name = str(arguments.pop("tool_name", "add_action_log"))
                return None, self._semantic_from_continuation(
                    clean,
                    tool_name,
                    arguments,
                    domain="action_log",
                )
            self.interaction_coordinator.cancel(conversation_id)
            return None, None

        if state.interaction_kind == "plan_batch_completion":
            if normalized in {"确认", "确认吧", "是的", "执行", "继续", "都完成"}:
                plan_service = getattr(self.business_resolver, "plan_service", None)
                plans = plan_service.list_plans() if plan_service is not None else []
                plans_by_id = {
                    str(item.get("uid") or item.get("id")): dict(item)
                    for item in plans
                    if isinstance(item, dict)
                }
                targets = [
                    plans_by_id[item_id]
                    for item_id in state.selected_object_ids
                    if item_id in plans_by_id
                ]
                if len(targets) == len(state.selected_object_ids) and targets:
                    return self._execute_confirmed_plan_batch(
                        clean,
                        conversation_id,
                        history_id,
                        record_history,
                        targets,
                    ), None
                return self._snapshot_failure_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "确认前计划状态发生了变化，因此这次没有修改任何计划。请重新查看今日计划后再试。",
                ), None
            return None, None

        if (
            state.interaction_kind == "memory_candidate_review"
            and state.listed_object_ids
        ):
            candidate_review = self._memory_candidate_review_continuation(
                clean,
                state,
            )
            if candidate_review is not None:
                return None, candidate_review

        if state.state in {
            "awaiting_confirmation",
            "awaiting_tool_result",
            "candidates_listed",
        }:
            return None, None

        if state.interaction_kind == "duplicate_plan_resolution":
            return self._resume_duplicate_plan_resolution(
                clean,
                normalized,
                conversation_id,
                history_id,
                record_history,
                state,
            )

        if state.interaction_kind == "suggestion_batch_duplicate_resolution":
            choice = self._suggestion_batch_duplicate_choice(normalized)
            if choice:
                common_arguments = state.immutable_arguments.get(
                    "common_arguments", {}
                )
                return self._execute_suggestion_plan_batch(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    selected_object_ids=list(state.selected_object_ids),
                    duplicate_policy=choice,
                    common_arguments=(
                        dict(common_arguments)
                        if isinstance(common_arguments, Mapping)
                        else {}
                    ),
                    expected_snapshot_id=str(
                        state.immutable_arguments.get("snapshot_id", "") or ""
                    ),
                ), None
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "请回复“添加未重复项”“仍然全部添加”，或“取消”。",
                bounded_pending=True,
            ), None

        if state.interaction_kind == "memory_or_plan_destination":
            arguments = dict(state.immutable_arguments)
            memory_arguments = arguments.get("memory_arguments", {})
            plan_arguments = arguments.get("plan_arguments", {})
            if self._contains_plan_write_destination(clean):
                return None, self._semantic_from_continuation(
                    clean,
                    "add_plan",
                    dict(plan_arguments) if isinstance(plan_arguments, dict) else {},
                    domain="plan",
                )
            if self._is_long_term_memory_destination(normalized):
                return None, self._semantic_from_continuation(
                    clean,
                    "save_formal_memory",
                    dict(memory_arguments) if isinstance(memory_arguments, dict) else {},
                    domain="memory",
                )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "这是只对今天有效的安排。你想把它加入今日计划，还是保存为长期记忆？",
                bounded_pending=True,
            ), None

        if state.interaction_kind == "plan_update":
            changes = self._pending_plan_changes(clean)
            selected_ref = next(iter(state.selected_object_ids), "")
            if changes and selected_ref:
                return None, self._semantic_from_continuation(
                    clean,
                    "update_plan",
                    {"task_ref": selected_ref, "changes": changes},
                    domain="plan",
                )
            # Broader wording remains a semantic-model decision. The stable
            # target is injected before schema validation below.
            return None, None

        if state.interaction_kind == "object_selection":
            pending_arguments = dict(state.immutable_arguments)
            pending_tool = str(pending_arguments.get("tool_name", ""))
            if (
                state.domain == "plan"
                and "reopen_plan" in {str(item) for item in state.missing_fields}
                and len(state.listed_object_ids) == 1
                and self._confirms_completed_plan_reopen(normalized)
            ):
                return None, self._semantic_from_continuation(
                    clean,
                    "reopen_plan",
                    {"task_ref": state.listed_object_ids[0]},
                    domain="plan",
                )
            if (
                state.domain == "plan"
                and pending_tool == "add_plan"
                and self._is_duplicate_plan_confirmation(normalized)
            ):
                pending_arguments.pop("tool_name", None)
                pending_arguments["allow_duplicate"] = True
                return None, self._semantic_from_continuation(
                    clean,
                    "add_plan",
                    pending_arguments,
                    domain="plan",
                    suggestion_selection=state.known_fields.get("suggestion_snapshot_selection"),
                )
            selections = self._selected_pending_objects(normalized, state)
            if selections:
                arguments = dict(state.immutable_arguments)
                tool_name = str(arguments.pop("tool_name", ""))
                selected_objects = [
                    item
                    for selection in selections
                    for item in state.action_candidates
                    if str(item.get("uid") or item.get("id")) == selection
                ]
                if tool_name in {"complete_plan"}:
                    return None, self._semantic_from_object_selections(
                        clean,
                        tool_name,
                        arguments,
                        selected_objects,
                        domain=state.domain,
                    )
                selection = selections[0]
                if "plan" in tool_name:
                    arguments["task_ref"] = selection
                elif "candidate" in tool_name:
                    arguments["candidate_id"] = int(selection)
                elif "memory" in tool_name:
                    arguments["memory_id"] = int(selection)
                return None, self._semantic_from_continuation(
                    clean,
                    tool_name,
                    arguments,
                    domain=state.domain,
                )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "我找到了几项可能的内容，请说“第一个”“第二个”，或直接告诉我编号。",
                bounded_pending=True,
            ), None

        if state.domain != "plan" or state.interaction_kind not in {
            "missing_slots",
            "missing_slot",
            "parameter_retry",
            "advice_choice",
            "advice_or_action_choice",
            "assistant_plan_offer",
            "assistant_plan_refinement",
            "assistant_plan_selection",
        }:
            return None, None

        pending_tool = str(state.immutable_arguments.get("tool_name", "")).strip()
        if pending_tool == "complete_plan":
            # A missing completion target remains a completion interaction.
            # It must never fall through to the add-plan duration workflow.
            if self._collection_control(clean, "complete_plan") == "all":
                return self._start_plan_batch_completion(
                    clean, conversation_id, history_id, record_history,
                    selected_all=True,
                ), None
            match_text = clean.strip(" ，,。.!！?？")
            if match_text:
                return None, self._semantic_from_continuation(
                    clean,
                    "complete_plan",
                    {"match_text": match_text},
                    domain="plan",
                )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "请告诉我具体完成了哪一项计划。",
                bounded_pending=True,
            ), None

        if state.interaction_kind in {
            "assistant_plan_offer",
            "assistant_plan_selection",
        }:
            selection_text = self._positive_suggestion_correction(clean) or clean
            selection_normalized = self._compact(selection_text)
            if self._suggestion_operation_help_request(selection_text):
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "如果要把刚才的建议全部加入，可以说“把你说的全部加入今日计划”；"
                    "只加入一项，可以说“把第二项加入今日计划”。",
                ), None
            if self._suggestion_refinement_follow_up(selection_text, state):
                original_request = str(state.original_user_text or "").strip()
                source_text = "\n".join(
                    part
                    for part in (
                        original_request,
                        f"补充要求：{selection_text}",
                    )
                    if part
                )
                prompt = (
                    f"用户原始需求：{original_request}\n"
                    + f"用户补充：{selection_text}\n"
                    "请根据原始需求和补充条件重新给出二至四项计划建议。"
                    "严格遵守原始日期、时间范围、时长和补充主题；"
                    "每行只写一个‘编号. 可单独成为计划标题的动作’，不要追问，也不要声称已经加入计划。"
                )
                request_id = "req_" + uuid4().hex
                memory_evidence: List[Dict[str, object]] = []
                self.interaction_coordinator.update(
                    conversation_id,
                    state="awaiting_clarification",
                    interaction_kind="assistant_plan_refinement",
                    original_user_text=source_text,
                    known_fields={"suggestion_refinement_used": True},
                    retry_count=1,
                    safe_summary="正在依据用户补充调整当前计划建议。",
                )
                return ConversationTurn(
                    clean,
                    conversation_id,
                    {
                        "intent": "chat",
                        "source": "interaction_state",
                        "request_mode": "advice",
                        "request_id": request_id,
                    },
                    llm_messages=self.build_llm_messages(
                        prompt,
                        conversation_id,
                        memory_evidence_sink=memory_evidence,
                    ),
                    record_history=record_history,
                    preserve_advice_as_plan_options=True,
                    plan_suggestion_source_text=source_text,
                    plan_suggestion_refinement_used=True,
                    memory_evidence=memory_evidence,
                ), None
            if self._suggestion_collection_negation(selection_text):
                self.interaction_coordinator.cancel(conversation_id)
                response = AgentResponse(
                    "completed",
                    "好，这次没有添加这些建议。",
                    conversation_id=conversation_id,
                )
                self._record_response(
                    response,
                    history_id,
                    record_history,
                    "suggestion_collection_cancelled",
                )
                return ConversationTurn(
                    clean,
                    conversation_id,
                    {"intent": "cancel", "source": "interaction_state"},
                    response=response,
                    record_history=record_history,
                ), None
            if (
                self._unsafe_suggestion_reference(selection_text)
                and self._contains_assistant_plan_reference(selection_text)
                and self._contains_plan_write_destination(selection_text)
                and bool(re.search(r"[?？]|(?:吗|么|呢)[。.!！]?$", selection_text))
            ):
                # A polite question about a verified list is not permission to
                # write. Re-display every current option without cancelling the
                # typed selection or asking an unrelated generic question.
                verified_source = "\n".join(
                    str(item.get("title", "") or "").strip()
                    for item in state.suggested_options
                    if isinstance(item, dict)
                    and str(item.get("title", "") or "").strip()
                )
                return self._assistant_plan_reference_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    verified_source,
                    {
                        "intent": "add_plan",
                        "source": "interaction_state",
                        "request_id": "req_" + uuid4().hex,
                    },
                ), None
            if self._unsafe_suggestion_reference(selection_text):
                if self._suggestion_collection_question(selection_text):
                    return self._interaction_turn(
                        clean,
                        conversation_id,
                        history_id,
                        record_history,
                        "这句话像是在询问，所以我没有添加计划。"
                        "如果要执行，请直接说“全部加入今日计划”。",
                        bounded_pending=True,
                    ), None
                self.interaction_coordinator.cancel(conversation_id)
                return None, None
            collection_control = self._collection_control(selection_text, "add_plan")
            option = self._selected_assistant_plan_option(selection_normalized, state)
            plan_add_choice = self._is_plan_add_choice(
                selection_normalized, scoped_plan_pending=(state.domain == "plan"),
            )
            implicit_whole_reference = (
                option is None
                and plan_add_choice
                and self._implicit_whole_suggestion_reference(selection_text)
            )
            if not (
                collection_control
                or implicit_whole_reference
                or option is not None
                or self._suggestion_reference_position(selection_text) > 0
                or plan_add_choice
            ):
                # Retain the independent displayed list for a later explicit
                # reference, but let unrelated conversation start a fresh turn.
                self.interaction_coordinator.cancel(conversation_id)
                return None, None
            snapshot = self._recent_suggestion_snapshot(conversation_id)
            if snapshot is None:
                return self._snapshot_failure_turn(
                    clean, conversation_id, history_id, record_history,
                    "刚才的建议列表不存在或已失效，这次没有添加计划。请重新获取建议，或直接说具体事项。",
                ), None
            if collection_control or implicit_whole_reference:
                return self._execute_suggestion_plan_batch(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                ), None
            if (
                option is None
                and len(state.suggested_options) == 1
                and self._contains_plan_write_destination(selection_text)
            ):
                option = state.suggested_options[0]
            if option is None:
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "请从刚才的安排中选择一项，例如说“第一个”“第二个”，或直接说具体事项。",
                    bounded_pending=True,
                ), None
            title = str(option.get("title", "")).strip()
            selected = next(
                (item for item in snapshot.objects if item.stable_id == str(option.get("id", "")) and item.title == title),
                None,
            )
            if selected is None or selected.consumed:
                return self._snapshot_failure_turn(
                    clean, conversation_id, history_id, record_history,
                    "这一项建议已经加入或当前不可用，这次没有重复添加计划。请重新选择尚未加入的项目。",
                ), None
            if not title:
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "这项安排没有可执行的具体内容，请直接告诉我想加入哪一项。",
                    bounded_pending=True,
                ), None
            continuation = self._semantic_from_continuation(
                clean,
                "add_plan",
                {"title": title},
                domain="plan",
            )
            self._annotate_suggestion_selection(
                continuation,
                conversation_id=conversation_id,
                option=option,
            )
            return None, continuation

        if state.interaction_kind == "advice_or_action_choice":
            if re.search(r"(?:建议|怎么学|从哪开始|现在开始)", normalized):
                topic = str(state.known_fields.get("title", "")).strip()
                prompt = f"关于{topic}，请给我一个简洁的学习建议。" if topic else clean
                request_id = "req_" + uuid4().hex
                memory_evidence: List[Dict[str, object]] = []
                llm_messages = self.build_llm_messages(
                    prompt,
                    conversation_id,
                    memory_evidence_sink=memory_evidence,
                )
                return ConversationTurn(
                    clean,
                    conversation_id,
                    {
                        "intent": "chat",
                        "source": "interaction_state",
                        "request_mode": "advice",
                        "request_id": request_id,
                    },
                    llm_messages=llm_messages,
                    record_history=record_history,
                    preserve_advice_as_plan_options=True,
                    memory_evidence=memory_evidence,
                ), None
            if not self._is_plan_add_choice(
                normalized,
                scoped_plan_pending=(state.domain == "plan"),
            ):
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "你想先听学习建议、现在开始，还是把它加入今天计划？",
                    bounded_pending=True,
                ), None
            tasks = state.suggested_options
            if isinstance(tasks, list) and tasks:
                return None, self._semantic_from_continuation(
                    clean,
                    "add_plan",
                    {"tasks": [dict(item) for item in tasks if isinstance(item, dict)]},
                    domain="plan",
                )

        known = dict(state.known_fields)
        suggestion_selection = known.pop("suggestion_snapshot_selection", {})
        immutable = dict(state.immutable_arguments)
        immutable.pop("tool_name", None)
        known.update(
            {
                key: value
                for key, value in immutable.items()
                if value not in (None, "", [])
            }
        )
        features = self.semantic_action_parser.feature_extractor.extract(clean)

        if state.interaction_kind == "advice_choice":
            option = self._selected_suggestion(normalized, state.suggested_options)
            if option is not None:
                known["title"] = str(option.get("title", "")).strip()
                suggestion_selection = self._suggestion_selection_for_option(
                    conversation_id=conversation_id, option=option
                )
            elif not any(term in normalized for term in ("不知道", "你安排", "给建议")):
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "请从刚才的建议里选一个，例如说“第二个，一个小时”。",
                    bounded_pending=True,
                ), None

        if any(term in normalized for term in ("不知道", "你给我安排", "你安排一下", "给我建议")):
            options = self._default_learning_options()
            self.interaction_coordinator.update(
                conversation_id,
                state="awaiting_choice",
                interaction_kind="advice_choice",
                suggested_options=options,
                listed_object_ids=[item["id"] for item in options],
                immutable_arguments={"tool_name": "add_plan", **known},
                domain="plan",
                request_mode="possible_action",
                original_user_text=state.original_user_text,
                known_fields=known,
                safe_summary="请选一个学习方向，再告诉我准备学多久。",
                originating_turn_id=state.originating_turn_id,
            )
            self.interaction_coordinator.remember_suggestion_snapshot(
                conversation_id,
                SuggestionSnapshot.for_options(
                    conversation_id=conversation_id,
                    options=options,
                    source_kind="local_learning_options",
                    source_turn_id=state.originating_turn_id,
                    captured_at=self.interaction_coordinator.now_provider(),
                ),
            )
            lines = ["可以。下午先从一个小方向开始，你可以选："]
            lines.extend(
                f"{index}. {item['title']}" for index, item in enumerate(options, 1)
            )
            lines.append("选一个，再告诉我准备学多久。")
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "\n".join(lines),
                bounded_pending=True,
            ), None

        parsed = features.parsed_time
        if parsed.get("date"):
            known["date"] = parsed["date"]
        if parsed.get("time_period"):
            known["time_slot"] = parsed["time_period"]
        if parsed.get("duration_minutes") is not None:
            known["duration_minutes"] = parsed["duration_minutes"]
        if str(known.get("title", "") or "").strip() in {
            "",
            "学一会儿",
            "学习一会儿",
            "做一会儿",
        }:
            title = self._followup_plan_title(clean)
            if title:
                known["title"] = title

        # A choice such as “就把刚才这个安排进今天计划” must consume the
        # current pending target.  Never replace it with the pronoun or with
        # wording copied from an assistant suggestion.
        if (
            state.domain == "plan"
            and str(known.get("title", "") or "").strip()
            and self._is_current_plan_reference(normalized)
        ):
            known["title"] = str(known["title"]).strip()

        missing = []
        if not str(known.get("title", "")).strip():
            missing.append("title")
        if (
            "duration_minutes" in state.missing_fields
            and state.interaction_kind != "advice_or_action_choice"
            and known.get("duration_minutes") in (None, "")
        ):
            missing.append("duration_minutes")
        if missing:
            question = (
                "想学什么内容，大概安排多久？"
                if len(missing) == 2
                else "准备学多久？例如半小时或一小时。"
                if missing == ["duration_minutes"]
                else "想学什么内容？"
            )
            self.interaction_coordinator.update(
                conversation_id,
                state="awaiting_clarification",
                interaction_kind="missing_slots",
                known_fields={
                    **known,
                    **(
                        {"suggestion_snapshot_selection": dict(suggestion_selection)}
                        if suggestion_selection else {}
                    ),
                },
                missing_fields=missing,
                immutable_arguments={"tool_name": "add_plan", **known},
                safe_summary=question,
            )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                question,
                bounded_pending=True,
            ), None

        return None, self._semantic_from_continuation(
            clean,
            "add_plan",
            known,
            domain="plan",
            suggestion_selection=suggestion_selection,
        )

    def _resume_recent_completed_plan_selection(
        self,
        message: str,
        conversation_id: str,
        state,
    ) -> Optional[SemanticParseResult]:
        """Resolve one explicit follow-up against the immediately prior list.

        Completed interactions are normally closed.  The narrow exception here
        is a numbered plan candidate list whose first selected item just
        completed: the user may explicitly say that another numbered candidate
        is also complete without forcing the model to reconstruct that list.
        """
        if (
            state.state != "completed"
            or state.interaction_kind != "object_selection"
            or state.domain != "plan"
            or str(state.immutable_arguments.get("tool_name", ""))
            != "complete_plan"
            or not state.listed_object_ids
        ):
            return None
        normalized = self._compact(message)
        if not re.search(r"(?:也)?(?:完成|做完)(?:了|啦|吧)?$", normalized):
            return None
        try:
            updated_at = datetime.fromisoformat(str(state.updated_at))
            now = self.interaction_coordinator.now_provider()
            if now - updated_at > timedelta(
                seconds=self.interaction_coordinator.continuation_ttl_seconds
            ):
                return None
        except (TypeError, ValueError):
            return None
        selections = self._selected_pending_objects(normalized, state)
        if len(selections) != 1:
            return None
        selected_id = selections[0]
        plan_service = getattr(self.business_resolver, "plan_service", None)
        plans = plan_service.list_plans() if plan_service is not None else []
        selected = next(
            (
                dict(item)
                for item in plans
                if isinstance(item, dict)
                and str(item.get("uid") or item.get("id")) == selected_id
                and not bool(item.get("done"))
                and str(item.get("status", "pending") or "pending") == "pending"
            ),
            None,
        )
        if selected is None:
            return None
        arguments = dict(state.immutable_arguments)
        arguments.pop("tool_name", None)
        self.interaction_coordinator.update(
            conversation_id,
            state="awaiting_clarification",
            consumed=False,
            selected_object_ids=[selected_id],
        )
        return self._semantic_from_object_selections(
            str(message).strip(),
            "complete_plan",
            arguments,
            [selected],
            domain="plan",
        )

    def _pause_incompatible_pending(
        self,
        message: str,
        conversation_id: str,
    ) -> str:
        """Cancel only the current conversation's stale short-lived operation."""
        if not self.interaction_coordinator_enabled:
            return ""
        state = self.interaction_coordinator.current(conversation_id)
        if not state.pending:
            return ""
        normalized = self._compact(message)
        if self._is_pending_control(normalized) or self._is_pending_input_compatible(
            state, normalized
        ):
            return ""
        self.interaction_coordinator.cancel(conversation_id)
        self.agent_core.executor.confirmation_manager.cancel(scope=conversation_id)
        # A topic change is a routing boundary, not user-facing content.  The
        # current message must be answered on its own merits rather than having a
        # stale-operation notice prepended to ordinary chat or a read answer.
        return ""

    def _is_pending_input_compatible(self, state, normalized: str) -> bool:
        if state.interaction_kind == "action_log_offer":
            return self._confirms_action_log_offer(normalized)
        if state.state == "awaiting_confirmation":
            tool_name = str(state.immutable_arguments.get("tool_name", ""))
            if tool_name == "save_formal_memory" and normalized in {
                "确认", "确认吧", "是的", "执行", "继续"
            }:
                return True
            if "action_log" in tool_name and re.search(
                r"(?:记录|记下来|可以.*记|确认)", normalized
            ):
                return True
            if tool_name == "delete_plan" and re.fullmatch(
                r"确认删除计划\d+", normalized
            ):
                return True
            exact_operation_reply = {
                "delete": {"删除"},
                "archive": {"归档"},
                "restore": {"恢复"},
                "update": {"更新", "修改"},
                "add": {"添加", "加入"},
            }
            if any(
                normalized in replies
                for operation, replies in exact_operation_reply.items()
                if operation in tool_name
            ):
                return True
            return False
        if state.interaction_kind == "object_selection":
            if (
                state.domain == "plan"
                and "reopen_plan" in {str(item) for item in state.missing_fields}
                and len(state.listed_object_ids) == 1
                and self._confirms_completed_plan_reopen(normalized)
            ):
                return True
            if re.search(
                r"(?:为什么|怎么|如何|哪个更|哪一个更|区别|合适吗|是什么|什么意思)",
                normalized,
            ):
                return False
            if (
                state.domain == "plan"
                and str(state.immutable_arguments.get("tool_name", ""))
                == "add_plan"
                and self._is_duplicate_plan_confirmation(normalized)
            ):
                return True
            return bool(self._selected_pending_objects(normalized, state))
        if state.interaction_kind == "duplicate_plan_resolution":
            return bool(
                isinstance(state.known_fields.get("merge_preview"), dict)
                or self._is_duplicate_plan_confirmation(normalized)
                or self._is_duplicate_plan_update_choice(normalized)
                or self._is_duplicate_plan_merge_choice(normalized)
                or self._selected_pending_objects(normalized, state)
                or self._selected_suggestion(normalized, state.suggested_options)
            )
        if state.interaction_kind == "suggestion_batch_duplicate_resolution":
            return bool(self._suggestion_batch_duplicate_choice(normalized))
        if state.interaction_kind == "memory_or_plan_destination":
            return bool(
                self._contains_plan_write_destination(normalized)
                or self._is_long_term_memory_destination(normalized)
            )
        if state.interaction_kind == "plan_update":
            return True
        if state.interaction_kind == "memory_candidate_review":
            return bool(re.search(r"(?:确认|忽略|第[一二三四五六七八九十\d]+个|全部|这条|那个)", normalized))
        if state.interaction_kind in {
            "assistant_plan_offer",
            "assistant_plan_selection",
        }:
            return bool(
                self._suggestion_collection_negation(normalized)
                or self._collection_control(normalized, "add_plan")
                or self._suggestion_collection_question(normalized)
                or self._suggestion_operation_help_request(normalized)
                or self._suggestion_refinement_follow_up(normalized, state)
                or self._selected_assistant_plan_option(normalized, state)
                or re.search(
                    r"(?:加入|添加|加进|放进|放入|纳入|安排到|安排进|塞进|放到|加到)"
                    r"(?:我)?(?:今天|今日)?(?:的)?(?:计划|任务|清单|待办)",
                    normalized,
                )
                or re.search(r"(?:好|嗯|行|可以).*(?:今天|今日).*(?:安排|待办|清单)", normalized)
            )
        if (
            state.domain == "plan"
            and state.interaction_kind
            in {"missing_slots", "missing_slot", "parameter_retry"}
            and str(state.immutable_arguments.get("tool_name", ""))
            == "complete_plan"
        ):
            if not normalized or re.search(
                r"(?:为什么|怎么|如何|什么|哪些|新闻|天气|发生|吗|呢)$",
                normalized,
            ):
                return False
            return not bool(
                re.search(
                    r"(?:加入|添加|删除|修改|更新|记住|保存|记录|查看|展示)",
                    normalized,
                )
            )
        if state.domain == "plan" and state.interaction_kind in {
            "missing_slots", "missing_slot", "parameter_retry", "advice_choice", "advice_or_action_choice"
        }:
            missing = {str(item) for item in state.missing_fields}
            if re.fullmatch(r"(?:不知道(?:你给我安排一下(?:可以吗)?)?|你给我安排一下(?:可以吗)?|(?:你|给我)?建议)", normalized):
                return True
            if (
                (
                    str(state.known_fields.get("title", "")).strip()
                    or (
                        state.interaction_kind == "advice_or_action_choice"
                        and bool(state.suggested_options)
                    )
                )
                and self._is_plan_add_choice(
                    normalized,
                    scoped_plan_pending=True,
                )
            ):
                return True
            extractor = getattr(self.semantic_action_parser, "feature_extractor", None)
            if extractor is None:
                return False
            features = extractor.extract(normalized)
            has_duration = features.parsed_time.get("duration_minutes") is not None
            followup_title = self._specific_plan_title("", normalized)
            if state.interaction_kind in {"advice_choice", "advice_or_action_choice"}:
                return bool(
                    has_duration
                    and self._selected_suggestion(normalized, state.suggested_options)
                )
            if missing == {"duration_minutes"}:
                return has_duration and not features.is_question and (
                    not followup_title or followup_title in {"安排", "先安排"}
                    or bool(
                        re.fullmatch(
                            r"(?:半|一|两|三|四|五|六|七|八|九|十|\d+(?:\.\d+)?)"
                            r"(?:个)?(?:分钟|小时)",
                            normalized,
                        )
                    )
                )
            if missing == {"title", "duration_minutes"}:
                return has_duration and bool(followup_title)
            if missing == {"title"}:
                return bool(followup_title) and not features.is_question
            if missing == {"time_slot"}:
                # A scoped plan clarification may explicitly be waiting for a
                # schedule.  Keep a concrete reply such as "晚上8点" in the
                # existing continuation path rather than restarting general
                # natural-language routing.
                return bool(features.parsed_time.get("time_period")) and not features.is_question and not bool(
                    re.search(
                        r"(?:为什么|怎么|如何|什么|哪些|新闻|天气|节目|发生|吗|呢)$",
                        normalized,
                    )
                )
            # Other free-form follow-ups are deliberately re-routed through the
            # semantic decision path; keyword overlap must not consume them.
            return False
        return False

    @staticmethod
    def _confirms_action_log_offer(normalized: str) -> bool:
        return normalized in {"好", "好的", "可以", "行", "行吧", "记吧"} or bool(
            re.search(r"(?:记录|记下来|加入行动记录|可以.*记|确认)", normalized)
        )

    @staticmethod
    def _confirms_completed_plan_reopen(normalized: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:好|好的|行|可以|那就)?(?:请)?(?:重新打开|重新开始|重开|恢复)"
                r"(?:这个|这条|这件事|刚才那个|它)(?:计划)?(?:吧)?",
                normalized,
            )
        )

    def _is_plan_add_choice(
        self,
        normalized: str,
        *,
        scoped_plan_pending: bool = False,
    ) -> Optional[str]:
        """Recognize a bounded add-plan choice without extracting a new title."""
        if re.search(r"^(?:不|别|不要|不用|取消)", normalized):
            return None
        extractor = getattr(self.semantic_action_parser, "feature_extractor", None)
        if extractor is None:
            return False
        features = extractor.extract(normalized)
        has_add_operation = any(
            cue in normalized
            for cue in ("添加", "加入", "加进", "加到", "放进", "放到", "安排", "塞进")
        )
        has_plan_domain = (
            scoped_plan_pending
            or "plan" in features.domain_cues
            or bool(
                re.search(
                    r"(?:今天|今日)?(?:的)?(?:计划|任务|清单|待办)",
                    normalized,
                )
            )
        )
        return bool(
            has_add_operation
            and has_plan_domain
            and not features.is_question
            and not features.negation_cues
        )

    @staticmethod
    def _selected_assistant_plan_option(normalized: str, state):
        if ConversationService._unsafe_suggestion_reference(normalized):
            return None
        options = [
            item
            for item in state.suggested_options
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]
        if not options:
            return None
        selected = ConversationService._selected_suggestion(normalized, options)
        if selected is not None:
            return selected
        compact = ConversationService._compact(normalized)
        for option in options:
            title = ConversationService._compact(str(option.get("title", "")))
            if title and (compact == title or title in compact):
                return option
        return None

    @staticmethod
    def _unsafe_suggestion_reference(text: str) -> bool:
        # A reference is an argument of an existing typed operation, never
        # permission to execute a negated, quoted or interrogative command.
        raw = str(text or "").strip()
        return bool(
            re.search(r"[?？\"'“”‘’]", raw)
            or re.search(r"不要|别|不想|不用|取消|先不|不是", raw)
            or raw.rstrip("。.!！").endswith(("吗", "么", "呢"))
        )

    @classmethod
    def _implicit_whole_suggestion_reference(cls, text: str) -> bool:
        """Treat an unqualified plural assistant reference as the whole typed list.

        This helper is consumed only after a validated, same-conversation plan
        suggestion snapshot exists.  It must not promote an arbitrary numbered
        chat reply into a write operation.  Explicit ordinals and singular
        pronouns remain single-object references and therefore still require a
        resolvable item.
        """

        raw = str(text or "").strip()
        if cls._unsafe_suggestion_reference(raw):
            return False
        if not cls._contains_plan_write_destination(raw):
            return False
        compact = cls._compact(raw)
        if cls._suggestion_reference_position(raw) > 0 or re.search(
            r"(?:这个|那个|它|其中(?:一个|一项|一条)|某个)", compact
        ):
            return False
        if cls._contains_assistant_plan_reference(raw):
            return True
        return bool(
            re.search(
                r"(?:这些|这几(?:个|项|条)?|上面这些|前面这些|刚才这些)"
                r"(?:建议|安排|事项|项目|步骤|内容)?",
                compact,
            )
        )

    @staticmethod
    def _suggestion_collection_question(text: str) -> bool:
        raw = str(text or "").strip()
        return bool(
            re.search(r"[?？]|(?:吗|么|呢)[。.!！]?$", raw)
            and re.search(r"全部|所有|都", raw)
        )

    @staticmethod
    def _suggestion_operation_help_request(text: str) -> bool:
        raw = ConversationService._compact(text)
        return bool(
            re.search(r"(?:怎么|如何|怎样|应该怎么).{0,10}(?:说|表达|操作|加入|添加)", raw)
            or re.search(r"(?:告诉我|教我).{0,8}(?:怎么说|如何说|该怎么表达)", raw)
        )

    @staticmethod
    def _positive_suggestion_correction(text: str) -> str:
        """Resolve a leading discourse dismissal before one explicit command."""
        raw = str(text or "").strip()
        match = re.match(r"^(?:算了|不用了|不必了)[，,。.!！\s]*(.+)$", raw)
        if not match:
            return ""
        remainder = match.group(1).strip()
        compact = ConversationService._compact(remainder)
        if re.match(r"^(?:不要|别|不用|不想|先不)", compact):
            return ""
        if not ConversationService._contains_assistant_plan_reference(remainder):
            return ""
        if not ConversationService._contains_plan_write_destination(remainder):
            return ""
        return remainder

    def _suggestion_refinement_follow_up(self, text: str, state) -> bool:
        """Accept one optional constraint update while a plan offer is active.

        A refinement is read-only.  Selection, cancellation, help and explicit
        plan-write language are handled by their dedicated branches instead.
        """
        raw = str(text or "").strip()
        compact = self._compact(raw)
        if not raw or len(compact) > 48:
            return False
        if state.interaction_kind != "assistant_plan_offer":
            return False
        if bool(state.known_fields.get("suggestion_refinement_used")):
            return False
        if re.search(r"[?？]", raw) or self._suggestion_operation_help_request(raw):
            return False
        if (
            self._suggestion_collection_negation(raw)
            or self._collection_control(raw, "add_plan")
            or self._selected_assistant_plan_option(compact, state) is not None
            or self._contains_plan_write_destination(raw)
        ):
            return False
        if re.fullmatch(r"(?:你好|嗨|在吗|谢谢|晚安|早上好|晚上好|你是谁)", compact):
            return False
        return True

    @staticmethod
    def _suggestion_collection_negation(text: str) -> bool:
        raw = ConversationService._compact(text)
        operation = r"(?:加入|添加|加进|放进|放入|加到|放到)"
        collection = r"(?:全部|所有|都|这些|刚才(?:的)?(?:建议|事项|项目)?)"
        return bool(
            re.search(
                r"(?:不要|别|不用|不想|先不).{0,12}" + operation,
                raw,
            )
            or re.search(
                collection + r".{0,6}不(?:要|再|全|都)?" + operation,
                raw,
            )
        )

    @staticmethod
    def _is_duplicate_plan_confirmation(normalized: str) -> bool:
        """Accept natural confirmations for an already listed duplicate."""
        value = str(normalized or "").strip()
        return bool(
            re.fullmatch(
                r"(?:仍然|继续|还是|照样)(?:保留|添加|加入)"
                r"(?:(?:这|该)条?(?:计划|任务)?|(?:这|该)个(?:计划|任务)?|(?:计划|任务))?"
                r"(?:[a-zA-Z0-9_\-\u4e00-\u9fff]+)?",
                value,
            )
            or re.fullmatch(
                r"(?:就|那就)?保留(?:(?:这|该)条?(?:计划|任务)?|(?:这|该)个(?:计划|任务)?|(?:计划|任务))?",
                value,
            )
        )

    @staticmethod
    def _is_duplicate_plan_update_choice(normalized: str) -> bool:
        value = str(normalized or "").strip()
        return bool(
            re.fullmatch(
                r"(?:更新|修改)(?:(?:这|该|那)条?|原来的?|原)(?:计划|任务)?|(?:计划|任务)",
                value,
            )
        )

    @staticmethod
    def _is_duplicate_plan_merge_choice(normalized: str) -> bool:
        value = str(normalized or "").strip()
        return bool(
            re.fullmatch(
                r"(?:把)?(?:这些|这几条|这组|它们)?(?:相近|重复)?(?:计划|任务)?(?:都)?合并(?:计划|任务)?",
                value,
            )
            or re.fullmatch(r"合并(?:它们|这些|这几条|这组)(?:计划|任务)?", value)
        )

    def _resume_duplicate_plan_resolution(
        self,
        clean: str,
        normalized: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        state,
    ):
        """Continue one bounded duplicate-plan decision without losing IDs."""
        resolution = str(state.known_fields.get("duplicate_resolution", ""))
        wants_add = self._is_duplicate_plan_confirmation(normalized)
        wants_update = self._is_duplicate_plan_update_choice(normalized)
        wants_merge = self._is_duplicate_plan_merge_choice(normalized)

        if wants_add:
            arguments = dict(state.immutable_arguments)
            arguments.pop("tool_name", None)
            arguments["allow_duplicate"] = True
            return None, self._semantic_from_continuation(
                clean,
                "add_plan",
                arguments,
                domain="plan",
                suggestion_selection=state.known_fields.get("suggestion_snapshot_selection"),
            )

        # A duplicate inspection stores one preview per group.  Select the
        # group first when more than one group was displayed.
        if state.suggested_options:
            group = self._selected_suggestion(normalized, state.suggested_options)
            if group is None and len(state.suggested_options) == 1 and (
                wants_merge or resolution == "merge"
            ):
                group = state.suggested_options[0]
            if group is None and wants_merge:
                self.interaction_coordinator.update(
                    conversation_id,
                    known_fields={**state.known_fields, "duplicate_resolution": "merge"},
                    safe_summary="发现多组相近计划，请先说要合并第几组。",
                )
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "发现多组相近计划，请先说要合并第几组。",
                    bounded_pending=True,
                ), None
            if group is not None and (wants_merge or resolution == "merge"):
                preview = dict(group.get("preview", {}))
                return self._continue_plan_merge_preview(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    state,
                    preview,
                )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "请说“合并计划”；如果有多组，再告诉我是第几组。",
                bounded_pending=True,
            ), None

        candidates = [
            dict(item) for item in state.candidate_objects if isinstance(item, dict)
        ]
        if wants_update or resolution == "update":
            selected_ids = self._selected_pending_objects(normalized, state)
            selected = next(
                (
                    item
                    for item in candidates
                    if str(item.get("uid") or item.get("id"))
                    in set(selected_ids or state.selected_object_ids)
                ),
                None,
            )
            if selected is None and len(candidates) == 1:
                selected = candidates[0]
            if selected is None:
                self.interaction_coordinator.update(
                    conversation_id,
                    known_fields={**state.known_fields, "duplicate_resolution": "update"},
                    safe_summary="找到了多条相近计划，请先说要更新第几条。",
                )
                return self._interaction_turn(
                    clean,
                    conversation_id,
                    history_id,
                    record_history,
                    "找到了多条相近计划，请先说要更新第几条。",
                    bounded_pending=True,
                ), None
            target_ref = str(selected.get("uid") or selected.get("id"))
            question = (
                f"好的，要更新的是“{selected.get('title', '')}”。"
                "你想修改标题、时段、时长，还是补充备注？"
            )
            self.interaction_coordinator.update(
                conversation_id,
                state="awaiting_clarification",
                interaction_kind="plan_update",
                selected_object_ids=[target_ref],
                missing_fields=["changes"],
                immutable_arguments={"tool_name": "update_plan", "task_ref": target_ref},
                known_fields={"task_ref": target_ref},
                safe_summary=question,
                consumed=False,
            )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                question,
                bounded_pending=True,
            ), None

        if wants_merge or resolution == "merge":
            plan_service = getattr(self.business_resolver, "plan_service", None)
            preview = (
                plan_service.build_merge_preview(
                    candidates,
                    proposed={
                        key: value
                        for key, value in state.immutable_arguments.items()
                        if key not in {"tool_name", "allow_duplicate"}
                    },
                )
                if plan_service is not None
                else {}
            )
            return self._continue_plan_merge_preview(
                clean,
                conversation_id,
                history_id,
                record_history,
                state,
                preview,
            )

        return self._interaction_turn(
            clean,
            conversation_id,
            history_id,
            record_history,
            "你想更新原计划、合并计划、仍然添加一条，还是取消？",
            bounded_pending=True,
        ), None

    def _maybe_time_bounded_memory_destination_turn(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        semantic_result: SemanticParseResult,
        *,
        request_id: str,
    ) -> Optional[ConversationTurn]:
        """Ask where a one-day remember command belongs before any write."""
        features = semantic_result.local_features
        parsed = features.parsed_time if features is not None else {}
        if str(parsed.get("scope", "")) != "today":
            return None
        if features is None or "memory" not in set(features.domain_cues):
            return None
        memory_tools = {
            "save_formal_memory",
            "request_add_memory",
        }
        candidates = [
            item for item in semantic_result.candidates if item.tool_name in memory_tools
        ]
        if not candidates:
            return None
        first = candidates[0]
        payload = str(features.payload_text or "").strip()
        content = str(first.arguments.get("content", "") or "").strip()
        if not content or content in {
            "previous_assistant_message",
            "previous_user_message",
        }:
            content = payload or str(message).strip()
        title = self._specific_plan_title(payload or content, message)
        if not title:
            return None
        plan_arguments: Dict[str, object] = {"title": title}
        if parsed.get("time_period"):
            plan_arguments["time_slot"] = parsed["time_period"]
        if parsed.get("duration_minutes") is not None:
            plan_arguments["duration_minutes"] = parsed["duration_minutes"]
        memory_arguments = {
            key: value
            for key, value in first.arguments.items()
            if key in {"content", "category", "source", "confirmed"}
            and value not in (None, "")
        }
        memory_arguments["content"] = content
        question = (
            f"“{title}”看起来是只对今天有效的安排。"
            "你想把它加入今日计划，还是保存为长期记忆？"
            "当前没有修改计划或长期记忆。"
        )
        self.interaction_coordinator.awaiting_choice(
            conversation_id,
            "memory_or_plan_destination",
            options=[
                {"id": "today_plan", "title": "加入今日计划"},
                {"id": "long_term_memory", "title": "保存为长期记忆"},
            ],
            listed_object_ids=["today_plan", "long_term_memory"],
            immutable_arguments={
                "memory_arguments": memory_arguments,
                "plan_arguments": plan_arguments,
            },
            domain="memory",
            request_mode="execute",
            original_user_text=message,
            safe_summary=question,
            originating_turn_id=request_id,
        )
        return self._interaction_turn(
            message,
            conversation_id,
            history_id,
            record_history,
            question,
        )

    @staticmethod
    def _is_long_term_memory_destination(normalized: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:把它)?(?:保存为|记到|加入)?长期记忆|(?:请)?长期(?:记住|保存)(?:它|这件事)?",
                str(normalized or "").strip(),
            )
        )

    def _continue_plan_merge_preview(
        self,
        clean: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        state,
        preview: Dict[str, object],
    ):
        conflicts = preview.get("conflicts", {})
        conflicts = dict(conflicts) if isinstance(conflicts, dict) else {}
        pending_preview = state.known_fields.get("merge_preview")
        if isinstance(pending_preview, dict) and pending_preview.get("conflicts"):
            preview = dict(pending_preview)
            conflicts = dict(preview.get("conflicts", {}))
            field_name = next(iter(conflicts), "")
            values = list(conflicts.get(field_name, []))
            selected = next(
                (
                    value
                    for value in values
                    if self._compact(str(value))
                    and self._compact(str(value)) in self._compact(clean)
                ),
                None,
            )
            if selected is not None:
                changes = dict(preview.get("changes", {}))
                changes[field_name] = selected
                preview["changes"] = changes
                conflicts.pop(field_name, None)
                preview["conflicts"] = conflicts
        if conflicts:
            field_name = next(iter(conflicts))
            labels = {
                "time_slot": "时段",
                "duration_minutes": "时长",
                "priority": "优先级",
                "note": "备注",
            }
            values = "、".join(str(item) for item in conflicts[field_name])
            question = f"这些计划的{labels.get(field_name, field_name)}冲突：{values}。请告诉我保留哪个值。"
            self.interaction_coordinator.update(
                conversation_id,
                known_fields={
                    **state.known_fields,
                    "duplicate_resolution": "merge",
                    "merge_preview": preview,
                },
                safe_summary=question,
                consumed=False,
            )
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                question,
                bounded_pending=True,
            ), None
        target_ref = str(preview.get("target_ref", ""))
        if not target_ref:
            return self._interaction_turn(
                clean,
                conversation_id,
                history_id,
                record_history,
                "没有找到可以安全保留的计划，因此没有执行合并。",
                bounded_pending=True,
            ), None
        return None, self._semantic_from_continuation(
            clean,
            "merge_plan",
            {
                "target_ref": target_ref,
                "duplicate_refs": list(preview.get("duplicate_refs", [])),
                "changes": dict(preview.get("changes", {})),
                "date": str(preview.get("date", "")),
            },
            domain="plan",
        )

    def _pending_plan_changes(self, text: str) -> Dict[str, object]:
        """Extract only unambiguous fields inside an established update state."""
        value = str(text or "").strip()
        changes: Dict[str, object] = {}
        extractor = getattr(self.semantic_action_parser, "feature_extractor", None)
        features = extractor.extract(value) if extractor is not None else None
        parsed = features.parsed_time if features is not None else {}
        if parsed.get("duration_minutes") is not None and re.search(
            r"(?:分钟|小时|时长)", value
        ):
            changes["duration_minutes"] = parsed["duration_minutes"]
        if parsed.get("time_period") and re.search(
            r"(?:时段|改到|改成|调整到|安排到)", value
        ):
            changes["time_slot"] = parsed["time_period"]
        note = re.search(r"(?:备注|补充|加上备注)(?:为|是|：|:)?(.+)", value)
        if note:
            changes["note"] = note.group(1).strip()
        if not changes:
            title = re.fullmatch(r"(?:把)?(?:标题)?(?:改成|改为)\s*(.+)", value)
            if title and self._compact(title.group(1)) not in {"上午", "中午", "下午", "晚上"}:
                changes["title"] = title.group(1).strip()
        return changes

    def _start_duplicate_plan_review_from_response(
        self,
        response: AgentResponse,
        conversation_id: str,
        *,
        original_user_text: str,
        originating_turn_id: str,
    ) -> None:
        for tool_result in response.tool_results:
            if not tool_result.success or tool_result.tool != "inspect_plan_duplicates":
                continue
            groups = tool_result.data.get("groups", [])
            groups = [dict(item) for item in groups if isinstance(item, dict)]
            if not groups:
                return
            self.interaction_coordinator.start(
                conversation_id,
                "duplicate_plan_resolution",
                "awaiting_choice",
                domain="plan",
                request_mode="query",
                original_user_text=original_user_text,
                suggested_options=groups,
                listed_object_ids=[str(item.get("group_id", "")) for item in groups],
                known_fields={},
                immutable_arguments={"tool_name": "merge_plan"},
                safe_summary="已列出相近计划，等待用户决定是否合并。",
                originating_turn_id=originating_turn_id,
            )
            return

    @staticmethod
    def _is_current_plan_reference(normalized: str) -> bool:
        value = str(normalized or "").strip()
        return bool(
            re.search(r"(?:刚才(?:这个|那个)?|这个|这条|这件事|它)", value)
            and re.search(r"(?:加入|添加|放进|安排)(?:到|进)?", value)
        )

    def _handle_scoped_memory_undo(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
    ) -> Optional[ConversationTurn]:
        if not self.interaction_coordinator_enabled:
            return None
        normalized = re.sub(r"[\s，。！？!?；;：:]", "", str(message or ""))
        if normalized not in {
            "撤销刚才保存",
            "刚才那条别记了",
            "取消刚才的记忆",
        }:
            return None
        recent = self.interaction_coordinator.last_created_memory(conversation_id)
        if recent is None:
            response = AgentResponse(
                "clarification",
                "当前会话没有可撤销的刚才保存的长期记忆。请明确说要归档哪一条记忆。",
                conversation_id=conversation_id,
            )
            self._record_response(
                response,
                history_id,
                record_history,
                "formal_memory_undo",
            )
            return ConversationTurn(
                message,
                conversation_id,
                {"intent": "archive_memory", "source": "scoped_undo"},
                response=response,
                record_history=record_history,
            )

        intent_result = {
            "intent": "archive_memory",
            "confidence": 1.0,
            "source": "scoped_undo",
            "needs_confirmation": False,
            "resolved_actions": [
                {
                    "tool_name": "archive_memory",
                    "arguments": {"memory_id": recent["memory_id"]},
                    "depends_on": [],
                }
            ],
        }
        response = self.agent_core.process(
            message,
            intent_result,
            confirmation_scope=conversation_id,
        )
        response.conversation_id = conversation_id
        response = self.response_composer.compose(response, user_text=message)
        if response.status in {"completed", "partial_success"} and any(
            str(result.tool) == "archive_memory" and bool(result.success)
            for result in response.tool_results
        ):
            self.interaction_coordinator.clear_created_memory(conversation_id)
            response.message = "已撤销刚才保存的长期记忆；它已归档，不会被永久删除。"
        elif response.status in {"completed", "partial_success"}:
            response.status = "failed"
            response.message = "这次没有成功撤销刚才保存的长期记忆。"
        self._record_response(
            response,
            history_id,
            record_history,
            "formal_memory_undo",
        )
        return ConversationTurn(
            message,
            conversation_id,
            intent_result,
            response=response,
            record_history=record_history,
        )

    def _remember_formal_memory_from_response(
        self,
        response: AgentResponse,
        conversation_id: str,
        source_turn_id: str,
    ) -> None:
        if not self.interaction_coordinator_enabled:
            return
        for result in response.tool_results:
            if str(result.tool) != "save_formal_memory" or not bool(result.success):
                continue
            operation = result.data.get("memory_operation", {})
            operation = operation if isinstance(operation, dict) else {}
            data = operation.get("data", {})
            data = data if isinstance(data, dict) else {}
            if (
                data.get("operation") == "created"
                and bool(data.get("undo_available"))
                and operation.get("memory_id") is not None
            ):
                self.interaction_coordinator.remember_created_memory(
                    conversation_id,
                    operation.get("memory_id"),
                    source_turn_id=source_turn_id,
                )
                return

    def _handle_read_result_follow_up(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
    ) -> Optional[ConversationTurn]:
        """Re-check only a verified read fact from this same conversation."""
        if not self.interaction_coordinator_enabled:
            return None
        normalized = self._compact(message)
        if normalized not in {"你确定吗", "你确定"}:
            return None
        source = self.interaction_coordinator.last_read_result(conversation_id)
        if source is None:
            return None
        tool_name = str(source.get("tool_name", ""))
        if tool_name not in self._READ_FOLLOW_UP_TOOLS:
            return None
        arguments = source.get("arguments", {})
        if not isinstance(arguments, dict):
            return None
        intent_result = {
            "mode": "read",
            "intent": str(source.get("intent", "") or "read_follow_up"),
            "follow_up_target": "previous_read_result",
            "confidence": 1.0,
            "source": "read_follow_up",
            "needs_confirmation": False,
            "resolved_actions": [
                {
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "depends_on": [],
                }
            ],
        }
        response = self.agent_core.process(
            message,
            intent_result,
            confirmation_scope=conversation_id,
        )
        response.conversation_id = conversation_id
        response = self.response_composer.compose(response, user_text=message)
        if response.status in {"completed", "partial_success"}:
            self._remember_read_result_from_response(
                response, conversation_id, intent_result
            )
        self._record_response(
            response, history_id, record_history, "read_follow_up"
        )
        return ConversationTurn(
            message,
            conversation_id,
            intent_result,
            response=response,
            record_history=record_history,
        )

    def _remember_read_result_from_response(
        self,
        response: AgentResponse,
        conversation_id: str,
        intent_result: Dict[str, object],
    ) -> None:
        if not self.interaction_coordinator_enabled:
            return
        actions = intent_result.get("resolved_actions", [])
        actions = actions if isinstance(actions, list) else []
        arguments_by_tool = {
            str(item.get("tool_name", "")): dict(item.get("arguments", {}))
            for item in actions
            if isinstance(item, dict) and isinstance(item.get("arguments", {}), dict)
        }
        for result in response.tool_results:
            tool_name = str(result.tool)
            if not result.success or tool_name not in self._READ_FOLLOW_UP_TOOLS:
                continue
            summary = str(result.display_message or result.message or "").strip()
            snapshot = {}
            if tool_name == "show_plan":
                tasks = result.data.get("tasks", [])
                if isinstance(tasks, list):
                    snapshot = ReadSnapshot.for_plan_list(
                        conversation_id=conversation_id,
                        intent=self._read_intent_for_tool(
                            tool_name, str(intent_result.get("intent", ""))
                        ),
                        tool_name=tool_name,
                        tasks=[item for item in tasks if isinstance(item, dict)],
                        arguments=arguments_by_tool.get(tool_name, {}),
                        captured_at=self.interaction_coordinator.now_provider(),
                    ).to_dict()
            self.interaction_coordinator.remember_read_result(
                conversation_id,
                intent=self._read_intent_for_tool(
                    tool_name, str(intent_result.get("intent", ""))
                ),
                tool_name=tool_name,
                arguments=arguments_by_tool.get(tool_name, {}),
                summary=summary,
                snapshot=snapshot,
            )

    def _recent_plan_snapshot(self, conversation_id: str) -> Optional[ReadSnapshot]:
        if not self.interaction_coordinator_enabled:
            return None
        state = self.interaction_coordinator.current(conversation_id)
        raw = state.last_read_snapshot
        if not isinstance(raw, dict) or not raw:
            self.development_log.event(None, "snapshot_state", snapshot_kind="read", state="missing", reason_code="missing_snapshot")
            return None
        try:
            snapshot = ReadSnapshot.from_dict(raw)
            captured_at = datetime.fromisoformat(snapshot.captured_at)
            now = self.interaction_coordinator.now_provider()
            if now - captured_at > timedelta(
                seconds=self.interaction_coordinator.continuation_ttl_seconds
            ):
                self.development_log.event(None, "snapshot_state", snapshot_kind="read", state="expired", reason_code="expired_snapshot", snapshot_id=snapshot.snapshot_id)
                return None
        except (TypeError, ValueError):
            self.development_log.event(None, "snapshot_state", snapshot_kind="read", state="unavailable", reason_code="invalid_tool_result")
            return None
        if (
            snapshot.tool_name != "show_plan"
            or snapshot.conversation_id != str(conversation_id).strip()
        ):
            self.development_log.event(None, "snapshot_state", snapshot_kind="read", state="unavailable", reason_code="cross_session_snapshot", snapshot_id=snapshot.snapshot_id)
            return None
        self.development_log.event(None, "snapshot_state", snapshot_kind="read", state="valid", snapshot_id=snapshot.snapshot_id, read_count=len(snapshot.objects))
        return snapshot

    def _recent_suggestion_snapshot(
        self,
        conversation_id: str,
    ) -> Optional[SuggestionSnapshot]:
        if not self.interaction_coordinator_enabled:
            return None
        state = self.interaction_coordinator.current(conversation_id)
        raw = state.last_suggestion_snapshot
        if not isinstance(raw, dict) or not raw:
            self.development_log.event(None, "snapshot_state", snapshot_kind="suggestion", state="missing", reason_code="missing_snapshot")
            return None
        try:
            snapshot = SuggestionSnapshot.from_dict(raw)
            captured_at = datetime.fromisoformat(snapshot.captured_at)
            now = self.interaction_coordinator.now_provider()
            target_date = snapshot.target_date or captured_at.date().isoformat()
            if (
                target_date != now.date().isoformat()
                or captured_at > now
                or now - captured_at > timedelta(
                    seconds=self.interaction_coordinator.continuation_ttl_seconds
                )
            ):
                self.development_log.event(None, "snapshot_state", snapshot_kind="suggestion", state="expired", reason_code="expired_snapshot", snapshot_id=snapshot.snapshot_id)
                return None
        except (TypeError, ValueError):
            self.development_log.event(None, "snapshot_state", snapshot_kind="suggestion", state="unavailable", reason_code="invalid_tool_result")
            return None
        if (
            snapshot.conversation_id != str(conversation_id).strip()
            or not any(not item.consumed for item in snapshot.objects)
        ):
            self.development_log.event(None, "snapshot_state", snapshot_kind="suggestion", state="unavailable", reason_code="suggestion_unavailable", snapshot_id=snapshot.snapshot_id)
            return None
        self.development_log.event(None, "snapshot_state", snapshot_kind="suggestion", state="valid", snapshot_id=snapshot.snapshot_id, suggestion_count=len(snapshot.objects))
        return snapshot

    def _annotate_suggestion_selection(
        self,
        semantic_result: SemanticParseResult,
        *,
        conversation_id: str,
        option: Mapping[str, object],
    ) -> None:
        selection = self._suggestion_selection_for_option(
            conversation_id=conversation_id, option=option
        )
        if selection:
            semantic_result.intent_result["suggestion_snapshot_selection"] = selection

    def _suggestion_selection_for_option(
        self,
        *,
        conversation_id: str,
        option: Mapping[str, object],
    ) -> Dict[str, str]:
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None:
            return {}
        option_id = str(option.get("id", "") or "").strip()
        option_title = str(option.get("title", "") or "").strip()
        selected = next(
            (
                item
                for item in snapshot.objects
                if not item.consumed
                and (
                    (option_id and item.stable_id == option_id)
                    or (option_title and item.title == option_title)
                )
            ),
            None,
        )
        if selected is None:
            return {}
        return {
            "snapshot_id": snapshot.snapshot_id,
            "object_id": selected.stable_id,
        }

    @classmethod
    def _collection_control(cls, text: str, tool_name: str) -> str:
        """Exact collection controls; only consumed by a typed operation.

        This does not classify ordinary conversation. Fresh turns must first
        select the same capability through the semantic gateway.
        """
        if re.search(r"[?？]", str(text or "")):
            return ""
        value = cls._compact(text)
        if tool_name == "add_plan" and cls._suggestion_collection_negation(value):
            return ""
        if re.search(r"不要|别|不想|不用|取消|先不|不是", value):
            return ""
        if tool_name == "add_plan":
            short_value = value.rstrip("。.!！")
            if re.fullmatch(
                r"(?:我)?(?:"
                r"(?:这些|这几(?:项|条|个)?|上面这些|刚才这些)(?:我)?"
                r"(?:全都|全部都|都|全部|所有)(?:要|选)"
                r"|(?:全都|全部都|都)(?:要|选)"
                r"|(?:要|选)(?:全部|所有|全都|这些)"
                r")(?:了|吧)?",
                short_value,
            ):
                return "all"
        verbs = {
            "add_plan": r"(?:加入|添加|加进|放进|放入|加到|放到)(?:(?:今天|今日)(?:的)?计划)?",
            "complete_plan": r"(?:完成|做完)",
        }
        verb = verbs.get(tool_name)
        if verb is None:
            return ""
        amount = r"(?:[一二两三四五六七八九十\d]+(?:项|条|个))?"
        if re.fullmatch(r"(?:我)?(?:全部|所有|都)" + amount + r"(?:都)?(?:" + verb + r")?(?:了|吧)?", value):
            return "all"
        if tool_name == "add_plan" and re.fullmatch(
            r"(?:剩下|剩余|其余)(?:的)?" + amount + r"(?:都|全部)?(?:" + verb + r")(?:了|吧)?", value,
        ):
            return "remaining"
        if tool_name == "add_plan":
            # An operation plus a whole-list quantifier may occur in either
            # order. These are collection arguments, not a second intent router.
            operation = r"(?:加入|添加|加进|放进|放入|加到|放到)"
            destination = r"(?:(?:今天|今日)(?:的)?)?(?:计划|任务|待办|清单)"
            object_kind = r"(?:计划|任务|建议|事项|项目)?"
            has_operation = bool(re.search(operation, value))
            if has_operation and re.search(r"(?:剩下|剩余|其余)", value):
                return "remaining"
            if has_operation and (
                re.search(r"(?:全部|所有|都)", value)
                or re.search(r"全(?=.{0,8}" + operation + r")", value)
            ):
                # This runs only after the pending interaction or semantic
                # gateway has already selected add_plan. It extracts the
                # collection scope; it does not promote ordinary chat to a write.
                return "all"
            all_scope = r"(?:全部|所有|都)(?:的)?" + amount + object_kind
            remaining_scope = r"(?:剩下|剩余|其余)(?:的)?" + amount + object_kind
            for scope, kind in ((all_scope, "all"), (remaining_scope, "remaining")):
                patterns = (
                    r"(?:请|帮我|把)?(?:我)?" + scope + r"(?:都)?" + operation + r"(?:" + destination + r")?(?:了|吧)?",
                    r"(?:请|帮我)?" + operation + scope + r"(?:" + destination + r")?(?:了|吧)?",
                )
                if any(re.fullmatch(pattern, value) for pattern in patterns):
                    return kind
        return ""

    @classmethod
    def _collection_control_count(cls, text: str) -> int:
        match = re.search(r"([一二两三四五六七八九十\d]+)(?:项|条|个)", cls._compact(text))
        return cls._selection_number(match.group(1)) if match else 0

    @classmethod
    def _has_grounded_plan_batch_candidates(
        cls,
        semantic_result: SemanticParseResult,
        current_message: str,
    ) -> bool:
        """Distinguish an explicit task batch from a bare collection reference."""

        candidates = [
            item for item in semantic_result.candidates
            if item.tool_name == "add_plan"
        ]
        if len(candidates) < 2:
            return False
        compact_message = cls._compact(current_message)
        titles = [
            cls._compact(str(item.arguments.get("title", "") or ""))
            for item in candidates
        ]
        return all(title and title in compact_message for title in titles)

    @classmethod
    def _suggestion_batch_duplicate_choice(cls, text: str) -> str:
        value = cls._compact(text).strip("。.!！?？")
        if re.search(r"(?:添加|加入)(?:未重复|不重复)(?:的)?(?:项|计划|任务)?", value) or re.search(
            r"(?:跳过|忽略)(?:这些|所有|全部)?重复", value
        ):
            return "skip"
        if re.search(
            r"(?:仍然|还是|继续)(?:把)?(?:这些|所有|全部)?(?:都)?(?:添加|加入)|"
            r"(?:重复(?:的)?(?:项|计划|任务)?也)(?:添加|加入)|"
            r"(?:仍然|还是|继续)?(?:全部|所有)(?:都)?(?:添加|加入)",
            value,
        ):
            return "force"
        return ""

    def _execute_suggestion_plan_batch(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        *,
        selected_object_ids: Optional[List[str]] = None,
        duplicate_policy: str = "",
        common_arguments: Optional[Mapping[str, object]] = None,
        expected_snapshot_id: str = "",
    ) -> ConversationTurn:
        """Preflight and execute one verified suggestion snapshot as a batch.

        The ordinary semantic/model action cap stays unchanged.  This path may
        exceed it because every title comes from the exact numbered list that
        was already displayed and stored in this conversation.
        """

        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None or (
            expected_snapshot_id and snapshot.snapshot_id != expected_snapshot_id
        ):
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "刚才的建议列表不存在、已变化或已失效，这次没有添加计划。"
                "请重新获取建议，或直接说“今天添加计划：具体事项”。",
            )

        requested_ids = {
            str(item).strip() for item in (selected_object_ids or []) if str(item).strip()
        }
        available = [item for item in snapshot.objects if not item.consumed]
        selected = [
            item for item in available
            if not requested_ids or item.stable_id in requested_ids
        ]
        if requested_ids and {item.stable_id for item in selected} != requested_ids:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "确认前建议列表发生了变化，这次没有添加任何计划。请重新获取建议后再试。",
            )
        expected_count = self._collection_control_count(message)
        if not selected or (
            not duplicate_policy
            and expected_count
            and expected_count != len(selected)
        ):
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                self._suggestion_collection_failure_reply(message, conversation_id),
            )

        features = self.semantic_action_parser.feature_extractor.extract(message)
        shared = {
            key: value
            for key, value in dict(common_arguments or {}).items()
            if key in {"date", "time_slot", "duration_minutes"}
            and value not in (None, "")
        }
        if snapshot.target_date:
            shared["date"] = snapshot.target_date
        for source, target in (
            ("time_period", "time_slot"),
            ("duration_minutes", "duration_minutes"),
        ):
            value = features.parsed_time.get(source)
            if value not in (None, ""):
                shared[target] = value

        def resolve(allow_duplicate: bool):
            candidates = []
            for index, item in enumerate(selected):
                arguments = {"title": item.title, **shared}
                if allow_duplicate:
                    arguments["allow_duplicate"] = True
                candidates.append(
                    ActionCandidate(
                        domain="plan",
                        tool_name="add_plan",
                        arguments=arguments,
                        request_mode="execute",
                        confidence=1.0,
                        explicit_command=True,
                        sequence_index=index,
                        source_intent="suggestion_snapshot_batch",
                    )
                )
            return self.business_resolver.resolve_all(
                candidates,
                conversation_id=conversation_id,
                features=features,
                structured_references={},
            )

        resolutions = resolve(duplicate_policy == "force")
        duplicate_indexes = [
            index for index, item in enumerate(resolutions)
            if item.reason_code == "similar_plan_exists"
        ]
        invalid = [
            item for item in resolutions
            if item.status not in {"resolved", "partial"}
            and item.reason_code != "similar_plan_exists"
        ]
        if invalid:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                (invalid[0].safe_prompt or "这些建议目前不能安全加入计划。")
                + " 这次没有添加任何计划。",
            )

        if duplicate_indexes and not duplicate_policy:
            duplicate_ids = [selected[index].stable_id for index in duplicate_indexes]
            duplicate_titles = [selected[index].title for index in duplicate_indexes]
            prompt = ["下面这些建议与今天已有计划重复："]
            prompt.extend(
                f"{index}. {title}" for index, title in enumerate(duplicate_titles, 1)
            )
            prompt.append("本轮尚未添加任何项目。")
            prompt.append("请回复“添加未重复项”“仍然全部添加”，或“取消”。")
            self.interaction_coordinator.start(
                conversation_id,
                "suggestion_batch_duplicate_resolution",
                "awaiting_choice",
                domain="plan",
                request_mode="execute",
                original_user_text=message,
                known_fields={"duplicate_object_ids": duplicate_ids},
                suggested_options=[
                    {"id": item.stable_id, "title": item.title} for item in selected
                ],
                listed_object_ids=[item.stable_id for item in selected],
                selected_object_ids=[item.stable_id for item in selected],
                immutable_arguments={
                    "tool_name": "add_plan",
                    "snapshot_id": snapshot.snapshot_id,
                    "common_arguments": shared,
                },
                safe_summary="批量加入前发现重复计划，等待一次统一选择。",
            )
            return self._interaction_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "\n".join(prompt),
            )

        if duplicate_policy == "force" and duplicate_indexes:
            # ``allow_duplicate`` should resolve every duplicate. Treat a
            # remaining duplicate result as a changed/invalid preflight.
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "计划状态发生了变化，这次没有添加任何计划。请重新查看今日计划后再试。",
            )

        skipped_ids = {
            selected[index].stable_id for index in duplicate_indexes
        } if duplicate_policy == "skip" else set()
        executable = [
            (item, resolution)
            for index, (item, resolution) in enumerate(zip(selected, resolutions))
            if index not in duplicate_indexes
            and resolution.status in {"resolved", "partial"}
            and resolution.resolved_action is not None
        ]

        results = []
        steps = []
        selections = []
        for item, resolution in executable:
            resolved_action = resolution.resolved_action
            single = self.agent_core.process(
                message,
                {
                    "intent": "resolved_actions",
                    "confidence": 1.0,
                    "source": "suggestion_snapshot_batch",
                    "needs_confirmation": False,
                    "resolved_actions": [resolved_action.to_dict()],
                },
                confirmation_scope=conversation_id,
            )
            results.extend(single.tool_results)
            steps.extend(single.steps)
            selections.append(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "object_id": item.stable_id,
                    "title": item.title,
                }
            )

        succeeded = sum(1 for item in results if item.success and item.tool == "add_plan")
        if not executable:
            status = "completed"
            response = AgentResponse(
                status,
                "这些项目都已存在，本次没有新增计划；已按你的选择跳过重复项。",
                conversation_id=conversation_id,
            )
        else:
            status = (
                "completed"
                if succeeded == len(executable) and len(results) == len(executable)
                else "partial_success"
                if succeeded
                else "failed"
            )
            response = AgentResponse(
                status,
                "",
                steps=steps,
                tool_results=results,
                conversation_id=conversation_id,
            )
            response = self.response_composer.compose(response, user_text=message)

        self._consume_suggestion_selection(
            response,
            conversation_id,
            {"suggestion_snapshot_selections": selections},
        )
        for object_id in skipped_ids:
            self.interaction_coordinator.mark_suggestion_consumed(
                conversation_id,
                snapshot_id=snapshot.snapshot_id,
                object_id=object_id,
            )
        if status == "failed":
            self.interaction_coordinator.cancel(conversation_id)
        else:
            self.interaction_coordinator.finish(
                conversation_id,
                partial=(status == "partial_success"),
                resolved_object_ids=[
                    item.stable_id for item, _resolution in executable
                ],
            )
        self._record_response(
            response,
            history_id,
            record_history,
            "suggestion_snapshot_batch",
        )
        return ConversationTurn(
            message,
            conversation_id,
            {"intent": "add_plan", "source": "suggestion_snapshot_batch"},
            response=response,
            record_history=record_history,
        )

    def _suggestion_collection_continuation(
        self, message: str, conversation_id: str,
    ) -> Optional[SemanticParseResult]:
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None:
            return None
        selected = [item for item in snapshot.objects if not item.consumed]
        expected_count = self._collection_control_count(message)
        if not selected or len(selected) > 3 or (expected_count and expected_count != len(selected)):
            return None
        continuation = self._semantic_from_continuation(
            message, "add_plan", {"tasks": [{"title": item.title} for item in selected]}, domain="plan",
        )
        continuation.intent_result["suggestion_snapshot_selections"] = [
            {"snapshot_id": snapshot.snapshot_id, "object_id": item.stable_id, "title": item.title}
            for item in selected
        ]
        return continuation

    def _suggestion_collection_failure_reply(self, message: str, conversation_id: str) -> str:
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None:
            reason = "刚才的建议列表不存在或已失效"
        else:
            count = sum(not item.consumed for item in snapshot.objects)
            expected = self._collection_control_count(message)
            if not count:
                reason = "这份建议列表中没有尚未加入的项目"
            elif expected and expected != count:
                reason = "你说的数量与尚未加入的建议数量不一致"
            else:
                reason = "目前不能确定要加入的建议范围"
        return reason + "，这次没有添加计划。请直接说“今天添加计划：具体事项”。"

    def _consume_suggestion_selection(
        self,
        response: AgentResponse,
        conversation_id: str,
        intent_result: Mapping[str, object],
    ) -> None:
        if not self.interaction_coordinator_enabled:
            return
        selections = intent_result.get("suggestion_snapshot_selections", [])
        if isinstance(selections, list) and selections:
            successful_titles = [
                str(result.data["task"].get("title", "") or "").strip()
                for result in response.tool_results
                if result.success and result.tool == "add_plan"
                and isinstance(result.data.get("task"), Mapping)
            ]
            for selected in selections:
                if not isinstance(selected, Mapping) or selected.get("title") not in successful_titles:
                    continue
                successful_titles.remove(selected.get("title"))
                self.interaction_coordinator.mark_suggestion_consumed(
                    conversation_id,
                    snapshot_id=str(selected.get("snapshot_id", "") or ""),
                    object_id=str(selected.get("object_id", "") or ""),
                )
            return
        selection = intent_result.get("suggestion_snapshot_selection", {})
        if not isinstance(selection, Mapping):
            return
        snapshot_id = str(selection.get("snapshot_id", "") or "").strip()
        object_id = str(selection.get("object_id", "") or "").strip()
        if not snapshot_id or not object_id:
            return
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None or snapshot.snapshot_id != snapshot_id:
            return
        selected = next((item for item in snapshot.objects if item.stable_id == object_id), None)
        if selected is None or not any(
            result.success and result.tool == "add_plan"
            and isinstance(result.data.get("task"), Mapping)
            and str(result.data["task"].get("title", "") or "").strip() == selected.title
            for result in response.tool_results
        ):
            return
        self.interaction_coordinator.mark_suggestion_consumed(
            conversation_id,
            snapshot_id=snapshot_id,
            object_id=object_id,
        )

    def _snapshot_live_plans(
        self,
        snapshot: ReadSnapshot,
        *,
        pending_only: bool = False,
    ) -> List[Dict[str, object]]:
        plan_service = getattr(self.business_resolver, "plan_service", None)
        if plan_service is None:
            return []
        by_date: Dict[str, Dict[str, Dict[str, object]]] = {}
        resolved: List[Dict[str, object]] = []
        for item in snapshot.objects:
            date = str(item.date or snapshot.arguments.get("date", "")).strip()
            if date not in by_date:
                try:
                    plans = plan_service.list_plans(date or None)
                except (TypeError, ValueError):
                    plans = plan_service.list_plans()
                by_date[date] = {
                    str(plan.get("uid") or plan.get("id")): dict(plan)
                    for plan in plans
                    if isinstance(plan, dict)
                }
            plan = by_date[date].get(item.stable_id)
            if plan is None:
                continue
            if pending_only and (
                bool(plan.get("done"))
                or str(plan.get("status", "pending") or "pending") != "pending"
            ):
                continue
            resolved.append(plan)
        return resolved

    def _plan_snapshot_follow_up(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
    ):
        """Bind an ordinal completion to the exact order previously displayed."""
        snapshot = self._recent_plan_snapshot(conversation_id)
        if snapshot is None or not snapshot.objects:
            return None, None
        normalized = self._compact(message)
        match = re.fullmatch(
            r"第([一二两三四五六七八九十\d]+)(?:个|项|条)?"
            r"(?:计划|任务)?(?:也)?(?:完成|做完)(?:了|啦|吧)?",
            normalized,
        )
        if match is None:
            return None, None
        position = self._selection_number(match.group(1))
        selected = next(
            (item for item in snapshot.objects if item.display_order == position),
            None,
        )
        if selected is None:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "抱歉，刚才展示的列表里没有这一项，这次没有修改任何计划。",
            ), None
        today = self.interaction_coordinator.now_provider().date().isoformat()
        if selected.date and selected.date != today:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "抱歉，我当前只能可靠地完成今日计划，这次没有修改数据。你可以先查看今天计划，再说“第二项完成了”。",
            ), None
        live = self._snapshot_live_plans(
            ReadSnapshot(
                conversation_id=snapshot.conversation_id,
                intent=snapshot.intent,
                tool_name=snapshot.tool_name,
                objects=[selected],
                arguments=snapshot.arguments,
                captured_at=snapshot.captured_at,
                snapshot_id=snapshot.snapshot_id,
                schema_version=snapshot.schema_version,
            )
        )
        if not live:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "抱歉，刚才展示的这一项现在已经不存在，这次没有修改其他计划。",
            ), None
        if bool(live[0].get("done")) or str(live[0].get("status", "")) != "pending":
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "刚才展示的这一项目前不是待完成状态，这次没有修改其他计划。",
            ), None
        return None, self._semantic_from_continuation(
            message,
            "complete_plan",
            {"match_text": selected.stable_id},
            domain="plan",
        )

    def _snapshot_failure_turn(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        reply: str,
    ) -> ConversationTurn:
        self.interaction_coordinator.cancel(conversation_id)
        self.agent_core.executor.confirmation_manager.cancel(scope=conversation_id)
        response = AgentResponse("failed", reply, conversation_id=conversation_id)
        self._record_response(
            response, history_id, record_history, "read_snapshot_follow_up"
        )
        return ConversationTurn(
            message,
            conversation_id,
            {"intent": "complete_plan", "source": "read_snapshot"},
            response=response,
            record_history=record_history,
        )

    def _maybe_implicit_memory_turn(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
    ) -> Optional[ConversationTurn]:
        policy = str(intent_result.get("implicit_memory_policy", "")).strip()
        if policy not in {"confirm_before_save", "suppress_sensitive"}:
            return None
        candidate = next(
            (
                item
                for item in semantic_result.candidates
                if item.tool_name == "save_formal_memory"
            ),
            None,
        )
        if candidate is None or candidate.explicit_command:
            return None
        if policy == "suppress_sensitive":
            # Sensitive ordinary conversation remains chat.  Nothing is placed
            # in a persistent candidate store or pending save operation.
            memory_evidence: List[Dict[str, object]] = []
            llm_messages = self.build_llm_messages(
                message,
                conversation_id,
                memory_evidence_sink=memory_evidence,
            )
            return ConversationTurn(
                message,
                history_id or conversation_id,
                {
                    "intent": "chat",
                    "source": "sensitive_memory_guard",
                    "request_mode": "discuss",
                    "request_id": str(intent_result.get("request_id", "")),
                },
                llm_messages=llm_messages,
                record_history=record_history,
                memory_evidence=memory_evidence,
            )
        content = str(candidate.arguments.get("content", "")).strip()
        category = str(candidate.arguments.get("category", "general")).strip()
        if not content:
            return None
        immutable = {
            "tool_name": "save_formal_memory",
            "content": content,
            "category": category or "general",
        }
        self.interaction_coordinator.awaiting_confirmation(
            conversation_id,
            "formal_memory_save",
            immutable_arguments=immutable,
            action_preview=self._action_preview(
                immutable,
                safe_summary="仅在你确认后保存这条长期记忆。",
            ),
            domain="memory",
            request_mode="execute",
            original_user_text=message,
            safe_summary="仅在你确认后保存这条长期记忆。",
            originating_turn_id=str(intent_result.get("request_id", "")),
        )
        return self._interaction_turn(
            message,
            conversation_id,
            history_id,
            record_history,
            f"这条信息可能适合长期保存：{content}。要加入长期记忆吗？请回复“确认”或“取消”。",
        )

    _READ_FOLLOW_UP_TOOLS = {
        "show_plan",
        "inspect_plan_duplicates",
        "show_action_log",
        "show_growth_log",
        "list_memories",
        "search_memories",
        "show_recent_conversation",
        "show_conversation_history",
    }

    @staticmethod
    def _read_intent_for_tool(tool_name: str, fallback: str) -> str:
        return {
            "show_plan": "show_plan",
            "inspect_plan_duplicates": "inspect_plan_duplicates",
            "show_action_log": "show_action_log",
            "show_growth_log": "show_growth_log",
            "list_memories": "show_memory",
            "search_memories": "search_memory",
            "show_recent_conversation": "show_recent_conversation",
            "show_conversation_history": "show_conversation_history",
        }.get(tool_name, fallback)

    @staticmethod
    def _is_pending_control(normalized: str) -> bool:
        # A stray surrounding quote is ordinary input punctuation, not a topic
        # change.  Keep this normalization scoped to short control replies so
        # quoted task titles and other user content remain opaque.
        value = str(normalized or "").strip("\"'“”‘’")
        if value == "确认吧":
            return True
        return bool(re.fullmatch(r"(?:确认(?:吧)?|是的|执行(?:这条)?|继续|取消|算了|不用了|不要了|先不|第[一二三四五六七八九十\d]+个|全部|这个|这条)", value))

    @staticmethod
    def _apply_pending_state_note(response: AgentResponse, note: str) -> None:
        clean = str(note or "").strip()
        if clean and clean not in str(response.message):
            response.message = f"{clean}\n{response.message}"

    def _semantic_from_continuation(
        self,
        text: str,
        tool_name: str,
        arguments: Dict[str, object],
        *,
        domain: str,
        suggestion_selection: Optional[Mapping[str, object]] = None,
    ) -> SemanticParseResult:
        request_id = "req_" + uuid4().hex
        features = self.semantic_action_parser.feature_extractor.extract(text)
        tasks = arguments.get("tasks", []) if tool_name == "add_plan" else []
        candidates = []
        if isinstance(tasks, list) and tasks:
            for index, item in enumerate(tasks[:3]):
                if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                    continue
                candidates.append(
                    ActionCandidate(
                        domain=domain,
                        tool_name=tool_name,
                        arguments=dict(item),
                        request_mode="execute",
                        confidence=1.0,
                        explicit_command=True,
                        sequence_index=index,
                        source_intent="interaction_continuation",
                    )
                )
        else:
            candidates.append(
                ActionCandidate(
                    domain=domain,
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    request_mode="execute",
                    confidence=1.0,
                    explicit_command=True,
                    source_intent="interaction_continuation",
                )
            )
        if tool_name == "add_plan":
            for candidate in candidates:
                for source, target in (
                    ("date", "date"),
                    ("time_period", "time_slot"),
                    ("duration_minutes", "duration_minutes"),
                ):
                    if features.parsed_time.get(source) not in (None, ""):
                        candidate.arguments[target] = features.parsed_time[source]
        intent = {
            "intent": "resolved_interaction",
            "confidence": 1.0,
            "entities": (
                dict(candidates[0].arguments)
                if len(candidates) == 1 else dict(arguments)
            ),
            "needs_confirmation": False,
            "source": "interaction_state",
            "request_mode": "execute",
            "request_id": request_id,
            "clarification_question": None,
        }
        if tool_name == "add_plan" and isinstance(suggestion_selection, Mapping):
            selection = {
                key: str(suggestion_selection.get(key, "") or "").strip()
                for key in ("snapshot_id", "object_id")
            }
            if all(selection.values()):
                intent["suggestion_snapshot_selection"] = selection
        return SemanticParseResult(
            source="interaction_state",
            candidates=candidates,
            request_mode="execute",
            plain_chat_probability=0.0,
            intent_result=intent,
            local_features=features,
        )

    def _repair_grounded_pending_plan_completion(
        self,
        text: str,
        semantic_result: SemanticParseResult,
    ) -> SemanticParseResult:
        """Correct a model-only miss using the authoritative pending plan list.

        This is intentionally not a second natural-language intent router.  It
        only repairs an LLM ``chat`` decision when the user's text contains one
        exact pending-plan title and an unambiguous completion report.  The
        resulting tool argument is the stored plan UID, so no fuzzy title is
        allowed to cross the write boundary.
        """
        intent = (
            semantic_result.intent_result
            if isinstance(semantic_result.intent_result, dict)
            else {}
        )
        if (
            semantic_result.source != "llm"
            or semantic_result.candidates
            or str(intent.get("intent", "chat") or "chat") != "chat"
        ):
            return semantic_result
        features = semantic_result.local_features
        if features is None:
            return semantic_result
        normalized = self._compact(text)
        if (
            features.is_question
            or features.negation_cues
            or not (
                str(features.parsed_time.get("operation", "")) == "complete"
                or any(
                    cue in features.operation_cues
                    for cue in ("完成", "做完", "学完")
                )
            )
            or re.search(
                r"(?:没|没有|还没|尚未|未曾|并未)(?:有)?(?:完成|做完|学完)",
                normalized,
            )
            or any(marker in normalized for marker in ("如果", "假如", "要是", "万一"))
        ):
            return semantic_result
        plan_service = getattr(self.business_resolver, "plan_service", None)
        if plan_service is None:
            return semantic_result
        matches = []
        for item in plan_service.list_plans():
            if (
                not isinstance(item, dict)
                or bool(item.get("done"))
                or str(item.get("status", "pending") or "pending") != "pending"
            ):
                continue
            title = self._compact(str(item.get("title", "")))
            task_id = str(item.get("uid") or item.get("id") or "").strip()
            if title and task_id and title in normalized:
                matches.append((title, task_id))
        # If one stored title contains another, the longer exact mention is the
        # user's more specific reference (for example, "写小说至少五百字" over
        # "写小说").  Distinct maximal matches remain ambiguous and untouched.
        maximal = [
            item
            for item in matches
            if not any(
                item[0] != other[0] and item[0] in other[0]
                for other in matches
            )
        ]
        if len(maximal) != 1:
            return semantic_result
        _, task_id = maximal[0]
        request_id = str(intent.get("request_id", "") or "") or "req_" + uuid4().hex
        repaired_intent = {
            **intent,
            "mode": "write",
            "intent": "complete_plan",
            "entities": {"task_ref": task_id},
            "proposed_tool": "complete_plan",
            "confidence": 1.0,
            "needs_confirmation": False,
            "warnings": list(intent.get("warnings", [])),
            "clarification_question": None,
            "subject": "self",
            "polarity": "positive",
            "modality": "commitment",
            "request_mode": "execute",
            "explicit_command": True,
            "source": "grounded_plan_completion_guard",
            "request_id": request_id,
        }
        return SemanticParseResult(
            source="grounded_plan_completion_guard",
            candidates=[
                ActionCandidate(
                    domain="plan",
                    tool_name="complete_plan",
                    arguments={"match_text": task_id},
                    request_mode="execute",
                    confidence=1.0,
                    explicit_command=True,
                    source_intent="grounded_pending_plan_completion",
                    clause_text=str(text).strip(),
                    command_text=str(text).strip(),
                    polarity="statement",
                    clause_parse_status="actionable",
                )
            ],
            request_mode="execute",
            plain_chat_probability=0.0,
            provider=semantic_result.provider,
            model=semantic_result.model,
            latency_ms=semantic_result.latency_ms,
            intent_result=repaired_intent,
            local_features=features,
            clauses=[dict(item) for item in semantic_result.clauses],
        )

    def _semantic_from_object_selections(
        self,
        text: str,
        tool_name: str,
        base_arguments: Dict[str, object],
        selected_objects: List[Dict[str, object]],
        *,
        domain: str,
    ) -> SemanticParseResult:
        request_id = "req_" + uuid4().hex
        candidates = []
        for index, selected in enumerate(selected_objects):
            arguments = dict(base_arguments)
            # Once the user has chosen an item from the displayed candidates,
            # preserve that identity.  Re-resolving its title would make a
            # short title such as "写小说" ambiguous again when a longer task
            # contains the same words.
            exact_reference = selected.get("id") or selected.get("uid")
            arguments["match_text"] = str(exact_reference)
            candidates.append(
                ActionCandidate(
                    domain=domain,
                    tool_name=tool_name,
                    arguments=arguments,
                    request_mode="execute",
                    confidence=1.0,
                    explicit_command=True,
                    sequence_index=index,
                    source_intent="interaction_object_selection",
                )
            )
        intent = {
            "intent": "resolved_interaction",
            "confidence": 1.0,
            "entities": {},
            "needs_confirmation": False,
            "source": "interaction_state",
            "request_mode": "execute",
            "request_id": request_id,
            "clarification_question": None,
        }
        return SemanticParseResult(
            source="interaction_state",
            candidates=candidates,
            request_mode="execute",
            plain_chat_probability=0.0,
            intent_result=intent,
            local_features=self.semantic_action_parser.feature_extractor.extract(text),
        )

    def _start_plan_batch_completion(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        *,
        selected_all: bool = False,
    ) -> Optional[ConversationTurn]:
        if not self.interaction_coordinator_enabled or self.business_resolver is None:
            return None
        if re.search(r"[?？]", str(message or "")):
            return None
        normalized = self._compact(message)
        if not selected_all and not re.search(r"(?:完成|做完)", normalized):
            return None
        polarity_text = re.sub(
            r"(?:没有|没|未|尚未|还没)(?:完成|做完)(?:的)?(?:计划|任务)",
            "",
            normalized,
        )
        if re.search(
            r"(?:没有|没|未|不是|并非|不算|还没).{0,12}(?:完成|做完)",
            polarity_text,
        ):
            # Batch completion is an eager local safety path, so it must reject
            # negative claims before they can be mistaken for an instruction.
            return None
        if normalized.endswith(("吗", "么", "没", "没有")) or re.search(
            r"^(?:我)?(?:今天)?(?:是不是|是否|有没有)", normalized
        ):
            return None
        plan_service = getattr(self.business_resolver, "plan_service", None)
        if plan_service is None:
            return None
        pending = [
            dict(item)
            for item in plan_service.list_plans()
            if isinstance(item, dict)
            and not bool(item.get("done"))
            and str(item.get("status", "pending") or "pending") == "pending"
        ]
        targets: List[Dict[str, object]] = []
        snapshot = self._recent_plan_snapshot(conversation_id)
        if selected_all and snapshot is None:
            return self._snapshot_failure_turn(
                message, conversation_id, history_id, record_history,
                "当前没有可核验的最近计划列表，这次没有修改数据。请先说“查看今天计划”，或直接说要完成的具体标题。",
            )
        snapshot_reference = bool(
            snapshot is not None
            and (selected_all or re.search(r"(?:都|全部)", normalized))
            and (
                selected_all
                or re.search(r"刚才|刚刚|展示|这些|这几|上面|上述", normalized)
                or re.search(r"(?:计划|任务)我(?:都|全部)", normalized)
            )
        )
        if snapshot_reference and snapshot is not None:
            expected_count = self._collection_control_count(message)
            if expected_count and expected_count != len(snapshot.objects):
                return self._snapshot_failure_turn(
                    message, conversation_id, history_id, record_history,
                    "你说的数量与刚才展示的计划列表不一致，这次没有修改数据。请重新查看今天计划并明确目标。",
                )
            today = self.interaction_coordinator.now_provider().date().isoformat()
            if any(item.date and item.date != today for item in snapshot.objects):
                return self._snapshot_failure_turn(
                    message,
                    conversation_id,
                    history_id,
                    record_history,
                    "抱歉，我当前只能可靠地批量完成今日计划，这次没有修改数据。",
                )
            live = self._snapshot_live_plans(snapshot)
            if len(live) != len(snapshot.objects):
                return self._snapshot_failure_turn(
                    message, conversation_id, history_id, record_history,
                    "刚才展示的计划中有项目已不存在，这次没有修改其他计划。请重新查看今天计划。",
                )
            targets = [
                item for item in live
                if not bool(item.get("done"))
                and str(item.get("status", "pending") or "pending") == "pending"
            ]
            if not targets:
                if live and all(bool(item.get("done")) or item.get("status") == "completed" for item in live):
                    self.interaction_coordinator.cancel(conversation_id)
                    self.agent_core.executor.confirmation_manager.cancel(scope=conversation_id)
                    response = AgentResponse(
                        "completed", "刚才展示的计划已经全部完成，无需重复标记。这次没有修改记录。",
                        conversation_id=conversation_id,
                    )
                    self._record_response(response, history_id, record_history, "plan_state_unchanged")
                    return ConversationTurn(
                        message, conversation_id, {"intent": "complete_plan"},
                        response=response, record_history=record_history,
                    )
                return self._snapshot_failure_turn(
                    message,
                    conversation_id,
                    history_id,
                    record_history,
                    "刚才展示的计划目前没有仍待完成的项目，这次没有修改其他计划。",
                )

        if not pending and not targets:
            return None

        prefix = re.search(r"前([一二两三四五六七八九十\d]+)个", normalized)
        if not targets and prefix:
            amount = self._selection_number(prefix.group(1))
            if amount > 1:
                targets = pending[:amount]

        if not targets:
            # A counted demonstrative can safely name the whole live pending
            # set when the count matches exactly.  This covers a fresh natural
            # chain such as adding two plans and then saying “这两项我都完成了”
            # without guessing a subset when any extra plan exists.  The
            # resolved titles are still shown and require one confirmation.
            amount = self._collection_control_count(message)
            if (
                amount > 1
                and amount == len(pending)
                and re.search(r"(?:这|那)(?:两|[一二三四五六七八九十\d]+)(?:项|条|个)", normalized)
                and re.search(r"(?:都|全部).*(?:完成|做完)", normalized)
            ):
                targets = pending

        if not targets and "除了" in normalized and "都" in normalized:
            excluded_text = normalized.split("除了", 1)[1].split("都", 1)[0]
            excluded = self._unique_plan_hint_match(excluded_text, pending)
            if excluded is not None:
                excluded_id = str(excluded.get("uid") or excluded.get("id"))
                targets = [
                    item
                    for item in pending
                    if str(item.get("uid") or item.get("id")) != excluded_id
                ]

        if not targets and "都" in normalized:
            mentioned = []
            for item in pending:
                title = self._compact(str(item.get("title", "")))
                if title and title in normalized:
                    mentioned.append(item)
            if len(mentioned) > 1:
                targets = mentioned

        if not targets and re.search(
            r"(?:所有|全部)(?:的)?(?:计划|任务).*(?:完成|做完)|"
            r"(?:计划|任务)我(?:都|全部)(?:完成|做完)",
            normalized,
        ):
            targets = pending

        if not targets:
            return None
        target_ids = [str(item.get("uid") or item.get("id")) for item in targets]
        self.interaction_coordinator.awaiting_confirmation(
            conversation_id,
            "plan_batch_completion",
            selected_object_ids=target_ids,
            immutable_arguments={
                "tool_name": "complete_plan",
            },
            action_preview={"target_count": len(targets)},
            domain="plan",
            request_mode="execute",
            original_user_text=message,
            safe_summary="批量完成计划前等待用户确认。",
        )
        lines = ["我准备把下面这些计划标记为完成："]
        lines.extend(
            f"{index}. {str(item.get('title', '')).strip()}"
            for index, item in enumerate(targets, 1)
        )
        lines.append("确认无误的话，请回复“确认”。")
        return self._interaction_turn(
            message,
            conversation_id,
            history_id,
            record_history,
            "\n".join(lines),
        )

    def _execute_confirmed_plan_batch(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        targets: List[Dict[str, object]],
    ) -> ConversationTurn:
        """Execute an already confirmed set without the three-action model cap."""
        invalid = [
            item
            for item in targets
            if bool(item.get("done"))
            or str(item.get("status", "pending") or "pending") != "pending"
        ]
        if invalid:
            return self._snapshot_failure_turn(
                message,
                conversation_id,
                history_id,
                record_history,
                "确认前计划状态发生了变化，因此这次没有修改任何计划。请重新查看今日计划后再试。",
            )
        results = []
        steps = []
        for target in targets:
            task_ref = str(target.get("uid") or target.get("id") or "").strip()
            if not task_ref:
                continue
            resolved = {
                "intent": "resolved_interaction",
                "confidence": 1.0,
                "source": "confirmed_plan_batch",
                "needs_confirmation": False,
                "resolved_actions": [
                    {
                        "tool_name": "complete_plan",
                        "arguments": {"match_text": task_ref},
                        "depends_on": [],
                    }
                ],
            }
            single = self.agent_core.process(
                message,
                resolved,
                confirmation_scope=conversation_id,
            )
            results.extend(single.tool_results)
            steps.extend(single.steps)
        succeeded = sum(1 for item in results if item.success)
        status = (
            "completed"
            if succeeded == len(targets) and len(results) == len(targets)
            else "partial_success"
            if succeeded
            else "failed"
        )
        response = AgentResponse(
            status,
            "",
            steps=steps,
            tool_results=results,
            conversation_id=conversation_id,
        )
        response = self.response_composer.compose(response, user_text=message)
        self.interaction_coordinator.finish(
            conversation_id,
            partial=(status == "partial_success"),
            resolved_object_ids=[
                str(item.get("uid") or item.get("id") or "") for item in targets
            ],
        )
        self._record_response(
            response,
            history_id,
            record_history,
            "confirmed_plan_batch",
        )
        return ConversationTurn(
            message,
            conversation_id,
            {"intent": "complete_plan", "source": "confirmed_plan_batch"},
            response=response,
            record_history=record_history,
        )

    @classmethod
    def _unique_plan_hint_match(
        cls,
        hint: str,
        plans: List[Dict[str, object]],
    ) -> Optional[Dict[str, object]]:
        normalized_hint = cls._compact(hint)
        if not normalized_hint:
            return None
        direct = [
            item
            for item in plans
            if normalized_hint in cls._compact(str(item.get("title", "")))
        ]
        if len(direct) == 1:
            return direct[0]
        if len(normalized_hint) <= 4:
            covered = [
                item
                for item in plans
                if all(
                    char in cls._compact(str(item.get("title", "")))
                    for char in normalized_hint
                )
            ]
            if len(covered) == 1:
                return covered[0]
        return None

    @staticmethod
    def _selection_number(value: str) -> int:
        if str(value).isdigit():
            return int(value)
        return {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }.get(str(value), 0)

    def _memory_candidate_review_continuation(
        self,
        text: str,
        state,
    ) -> Optional[SemanticParseResult]:
        parts = self.semantic_action_parser.feature_extractor.split_actions(text)
        actions = []
        for index, part in enumerate(parts):
            normalized = self._compact(part)
            accepts = bool(re.search(r"(?:确认|接受|保存|加入长期记忆)", normalized))
            rejects = bool(re.search(r"(?:忽略|拒绝|不保存|不要)", normalized))
            selected = self._selected_pending_object(normalized, state)
            if accepts == rejects or not selected:
                return None
            actions.append(
                ActionCandidate(
                    domain="memory",
                    tool_name=(
                        "accept_memory_candidate"
                        if accepts
                        else "reject_memory_candidate"
                    ),
                    arguments={"candidate_id": int(selected)},
                    request_mode="execute",
                    confidence=1.0,
                    explicit_command=True,
                    sequence_index=index,
                    source_intent="interaction_candidate_review",
                )
            )
        if not actions:
            return None
        request_id = "req_" + uuid4().hex
        return SemanticParseResult(
            source="interaction_state",
            candidates=actions,
            request_mode="execute",
            plain_chat_probability=0.0,
            intent_result={
                "intent": "resolved_interaction",
                "confidence": 1.0,
                "entities": {},
                "needs_confirmation": False,
                "source": "interaction_state",
                "request_mode": "execute",
                "request_id": request_id,
                "clarification_question": None,
            },
            local_features=self.semantic_action_parser.feature_extractor.extract(text),
        )

    def _interaction_turn(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
        reply: str,
        *,
        bounded_pending: bool = False,
    ) -> ConversationTurn:
        status = "clarification"
        if bounded_pending and self.interaction_coordinator_enabled:
            state = self.interaction_coordinator.current(conversation_id)
            if state.pending and state.retry_count >= self.MAX_CLARIFICATION_ROUNDS - 1:
                self.interaction_coordinator.cancel(conversation_id)
                self.agent_core.executor.confirmation_manager.cancel(
                    scope=conversation_id
                )
                status = "failed"
                reply = self._clarification_exhausted_reply(state)
            elif state.pending:
                self.interaction_coordinator.update(
                    conversation_id,
                    retry_count=state.retry_count + 1,
                )
        response = AgentResponse(
            status,
            reply,
            conversation_id=conversation_id,
        )
        self._record_response(
            response,
            history_id,
            record_history,
            "interaction_continuation",
        )
        return ConversationTurn(
            message,
            conversation_id,
            {"intent": "interaction_continuation", "source": "interaction_state"},
            response=response,
            record_history=record_history,
        )

    @classmethod
    def _degraded_feature_reply(cls, tool_names) -> str:
        names = [str(item).strip() for item in tool_names if str(item).strip()]
        guidance = next(
            (
                DEFAULT_CAPABILITY_REGISTRY.guidance_for_tool(name)
                for name in names
                if DEFAULT_CAPABILITY_REGISTRY.guidance_for_tool(name)
            ),
            "把目标和要做的操作说得更具体一些",
        )
        return (
            "抱歉，我当前这个功能还不完善，这次没有完成。"
            f"你可以这样说：{guidance}"
        )

    @staticmethod
    def _is_unsupported_timer_request(text: str) -> bool:
        value = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
        if not value or re.search(r"(?:不要|别|不用|不想|取消).{0,8}(?:计时|倒计时)", value):
            return False
        return bool(
            re.search(
                r"(?:开始|启动|开启|替我启动|帮我启动).{0,10}(?:学习)?(?:计时|倒计时)"
                r"|(?:计时|倒计时).{0,8}(?:开始|启动|开启)",
                value,
            )
        )

    def _unsupported_timer_turn(
        self,
        message: str,
        conversation_id: str,
        history_id: str,
        record_history: bool,
    ) -> ConversationTurn:
        if self.interaction_coordinator_enabled:
            self.interaction_coordinator.cancel(conversation_id)
        self.agent_core.executor.confirmation_manager.cancel(scope=conversation_id)
        response = AgentResponse(
            "failed",
            "抱歉，开始学习计时当前还不完善，这次没有启动计时，也没有创建计划。"
            "如果你只是想安排学习，请明确说“今天添加计划：具体事项，25分钟”。",
            conversation_id=conversation_id,
        )
        self._record_response(
            response,
            history_id,
            record_history,
            "degraded_timer_request",
        )
        return ConversationTurn(
            message,
            conversation_id,
            {
                "intent": "chat",
                "source": "local_safety",
                "request_mode": "execute",
            },
            response=response,
            record_history=record_history,
        )

    @staticmethod
    def _candidate_review_unavailable_reply() -> str:
        return (
            "候选记忆功能已经停用，这次没有执行任何操作。"
            "要保存正式长期记忆，请说“记住：完整内容”；"
            "要查看已经保存的内容，请说“查看长期记忆”。"
        )

    def _is_candidate_memory_scope(self, text: str) -> bool:
        guard = getattr(self.intent_router, "memory_query_guard", None)
        matcher = getattr(guard, "is_retired_candidate_request", None)
        if not callable(matcher):
            return False
        try:
            return bool(matcher(text))
        except (AttributeError, TypeError, ValueError):
            return False

    @classmethod
    def _clarification_exhausted_reply(cls, state) -> str:
        arguments = getattr(state, "immutable_arguments", {})
        tool_name = (
            str(arguments.get("tool_name", "")).strip()
            if isinstance(arguments, Mapping)
            else ""
        )
        if tool_name == "complete_plan":
            return (
                "抱歉，我当前这个功能还不完善，这次没有完成。"
                "你可以这样说：完成计划：具体计划标题"
            )
        if tool_name == "add_plan" or getattr(state, "domain", "") == "plan":
            return (
                "抱歉，我当前这个功能还不完善，这次没有完成。"
                "你可以这样说：添加今天计划：具体事项，30分钟"
            )
        return cls._degraded_feature_reply([tool_name])

    def _register_semantic_clarification(
        self,
        semantic_result: SemanticParseResult,
        *,
        conversation_id: str,
        user_text: str,
        clarification_message: str,
        request_id: str,
    ) -> str:
        """Persist bounded semantic context so a clarification can continue."""
        if not self.interaction_coordinator_enabled:
            return clarification_message
        first_candidate = (
            semantic_result.candidates[0]
            if semantic_result.candidates
            else None
        )
        bound_confirmation = self._register_bound_plan_delete_confirmation(
            first_candidate,
            semantic_result=semantic_result,
            conversation_id=conversation_id,
            user_text=user_text,
            clarification_message=clarification_message,
            request_id=request_id,
        )
        if bound_confirmation:
            return bound_confirmation
        known_fields = {
            key: value
            for key, value in (
                first_candidate.arguments.items()
                if first_candidate is not None
                else []
            )
            if value not in (None, "", [])
        }
        plan_title = ""
        if first_candidate is not None and first_candidate.domain == "plan":
            plan_title = self._specific_plan_title(
                first_candidate.arguments.get("title", ""), user_text
            )
            if plan_title:
                known_fields["title"] = plan_title
        interaction_kind = (
            "advice_or_action_choice"
            if semantic_result.request_mode == "possible_action" and plan_title
            else "missing_slots"
        )
        self.interaction_coordinator.awaiting_clarification(
            conversation_id,
            interaction_kind,
            missing_fields=self._candidate_missing_fields(first_candidate),
            candidates=[item.to_dict() for item in semantic_result.candidates],
            immutable_arguments={
                "tool_name": first_candidate.tool_name,
                **known_fields,
            }
            if first_candidate is not None
            else {},
            domain=first_candidate.domain if first_candidate else "",
            request_mode=semantic_result.request_mode,
            original_user_text=user_text,
            known_fields=known_fields,
            safe_summary=clarification_message,
            originating_turn_id=request_id,
        )
        return clarification_message

    def _register_bound_plan_delete_confirmation(
        self,
        candidate,
        *,
        semantic_result: SemanticParseResult,
        conversation_id: str,
        user_text: str,
        clarification_message: str,
        request_id: str,
    ) -> Optional[str]:
        """Bind a unique delete target to the real confirmation manager.

        The semantic model may ask a confirmation question instead of emitting
        a tool call.  A confirmed delete is safe only when the local resolver
        can turn that proposal into one current stable plan identity and the
        executor has created its immutable, fingerprinted pending operation.
        """
        if (
            candidate is None
            or candidate.tool_name != "delete_plan"
            or candidate.request_mode != "execute"
            or self._multiple_plan_delete_scope(user_text, semantic_result.local_features)
            or not (
                candidate.explicit_command
                or str(getattr(candidate, "polarity", "")) == "command"
            )
        ):
            return None
        plans = self.business_resolver.plan_service.list_plans()
        current_text = self._compact(user_text)
        named = [
            item for item in plans
            if len(self._compact(str(item.get("title", "")))) >= 2
            and self._compact(str(item.get("title", ""))) in current_text
        ]
        # Ignore a short title only when it is contained in another complete
        # named title; distinct real titles are multiple objects, not a scalar.
        named = [
            item for item in named
            if not any(
                self._compact(str(item.get("title", ""))) != self._compact(str(other.get("title", "")))
                and self._compact(str(item.get("title", ""))) in self._compact(str(other.get("title", "")))
                for other in named
            )
        ]
        if len(named) > 1:
            return None
        explicit_ids = semantic_result.local_features.explicit_ids.get("plan", []) if semantic_result.local_features else []
        identified = [item for item in plans if explicit_ids and str(item.get("id")) == str(explicit_ids[0])]
        if explicit_ids and len(identified) != 1:
            return None
        if named and identified and named[0].get("uid") != identified[0].get("uid"):
            return None
        current_target = identified[0] if identified else named[0] if named else None
        if current_target is not None:
            candidate = self.business_resolver._clone(candidate)
            candidate.arguments = {"task_ref": str(current_target.get("uid") or current_target.get("id"))}
            candidate.reference_text = ""
            candidate.source_intent = "current_sentence_binding"
        references = self.state_manager.reference_context(conversation_id)
        resolutions = self.business_resolver.resolve_all(
            [candidate],
            conversation_id=conversation_id,
            features=semantic_result.local_features,
            structured_references=references,
        )
        if len(resolutions) != 1:
            return None
        resolution = resolutions[0]
        resolved = resolution.resolved_action
        if resolution.status != "resolved" or resolved is None:
            return None
        arguments = dict(resolved.arguments)
        task_ref = str(arguments.get("task_ref", "") or "").strip()
        if not task_ref:
            return None
        target = next(
            (
                item
                for item in resolution.candidate_objects
                if isinstance(item, dict)
                and str(item.get("uid") or item.get("id") or "").strip()
                == task_ref
            ),
            None,
        )
        target_title = ""
        if isinstance(target, dict):
            target_title = " ".join(
                str(target.get("title", "") or "").split()
            ).strip()[:120]
        safe_confirmation = (
            f"你是指删除“{target_title}”这个计划吗？确认后我再执行删除。"
            if target_title
            else "你是指删除刚才那条计划吗？确认后我再执行删除。"
        )
        pending_result = self.agent_core.executor.execute(
            "delete_plan",
            arguments,
            confidence=1.0,
            require_confirmation=True,
            confirmation_scope=conversation_id,
        )
        if pending_result.error != "confirmation_required":
            self.agent_core.executor.confirmation_manager.cancel(
                scope=conversation_id
            )
            return None
        immutable = {"tool_name": "delete_plan", **arguments}
        self.interaction_coordinator.awaiting_confirmation(
            conversation_id,
            "dangerous_tool",
            selected_object_ids=[task_ref],
            immutable_arguments=immutable,
            action_preview=self._action_preview(
                immutable,
                safe_summary=safe_confirmation,
            ),
            domain="plan",
            request_mode="execute",
            original_user_text=user_text,
            known_fields={"title": target_title} if target_title else {},
            safe_summary=safe_confirmation,
            originating_turn_id=request_id,
        )
        return safe_confirmation

    def _hydrate_runtime_semantic_arguments(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        current_message: str,
    ) -> None:
        entities = intent_result.setdefault("entities", {})
        if not isinstance(entities, dict):
            entities = {}
            intent_result["entities"] = entities
        pending_target = ""
        if self.interaction_coordinator_enabled:
            interaction = self.interaction_coordinator.current(conversation_id)
            if interaction.pending and interaction.interaction_kind == "plan_update":
                pending_target = next(iter(interaction.selected_object_ids), "")
        last_task_ref = ""
        if intent_result.get("follow_up_target") == "last_task":
            context = self.state_manager.reference_context(conversation_id)
            last_task = context.get("last_task")
            if isinstance(last_task, dict):
                identity = str(last_task.get("uid") or last_task.get("id") or "")
                plan_service = getattr(self.business_resolver, "plan_service", None)
                current_plans = plan_service.list_plans() if plan_service else []
                if identity and any(
                    str(item.get("uid") or item.get("id") or "") == identity
                    for item in current_plans
                ):
                    last_task_ref = identity
        for candidate in semantic_result.candidates:
            if pending_target and candidate.tool_name in {"update_plan", "reschedule_plan"}:
                runtime_arguments = {"task_ref": pending_target}
            elif last_task_ref and candidate.tool_name in {
                "update_plan", "reschedule_plan", "complete_plan", "reopen_plan",
                "cancel_plan", "delete_plan",
            }:
                # Hydrate only a model-declared reference to a verified tool
                # result in this conversation. Explicit object arguments win.
                definition = self._tool_registry().get(candidate.tool_name)
                target_field = "match_text" if candidate.tool_name == "complete_plan" else "task_ref"
                target_fields = {target_field} | {
                    alias for alias, canonical in definition.field_aliases.items()
                    if canonical == target_field
                }
                if any(
                    candidate.arguments.get(field) not in (None, "", "last_task")
                    for field in target_fields
                ):
                    continue
                runtime_arguments = {target_field: last_task_ref}
            elif candidate.tool_name == "show_recent_conversation":
                runtime_arguments = {
                    "conversation_id": conversation_id,
                    "current_message": current_message,
                }
            elif candidate.tool_name == "show_conversation_history":
                runtime_arguments = {
                    "current_conversation_id": conversation_id,
                    "exclude_today": bool(entities.get("exclude_today", False)),
                }
            else:
                continue
            candidate.arguments.update(runtime_arguments)
            entities.update(runtime_arguments)

    def _bind_snapshot_ordinal_completion(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        current_message: str,
    ) -> None:
        """Correct only the object identity after the model chose completion."""
        completion_candidates = [
            candidate
            for candidate in semantic_result.candidates
            if candidate.tool_name == "complete_plan"
        ]
        if len(completion_candidates) != 1:
            return
        snapshot = self._recent_plan_snapshot(conversation_id)
        if snapshot is None:
            return
        current_text = self._compact(current_message)
        named = [
            item for item in snapshot.objects
            if len(self._compact(item.title)) >= 2
            and self._compact(item.title) in current_text
        ]
        if named:
            longest = max(len(self._compact(item.title)) for item in named)
            named = [item for item in named if len(self._compact(item.title)) == longest]
            if len(named) != 1:
                return
            # The current sentence's exact real title outranks index-like words
            # inside that title. Never complete another displayed task instead.
            selected = named[0]
        else:
            position = self._ordinal_position(current_message)
            if position <= 0:
                return
            selected = next(
                (item for item in snapshot.objects if item.display_order == position),
                None,
            )
        if selected is None:
            return
        candidate = completion_candidates[0]
        candidate.arguments = {"match_text": selected.stable_id}
        candidate.source_intent = "read_snapshot_binding"
        live = self._snapshot_live_plans(
            ReadSnapshot(
                conversation_id=snapshot.conversation_id,
                intent=snapshot.intent,
                tool_name=snapshot.tool_name,
                objects=[selected],
                arguments=snapshot.arguments,
                captured_at=snapshot.captured_at,
                snapshot_id=snapshot.snapshot_id,
                schema_version=snapshot.schema_version,
            ),
            pending_only=False,
        )
        if not live:
            return
        candidate.request_mode = "execute"
        # The model had already selected ``complete_plan``; the recent read
        # snapshot now supplies an exact, live object identity.  That removes
        # the ambiguity which originally caused the clarification hint.
        candidate.confidence = max(candidate.confidence, 0.99)
        candidate.explicit_command = True
        candidate.requires_confirmation_hint = False
        candidate.missing_fields = []
        candidate.ambiguities = []
        semantic_result.request_mode = "execute"
        semantic_result.needs_clarification = False
        semantic_result.global_ambiguities = []
        entities = intent_result.setdefault("entities", {})
        if not isinstance(entities, dict):
            entities = {}
            intent_result["entities"] = entities
        entities["match_text"] = selected.stable_id
        intent_result["request_mode"] = "execute"
        intent_result["confidence"] = max(
            float(intent_result.get("confidence", 0.0) or 0.0),
            0.99,
        )
        intent_result["needs_confirmation"] = False
        intent_result["clarification_question"] = None

    def _bind_snapshot_ordinal_delete(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        current_message: str,
    ) -> None:
        """Bind a model-selected delete to one live item from the last plan read."""

        delete_candidates = [
            candidate
            for candidate in semantic_result.candidates
            if candidate.tool_name == "delete_plan"
        ]
        if len(delete_candidates) != 1:
            return
        position = self._ordinal_position(current_message)
        if position <= 0:
            return
        snapshot = self._recent_plan_snapshot(conversation_id)
        if snapshot is None:
            return
        current_text = self._compact(current_message)
        named = [
            item for item in snapshot.objects
            if len(self._compact(item.title)) >= 2
            and self._compact(item.title) in current_text
        ]
        if named:
            longest = max(len(self._compact(item.title)) for item in named)
            named = [
                item for item in named
                if len(self._compact(item.title)) == longest
            ]
            selected = named[0] if len(named) == 1 else None
        else:
            selected = next(
                (
                    item for item in snapshot.objects
                    if item.display_order == position
                ),
                None,
            )
        candidate = delete_candidates[0]
        if selected is None:
            # Never let a stale model reference such as ``last_task`` become a
            # fallback when the user's displayed ordinal is invalid.
            candidate.arguments = {"task_ref": "__invalid_displayed_plan__"}
            candidate.source_intent = "read_snapshot_binding"
            return
        live = self._snapshot_live_plans(
            ReadSnapshot(
                conversation_id=snapshot.conversation_id,
                intent=snapshot.intent,
                tool_name=snapshot.tool_name,
                objects=[selected],
                arguments=snapshot.arguments,
                captured_at=snapshot.captured_at,
                snapshot_id=snapshot.snapshot_id,
                schema_version=snapshot.schema_version,
            ),
            pending_only=False,
        )
        if not live:
            candidate.arguments = {"task_ref": "__invalid_displayed_plan__"}
            candidate.source_intent = "read_snapshot_binding"
            return
        candidate.arguments = {"task_ref": selected.stable_id}
        candidate.source_intent = "read_snapshot_binding"
        candidate.request_mode = "execute"
        candidate.confidence = max(candidate.confidence, 0.99)
        candidate.explicit_command = True
        candidate.requires_confirmation_hint = True
        candidate.missing_fields = []
        candidate.ambiguities = []
        semantic_result.request_mode = "execute"
        semantic_result.needs_clarification = False
        semantic_result.global_ambiguities = []
        entities = intent_result.setdefault("entities", {})
        if not isinstance(entities, dict):
            entities = {}
            intent_result["entities"] = entities
        entities.clear()
        entities["task_ref"] = selected.stable_id
        intent_result["request_mode"] = "execute"
        intent_result["confidence"] = max(
            float(intent_result.get("confidence", 0.0) or 0.0),
            0.99,
        )
        intent_result["needs_confirmation"] = True
        intent_result["clarification_question"] = None

    def _bind_suggestion_snapshot_reference(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        current_message: str,
    ) -> None:
        """Bind an ordinal only after semantics selected the add-plan capability."""

        candidates = [
            candidate
            for candidate in semantic_result.candidates
            if candidate.tool_name == "add_plan"
        ]
        if len(candidates) != 1:
            return
        candidate = candidates[0]
        title = self._compact(str(candidate.arguments.get("title", "") or ""))
        position = self._suggestion_reference_position(current_message)
        title_is_reference_shell = bool(
            re.fullmatch(
                r"(?:就|把|将)?(?:第)?[一二两三四五六七八九十\d]+"
                r"(?:个|项|条)(?:也|再|还|都)?",
                title,
            )
        )
        if (
            semantic_result.source == "interaction_state"
            and title
            and not ReferenceResolver.is_previous_assistant_plan_reference(title)
            and not title_is_reference_shell
        ):
            # A typed continuation already consumed a verified pending option.
            # Do not reinterpret its explicit time fields as a fresh reference.
            return
        if (
            title
            and title in self._compact(current_message)
            and not ReferenceResolver.is_previous_assistant_plan_reference(title)
            and not title_is_reference_shell
        ):
            # A task title explicitly present now outranks an ordinal embedded
            # inside that title (e.g. reading the second chapter).
            return
        if position <= 0:
            return
        if self._unsafe_suggestion_reference(current_message):
            self._reject_unbound_suggestion_reference(
                semantic_result,
                intent_result,
                "这次没有添加计划。请明确说要加入哪一项建议，例如“第二项加入今天计划”。",
            )
            return
        snapshot = self._recent_suggestion_snapshot(conversation_id)
        if snapshot is None:
            if self._contains_assistant_plan_reference(current_message):
                # Let the existing grounded source-extraction flow establish
                # its first real list before attempting ordinal selection.
                return
            self._reject_unbound_suggestion_reference(
                semantic_result,
                intent_result,
                "刚才的建议列表已经失效。请让我重新列出建议，或直接说要加入的具体事项。",
            )
            return
        selected = snapshot.select_position(position)
        if selected is None or selected.consumed or not selected.title:
            self._reject_unbound_suggestion_reference(
                semantic_result,
                intent_result,
                "刚才的建议列表里没有可继续加入的这一项。请重新选择未处理的项目，或直接说具体事项。",
            )
            return
        candidate.arguments = {"title": selected.title}
        parsed_time = (
            semantic_result.local_features.parsed_time
            if semantic_result.local_features else {}
        )
        for source, target in (
            ("date", "date"),
            ("time_period", "time_slot"),
            ("duration_minutes", "duration_minutes"),
        ):
            if parsed_time.get(source) not in (None, ""):
                candidate.arguments[target] = parsed_time[source]
        candidate.request_mode = "execute"
        candidate.confidence = max(candidate.confidence, 0.99)
        candidate.explicit_command = True
        candidate.requires_confirmation_hint = False
        candidate.missing_fields = []
        candidate.ambiguities = []
        semantic_result.request_mode = "execute"
        semantic_result.needs_clarification = False
        semantic_result.global_ambiguities = []
        entities = intent_result.setdefault("entities", {})
        if not isinstance(entities, dict):
            entities = {}
            intent_result["entities"] = entities
        entities.clear()
        entities.update(candidate.arguments)
        intent_result["request_mode"] = "execute"
        intent_result["confidence"] = max(
            float(intent_result.get("confidence", 0.0) or 0.0),
            0.99,
        )
        intent_result["needs_confirmation"] = False
        intent_result["clarification_question"] = None
        intent_result["suggestion_snapshot_selection"] = {
            "snapshot_id": snapshot.snapshot_id,
            "object_id": selected.stable_id,
        }

    @staticmethod
    def _reject_unbound_suggestion_reference(
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        question: str,
    ) -> None:
        semantic_result.candidates = []
        semantic_result.needs_clarification = True
        semantic_result.global_ambiguities = ["suggestion_snapshot_reference"]
        semantic_result.intent_result.update(
            {
                "entities": {},
                "needs_confirmation": False,
                "clarification_question": question,
                "request_mode": "execute",
            }
        )
        intent_result.update(
            {
                "entities": {},
                "needs_confirmation": False,
                "clarification_question": question,
                "request_mode": "execute",
            }
        )

    def _promote_committed_plan_add(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        current_message: str,
    ) -> None:
        """Honor structured commitment semantics when every add has a title."""
        candidates = list(semantic_result.candidates)
        if not candidates or any(
            candidate.domain != "plan" or candidate.tool_name != "add_plan"
            for candidate in candidates
        ):
            return
        if (
            str(intent_result.get("subject", "")) != "self"
            or str(intent_result.get("polarity", "")) != "positive"
            or str(intent_result.get("modality", "")) != "commitment"
        ):
            return
        titles = [
            self._specific_plan_title(
                candidate.arguments.get("title", ""),
                current_message,
            )
            for candidate in candidates
        ]
        if not all(titles):
            return
        for candidate, title in zip(candidates, titles):
            candidate.arguments["title"] = title
            candidate.request_mode = "execute"
            candidate.missing_fields = []
            candidate.ambiguities = []
        semantic_result.request_mode = "execute"
        semantic_result.needs_clarification = False
        semantic_result.global_ambiguities = []
        intent_result["request_mode"] = "execute"
        intent_result["needs_confirmation"] = False
        intent_result["clarification_question"] = None
        entities = intent_result.setdefault("entities", {})
        if isinstance(entities, dict) and len(candidates) == 1:
            entities["title"] = titles[0]

    def _uses_degraded_v22_plan_capability(
        self,
        semantic_result: SemanticParseResult,
    ) -> bool:
        """Stop closed V2.2 capabilities before they create pending state."""
        return semantic_result.source == "llm" and any(
            candidate.domain == "plan"
            and candidate.tool_name in self._V22_DEGRADED_PLAN_TOOLS
            for candidate in semantic_result.candidates
        )

    @staticmethod
    def _multiple_plan_delete_scope(user_text: str, features) -> bool:
        """A scalar model target cannot narrow a locally explicit collection."""
        if any(term in str(user_text or "") for term in (
            "全部", "所有", "多个", "多项", "多条", "这些", "那些", "这几", "那几",
        )):
            return True
        ordinals = set(getattr(features, "ordinal_expressions", []) or [])
        identifiers = getattr(features, "explicit_ids", {}) or {}
        return len(ordinals) > 1 or len(set(identifiers.get("plan", []))) > 1

    def _bind_exact_memory_forget(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        user_text: str,
        history_id: Optional[str],
        record_history: bool,
        target_guard: Optional[Dict[str, object]] = None,
    ) -> Optional[ConversationTurn]:
        """Bind one literal forget command to a real record before validation.

        No fuzzy retrieval or model-provided memory ID grants deletion authority.
        Execution and its one confirmation remain in the existing tool chain.
        """
        literal = str(user_text or "").strip()
        exact = literal.startswith(("忘记：", "忘记:"))
        deletes = (
            str(intent_result.get("intent", "")) == "delete_memory"
            or str(intent_result.get("proposed_tool", "")) in {"delete_memory", "delete_all_memories"}
            or any(item.tool_name in {"delete_memory", "delete_all_memories"} for item in semantic_result.candidates)
        )
        if not exact and not deletes:
            return None
        self.agent_core.executor.confirmation_manager.cancel(scope=conversation_id)
        if self.interaction_coordinator_enabled:
            self.interaction_coordinator.cancel(conversation_id)

        def fail(message: str) -> ConversationTurn:
            self._update_semantic_diagnostics(conversation_id, intent_result, semantic_result)
            return self._snapshot_failure_turn(
                user_text, conversation_id, history_id, record_history, message,
            )

        if not exact:
            return fail(
                "这次没有删除记忆。聊天删除只支持“忘记：完整记忆正文”，"
                "会先展示目标并请你确认；也可以打开“记忆”面板选择记录删除。"
            )
        content = literal[3:].strip()
        if not content:
            return fail("这次没有删除记忆。请在“忘记：”后填写要删除的完整记忆正文。")
        listed = self.business_resolver.memory_service.list_memories(status=None)
        if not listed.success:
            return fail("长期记忆暂时无法读取，这次没有删除。请稍后重试或打开“记忆”面板。")
        matches = [
            item for item in listed.data.get("memories", [])
            if isinstance(item, Mapping) and str(item.get("content", "")) == content
        ]
        if not matches:
            return fail(
                "没有找到正文完全一致的长期记忆，这次没有删除。"
                "请核对完整正文；聊天内容和旧资料不属于可删除的正式记忆记录。"
            )
        if len(matches) != 1:
            return fail(
                f"找到 {len(matches)} 条正文相同的记忆，这次没有删除。"
                "请打开“记忆”面板选择具体记录删除。"
            )
        try:
            memory_id = int(matches[0].get("id", 0))
        except (TypeError, ValueError):
            memory_id = 0
        if memory_id <= 0:
            return fail("这条记忆的编号无效，这次没有删除。请打开“记忆”面板检查。")
        candidate = ActionCandidate(
            domain="memory", tool_name="delete_memory",
            arguments={"memory_id": memory_id}, request_mode="execute",
            confidence=1.0, explicit_command=True,
            requires_confirmation_hint=True, source_intent="exact_memory_forget",
        )
        semantic_result.candidates = [candidate]
        semantic_result.source = "fixed_command"
        semantic_result.request_mode = "execute"
        semantic_result.needs_clarification = False
        semantic_result.global_ambiguities = []
        intent_result.update(
            intent="delete_memory", mode="write", source="fixed_command",
            entities={"memory_id": memory_id}, slots={"memory_id": memory_id},
            request_mode="execute", explicit_command=True,
            confidence=1.0, needs_confirmation=True, clarification_question=None,
            proposed_tool="delete_memory", candidate_actions=[],
        )
        semantic_result.intent_result = dict(intent_result)
        if target_guard is not None:
            # Internal execution evidence only: never a model parameter or
            # diagnostic payload. The executor compares it under the memory
            # transaction before showing a confirmation for this exact body.
            target_guard.update(
                id=memory_id, content=content,
                created_at=str(matches[0].get("created_at", "")),
            )
        return None

    def _bridge_explicit_plan_delete(
        self,
        semantic_result: SemanticParseResult,
        intent_result: Dict[str, object],
        *,
        conversation_id: str,
        user_text: str,
        history_id: Optional[str],
        record_history: bool,
    ) -> Optional[ConversationTurn]:
        """Bind one explicit delete locally without exposing it to the model.

        ``delete_plan`` intentionally remains outside the model-visible tool
        catalog.  The model may identify the user's intent, but only current
        sentence features plus a live local plan lookup can create the real
        confirmation pending.
        """
        if semantic_result.source != "llm" or len(semantic_result.candidates) != 1:
            return None
        candidate = semantic_result.candidates[0]
        features = semantic_result.local_features
        operation_cues = list(getattr(features, "operation_cues", []) or [])
        if (
            candidate.tool_name != "delete_plan"
            or candidate.request_mode != "execute"
            or str(getattr(features, "polarity", "")) != "command"
            or "删除" not in operation_cues
            or bool(getattr(features, "negation_cues", []) or [])
        ):
            return None
        # The model may use clarify mode for a confirmation, while the current
        # sentence is already an explicit scalar deletion command. Preserve
        # that reliable local path without trusting the model's prose.
        candidate = self.business_resolver._clone(candidate)
        candidate.explicit_command = True
        request_id = str(intent_result.get("request_id", ""))
        confirmation = self._register_bound_plan_delete_confirmation(
            candidate,
            semantic_result=semantic_result,
            conversation_id=conversation_id,
            user_text=user_text,
            clarification_message="",
            request_id=request_id,
        )
        if not confirmation:
            return None
        self._update_semantic_diagnostics(
            conversation_id,
            intent_result,
            semantic_result,
        )
        response = AgentResponse(
            "clarification",
            confirmation,
            conversation_id=conversation_id,
            request_id=request_id or None,
        )
        self._record_response(
            response,
            history_id,
            record_history,
            "delete_plan_confirmation",
        )
        return ConversationTurn(
            user_text,
            conversation_id,
            intent_result,
            response=response,
            record_history=record_history,
        )

    @classmethod
    def _ordinal_position(cls, text: str) -> int:
        return cls._suggestion_reference_position(text)

    @classmethod
    def _suggestion_reference_position(cls, text: str) -> int:
        """An index is not a quantity: '再加一条/一个小时' are not references."""
        normalized = cls._compact(text)
        match = re.search(
            r"第([一二两三四五六七八九十\d]+)(?:个|项|条)", normalized
        )
        if match:
            return cls._selection_number(match.group(1))
        numeric = re.match(r"^(\d+)[.、](?!\d)", str(text).strip())
        return cls._selection_number(numeric.group(1)) if numeric else 0

    @staticmethod
    def _plan_choice_candidates(
        semantic_result: SemanticParseResult,
    ) -> List[ActionCandidate]:
        if semantic_result.request_mode not in {"possible_action", "execute"}:
            return []
        candidates = list(semantic_result.candidates)
        if not candidates or any(
            candidate.domain != "plan" or candidate.tool_name != "add_plan"
            for candidate in candidates
        ):
            return []
        has_omitted_schedule_items = bool(
            semantic_result.intent_result.get("schedule_unresolved_items")
            or semantic_result.intent_result.get("schedule_overflow_count")
        )
        # ``request_mode=execute`` is the canonical semantic decision that the
        # user asked the application to perform the write.  Do not reinterpret
        # it from a second list of Chinese command phrases.  Real omissions in
        # a bounded schedule still need a choice so nothing is silently lost.
        if (
            semantic_result.request_mode == "execute"
            and all(candidate.request_mode == "execute" for candidate in candidates)
            and not has_omitted_schedule_items
        ):
            return []
        return candidates

    @staticmethod
    def _candidate_missing_fields(candidate) -> List[str]:
        if candidate is None:
            return []
        if candidate.tool_name != "add_plan":
            return list(candidate.ambiguities)
        result = []
        title = str(candidate.arguments.get("title", "") or "").strip()
        if not title or title in {"学一会儿", "学习一会儿", "做一会儿"}:
            result.append("title")
        if candidate.arguments.get("duration_minutes") in (None, ""):
            result.append("duration_minutes")
        return result

    @staticmethod
    def _is_persistent_write(candidate: ActionCandidate) -> bool:
        return candidate.tool_name in {
            "add_plan",
            "complete_plan",
            "delete_plan",
            "update_plan",
            "merge_plan",
            "reschedule_plan",
            "reopen_plan",
            "cancel_plan",
            "add_action_log",
            "create_memory_candidate",
            "request_add_memory",
            "queue_memory_candidate",
            "save_formal_memory",
            "archive_memory",
            "restore_memory",
            "delete_memory",
            "accept_memory_candidate",
            "reject_memory_candidate",
            "accept_memory_candidates",
            "reject_memory_candidates",
            "resolve_memory_conflict",
        }

    @staticmethod
    def _batch_clarification_prompt(unresolved_writes, *, total_write_count: int) -> str:
        prompts = [str(item.safe_prompt).strip() for item in unresolved_writes]
        primary = next((item for item in prompts if item), "我还不能安全确定要写入的内容。")
        if total_write_count <= 1:
            return primary
        details = []
        domains = set()
        for index, item in enumerate(unresolved_writes, 1):
            prompt = str(item.safe_prompt).strip() or "缺少要写入的具体内容。"
            details.append(f"第{index}项：{prompt}")
            domains.add(str(item.candidate.domain))
        examples = []
        if "plan" in domains:
            examples.append("把‘复习随机森林’加入今天计划。")
        if "memory" in domains:
            examples.append("请记住：我的长期目标是成为AI经理和Agent全栈开发者。")
        if "action_log" in domains:
            examples.append("记录：我完成了随机森林练习。")
        return (
            "这次没有修改数据。"
            f"这组写入请求里有 {len(unresolved_writes)} 项还不完整，"
            "所以我没有执行其中任何一项。"
            + "\n".join(details)
            + ("\n可以这样说：" + "\n".join(examples) if examples else "")
        )

    def _specific_plan_title(self, value: object, original_text: str) -> str:
        title = self.semantic_action_parser.feature_extractor.entity_parser.clean_plan_title(
            str(value or "").strip()
        )
        if not title:
            title = self._followup_plan_title(original_text)
        title = str(title or "").strip(" \t\r\n，,：:。.!！?？-—_")
        shell = re.sub(r"^(?:请|帮我|给我|麻烦)?", "", self._compact(title))
        shell = re.sub(r"^(?:再|又|也|另外|顺便)?", "", shell)
        shell = re.sub(r"^(?:加|添加|加入|新增)(?:一条|一项|一个|个)?", "", shell)
        if shell in {
            "",
            "计划",
            "今天计划",
            "今日计划",
            "任务",
            "今天任务",
            "今日任务",
            "清单",
            "待办",
            "今日待办",
            "今天待办",
        }:
            return ""
        return "" if title in {"学一会儿", "学习一会儿", "做一会儿"} else title

    def _followup_plan_title(self, text: str) -> str:
        value = self.semantic_action_parser.feature_extractor.entity_parser.clean_plan_title(text)
        value = re.sub(r"^(?:学习|学|练习)", "", value).strip("，,：:。 ")
        value = re.sub(r"(?:加入|放进|添加)(?:今天)?(?:计划|任务|清单).*$", "", value)
        value = re.sub(r"^(?:第[一二三四五六七八九十\d]+个|这个|那个)$", "", value)
        if value in {"", "不知道", "你给我安排一下可以吗", "你安排一下", "加入计划"}:
            return ""
        return value

    @staticmethod
    def _default_learning_options() -> List[Dict[str, object]]:
        return [
            {"id": "advice_1", "title": "复习最近学过的核心概念"},
            {"id": "advice_2", "title": "继续当前项目的主程序"},
            {"id": "advice_3", "title": "练习特征工程并记录问题"},
        ]

    @staticmethod
    def _selected_suggestion(text: str, options: List[Dict[str, object]]):
        if not options:
            return None
        if re.search(r"(?:第(?:一|1)(?:个|条|项)|^1(?:个|条|项|号)?)", text):
            return options[0]
        if re.search(r"(?:第(?:二|两|2)(?:个|条|项)|^2(?:个|条|项|号)?)", text) and len(options) > 1:
            return options[1]
        if re.search(r"(?:第(?:三|3)(?:个|条|项)|^3(?:个|条|项|号)?)", text) and len(options) > 2:
            return options[2]
        if "最后一个" in text:
            return options[-1]
        return None

    @staticmethod
    def _selected_pending_object(text: str, state) -> str:
        ids = list(state.listed_object_ids)
        if not ids:
            return ""
        if re.search(r"(?:第(?:一|1)个|^1(?:个|号)?)", text):
            return ids[0]
        if re.search(r"(?:第(?:二|两|2)个|^2(?:个|号)?)", text) and len(ids) > 1:
            return ids[1]
        if "最后一个" in text:
            return ids[-1]
        explicit = re.search(r"(?:计划|任务|候选|记忆)\s*(\d+)", text)
        if explicit and explicit.group(1) in ids:
            return explicit.group(1)
        return ""

    @classmethod
    def _selected_pending_objects(cls, text: str, state) -> List[str]:
        ids = list(state.listed_object_ids)
        if not ids:
            return []
        if re.fullmatch(
            r"(?:都|全部)(?:处理|完成|标记完成|算完成)?(?:了|吧)?",
            text,
        ):
            return ids

        ordinal_values = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        positions = []
        for token in re.findall(r"第([一二两三四五六七八九十\d]+)(?:个|项|条)?", text):
            position = int(token) if token.isdigit() else ordinal_values.get(token, 0)
            if 1 <= position <= len(ids) and position not in positions:
                positions.append(position)
        if len(positions) > 1:
            return [ids[position - 1] for position in positions]

        numeric_sequence = re.fullmatch(r"(\d+)(?:和(\d+))+(?:都)?", text)
        if numeric_sequence:
            positions = [int(item) for item in re.findall(r"\d+", text)]
            if all(1 <= position <= len(ids) for position in positions):
                return [ids[position - 1] for position in dict.fromkeys(positions)]

        selected = cls._selected_pending_object(text, state)
        return [selected] if selected else []

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))

    def _is_complete_plan_command(self, text: str) -> bool:
        feature_extractor = getattr(self.semantic_action_parser, "feature_extractor", None)
        if feature_extractor is None:
            return False
        features = feature_extractor.extract(text)
        # A command envelope can be structurally complete while its payload is
        # only a reference to the current pending target (for example,
        # ``把它加入今天计划``).  Such a turn must continue that interaction;
        # cancelling it here would make the fresh route persist the pronoun as
        # the literal plan title.  Concrete payloads still preempt stale state.
        if self._is_pure_plan_reference_payload(features.payload_text):
            return False
        return bool(
            features.payload_text
            and features.polarity == "command"
            and "plan" in features.domain_cues
            and any(
                cue in features.operation_cues
                for cue in ("加入", "添加", "加进", "加到", "放进", "放到", "安排")
            )
        )

    @staticmethod
    def _is_pure_plan_reference_payload(payload: object) -> bool:
        value = ConversationService._compact(str(payload or ""))
        if not value:
            return False
        return bool(
            re.fullmatch(
                r"(?:它|前者|后者|这些|那些|这几个|那几个|这几条|那几条|"
                r"上一个|前一个|(?:这|那|该)(?:个|条|项|件)?"
                r"(?:事(?:情)?|安排|内容|任务|计划)?|"
                r"(?:刚才|之前|前面|上面)(?:(?:说|提到)的)?"
                r"(?:(?:这|那)(?:个|条|项|件)?"
                r"(?:事(?:情)?|安排|内容|任务|计划)?|(?:内容|安排))?)",
                value,
            )
        )

    @staticmethod
    def _is_fresh_query(text: str) -> bool:
        return text in {
            "今日计划",
            "我的计划",
            "今天的计划",
            "查看计划",
            "我的行动记录",
            "查看记录",
            "你记得我什么",
            "查看长期记忆",
        }

    @staticmethod
    def _is_conversation_memory_summary_request(text: str) -> bool:
        """Scope memory-summary prose to the current conversation only."""
        value = re.sub(r"[\s，,。.!！?？；;：:]", "", str(text or ""))
        return bool(
            re.search(r"(?:总结|概括|回顾).{0,12}(?:对话|聊天|我们)", value)
            and re.search(r"(?:保存|存入|记住|长期记忆|记忆)", value)
        )

    def _verified_chat_turn_context(
        self,
        intent_result: Dict[str, object],
        semantic_result: Optional[SemanticParseResult],
        conversation_id: str,
        *,
        user_text: str = "",
    ) -> VerifiedTurnContext:
        features = (
            semantic_result.local_features
            if semantic_result is not None
            else None
        )
        operation_cues = getattr(features, "operation_cues", [])
        domain_cues = getattr(features, "domain_cues", [])
        plans = []
        plan_service = getattr(self.business_resolver, "plan_service", None)
        if plan_service is not None:
            try:
                plans = plan_service.list_plans()
            except (OSError, TypeError, ValueError):
                plans = []
        references = self.state_manager.reference_context(conversation_id)
        memory_facts, memory_operation = self._verified_recent_memory_context(
            conversation_id, references, user_text,
        )
        return VerifiedTurnContext.for_chat_reply(
            intent_result,
            operation_cues=operation_cues,
            domain_cues=domain_cues,
            plans=plans,
            confirmation_pending=self._verified_confirmation_pending(conversation_id),
            prior_tool_result=(
                references.get("last_tool_result")
                if isinstance(references.get("last_tool_result"), Mapping)
                else None
            ),
            prior_task=(
                references.get("last_task")
                if isinstance(references.get("last_task"), Mapping)
                else None
            ),
            prior_memory_facts=memory_facts,
            prior_memory_operation=memory_operation,
            prior_client_action=references.get("prior_client_action"),
        )

    def _verified_recent_memory_context(
        self,
        conversation_id: str,
        references: Mapping[str, object],
        user_text: str,
    ) -> tuple[List[Dict[str, object]], Dict[str, object]]:
        """Revalidate only a few recent real records; never revive stale facts."""
        cached = references.get("prior_memory_facts", [])
        operation = references.get("prior_memory_operation", {})
        operation = dict(operation) if isinstance(operation, Mapping) else {}
        if not cached and not operation:
            return [], {}
        now = self.now_provider().astimezone()

        def recent(value: Mapping[str, object]) -> bool:
            try:
                observed = datetime.fromisoformat(str(value.get("observed_at", ""))).astimezone()
                age = (now - observed).total_seconds()
                return 0 <= age <= self.state_manager.memory_reference_ttl_seconds
            except (TypeError, ValueError, OSError):
                return False

        try:
            live = {
                int(item["id"]): item
                for item in self.memory_retriever.memory_manager.working_memories()
                if isinstance(item, Mapping) and int(item.get("id", 0) or 0) > 0
            }
        except (OSError, TypeError, ValueError):
            return [], {}
        suppressed = self.state_manager.suppressed_categories(conversation_id)

        def permitted(item: Mapping[str, object]) -> bool:
            category = str(item.get("category", "other"))
            content = str(item.get("content", ""))
            # Do not carry sensitive record bodies into an unrelated next turn.
            # Relevant sensitive queries still use the existing retrieval policy.
            return (
                category not in SENSITIVE_CATEGORIES
                and category not in suppressed
                and not MemoryGovernanceService._is_sensitive(content)
                and item.get("scope") != "temporary_state"
            )

        facts: List[Dict[str, object]] = []
        if isinstance(cached, list):
            for fact in reversed(cached[-5:]):
                if not isinstance(fact, Mapping) or not recent(fact):
                    continue
                try:
                    memory_id = int(fact.get("memory_id", 0) or 0)
                except (TypeError, ValueError):
                    continue
                item = live.get(memory_id)
                if (
                    item is None or not permitted(item)
                    or str(item.get("content", "")) != str(fact.get("content", ""))
                    or str(item.get("created_at", "")) != str(fact.get("created_at", ""))
                ):
                    continue
                facts.append({
                    "memory_id": memory_id,
                    "content": str(item.get("content", "")),
                    "value": str(fact.get("value", "") or item.get("content", "")),
                    "category": str(item.get("category", "other")),
                    "scope": str(item.get("scope", "stable_identity")),
                    "source": str(item.get("source", "formal_memory")),
                    "observed_at": str(fact.get("observed_at", "")),
                })
        if not operation or not recent(operation) or not permitted(operation):
            operation = {}
        elif operation.get("tool") not in {"delete_memory", "archive_memory"}:
            try:
                item = live.get(int(operation.get("memory_id", 0) or 0))
            except (TypeError, ValueError):
                item = None
            if (
                item is None or not permitted(item)
                or str(item.get("content", "")) != str(operation.get("content", ""))
                or str(item.get("created_at", "")) != str(operation.get("created_at", ""))
            ):
                operation = {}
        return facts, operation

    def _remember_verified_memory_result(self, conversation_id: str, result) -> None:
        """Store only actual successful memory ToolResults, in process only."""
        if not result.success or not isinstance(result.data, dict):
            return
        data = result.data
        snapshot = data.get("memory_read", {})
        if result.tool in {"list_memories", "show_memory", "search_memories", "search_memory"}:
            facts = snapshot.get("facts", []) if isinstance(snapshot, Mapping) else []
            if not facts:
                facts = data.get("memories", [])
            if isinstance(facts, list):
                self.state_manager.observe_memory_facts(conversation_id, facts)
            return
        if result.tool not in {"save_formal_memory", "delete_memory", "update_memory", "archive_memory"}:
            return
        operation = data.get("memory_operation", {})
        operation = operation if isinstance(operation, Mapping) else {}
        memory = data.get("memory")
        if not isinstance(memory, Mapping) and result.tool == "save_formal_memory":
            memory_id = operation.get("memory_id")
            found = self.business_resolver.memory_service.get_memory(memory_id)
            memory = found.data.get("memory") if found.success else None
        if not isinstance(memory, Mapping):
            return
        try:
            memory_id = int(memory.get("id", 0) or 0)
        except (TypeError, ValueError):
            return
        if memory_id <= 0:
            return
        if result.tool in {"save_formal_memory", "update_memory"}:
            self.state_manager.observe_memory_facts(conversation_id, [memory])
        self.state_manager.observe_memory_operation(conversation_id, {
            "tool": result.tool, "status": "success", "memory_id": memory_id,
            "content": str(memory.get("content", "")),
            "category": str(memory.get("category", "other")),
            "scope": str(memory.get("scope", "stable_identity")),
            "created_at": str(memory.get("created_at", "")),
            "source": "formal_memory_tool",
            "observed_at": self.now_provider().astimezone().isoformat(timespec="seconds"),
        })

    def _verified_confirmation_pending(self, conversation_id: str) -> Dict[str, object]:
        """Project only executable, typed confirmation facts for reply policy."""
        if not self.interaction_coordinator_enabled:
            return {}
        state = self.interaction_coordinator.current(conversation_id)
        if state.state != "awaiting_confirmation" or not state.pending:
            return {}
        tool_name = str(state.immutable_arguments.get("tool_name", "") or "")
        tool = self._tool_registry().get(tool_name)
        if tool is None or not tool.enabled:
            return {}
        count = 1
        if state.interaction_kind == "plan_batch_completion":
            if tool_name != "complete_plan":
                return {}
            plan_service = getattr(self.business_resolver, "plan_service", None)
            try:
                plans = plan_service.list_plans() if plan_service is not None else []
            except (OSError, TypeError, ValueError):
                return {}
            eligible = {
                str(item.get("uid") or item.get("id"))
                for item in plans
                if not bool(item.get("done"))
                and str(item.get("status", "pending") or "pending") == "pending"
            }
            selected = list(state.selected_object_ids)
            if not selected or len(set(selected)) != len(selected) or not set(selected) <= eligible:
                return {}
            count = len(selected)
        elif state.interaction_kind == "formal_memory_save":
            if tool_name != "save_formal_memory" or not str(state.immutable_arguments.get("content", "") or "").strip():
                return {}
        elif state.interaction_kind == "action_log_offer":
            if tool_name != "add_action_log" or not str(state.immutable_arguments.get("content", "") or "").strip():
                return {}
        else:
            pending = self.agent_core.executor.confirmation_manager.pending(scope=conversation_id)
            if pending is None or str(pending.get("tool", "")) != tool_name:
                return {}
            expected = {
                key: value for key, value in state.immutable_arguments.items()
                if key != "tool_name"
            }
            if dict(pending.get("arguments", {})) != expected:
                return {}
            fingerprint = str(pending.get("state_fingerprint", "") or "")
            if fingerprint:
                executor = self.agent_core.executor
                try:
                    with executor._confirmation_context(tool):
                        _summary, current = executor._confirmation_details(tool, expected)
                except (OSError, TypeError, ValueError, LookupError, RuntimeError):
                    return {}
                if current != fingerprint:
                    return {}
        return {"active": True, "capability": tool.name, "object_count": count}

    def build_llm_messages(
        self,
        user_text: str,
        conversation_id: str,
        *,
        summary_mode: bool = False,
        verified_turn_context: str = "",
        memory_evidence_sink: Optional[List[Dict[str, object]]] = None,
    ) -> List[Dict[str, str]]:
        session_summary = ""
        recent_messages: List[Dict[str, object]] = []
        if self.chat_history_manager is not None and conversation_id:
            recent_messages = self.chat_history_manager.recent_messages(
                conversation_id,
                self.context_builder.recent_message_limit + 1,
            )
            if summary_mode:
                # Summary-to-memory is deliberately based on user-authored
                # turns in this conversation.  Assistant advice, old session
                # summaries, and formal memories are not source facts.
                recent_messages = [
                    item
                    for item in recent_messages
                    if str(item.get("role", "")) == "user"
                ]
            else:
                session_summary = self.chat_history_manager.get_summary(conversation_id)

        suppressed = self.state_manager.suppressed_categories(conversation_id)
        retrieved = [] if summary_mode else self.memory_retriever.retrieve(
            user_text,
            session_summary,
            limit=5,
            update_usage=False,
            suppressed_categories=suppressed,
        )
        if memory_evidence_sink is not None:
            memory_evidence_sink.clear()
            memory_evidence_sink.extend(
                dict(item.get("memory", {}))
                for item in retrieved
                if isinstance(item.get("memory"), dict)
            )
            if not summary_mode:
                prior_facts, _operation = self._verified_recent_memory_context(
                    conversation_id, self.state_manager.reference_context(conversation_id), user_text,
                )
                existing_ids = {item.get("id") for item in memory_evidence_sink}
                memory_evidence_sink.extend(
                    {**fact, "id": fact["memory_id"]}
                    for fact in prior_facts if fact["memory_id"] not in existing_ids
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
        persona_context = ""
        persona_lore_context = ""
        persona_relationship_context = ""
        persona_examples_context = ""
        persona_counts: Dict[str, int] = {}
        active_persona_id = "roxy"
        if self.persona_registry is not None:
            try:
                pack = self.persona_registry.current_pack()
                active_persona_id = pack.persona_id
                persona_context = pack.fixed_core()
                if pack.persona_id == "roxy":
                    legacy_roxy_context = self.personality_context_provider().strip()
                    if legacy_roxy_context:
                        # Keep the prior Roxy configuration readable during the
                        # migration, but never apply it to another active pack.
                        persona_context += "\n\n[Legacy Roxy compatibility]\n" + legacy_roxy_context
                material = pack.retrieve_context(user_text)
                persona_lore_context = material.lore
                persona_relationship_context = material.relationships
                persona_examples_context = material.dialogue_examples
                persona_counts = {
                    "persona_lore": material.lore_items,
                    "persona_relationships": material.relationship_items,
                    "persona_dialogue_examples": material.dialogue_example_items,
                }
            except Exception as error:
                # A bad optional pack must not break a chat turn. The registry has
                # already selected its validated fallback where one is available.
                print(f"[Persona] context unavailable: {type(error).__name__}", flush=True)
        relevant_summary_context = ""
        relevant_summary_count = 0
        relevant_summary_provenance: List[Dict[str, object]] = []
        if self.chat_history_manager is not None and not summary_mode:
            summaries = self.chat_history_manager.relevant_summaries(
                user_text,
                exclude_session_id=conversation_id,
                limit=3,
                char_budget=900,
                persona_id=active_persona_id,
            )
            if summaries:
                relevant_summary_count = len(summaries)
                current_date = self.now_provider().date()
                summary_lines = []
                for item in summaries:
                    raw_time_range = item.get("time_range", {})
                    time_range = (
                        raw_time_range if isinstance(raw_time_range, dict) else {}
                    )
                    source_start = str(time_range.get("start", ""))
                    source_end = str(time_range.get("end", ""))
                    temporal_relation = self._summary_temporal_relation(
                        source_start,
                        source_end,
                        current_date=current_date,
                    )
                    summary_lines.append(
                        "- [session_id={session_id}; source_message_time_start={source_start}; "
                        "source_message_time_end={source_end}; "
                        "relation_to_current_date={temporal_relation}] {summary}".format(
                            session_id=str(item.get("session_id", "")),
                            source_start=source_start,
                            source_end=source_end,
                            temporal_relation=temporal_relation,
                            summary=str(item.get("summary", "")),
                        )
                    )
                    relevant_summary_provenance.append(
                        {
                            "session_id": str(item.get("session_id", "")),
                            "source_message_time_start": source_start,
                            "source_message_time_end": source_end,
                            "summary_refreshed_at": str(item.get("updated_at", "")),
                            "relation_to_current_date": temporal_relation,
                            "source_message_count": int(
                                item.get("source_message_count", 0) or 0
                            ),
                            "trimmed": bool(item.get("trimmed", False)),
                        }
                    )
                relevant_summary_context = (
                    "相关旧会话摘要（仅供大意衔接，不代表精确原话或工具事实）：\n"
                    f"当前本地日期：{current_date.isoformat()}。时间归属只能依据每条摘要的 "
                    "source_message_time_start、source_message_time_end 和 "
                    "relation_to_current_date；摘要生成或刷新时间不是对话发生时间。"
                    "只有 relation_to_current_date 明确为 today 或 yesterday 时，才可称为"
                    "“今天”或“昨天”；unknown、multi_day、earlier 或 future 一律使用"
                    "“之前”“有次”等不带具体日期的说法。\n"
                    + "\n".join(summary_lines)
                )
        provided_sections = self.context_sections_provider()
        if not isinstance(provided_sections, dict):
            provided_sections = {}
        client_runtime = str(provided_sections.get("client_runtime", "") or "")
        if client_runtime and str(provided_sections.get("client_runtime_session_id", "")) != conversation_id:
            client_runtime = "当前会话没有取得客户端状态快照，不能据其他会话的状态推测当前动作。"
        section_counts = dict(persona_counts)
        section_counts["relevant_conversation_summaries"] = relevant_summary_count
        raw_counts = provided_sections.get("item_counts", {})
        if isinstance(raw_counts, dict):
            section_counts.update(raw_counts)
        summary_instruction = (
            "这是当前会话的长期记忆摘要准备步骤。只根据上下文中的用户消息提取用户明确表达并确认过的事实、目标或偏好；不要把助手建议、助手复述、旧会话、正式记忆、今日计划或行动记录写入摘要。输出一至三条简洁的‘请记住：……’候选，并说明用户需要单独发送确认后才会保存。"
            if summary_mode
            else ""
        )
        capability_help_instruction = (
            "\n\n" + DEFAULT_CAPABILITY_REGISTRY.user_help_docs()
            if (
                not summary_mode
                and DEFAULT_CAPABILITY_REGISTRY.is_user_help_query(user_text)
            )
            else ""
        )
        messages = self.context_builder.build(
            personality_context=self.personality_context_provider(),
            persona_context=persona_context,
            memory_context=self.memory_context_provider(memories),
            memory_count=len(memories),
            memory_categories=categories,
            skipped_sensitive_categories=(
                self.memory_retriever.last_skipped_sensitive_categories
            ),
            suppressed_categories=suppressed,
            session_summary=session_summary,
            recent_messages=recent_messages,
            knowledge_context="" if summary_mode else self.knowledge_context_provider(user_text),
            current_user_input=user_text,
            instruction=(
                self.instruction
                + summary_instruction
                + capability_help_instruction
            ),
            verified_turn_context=verified_turn_context,
            client_runtime_context="" if summary_mode else client_runtime,
            user_goals_context="" if summary_mode else str(provided_sections.get("user_goals", "")),
            today_pending_context="" if summary_mode else str(provided_sections.get("today_pending", "")),
            today_completed_context="" if summary_mode else str(provided_sections.get("today_completed", "")),
            action_context="" if summary_mode else str(provided_sections.get("actions", "")),
            relevant_conversation_summaries_context=relevant_summary_context,
            persona_lore_context=persona_lore_context,
            persona_relationship_context=persona_relationship_context,
            persona_examples_context=persona_examples_context,
            section_item_counts=section_counts,
        )
        self.interaction_diagnostics.update(
            conversation_id,
            persona_context={
                "active_persona_id": active_persona_id,
                "persona_version": (
                    str(pack.manifest.get("version", ""))
                    if self.persona_registry is not None and "pack" in locals()
                    else ""
                ),
                "fixed_core_characters": len(persona_context),
                "lore_items": int(persona_counts.get("persona_lore", 0)),
                "dialogue_example_items": int(
                    persona_counts.get("persona_dialogue_examples", 0)
                ),
                "relevant_summary_items": relevant_summary_count,
            },
            relevant_summary_provenance=relevant_summary_provenance,
            context_sections=[
                {
                    "name": item.name,
                    "source": item.source,
                    "characters": item.characters,
                    "item_count": item.item_count,
                    "trimmed": item.trimmed,
                }
                for item in self.context_builder.last_diagnostics
            ],
        )
        return messages

    @staticmethod
    def _summary_temporal_relation(
        source_start: str,
        source_end: str,
        *,
        current_date,
    ) -> str:
        """Classify source-message dates without using summary refresh time."""

        try:
            start_date = datetime.fromisoformat(str(source_start)).date()
            end_date = datetime.fromisoformat(str(source_end)).date()
        except (TypeError, ValueError):
            return "unknown"
        if start_date != end_date:
            return "multi_day"
        if start_date == current_date:
            return "today"
        if start_date == current_date - timedelta(days=1):
            return "yesterday"
        if start_date < current_date:
            return "earlier"
        return "future"

    def finalize_conversation(self, conversation_id: str) -> bool:
        """Persist episodic continuity without moving any working state across windows."""
        if self.chat_history_manager is None or not conversation_id:
            return False
        persona_id = "roxy"
        if self.persona_registry is not None:
            try:
                persona_id = self.persona_registry.current_pack().persona_id
            except Exception:
                pass
        return self.chat_history_manager.finalize_session(
            conversation_id, persona_id=persona_id
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
        state_after = "idle"
        if self.interaction_coordinator_enabled and conversation_id:
            state_after = self.interaction_coordinator.current(conversation_id).state
        provider_response = getattr(self.llm_client, "last_response", None)
        self.interaction_diagnostics.update(
            conversation_id,
            interaction_state_after=state_after,
            response_source=intent,
            provider=str(getattr(provider_response, "provider", "") or ""),
            model=str(getattr(provider_response, "model", "") or ""),
            latency_ms=int(getattr(provider_response, "latency_ms", 0) or 0),
            tool_calls=[
                {
                    "tool": result.tool,
                    "tool_call_id": result.tool_call_id,
                }
                for result in response.tool_results
            ],
            tool_results=[
                self._diagnostic_tool_result(result)
                for result in response.tool_results
            ],
            postcondition_result=[
                {
                    "tool": result.tool,
                    "verified": result.data.get("postcondition_verified"),
                }
                for result in response.tool_results
                if isinstance(result.data, dict)
                and "postcondition_verified" in result.data
            ],
        )
        diagnostic = self.interaction_diagnostics.finalize(
            conversation_id,
            request_id=response.request_id,
            status=response.status,
        )
        if diagnostic is not None:
            print(
                "[Diagnostic] request_id={} route={} state={} tools={} status={}".format(
                    diagnostic.get("request_id", "untracked"),
                    diagnostic.get("route_source", ""),
                    diagnostic.get("interaction_state_after", "idle"),
                    len(diagnostic.get("tool_results", [])),
                    diagnostic.get("status", "failed"),
                ),
                flush=True,
            )
        if conversation_id:
            self.state_manager.observe_assistant(conversation_id, response.message)
            for result in response.tool_results:
                self.state_manager.observe_tool_result(
                    conversation_id, result.to_dict()
                )
                self._remember_verified_memory_result(conversation_id, result)
                self._observe_interaction_tool_result(
                    conversation_id,
                    result,
                    response_message=response.message,
                )
        for result in response.tool_results:
            details = self._diagnostic_tool_result(result)
            data = result.data if isinstance(result.data, dict) else {}
            evidence = {}
            if isinstance(data.get("postcondition_verified"), bool):
                evidence["verified"] = data["postcondition_verified"]
            if result.tool == "save_daily_review" and result.success:
                entry = data.get("entry")
                if isinstance(entry, dict):
                    evidence.update(
                        persisted=True, date=entry.get("date"),
                        record_id=entry.get("uid"), revision=entry.get("revision"),
                    )
                    review = entry.get("review")
                    if isinstance(review, dict):
                        evidence.update({key: review.get(key) for key in ("total", "done", "pending", "action_count")})
            self.development_log.event(
                None, "tool_response_evidence", tool=result.tool,
                tool_call_id=result.tool_call_id, success=result.success,
                status=result.status, reason_code=result.error_code,
                changed_resource_ids=details["changed_resource_ids"],
                memory_id=details["memory_id"],
                operation_kind=result.operation_kind,
                **evidence,
            )
        state = (
            self.interaction_coordinator.current(conversation_id)
            if self.interaction_coordinator_enabled and conversation_id else None
        )
        self.development_log.event(
            None, "turn_service_finished", request_id=response.request_id,
            status=response.status, tool_count=len(response.tool_results),
            state=state.state if state else "unavailable",
            retry_count=state.retry_count if state else 0,
            confirmation_pending=bool(state and state.state == "awaiting_confirmation"),
            read_snapshot_present=bool(state and state.last_read_snapshot),
            suggestion_snapshot_present=bool(state and state.last_suggestion_snapshot),
            response_hash=hashlib.sha256(response.message.encode("utf-8")).hexdigest(),
        )
        if not record_history or self.chat_history_manager is None or not conversation_id:
            return
        trace = self.development_log.current_trace()
        metadata = {"status": response.status}
        if trace and self.development_log.enabled:
            metadata.update(trace.to_metadata())
        saved = self.chat_history_manager.add_message(
            conversation_id,
            "assistant",
            response.message,
            intent=intent or None,
            metadata=metadata,
        )
        self.development_log.event(
            trace, "history_written", success=saved is not None,
            message_id=str(saved.get("id", "")) if saved else "",
            status="success" if saved else "unavailable",
        )
        self._maybe_generate_rule_summary(conversation_id)

    @staticmethod
    def _diagnostic_tool_result(result) -> Dict[str, object]:
        data = result.data if isinstance(result.data, dict) else {}
        memory_operation = data.get("memory_operation", {})
        memory_operation = (
            memory_operation if isinstance(memory_operation, dict) else {}
        )
        formal_data = memory_operation.get("data", {})
        formal_data = formal_data if isinstance(formal_data, dict) else {}
        operation = str(formal_data.get("operation", "") or "")
        audit_action = {
            "created": "create",
            "updated": "update",
            "merged": "update",
            "duplicate": "duplicate",
        }.get(operation, "")
        return {
            "tool": result.tool,
            "tool_call_id": result.tool_call_id,
            "success": result.success,
            "error_code": result.error_code,
            "changed_resource_ids": list(data.get("changed_resource_ids", []))
            if isinstance(data.get("changed_resource_ids", []), list)
            else [],
            "memory_id": memory_operation.get("memory_id"),
            "memory_operation": operation,
            "memory_audit_action": audit_action,
        }

    def diagnostic_snapshot(self) -> List[Dict[str, object]]:
        return self.interaction_diagnostics.snapshot()

    def export_diagnostics(self, path) -> bool:
        return self.interaction_diagnostics.export(path)

    def _update_semantic_diagnostics(
        self,
        conversation_id: str,
        intent_result: Dict[str, object],
        semantic_result,
    ) -> None:
        arguments = intent_result.get("entities", {})
        rejected = str(intent_result.get("pipeline_outcome", "")) == "reject"
        self.development_log.event(
            None, "semantic_decision",
            mode=intent_result.get("mode", ""),
            intent=intent_result.get("intent", "chat"),
            tool=intent_result.get("proposed_tool", ""),
            argument_count=len(arguments) if isinstance(arguments, dict) else 0,
            status="failed" if rejected else "success",
            reason_code="schema_rejected" if rejected else "",
        )
        if not self.interaction_diagnostics.enabled:
            return
        if semantic_result is None:
            self.interaction_diagnostics.update(
                conversation_id,
                route_source=str(intent_result.get("source", "fallback")),
                semantic_parse_source="legacy_intent_router",
            )
            return
        features = semantic_result.local_features
        feature_summary = {}
        if features is not None:
            feature_summary = {
                "domains": list(features.domain_cues),
                "operations": list(features.operation_cues),
                "queries": list(features.query_cues),
                "advice": list(features.advice_cues),
                "negations": list(features.negation_cues),
                "references": list(features.reference_expressions),
                "durations": list(features.duration_expressions),
                "is_question": bool(features.is_question),
                "likely_actionable": bool(features.likely_actionable),
            }
        self.interaction_diagnostics.update(
            conversation_id,
            route_source=str(intent_result.get("source", "fallback")),
            local_features=feature_summary,
            semantic_parse_source=semantic_result.source,
            semantic_decision={
                "mode": str(intent_result.get("mode", "") or ""),
                "intent": str(intent_result.get("intent", "chat") or "chat"),
                "proposed_tool": str(
                    intent_result.get("proposed_tool", "") or ""
                ),
                "follow_up_target": str(
                    intent_result.get("follow_up_target", "") or ""
                ),
                "candidate_action_count": len(
                    intent_result.get("candidate_actions", [])
                    if isinstance(intent_result.get("candidate_actions", []), list)
                    else []
                ),
            },
            validation_notes=[
                item
                for item in str(
                    intent_result.get("semantic_diagnostic", "")
                ).split(";")
                if item
            ],
            pipeline_diagnostics=(
                intent_result.get("pipeline_diagnostics", {})
                if isinstance(intent_result.get("pipeline_diagnostics"), dict)
                else {}
            ),
            pipeline_outcome=str(
                intent_result.get("pipeline_outcome", "")
            ),
            action_candidates=[
                {
                    "action_id": item.action_id,
                    "domain": item.domain,
                    "tool_name": item.tool_name,
                    "request_mode": item.request_mode,
                    "confidence": item.confidence,
                }
                for item in semantic_result.candidates
            ],
            provider=semantic_result.provider,
            model=semantic_result.model,
            latency_ms=semantic_result.latency_ms,
        )

    def _update_resolution_diagnostics(self, conversation_id: str, resolutions) -> None:
        if not self.interaction_diagnostics.enabled:
            return
        selected_ids = []
        policies = []
        for item in resolutions:
            action = item.resolved_action or item.candidate
            for candidate in item.candidate_objects:
                if not isinstance(candidate, dict):
                    continue
                value = candidate.get("uid") or candidate.get("id")
                if value is not None:
                    selected_ids.append(str(value))
            definition = self.agent_core.executor.registry.get(action.tool_name)
            if definition is not None:
                policies.append(
                    {
                        "tool_name": action.tool_name,
                        "policy": definition.confirmation_policy,
                    }
                )
        self.interaction_diagnostics.update(
            conversation_id,
            resolver_result=[
                {
                    "action_id": item.candidate.action_id,
                    "tool_name": item.candidate.tool_name,
                    "status": item.status,
                    "reason_code": item.reason_code,
                }
                for item in resolutions
            ],
            selected_object_ids=selected_ids,
            confirmation_policy=policies,
        )

    def _observe_interaction_tool_result(
        self,
        conversation_id: str,
        result,
        *,
        response_message: str = "",
    ) -> None:
        if not result.success:
            return
        data = result.data if isinstance(result.data, dict) else {}
        self._mark_typed_memory_use(result, response_message)
        if not self.interaction_coordinator_enabled:
            return
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

    def _mark_chat_memory_use(
        self,
        evidence: List[Dict[str, object]],
        reply: str,
    ) -> None:
        if not evidence:
            return
        manager = self.memory_retriever.memory_manager
        normalized_reply = manager.normalize_text(reply)
        used_ids = []
        for memory in evidence:
            raw_id = memory.get("id")
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            content = str(memory.get("content", "") or "").strip()
            core = manager.semantic_core(content)
            if len(core) >= 3 and core in normalized_reply:
                used_ids.append(memory_id)
        if used_ids:
            manager.mark_used(used_ids)

    def _mark_typed_memory_use(self, result, reply: str) -> None:
        if result.tool not in {"list_memories", "show_memory"}:
            return
        call_id = str(result.tool_call_id or "").strip()
        if call_id and call_id in self._marked_memory_tool_calls:
            return
        data = result.data if isinstance(result.data, dict) else {}
        memory_read = data.get("memory_read", {})
        memory_read = memory_read if isinstance(memory_read, dict) else {}
        facts = memory_read.get("facts", [])
        if not isinstance(facts, list):
            return
        manager = self.memory_retriever.memory_manager
        if "natural_answer_memory_ids" in data:
            verified_natural_ids = data.get("natural_answer_memory_ids", [])
            verified_natural_ids = (
                verified_natural_ids
                if isinstance(verified_natural_ids, list)
                else []
            )
            allowed_ids = set()
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                try:
                    allowed_ids.add(int(fact.get("memory_id")))
                except (TypeError, ValueError):
                    continue
            used_ids = []
            for raw_id in verified_natural_ids:
                try:
                    memory_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if memory_id in allowed_ids and memory_id not in used_ids:
                    used_ids.append(memory_id)
            if used_ids:
                manager.mark_used(used_ids)
            if call_id:
                self._marked_memory_tool_calls.add(call_id)
            return
        normalized_reply = manager.normalize_text(reply)
        used_ids = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            raw_id = fact.get("memory_id")
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            value = manager.normalize_text(str(fact.get("value", "") or ""))
            content_core = manager.semantic_core(
                str(fact.get("content", "") or "")
            )
            if (
                (len(value) >= 2 and value in normalized_reply)
                or (len(content_core) >= 3 and content_core in normalized_reply)
            ):
                used_ids.append(memory_id)
        if used_ids:
            manager.mark_used(used_ids)
        if call_id:
            self._marked_memory_tool_calls.add(call_id)
            if len(self._marked_memory_tool_calls) > 256:
                self._marked_memory_tool_calls = set(
                    list(self._marked_memory_tool_calls)[-128:]
                )

    def _chat(self, messages: List[Dict[str, str]], request_id: str) -> str:
        self.development_log.event(None, "model_request_started", request_id=request_id)
        try:
            try:
                from modules.llm.routed_client import RoutedLLMClient
            except ImportError:
                RoutedLLMClient = None
            if RoutedLLMClient is not None and isinstance(self.llm_client, RoutedLLMClient):
                reply = self.llm_client.chat(
                    messages, route_context={"request_id": request_id},
                )
            else:
                reply = self.llm_client.chat(messages)
        except Exception as error:
            self.development_log.record_exception(None, error)
            self.development_log.event(
                None, "model_request_finished", request_id=request_id,
                status="failed", reason_code="model_error",
            )
            raise
        self.development_log.event(
            None, "model_request_finished", request_id=request_id, status="success",
            response_hash=hashlib.sha256(str(reply).encode("utf-8")).hexdigest(),
        )
        return reply

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
        if self.finalize_conversation(conversation_id):
            print("[Summary] fallback", flush=True)

    def _capture_memory_candidate(self, turn: ConversationTurn) -> None:
        if not self.enable_memory_candidates or self.memory_governance is None:
            return
        try:
            result = self.memory_governance.propose_from_text(
                turn.message,
                explicit=False,
                source="conversation",
                source_role="user",
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
    def _is_exhaustive_memory_query(user_text: str) -> bool:
        compact = re.sub(r"[\s，,。.!！?？；;：:]", "", str(user_text or ""))
        if any(term in compact for term in ("全部", "所有", "完整", "逐条")):
            return True
        return bool(
            re.fullmatch(
                r"(?:请)?(?:查看|列出|展示)(?:我的)?(?:正式)?(?:长期)?记忆",
                compact,
            )
            or re.fullmatch(r"(?:我的)?正式长期记忆", compact)
        )

    @staticmethod
    def _parse_memory_answer_payload(raw: object) -> Optional[Dict[str, object]]:
        value = str(raw or "").strip()
        if value.startswith("```"):
            value = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                value,
                flags=re.IGNORECASE,
            )
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        answer = sanitize_public_reply(str(parsed.get("answer", "") or ""))
        raw_ids = parsed.get("used_memory_ids", [])
        if not answer or not isinstance(raw_ids, list):
            return None
        used_ids: List[int] = []
        for raw_id in raw_ids:
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                return None
            if memory_id not in used_ids:
                used_ids.append(memory_id)
        return {"answer": answer, "used_memory_ids": used_ids}

    def _memory_answer_mentions_fact(
        self,
        answer: str,
        fact: Mapping[str, object],
    ) -> bool:
        manager = self.memory_retriever.memory_manager
        answer_normalized = manager.normalize_text(answer)
        answer_core = manager.semantic_core(answer)
        raw_values = [
            str(fact.get("value", "") or "").strip(),
            str(fact.get("content", "") or "").strip(),
        ]
        content = raw_values[-1]
        if content:
            raw_values.append(ResponseComposer._memory_from_roxy_perspective(content))
        for value in raw_values:
            normalized = manager.normalize_text(value)
            if len(normalized) >= 2 and normalized in answer_normalized:
                return True
            core = manager.semantic_core(value)
            core = re.sub(r"^(?:我的|你的|我|你)", "", core)
            if len(core) >= 4 and core in answer_core:
                return True
        fact_tags = {
            tag
            for value in raw_values
            for tag in manager.extract_tags(value)
            if len(tag) >= 2
            and tag not in {"喜欢", "记得", "知道", "现在", "目前", "长期", "目标"}
        }
        answer_tags = set(manager.extract_tags(answer))
        overlap = fact_tags & answer_tags
        return any(len(tag) >= 4 for tag in overlap) or len(overlap) >= 2

    def _validated_memory_answer(
        self,
        payload: Optional[Mapping[str, object]],
        facts_by_id: Mapping[int, Mapping[str, object]],
        *,
        required_ids: set[int],
    ) -> tuple[str, List[int], str]:
        if not isinstance(payload, Mapping):
            return "", [], "invalid_json"
        answer = str(payload.get("answer", "") or "").strip()
        raw_ids = payload.get("used_memory_ids", [])
        used_ids = [int(item) for item in raw_ids] if isinstance(raw_ids, list) else []
        available_ids = set(facts_by_id)
        if any(marker in answer for marker in ("memory_id", "ToolResult", "query_mode", "attribute")):
            return "", [], "internal_terms"
        if not available_ids:
            if used_ids:
                return "", [], "unknown_memory_id"
            if not re.search(r"(?:还没|没有|未找到|没找到|暂时没有|不记得)", answer):
                return "", [], "empty_state_not_grounded"
            return answer, [], ""
        if not used_ids:
            return "", [], "missing_memory_ids"
        if not set(used_ids).issubset(available_ids):
            return "", [], "unknown_memory_id"
        if not required_ids.issubset(set(used_ids)):
            return "", [], "incomplete_memory_coverage"
        for memory_id in used_ids:
            if not self._memory_answer_mentions_fact(answer, facts_by_id[memory_id]):
                return "", [], "ungrounded_memory_reference"
        return answer, used_ids, ""

    def _maybe_generate_formal_memory_answer(
        self,
        response: AgentResponse,
        user_text: str,
        conversation_id: str,
    ) -> None:
        """Let the model phrase verified formal-memory facts, never select data."""

        result = next(
            (
                item
                for item in response.tool_results
                if item.success
                and str(item.tool)
                in {"list_memories", "show_memory", "search_memories", "search_memory"}
            ),
            None,
        )
        if result is None or str(result.data.get("natural_answer", "") or "").strip():
            return
        memory_read = result.data.get("memory_read", {})
        memory_read = memory_read if isinstance(memory_read, Mapping) else {}
        request = memory_read.get("request", {})
        request = request if isinstance(request, Mapping) else {}
        raw_facts = memory_read.get("facts", [])
        raw_facts = raw_facts if isinstance(raw_facts, list) else []
        facts_by_id: Dict[int, Dict[str, object]] = {}
        prompt_facts: List[Dict[str, object]] = []
        for item in raw_facts:
            if not isinstance(item, Mapping):
                continue
            try:
                memory_id = int(item.get("memory_id"))
            except (TypeError, ValueError):
                continue
            fact = dict(item)
            facts_by_id[memory_id] = fact
            prompt_facts.append(
                {
                    "memory_id": memory_id,
                    "content": str(fact.get("content", "") or ""),
                    "value": str(fact.get("value", "") or ""),
                    "category": str(fact.get("category", "") or ""),
                    "scope": str(fact.get("scope", "") or ""),
                }
            )
        exhaustive = self._is_exhaustive_memory_query(user_text)
        query_mode = str(request.get("query_mode", "overview") or "overview")
        required_ids = (
            set(facts_by_id)
            if exhaustive or query_mode in {"attribute", "existence", "provenance"}
            else set()
        )
        verified = json.dumps(
            {
                "request": {
                    "query_mode": query_mode,
                    "attribute": str(request.get("attribute", "") or ""),
                    "topic": str(request.get("topic", "") or ""),
                    "exhaustive": exhaustive,
                },
                "facts": prompt_facts,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        base_prompt = (
            "你正在根据本轮工具刚刚核验过的正式长期记忆回答用户。"
            "下面 JSON 是本轮唯一允许使用的长期记忆事实；不得补充、猜测或引用其他记忆。"
            "结合用户当前问题和对话语气自然回答，像熟悉用户的成长伙伴，不要说‘检索到记录’、"
            "‘正式长期记忆里找到’、字段名、内部 ID、工具或数据库。"
            "具体属性或是非问题直接回答，不要输出管理回执。宽泛了解问题可以综合全部事实并自主组织详略，"
            "但所有事实都已经提供给你，不存在三四条输入上限。"
            "如果 exhaustive=true，回答必须逐项覆盖 facts 中每条事实，不得省略；如果 facts 为空，"
            "自然说明目前还没有相应记忆。只能输出 JSON，格式为"
            '{"answer":"面向用户的自然纯文本","used_memory_ids":[1,2]}。'
            "used_memory_ids 只能填写回答中确实表达过的事实 ID，不得显示在 answer 中。\n"
            f"已核验正式记忆 JSON：{verified}"
        )
        request_id = str(response.request_id or uuid4().hex)
        failure_reason = ""
        try:
            base_messages = self.build_llm_messages(user_text, conversation_id)
            for attempt in range(2):
                system_prompt = base_prompt
                if attempt and failure_reason:
                    system_prompt += (
                        "\n上一次回答未通过本地事实校验，原因是："
                        f"{failure_reason}。请重新输出完整合法 JSON。"
                    )
                messages = list(base_messages)
                insert_at = len(messages)
                for index in range(len(messages) - 1, -1, -1):
                    if messages[index].get("role") == "user":
                        insert_at = index
                        break
                messages.insert(insert_at, {"role": "system", "content": system_prompt})
                raw = self._chat(messages, f"{request_id}:memory_answer:{attempt + 1}")
                payload = self._parse_memory_answer_payload(raw)
                answer, used_ids, failure_reason = self._validated_memory_answer(
                    payload,
                    facts_by_id,
                    required_ids=required_ids,
                )
                if answer:
                    result.data["natural_answer"] = answer
                    result.data["natural_answer_memory_ids"] = used_ids
                    self.development_log.event(
                        None,
                        "memory_answer_generated",
                        status="success",
                        memory_count=len(facts_by_id),
                        used_count=len(used_ids),
                        exhaustive=exhaustive,
                        retry_count=attempt,
                    )
                    return
        except Exception as error:
            failure_reason = "model_error"
            self.development_log.record_exception(None, error)
        self.development_log.event(
            None,
            "memory_answer_generated",
            status="fallback",
            reason_code=failure_reason or "invalid_model_answer",
            memory_count=len(facts_by_id),
            exhaustive=exhaustive,
        )

    def _maybe_generate_daily_review_summary(
        self,
        response: AgentResponse,
        user_text: str,
        conversation_id: str,
    ) -> None:
        """Ask the LLM to phrase verified review facts, with a deterministic fallback."""
        review_result = next(
            (
                result
                for result in response.tool_results
                if str(result.tool) == "generate_daily_review" and bool(result.success)
            ),
            None,
        )
        if review_result is None:
            return
        review = review_result.data.get("review", {})
        if not isinstance(review, dict):
            return
        if str(review.get("natural_summary", "")).strip():
            return
        try:
            messages = self.build_llm_messages(user_text, conversation_id)
            verified_facts = {
                key: review.get(key)
                for key in (
                    "date",
                    "total",
                    "done",
                    "pending",
                    "action_count",
                    "completed_tasks",
                    "pending_tasks",
                    "actions",
                )
            }
            facts = json.dumps(
                verified_facts,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            system_prompt = (
                "你正在把一份已经由程序读取并核验过的今日复盘事实，写成洛琪希风格的自然回复。"
                "只能使用下面 JSON 中的日期、数量、计划、行动和状态；不得补写不存在的完成事项、记忆、健康事实或执行结果。"
                "用户问‘今天完成了什么’时，可以自然理解为‘今天状态怎么样’，但不要输出数据库式编号清单。"
                "结合上下文中已经提供的用户目标、偏好和近期状态调整侧重点，但不得把这些背景说成今天新完成的事实。"
                "先用一两句陪伴式观察，再提到关键进展和未完成部分；没有数据时也要如实说明。"
                "可少量使用动作描写，但不要固定套模板。只输出面向用户的纯文本。\n"
                f"已核验的今日复盘 JSON：{facts}"
            )
            insert_at = len(messages)
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    insert_at = index
                    break
            messages.insert(insert_at, {"role": "system", "content": system_prompt})
            reply = sanitize_public_reply(self._chat(messages, f"{response.request_id}:daily_review"))
            if reply and not self._is_llm_unavailable_message(reply):
                review["natural_summary"] = reply
        except Exception as error:
            print(f"[DailyReview] natural summary unavailable: {type(error).__name__}", flush=True)

    def _maybe_generate_growth_log_summary(
        self,
        response: AgentResponse,
        user_text: str,
        conversation_id: str,
    ) -> None:
        """Phrase a requested monthly summary from verified tool data only."""
        if not re.search(r"总结|概括|分析|回顾", str(user_text or "")):
            return
        growth_result = next(
            (
                result
                for result in response.tool_results
                if str(result.tool) == "show_growth_log" and bool(result.success)
            ),
            None,
        )
        if growth_result is None or str(
            growth_result.data.get("natural_summary", "")
        ).strip():
            return
        statistics = growth_result.data.get("statistics", {})
        entries = growth_result.data.get("entries", [])
        if not isinstance(statistics, dict) or not isinstance(entries, list):
            return
        verified_days = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            review = entry.get("review", {})
            review = review if isinstance(review, dict) else {}
            verified_days.append(
                {
                    "date": entry.get("date", ""),
                    "total": review.get("total", 0),
                    "done": review.get("done", 0),
                    "pending": review.get("pending", 0),
                    "completed_tasks": review.get("completed_tasks", []),
                    "pending_tasks": review.get("pending_tasks", []),
                    "actions": review.get("actions", []),
                }
            )
        facts = json.dumps(
            {"statistics": statistics, "days": verified_days},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            messages = self.build_llm_messages(user_text, conversation_id)
            system_prompt = (
                "你正在总结一份由本地程序核验过的自然月成长记录。"
                "只能使用下面 JSON 中的月份、数量、日期、计划标题、完成状态和计划外行动；"
                "不得补写未出现的事实，不得把长期记忆说成本月新完成事项，也不得声称执行了任何操作。"
                "先概括整体进展，再指出一项真实亮点和一项可继续关注的未完成内容；"
                "数据为空时明确说明本月还没有成长日志。只输出自然纯文本。\n"
                f"已核验的自然月成长 JSON：{facts}"
            )
            insert_at = len(messages)
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    insert_at = index
                    break
            messages.insert(insert_at, {"role": "system", "content": system_prompt})
            reply = sanitize_public_reply(
                self._chat(messages, f"{response.request_id}:growth_month")
            )
            if reply and not self._is_llm_unavailable_message(reply):
                growth_result.data["natural_summary"] = reply
        except Exception as error:
            print(
                f"[GrowthMonth] natural summary unavailable: {type(error).__name__}",
                flush=True,
            )

    @staticmethod
    def _is_llm_unavailable_message(reply: str) -> bool:
        text = str(reply or "").strip()
        if not text:
            return True
        return any(
            marker in text
            for marker in (
                "模型服务当前不可用",
                "模型服务暂时不可用",
                "在线模型和本地模型当前都不可用",
                "在线模型不可用",
                "没有返回有效结果",
                "请稍后再试",
                "这次没有得到可显示的结果",
                "暂时没有得到可显示的回答",
                "请换一种说法",
                "模型接口尚未配置",
                "config.json",
            )
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
