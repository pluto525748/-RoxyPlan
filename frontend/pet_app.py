from __future__ import annotations

import html
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.llm_client import LLMClient, load_llm_config, default_config
from modules.llm.routed_client import RoutedLLMClient
from modules.llm.secret_store import SecretStore
from modules.llm.response_sanitizer import sanitize_public_reply
from modules.llm.usage_store import ModelUsageStore
from modules.model_action_adapter import ModelActionAdapter
from modules.agent_core import AgentCore
from modules.agent_planner import AgentPlanner, LLMPlanner
from modules.business_resolver import BusinessResolver
from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.chat_history_manager import ChatHistoryManager
from modules.chinese_entity_parser import ChineseEntityParser
from modules.development_log import configure_development_log, get_development_log
from modules.confirmation_manager import ConfirmationManager
from modules.context_builder import ContextBuilder
from modules.contracts import AgentResponse, ClientAction
from modules.client_action_claim_guard import ClientActionClaimGuard
from modules.client_action_result import ClientActionResult
from modules.conversation_service import ConversationService, ConversationTurn
from modules.growth import GrowthService
from modules.growth_manager import GrowthManager
from modules.learning_loop import LearningLoopService
from modules.feature_flags import validate_feature_flags
from modules.intent_router import IntentRouter, LLMIntentParser, match_plan_task
from modules.interaction_state_coordinator import InteractionStateCoordinator
from modules.knowledge_manager import KnowledgeManager
from modules.local_feature_extractor import LocalFeatureExtractor
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever
from modules.memory_service import MemoryService
from modules.plan_service import PlanService
from modules.persona_registry import PersonaRegistry
from modules.response_composer import ResponseComposer
from modules.simulation_clock import SimulatedBusinessClock
from modules.runtime_paths import (
    RuntimePaths, ensure_windowed_streams, initialize_runtime, read_json_strict,
    report_startup_failure, resolve_runtime, show_startup_error, write_run_metadata,
)
from modules.semantic_action_parser import SemanticActionParser
from modules.safety_policy import SafetyPolicy
from modules.tool_executor import ToolExecutor
from modules.tool_registry import create_roxy_tool_registry
from modules.today_plan import TodayPlanStore
from frontend.desktop_pet import DesktopPet, DEFAULT_CONFIG, DEFAULT_TIPS
from frontend.window_chrome import install_titlebar_policy
from frontend.chat_history_dialog import ChatHistoryDialog, session_display_title
from frontend.chat_widgets import ChatDecoration, ChatTexturePanel, ChatTranscript
from frontend.first_use_hint import FirstUseHint
from frontend.growth_dialog import GrowthDialog
from frontend.today_growth_dialog import TodayGrowthDialog
from frontend.usage_dialog import UsageDialog
from frontend.interaction_menu import InteractionMenu
from frontend.memory_dialog import CATEGORY_LABELS, MemoryDialog
from frontend.settings_dialog import (
    DEFAULT_SETTINGS, SettingsDialog, first_use_hint_seen, load_pet_settings,
    mark_first_use_hint_seen, save_pet_settings,
)


DEFAULT_MEMORY_FILE = PROJECT_ROOT / "memory.json"
MEMORY_FILE = DEFAULT_MEMORY_FILE
MEMORY_EXAMPLE_FILE = PROJECT_ROOT / "memory.example.json"
PERSONALITY_FILE = PROJECT_ROOT / "data" / "roxy_personality.json"
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
CONFIG_FILE = PROJECT_ROOT / "config.json"
SUPPORTED_KNOWLEDGE_EXTENSIONS = {".txt", ".md"}
IGNORED_KNOWLEDGE_FILE_NAMES = {"readme.md"}
MAX_KNOWLEDGE_FILE_CHARS = 12000
MAX_KNOWLEDGE_SNIPPET_CHARS = 900
MAX_KNOWLEDGE_MATCHES = 3


def default_memory() -> Dict[str, Any]:
    fallback = {
        "version": 1,
        "profile": {},
        "memories": [],
    }
    if not MEMORY_EXAMPLE_FILE.exists():
        return fallback

    try:
        with MEMORY_EXAMPLE_FILE.open("r", encoding="utf-8") as file:
            example = json.load(file)
    except (json.JSONDecodeError, OSError):
        return fallback

    if not isinstance(example, dict):
        return fallback

    example.setdefault("version", 1)
    example.setdefault("profile", {})
    example.setdefault("memories", [])
    return example


def default_personality() -> Dict[str, Any]:
    return {
        "version": 1,
        "name": "",
        "personality": "",
        "likes": "",
        "speaking_style": "",
        "rules": [],
        "fallback_reply": "你好，{nickname}。",
    }


def clean_nickname(nickname: str) -> str:
    return nickname.strip(" \t\r\n。.!！?？：:")


def extract_nickname(user_text: str) -> str:
    # 支持“我叫煜”“我的名字叫煜”“叫我煜”等常见昵称表达。
    text = user_text.strip()
    patterns = (
        r"^我叫\s*(.+)$",
        r"^我的名字叫\s*(.+)$",
        r"^我的昵称是\s*(.+)$",
        r"^叫我\s*(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return clean_nickname(match.group(1))
    return clean_nickname(text)


class ChatReplyWorker(QObject):
    finished = Signal(object, int)

    def __init__(
        self,
        conversation_service: ConversationService,
        turn: ConversationTurn,
        turn_sequence: int,
    ) -> None:
        super().__init__()
        self.conversation_service = conversation_service
        self.turn = turn
        self.turn_sequence = int(turn_sequence)

    def run(self) -> None:
        try:
            response = self.conversation_service.complete(self.turn)
        except Exception as error:
            print(f"[Conversation] request failed: {type(error).__name__}", flush=True)
            response = AgentResponse(
                "failed", "这次请求处理失败，没有把它当作成功。请检查记录后再试。",
                request_id=self.turn.request_id or None,
                conversation_id=self.turn.conversation_id,
            )
        self.finished.emit(response, self.turn_sequence)


class ChatWindow(QMainWindow):
    """V1.0 chat window with local memory, knowledge, and natural growth intents."""

    def __init__(
        self,
        pet_controller: Optional[Any] = None,
        plan_store: Optional[TodayPlanStore] = None,
        growth_service: Optional[Any] = None,
        memory_candidate_manager: Optional[MemoryCandidateManager] = None,
        chat_history_manager: Optional[ChatHistoryManager] = None,
        memory_manager: Optional[MemoryManager] = None,
        memory_service: Optional[MemoryService] = None,
        development_log=None,
        llm_client: Optional[Any] = None,
        pet_settings: Optional[Dict[str, Any]] = None,
        settings_config_file: Optional[Path] = None,
        personality_file: Optional[Path] = None,
        knowledge_dir: Optional[Path] = None,
        legacy_config_file: Optional[Path] = None,
        simulation_clock: Optional[SimulatedBusinessClock] = None,
        runtime: Optional[RuntimePaths] = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime or getattr(pet_controller, "runtime", None)
        if self.runtime is not None and self.runtime.is_beta and simulation_clock is not None:
            raise ValueError("Beta cannot use a simulated business clock")
        if self.runtime is not None and self.runtime.is_packaged and any(
            service is None for service in
            (growth_service, memory_service, chat_history_manager, llm_client)
        ):
            raise ValueError("Beta chat requires the shared, path-injected services")
        self.development_log = development_log or get_development_log()
        self.settings_config_file = Path(
            settings_config_file
            or PROJECT_ROOT / "data" / "pet_config.json"
        )
        self.personality_file = Path(personality_file or PERSONALITY_FILE)
        self.knowledge_dir = Path(knowledge_dir or KNOWLEDGE_DIR)
        self.legacy_config_file = Path(legacy_config_file or CONFIG_FILE)
        self.simulation_clock = simulation_clock
        self._submitted_trace = None
        self._active_reply_trace = None
        self._client_runtime_snapshot: Dict[str, object] = {}
        self.pet_controller = pet_controller
        # Closing hides this retained object; the worker and its origin stay alive.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        if pet_controller is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._quit_requested = False
        self.client_action_claim_guard = ClientActionClaimGuard()
        self.reply_thread: Optional[QThread] = None
        self.reply_worker: Optional[ChatReplyWorker] = None
        self._pending_conversation_turn: Optional[ConversationTurn] = None
        self._message_sequence = 0
        self._active_reply_sequence = 0
        self._active_reply_request_id = ""
        self._active_reply_business_date = ""
        self._last_visible_assistant_message = ""
        self.settings_dialog: Optional[SettingsDialog] = None
        self.growth_dialog: Optional[GrowthDialog] = None
        self.today_growth_dialog: Optional[TodayGrowthDialog] = None
        self.usage_dialog: Optional[UsageDialog] = None
        self.memory_dialog: Optional[MemoryDialog] = None
        self.chat_history_dialog: Optional[ChatHistoryDialog] = None
        self._clear_history_confirmation_pending = False
        self._pending_memory_delete_id: Optional[int] = None
        self._pending_plan_delete_id: Optional[int] = None
        self._generating_summary = False
        self._memory_temp_dir = None
        if memory_service is not None:
            self.memory_manager = memory_service.memory_manager
        elif memory_manager is not None:
            self.memory_manager = memory_manager
        else:
            memory_path = MEMORY_FILE
            is_default_path = memory_path.resolve() == DEFAULT_MEMORY_FILE.resolve()
            if (
                os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"
                and is_default_path
            ):
                self._memory_temp_dir = tempfile.TemporaryDirectory()
                memory_path = Path(self._memory_temp_dir.name) / "memory.json"
            if memory_path.resolve() == DEFAULT_MEMORY_FILE.resolve():
                private_dir = PROJECT_ROOT / "data" / "private"
            else:
                private_dir = memory_path.parent / "private"
            memory_service = MemoryService.from_data_root(
                PROJECT_ROOT,
                memory_file=memory_path,
                private_dir=private_dir,
                default_data=default_memory(),
            )
            self.memory_manager = memory_service.memory_manager
        self.memory = self.memory_manager.data
        self.memory_retriever = MemoryRetriever(self.memory_manager)
        self.personality = self.load_personality()
        self.knowledge_manager = KnowledgeManager(
            self.knowledge_dir,
            max_file_chars=MAX_KNOWLEDGE_FILE_CHARS,
            max_snippet_chars=MAX_KNOWLEDGE_SNIPPET_CHARS,
            max_matches=MAX_KNOWLEDGE_MATCHES,
        )
        self.knowledge_files = self.scan_knowledge_files()
        shared_growth_service = getattr(pet_controller, "growth_service", None)
        if growth_service is not None or shared_growth_service is not None:
            self.growth_service = growth_service or shared_growth_service
        elif plan_store is not None:
            self.growth_service = GrowthService(plan_store=plan_store)
        else:
            self.growth_service = GrowthManager()
        self.growth_manager = self.growth_service
        self.plan_store = self.growth_service.plan_store
        shared_candidate_manager = getattr(
            pet_controller, "memory_candidate_manager", None
        )
        self.memory_candidate_manager = (
            (memory_service.candidate_manager if memory_service is not None else None)
            or memory_candidate_manager
            or shared_candidate_manager
            or MemoryCandidateManager(repository=self.memory_manager.repository)
        )
        self.pet_settings = (
            dict(pet_settings)
            if pet_settings is not None
            else load_pet_settings(self.settings_config_file)
        )
        if self.runtime is not None:
            self.pet_settings = self.runtime.effective_settings(self.pet_settings)
        self.persona_registry = PersonaRegistry(
            (self.runtime.resource_root if self.runtime else PROJECT_ROOT) / "data" / "personas",
            active_persona_id=str(self.pet_settings.get("active_persona_id", "roxy")),
        )
        for warning in validate_feature_flags(self.pet_settings):
            print(f"[FeatureFlags] warning={warning}", flush=True)
        self.memory_service = memory_service or MemoryService(
            self.memory_manager,
            self.memory_candidate_manager,
            candidates_enabled=bool(
                self.pet_settings.get("enable_memory_candidates", False)
            ),
        )
        self.memory_service.set_candidates_enabled(
            bool(self.pet_settings.get("enable_memory_candidates", False))
        )
        self.memory_manager = self.memory_service.memory_manager
        self.memory_candidate_manager = self.memory_service.candidate_manager
        self.memory_governance = self.memory_service.governance
        if self.pet_controller is not None:
            self.pet_controller.memory_service = self.memory_service
            self.pet_controller.memory_candidate_manager = self.memory_candidate_manager
        shared_history_manager = getattr(pet_controller, "chat_history_manager", None)
        self._chat_history_temp_dir = None
        if chat_history_manager is not None or shared_history_manager is not None:
            self.chat_history_manager = chat_history_manager or shared_history_manager
        elif os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self._chat_history_temp_dir = tempfile.TemporaryDirectory()
            self.chat_history_manager = ChatHistoryManager(
                Path(self._chat_history_temp_dir.name) / "private",
                enabled=bool(self.pet_settings.get("chat_history_enabled", True)),
            )
        else:
            self.chat_history_manager = ChatHistoryManager(
                enabled=bool(self.pet_settings.get("chat_history_enabled", True))
            )
        self.chat_history_manager.set_enabled(
            bool(self.pet_settings.get("chat_history_enabled", True))
        )
        self.context_builder = ContextBuilder(
            int(self.pet_settings.get("recent_context_messages", 16))
        )
        self._daily_startup_session_id = ""
        self._session_startup_error = ""
        self._viewing_history = False
        self._first_use_hint_shown_this_run = False
        self.current_session_id = ""
        self._select_today_session()
        llm_config = self._load_legacy_config()
        legacy_config_empty = not any(
            llm_config.get(key) for key in ("provider", "base_url", "model")
        )
        model_name = str(self.pet_settings.get("model_name", "")).strip()
        if model_name:
            llm_config["model"] = model_name
        use_legacy_offscreen_client = (
            os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"
            and legacy_config_empty
        )
        self.llm_client = llm_client or (
            LLMClient(llm_config)
            if use_legacy_offscreen_client
            else RoutedLLMClient(
                PROJECT_ROOT,
                settings=self.pet_settings,
                legacy_config=llm_config,
            )
        )
        self.business_entity_parser = ChineseEntityParser(
            now_provider=self.growth_service.now_provider
        )
        self.intent_router = IntentRouter(
            LLMIntentParser(self.llm_client.chat),
            enable_llm=isinstance(self.llm_client, RoutedLLMClient),
            entity_parser=self.business_entity_parser,
        )
        self.tool_registry = create_roxy_tool_registry(
            self.growth_service,
            self.memory_manager,
            self.pet_controller,
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
            medium_confidence_threshold=float(
                self.pet_settings.get("agent_medium_confidence", 0.82)
            ),
            always_confirm_high_risk=bool(
                self.pet_settings.get("agent_confirm_high_risk", True)
            ),
        )
        self.tool_executor = ToolExecutor(
            self.tool_registry, self.safety_policy, self.confirmation_manager
        )
        self.agent_planner = AgentPlanner(
            self.tool_registry,
            max_steps=int(self.pet_settings.get("agent_max_steps", 3)),
            enable_multi_step=bool(
                self.pet_settings.get("agent_multi_step_enabled", True)
            ),
            llm_planner=LLMPlanner(self.llm_client.chat),
            enable_llm=bool(
                self.pet_settings.get("llm_planner_assist_enabled", False)
            ),
        )
        self.agent_core = AgentCore(
            self.agent_planner,
            self.tool_executor,
            enabled=bool(self.pet_settings.get("agent_core_enabled", True)),
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
        self.local_feature_extractor = LocalFeatureExtractor(
            self.business_entity_parser
        )
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
        self.plan_service = PlanService(self.growth_service)
        self.learning_loop_service = (
            LearningLoopService(
                self.growth_service, now_provider=self.growth_service.now_provider,
            )
            if all(
                callable(getattr(self.growth_service, name, None))
                for name in ("pending_before", "tasks", "records_for_date", "entries")
            ) else None
        )
        self.business_resolver = BusinessResolver(
            plan_service=self.plan_service,
            memory_service=self.memory_service,
            interaction_coordinator=self.interaction_coordinator,
            tool_registry=self.tool_registry,
            enabled=bool(self.pet_settings.get("business_resolver_enabled", True)),
            entity_parser=self.business_entity_parser,
        )
        self.response_composer = ResponseComposer(
            enabled=bool(
                self.pet_settings.get("deterministic_response_enabled", True)
            ),
            now_provider=self.growth_service.now_provider,
        )
        # ModelActionAdapter remains the proposal/schema adapter used by the
        # unified semantic parser.  The old ModelToolCallLoop is intentionally
        # not constructed here: a completed reply must not start a second
        # natural-language tool decision.
        self.model_action_adapter = None
        self.conversation_service = ConversationService(
            intent_router=self.intent_router,
            agent_core=self.agent_core,
            llm_client=self.llm_client,
            memory_retriever=self.memory_retriever,
            context_builder=self.context_builder,
            chat_history_manager=self.chat_history_manager,
            personality_context_provider=self.build_personality_context,
            persona_registry=self.persona_registry,
            context_sections_provider=self.build_context_sections,
            learning_loop_service=self.learning_loop_service,
            memory_context_provider=self.build_memory_context,
            knowledge_context_provider=self.build_knowledge_context,
            memory_governance=self.memory_governance,
            enable_memory_candidates=bool(
                self.pet_settings.get("enable_memory_candidates", False)
            ),
            summary_message_threshold=int(
                self.pet_settings.get("summary_message_threshold", 30)
            ),
            summary_character_threshold=int(
                self.pet_settings.get("summary_character_threshold", 12000)
            ),
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
            development_log=self.development_log,
            interaction_diagnostics_path=(
                PROJECT_ROOT / "logs" / "interaction_diagnostics.jsonl"
                if Path(self.memory_manager.memory_file).resolve()
                == DEFAULT_MEMORY_FILE.resolve()
                else Path(self.memory_manager.memory_file).parent
                / "logs"
                / "interaction_diagnostics.jsonl"
            ),
            now_provider=(getattr(self.growth_service, "timestamp_provider", None)
                          or self.growth_service.now_provider),
            business_now_provider=self.growth_service.now_provider,
        )

        self.setWindowTitle("RoxyPlan · 洛琪希")
        self.resize(460, 560)
        self.setMinimumSize(360, 420)

        container = QWidget()
        container.setObjectName("chatRoot")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        session_layout = QHBoxLayout()
        session_layout.setSpacing(7)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_label = QLabel("洛琪希")
        title_label.setObjectName("chatTitle")
        self.session_title_label = QLabel("魔法修行日记")
        self.session_title_label.setObjectName("sessionTitle")
        self.session_title_label.setWordWrap(True)
        title_layout.addWidget(title_label)
        title_layout.addWidget(self.session_title_label)
        self.header_decoration = ChatDecoration("roxy_cat_header.png")
        self.header_decoration.setFixedSize(112, 63)
        self.return_today_button = QPushButton("返回今天")
        self.return_today_button.setObjectName("sessionButton")
        self.return_today_button.clicked.connect(self.return_today_session)
        self.return_today_button.hide()
        self.history_button = QPushButton("历史")
        self.history_button.setObjectName("sessionButton")
        self.history_button.clicked.connect(self.open_chat_history_dialog)
        session_layout.addLayout(title_layout, 1)
        session_layout.addWidget(self.header_decoration)
        session_layout.addWidget(self.return_today_button)
        session_layout.addWidget(self.history_button)
        self.simulation_date_label = None
        self.advance_day_button = None
        if self.simulation_clock is not None:
            self.simulation_date_label = QLabel()
            self.simulation_date_label.setObjectName("simulationDate")
            if self.runtime is not None and self.runtime.is_study:
                self.simulation_date_label.hide()
            self.advance_day_button = QPushButton("推进一天")
            self.advance_day_button.setObjectName("sessionButton")
            self.advance_day_button.clicked.connect(self.advance_simulation_day)
            self._update_simulation_date_label()

        message_panel = ChatTexturePanel()
        message_panel.setObjectName("chatMessagePanel")
        message_layout = QVBoxLayout(message_panel)
        message_layout.setContentsMargins(1, 1, 1, 1)
        self.transcript = ChatTranscript()
        self.transcript.setPlaceholderText("聊天记录会出现在这里。")
        self.transcript.setObjectName("chatTranscript")
        message_layout.addWidget(self.transcript)

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("和洛琪希说点什么吧...")
        self.input_box.setObjectName("chatInput")
        self.input_box.returnPressed.connect(self.send_message)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_message)

        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self.open_settings_dialog)

        self.growth_button = QPushButton("成长")
        self.growth_button.setObjectName("growthButton")
        self.growth_button.clicked.connect(self.open_today_growth_dialog)

        self.interaction_button = InteractionMenu(self)
        self.interaction_menu = self.interaction_button.menu()
        self.dance_action = self.interaction_button.action_items["dance"]
        self.encourage_action = self.interaction_button.action_items["encourage"]
        self.interaction_button.action_selected.connect(
            lambda action: self.start_dance() if action == "dance" else self.encourage()
        )
        for action in (self.dance_action, self.encourage_action):
            action.setEnabled(self.pet_controller is not None)
        self.interaction_menu.addSeparator()
        self.usage_action = self.interaction_menu.addAction("操作说明")
        self.usage_action.triggered.connect(self.open_usage_dialog)
        self.interaction_button.setMenu(self.interaction_menu)

        self.memory_button = QPushButton("记忆")
        self.memory_button.setObjectName("memoryButton")
        self.memory_button.clicked.connect(self.open_memory_dialog)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.footer_decoration = ChatDecoration("lantern_footer.png")
        self.footer_decoration.setFixedSize(48, 36)
        input_layout.addWidget(self.footer_decoration)
        input_layout.addWidget(self.growth_button)
        input_layout.addWidget(self.memory_button)
        input_layout.addWidget(self.settings_button)
        input_layout.addWidget(self.interaction_button)
        input_layout.addStretch(1)
        input_layout.addWidget(self.send_button)

        # 首次打开聊天时的轻量提示：可关闭、非模态，只由 showEvent 决定是否出现。
        self.first_use_hint = FirstUseHint()
        self.first_use_hint.hide()
        self.first_use_hint.example_requested.connect(
            self._open_examples_from_first_use_hint
        )
        self.first_use_hint.dismissed.connect(self._dismiss_first_use_hint)

        layout.addLayout(session_layout)
        if self.simulation_clock is not None:
            simulation_layout = QHBoxLayout()
            simulation_layout.addWidget(self.simulation_date_label)
            simulation_layout.addStretch(1)
            simulation_layout.addWidget(self.advance_day_button)
            layout.addLayout(simulation_layout)
        layout.addWidget(self.first_use_hint)
        layout.addWidget(message_panel, 1)
        layout.addWidget(self.input_box)
        layout.addLayout(input_layout)

        self.setCentralWidget(container)
        self.apply_chat_style()
        self.restore_current_session()
        self.log_knowledge_summary()
        QTimer.singleShot(0, self.move_to_bottom_right)

    def apply_chat_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#chatRoot {
                background: #FAF8F2;
                color: #333333;
                font-family: "Microsoft YaHei";
            }
            QTextEdit#chatTranscript {
                background: transparent;
                border: none;
                padding: 2px;
                color: #35415A;
                font-family: "Microsoft YaHei";
                font-size: 13px;
                selection-background-color: #C8D8EF;
                selection-color: #263550;
            }
            QLabel#chatTitle {
                color: #475777;
                font-size: 20px;
                font-weight: 600;
            }
            QLabel#sessionTitle {
                color: #59658F;
                font-family: "Microsoft YaHei";
                font-size: 11px;
            }
            QLineEdit#chatInput {
                background: #FFFFFF;
                border: 1px solid #D8CCFF;
                border-radius: 12px;
                padding: 9px 12px;
                color: #333333;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }
            QLineEdit#chatInput:focus {
                border: 1px solid #8EA7FF;
            }
            QLineEdit#chatInput:disabled {
                background: #F2F3F8;
                color: #9AA3BB;
            }
            QPushButton#sendButton {
                background: #829CCD;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 8px 18px;
                font-family: "Microsoft YaHei";
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#sendButton:hover {
                background: #7894F8;
            }
            QPushButton#sendButton:pressed {
                background: #6F88EA;
            }
            QPushButton#sendButton:disabled {
                background: #C9D1E4;
                color: #F8F9FC;
            }
            QPushButton#settingsButton, QPushButton#growthButton, QPushButton#memoryButton, QPushButton#sessionButton, QPushButton#interactionButton {
                background: #FFFFFF;
                color: #6677B8;
                border: 1px solid #D8CCFF;
                border-radius: 9px;
                padding: 7px 10px;
                font-family: "Microsoft YaHei";
                font-size: 13px;
            }
            QPushButton#settingsButton:hover, QPushButton#growthButton:hover, QPushButton#memoryButton:hover, QPushButton#sessionButton:hover, QPushButton#interactionButton:hover {
                background: #F1EAFE;
            }
            QMenu { background: #FFFDFA; color: #475777; border: 1px solid #DADDED; padding: 4px; }
            QMenu::item { padding: 7px 14px; }
            QMenu::item:selected { background: #EAF0FA; }
            QMenu::item:disabled { color: #9CA4B5; }
            QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
            QScrollBar::handle:vertical { background: #CFD7E7; border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            """
        )

    def open_settings_dialog(self) -> None:
        if self.runtime is not None and self.runtime.is_packaged:
            read_json_strict(self.settings_config_file)
        if self.pet_controller is not None:
            self.pet_controller.record_interaction()

        if self.settings_dialog is None:
            from modules.feedback_export import feedback_export_for_runtime
            self.settings_dialog = SettingsDialog(
                self,
                config_file=self.settings_config_file,
                chat_history_manager=self.chat_history_manager,
                secret_store=getattr(self.llm_client, "secret_store", None),
                usage_store=getattr(self.llm_client, "usage_store", None),
                knowledge_dir=self.knowledge_dir,
                feedback_export=feedback_export_for_runtime(self.runtime),
            )
            self.settings_dialog.settings_saved.connect(self.apply_chat_settings)
            self.settings_dialog.history_cleared.connect(
                self.handle_history_cleared_from_settings
            )
            for name, handler in (("usage_help_requested", self.open_usage_dialog),
                                  ("quit_requested", self.request_quit)):
                signal = getattr(self.settings_dialog, name, None)
                if signal is not None:
                    signal.connect(handler)
            if self.pet_controller is not None:
                self.settings_dialog.settings_saved.connect(
                    self.pet_controller.apply_saved_settings
                )
                self.settings_dialog.open_today_growth_requested.connect(
                    self.pet_controller.open_today_growth_dialog
                )
            else:
                self.settings_dialog.open_today_growth_button.setEnabled(False)
                self.settings_dialog.open_today_growth_button.setToolTip(
                    "请从桌宠设置中打开今日成长面板。"
                )
            self.settings_dialog.finished.connect(self._clear_settings_dialog)

        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _clear_settings_dialog(self) -> None:
        self.settings_dialog = None

    def open_usage_dialog(self) -> None:
        self.record_pet_interaction()
        if self.pet_controller is not None:
            self.pet_controller.open_usage_dialog()
            return
        if self.usage_dialog is None:
            self.usage_dialog = UsageDialog(self)
            signal = getattr(self.usage_dialog, "example_selected", None)
            if signal is not None:
                signal.connect(self.select_usage_example)
                ready = getattr(self.usage_dialog, "set_chat_destination_ready", None)
                if callable(ready):
                    ready()
        self.usage_dialog.show()
        self.usage_dialog.raise_()
        self.usage_dialog.activateWindow()

    def select_usage_example(self, text: str) -> None:
        if self._quit_requested or not text.strip():
            return
        if self.input_box.text() and QMessageBox.question(
            self, "替换输入草稿？", "输入框已有内容。是否替换为这条示例？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._viewing_history:
            self.return_today_session()
        self.show()
        self.input_box.setText(text)
        self.input_box.setFocus()

    def start_dance(self) -> None:
        if self.pet_controller is not None:
            self.pet_controller.start_dance()

    def encourage(self) -> None:
        if self.pet_controller is not None:
            self.pet_controller.encourage()

    def request_quit(self) -> None:
        self._quit_requested = True
        if self.reply_thread is not None:
            # Existing completion saves/presents the response before thread cleanup.
            # Never destroy or terminate a worker that is executing business logic.
            self.input_box.setEnabled(False)
            self.send_button.setEnabled(False)
            self.setWindowTitle("RoxyPlan · 等待当前回复保存后退出")
            return
        QApplication.quit()

    def apply_chat_settings(self, config: Dict[str, Any]) -> None:
        history_was_enabled = self.chat_history_manager.enabled
        self.pet_settings.update(config)
        if self.runtime is not None:
            self.pet_settings = self.runtime.effective_settings(self.pet_settings)
        if isinstance(self.llm_client, RoutedLLMClient):
            self.llm_client.reconfigure(
                self.pet_settings,
                legacy_config=self._load_legacy_config(),
            )
        self.chat_history_manager.set_enabled(
            bool(self.pet_settings.get("chat_history_enabled", True))
        )
        if history_was_enabled != self.chat_history_manager.enabled:
            self._select_today_session()
        self.context_builder.set_recent_message_limit(
            int(self.pet_settings.get("recent_context_messages", 16))
        )
        self.persona_registry.select(
            str(self.pet_settings.get("active_persona_id", "roxy"))
        )
        self.intent_router.configure_llm(
            LLMIntentParser(self.llm_client.chat),
            isinstance(self.llm_client, RoutedLLMClient),
        )
        self.agent_core.enabled = bool(
            self.pet_settings.get("agent_core_enabled", True)
        )
        self.agent_planner.configure(
            max_steps=int(self.pet_settings.get("agent_max_steps", 3)),
            enable_multi_step=bool(
                self.pet_settings.get("agent_multi_step_enabled", True)
            ),
            enable_llm=bool(
                self.pet_settings.get("llm_planner_assist_enabled", False)
            ),
        )
        self.safety_policy.medium_confidence_threshold = float(
            self.pet_settings.get("agent_medium_confidence", 0.82)
        )
        self.safety_policy.always_confirm_high_risk = bool(
            self.pet_settings.get("agent_confirm_high_risk", True)
        )
        self.conversation_service.set_memory_candidates_enabled(
            bool(self.pet_settings.get("enable_memory_candidates", False))
        )
        self.memory_service.set_candidates_enabled(
            bool(self.pet_settings.get("enable_memory_candidates", False))
        )
        self.semantic_action_parser.enabled = bool(
            self.pet_settings.get("unified_semantic_parser_enabled", True)
        )
        self.business_resolver.enabled = bool(
            self.pet_settings.get("business_resolver_enabled", True)
        )
        self.response_composer.enabled = bool(
            self.pet_settings.get("deterministic_response_enabled", True)
        )
        self.conversation_service.interaction_coordinator_enabled = bool(
            self.pet_settings.get("interaction_coordinator_enabled", True)
        )
        self.conversation_service.unified_semantic_parser_enabled = bool(
            self.pet_settings.get("unified_semantic_parser_enabled", True)
        )
        self.conversation_service.business_resolver_enabled = bool(
            self.pet_settings.get("business_resolver_enabled", True)
        )
        self.conversation_service.deterministic_response_enabled = bool(
            self.pet_settings.get("deterministic_response_enabled", True)
        )
        self.conversation_service.action_batch_enabled = bool(
            self.pet_settings.get("action_batch_enabled", True)
        )
        self.conversation_service.action_preview_enabled = bool(
            self.pet_settings.get("action_preview_enabled", True)
        )
        self.conversation_service.legacy_intent_path_enabled = bool(
            self.pet_settings.get("legacy_intent_path_enabled", False)
        )
        self.conversation_service.semantic_decision_compatibility_enabled = not isinstance(
            self.llm_client, RoutedLLMClient
        )
        for warning in validate_feature_flags(self.pet_settings):
            print(f"[FeatureFlags] warning={warning}", flush=True)
        self.agent_core.action_batch_enabled = (
            self.conversation_service.action_batch_enabled
        )
        self.agent_core.plan_postcondition_enabled = bool(
            self.pet_settings.get("plan_postcondition_enabled", True)
        )

    def handle_history_cleared_from_settings(self) -> None:
        self.current_session_id = ""
        self.return_today_session()

    def new_chat_session(self) -> None:
        self.add_message("System", "每天只有一个学习会话。可以点击“返回今天”继续。", record_history=False)

    def _select_today_session(self, business_date: Optional[str] = None) -> bool:
        try:
            day = business_date or self.growth_service.now_provider().date().isoformat()
            session = self.chat_history_manager.ensure_daily_learning_session(day)
            session_id = str(session["session_id"])
            verified = self.chat_history_manager.get_session(session_id)
            if verified is None or verified.get("business_date") != day:
                raise OSError("Daily session could not be read back")
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            print(f"[ChatHistory] daily session unavailable: {error}", flush=True)
            self.current_session_id = ""
            self._session_startup_error = "今天的学习会话读取失败，聊天暂不可用。请点击“返回今天”重试。"
            self._viewing_history = False
            if hasattr(self, "transcript"):
                self.transcript.clear()
                self._show_session_startup_error()
            return False
        self._session_startup_error = ""
        self._daily_startup_session_id = session_id
        previous_session_id = self.current_session_id
        if (previous_session_id and previous_session_id != session_id
                and hasattr(self, "conversation_service")
                and not self._request_in_progress_for(previous_session_id)):
            self.confirmation_manager.cancel(scope=previous_session_id)
            if hasattr(self, "interaction_coordinator"):
                self.interaction_coordinator.clear_conversation(previous_session_id)
            if hasattr(self.conversation_service, "state_manager"):
                self.conversation_service.state_manager.reset(previous_session_id)
            self._pending_conversation_turn = None
            self._pending_memory_delete_id = None
            self._pending_plan_delete_id = None
        self._viewing_history = False
        self.current_session_id = session_id
        if hasattr(self, "transcript"):
            self.restore_current_session()
        return True

    def return_today_session(self) -> None:
        self._select_today_session()

    def _update_simulation_date_label(self) -> None:
        if self.simulation_date_label is not None:
            self.simulation_date_label.setText(
                f"当前测试日期 {self.simulation_clock.today()}"
            )

    def advance_simulation_day(self) -> bool:
        if self.simulation_clock is None:
            return False
        if (self.reply_thread is not None and self.reply_thread.isRunning()
                or self._pending_conversation_turn is not None):
            self.add_message("System", "请求仍在执行，请等待完成后再推进测试日期。", record_history=False)
            return False
        try:
            self.simulation_clock.advance_one_day()
            self._update_simulation_date_label()
            if not self._select_today_session():
                return False
            if self.growth_dialog is not None:
                self.growth_dialog.refresh_all()
            pet_panel = getattr(self.pet_controller, "today_growth_dialog", None)
            if pet_panel is not None:
                pet_panel.refresh_from_service()
        except (OSError, ValueError) as error:
            self.add_message("System", f"推进测试日期失败：{error}", record_history=False)
            return False
        return True

    def showEvent(self, event) -> None:
        self._select_today_session()
        self._maybe_show_first_use_hint()
        super().showEvent(event)

    def _maybe_show_first_use_hint(self) -> None:
        """首次打开聊天时显示一次轻量提示；同一次运行内不再重复。"""
        if self._first_use_hint_shown_this_run or self._viewing_history:
            return
        self._first_use_hint_shown_this_run = True
        if first_use_hint_seen(self.settings_config_file):
            return
        self.first_use_hint.show()
        # 只追加这一个标志；配置损坏时不覆盖，写入失败则下次启动再提示。
        mark_first_use_hint_seen(self.settings_config_file)

    def _open_examples_from_first_use_hint(self) -> None:
        self._dismiss_first_use_hint()
        self.open_usage_dialog()

    def _dismiss_first_use_hint(self) -> None:
        self.first_use_hint.hide()

    def open_chat_history_dialog(self) -> None:
        self.record_pet_interaction()
        if self.chat_history_dialog is None:
            self.chat_history_dialog = ChatHistoryDialog(
                self.chat_history_manager,
                self.switch_chat_session,
                self,
            )
            self.chat_history_dialog.finished.connect(
                self._clear_chat_history_dialog
            )
        self.chat_history_dialog.refresh_sessions()
        self.chat_history_dialog.show()
        self.chat_history_dialog.raise_()
        self.chat_history_dialog.activateWindow()

    def _clear_chat_history_dialog(self) -> None:
        self.chat_history_dialog = None

    def switch_chat_session(self, session_id: str) -> bool:
        session = self.chat_history_manager.switch_session(session_id)
        if session is None:
            return False
        today = self.growth_service.now_provider().date().isoformat()
        if session.get("session_kind") == "daily_learning" and session.get("business_date") == today:
            return self._select_today_session(today)
        previous_session_id = self.current_session_id
        request_in_progress = self._request_in_progress_for(previous_session_id)
        if not request_in_progress:
            self.confirmation_manager.cancel(scope=previous_session_id)
        self.confirmation_manager.cancel(scope=session_id)
        if hasattr(self, "interaction_coordinator"):
            if not request_in_progress:
                self.interaction_coordinator.clear_conversation(previous_session_id)
            self.interaction_coordinator.clear_conversation(session_id)
        if hasattr(self.conversation_service, "state_manager"):
            if not request_in_progress:
                self.conversation_service.state_manager.reset(previous_session_id)
            self.conversation_service.state_manager.reset(session_id)
        self._pending_conversation_turn = None
        self._pending_memory_delete_id = None
        self._pending_plan_delete_id = None
        self.current_session_id = session_id
        self._viewing_history = True
        self.restore_current_session()
        return True

    def _request_in_progress_for(self, session_id: str) -> bool:
        return bool(
            session_id
            and self._active_reply_trace is not None
            and self._active_reply_trace.session_id == session_id
            and self.reply_thread is not None
            and self.reply_thread.isRunning()
        )

    def delete_chat_session(self, session_id: str) -> bool:
        return False

    def restore_current_session(self) -> None:
        self.transcript.clear()
        if self._session_startup_error and not self.current_session_id:
            self._show_session_startup_error()
            return
        session = self.chat_history_manager.get_session(self.current_session_id)
        if session is None:
            self.current_session_id = ""
            self._session_startup_error = "会话读取失败，请点击“返回今天”重试。"
            self._show_session_startup_error()
            return
        self._session_startup_error = ""
        self.input_box.setEnabled(not self._viewing_history and not self._quit_requested)
        self.send_button.setEnabled(not self._viewing_history and not self._quit_requested)
        self.return_today_button.setVisible(self._viewing_history)
        # 只读历史视图不显示首次提示，也不在这里出现新的发送入口。
        if self._viewing_history and hasattr(self, "first_use_hint"):
            self.first_use_hint.hide()
        self.session_title_label.setText(
            (f"{session_display_title(session)}\n历史对话 · 只读"
             if session.get("session_kind") == "daily_learning" else
             f"历史对话 · 只读 · {str(session.get('started_at', '日期未标记'))[:10]}")
            if self._viewing_history else session_display_title(session)
        )
        self._fit_chat_decorations()
        messages = session.get("messages", [])
        if not isinstance(messages, list) or not messages:
            if not self._viewing_history:
                self.greet_user()
            return
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "system"))
            sender = {"user": "You", "assistant": "Roxy"}.get(role, "System")
            self.add_message(
                sender,
                str(item.get("content", "")),
                record_history=False,
                timestamp=self._display_timestamp(item.get("created_at")),
            )

    def _show_session_startup_error(self) -> None:
        self.session_title_label.setText("会话不可用")
        self.input_box.setEnabled(False)
        self.send_button.setEnabled(False)
        self.return_today_button.setVisible(True)
        self.add_message("System", self._session_startup_error, record_history=False)

    @staticmethod
    def _display_timestamp(value: object) -> str:
        text = str(value or "")
        if "T" in text:
            return text.split("T", 1)[1][:8]
        return text[-8:] if text else datetime.now().strftime("%H:%M:%S")

    def open_growth_dialog(self) -> None:
        self.record_pet_interaction()
        if self.pet_controller is not None and hasattr(self.pet_controller, "open_growth_dialog"):
            self.pet_controller.open_growth_dialog()
            return

        if self.growth_dialog is None:
            self.growth_dialog = GrowthDialog(self.growth_service, self)
            self.growth_dialog.finished.connect(self._clear_growth_dialog)
        self.growth_dialog.refresh_all()
        self.growth_dialog.show()
        self.growth_dialog.raise_()
        self.growth_dialog.activateWindow()

    def open_today_growth_dialog(self) -> None:
        self.record_pet_interaction()
        if self.pet_controller is not None and hasattr(self.pet_controller, "open_today_growth_dialog"):
            self.pet_controller.open_today_growth_dialog()
            return
        if self.today_growth_dialog is None:
            self.today_growth_dialog = TodayGrowthDialog(
                growth_manager=self.growth_service,
                learning_loop=LearningLoopService(
                    self.growth_service, now_provider=self.growth_service.now_provider,
                ),
                parent=self,
            )
            self.today_growth_dialog.details_requested.connect(self.open_growth_dialog)
            self.today_growth_dialog.finished.connect(self._clear_today_growth_dialog)
        self.today_growth_dialog.show()
        self.today_growth_dialog.raise_()

    def _clear_today_growth_dialog(self) -> None:
        self.today_growth_dialog = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_chat_decorations()

    def _fit_chat_decorations(self) -> None:
        if not hasattr(self, "header_decoration"):
            return
        header_width = min(112, max(46, self.width() - (370 if self._viewing_history else 300)))
        self.header_decoration.setFixedSize(header_width, round(header_width * 941 / 1672))
        footer_width = min(48, max(28, self.width() - 332))
        self.footer_decoration.setFixedSize(footer_width, round(footer_width * 1086 / 1448))

    def _clear_growth_dialog(self) -> None:
        self.growth_dialog = None

    def open_memory_dialog(self) -> None:
        self.record_pet_interaction()
        if self.memory_dialog is None:
            self.memory_dialog = MemoryDialog(
                parent=self,
                memory_service=self.memory_service,
            )
            self.memory_dialog.finished.connect(self._clear_memory_dialog)
        self.memory_dialog.refresh_all()
        self.memory_dialog.show()
        self.memory_dialog.raise_()
        self.memory_dialog.activateWindow()

    def _clear_memory_dialog(self) -> None:
        self.memory_dialog = None

    def move_to_bottom_right(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 28, available.bottom() - self.height() - 36)

    def load_memory(self) -> Dict[str, Any]:
        if hasattr(self, "memory_manager"):
            return self.memory_manager.data
        return default_memory()

    def save_memory(self, memory: Optional[Dict[str, Any]] = None) -> None:
        # 所有长期记忆都写入本地 memory.json；该文件不应提交到公开仓库。
        data = self.memory if memory is None else memory
        self.memory_manager.data = data
        self.memory = self.memory_manager.data
        if not self.memory_manager.save():
            raise OSError("Failed to save local memory file")

    def load_personality(self) -> Dict[str, Any]:
        # V0.4 从 data/roxy_personality.json 读取规则人格，不接入 AI。
        if self.runtime is not None and self.runtime.is_packaged:
            return read_json_strict(self.personality_file)
        if not self.personality_file.exists():
            personality = default_personality()
            self.save_personality(personality)
            return personality

        try:
            with self.personality_file.open("r", encoding="utf-8") as file:
                personality = json.load(file)
        except (json.JSONDecodeError, OSError):
            personality = default_personality()
            self.save_personality(personality)
            return personality

        if not isinstance(personality, dict):
            personality = default_personality()

        personality.setdefault("version", 1)
        personality.setdefault("name", "")
        personality.setdefault("personality", "")
        personality.setdefault("likes", "")
        personality.setdefault("speaking_style", "")
        personality.setdefault("rules", [])
        personality.setdefault("fallback_reply", "你好，{nickname}。")
        return personality

    def _load_legacy_config(self) -> Dict[str, str]:
        if self.runtime is not None and self.runtime.is_packaged:
            # Prevent the compatibility loader from replacing damaged config.
            read_json_strict(self.legacy_config_file)
        return load_llm_config(self.legacy_config_file)

    def save_personality(self, personality: Dict[str, Any]) -> None:
        self.personality_file.parent.mkdir(parents=True, exist_ok=True)
        with self.personality_file.open("w", encoding="utf-8") as file:
            json.dump(personality, file, ensure_ascii=False, indent=2)

    def scan_knowledge_files(self) -> List[Dict[str, str]]:
        self.knowledge_manager.directory = self.knowledge_dir
        return self.knowledge_manager.scan()

    def greet_user(self) -> None:
        nickname = self.memory_manager.preferred_name()
        if nickname:
            self.add_message("Roxy", f"欢迎回来，{nickname}。")
            self.log_saved_memories()
        else:
            self.add_message("Roxy", "欢迎回来。今天也一起慢慢推进吧。")
            self.log_saved_memories()

    def log_saved_memories(self) -> None:
        memories = self.memory.get("memories", [])
        if not memories:
            print("[MEMORY] no saved memories", flush=True)
            return

        categories = []
        for item in memories:
            if isinstance(item, dict):
                content = item.get("content", "")
                category = str(item.get("category", "other"))
            else:
                content = str(item)
                category = "legacy"
            if content:
                categories.append(category)

        if categories:
            category_summary = ",".join(sorted(set(categories)))
            print(
                f"[MEMORY] {len(categories)} memories loaded; categories={category_summary}",
                flush=True,
            )
        else:
            print("[MEMORY] no non-empty saved memories", flush=True)

    def send_message(self) -> None:
        if self._quit_requested or self._viewing_history or not self.current_session_id:
            return
        user_text = self.input_box.text().strip()
        if not user_text:
            return

        submitted_business_date = self.growth_service.now_provider().date().isoformat()
        session = self.chat_history_manager.get_session(self.current_session_id)
        if (session is None or session.get("session_kind") != "daily_learning"
                or session.get("business_date") != submitted_business_date):
            if not self._select_today_session(submitted_business_date):
                return
        submitted_session_id = self.current_session_id

        self._message_sequence += 1
        trace = self.development_log.new_trace(
            session_id=submitted_session_id, source="chat",
        )
        self._submitted_trace = trace
        with self.development_log.bind(trace):
            self.development_log.event(
                trace, "chat_submitted",
                user_text_hash=hashlib.sha256(user_text.encode("utf-8")).hexdigest(),
            )
            self.record_pet_interaction()
            self.add_message("You", user_text)
            self.input_box.clear()

            if self.handle_chat_history_command(user_text):
                self.development_log.event(trace, "turn_service_finished", status="completed", terminal=True)
                return

            if self.handle_agent_confirmation(user_text):
                self.development_log.event(trace, "turn_service_finished", status="completed", terminal=True)
                return

            self.handle_agent_request(
                user_text,
                session_id=submitted_session_id,
                business_date=submitted_business_date,
            )

    @staticmethod
    def _retire_legacy_natural_handler(handler_name: str) -> bool:
        """Seal former desktop text handlers outside the unified pipeline.

        These compatibility methods remain in the class for API stability and
        for their data-management implementation history, but the desktop chat
        entry must never let them rescan a natural-language message after
        ConversationService has produced its one semantic decision.
        """
        print(f"[LegacyIntent] retired handler={handler_name}", flush=True)
        return True

    def handle_agent_confirmation(self, user_text: str) -> bool:
        if not bool(self.pet_settings.get("agent_core_enabled", True)):
            return False
        legacy_response = self._handle_legacy_confirmation(user_text)
        if legacy_response is not None:
            self.add_message("Roxy", legacy_response)
            return True
        # Scoped confirmations created by ConversationService must return to
        # ConversationService.  Only the exact local management confirmations
        # above remain in this compatibility entry point.
        return False

    def _handle_legacy_confirmation(self, user_text: str) -> Optional[str]:
        text = user_text.strip().strip("。.!！?？")
        pending = self.confirmation_manager.pending(scope=self.current_session_id)
        if pending is not None and pending.get("tool") in {"clear_chat_history", "delete_chat_session"}:
            self.confirmation_manager.cancel(scope=self.current_session_id)
            return "聊天窗口已关闭历史删除和清空操作。"
        if pending is None or pending.get("tool") not in {
            "clear_memory_candidates", "delete_memory_candidate",
        }:
            return None
        if text in {"取消", "不要执行", "取消刚才的操作", "不用了"}:
            self.confirmation_manager.cancel(scope=self.current_session_id)
            return "好，刚才的操作已经取消。"
        expected_specific = {
            "clear_memory_candidates": "确认清空待确认记忆",
            "delete_memory_candidate": "确认删除候选记忆",
        }[str(pending["tool"])]
        if text not in {"确认", "确认刚才的操作", "继续执行"} and not text.startswith(expected_specific):
            return None
        consumed = self.confirmation_manager.consume(
            str(pending["confirmation_id"]),
            scope=self.current_session_id,
        )
        if consumed is None:
            return "刚才的确认已经过期，请重新发起操作。"
        tool = str(consumed["tool"])
        arguments = dict(consumed["arguments"])
        if tool == "clear_memory_candidates":
            operation = self.memory_service.clear_pending_candidates()
            count = int(operation.data.get("changed_count", 0))
            return (
                f"已经清空 {count} 条待确认记忆，它们不会写入长期记忆。"
                if operation.success
                else operation.safe_message
            )
        candidate_id = int(arguments.get("candidate_id", 0))
        operation = self.memory_service.delete_candidate(candidate_id)
        return (
            f"已经删除候选记忆 {candidate_id}。"
            if operation.success
            else operation.safe_message
        )

    def handle_agent_request(
        self, user_text: str, *, session_id: str = "", business_date: str = ""
    ) -> bool:
        session_id = session_id or self.current_session_id
        business_date = business_date or self.growth_service.now_provider().date().isoformat()
        self._capture_client_runtime_snapshot()
        turn = self.conversation_service.prepare(
            user_text,
            session_id,
            record_history=False,
            allow_llm_intent=False,
            business_date=business_date,
            require_existing_session=bool(self.chat_history_manager.enabled),
        )
        if turn.response is None:
            self._pending_conversation_turn = turn if turn.requires_llm else None
            if turn.requires_llm:
                self.start_ai_reply(user_text, self._message_sequence, business_date=business_date)
                return True
            self.add_message("Roxy", "这次没有得到可继续处理的结果，请换一种说法。")
            return True
        self._pending_conversation_turn = None
        self._present_agent_response(
            turn.response,
            intent=str(turn.intent_result.get("intent", "")) or None,
        )
        return True

    def _present_agent_response(
        self,
        response: AgentResponse,
        *,
        intent: Optional[str] = None,
    ) -> List[ClientActionResult]:
        trace = self.development_log.current_trace() or self._active_reply_trace
        response_session_id = str(
            response.conversation_id or (trace.session_id if trace is not None else "")
            or self.current_session_id
        )
        if response_session_id != self.current_session_id:
            return []
        results: List[ClientActionResult] = []
        dispatcher_enabled = bool(
            self.pet_settings.get("unified_client_action_dispatcher_enabled", True)
        )
        dispatcher = getattr(
            self.pet_controller, "client_action_dispatcher", None
        )
        if dispatcher_enabled and response.client_actions:
            if dispatcher is not None:
                results = dispatcher.dispatch_all(response.client_actions)
            else:
                results = [
                    ClientActionResult.rejected(
                        action,
                        status="failed",
                        reason_code="pet_unavailable",
                        display_message="桌宠当前不可用，动作没有执行。",
                    )
                    for action in response.client_actions
                ]
        elif response.client_actions and bool(
            self.pet_settings.get("legacy_direct_pet_action_enabled", False)
        ):
            for action in response.client_actions:
                accepted = bool(
                    self.pet_controller
                    and self.pet_controller.execute_client_action(action.to_dict())
                )
                results.append(
                    ClientActionResult(
                        action_id=action.action_id,
                        name=action.name,
                        status="running" if accepted else "failed",
                        accepted=accepted,
                        started=accepted,
                        reason_code="legacy_direct",
                    )
                )
        elif response.client_actions:
            results = [
                ClientActionResult.rejected(
                    action,
                    status="rejected",
                    reason_code="dispatcher_disabled",
                    display_message="当前客户端未启用这个桌宠动作，这次没有执行。",
                )
                for action in response.client_actions
            ]
        snapshot = self._capture_client_runtime_snapshot()
        for result in results:
            self.conversation_service.state_manager.observe_client_action_result(
                response_session_id,
                {
                    "name": result.name,
                    "status": result.status,
                    "accepted": result.accepted,
                    "started": result.started,
                    "completed": result.completed,
                    "reason_code": result.reason_code,
                    "source": "desktop_dispatcher",
                    "observed_at": snapshot["observed_at"],
                    "state": snapshot["pet_state"],
                },
            )
        message = response.message
        if bool(self.pet_settings.get("client_action_claim_guard_enabled", True)):
            message = self.client_action_claim_guard.validate(
                message,
                results,
                action_expected=(
                    any(item.name == "play_dance" for item in response.client_actions)
                    or any(item.tool == "play_dance" for item in response.tool_results)
                ),
            )
        with self.development_log.bind(trace):
            self._refresh_memory_dialog_after_verified_save(response)
            self.add_message("Roxy", sanitize_public_reply(message), intent=intent)
        return results

    def _refresh_memory_dialog_after_verified_save(
        self,
        response: AgentResponse,
    ) -> None:
        if self.memory_dialog is None:
            return
        for result in response.tool_results:
            if not result.success or result.tool not in {
                "save_formal_memory", "delete_memory", "update_memory", "archive_memory",
            }:
                continue
            operation = result.data.get("memory_operation", {})
            operation = operation if isinstance(operation, dict) else {}
            if operation.get("memory_id") is None:
                continue
            self.memory_dialog.refresh_all()
            reason = {
                "save_formal_memory": "formal_memory_saved",
                "delete_memory": "formal_memory_deleted",
                "update_memory": "formal_memory_updated",
                "archive_memory": "formal_memory_archived",
            }[result.tool]
            print(f"[MemoryUI] refreshed reason={reason}", flush=True)
            return

    def ask_ai(self, user_text: str) -> str:
        self._capture_client_runtime_snapshot()
        turn = self.conversation_service.prepare(
            user_text,
            self.current_session_id,
            record_history=False,
        )
        return self.conversation_service.complete(turn).message

    def start_ai_reply(
        self,
        user_text: str,
        turn_sequence: Optional[int] = None,
        business_date: str = "",
    ) -> None:
        if self._quit_requested:
            return
        if self.reply_thread is not None and self.reply_thread.isRunning():
            self.add_message("Roxy", "我还在思考上一条消息，请稍等一下。")
            return

        self._capture_client_runtime_snapshot()
        turn = self._pending_conversation_turn
        self._pending_conversation_turn = None
        if turn is None or turn.message != user_text or not turn.requires_llm:
            turn = self.conversation_service.prepare(
                user_text,
                self.current_session_id,
                record_history=False,
                allow_llm_intent=False,
                business_date=business_date or self.growth_service.now_provider().date().isoformat(),
                require_existing_session=bool(self.chat_history_manager.enabled),
            )
        if turn.response is not None or turn.deferred:
            response = self.conversation_service.complete(turn)
            with self.development_log.bind(turn.trace_context):
                self._present_agent_response(response)
            return

        if turn_sequence is None:
            if self._message_sequence <= 0:
                self._message_sequence = 1
            turn_sequence = self._message_sequence
        self._active_reply_sequence = int(turn_sequence)
        self._active_reply_request_id = turn.request_id
        self._active_reply_business_date = turn.business_date
        self._active_reply_trace = turn.trace_context
        self.start_pet_thinking()
        self.reply_thread = QThread(self)
        self.reply_worker = ChatReplyWorker(
            self.conversation_service,
            turn,
            self._active_reply_sequence,
        )
        self.reply_worker.moveToThread(self.reply_thread)
        self.reply_thread.started.connect(self.reply_worker.run)
        self.reply_worker.finished.connect(self.finish_ai_reply)
        self.reply_worker.finished.connect(self.reply_thread.quit)
        self.reply_worker.finished.connect(self.reply_worker.deleteLater)
        self.reply_thread.finished.connect(self.reply_thread.deleteLater)
        self.reply_thread.finished.connect(self.clear_reply_thread)
        self.reply_thread.start()

    def finish_ai_reply(
        self,
        response: object,
        turn_sequence: Optional[int] = None,
    ) -> None:
        self.stop_pet_thinking()
        completed_sequence = int(
            turn_sequence
            if turn_sequence is not None
            else self._active_reply_sequence
        )
        trace = self._active_reply_trace
        response_origin_id = (
            str(response.conversation_id or "")
            if isinstance(response, AgentResponse) else ""
        )
        if (
            completed_sequence != self._message_sequence
            or (trace is not None and trace.session_id != self.current_session_id)
            or (response_origin_id and response_origin_id != self.current_session_id)
        ):
            origin_id = str(
                response_origin_id
                or (trace.session_id if trace is not None else "")
            )
            saved = None
            if origin_id and self.chat_history_manager.get_session(origin_id) is not None:
                message = response.message if isinstance(response, AgentResponse) else str(response)
                metadata = trace.to_metadata() if trace is not None else {}
                metadata["request_id"] = (
                    response.request_id if isinstance(response, AgentResponse)
                    else self._active_reply_request_id
                ) or self._active_reply_request_id
                if self._active_reply_business_date:
                    metadata["business_date"] = self._active_reply_business_date
                saved = self.chat_history_manager.add_message(
                    origin_id, "assistant", sanitize_public_reply(message),
                    metadata=metadata,
                )
            print("[Conversation] response stored in origin session" if saved else "[Conversation] origin response not stored", flush=True)
            self.development_log.event(
                trace, "reply_not_displayed", status="stored" if saved else "ignored",
                reason_code="session_changed" if trace and trace.session_id != self.current_session_id else "reply_stale",
                terminal=True,
            )
            return
        reply = response.message if isinstance(response, AgentResponse) else str(response)
        request_id = (
            response.request_id
            if isinstance(response, AgentResponse)
            else self._active_reply_request_id
        )
        with self.development_log.bind(trace):
            if isinstance(response, AgentResponse):
                self._present_agent_response(response)
            else:
                self.add_message("Roxy", sanitize_public_reply(reply))
        print(
            f"[Conversation] completed request_id={request_id or 'untracked'}",
            flush=True,
        )

    def clear_reply_thread(self) -> None:
        self.reply_thread = None
        self.reply_worker = None
        self._active_reply_sequence = 0
        self._active_reply_request_id = ""
        self._active_reply_business_date = ""
        self._active_reply_trace = None

        if self._quit_requested:
            QTimer.singleShot(0, QApplication.quit)

    def record_pet_interaction(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "record_interaction"):
            self.pet_controller.record_interaction()

    def handle_chat_history_command(self, user_text: str) -> bool:
        text = user_text.strip()
        if text == "新建对话":
            self.new_chat_session()
            return True

        if text in {"清空聊天记录", "确认清空聊天记录", "确认删除对话"} or re.fullmatch(r"删除对话\s*\d+", text):
            self.add_message("Roxy", "聊天窗口已关闭历史删除和清空操作。")
            return True

        if text == "查看历史对话":
            sessions = self.chat_history_manager.sessions()
            if not sessions:
                self.add_message("Roxy", "现在还没有历史对话。")
                return True
            lines = ["本地历史对话："]
            for index, session in enumerate(sessions, start=1):
                lines.append(
                    f"{index}. {session.get('title', '新对话')} "
                    f"（{session.get('message_count', 0)} 条消息）"
                )
            lines.append("可以说“切换对话1”只读回看，随后点击“返回今天”。")
            self.add_message("Roxy", "\n".join(lines))
            return True

        switch_match = re.fullmatch(r"切换对话\s*(\d+)", text)
        if switch_match:
            session = self.chat_history_manager.session_by_index(
                int(switch_match.group(1))
            )
            if session is None:
                self.add_message("Roxy", "没有找到这个会话，可以先说“查看历史对话”。")
                return True
            self.switch_chat_session(str(session["session_id"]))
            return True

        if text == "总结这段对话":
            summary = self.generate_session_summary(force=True)
            self.add_message("Roxy", summary)
            return True

        return False

    def handle_plan_command(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_plan_command"):
            return False
        text = user_text.strip()
        add_prefixes = ("今日计划：", "今日计划:", "添加计划：", "添加计划:")
        for prefix in add_prefixes:
            if text.startswith(prefix):
                title = text[len(prefix) :].strip()
                if not title:
                    self.add_message("Roxy", "可以告诉我要安排什么，例如：添加计划：学习机器学习30分钟")
                    return True
                task = self.plan_store.add_task(title)
                self.add_message("Roxy", f"已经记下：{task['id']}. {task['title']}")
                return True

        if text == "查看计划":
            self.show_today_plan()
            return True

        completion_match = re.fullmatch(r"(?:完成计划|完成任务)\s*(\d+)", text)
        if completion_match:
            task_id = int(completion_match.group(1))
            task, changed = self.plan_store.complete_by_id(task_id)
            self.show_plan_completion(task, changed, f"计划 {task_id}")
            return True

        if text.startswith("我完成了"):
            title = text[len("我完成了") :].strip()
            if not title:
                self.add_message("Roxy", "告诉我完成了什么就好，例如：我完成了学习机器学习30分钟")
                return True
            task, changed = self.plan_store.complete_by_title(title)
            self.show_plan_completion(task, changed, title)
            return True

        confirm_deletion_match = re.fullmatch(r"确认删除计划\s*(\d+)", text)
        if confirm_deletion_match:
            task_id = int(confirm_deletion_match.group(1))
            if self._pending_plan_delete_id != task_id:
                self.add_message("Roxy", "这条删除请求已经失效，请重新说“删除计划编号”。")
                return True
            self._pending_plan_delete_id = None
            removed = self.plan_store.delete_by_id(task_id)
            self.add_message(
                "Roxy",
                f"已经删除计划：{removed['title']}" if removed else "没有找到这条计划。",
            )
            return True

        deletion_match = re.fullmatch(r"删除计划\s*(\d+)", text)
        if deletion_match:
            task_id = int(deletion_match.group(1))
            task = next((item for item in self.plan_store.tasks() if int(item.get("id", 0)) == task_id), None)
            if task is None:
                self.add_message("Roxy", f"没有找到计划 {task_id}，可以先说“查看计划”。")
            else:
                self._pending_plan_delete_id = task_id
                self.add_message(
                    "Roxy",
                    f"将删除计划“{task['title']}”。确定的话，请再说“确认删除计划{task_id}”。",
                )
            return True

        return False

    def show_today_plan(self) -> None:
        tasks = self.plan_store.tasks()
        if not tasks:
            self.add_message("Roxy", "今天还没有计划。可以对我说“添加计划：……”")
            return

        lines = ["今天的计划："]
        for task in tasks:
            status = "已完成" if task.get("done", False) else "待完成"
            lines.append(f"{task['id']}. [{status}] {task['title']}")
        self.add_message("Roxy", "\n".join(lines))

    def show_plan_completion(
        self,
        task: Optional[Dict[str, object]],
        changed: bool,
        requested: str,
    ) -> None:
        if task is None:
            self.add_message("Roxy", f"我没有找到{requested}。可以先说“查看计划”。")
            return
        if not changed:
            self.add_message("Roxy", f"这件事已经完成了：{task['title']}")
            return
        self.add_message("Roxy", f"完成得很好，我记下了：{task['title']}")
        self.notify_plan_completed()

    def show_today_review(self) -> None:
        review = self.growth_service.generate_review()
        self.add_message("Roxy", str(review["text"]))

    def handle_action_log_command(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_action_log_command"):
            return False
        text = user_text.strip()
        for prefix in ("记录：", "记录:", "行动记录：", "行动记录:"):
            if text.startswith(prefix):
                content = text[len(prefix) :].strip()
                if not content:
                    self.add_message("Roxy", "可以告诉我刚刚推进了什么，例如：记录：今天学习了逻辑回归")
                    return True
                record = self.growth_service.action_store.add_record(content, source="chat")
                self.add_message("Roxy", f"已经记下这次行动：{record['content']}")
                return True

        if text == "查看记录":
            records = self.growth_service.action_store.records_for_date()
            if not records:
                self.add_message("Roxy", "今天还没有行动记录。做完一小步时告诉我就好。")
                return True
            lines = ["今天的行动记录："]
            lines.extend(f"- {record['time']}  {record['content']}" for record in records)
            self.add_message("Roxy", "\n".join(lines))
            return True
        return False

    def handle_growth_command(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_growth_command"):
            return False
        text = user_text.strip()
        if text in {"今日复盘", "复盘一下", "今天完成了什么"}:
            self.show_today_review()
            return True

        if text == "保存今日复盘":
            self.growth_service.save_today_review()
            self.add_message("Roxy", "今天的复盘已经保存到成长日志了。")
            return True

        if text == "查看成长日志":
            entries = self.growth_service.growth_store.entries()
            if not entries:
                self.add_message("Roxy", "成长日志还是空的。完成今天的复盘后，可以说“保存今日复盘”。")
                return True
            lines = ["成长日志："]
            for entry in entries[-7:]:
                review = entry.get("review", {})
                if not isinstance(review, dict):
                    review = {}
                lines.append(
                    f"- {entry.get('date', '')}：计划 {review.get('total', 0)} 件，"
                    f"完成 {review.get('done', 0)} 件，行动 {len(review.get('actions', []))} 条"
                )
            self.add_message("Roxy", "\n".join(lines))
            return True
        return False

    def handle_natural_intent(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_natural_intent"):
            return False
        if not bool(self.pet_settings.get("smart_intent_enabled", True)):
            return False
        result = self.intent_router.route(user_text)
        intent = str(result.get("intent", "chat"))
        slots = result.get("entities", result.get("slots", {}))
        if not isinstance(slots, dict) or intent == "chat":
            return False
        confidence = float(result.get("confidence", 0.0))
        required = float(self.pet_settings.get("intent_auto_execute_confidence", 0.78))
        if confidence < required:
            print("[Intent] clarification required", flush=True)
            self.add_message("Roxy", "我大概理解了，但不想替你做错操作。可以再具体说一点吗？")
            return True

        if intent == "add_plan":
            titles = [str(item).strip() for item in slots.get("tasks", []) if str(item).strip()]
            tasks = [self.plan_store.add_task(title) for title in titles]
            if not tasks:
                return False
            if len(tasks) == 1:
                self.add_message("Roxy", f"好，我帮你把“{tasks[0]['title']}”加入今天计划了。")
            else:
                task_lines = "\n".join(f"{task['id']}. {task['title']}" for task in tasks)
                self.add_message("Roxy", f"好，已经把这 {len(tasks)} 件事加入今天计划：\n{task_lines}")
            return True

        if intent in {"complete_plan", "delete_plan"}:
            query = str(slots.get("query", "")).strip()
            return self.handle_natural_plan_match(intent, query)

        if intent == "add_action_log":
            content = str(slots.get("content", "")).strip()
            if not content:
                self.add_message("Roxy", "你想记录哪一步行动？再具体告诉我一点就好。")
                return True
            record = self.growth_service.action_store.add_record(content, source="chat")
            self.add_message("Roxy", f"好，这一步已经记到行动记录里了：{record['content']}")
            return True

        if intent == "daily_review":
            self.show_today_review()
            return True

        if intent == "save_review":
            self.growth_service.save_today_review()
            self.add_message("Roxy", "好，今天的复盘已经存进成长日志了。")
            return True

        if intent == "add_memory_request":
            content = str(slots.get("content", "")).strip()
            if content in {"", "这个", "这件事", "这件事情"}:
                self.add_message("Roxy", "可以再具体说说要长期记住什么吗？")
                return True
            return self.handle_memory_command(f"记住：{content}")

        if intent == "memory_candidate":
            content = str(slots.get("content", "")).strip()
            source_text = str(slots.get("source_text", user_text)).strip()
            operation = self.memory_service.create_candidate(
                content,
                source_text=source_text,
                source="conversation",
                explicit=False,
            )
            if operation.status == "already_processed":
                self.add_message(
                    "Roxy",
                    "这条和已有长期记忆很接近，我没有重复加入候选。",
                )
                return True
            candidate = operation.data.get("candidate", {})
            candidate = candidate if isinstance(candidate, dict) else {}
            candidate_id = int(candidate.get("id", 0))
            if operation.success:
                self.add_message(
                    "Roxy",
                    "这个信息以后可能有用，我先放到待确认记忆里啦。"
                    f"你可以说“确认记忆{candidate_id}”让我长期记住，"
                    f"或者说“忽略记忆{candidate_id}”。",
                )
            else:
                self.add_message("Roxy", operation.safe_message)
            return True

        if intent == "reminder_control":
            action = str(slots.get("action", "")).strip()
            if action == "pause":
                if self.pet_controller is not None and hasattr(
                    self.pet_controller, "pause_proactive_reminders"
                ):
                    self.pet_controller.pause_proactive_reminders()
                self.add_message(
                    "Roxy",
                    "好，这次运行里我先暂停主动提醒。需要时对我说“恢复提醒”。",
                )
                return True
            if action == "resume":
                if self.pet_controller is not None and hasattr(
                    self.pet_controller, "resume_proactive_reminders"
                ):
                    self.pet_controller.resume_proactive_reminders()
                self.add_message("Roxy", "主动提醒已经恢复，我会注意保持安静和克制。")
                return True

        if intent == "show_plan":
            self.show_today_plan()
            return True

        if intent == "show_action_log":
            return self.handle_action_log_command("查看记录")

        if intent == "show_growth_log":
            return self.handle_growth_command("查看成长日志")

        if intent == "show_memory":
            self.show_all_memories()
            return True

        if intent == "search_memory":
            self.show_all_memories(query=str(slots.get("query", "")).strip())
            return True

        if intent == "show_memory_candidates":
            return self.handle_memory_candidate_command("查看待确认记忆")

        if intent == "accept_memory_candidate":
            return self.accept_memory_candidate(int(slots.get("candidate_id", 0)))

        if intent == "reject_memory_candidate":
            candidate_id = int(slots.get("candidate_id", 0))
            self.add_message(
                "Roxy",
                f"忽略后不会保存这条候选记忆。确定的话，请说“忽略记忆{candidate_id}”。",
            )
            return True

        if intent == "show_memory_conflicts":
            self.show_memory_conflicts()
            return True

        if intent == "resolve_memory_conflict":
            labels = {"use_new": "使用新记忆", "keep_old": "保留旧记忆", "keep_both": "两条都保留"}
            command = labels.get(str(slots.get("resolution", "")), "")
            if command:
                return self.handle_memory_command(
                    f"{command}{int(slots.get('conflict_id', 0))}"
                )
            return False

        if intent == "archive_memory":
            memory_id = int(slots.get("memory_id", 0))
            self.add_message(
                "Roxy",
                f"归档后它不会参与普通检索。确定的话，请说“归档记忆{memory_id}”。",
            )
            return True

        if intent == "restore_memory":
            return self.handle_memory_command(f"恢复记忆{int(slots.get('memory_id', 0))}")

        if intent == "delete_memory":
            memory_id = int(slots.get("memory_id", 0))
            return self.handle_memory_command(f"删除记忆{memory_id}")

        return False

    def handle_natural_plan_match(self, intent: str, query: str) -> bool:
        match = match_plan_task(query, self.plan_store.tasks())
        status = str(match.get("status", "not_found"))

        if status == "ambiguous":
            print(f"[Intent] ambiguous: {intent}", flush=True)
            candidates = match.get("candidates", [])
            lines = ["我找到几个可能的计划："]
            for task in candidates if isinstance(candidates, list) else []:
                if isinstance(task, dict):
                    lines.append(f"{task.get('id')}. {task.get('title')}")
            action = "完成" if intent == "complete_plan" else "删除"
            command = "完成计划" if intent == "complete_plan" else "删除计划"
            lines.append(f"你想{action}哪一个？可以说“{command}1”这样的编号命令。")
            self.add_message("Roxy", "\n".join(lines))
            return True

        task = match.get("task")
        if status != "matched" or not isinstance(task, dict):
            self.add_message("Roxy", f"我暂时没在今天的计划里找到“{query}”，可以先看看今天任务。")
            return True

        task_id = int(task.get("id", 0))
        if intent == "complete_plan":
            completed, changed = self.plan_store.complete_by_id(task_id)
            if completed is None:
                self.add_message("Roxy", "刚才匹配到的计划似乎已经变化了，可以再说一次。")
            elif not changed:
                self.add_message("Roxy", f"“{completed['title']}”之前已经完成了。")
            else:
                self.add_message("Roxy", f"收到，已经把“{completed['title']}”标记完成啦。")
                self.notify_plan_completed()
            return True

        self._pending_plan_delete_id = task_id
        self.add_message(
            "Roxy",
            f"我找到“{task['title']}”。删除后无法从当前计划恢复，确定的话请说“确认删除计划{task_id}”。",
        )
        return True

    def notify_plan_completed(self) -> None:
        if self.pet_controller is not None and hasattr(
            self.pet_controller, "notify_plan_completed"
        ):
            self.pet_controller.notify_plan_completed()

    def handle_dance_command(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_dance_command"):
            return False
        normalized_text = user_text.lower()
        if any(term in normalized_text for term in ("不想跳", "不要跳", "别跳")):
            return False
        if not any(trigger in normalized_text for trigger in ("跳舞", "跳一段", "跳一个舞", "dance")):
            return False
        response = AgentResponse(
            "completed",
            "好，我跳一小段。",
            client_actions=[
                ClientAction(
                    "play_dance",
                    {"dance_id": None},
                    request_id=f"local_{self._message_sequence}",
                    conversation_id=self.current_session_id,
                    source="fixed_command_compatibility",
                )
            ],
            conversation_id=self.current_session_id,
        )
        self._present_agent_response(response, intent="dance")
        return True

    def start_pet_thinking(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "start_thinking"):
            self.pet_controller.start_thinking()

    def stop_pet_thinking(self) -> None:
        if self.pet_controller is not None and hasattr(self.pet_controller, "stop_thinking"):
            self.pet_controller.stop_thinking()

    def build_llm_messages(self, user_text: str) -> List[Dict[str, str]]:
        self._capture_client_runtime_snapshot()
        return self.conversation_service.build_llm_messages(
            user_text,
            self.current_session_id,
        )

    def build_system_prompt(self, user_text: str = "") -> str:
        memory_context = self.build_memory_context()
        personality_context = self.build_personality_context()
        knowledge_context = self.build_knowledge_context(user_text)
        return "\n\n".join(
            [
                personality_context,
                memory_context,
                knowledge_context,
                "请用当前人格回复用户。使用自然纯文本，不使用 Markdown 或 **加粗** 标记。"
                "优先帮助用户学习、健康、项目、赚钱和长期成长。不要声称你接入了数据库或语音。",
            ]
        )

    def generate_session_summary(self, force: bool = False) -> str:
        if self._generating_summary:
            return self.chat_history_manager.get_summary(self.current_session_id)
        if not force and not self.chat_history_manager.should_summarize(
            self.current_session_id,
            message_threshold=int(
                self.pet_settings.get("summary_message_threshold", 30)
            ),
            character_threshold=int(
                self.pet_settings.get("summary_character_threshold", 12000)
            ),
        ):
            return ""

        self._generating_summary = True
        try:
            print("[Summary] generate", flush=True)
            # The rule summary is the stable fallback and avoids a second blocking model call.
            print("[Summary] fallback", flush=True)
            summary = self.chat_history_manager.generate_rule_summary(
                self.current_session_id
            )
            self.chat_history_manager.save_summary(
                self.current_session_id, summary
            )
            return summary
        finally:
            self._generating_summary = False

    def maybe_generate_session_summary(self) -> None:
        if not bool(self.pet_settings.get("auto_summary_enabled", True)):
            return
        self.generate_session_summary(force=False)

    def build_personality_context(self) -> str:
        return "\n".join(
            [
                "人格配置：",
                f"名称：{self.personality.get('name') or '未设置'}",
                f"性格：{self.personality.get('personality') or '未设置'}",
                f"喜好：{self.personality.get('likes') or '未设置'}",
                f"说话风格：{self.personality.get('speaking_style') or '未设置'}",
            ]
        )

    def available_personas(self) -> List[Dict[str, str]]:
        return self.persona_registry.available_personas()

    def _capture_client_runtime_snapshot(self) -> Dict[str, object]:
        """Copy Qt-owned facts on the UI thread; workers consume only this copy."""
        if QThread.currentThread() != self.thread():
            return dict(self._client_runtime_snapshot)
        observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        manager = getattr(self.pet_controller, "action_manager", None)
        manager_state = str(getattr(manager, "current_state", "unknown"))
        # The thinking decoration can temporarily override the manager label
        # while existing dance frames still render. Read the actual UI-owned
        # frame state rather than deriving animation playback from reply text.
        dancing = bool(getattr(self.pet_controller, "_dance_frames", []))
        pet_state = "dancing" if dancing else manager_state
        if pet_state not in {"idle", "thinking", "sleeping", "dancing", "reminding"}:
            pet_state = "unknown"
        dispatcher_available = bool(
            self.pet_controller is not None
            and (
                (
                    self.pet_settings.get("unified_client_action_dispatcher_enabled", True)
                    and getattr(self.pet_controller, "client_action_dispatcher", None) is not None
                )
                or self.pet_settings.get("legacy_direct_pet_action_enabled", False)
            )
            and manager is not None
        )
        capabilities = []
        for tool_name in self.tool_registry.names(enabled_only=True):
            tool = self.tool_registry.get(tool_name)
            capability = DEFAULT_CAPABILITY_REGISTRY.for_tool(tool_name)
            if tool is None or not tool.model_visible or capability is None:
                continue
            if capability.domain == "pet" and not dispatcher_available:
                continue
            capabilities.append({
                "name": capability.capability_id,
                "description": capability.description,
            })
        snapshot = {
            "client": "desktop",
            "source": "desktop_ui_snapshot",
            "observed_at": observed_at,
            "pet_available": self.pet_controller is not None,
            "pet_actions_available": dispatcher_available,
            "pet_state": pet_state,
            "manager_state": manager_state if manager_state in {
                "idle", "thinking", "sleeping", "dancing", "reminding"
            } else "unknown",
            "capabilities": capabilities,
            "conversation_id": self.current_session_id,
        }
        self._client_runtime_snapshot = snapshot
        references = self.conversation_service.state_manager.reference_context(self.current_session_id)
        prior = references.get("prior_client_action", {})
        reason = str(getattr(manager, "last_reason", ""))
        if (
            isinstance(prior, dict) and prior.get("name") == "play_dance"
            and prior.get("status") == "running" and not dancing
            and pet_state != "dancing" and reason in {"completed", "cancelled", "action_failed", "exception"}
        ):
            status = (
                "finished" if reason == "completed" else "cancelled" if reason == "cancelled" else "failed"
            )
            self.conversation_service.state_manager.observe_client_action_result(
                self.current_session_id,
                {
                    **prior,
                    "status": status,
                    "completed": status == "finished",
                    "reason_code": reason,
                    "state": pet_state,
                    "source": "desktop_state_snapshot",
                    "observed_at": observed_at,
                },
            )
        return dict(snapshot)

    def build_context_sections(self) -> Dict[str, object]:
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
        runtime = dict(self._client_runtime_snapshot)
        runtime_session_id = str(runtime.pop("conversation_id", ""))
        return {
            "client_runtime_session_id": runtime_session_id,
            "client_runtime": (
                "程序已核验的客户端快照（只读，仅说明采集时的能力和状态，不授权执行）：\n"
                + json.dumps(runtime, ensure_ascii=False, separators=(",", ":"))
                + "\n能力列表来自当前启用的程序目录；桌面动画只在 pet_actions_available=true 时可派发。"
                "这是当前桌面客户端，不代表 Local Web 也能播放动画。"
                "pet_state 是 observed_at 时采集的真实状态，不等于动画现在仍在运行。"
                "可以说明具备播放能力，但不能凭能力列表说已经播放；执行事实只认真实客户端结果。"
                "不要向用户展示JSON、内部字段或工具名称。"
                if runtime else "客户端本轮状态尚未取得，不要推测桌宠正在执行什么动作。"
            ),
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

    def build_memory_context(
        self, memories: Optional[List[Dict[str, object]]] = None
    ) -> str:
        nickname = self.memory_manager.preferred_name() or "未设置"
        source_items = (
            self.memory_manager.working_memories() if memories is None else memories
        )
        memory_lines = []
        for item in source_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("scope", "")) == "temporary_state":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                scope = str(item.get("scope", "stable_identity"))
                location = str(item.get("location", "") or "").strip()
                label = f"scope={scope}"
                if location:
                    label += f", location={location}"
                memory_lines.append(f"[{label}] {content}")

        lines = [
            ("当前有效长期记忆：" if memories is None
             else "本轮相关长期记忆（检索子集，不代表全库）："),
            f"独立称呼设置（不代表记忆是否存在）：{nickname}",
        ]
        if memory_lines:
            lines.append("记忆内容：")
            lines.append(
                "优先级：当前对话明确事实 > current_state > 有效计划 > "
                "stable_identity > historical_state > future_intent。"
            )
            for content in memory_lines:
                lines.append(f"- {content}")
        else:
            lines.append(
                "当前有效记录列表为空。" if memories is None
                else "本轮未召回相关记录；不能据此判断正式记忆是否为空。"
            )
        return "\n".join(lines)

    def build_knowledge_context(self, user_text: str = "") -> str:
        self.knowledge_files = self.scan_knowledge_files()
        return self.knowledge_manager.build_context(user_text, self.knowledge_files)

    def find_relevant_knowledge(self, user_text: str) -> List[Dict[str, str]]:
        return self.knowledge_manager.find_relevant(user_text, self.knowledge_files)

    def extract_knowledge_keywords(self, user_text: str) -> List[str]:
        return self.knowledge_manager.extract_keywords(user_text)

    def extract_knowledge_snippet(self, content: str, keywords: List[str]) -> str:
        return self.knowledge_manager.extract_snippet(content, keywords)

    def match_personality_rule(self, user_text: str) -> Optional[str]:
        # 规则人格：精确匹配用户输入，命中后直接返回配置回复。
        normalized_text = user_text.strip()
        rules = self.personality.get("rules", [])
        for rule in rules:
            if not isinstance(rule, dict):
                continue

            inputs = rule.get("inputs", [])
            if normalized_text in inputs:
                return self.render_reply(str(rule.get("reply", "")))

        return None

    def render_reply(self, template: str) -> str:
        nickname = self.memory_manager.preferred_name() or "你"
        values = {
            "nickname": nickname,
            "name": str(self.personality.get("name") or "未设置"),
            "personality": str(self.personality.get("personality") or "未设置"),
            "likes": str(self.personality.get("likes") or "未设置"),
            "speaking_style": str(self.personality.get("speaking_style") or "未设置"),
        }
        reply = template
        for key, value in values.items():
            reply = reply.replace("{" + key + "}", value)
        return reply

    def handle_personality_command(self, user_text: str) -> bool:
        if user_text != "查看人格":
            return False

        lines = [
            "当前人格配置：",
            f"名称：{self.personality.get('name') or '未设置'}",
            f"性格：{self.personality.get('personality') or '未设置'}",
            f"喜好：{self.personality.get('likes') or '未设置'}",
            f"说话风格：{self.personality.get('speaking_style') or '未设置'}",
        ]
        self.add_message("Roxy", "\n".join(lines))
        return True

    def handle_knowledge_command(self, user_text: str) -> bool:
        if user_text != "查看知识":
            return False

        self.knowledge_files = self.scan_knowledge_files()
        self.show_knowledge_summary()
        return True

    def show_knowledge_summary(self) -> None:
        if not self.knowledge_files:
            print("[KNOWLEDGE] loaded 0 files", flush=True)
            return

        lines = ["已加载知识："]
        for file_name in self.knowledge_file_names():
            lines.append(f"* {file_name}")
        self.add_message("Roxy", "\n".join(lines))

    def log_knowledge_summary(self) -> None:
        if not self.knowledge_files:
            print("[KNOWLEDGE] loaded 0 files", flush=True)
            return

        file_list = ", ".join(self.knowledge_file_names())
        print(f"[KNOWLEDGE] loaded {len(self.knowledge_files)} files: {file_list}", flush=True)

    def knowledge_file_names(self) -> List[str]:
        return [item.get("name", "") for item in self.knowledge_files if item.get("name")]

    def handle_memory_candidate_command(self, user_text: str) -> bool:
        text = user_text.strip()
        if text == "清空待确认记忆":
            self.confirmation_manager.create(
                "clear_memory_candidates", {}, "清空待确认记忆",
                scope=self.current_session_id,
            )
            self.add_message("Roxy", "这会忽略全部待确认记忆。确定的话，请回复“确认”或“取消”。")
            return True

        delete_match = re.fullmatch(r"删除候选记忆\s*(\d+)", text)
        if delete_match:
            candidate_id = int(delete_match.group(1))
            candidates = self.memory_service.list_candidates(status=None)
            candidate_ids = {
                int(item.get("id", 0))
                for item in candidates.data.get("candidates", [])
                if isinstance(item, dict)
            }
            if not candidates.success or candidate_id not in candidate_ids:
                self.add_message("Roxy", f"没有找到候选记忆 {candidate_id}。")
                return True
            self.confirmation_manager.create(
                "delete_memory_candidate", {"candidate_id": candidate_id}, "删除候选记忆",
                scope=self.current_session_id,
            )
            self.add_message("Roxy", "删除后无法恢复。确定的话，请回复“确认”或“取消”。")
            return True
        return False

    def show_pending_memory_candidates(self) -> None:
        operation = self.memory_service.list_candidates(status="pending")
        pending = operation.data.get("candidates", []) if operation.success else []
        if not operation.success:
            self.add_message("Roxy", operation.safe_message)
            return
        if not pending:
            self.add_message("Roxy", "现在没有待确认记忆。")
            return

        lines = ["待确认记忆："]
        for candidate in pending:
            category = CATEGORY_LABELS.get(
                str(candidate.get("category", "")),
                str(candidate.get("category", "其他")),
            )
            lines.append(
                f"{candidate.get('id')}. [{category}] {candidate.get('content', '')}"
            )
        lines.append("可以说“确认记忆1”或“忽略记忆1”。")
        self.add_message("Roxy", "\n".join(lines))

    def accept_memory_candidate(self, candidate_id: int, announce: bool = True) -> bool:
        operation = self.memory_service.accept_candidate(
            candidate_id,
            source="desktop_confirmation",
        )
        if not operation.success and operation.status != "conflict":
            if announce:
                self.add_message("Roxy", operation.safe_message)
            return False
        if announce:
            if operation.status == "success":
                self.add_message(
                    "Roxy",
                    f"好的，我已经长期记住：{operation.content_summary}",
                )
            elif operation.status == "conflict":
                conflict = operation.data.get("conflict", {})
                conflict = conflict if isinstance(conflict, dict) else {}
                self.add_message(
                    "Roxy",
                    "这条信息和之前的记忆有些不同，我先放进记忆冲突里。"
                    f"可以说“查看记忆冲突”处理编号 {conflict.get('id', '')}。",
                )
            elif operation.status == "already_processed":
                self.add_message("Roxy", operation.safe_message)
            else:
                self.add_message("Roxy", operation.safe_message)
        return operation.success or operation.status == "conflict"

    def reject_memory_candidate(self, candidate_id: int, announce: bool = True) -> bool:
        operation = self.memory_service.reject_candidate(
            candidate_id,
            source="desktop_confirmation",
        )
        if not operation.success:
            if announce:
                self.add_message("Roxy", operation.safe_message)
            return False
        if announce:
            self.add_message("Roxy", operation.safe_message)
        return True

    def handle_memory_command(self, user_text: str) -> bool:
        if self._retire_legacy_natural_handler("handle_memory_command"):
            return False
        # 先判断所有记忆命令，再进入普通聊天或首次昵称逻辑。
        text = user_text.strip()
        if text in {"我的记忆", "查看长期记忆", "你记得我什么"}:
            self.show_all_memories()
            return True

        if text == "查看项目记忆":
            self.show_all_memories(category="project")
            return True

        if text == "查看学习记忆":
            self.show_all_memories(category="learning")
            return True

        if text == "查看已归档记忆":
            self.show_all_memories(status="archived")
            return True

        if text.startswith(("搜索记忆：", "搜索记忆:")):
            query = text.split("：", 1)[1] if "：" in text else text.split(":", 1)[1]
            self.show_all_memories(query=query.strip())
            return True

        archive_match = re.fullmatch(r"归档记忆\s*(\d+)", text)
        if archive_match:
            operation = self.memory_service.archive_memory(int(archive_match.group(1)))
            self.add_message("Roxy", operation.safe_message)
            return True

        restore_match = re.fullmatch(r"恢复记忆\s*(\d+)", text)
        if restore_match:
            operation = self.memory_service.restore_memory(int(restore_match.group(1)))
            self.add_message("Roxy", operation.safe_message)
            return True

        confirm_delete = re.fullmatch(r"确认删除记忆\s*(\d+)", text)
        if confirm_delete:
            memory_id = int(confirm_delete.group(1))
            if self._pending_memory_delete_id != memory_id:
                self.add_message("Roxy", "这条删除请求已经失效，请重新说“删除记忆编号”。")
                return True
            self._pending_memory_delete_id = None
            operation = self.memory_service.delete_memory(memory_id)
            self.add_message("Roxy", operation.safe_message)
            return True

        delete_match = re.fullmatch(r"删除记忆\s*(\d+)", text)
        if delete_match:
            memory_id = int(delete_match.group(1))
            found = self.memory_service.get_memory(memory_id)
            if not found.success:
                self.add_message("Roxy", found.safe_message)
                return True
            self._pending_memory_delete_id = memory_id
            self.add_message(
                "Roxy",
                f"删除后无法从当前文件恢复。确定的话，请再说“确认删除记忆{memory_id}”。",
            )
            return True

        if text == "查看记忆冲突":
            self.show_memory_conflicts()
            return True

        conflict_match = re.fullmatch(
            r"(使用新记忆|保留旧记忆|两条都保留)\s*(\d+)", text
        )
        if conflict_match:
            resolution = {
                "使用新记忆": "use_new",
                "保留旧记忆": "keep_old",
                "两条都保留": "keep_both",
            }[conflict_match.group(1)]
            operation = self.memory_service.resolve_conflict(
                int(conflict_match.group(2)),
                resolution,
                source="desktop_confirmation",
            )
            self.add_message("Roxy", operation.safe_message)
            return True

        if text == "整理记忆":
            self.show_memory_organization()
            return True

        if self.forget_memory(user_text):
            return True

        # Agent 关闭或不可用时，明确记忆命令仍进入相同的候选审核流程。
        prefixes = ("记住：", "记住:")
        matched_prefix = None
        for prefix in prefixes:
            if user_text.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            return False

        content = user_text[len(matched_prefix) :].strip()
        if not content:
            self.add_message("Roxy", "可以告诉我要记住什么，例如：记住：我喜欢洛琪希")
            return True

        operation = self.memory_service.create_candidate(
            content,
            source_text=content,
            explicit=True,
            source="explicit_command",
        )
        if operation.status == "success":
            candidate = operation.data.get("candidate", {})
            candidate = candidate if isinstance(candidate, dict) else {}
            self.add_message(
                "Roxy",
                f"我先把它放进待审核记忆 {candidate.get('id', '')}。确认后才会长期保存。",
            )
        elif operation.status == "already_processed":
            self.add_message("Roxy", "这条和已有记忆或候选很接近，我没有重复加入。")
        elif operation.error_code == "disabled":
            self.add_message("Roxy", "当前已关闭记忆候选，我没有保存这条信息。")
        else:
            self.add_message("Roxy", operation.safe_message)
        return True

    def show_all_memories(
        self,
        *,
        status: str = "active",
        category: Optional[str] = None,
        query: str = "",
    ) -> None:
        operation = (
            self.memory_service.search_memories(
                query,
                status=status,
                category=category,
            )
            if query
            else self.memory_service.list_memories(
                status=status,
                category=category,
            )
        )
        if not operation.success:
            self.add_message("Roxy", operation.safe_message)
            return
        memories = operation.data.get("memories", [])
        if not memories:
            self.add_message("Roxy", "我现在还没有保存任何长期记忆。")
            return

        lines = ["我现在记得：" if status == "active" else "已归档记忆："]
        for item in memories:
            lines.append(
                f"{item.get('id')}. [{item.get('category', 'other')}] {item.get('content', '')}"
            )
        self.add_message("Roxy", "\n".join(lines))

    def show_memory_conflicts(self) -> None:
        operation = self.memory_service.list_conflicts(status="pending")
        if not operation.success:
            self.add_message("Roxy", operation.safe_message)
            return
        conflicts = operation.data.get("conflicts", [])
        if not conflicts:
            self.add_message("Roxy", "现在没有待处理的记忆冲突。")
            return
        lines = ["待处理的记忆冲突："]
        for conflict in conflicts:
            old = conflict.get("old_memory", {})
            old = old if isinstance(old, dict) else {}
            lines.append(
                f"{conflict.get('id')}. 旧：{old.get('content', '')} / 新：{conflict.get('new_content', '')}"
            )
        lines.append("可以说“使用新记忆1”“保留旧记忆1”或“两条都保留1”。")
        self.add_message("Roxy", "\n".join(lines))

    def show_memory_organization(self) -> None:
        operation = self.memory_service.organize_suggestions()
        if not operation.success:
            self.add_message("Roxy", operation.safe_message)
            return
        suggestions = operation.data.get("suggestions", {})
        lines = ["记忆整理建议（不会自动删除）："]
        lines.append(f"- 可能重复：{len(suggestions['duplicates'])} 组")
        lines.append(f"- 长期未使用：{len(suggestions['stale'])} 条")
        lines.append(f"- 待处理冲突：{len(suggestions['conflicts'])} 条")
        lines.append(f"- 内容过于模糊：{len(suggestions['vague'])} 条")
        self.add_message("Roxy", "\n".join(lines))

    def forget_memory(self, user_text: str) -> bool:
        prefixes = ("忘记：", "忘记:")
        matched_prefix = None
        for prefix in prefixes:
            if user_text.startswith(prefix):
                matched_prefix = prefix
                break

        if matched_prefix is None:
            return False

        content_to_forget = user_text[len(matched_prefix) :].strip()
        if not content_to_forget:
            self.add_message("Roxy", "可以告诉我要忘记什么，例如：忘记：我喜欢洛琪希")
            return True

        listed = self.memory_service.list_memories(status=None)
        matches = [
            item
            for item in listed.data.get("memories", [])
            if isinstance(item, dict)
            and str(item.get("content", "")) == content_to_forget
        ] if listed.success else []

        if not matches:
            self.add_message("Roxy", f"我没有找到这条记忆：{content_to_forget}")
            return True

        for item in matches:
            self.memory_service.delete_memory(int(item.get("id", 0)))
        self.memory = self.memory_manager.data
        self.add_message("Roxy", f"我已经忘记了：{content_to_forget}")
        return True

    def save_first_nickname(self, user_text: str) -> bool:
        # 如果还没有昵称，把用户第一条普通输入作为昵称保存。
        profile = self.memory.setdefault("profile", {})
        if profile.get("nickname"):
            return False

        nickname = extract_nickname(user_text)
        if not nickname:
            self.add_message("Roxy", "请告诉我你的昵称，例如：我叫煜")
            return True

        self.memory_manager.update_profile(
            {
                "nickname": nickname,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.memory = self.memory_manager.data
        self.add_message("Roxy", f"我记住你的昵称了，你叫{nickname}。")
        return True

    def default_reply(self) -> str:
        fallback_reply = str(self.personality.get("fallback_reply", "你好，{nickname}。"))
        return self.render_reply(fallback_reply)

    def add_message(
        self,
        sender: str,
        message: str,
        *,
        record_history: bool = True,
        intent: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        # 每条消息都显示本地时间戳，方便用户确认交互顺序。
        normalized_sender = sender.strip().lower()
        if normalized_sender not in {"you", "user", "你", "system", "系统"}:
            message = sanitize_public_reply(message)
            self._last_visible_assistant_message = message
        display_time = timestamp or datetime.now().strftime("%H:%M:%S")
        self.transcript.append(self.render_message_html(sender, message, display_time))
        self.scroll_to_bottom()
        role = (
            "user"
            if normalized_sender in {"you", "user", "你"}
            else "system"
            if normalized_sender in {"system", "系统"}
            else "assistant"
        )
        trace = self.development_log.current_trace()
        if trace and trace.session_id != self.current_session_id:
            trace = None
        if role == "assistant" and record_history and trace:
            self.development_log.event(
                trace, "reply_displayed",
                response_hash=hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest(),
                terminal=True,
            )
        if record_history:
            saved_metadata = dict(metadata or {})
            if trace and self.development_log.enabled:
                saved_metadata.update(trace.to_metadata())
            saved = self.chat_history_manager.add_message(
                self.current_session_id,
                role,
                message,
                intent=intent,
                metadata=saved_metadata,
            )
            with self.development_log.bind(trace):
                persisted = saved is not None and self.chat_history_manager.enabled
                self.development_log.event(
                    trace, "history_written", success=persisted,
                    message_id=str(saved.get("id", "")) if saved else "",
                    status="success" if persisted else "transient" if saved else "unavailable",
                    reason_code="history_disabled" if not self.chat_history_manager.enabled else
                    ("history_save_failed" if saved is None else ""),
                )
            session = self.chat_history_manager.get_session(self.current_session_id)
            if session is not None and not self._viewing_history:
                if session.get("session_kind") == "daily_learning":
                    day = str(session.get("business_date") or "")
                    self.session_title_label.setText(f"魔法修行日记 · {day}")
                else:
                    self.session_title_label.setText(str(session.get("title", "新对话")))
            if role == "assistant":
                self.maybe_generate_session_summary()

    def render_message_html(self, sender: str, message: str, timestamp: str) -> str:
        safe_message = html.escape(message).replace("\n", "<br>")
        safe_time = html.escape(timestamp)
        normalized_sender = sender.strip().lower()

        if normalized_sender in {"you", "user", "你"}:
            return (
                "<table width='84%' align='right' cellspacing='0' cellpadding='0'>"
                "<tr><td align='right' style='color:#778198; font-size:10px;'>"
                f"你 · {safe_time}</td></tr>"
                "<tr><td><table width='100%' bgcolor='#EEEAF7' cellspacing='0' cellpadding='9'>"
                "<tr><td style='color:#35415A; white-space:pre-wrap;'>"
                f"{safe_message}</td></tr></table></td></tr></table><br>"
            )

        if normalized_sender in {"system", "系统"}:
            return (
                "<div align='center' style='margin: 7px 0; color:#999999; font-size:11px;'>"
                f"{safe_time} · {safe_message}"
                "</div>"
            )

        return (
            "<table width='91%' align='left' cellspacing='0' cellpadding='0'>"
            "<tr><td width='40' valign='top'><img src='roxy-avatar' width='32' height='32'></td>"
            "<td><table width='100%' cellspacing='0' cellpadding='0'>"
            "<tr><td style='color:#778198; font-size:10px;'>"
            f"洛琪希 · {safe_time}</td></tr>"
            "<tr><td><table width='100%' bgcolor='#EAF1FA' cellspacing='0' cellpadding='9'>"
            "<tr><td style='color:#35415A; white-space:pre-wrap;'>"
            f"{safe_message}</td></tr></table></td></tr></table></td></tr></table><br>"
        )

    def scroll_to_bottom(self) -> None:
        # 新消息出现后自动滚动到底部。
        scroll_bar = self.transcript.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())


def _acceptance_root_from_argv(argv: List[str]) -> Optional[Path]:
    """Return an external, explicit data root for real desktop acceptance."""
    if "--acceptance-root" not in argv:
        return None
    index = argv.index("--acceptance-root")
    if index + 1 >= len(argv) or str(argv[index + 1]).startswith("--"):
        raise ValueError("--acceptance-root requires an absolute directory")
    root = Path(argv[index + 1])
    if not root.is_absolute():
        raise ValueError("--acceptance-root must be an absolute directory")
    resolved = root.resolve()
    project = PROJECT_ROOT.resolve()
    if resolved == project or project in resolved.parents or resolved in project.parents:
        raise ValueError("--acceptance-root must be separate from the project directory")
    return resolved


def _acceptance_desktop_pet(
    root: Path, logger, simulation_clock: Optional[SimulatedBusinessClock] = None,
    *, runtime: Optional[RuntimePaths] = None,
) -> DesktopPet:
    """Shared path-injected composition for Developer acceptance and Beta."""
    if runtime is not None:
        if Path(root).resolve() != runtime.data_root:
            raise ValueError("Composition root must match runtime data root")
        if runtime.is_packaged:
            if runtime.is_beta and simulation_clock is not None:
                raise ValueError("Beta cannot use a simulated business clock")
            _initialize_desktop_runtime(runtime)
    private_dir = root / "data" / "private"
    isolated_config = root / "data" / "pet_config.json"
    isolated_tips = root / "data" / "pet_tips.json"
    isolated_legacy_config = root / "config.json"
    if simulation_clock is not None and not isolated_config.exists():
        save_pet_settings({
            "test_mode": False,
            "auto_tips_enabled": False,
            "proactive_enabled": False,
            "evening_review_enabled": False,
            "idle_nudge_enabled": False,
            "pet_always_on_top": False,
        }, isolated_config)
    settings = load_pet_settings(isolated_config)
    if runtime is not None:
        settings = runtime.effective_settings(settings)
    growth_service = GrowthManager(
        private_dir,
        now_provider=simulation_clock.now if simulation_clock else datetime.now,
        timestamp_provider=datetime.now,
    )
    history_manager = ChatHistoryManager(
        private_dir,
        enabled=bool(settings.get("chat_history_enabled", True)),
    )
    memory_service = MemoryService.from_data_root(
        root,
        memory_file=root / "memory.json",
        private_dir=private_dir,
        default_data=({"version": 2, "profile": {}, "memories": []}
                      if runtime is not None and runtime.is_packaged else default_memory()),
        candidates_enabled=bool(settings.get("enable_memory_candidates", False)),
    )
    secret_store = SecretStore(root)
    usage_store = ModelUsageStore(root)
    llm_client = RoutedLLMClient(
        root,
        settings=settings,
        legacy_config=load_llm_config(isolated_legacy_config),
        secret_store=secret_store,
        usage_store=usage_store,
    )

    def chat_factory(pet_controller):
        return ChatWindow(
            pet_controller=pet_controller,
            growth_service=growth_service,
            chat_history_manager=history_manager,
            memory_service=memory_service,
            development_log=logger,
            llm_client=llm_client,
            pet_settings=settings,
            settings_config_file=isolated_config,
            personality_file=root / "data" / "roxy_personality.json",
            knowledge_dir=root / "data" / "knowledge",
            legacy_config_file=isolated_legacy_config,
            simulation_clock=simulation_clock,
            runtime=runtime,
        )

    return DesktopPet(
        chat_factory=chat_factory,
        growth_service=growth_service,
        chat_history_manager=history_manager,
        config_file=isolated_config,
        tips_file=isolated_tips,
        secret_store=secret_store,
        usage_store=usage_store,
        knowledge_dir=root / "data" / "knowledge",
        runtime=runtime,
    )


def _initialize_desktop_runtime(runtime: RuntimePaths) -> dict:
    study_defaults = ({"auto_tips_enabled": False, "proactive_enabled": False,
                       "evening_review_enabled": False, "idle_nudge_enabled": False,
                       "pet_always_on_top": False} if runtime.is_study else {})
    return initialize_runtime(runtime, {
        "data/pet_config.json": runtime.effective_settings({**DEFAULT_CONFIG, **DEFAULT_SETTINGS, **study_defaults}),
        "data/pet_tips.json": list(DEFAULT_TIPS),
        "data/roxy_personality.json": default_personality(),
        "config.json": default_config(),
    })


def main(*, runtime: Optional[RuntimePaths] = None, argv: Optional[List[str]] = None) -> int:
    ensure_windowed_streams()
    args = list(sys.argv[1:] if argv is None else argv)
    runtime = runtime or resolve_runtime("developer", args)
    if getattr(sys, "frozen", False) and not runtime.is_packaged:
        raise ValueError("Frozen applications require Beta mode")
    if runtime.is_study:
        if args != ["--open-chat"]:
            raise ValueError("Study entry accepts only its validated startup arguments")
        acceptance_root = runtime.data_root
    elif runtime.is_beta:
        if args:
            runtime = resolve_runtime("beta", args, resource_root=runtime.resource_root,
                                      program_root=runtime.program_root)
        acceptance_root = runtime.data_root
    else:
        acceptance_root = _acceptance_root_from_argv(args)
        if acceptance_root is not None:
            runtime = RuntimePaths(runtime.resource_root, runtime.program_root,
                                   acceptance_root, "developer", data_source="engineering")
    simulation_clock = None
    if runtime.is_study:
        first_date = None if runtime.writable("simulation_state.json").exists() else date.today().isoformat()
        simulation_clock = SimulatedBusinessClock(acceptance_root, first_date)
    elif "--simulate-days" in args:
        if acceptance_root is None:
            raise ValueError("--simulate-days requires --acceptance-root")
        if "--start-date" in args:
            index = args.index("--start-date")
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError("--start-date requires YYYY-MM-DD")
            start_date = args[index + 1]
        else:
            start_date = None
        simulation_clock = SimulatedBusinessClock(acceptance_root, start_date)
    elif "--start-date" in args:
        raise ValueError("--start-date requires --simulate-days")
    metadata = _initialize_desktop_runtime(runtime)
    logger = configure_development_log(
        runtime.writable("logs/development"),
        enabled=runtime.is_packaged or "--no-development-log" not in args,
    )
    started = logger.event(None, "run_started")
    if runtime.is_packaged and started is None:
        raise OSError("Required diagnostic log could not be written")
    run_metadata = write_run_metadata(runtime, metadata, logger)
    previous_exception_hook = sys.excepthook

    def report_exception(error_type, error, traceback):
        logger.record_exception(None, error)
        if runtime.is_packaged:
            show_startup_error(error, report_startup_failure(runtime, error))
        else:
            previous_exception_hook(error_type, error, traceback)

    sys.excepthook = report_exception
    try:
        app = QApplication([sys.argv[0], *args])
        app.setApplicationName("RoxyPlan")
        app.setQuitOnLastWindowClosed(False)
        install_titlebar_policy(app)

        print("[PET] using frontend.desktop_pet.DesktopPet", flush=True)
        pet = (
            _acceptance_desktop_pet(acceptance_root, logger, simulation_clock, runtime=runtime)
            if acceptance_root is not None
            else DesktopPet(
                chat_factory=lambda pet_controller: ChatWindow(
                    pet_controller=pet_controller, runtime=runtime,
                ), runtime=runtime,
            )
        )
        pet.runtime_metadata = run_metadata
        pet.show()

        if "--open-chat" in args:
            QTimer.singleShot(0, pet.open_chat_window)
        return app.exec()
    except Exception as error:
        logger.record_exception(None, error)
        raise
    finally:
        logger.event(None, "run_finished", terminal=True)
        sys.excepthook = previous_exception_hook


if __name__ == "__main__":
    raise SystemExit(main())
