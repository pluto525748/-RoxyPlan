from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, Optional
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlsplit
from uuid import uuid4

from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner
from modules.chat_history_manager import ChatHistoryManager
from modules.confirmation_manager import ConfirmationManager
from modules.context_builder import ContextBuilder
from modules.conversation_service import ConversationService, DEFAULT_CHAT_INSTRUCTION
from modules.business_resolver import BusinessResolver
from modules.contracts import AgentResponse, ToolResult
from modules.growth_manager import GrowthManager
from modules.feature_flags import validate_feature_flags
from modules.intent_router import IntentRouter, LLMIntentParser
from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.knowledge_manager import KnowledgeManager
from modules.local_feature_extractor import LocalFeatureExtractor
from modules.llm_client import LLMClient, default_config, load_llm_config
from modules.llm.routed_client import RoutedLLMClient
from modules.model_action_adapter import ModelActionAdapter
from modules.memory_manager import MemoryManager
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_retriever import MemoryRetriever
from modules.memory_service import MemoryOperationResult, MemoryService
from modules.plan_service import PlanService
from modules.persona_registry import PersonaRegistry
from modules.response_composer import ResponseComposer
from modules.semantic_action_parser import SemanticActionParser
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHAT_INSTRUCTION = DEFAULT_CHAT_INSTRUCTION


class AgentService:
    """Thin, Qt-free adapter around the existing local Agent and LLM modules."""

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        growth_manager: Optional[GrowthManager] = None,
        memory_manager: Optional[MemoryManager] = None,
        memory_candidate_manager: Optional[MemoryCandidateManager] = None,
        memory_service: Optional[MemoryService] = None,
        chat_history_manager: Optional[ChatHistoryManager] = None,
        knowledge_manager: Optional[KnowledgeManager] = None,
        persona_registry: Optional[PersonaRegistry] = None,
        llm_client=None,
        status_probe: Optional[Callable[[LLMClient], bool]] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.pet_settings = self._load_pet_settings_read_only()
        for warning in validate_feature_flags(self.pet_settings):
            print(f"[FeatureFlags] warning={warning}", flush=True)
        self.growth_manager = growth_manager or GrowthManager(
            self.project_root / "data" / "private"
        )
        if memory_service is None:
            if memory_manager is None and memory_candidate_manager is None:
                memory_service = MemoryService.from_data_root(
                    self.project_root,
                    default_data=self._load_memory_example(),
                    candidates_enabled=bool(
                        self.pet_settings.get("enable_memory_candidates", False)
                    ),
                )
            else:
                if memory_manager is not None:
                    resolved_manager = memory_manager
                else:
                    repository = memory_candidate_manager.repository
                    resolved_manager = MemoryManager(
                        getattr(repository, "memory_file"),
                        backup_dir=getattr(repository, "backup_dir"),
                        conflict_file=getattr(repository, "conflict_file"),
                        audit_file=getattr(repository, "audit_file", None),
                        default_data=self._load_memory_example(),
                        repository=repository,
                    )
                memory_service = MemoryService(
                    resolved_manager,
                    memory_candidate_manager,
                    candidates_enabled=bool(
                        self.pet_settings.get("enable_memory_candidates", False)
                    ),
                )
        self.memory_service = memory_service
        self.memory_manager = memory_service.memory_manager
        self.memory_candidate_manager = memory_service.candidate_manager
        self.memory_governance = memory_service.governance
        self.llm_client = llm_client or RoutedLLMClient(
            self.project_root,
            settings=self.pet_settings,
            legacy_config=self._load_llm_config_read_only(),
        )
        self.status_probe = status_probe or self._probe_ollama
        self.personality = self._load_personality()
        self.persona_registry = persona_registry or PersonaRegistry(
            self._persona_root(),
            active_persona_id=str(self.pet_settings.get("active_persona_id", "roxy")),
        )
        self.memory_retriever = MemoryRetriever(self.memory_manager)
        self.context_builder = ContextBuilder(recent_message_limit=16)
        self.chat_history_manager = chat_history_manager or ChatHistoryManager(
            self.project_root / "data" / "private"
        )
        self.knowledge_manager = knowledge_manager or KnowledgeManager(
            self.project_root / "data" / "knowledge"
        )

        self.intent_router = IntentRouter(
            LLMIntentParser(self.llm_client.chat),
            enable_llm=isinstance(self.llm_client, RoutedLLMClient),
        )
        self.tool_registry = create_roxy_tool_registry(
            self.growth_manager,
            self.memory_manager,
            pet_controller=None,
            memory_governance=self.memory_governance,
            chat_history_manager=self.chat_history_manager,
            memory_service=self.memory_service,
            plan_postcondition_enabled=bool(
                self.pet_settings.get("plan_postcondition_enabled", True)
            ),
        )
        confirmation_ttl_seconds = int(
            self.pet_settings.get("interaction_confirmation_ttl_seconds", 180)
        )
        self.confirmation_manager = ConfirmationManager(
            ttl_seconds=confirmation_ttl_seconds
        )
        self.safety_policy = SafetyPolicy(
            medium_confidence_threshold=0.82,
            always_confirm_high_risk=True,
        )
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            self.safety_policy,
            self.confirmation_manager,
        )
        self.agent_planner = AgentPlanner(
            self.tool_registry,
            max_steps=3,
            enable_multi_step=True,
            enable_llm=False,
        )
        self.agent_core = AgentCore(
            self.agent_planner,
            self.tool_executor,
            plan_postcondition_enabled=bool(
                self.pet_settings.get("plan_postcondition_enabled", True)
            ),
        )
        self.interaction_coordinator = InteractionStateCoordinator(
            ttl_seconds=int(self.pet_settings.get("interaction_ttl_seconds", 300)),
            continuation_ttl_seconds=int(
                self.pet_settings.get("interaction_continuation_ttl_seconds", 1200)
            ),
            confirmation_ttl_seconds=confirmation_ttl_seconds,
        )
        self.local_feature_extractor = LocalFeatureExtractor()
        self.model_action_proposal_adapter = ModelActionAdapter(self.tool_registry)
        self.semantic_action_parser = SemanticActionParser(
            self.intent_router,
            feature_extractor=self.local_feature_extractor,
            proposal_adapter=self.model_action_proposal_adapter,
            enabled=bool(
                self.pet_settings.get("unified_semantic_parser_enabled", True)
            ),
            max_actions=3,
        )
        self.plan_service = PlanService(self.growth_manager)
        self.business_resolver = BusinessResolver(
            plan_service=self.plan_service,
            memory_service=self.memory_service,
            interaction_coordinator=self.interaction_coordinator,
            tool_registry=self.tool_registry,
            enabled=bool(self.pet_settings.get("business_resolver_enabled", True)),
        )
        self.response_composer = ResponseComposer(
            enabled=bool(
                self.pet_settings.get("deterministic_response_enabled", True)
            )
        )
        # The unified semantic parser owns natural-language tool decisions.
        # Do not construct the legacy JSON/native ModelToolCallLoop here.
        self.model_action_adapter = None
        self.conversation_service = ConversationService(
            intent_router=self.intent_router,
            agent_core=self.agent_core,
            llm_client=self.llm_client,
            memory_retriever=self.memory_retriever,
            context_builder=self.context_builder,
            chat_history_manager=self.chat_history_manager,
            personality_context_provider=self._build_personality_context,
            persona_registry=self.persona_registry,
            context_sections_provider=self._build_context_sections,
            memory_context_provider=self._build_memory_context,
            knowledge_context_provider=self.knowledge_manager.build_context,
            memory_governance=self.memory_governance,
            enable_memory_candidates=bool(
                self.pet_settings.get("enable_memory_candidates", False)
            ),
            instruction=CHAT_INSTRUCTION,
            model_action_adapter=self.model_action_adapter,
            interaction_coordinator=self.interaction_coordinator,
            semantic_action_parser=self.semantic_action_parser,
            business_resolver=self.business_resolver,
            response_composer=self.response_composer,
            interaction_coordinator_enabled=bool(
                self.pet_settings.get("interaction_coordinator_enabled", True)
            ),
            unified_semantic_parser_enabled=bool(
                self.pet_settings.get("unified_semantic_parser_enabled", True)
            ),
            business_resolver_enabled=bool(
                self.pet_settings.get("business_resolver_enabled", True)
            ),
            deterministic_response_enabled=bool(
                self.pet_settings.get("deterministic_response_enabled", True)
            ),
            action_batch_enabled=bool(
                self.pet_settings.get("action_batch_enabled", True)
            ),
            action_preview_enabled=bool(
                self.pet_settings.get("action_preview_enabled", True)
            ),
            legacy_intent_path_enabled=bool(
                self.pet_settings.get("legacy_intent_path_enabled", False)
            ),
            semantic_decision_compatibility_enabled=not isinstance(
                self.llm_client, RoutedLLMClient
            ),
            interaction_diagnostics_enabled=bool(
                self.pet_settings.get("interaction_diagnostics_enabled", False)
            ),
            interaction_diagnostics_path=(
                self.project_root / "logs" / "interaction_diagnostics.jsonl"
            ),
        )
        self._request_lock = Lock()

    def handle(self, message: str, conversation_id: str) -> AgentResponse:
        """Adapt one HTTP request to the shared, Qt-free conversation pipeline."""
        clean_message = str(message).strip()
        if not clean_message:
            raise ValueError("message cannot be empty")

        with self._request_lock:
            return self.conversation_service.handle(
                clean_message,
                conversation_id,
                record_history=True,
            )

    def diagnostic_snapshot(self):
        return self.conversation_service.diagnostic_snapshot()

    def export_diagnostics(self, path) -> bool:
        return self.conversation_service.export_diagnostics(path)

    def execute_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
    ) -> ToolResult:
        with self._request_lock:
            return self.tool_executor.execute(
                tool_name,
                arguments or {},
                confidence=1.0,
            )

    def confirm_tool(self, confirmation_id: str) -> ToolResult:
        with self._request_lock:
            return self.tool_executor.execute_confirmed(str(confirmation_id))

    def memory_view(self, query: str = "") -> ToolResult:
        clean_query = str(query).strip()
        if clean_query:
            result = self.execute_tool("search_memory", {"query": clean_query})
        else:
            result = self.execute_tool("show_memory")

        allowed_fields = {
            "id",
            "content",
            "category",
            "importance",
            "confidence",
            "source",
            "created_at",
            "updated_at",
            "last_used",
            "status",
            "tags",
        }
        memories = result.data.get("memories", []) if result.success else []
        result.data["memories"] = [
            {
                key: item[key]
                for key in allowed_fields
                if key in item
            }
            for item in memories
            if isinstance(item, dict)
        ]
        return result

    def memory_candidates_view(self) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.list_candidates(status="pending")
            candidates = operation.data.get("candidates", []) if operation.success else []
        allowed = {
            "id", "category", "content", "source", "source_text", "created_at",
            "confidence", "sensitivity", "status", "duplicate_of",
            "conflict_with", "reason",
        }
        return {
            "candidates": [
                {key: item[key] for key in allowed if key in item}
                for item in candidates
                if isinstance(item, dict)
            ]
        }

    def accept_memory_candidate(
        self,
        candidate_id: int,
        *,
        edited_content: Optional[str] = None,
    ) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.accept_candidate(
                int(candidate_id),
                edited_content=edited_content,
                source="web_confirmation",
            )
        return self._legacy_memory_operation(operation)

    def reject_memory_candidate(self, candidate_id: int) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.reject_candidate(
                int(candidate_id),
                source="web_confirmation",
            )
        return self._legacy_memory_operation(operation)

    def reject_low_value_candidates(self) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.reject_low_value_candidates()
        return self._legacy_memory_operation(operation)

    def memory_conflicts_view(self) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.list_conflicts(status="pending")
            conflicts = operation.data.get("conflicts", []) if operation.success else []
        return {"conflicts": [self._conflict_view(item) for item in conflicts]}

    def resolve_memory_conflict(
        self,
        conflict_id: int,
        resolution: str,
        *,
        merged_content: Optional[str] = None,
    ) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.resolve_conflict(
                int(conflict_id),
                resolution,
                merged_content=merged_content,
                source="web_confirmation",
            )
        return self._legacy_memory_operation(operation)

    def memory_audit_view(self, limit: int = 100) -> Dict[str, object]:
        with self._request_lock:
            operation = self.memory_service.list_audit(limit=limit)
            entries = operation.data.get("entries", []) if operation.success else []
        return {
            "entries": [
                {
                    "id": item.get("id"),
                    "action": item.get("action"),
                    "memory_id": item.get("memory_id"),
                    "old_value": self._audit_snapshot(item.get("old_value")),
                    "new_value": self._audit_snapshot(item.get("new_value")),
                    "timestamp": item.get("timestamp"),
                    "source": item.get("source"),
                }
                for item in entries
                if isinstance(item, dict)
            ]
        }

    def status(self) -> Dict[str, object]:
        if isinstance(self.llm_client, RoutedLLMClient):
            model_status = self.llm_client.provider_status()
            return {
                "service": "ok",
                "ollama": str(model_status["ollama"].get("status", "offline")),
                "model": str(model_status.get("current_model", "")) or "未配置",
                "knowledge_files": self.knowledge_manager.file_count(),
            }
        provider = str(getattr(self.llm_client, "provider", "")).strip().lower()
        model = str(getattr(self.llm_client, "model", "")).strip()
        ollama_status = "not_configured"
        if provider == "ollama":
            try:
                ollama_status = "online" if self.status_probe(self.llm_client) else "offline"
            except Exception as error:
                print(
                    f"[LocalWeb] status probe failed: {type(error).__name__}",
                    flush=True,
                )
                ollama_status = "offline"
        return {
            "service": "ok",
            "ollama": ollama_status,
            "model": model or "未配置",
            "knowledge_files": self.knowledge_manager.file_count(),
        }

    def model_status(self) -> Dict[str, object]:
        if isinstance(self.llm_client, RoutedLLMClient):
            model_status = self.llm_client.provider_status()
            return {
                "provider": str(model_status.get("current_provider", "")),
                "model": str(model_status.get("current_model", "")),
                "model_mode": str(model_status.get("mode", "auto")),
                "fallback_active": bool(model_status.get("fallback_active", False)),
                "deepseek": model_status.get("deepseek", {}),
                "ollama": model_status.get("ollama", {}),
                "api_key_configured": bool(
                    model_status.get("api_key", {}).get("configured", False)
                ),
                "last_route_reason": str(model_status.get("last_route_reason", "")),
            }
        return {
            "provider": str(getattr(self.llm_client, "provider", "")),
            "model": str(getattr(self.llm_client, "model", "")),
            "model_mode": "legacy",
            "fallback_active": False,
            "deepseek": {"status": "not_configured", "configured": False},
            "ollama": {"status": self.status().get("ollama", "not_configured")},
            "api_key_configured": False,
            "last_route_reason": "",
        }

    def model_usage(self) -> Dict[str, object]:
        if isinstance(self.llm_client, RoutedLLMClient):
            return self.llm_client.usage_summary()
        return {"updated_at": "", "totals": {}, "recent": []}

    def clear_model_usage(self) -> bool:
        return bool(
            isinstance(self.llm_client, RoutedLLMClient)
            and self.llm_client.clear_usage()
        )

    def list_web_sessions(self):
        return [
            self._session_summary(item)
            for item in self.chat_history_manager.sessions()
            if self._is_web_session(str(item.get("session_id", "")))
        ]

    def create_web_session(self, title: str = "新对话"):
        session = self.chat_history_manager.new_session(
            title=title,
            session_id=f"web_{uuid4().hex}",
        )
        return self._session_summary(session)

    def get_web_session(self, session_id: str):
        if not self._is_web_session(session_id):
            return None
        session = self.chat_history_manager.get_session(session_id)
        if session is None:
            return None
        return {
            **self._session_summary(session),
            "summary": self.chat_history_manager.get_summary(session_id),
            "messages": [
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                    "created_at": str(message.get("created_at", "")),
                }
                for message in self.chat_history_manager.messages(session_id)
                if isinstance(message, dict)
            ],
        }

    def rename_web_session(self, session_id: str, title: str):
        if not self._is_web_session(session_id):
            return None
        session = self.chat_history_manager.rename_session(session_id, title)
        return self._session_summary(session) if session is not None else None

    def _build_chat_messages(
        self,
        user_text: str,
        conversation_id: str = "local_default",
    ):
        return self.conversation_service.build_llm_messages(
            user_text,
            conversation_id,
        )

    def _load_personality(self) -> Dict[str, object]:
        fallback: Dict[str, object] = {
            "version": 1,
            "name": "Roxy",
            "personality": "成长陪伴桌宠，温和、克制、专注于帮助用户持续成长。",
            "likes": "",
            "speaking_style": "简洁、温柔、直接。",
        }
        path = self.project_root / "data" / "roxy_personality.json"
        if not path.exists():
            return fallback
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return fallback
        if not isinstance(loaded, dict):
            return fallback
        result = dict(fallback)
        result.update(loaded)
        return result

    def _build_personality_context(self) -> str:
        return "\n".join(
            [
                "人格配置：",
                f"名称：{self.personality.get('name') or '未设置'}",
                f"性格：{self.personality.get('personality') or '未设置'}",
                f"喜好：{self.personality.get('likes') or '未设置'}",
                f"说话风格：{self.personality.get('speaking_style') or '未设置'}",
            ]
        )

    def available_personas(self):
        """Shared desktop/Web capability: list validated, data-only packs."""
        return self.persona_registry.available_personas()

    def _persona_root(self) -> Path:
        preferred = self.project_root / "data" / "personas"
        if preferred.is_dir():
            return preferred
        # Test application-data roots intentionally omit immutable application
        # resources. Use the installed/source pack without falling back to any
        # production user-data path.
        return PROJECT_ROOT / "data" / "personas"

    def _build_context_sections(self) -> Dict[str, object]:
        plans = self.plan_service.list_plans()
        pending = [item for item in plans if not item.get("done") and item.get("status") != "cancelled"]
        completed = [item for item in plans if item.get("done") or item.get("status") == "completed"]
        actions = self.plan_service.action_records()
        memories = self.memory_manager.working_memories()
        goals = [
            str(item.get("content", "")).strip()
            for item in memories
            if isinstance(item, dict)
            and str(item.get("content", "")).strip()
            and str(item.get("scope", "")) != "temporary_state"
            and (
                str(item.get("category", "")).lower() in {"goal", "long_term_goal"}
                or "长期目标" in str(item.get("content", ""))
            )
        ][:3]
        return {
            "user_goals": "重要长期目标：\n" + "\n".join(f"- {item}" for item in goals) if goals else "",
            "today_pending": "今日未完成计划：\n" + "\n".join(
                f"- {str(item.get('title', '')).strip()}" for item in pending[:8] if str(item.get("title", "")).strip()
            ) if pending else "",
            "today_completed": "今日已完成摘要：\n" + "\n".join(
                f"- {str(item.get('title', '')).strip()}" for item in completed[:6] if str(item.get("title", "")).strip()
            ) if completed else "",
            "actions": "今日行动记录：\n" + "\n".join(
                f"- {str(item.get('content', '')).strip()}" for item in actions[:6] if str(item.get("content", "")).strip()
            ) if actions else "",
            "item_counts": {
                "user_important_goals": len(goals),
                "today_unfinished_plan": len(pending),
                "today_completed_summary": len(completed),
                "today_action_records": len(actions),
            },
        }

    def _build_memory_context(self, memories) -> str:
        nickname = self.memory_manager.preferred_name()
        lines = ["长期记忆：", f"用户昵称：{nickname or '未设置'}"]
        contents = [
            (
                str(item.get("content", "")).strip(),
                str(item.get("scope", "stable_identity")),
                str(item.get("location", "") or "").strip(),
            )
            for item in memories
            if isinstance(item, dict)
            and str(item.get("content", "")).strip()
            and str(item.get("scope", "")) != "temporary_state"
        ]
        if contents:
            lines.append("记忆内容：")
            lines.append(
                "优先级：当前对话明确事实 > current_state > 有效计划 > "
                "stable_identity > historical_state > future_intent。"
            )
            lines.extend(
                f"- [scope={scope}{', location=' + location if location else ''}] {content}"
                for content, scope, location in contents
            )
        else:
            lines.append("记忆内容：暂无")
        return "\n".join(lines)

    def _load_memory_example(self) -> Dict[str, object]:
        fallback: Dict[str, object] = {
            "version": 2,
            "profile": {},
            "memories": [],
        }
        path = self.project_root / "memory.example.json"
        if not path.exists():
            return fallback
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback
        return loaded if isinstance(loaded, dict) else fallback

    def _load_llm_config_read_only(self) -> Dict[str, str]:
        path = self.project_root / "config.json"
        if not path.exists():
            return default_config()
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_config()
        if not isinstance(loaded, dict):
            return default_config()
        return load_llm_config(path)

    def _load_pet_settings_read_only(self) -> Dict[str, object]:
        fallback: Dict[str, object] = {"enable_memory_candidates": False}
        path = self.project_root / "data" / "pet_config.json"
        if not path.exists():
            return fallback
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback
        if isinstance(loaded, dict):
            fallback.update(loaded)
        return fallback

    @staticmethod
    def _is_web_session(session_id: str) -> bool:
        value = str(session_id).strip()
        return value == "local_default" or value.startswith("web_")

    @staticmethod
    def _legacy_memory_operation(
        operation: MemoryOperationResult,
    ) -> Dict[str, object]:
        payload = dict(operation.data)
        payload.setdefault(
            "status",
            {
                "success": "added",
                "not_found": "missing",
                "validation_error": "invalid",
                "already_processed": "unchanged",
                "conflict": "conflict",
            }.get(operation.status, "error"),
        )
        payload["safe_message"] = operation.safe_message
        return payload

    @staticmethod
    def _session_summary(session: Dict[str, object]) -> Dict[str, object]:
        return {
            "session_id": str(session.get("session_id", "")),
            "title": str(session.get("title", "新对话")),
            "started_at": str(session.get("started_at", "")),
            "updated_at": str(session.get("updated_at", "")),
            "message_count": int(session.get("message_count", 0) or 0),
        }

    @classmethod
    def _conflict_view(cls, item: Dict[str, object]) -> Dict[str, object]:
        return {
            "id": item.get("id"),
            "old_memory_id": item.get("old_memory_id"),
            "old_memory": cls._memory_snapshot(item.get("old_memory")),
            "new_content": str(item.get("new_content", "")),
            "category": str(item.get("category", "other")),
            "relation": str(item.get("relation", "conflict")),
            "source": item.get("source"),
            "created_at": item.get("created_at"),
            "status": item.get("status"),
        }

    @staticmethod
    def _memory_snapshot(value):
        if value is None:
            return None
        if isinstance(value, dict):
            allowed = {"id", "content", "category", "importance", "confidence", "status"}
            return {key: value[key] for key in allowed if key in value}
        return str(value)

    @staticmethod
    def _audit_snapshot(value):
        if value is None:
            return None
        if not isinstance(value, dict):
            return {"content_redacted": True}
        allowed = {"id", "category", "importance", "confidence", "status"}
        snapshot = {key: value[key] for key in allowed if key in value}
        if "content" in value:
            snapshot["content_redacted"] = True
            snapshot["content_length"] = len(str(value.get("content", "")))
        return snapshot

    @staticmethod
    def _probe_ollama(client: LLMClient) -> bool:
        base_url = str(getattr(client, "base_url", "")).strip()
        if not base_url:
            return False
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        health_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
        probe = url_request.Request(health_url, method="GET")
        try:
            with url_request.urlopen(probe, timeout=1.0) as response:
                return 200 <= int(response.status) < 300
        except (url_error.URLError, TimeoutError, OSError):
            return False
