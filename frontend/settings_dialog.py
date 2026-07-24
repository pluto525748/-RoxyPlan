import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modules.llm.provider_factory import ProviderFactory
from modules.llm.secret_store import SecretStore
from modules.llm.settings import (
    DEFAULT_DEEPSEEK_COMPLEX_MODEL,
    DEFAULT_DEEPSEEK_MODEL,
    ModelSettings,
)
from modules.llm.usage_store import ModelUsageStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_CONFIG_FILE = PROJECT_ROOT / "data" / "pet_config.json"
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"

DEFAULT_SETTINGS = {
    "pet_scale": 1.0,
    "pet_always_on_top": True,
    "auto_tips_enabled": True,
    "auto_tips_min_minutes": 5,
    "auto_tips_max_minutes": 15,
    "test_mode": False,
    "study_reminder_seconds_test": 10,
    "sleep_seconds_test": 30,
    "study_reminder_minutes": 25,
    "sleep_minutes": 20,
    "proactive_enabled": True,
    "proactive_interval_minutes": 10,
    "proactive_check_seconds_test": 30,
    "evening_review_enabled": True,
    "idle_nudge_enabled": True,
    "chat_history_enabled": True,
    "restore_last_session": True,
    "enable_memory_candidates": True,
    "recent_context_messages": 16,
    "smart_intent_enabled": True,
    "llm_intent_assist_enabled": False,
    "intent_auto_execute_confidence": 0.78,
    "agent_core_enabled": True,
    "agent_multi_step_enabled": True,
    "llm_planner_assist_enabled": False,
    "agent_max_steps": 3,
    "agent_medium_confidence": 0.82,
    "agent_confirm_high_risk": True,
    "interaction_coordinator_enabled": True,
    "unified_semantic_parser_enabled": True,
    "business_resolver_enabled": True,
    "deterministic_response_enabled": True,
    "action_batch_enabled": True,
    "interaction_ttl_seconds": 300,
    "auto_summary_enabled": True,
    "summary_message_threshold": 30,
    "summary_character_threshold": 12000,
    "pet_asset_path": "assets/pet/roxy_pet_transparent.png",
    "model_name": "qwen3:4b",
    "online_model_enabled": True,
    "model_mode": "auto",
    "default_model": DEFAULT_DEEPSEEK_MODEL,
    "complex_model": DEFAULT_DEEPSEEK_COMPLEX_MODEL,
    "deepseek_base_url": "https://api.deepseek.com",
    "enable_auto_escalation": True,
    "enable_thinking_for_complex_tasks": True,
    "offline_fallback_enabled": True,
    "offline_model": "qwen3:4b",
    "ollama_base_url": "http://localhost:11434/v1",
    "request_timeout_seconds": 120,
}


class ConnectionTestWorker(QObject):
    finished = Signal(dict)

    def __init__(self, api_key, config):
        super().__init__()
        self.api_key = str(api_key)
        self.config = dict(config)

    def run(self):
        try:
            settings = ModelSettings.from_mapping(self.config)
            provider = ProviderFactory.create_deepseek(settings, self.api_key)
            self.finished.emit(provider.health_check(force=True).to_dict())
        except Exception as error:
            self.finished.emit(
                {
                    "status": "offline",
                    "configured": bool(self.api_key),
                    "error_code": type(error).__name__,
                }
            )


def load_pet_settings(config_file=PET_CONFIG_FILE):
    config = dict(DEFAULT_SETTINGS)
    try:
        with Path(config_file).open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            config.update(loaded)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[SETTINGS] failed to load {config_file}: {error}")
    return config


def save_pet_settings(config, config_file=PET_CONFIG_FILE):
    path = Path(config_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")


class SettingsDialog(QDialog):
    settings_saved = Signal(dict)
    history_cleared = Signal()

    def __init__(
        self,
        parent=None,
        config_file=PET_CONFIG_FILE,
        chat_history_manager=None,
        secret_store=None,
        usage_store=None,
    ):
        super().__init__(parent)
        self.config_file = Path(config_file)
        self.config = load_pet_settings(self.config_file)
        self.chat_history_manager = chat_history_manager
        self.secret_store = secret_store or SecretStore(PROJECT_ROOT)
        self.usage_store = usage_store or ModelUsageStore(PROJECT_ROOT)
        self.connection_thread = None
        self.connection_worker = None

        self.setWindowTitle("RoxyPlan 设置")
        self.setMinimumWidth(390)
        self.resize(450, 720)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("桌宠设置")
        title.setStyleSheet("font: 600 18px 'Microsoft YaHei'; color: #333333;")
        layout.addWidget(title)

        form_container = QWidget()
        form = QFormLayout(form_container)
        form.setContentsMargins(4, 4, 10, 4)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.test_mode = QCheckBox("使用秒级测试间隔")
        form.addRow("测试模式", self.test_mode)

        self.always_on_top = QCheckBox("桌宠保持在窗口上方")
        form.addRow("始终置顶", self.always_on_top)

        self.auto_tips_enabled = QCheckBox("允许桌宠自动弹出提示")
        form.addRow("自动提示", self.auto_tips_enabled)

        self.auto_tips_min_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("提示最小间隔", self.auto_tips_min_minutes)

        self.auto_tips_max_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("提示最大间隔", self.auto_tips_max_minutes)

        self.proactive_enabled = QCheckBox("根据今日成长状态给出轻量提醒")
        form.addRow("开启主动陪伴", self.proactive_enabled)

        self.proactive_interval_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("主动提醒间隔", self.proactive_interval_minutes)

        self.evening_review_enabled = QCheckBox("晚间提醒进行今日复盘")
        form.addRow("晚间复盘提醒", self.evening_review_enabled)

        self.idle_nudge_enabled = QCheckBox("长时间未互动时轻声提醒")
        form.addRow("未互动提醒", self.idle_nudge_enabled)

        self.chat_history_enabled = QCheckBox("将新对话保存在本地私有目录")
        form.addRow("保存聊天历史", self.chat_history_enabled)

        self.restore_last_session = QCheckBox("启动聊天窗口时恢复最近会话")
        form.addRow("恢复最近对话", self.restore_last_session)

        self.enable_memory_candidates = QCheckBox("从稳定表达中生成待审核记忆，不直接写入长期记忆")
        form.addRow("记忆候选", self.enable_memory_candidates)

        self.recent_context_messages = self._spin_box(4, 40, " 条")
        form.addRow("最近上下文", self.recent_context_messages)

        self.smart_intent_enabled = QCheckBox("理解自然表达并调用现有功能")
        form.addRow("智能意图理解", self.smart_intent_enabled)

        self.llm_intent_assist_enabled = QCheckBox("在规则无法判断时请求当前模型辅助识别")
        form.addRow("模型意图辅助", self.llm_intent_assist_enabled)

        self.intent_auto_execute_confidence = QDoubleSpinBox()
        self.intent_auto_execute_confidence.setRange(0.5, 1.0)
        self.intent_auto_execute_confidence.setSingleStep(0.05)
        self.intent_auto_execute_confidence.setDecimals(2)
        form.addRow("自动执行置信度", self.intent_auto_execute_confidence)

        self.agent_core_enabled = QCheckBox("使用受限内部工具处理计划、记忆与动作")
        form.addRow("启用 Agent Core", self.agent_core_enabled)

        self.agent_multi_step_enabled = QCheckBox("允许一次请求按顺序执行多个内部工具")
        form.addRow("多步骤规划", self.agent_multi_step_enabled)

        self.llm_planner_assist_enabled = QCheckBox("规则无法规划时允许当前模型输出受限步骤")
        form.addRow("模型规划辅助", self.llm_planner_assist_enabled)

        self.agent_max_steps = self._spin_box(1, 3, " 步")
        form.addRow("最大执行步骤", self.agent_max_steps)

        self.agent_medium_confidence = QDoubleSpinBox()
        self.agent_medium_confidence.setRange(0.5, 1.0)
        self.agent_medium_confidence.setSingleStep(0.05)
        self.agent_medium_confidence.setDecimals(2)
        form.addRow("中风险置信度", self.agent_medium_confidence)

        self.agent_confirm_high_risk = QCheckBox("删除、归档和恢复操作始终确认")
        self.agent_confirm_high_risk.setChecked(True)
        self.agent_confirm_high_risk.setEnabled(False)
        form.addRow("高风险确认", self.agent_confirm_high_risk)

        self.auto_summary_enabled = QCheckBox("对话过长时生成本地会话摘要")
        form.addRow("自动会话摘要", self.auto_summary_enabled)

        clear_history_button = QPushButton("清空本地聊天历史")
        clear_history_button.clicked.connect(self._clear_chat_history)
        form.addRow("聊天隐私", clear_history_button)

        self.pet_scale = QDoubleSpinBox()
        self.pet_scale.setRange(0.65, 1.8)
        self.pet_scale.setSingleStep(0.05)
        self.pet_scale.setDecimals(2)
        self.pet_scale.setSuffix(" 倍")
        form.addRow("桌宠缩放", self.pet_scale)

        self.study_test_seconds = self._spin_box(1, 3600, " 秒")
        form.addRow("测试学习提醒", self.study_test_seconds)

        self.sleep_test_seconds = self._spin_box(1, 3600, " 秒")
        form.addRow("测试睡眠时间", self.sleep_test_seconds)

        self.study_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("学习提醒时间", self.study_minutes)

        self.sleep_minutes = self._spin_box(1, 1440, " 分钟")
        form.addRow("睡眠时间", self.sleep_minutes)

        asset_row = QWidget()
        asset_layout = QHBoxLayout(asset_row)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.setSpacing(8)
        self.asset_path = QLineEdit()
        browse_button = QPushButton("浏览")
        browse_button.clicked.connect(self._browse_asset)
        asset_layout.addWidget(self.asset_path, 1)
        asset_layout.addWidget(browse_button)
        form.addRow("桌宠图片", asset_row)

        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("例如 qwen3:4b")
        form.addRow("模型名称", self.model_name)

        model_section = QLabel("在线模型服务")
        model_section.setStyleSheet("font: 600 15px 'Microsoft YaHei'; color: #4F5F91;")
        form.addRow(model_section)

        self.online_model_enabled = QCheckBox("优先使用 DeepSeek 在线模型")
        form.addRow("在线模型", self.online_model_enabled)

        self.model_mode = QComboBox()
        self.model_mode.addItem("省钱", "economy")
        self.model_mode.addItem("自动", "auto")
        self.model_mode.addItem("高质量", "quality")
        form.addRow("模型模式", self.model_mode)

        self.default_model = QLineEdit()
        self.default_model.setPlaceholderText(DEFAULT_DEEPSEEK_MODEL)
        form.addRow("默认模型", self.default_model)

        self.complex_model = QLineEdit()
        self.complex_model.setPlaceholderText(DEFAULT_DEEPSEEK_COMPLEX_MODEL)
        form.addRow("复杂模型", self.complex_model)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空则保留现有 Key")
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        show_key_button = QPushButton("显示")
        show_key_button.setCheckable(True)
        show_key_button.toggled.connect(
            lambda checked: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        clear_key_button = QPushButton("清除")
        clear_key_button.clicked.connect(self._clear_api_key)
        key_layout.addWidget(self.api_key, 1)
        key_layout.addWidget(show_key_button)
        key_layout.addWidget(clear_key_button)
        form.addRow("DeepSeek API Key", key_row)

        self.api_key_status = QLabel()
        form.addRow("Key 状态", self.api_key_status)

        test_connection_button = QPushButton("测试 DeepSeek 连接")
        test_connection_button.clicked.connect(self._test_connection)
        self.connection_status = QLabel("尚未测试")
        connection_row = QWidget()
        connection_layout = QHBoxLayout(connection_row)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.addWidget(test_connection_button)
        connection_layout.addWidget(self.connection_status, 1)
        form.addRow("连接", connection_row)

        self.enable_auto_escalation = QCheckBox("复杂任务自动使用复杂模型")
        form.addRow("自动升级", self.enable_auto_escalation)

        self.enable_thinking = QCheckBox("深度规划等复杂任务允许思考模式")
        form.addRow("复杂任务思考", self.enable_thinking)

        self.offline_fallback_enabled = QCheckBox("DeepSeek 不可用时使用本地 Ollama")
        form.addRow("本地降级", self.offline_fallback_enabled)

        self.offline_model = QLineEdit()
        self.offline_model.setPlaceholderText("qwen3:4b")
        form.addRow("Ollama 模型", self.offline_model)

        self.request_timeout_seconds = self._spin_box(5, 600, " 秒")
        form.addRow("请求超时", self.request_timeout_seconds)

        usage_row = QWidget()
        usage_layout = QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        self.usage_summary_label = QLabel()
        clear_usage_button = QPushButton("清空")
        clear_usage_button.clicked.connect(self._clear_usage)
        usage_layout.addWidget(self.usage_summary_label, 1)
        usage_layout.addWidget(clear_usage_button)
        form.addRow("本地使用统计", usage_row)

        open_knowledge_button = QPushButton("打开知识库文件夹")
        open_knowledge_button.clicked.connect(self._open_knowledge_folder)
        form.addRow("本地知识库", open_knowledge_button)
        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(form_container)
        layout.addWidget(scroll_area, 1)

        note = QLabel("计时、提示、缩放和置顶设置会尽量立即生效；桌宠图片和模型名称建议重启 RoxyPlan 后生效。")
        note.setWordWrap(True)
        note.setStyleSheet("font: 12px 'Microsoft YaHei'; color: #777777;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet(
            "QDialog { background: #F7F4FF; }"
            "QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget { background: #F7F4FF; border: none; }"
            "QLabel, QCheckBox { font: 13px 'Microsoft YaHei'; color: #333333; }"
            "QLineEdit, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #D8CCFF; "
            "border-radius: 6px; padding: 6px; }"
            "QPushButton { background: #8EA7FF; color: white; border: none; "
            "border-radius: 6px; padding: 7px 12px; }"
            "QPushButton:hover { background: #7894F8; }"
        )

    @staticmethod
    def _spin_box(minimum, maximum, suffix):
        spin_box = QSpinBox()
        spin_box.setRange(minimum, maximum)
        spin_box.setSuffix(suffix)
        return spin_box

    def _load_values(self):
        self.test_mode.setChecked(bool(self.config.get("test_mode", False)))
        self.always_on_top.setChecked(bool(self.config.get("pet_always_on_top", True)))
        self.auto_tips_enabled.setChecked(bool(self.config.get("auto_tips_enabled", True)))
        self.auto_tips_min_minutes.setValue(
            int(self.config.get("auto_tips_min_minutes", 5))
        )
        self.auto_tips_max_minutes.setValue(
            int(self.config.get("auto_tips_max_minutes", 15))
        )
        self.proactive_enabled.setChecked(
            bool(self.config.get("proactive_enabled", True))
        )
        self.proactive_interval_minutes.setValue(
            int(self.config.get("proactive_interval_minutes", 10))
        )
        self.evening_review_enabled.setChecked(
            bool(self.config.get("evening_review_enabled", True))
        )
        self.idle_nudge_enabled.setChecked(
            bool(self.config.get("idle_nudge_enabled", True))
        )
        self.chat_history_enabled.setChecked(
            bool(self.config.get("chat_history_enabled", True))
        )
        self.restore_last_session.setChecked(
            bool(self.config.get("restore_last_session", True))
        )
        self.enable_memory_candidates.setChecked(
            bool(self.config.get("enable_memory_candidates", True))
        )
        self.recent_context_messages.setValue(
            int(self.config.get("recent_context_messages", 16))
        )
        self.smart_intent_enabled.setChecked(
            bool(self.config.get("smart_intent_enabled", True))
        )
        self.llm_intent_assist_enabled.setChecked(
            bool(self.config.get("llm_intent_assist_enabled", False))
        )
        self.intent_auto_execute_confidence.setValue(
            float(self.config.get("intent_auto_execute_confidence", 0.78))
        )
        self.agent_core_enabled.setChecked(
            bool(self.config.get("agent_core_enabled", True))
        )
        self.agent_multi_step_enabled.setChecked(
            bool(self.config.get("agent_multi_step_enabled", True))
        )
        self.llm_planner_assist_enabled.setChecked(
            bool(self.config.get("llm_planner_assist_enabled", False))
        )
        self.agent_max_steps.setValue(int(self.config.get("agent_max_steps", 3)))
        self.agent_medium_confidence.setValue(
            float(self.config.get("agent_medium_confidence", 0.82))
        )
        self.agent_confirm_high_risk.setChecked(True)
        self.auto_summary_enabled.setChecked(
            bool(self.config.get("auto_summary_enabled", True))
        )
        self.pet_scale.setValue(float(self.config.get("pet_scale", 1.0)))
        self.study_test_seconds.setValue(
            int(self.config.get("study_reminder_seconds_test", 10))
        )
        self.sleep_test_seconds.setValue(
            int(self.config.get("sleep_seconds_test", 30))
        )
        self.study_minutes.setValue(int(self.config.get("study_reminder_minutes", 25)))
        self.sleep_minutes.setValue(int(self.config.get("sleep_minutes", 20)))
        self.asset_path.setText(str(self.config.get("pet_asset_path", "")))
        self.model_name.setText(str(self.config.get("model_name", "qwen3:4b")))
        self.online_model_enabled.setChecked(
            bool(self.config.get("online_model_enabled", True))
        )
        mode = str(self.config.get("model_mode", "auto"))
        mode_index = self.model_mode.findData(mode)
        self.model_mode.setCurrentIndex(mode_index if mode_index >= 0 else 1)
        self.default_model.setText(
            str(self.config.get("default_model", DEFAULT_DEEPSEEK_MODEL))
        )
        self.complex_model.setText(
            str(self.config.get("complex_model", DEFAULT_DEEPSEEK_COMPLEX_MODEL))
        )
        self.enable_auto_escalation.setChecked(
            bool(self.config.get("enable_auto_escalation", True))
        )
        self.enable_thinking.setChecked(
            bool(self.config.get("enable_thinking_for_complex_tasks", True))
        )
        self.offline_fallback_enabled.setChecked(
            bool(self.config.get("offline_fallback_enabled", True))
        )
        self.offline_model.setText(
            str(self.config.get("offline_model", self.config.get("model_name", "qwen3:4b")))
        )
        self.request_timeout_seconds.setValue(
            int(self.config.get("request_timeout_seconds", 120))
        )
        self._refresh_secret_status()
        self._refresh_usage_summary()

    def _refresh_secret_status(self):
        status = self.secret_store.status()
        if status["configured"]:
            source = "环境变量" if status["source"] == "environment" else "本地私有文件"
            self.api_key_status.setText(f"已配置（{source}，{status['masked']}）")
        else:
            self.api_key_status.setText("未配置，将使用本地 Ollama")

    def _test_connection(self):
        if self.connection_thread is not None:
            return
        key = self.api_key.text().strip() or self.secret_store.get_deepseek_key()[0]
        if not key:
            self.connection_status.setText("未配置 API Key")
            return
        self.connection_status.setText("测试中...")
        self.connection_thread = QThread(self)
        self.connection_worker = ConnectionTestWorker(key, self._model_config_values())
        self.connection_worker.moveToThread(self.connection_thread)
        self.connection_thread.started.connect(self.connection_worker.run)
        self.connection_worker.finished.connect(self._connection_finished)
        self.connection_worker.finished.connect(self.connection_thread.quit)
        self.connection_thread.finished.connect(self._release_connection_thread)
        self.connection_thread.start()

    def _connection_finished(self, status):
        state = str(status.get("status", "offline"))
        code = str(status.get("error_code", ""))
        self.connection_status.setText("连接正常" if state == "online" else f"连接失败（{code or 'offline'}）")

    def _release_connection_thread(self):
        if self.connection_worker is not None:
            self.connection_worker.deleteLater()
        if self.connection_thread is not None:
            self.connection_thread.deleteLater()
        self.connection_worker = None
        self.connection_thread = None

    def _clear_api_key(self):
        answer = QMessageBox.question(
            self,
            "清除 API Key",
            "确定清除本地私有文件中的 DeepSeek API Key 吗？环境变量不会被修改。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.secret_store.clear_deepseek_key()
            self.api_key.clear()
            self._refresh_secret_status()

    def _refresh_usage_summary(self):
        totals = self.usage_store.summary().get("totals", {})
        requests = sum(int(item.get("requests", 0)) for item in totals.values() if isinstance(item, dict))
        tokens = sum(int(item.get("total_tokens", 0)) for item in totals.values() if isinstance(item, dict))
        self.usage_summary_label.setText(f"{requests} 次请求 / {tokens} tokens")

    def _clear_usage(self):
        if self.usage_store.clear():
            self._refresh_usage_summary()

    def _model_config_values(self):
        return {
            "online_model_enabled": self.online_model_enabled.isChecked(),
            "model_mode": self.model_mode.currentData(),
            "default_model": self.default_model.text().strip() or DEFAULT_DEEPSEEK_MODEL,
            "complex_model": self.complex_model.text().strip() or DEFAULT_DEEPSEEK_COMPLEX_MODEL,
            "deepseek_base_url": str(self.config.get("deepseek_base_url", "https://api.deepseek.com")),
            "enable_auto_escalation": self.enable_auto_escalation.isChecked(),
            "enable_thinking_for_complex_tasks": self.enable_thinking.isChecked(),
            "offline_fallback_enabled": self.offline_fallback_enabled.isChecked(),
            "offline_model": self.offline_model.text().strip() or "qwen3:4b",
            "ollama_base_url": str(self.config.get("ollama_base_url", "http://localhost:11434/v1")),
            "request_timeout_seconds": self.request_timeout_seconds.value(),
        }

    def _browse_asset(self):
        initial_path = self.asset_path.text().strip()
        if initial_path and not Path(initial_path).is_absolute():
            initial_path = str(PROJECT_ROOT / initial_path)
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择桌宠图片",
            initial_path or str(PROJECT_ROOT / "assets" / "pet"),
            "图片文件 (*.png *.jpg *.jpeg *.webp *.svg)",
        )
        if selected:
            selected_path = Path(selected)
            try:
                selected_path = selected_path.relative_to(PROJECT_ROOT)
            except ValueError:
                pass
            self.asset_path.setText(selected_path.as_posix())

    def _open_knowledge_folder(self):
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(KNOWLEDGE_DIR)))

    def _clear_chat_history(self):
        if self.chat_history_manager is None:
            QMessageBox.information(self, "聊天历史", "当前没有可用的聊天历史管理器。")
            return
        answer = QMessageBox.question(
            self,
            "清空聊天历史",
            "确定清空全部本地聊天会话和会话摘要吗？\n长期记忆和成长数据不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.chat_history_manager.clear_history(confirmed=True):
            self.history_cleared.emit()
            QMessageBox.information(self, "聊天历史", "本地聊天历史已清空。")

    def _save(self):
        asset_path = self.asset_path.text().strip()
        model_name = self.model_name.text().strip()
        if not asset_path or not model_name:
            QMessageBox.warning(self, "无法保存", "桌宠图片路径和模型名称不能为空。")
            return
        if self.auto_tips_max_minutes.value() < self.auto_tips_min_minutes.value():
            QMessageBox.warning(self, "无法保存", "提示最大间隔不能小于最小间隔。")
            return
        if not self.default_model.text().strip() or not self.complex_model.text().strip():
            QMessageBox.warning(self, "无法保存", "默认模型和复杂模型名称不能为空。")
            return

        entered_key = self.api_key.text().strip()
        if entered_key and not self.secret_store.save_deepseek_key(entered_key):
            QMessageBox.critical(self, "保存失败", "API Key 无法写入本地私有配置。")
            return

        self.config.update(
            {
                "pet_scale": round(self.pet_scale.value(), 2),
                "pet_always_on_top": self.always_on_top.isChecked(),
                "auto_tips_enabled": self.auto_tips_enabled.isChecked(),
                "auto_tips_min_minutes": self.auto_tips_min_minutes.value(),
                "auto_tips_max_minutes": self.auto_tips_max_minutes.value(),
                "proactive_enabled": self.proactive_enabled.isChecked(),
                "proactive_interval_minutes": self.proactive_interval_minutes.value(),
                "proactive_check_seconds_test": int(
                    self.config.get("proactive_check_seconds_test", 30)
                ),
                "evening_review_enabled": self.evening_review_enabled.isChecked(),
                "idle_nudge_enabled": self.idle_nudge_enabled.isChecked(),
                "chat_history_enabled": self.chat_history_enabled.isChecked(),
                "restore_last_session": self.restore_last_session.isChecked(),
                "enable_memory_candidates": self.enable_memory_candidates.isChecked(),
                "recent_context_messages": self.recent_context_messages.value(),
                "smart_intent_enabled": self.smart_intent_enabled.isChecked(),
                "llm_intent_assist_enabled": self.llm_intent_assist_enabled.isChecked(),
                "intent_auto_execute_confidence": round(
                    self.intent_auto_execute_confidence.value(), 2
                ),
                "agent_core_enabled": self.agent_core_enabled.isChecked(),
                "agent_multi_step_enabled": self.agent_multi_step_enabled.isChecked(),
                "llm_planner_assist_enabled": self.llm_planner_assist_enabled.isChecked(),
                "agent_max_steps": self.agent_max_steps.value(),
                "agent_medium_confidence": round(
                    self.agent_medium_confidence.value(), 2
                ),
                "agent_confirm_high_risk": True,
                "auto_summary_enabled": self.auto_summary_enabled.isChecked(),
                "summary_message_threshold": int(
                    self.config.get("summary_message_threshold", 30)
                ),
                "summary_character_threshold": int(
                    self.config.get("summary_character_threshold", 12000)
                ),
                "test_mode": self.test_mode.isChecked(),
                "study_reminder_seconds_test": self.study_test_seconds.value(),
                "sleep_seconds_test": self.sleep_test_seconds.value(),
                "study_reminder_minutes": self.study_minutes.value(),
                "sleep_minutes": self.sleep_minutes.value(),
                "pet_asset_path": asset_path,
                "model_name": model_name,
                **self._model_config_values(),
            }
        )
        try:
            save_pet_settings(self.config, self.config_file)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return

        print(f"[SETTINGS] saved {self.config_file}")
        self.settings_saved.emit(dict(self.config))
        QMessageBox.information(
            self,
            "设置已保存",
            "测试模式、主动陪伴、学习提醒、睡眠计时、自动提示、缩放和置顶设置已尽量立即生效。\n"
            "模型服务、计时、主动陪伴、自动提示、缩放和置顶设置会尽量立即生效。\n"
            "桌宠图片路径建议重启 RoxyPlan 后生效。",
        )
        self.accept()
