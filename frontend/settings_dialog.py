import json
from pathlib import Path
from string import Template

from PySide6.QtCore import QObject, QRectF, QSize, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
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
from modules.feature_flags import SEMANTIC_FLAG_DEFAULTS
from modules.persona_registry import PersonaRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PET_CONFIG_FILE = PROJECT_ROOT / "data" / "pet_config.json"
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
UI_ASSET_DIR = PROJECT_ROOT / "assets" / "ui" / "settings"

# 奶白 / 浅蓝 / 雾紫 配色，与 UI 素材包 V1 的视觉方向一致。
# 图案素材由 tools/build_settings_ui_assets.py 从素材包图集切片生成。
SETTINGS_COLORS = {
    "bg": "#F1F2FA",
    "card": "#FFFFFF",
    "card_border": "#E5E8F6",
    "title": "#303B5C",
    "text": "#3C4767",
    "muted": "#8A91AB",
    "accent": "#7C8CE4",
    "accent_hover": "#6C7BDB",
    "accent_pressed": "#5F6ECD",
    "accent_soft": "#F1F3FE",
    "accent_soft_pressed": "#E6E9FC",
    "accent_border": "#C7CFF3",
    "accent_text": "#5A68C0",
    "input_border": "#DCE0F0",
    "button_text": "#5A67A8",
    "danger_soft": "#FCE9EE",
    "danger_soft_hover": "#F9DCE3",
    "danger_text": "#C2556B",
    "danger_border": "#F5D2DA",
    "scroll": "#C9CFE6",
    "scroll_hover": "#B3BBD9",
    "disabled_bg": "#F4F5FB",
    "disabled_text": "#AFB5CC",
    "toggle_off": "#D9DDEC",
    "toggle_off_hover": "#C9CFE6",
    "toggle_off_disabled": "#E7E9F4",
    "toggle_on_disabled": "#C3CAE8",
}

SETTINGS_QSS = Template(
    """
QDialog { background: $bg; }
QLabel { font-family: 'Microsoft YaHei'; font-size: 13px; color: $text; }
QLabel#dialogTitle { font-size: 20px; font-weight: 600; color: $title; }
QLabel#dialogSubtitle { font-size: 12px; color: $muted; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; color: $title; }
QLabel#rowLabel { font-size: 13px; font-weight: 600; color: $text; }
QLabel#rowHint { font-size: 12px; color: $muted; }
QLabel#footerNote { font-size: 12px; color: $muted; }
QFrame#card { background: $card; border: 1px solid $card_border; border-radius: 14px; }
QFrame#headerLine { border: none; border-top: 1px solid $card_border; max-height: 1px; }
QScrollArea#settingsScroll { background: transparent; border: none; }
QScrollArea#settingsScroll > QWidget > QWidget { background: transparent; }
QWidget#settingsContent { background: transparent; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $card;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 4px 10px;
    min-height: 22px;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
    color: $text;
    selection-background-color: $accent;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: $accent; }
/* 数字输入框右侧的加减按钮：默认分隔线偏硬，这里改成与输入框同色的细线。 */
QSpinBox, QDoubleSpinBox { padding-right: 26px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    height: 15px;
    margin: 1px 1px 0 0;
    border: none;
    border-left: 1px solid $input_border;
    border-top-right-radius: 7px;
    background: transparent;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    height: 15px;
    margin: 0 1px 1px 0;
    border: none;
    border-left: 1px solid $input_border;
    border-bottom-right-radius: 7px;
    background: transparent;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("$spin_up");
    width: 8px;
    height: 5px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("$spin_down");
    width: 8px;
    height: 5px;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    image: none;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: $accent_soft;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: $disabled_bg;
    color: $disabled_text;
}
/* 下拉框：自定义 drop-down 后 Qt 不再绘制默认箭头，这里补上箭头图片。 */
QComboBox { padding-right: 30px; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url("$spin_down");
    width: 8px;
    height: 5px;
}
QComboBox::down-arrow:disabled { image: none; }
QComboBox QAbstractItemView {
    background: $card;
    border: 1px solid $card_border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QCheckBox { font-family: 'Microsoft YaHei'; font-size: 13px; color: $text; spacing: 8px; }
QPushButton {
    background: $card;
    color: $button_text;
    border: 1px solid $input_border;
    border-radius: 9px;
    padding: 6px 14px;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
}
QPushButton:hover { background: $accent_soft; border-color: $accent_border; }
QPushButton:pressed { background: $accent_soft_pressed; }
QPushButton:disabled { background: $disabled_bg; color: $disabled_text; border-color: $card_border; }
QPushButton:checked { background: $accent_soft; border-color: $accent; color: $accent_text; }
QPushButton#primaryButton {
    background: $accent;
    color: #FFFFFF;
    border: none;
    padding: 7px 22px;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: $accent_hover; }
QPushButton#primaryButton:pressed { background: $accent_pressed; }
QPushButton#primaryButton:disabled { background: #C7CEEB; color: #F4F5FB; }
QPushButton#dangerButton { background: $danger_soft; color: $danger_text; border: 1px solid $danger_border; }
QPushButton#dangerButton:hover { background: $danger_soft_hover; border-color: $danger_text; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
).substitute(
    {
        **SETTINGS_COLORS,
        # 数字输入框的箭头：Qt 在自定义 QSpinBox 按钮后不再绘制默认箭头，
        # 这里使用 tools/build_settings_ui_assets.py 生成的图片。
        "spin_up": (UI_ASSET_DIR / "spin_up.png").as_posix(),
        "spin_down": (UI_ASSET_DIR / "spin_down.png").as_posix(),
    }
)

DEFAULT_SETTINGS = {
    **SEMANTIC_FLAG_DEFAULTS,
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
    "auto_complete_yesterday_review": True,
    "idle_nudge_enabled": True,
    "chat_history_enabled": True,
    "restore_last_session": True,
    "first_use_hint_seen": False,
    "enable_memory_candidates": False,
    "active_persona_id": "roxy",
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
    "plan_postcondition_enabled": True,
    "interaction_diagnostics_enabled": False,
    "client_action_claim_guard_enabled": True,
    "pet_action_state_machine_enabled": True,
    "interaction_ttl_seconds": 300,
    "interaction_continuation_ttl_seconds": 1200,
    "interaction_confirmation_ttl_seconds": 180,
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


class ToggleSwitch(QCheckBox):
    """iOS 风格开关；保留 QCheckBox 的 setChecked / isChecked / toggled 接口。"""

    TRACK_WIDTH = 46
    TRACK_HEIGHT = 26
    KNOB_MARGIN = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.TRACK_WIDTH, self.TRACK_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self):
        return QSize(self.TRACK_WIDTH, self.TRACK_HEIGHT)

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = track.height() / 2.0

        if not self.isEnabled():
            track_color = QColor(
                SETTINGS_COLORS["toggle_on_disabled"]
                if self.isChecked()
                else SETTINGS_COLORS["toggle_off_disabled"]
            )
        elif self.isChecked():
            track_color = QColor(SETTINGS_COLORS["accent"])
        else:
            track_color = QColor(
                SETTINGS_COLORS["toggle_off_hover"]
                if self.underMouse()
                else SETTINGS_COLORS["toggle_off"]
            )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)

        diameter = track.height() - 2.0 * self.KNOB_MARGIN
        knob_x = (
            track.right() - self.KNOB_MARGIN - diameter
            if self.isChecked()
            else track.left() + self.KNOB_MARGIN
        )
        knob = QRectF(knob_x, track.top() + self.KNOB_MARGIN, diameter, diameter)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob)

        if self.hasFocus():
            focus_color = QColor(SETTINGS_COLORS["accent"])
            focus_color.setAlpha(90)
            pen = QPen(focus_color)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, radius, radius)
        painter.end()


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


# 首次使用提示只看一次；标志位跟随各实例自己的 pet_config.json。
FIRST_USE_HINT_SEEN_KEY = "first_use_hint_seen"


def first_use_hint_seen(config_file=PET_CONFIG_FILE):
    """读取实例配置里的“已看过”标志；缺失、损坏或非对象时按未看过处理。"""
    try:
        with Path(config_file).open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(loaded, dict) and bool(loaded.get(FIRST_USE_HINT_SEEN_KEY))


def mark_first_use_hint_seen(config_file=PET_CONFIG_FILE):
    """只追加“已看过”标志，保留其他已知、未知和隐藏配置。

    配置损坏时返回 False 且不覆盖原文件；配置不存在时创建只含该标志的文件。
    不读取、合并或迁移其他实例的配置。
    """
    path = Path(config_file)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(loaded, dict):
            return False
        config = loaded
    else:
        config = {}
    config[FIRST_USE_HINT_SEEN_KEY] = True
    try:
        save_pet_settings(config, path)
    except OSError:
        # 写不进去时本次运行仍由内存标志去重，下次启动重新提示。
        return False
    return True


class SettingsDialog(QDialog):
    settings_saved = Signal(dict)
    history_cleared = Signal()
    open_today_growth_requested = Signal()
    feedback_exported = Signal(str)
    # 帮助与退出入口只发信号，由调用方处理；设置窗口不自己打开帮助窗口，
    # 也不自己退出程序，避免绕开桌宠窗口和聊天的退出流程。
    usage_help_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        parent=None,
        config_file=PET_CONFIG_FILE,
        chat_history_manager=None,
        secret_store=None,
        usage_store=None,
        knowledge_dir=None,
        feedback_export=None,
    ):
        super().__init__(parent)
        self.config_file = Path(config_file)
        self.config = load_pet_settings(self.config_file)
        self.chat_history_manager = chat_history_manager
        self.secret_store = secret_store or SecretStore(PROJECT_ROOT)
        self.usage_store = usage_store or ModelUsageStore(PROJECT_ROOT)
        self.knowledge_dir = Path(knowledge_dir or KNOWLEDGE_DIR)
        # 反馈导出服务由调用方注入（含 data_root 与运行元信息）。这里不推断路径，
        # 未注入时入口明确不可用，而不是回退到项目目录。
        self.feedback_export = feedback_export
        self.feedback_dialog = None
        self.connection_thread = None
        self.connection_worker = None

        self.setWindowTitle("RoxyPlan 设置")
        self.setMinimumSize(430, 500)
        self.resize(620, 660)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_header())

        # “打开今日成长面板”按钮已从可见布局移除（2026-10-12 设置界面整理）。
        # 控件和信号保留，现有调用方（桌宠窗口、回归测试）仍可连接和点击。
        self.open_today_growth_button = QPushButton("打开今日成长面板", self)
        self.open_today_growth_button.clicked.connect(
            self.open_today_growth_requested.emit
        )
        self.open_today_growth_button.setVisible(False)

        self._build_hidden_parameters()

        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 2)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._build_appearance_card())
        content_layout.addWidget(self._build_companion_card())
        content_layout.addWidget(self._build_model_card())
        content_layout.addWidget(self._build_privacy_card())
        content_layout.addWidget(self._build_help_card())
        content_layout.addStretch(1)
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, 1)

        note = QLabel(
            "计时、提示、缩放和置顶设置会尽量立即生效；"
            "桌宠图片和模型名称建议重启 RoxyPlan 后生效。"
        )
        note.setObjectName("footerNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("保存")
        save_button.setObjectName("primaryButton")
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet(SETTINGS_QSS)

    def _build_header(self):
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        emblem = QLabel()
        emblem_pixmap = self._asset_pixmap("header_emblem.png", 44)
        if emblem_pixmap is not None:
            emblem.setPixmap(emblem_pixmap)
        title_row.addWidget(emblem)
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("洛琪希设置")
        title.setObjectName("dialogTitle")
        version = getattr(getattr(self.feedback_export, "run_meta", None), "version", "")
        subtitle = QLabel("让陪伴更符合你的习惯。" + (f" · 版本 {version}" if version else ""))
        subtitle.setObjectName("dialogSubtitle")
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        title_column.addStretch(1)
        title_row.addLayout(title_column, 1)
        header_layout.addLayout(title_row)

        divider_row = QHBoxLayout()
        divider_row.setSpacing(8)
        divider = QFrame()
        divider.setObjectName("headerLine")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider_row.addWidget(divider, 1)
        star = QLabel()
        star_pixmap = self._asset_pixmap("title_star.png", 16)
        if star_pixmap is not None:
            star.setPixmap(star_pixmap)
        divider_row.addWidget(star, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addLayout(divider_row)
        return header

    def _build_appearance_card(self):
        self.always_on_top = ToggleSwitch()
        self.pet_scale = QDoubleSpinBox()
        self.pet_scale.setRange(0.65, 1.8)
        self.pet_scale.setSingleStep(0.05)
        self.pet_scale.setDecimals(2)
        self.pet_scale.setSuffix(" 倍")
        self._fix_control_width(self.pet_scale, 150)

        self.asset_path = QLineEdit()
        browse_button = QPushButton("浏览")
        browse_button.clicked.connect(self._browse_asset)
        asset_row = QWidget()
        asset_layout = QHBoxLayout(asset_row)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.setSpacing(8)
        asset_layout.addWidget(self.asset_path, 1)
        asset_layout.addWidget(browse_button)

        return self._card(
            "section_pet.png",
            "桌宠外观",
            [
                self._row("始终置顶", "让桌宠始终显示在其他窗口之上", self.always_on_top),
                self._row("桌宠缩放", "调整桌宠在桌面上的显示大小", self.pet_scale),
                self._row(
                    "桌宠图片",
                    "更换桌宠显示的图片（支持透明背景 PNG）",
                    asset_row,
                    stretch_control=True,
                ),
            ],
        )

    def _build_companion_card(self):
        self.active_persona_id = QComboBox()
        registry = PersonaRegistry(PROJECT_ROOT / "data" / "personas")
        for persona in registry.available_personas():
            self.active_persona_id.addItem(
                str(persona["display_name"]), str(persona["persona_id"])
            )
        self._fix_control_width(self.active_persona_id, 150)

        self.smart_intent_enabled = ToggleSwitch()
        self.auto_tips_enabled = ToggleSwitch()
        self.auto_tips_min_minutes = self._spin_box(1, 1440, " 分钟")
        self._fix_control_width(self.auto_tips_min_minutes, 150)
        self.auto_tips_max_minutes = self._spin_box(1, 1440, " 分钟")
        self._fix_control_width(self.auto_tips_max_minutes, 150)
        self.proactive_enabled = ToggleSwitch()
        self.proactive_interval_minutes = self._spin_box(1, 1440, " 分钟")
        self._fix_control_width(self.proactive_interval_minutes, 150)
        self.evening_review_enabled = ToggleSwitch()
        self.auto_complete_yesterday_review = ToggleSwitch()
        self.idle_nudge_enabled = ToggleSwitch()
        self.study_minutes = self._spin_box(1, 1440, " 分钟")
        self._fix_control_width(self.study_minutes, 150)
        self.sleep_minutes = self._spin_box(1, 1440, " 分钟")
        self._fix_control_width(self.sleep_minutes, 150)

        return self._card(
            "section_companion.png",
            "陪伴与提醒",
            [
                self._row("当前人格", "选择陪伴你的角色人格", self.active_persona_id),
                self._row(
                    "智能意图理解", "理解自然表达并调用现有功能", self.smart_intent_enabled
                ),
                self._row("自动提示", "允许桌宠自动弹出提示", self.auto_tips_enabled),
                self._row(
                    "提示最小间隔",
                    "两次自动提示之间的最小时间间隔",
                    self.auto_tips_min_minutes,
                ),
                self._row(
                    "提示最大间隔",
                    "两次自动提示之间的最大时间间隔",
                    self.auto_tips_max_minutes,
                ),
                self._row(
                    "开启主动陪伴", "根据今日成长状态给出轻量提醒", self.proactive_enabled
                ),
                self._row(
                    "主动提醒间隔",
                    "主动提醒的时间间隔",
                    self.proactive_interval_minutes,
                    tooltip="主动陪伴开启时，每隔多久检查一次今日状态并考虑轻量提醒。",
                ),
                self._row(
                    "晚间复盘提醒", "晚间提醒进行今日复盘", self.evening_review_enabled
                ),
                self._row(
                    "昨日复盘补全",
                    "启动桌面程序时自动补全昨日成长复盘",
                    self.auto_complete_yesterday_review,
                ),
                self._row(
                    "未互动提醒", "长时间未互动时轻声提醒", self.idle_nudge_enabled
                ),
                self._row("学习提醒时间", "学习提醒的计时长度", self.study_minutes),
                self._row("睡眠时间", "睡眠提醒的计时长度", self.sleep_minutes),
            ],
        )

    def _build_model_card(self):
        self.online_model_enabled = ToggleSwitch()
        self.model_mode = QComboBox()
        self.model_mode.addItem("省钱", "economy")
        self.model_mode.addItem("自动", "auto")
        self.model_mode.addItem("高质量", "quality")
        self._fix_control_width(self.model_mode, 150)

        self.default_model = QLineEdit()
        self.default_model.setPlaceholderText(DEFAULT_DEEPSEEK_MODEL)
        self._fix_control_width(self.default_model, 200)
        self.complex_model = QLineEdit()
        self.complex_model.setPlaceholderText(DEFAULT_DEEPSEEK_COMPLEX_MODEL)
        self._fix_control_width(self.complex_model, 200)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空则保留现有 Key")
        show_key_button = QPushButton("显示")
        show_key_button.setCheckable(True)
        show_key_button.toggled.connect(
            lambda checked: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        clear_key_button = QPushButton("清除")
        clear_key_button.clicked.connect(self._clear_api_key)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.api_key, 1)
        key_layout.addWidget(show_key_button)
        key_layout.addWidget(clear_key_button)

        self.api_key_status = QLabel()
        self.api_key_status.setObjectName("rowHint")

        test_connection_button = QPushButton("测试连接")
        test_connection_button.setObjectName("primaryButton")
        test_connection_button.clicked.connect(self._test_connection)
        self.connection_status = QLabel("尚未测试")
        self.connection_status.setObjectName("rowHint")
        connection_row = QWidget()
        connection_layout = QHBoxLayout(connection_row)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.addWidget(test_connection_button)
        connection_layout.addWidget(self.connection_status, 1)

        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("例如 qwen3:4b")
        self._fix_control_width(self.model_name, 200)

        self.enable_auto_escalation = ToggleSwitch()
        self.enable_thinking = ToggleSwitch()
        self.offline_fallback_enabled = ToggleSwitch()
        self.offline_model = QLineEdit()
        self.offline_model.setPlaceholderText("qwen3:4b")
        self._fix_control_width(self.offline_model, 200)

        self.request_timeout_seconds = self._spin_box(5, 600, " 秒")
        self._fix_control_width(self.request_timeout_seconds, 150)

        self.llm_intent_assist_enabled = ToggleSwitch()

        self.usage_summary_label = QLabel()
        clear_usage_button = QPushButton("清空")
        clear_usage_button.clicked.connect(self._clear_usage)
        usage_row = QWidget()
        usage_layout = QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.addWidget(self.usage_summary_label, 1)
        usage_layout.addWidget(clear_usage_button)

        return self._card(
            "section_model.png",
            "模型连接",
            [
                self._row(
                    "在线模型", "优先使用 DeepSeek 在线模型", self.online_model_enabled
                ),
                self._row("模型模式", "模型调用模式", self.model_mode),
                self._row("默认模型", "日常对话使用的默认模型", self.default_model),
                self._row("复杂模型", "复杂任务使用的模型", self.complex_model),
                self._row(
                    "DeepSeek API Key",
                    "仅保存在本地私有配置，留空则保留现有 Key",
                    key_row,
                    stretch_control=True,
                ),
                self._row("Key 状态", None, self.api_key_status, stretch_control=True),
                self._row(
                    "连接测试",
                    "发送一次轻量请求，检查 DeepSeek 服务是否可用",
                    connection_row,
                    stretch_control=True,
                    tooltip=(
                        "用当前填入或已保存的 Key 发送一次轻量请求，只检查服务是否可用；"
                        "不会开始对话或写入学习记录。"
                    ),
                ),
                self._row(
                    "模型名称", "本地模型名称（例如 qwen3:4b）", self.model_name
                ),
                self._row(
                    "自动升级", "复杂任务自动使用复杂模型", self.enable_auto_escalation
                ),
                self._row(
                    "复杂任务思考", "深度规划等复杂任务允许思考模式", self.enable_thinking
                ),
                self._row(
                    "本地降级",
                    "DeepSeek 不可用时使用本地 Ollama",
                    self.offline_fallback_enabled,
                ),
                self._row("Ollama 模型", "本地降级使用的模型名称", self.offline_model),
                self._row(
                    "请求超时", "单次模型请求的最长等待时间", self.request_timeout_seconds
                ),
                self._row(
                    "模型意图辅助",
                    "在规则无法判断时请求当前模型辅助识别",
                    self.llm_intent_assist_enabled,
                ),
                self._row(
                    "本地使用统计",
                    "记录在本地的模型调用次数与 tokens",
                    usage_row,
                    stretch_control=True,
                ),
            ],
        )

    def _build_privacy_card(self):
        self.chat_history_enabled = ToggleSwitch()
        self.recent_context_messages = self._spin_box(4, 40, " 条")
        self._fix_control_width(self.recent_context_messages, 150)
        self.auto_summary_enabled = ToggleSwitch()

        clear_history_button = QPushButton("清空聊天历史")
        clear_history_button.setObjectName("dangerButton")
        clear_history_button.clicked.connect(self._clear_chat_history)

        open_knowledge_button = QPushButton("打开知识库文件夹")
        open_knowledge_button.clicked.connect(self._open_knowledge_folder)

        feedback_button = QPushButton("导出内测反馈")
        feedback_button.clicked.connect(self.open_feedback_export_dialog)
        unavailable = getattr(self.feedback_export, "unavailable_reason", "")
        feedback_available = bool(
            self.feedback_export is not None
            and getattr(self.feedback_export, "available", False)
        )
        if feedback_available:
            feedback_hint = "整理问题、预期与实际结果，连同所选数据打包成 ZIP；没有后台上传"
        else:
            feedback_button.setEnabled(False)
            feedback_hint = unavailable or "未接入导出服务，导出不可用。"

        return self._card(
            "section_privacy.png",
            "隐私与数据",
            [
                self._row(
                    "保存聊天历史",
                    "保存每日学习会话，重启后可继续",
                    self.chat_history_enabled,
                    tooltip="关闭后不再保存新的聊天记录；已有本地历史不会自动删除。",
                ),
                self._row(
                    "最近上下文", "对话中携带的最近消息条数", self.recent_context_messages
                ),
                self._row(
                    "自动会话摘要", "对话过长时生成本地会话摘要", self.auto_summary_enabled
                ),
                self._row(
                    "清空本地聊天历史",
                    "删除本地全部聊天记录（不可撤销；长期记忆和成长数据不受影响）",
                    clear_history_button,
                ),
                self._row(
                    "本地知识库",
                    "打开知识库文件夹，管理 RoxyPlan 可读取的资料",
                    open_knowledge_button,
                ),
                self._row(
                    "导出内测反馈",
                    feedback_hint,
                    feedback_button,
                    tooltip="打开导出窗口，由你选择要包含的数据；只在本地打包成 ZIP，没有后台上传。",
                ),
            ],
        )

    def open_feedback_export_dialog(self):
        """打开反馈导出窗口；服务未注入时不做任何事，也不猜数据根。"""
        if self.feedback_export is None or not getattr(self.feedback_export, "available", False):
            return None
        # 延迟导入：反馈窗口复用本模块的配色，模块级导入会形成循环。
        from frontend.feedback_dialog import FeedbackExportDialog

        dialog = FeedbackExportDialog(
            self.feedback_export, self, display_info=self._display_environment()
        )
        dialog.feedback_exported.connect(self.feedback_exported.emit)
        self.feedback_dialog = dialog
        dialog.show()
        return dialog

    def _display_environment(self):
        """只取屏幕逻辑尺寸与缩放比；不读取用户名、主机名等无关信息。"""
        info = {}
        screen = self.screen()
        if screen is not None:
            geometry = screen.geometry()
            info["screen_logical"] = f"{geometry.width()}x{geometry.height()}"
            info["device_pixel_ratio"] = round(screen.devicePixelRatio(), 3)
        return info

    def _build_help_card(self):
        usage_button = QPushButton("打开操作说明")
        usage_button.clicked.connect(self.usage_help_requested.emit)

        quit_button = QPushButton("退出 Roxy")
        quit_button.setObjectName("dangerButton")
        quit_button.clicked.connect(self.request_quit)

        return self._card(
            "title_star.png",
            "帮助与退出",
            [
                self._row(
                    "操作说明",
                    "只读查看常用表达和内测使用提示；不会读取私人数据，也不会调用模型",
                    usage_button,
                ),
                self._row(
                    "退出程序",
                    "关闭桌宠并退出 RoxyPlan；聊天历史、长期记忆和成长数据都会保留",
                    quit_button,
                ),
            ],
        )

    def request_quit(self):
        """退出前简单确认，默认取消；这里不保存也不重置任何设置。"""
        if not self._confirm_quit():
            return
        self.quit_requested.emit()

    def _confirm_quit(self) -> bool:
        answer = QMessageBox.question(
            self,
            "退出 Roxy",
            "确定要退出 RoxyPlan 吗？\n未保存的设置修改不会生效。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _build_hidden_parameters(self):
        """内部实现与测试参数：保留控件和配置值，但不进入普通界面布局。"""
        self.test_mode = QCheckBox("使用秒级测试间隔")
        self.study_test_seconds = self._spin_box(1, 3600, " 秒")
        self.sleep_test_seconds = self._spin_box(1, 3600, " 秒")

        self.intent_auto_execute_confidence = QDoubleSpinBox()
        self.intent_auto_execute_confidence.setRange(0.5, 1.0)
        self.intent_auto_execute_confidence.setSingleStep(0.05)
        self.intent_auto_execute_confidence.setDecimals(2)

        self.agent_core_enabled = QCheckBox("使用受限内部工具处理计划、记忆与动作")
        self.agent_multi_step_enabled = QCheckBox("允许一次请求按顺序执行多个内部工具")
        self.llm_planner_assist_enabled = QCheckBox("规则无法规划时允许当前模型输出受限步骤")
        self.agent_max_steps = self._spin_box(1, 3, " 步")

        self.agent_medium_confidence = QDoubleSpinBox()
        self.agent_medium_confidence.setRange(0.5, 1.0)
        self.agent_medium_confidence.setSingleStep(0.05)
        self.agent_medium_confidence.setDecimals(2)

        self.agent_confirm_high_risk = QCheckBox("删除和其他不可逆操作始终确认")
        self.agent_confirm_high_risk.setChecked(True)
        self.agent_confirm_high_risk.setEnabled(False)

        # Retain the legacy config values for compatibility; daily sessions
        # always resume when chat history saving is enabled, and candidate
        # memory is no longer a user-facing product option.
        self.restore_last_session = QCheckBox()
        self.enable_memory_candidates = QCheckBox()

        for widget in (
            self.test_mode,
            self.study_test_seconds,
            self.sleep_test_seconds,
            self.intent_auto_execute_confidence,
            self.agent_core_enabled,
            self.agent_multi_step_enabled,
            self.llm_planner_assist_enabled,
            self.agent_max_steps,
            self.agent_medium_confidence,
            self.agent_confirm_high_risk,
            self.restore_last_session,
            self.enable_memory_candidates,
        ):
            widget.setParent(self)
            widget.setVisible(False)

    @staticmethod
    def _fix_control_width(widget, width):
        widget.setFixedWidth(width)

    @staticmethod
    def _asset_pixmap(name, size):
        path = UI_ASSET_DIR / name
        if not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        # 以 2 倍逻辑尺寸解码，Windows 150% 缩放下依旧清晰。
        ratio = 2.0
        scaled = pixmap.scaled(
            int(size * ratio),
            int(size * ratio),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        return scaled

    def _row(self, label_text, hint_text, control, stretch_control=False, tooltip=None):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(14)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        label = QLabel(label_text)
        label.setObjectName("rowLabel")
        label.setWordWrap(True)
        if tooltip:
            row.setToolTip(tooltip)
            label.setToolTip(tooltip)
        text_column.addWidget(label)
        if hint_text:
            hint = QLabel(hint_text)
            hint.setObjectName("rowHint")
            hint.setWordWrap(True)
            text_column.addWidget(hint)
        text_column.addStretch(1)
        row_layout.addLayout(text_column, 2)

        if control is not None:
            if stretch_control:
                row_layout.addWidget(control, 3)
            else:
                row_layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _card(self, icon_name, title_text, rows):
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 16)
        card_layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        icon = QLabel()
        icon_pixmap = self._asset_pixmap(icon_name, 22)
        if icon_pixmap is not None:
            icon.setPixmap(icon_pixmap)
        title_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel(title_text)
        title.setObjectName("cardTitle")
        title_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        card_layout.addLayout(title_row)

        for row in rows:
            card_layout.addWidget(row)
        return card

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
        self.auto_complete_yesterday_review.setChecked(
            bool(self.config.get("auto_complete_yesterday_review", True))
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
            bool(self.config.get("enable_memory_candidates", False))
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
        persona_index = self.active_persona_id.findData(
            str(self.config.get("active_persona_id", "roxy"))
        )
        self.active_persona_id.setCurrentIndex(persona_index if persona_index >= 0 else 0)
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
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.knowledge_dir)))

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
                "auto_complete_yesterday_review": (
                    self.auto_complete_yesterday_review.isChecked()
                ),
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
                "active_persona_id": str(self.active_persona_id.currentData() or "roxy"),
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
        # 设置窗口打开期间聊天可能刚写入“已看过”标志；保存时不要覆盖回未看。
        if not self.config.get(FIRST_USE_HINT_SEEN_KEY) and first_use_hint_seen(
            self.config_file
        ):
            self.config[FIRST_USE_HINT_SEEN_KEY] = True
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
            "计时、提示、缩放和置顶设置会尽量立即生效。\n"
            "模型服务设置会尽量立即生效；桌宠图片路径建议重启 RoxyPlan 后生效。",
        )
        self.accept()
