"""Today Growth panel with caller-injected services or a synthetic preview snapshot."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import re

from PySide6.QtCore import Qt, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid
from frontend.app_branding import APP_ICON_PATH


PET_IMAGE = APP_ICON_PATH
VISIBLE_REFRESH_MS = 2000


def _label(text: str, name: str = "", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _minutes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= 1440 else None


class PreviewCheckBox(QCheckBox):
    """Clearly show the local checked state on every Qt platform style."""

    def __init__(self, scale: float = 1.0) -> None:
        super().__init__()
        self.scale_factor = scale
        side = max(1, round(27 * scale))
        self.setFixedSize(side, side)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.scale_factor, self.scale_factor)
        painter.setPen(QPen(QColor("#6266EE" if self.isChecked() else "#B7C3DD"), 1.7))
        painter.setBrush(QColor("#6266EE" if self.isChecked() else "#FFFFFF"))
        painter.drawRoundedRect(QRectF(2, 2, 23, 23), 5, 5)
        if self.isChecked():
            painter.setPen(QPen(QColor("#FFFFFF"), 2.8))
            painter.drawLine(7, 13, 11, 18)
            painter.drawLine(11, 18, 20, 8)
        painter.end()


class TodayGrowthDialog(QDialog):
    """Standalone panel; service mode reads shared growth data, preview stays local."""

    details_requested = Signal()

    def __init__(self, snapshot: dict | None = None, parent: QWidget | None = None,
                 *, scale: float = 660 / 900, growth_manager=None,
                 learning_loop=None) -> None:
        super().__init__(parent)
        if not 0.45 <= scale <= 1.5:
            raise ValueError("scale must be between 0.45 and 1.5")
        if (growth_manager is None) != (learning_loop is None):
            raise ValueError("growth_manager and learning_loop must be injected together")
        if growth_manager is not None and learning_loop.growth_manager is not growth_manager:
            raise ValueError("learning_loop must use the injected growth_manager")
        if growth_manager is not None and snapshot is not None:
            raise ValueError("service mode cannot use a preview snapshot")
        if growth_manager is None and snapshot is None:
            raise ValueError("a preview snapshot or shared growth services are required")
        self.service_mode = growth_manager is not None
        self.growth_manager = growth_manager
        self.learning_loop = learning_loop
        self._service_ready = False
        self._display_date = ""
        self._day_facts: dict = {}
        self._rendered_tasks: tuple = ()
        self._rendered_actions: tuple = ()
        self._rendered_action_date: str | None = None
        self._read_failed = False
        self._summary_display_date: str | None = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(VISIBLE_REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh_from_service)
        self.scale_factor = scale
        self.snapshot = (deepcopy(snapshot) if not self.service_mode else {
            "date_label": "今日 · 读取中",
            "tasks": [], "actions": [], "summary": "今日复盘待接入。",
        })
        self.tasks = self.snapshot.get("tasks", [])
        self.actions = [] if self.service_mode else self.snapshot.get("actions", [])
        self._apply_readable_font()
        self.setWindowTitle(
            "RoxyPlan · 今日成长面板" if self.service_mode
            else "RoxyPlan · 今日成长面板 · 视觉预览"
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self._u(900), self._u(650))
        self.setMinimumSize(self._u(480), self._u(520))
        self._wide_layout = True
        self._build_ui()
        self._sync_progress()

    def _u(self, value: int) -> int:
        return max(1, round(value * self.scale_factor))

    def _scaled_style(self, style: str) -> str:
        def replace(match: re.Match) -> str:
            return f"{match.group(1)}: {self._u(int(match.group(2)))}px"
        return re.sub(r"(font-size|border-radius|height):\s*(\d+)px", replace, style)

    def _apply_readable_font(self) -> None:
        for path in (
            Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
            Path("C:/Windows/Fonts/Deng.ttf"),
        ):
            if path.exists():
                font_id = QFontDatabase.addApplicationFont(str(path))
                families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
                if families:
                    font = QFont(families[0])
                    font.setPointSizeF(10 * self.scale_factor)
                    self.setFont(font)
                    return

    def _build_ui(self) -> None:
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("todayPageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        shell.addWidget(self.page_scroll)

        page = QWidget()
        page.setObjectName("todayPage")
        self.page_scroll.setWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(self._u(14), self._u(12), self._u(14), self._u(12))
        root.setSpacing(self._u(10))

        header = QHBoxLayout()
        header.setSpacing(self._u(10))
        icon = _label("▦", "heroIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(self._u(44), self._u(44))
        header.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(self._u(1))
        titles.addWidget(_label("今日成长面板", "heroTitle"))
        titles.addWidget(_label("看见今天的计划，也看见已经留下的行动。", "heroSubtitle"))
        titles.addWidget(_label(
            "今日计划与行动已接入 · 其他区域待接入" if self.service_mode
            else "视觉预览 · 全部内容均为合成示例", "previewBadge"))
        header.addLayout(titles, 1)
        self.pet_image = _label("", "petDecoration")
        self.pet_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pet_image.setFixedSize(self._u(105), self._u(78))
        pixmap = QPixmap(str(PET_IMAGE))
        if not pixmap.isNull():
            ratio = self.pet_image.devicePixelRatioF()
            decoration = pixmap.scaled(
                int(self.pet_image.width() * ratio),
                int(self.pet_image.height() * ratio),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            decoration.setDevicePixelRatio(ratio)
            self.pet_image.setPixmap(decoration)
        header.addWidget(self.pet_image, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.middle = QGridLayout()
        self.middle.setHorizontalSpacing(self._u(10))
        self.middle.setVerticalSpacing(self._u(10))
        self.plan_panel = self._build_plan_panel()
        self.status_panel = self._build_status_panel()
        self.action_panel = self._build_action_panel()
        self._position_panels(wide=True)
        root.addLayout(self.middle, 1)

        summary = self._panel("summaryPanel")
        self.summary_panel = summary
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(self._u(14), self._u(10),
                                          self._u(14), self._u(10))
        summary_layout.setSpacing(self._u(3))
        summary_heading = QHBoxLayout()
        summary_heading.setSpacing(self._u(6))
        summary_heading.addWidget(_label("▤  今日小结", "sectionTitle"))
        summary_heading.addStretch(1)
        self.details_button = QPushButton("详情")
        self.details_button.setObjectName("detailsButton")
        self.details_button.setEnabled(self.service_mode)
        self.details_button.setToolTip("打开详细成长管理" if self.service_mode else "视觉预览未接入管理服务")
        self.details_button.clicked.connect(lambda: self.details_requested.emit())
        summary_heading.addWidget(self.details_button)
        refresh_button = QPushButton()
        refresh_button.setObjectName("refreshButton")
        refresh_button.setAccessibleName("刷新状态")
        refresh_button.setFixedSize(self._u(29), self._u(29))
        refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_button.setIconSize(QSize(self._u(16), self._u(16)))
        refresh_button.setEnabled(self.service_mode)
        refresh_button.setToolTip("重新读取今日计划、行动与复盘" if self.service_mode else "预览中尚未接入")
        if self.service_mode:
            refresh_button.clicked.connect(self.refresh_from_service)
        if self.service_mode:
            self.summary_status_label = _label("读取中", "sectionHint")
            summary_heading.addWidget(self.summary_status_label)
            self.summary_notice_label = _label("", "noticeText", wrap=True)
            self.summary_notice_label.hide()
            summary_heading.addWidget(self.summary_notice_label, 1)
        summary_heading.addWidget(refresh_button)
        summary_layout.addLayout(summary_heading)
        if self.service_mode:
            self.summary_text = QPlainTextEdit()
            self.summary_text.setObjectName("summaryText")
            self.summary_text.setReadOnly(True)
            self.summary_text.setFrameShape(QFrame.Shape.NoFrame)
            self.summary_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self.summary_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.summary_text.setFixedHeight(self._u(48))
            self.summary_text.setPlainText("今日复盘读取中。")
            summary_layout.addWidget(self.summary_text)
        else:
            summary_text = str(self.snapshot.get("summary") or "这是示例小结。今天的计划与行动会在正式接入后显示在这里。")
            summary_label = _label("示例小结 · " + summary_text,
                                   "summaryText", wrap=True)
            summary_layout.addWidget(summary_label)
        root.addWidget(summary)
        self._position_panels(wide=self._wide_layout)

        self.setStyleSheet(self._scaled_style("""
            QDialog, QScrollArea#todayPageScroll, QWidget#todayPage {
                background: #F6F8FF; color: #172447;
            }
            QLabel#heroIcon, QLabel#sectionIcon {
                background: #E8EDFF; color: #5965E9; border-radius: 12px;
                font-size: 23px; font-weight: 700;
            }
            QLabel#heroTitle { color: #101A3E; font-size: 24px; font-weight: 700; }
            QLabel#heroSubtitle { color: #7180A0; font-size: 12px; }
            QLabel#previewBadge { color: #6472CE; font-size: 10px; font-weight: 600; }
            QFrame#planPanel, QFrame#statusPanel, QFrame#actionPanel,
            QFrame#summaryPanel {
                background: #FFFFFF; border: 1px solid #DCE4FF; border-radius: 14px;
            }
            QFrame#summaryPanel { background: #F0F2FF; }
            QLabel#sectionTitle { color: #172044; font-size: 18px; font-weight: 700; }
            QLabel#sectionHint, QLabel#mutedText { color: #7683A3; font-size: 12px; }
            QLabel#dateText { color: #64739C; font-size: 11px; }
            QFrame#taskCard {
                background: #FFFFFF; border: 1px solid #E5EAF8; border-radius: 10px;
            }
            QFrame#taskCard[done="true"] {
                background: #EFF3FF; border-color: #EFF3FF;
            }
            QLabel#taskTitle { color: #192547; font-size: 15px; font-weight: 600; }
            QLabel#taskTitle[done="true"] { color: #7481A3; text-decoration: line-through; }
            QLabel#taskDescription, QLabel#taskDuration { color: #7885A4; font-size: 12px; }
            QFrame#progressBox { background: #F8FAFF; border: 1px solid #E8ECF8;
                border-radius: 12px; }
            QLabel#progressCount { color: #1B2749; font-size: 13px; font-weight: 600; }
            QLabel#progressPercent { color: #6065EC; font-size: 15px; font-weight: 700; }
            QLabel#durationTotals { color: #526183; font-size: 11px; }
            QProgressBar { background: #E4E8F2; border: none; border-radius: 5px; height: 10px; }
            QProgressBar::chunk { background: #6266EE; border-radius: 5px; }
            QScrollArea#taskScroll, QScrollArea#actionScroll { background: transparent; border: none; }
            QWidget#taskList, QWidget#actionList { background: transparent; }
            QFrame#metricCard { border: none; border-radius: 10px; background: #F2F5FF; }
            QFrame#metricCard[accent="pink"] { background: #FFF1F2; }
            QFrame#metricCard[accent="blue"] { background: #EEF6FF; }
            QFrame#metricCard[accent="green"] { background: #EFF9F3; }
            QLabel#metricIcon { color: #6266EB; font-size: 18px; font-weight: 700; }
            QLabel#metricLabel { color: #687697; font-size: 11px; }
            QLabel#metricValue { color: #172247; font-size: 19px; font-weight: 700; }
            QLabel#metricValue[compact="true"] { font-size: 12px; font-weight: 600; }
            QLabel#timelineDot { color: #636AEB; font-size: 17px; }
            QLabel#actionTime { color: #657498; font-size: 11px; font-weight: 600; }
            QLabel#actionContent { color: #253354; font-size: 12px; }
            QFrame#actionRow { background: #F7F9FF; border: none; border-radius: 9px; }
            QLabel#summaryText { color: #47567D; font-size: 12px; }
            QPlainTextEdit#summaryText { color: #47567D; font-size: 12px;
                background: transparent; border: none; }
            QLabel#noticeText { color: #B75066; font-size: 11px; }
            QPushButton#detailsButton { color: #5965B0; background: #F8FAFF;
                border: 1px solid #DEE4F4; border-radius: 8px; padding: 4px 8px;
                font-size: 12px; }
            QPushButton#detailsButton:hover { background: #E8EDFF; }
            QPushButton#refreshButton:disabled { color: #9CA7C5; background: #F8F9FF;
                border: 1px solid #DEE4F4; border-radius: 8px; }
        """))

    @staticmethod
    def _panel(name: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName(name)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return frame

    def _scroll_list(self, name: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setObjectName(name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("taskList" if name == "taskScroll" else "actionList")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, self._u(3), 0)
        layout.setSpacing(self._u(6))
        scroll.setWidget(body)
        return scroll, layout

    def _build_plan_panel(self) -> QFrame:
        panel = self._panel("planPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(self._u(12), self._u(10),
                                  self._u(12), self._u(10))
        layout.setSpacing(self._u(8))
        heading = QHBoxLayout()
        heading.addWidget(_label("▣  今日计划", "sectionTitle"))
        heading.addStretch(1)
        self.date_label = _label(str(self.snapshot.get("date_label", "示例日期")), "dateText")
        heading.addWidget(self.date_label)
        layout.addLayout(heading)
        self.notice_label = _label("", "noticeText", wrap=True)
        self.notice_label.hide()
        layout.addWidget(self.notice_label)
        scroll, task_layout = self._scroll_list("taskScroll")
        self.task_scroll = scroll
        self.task_layout = task_layout
        scroll.setMinimumHeight(self._u(260))
        scroll.setMaximumHeight(self._u(305))
        self._populate_task_list()
        layout.addWidget(scroll, 1)

        progress = self._panel("progressBox")
        progress_layout = QVBoxLayout(progress)
        progress_layout.setContentsMargins(self._u(10), self._u(7),
                                           self._u(10), self._u(7))
        progress_layout.setSpacing(self._u(4))
        progress_row = QHBoxLayout()
        self.count_label = _label("", "progressCount")
        self.percent_label = _label("", "progressPercent")
        progress_row.addWidget(self.count_label)
        progress_row.addStretch(1)
        progress_row.addWidget(self.percent_label)
        progress_layout.addLayout(progress_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        progress_layout.addWidget(self.progress_bar)
        self.duration_label = _label("", "durationTotals", wrap=True)
        progress_layout.addWidget(self.duration_label)
        layout.addWidget(progress)
        return panel

    def _task_card(self, index: int, task: dict) -> QFrame:
        card = self._panel("taskCard")
        card.setMinimumHeight(self._u(62))
        done = bool(task.get("done"))
        card.setProperty("done", done)
        row = QHBoxLayout(card)
        row.setContentsMargins(self._u(9), self._u(7),
                               self._u(9), self._u(7))
        row.setSpacing(self._u(8))
        checkbox = PreviewCheckBox(self.scale_factor)
        checkbox.setObjectName("taskCheck")
        checkbox.setAccessibleName(str(task.get("title") or "任务") + " 完成状态")
        checkbox.setChecked(done)
        if self.service_mode:
            uid = str(task.get("uid") or "")
            task_date = str(task.get("date") or "")
            checkbox.setEnabled(self._service_ready and not done
                                and bool(uid) and bool(task.get("persisted_uid")))
            if done:
                checkbox.setToolTip("已完成，无需重复操作")
            elif not uid or not task.get("persisted_uid"):
                checkbox.setToolTip("此计划缺少已保存的稳定标识，暂不能安全完成")
            checkbox.toggled.connect(
                lambda checked, key=uid, day=task_date:
                self._on_service_task_checked(key, day, checked))
        else:
            checkbox.toggled.connect(lambda checked, i=index: self._on_task_checked(i, checked))
        row.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        text_column = QVBoxLayout()
        text_column.setSpacing(self._u(1))
        title = _label(str(task.get("title") or "未命名计划"), "taskTitle", wrap=True)
        title.setProperty("done", done)
        text_column.addWidget(title)
        description = str(task.get("description") or "").strip()
        if description:
            text_column.addWidget(_label(description, "taskDescription", wrap=True))
        row.addLayout(text_column, 1)
        duration = _minutes(task.get("duration_minutes"))
        row.addWidget(_label(f"预计 {duration} 分钟" if duration is not None else "时长未记录", "taskDuration"),
                      0, Qt.AlignmentFlag.AlignVCenter)
        card.title_label = title
        card.check_box = checkbox
        task["_card"] = card
        return card

    def _populate_task_list(self) -> None:
        position = self.task_scroll.verticalScrollBar().value()
        while self.task_layout.count():
            item = self.task_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for index, task in enumerate(self.tasks):
            self.task_layout.addWidget(self._task_card(index, task))
        if not self.tasks:
            message = ("今天还没有计划。" if self.service_mode
                       else "今天还没有计划。正式接入后，这里会显示今日计划。")
            empty = _label(message, "mutedText", wrap=True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.task_layout.addWidget(empty)
        self.task_layout.addStretch(1)
        scroll = self.task_scroll
        QTimer.singleShot(
            0,
            lambda: scroll.verticalScrollBar().setValue(position) if isValid(scroll) else None,
        )

    def _build_status_panel(self) -> QFrame:
        panel = self._panel("statusPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(self._u(12), self._u(10),
                                  self._u(12), self._u(10))
        layout.setSpacing(self._u(8))
        layout.addWidget(_label("▥  最近成长状态", "sectionTitle"))
        grid = QGridLayout()
        grid.setSpacing(self._u(7))
        self.today_metric = self._metric("◉", "今日完成率", "", "blue")
        self.pending_metric = self._metric("◇", "今日未完成", "", "pink")
        recent_total = self.snapshot.get("recent_total") if not self.service_mode else None
        recent_done = self.snapshot.get("recent_completed") if not self.service_mode else None
        recent_value = (
            "读取中" if self.service_mode else
            f"{round(recent_done / recent_total * 100)}%"
            if isinstance(recent_total, int) and recent_total > 0
            and isinstance(recent_done, int) else "待接入"
        )
        self.recent_metric = self._metric("▥", "近七日计划完成", recent_value, "")
        self.recent_metric.setToolTip(
            "不含今天的前七个自然日；已完成计划项数 ÷ 计划项总数；无计划日不增加分母"
        )
        self.actions_metric = self._metric("●", "行动记录数量", "", "green")
        grid.addWidget(self.today_metric, 0, 0)
        grid.addWidget(self.pending_metric, 0, 1)
        grid.addWidget(self.recent_metric, 1, 0)
        grid.addWidget(self.actions_metric, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return panel

    def _metric(self, icon: str, name: str, value: str, accent: str) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        card.setProperty("accent", accent)
        card.setMinimumHeight(self._u(71))
        row = QHBoxLayout(card)
        row.setContentsMargins(self._u(8), self._u(6),
                               self._u(7), self._u(6))
        row.setSpacing(self._u(5))
        row.addWidget(_label(icon, "metricIcon"), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(self._u(1))
        texts.addWidget(_label(name, "metricLabel", wrap=True))
        value_label = _label(value, "metricValue")
        texts.addWidget(value_label)
        row.addLayout(texts, 1)
        card.value_label = value_label
        return card

    def _build_action_panel(self) -> QFrame:
        panel = self._panel("actionPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(self._u(12), self._u(10),
                                  self._u(12), self._u(10))
        layout.setSpacing(self._u(7))
        layout.addWidget(_label("●  今日行动记录", "sectionTitle"))
        self.action_notice_label = _label("", "noticeText", wrap=True)
        self.action_notice_label.hide()
        layout.addWidget(self.action_notice_label)
        scroll, actions_layout = self._scroll_list("actionScroll")
        scroll.setMinimumHeight(self._u(152))
        self.action_scroll = scroll
        self.action_layout = actions_layout
        self._populate_action_list(self.actions)
        layout.addWidget(scroll, 1)
        return panel

    def _populate_action_list(self, actions: list[dict], *,
                              empty_message: str | None = None,
                              reset_scroll: bool = False) -> None:
        bar = self.action_scroll.verticalScrollBar()
        position = 0 if reset_scroll else bar.value()
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        for action in actions:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            dot = _label("●", "timelineDot")
            dot.setFixedWidth(self._u(14))
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            record = self._panel("actionRow")
            record_layout = QHBoxLayout(record)
            record_layout.setContentsMargins(self._u(8), self._u(6),
                                             self._u(8), self._u(6))
            record_layout.setSpacing(self._u(7))
            time_text = str(action.get("time") or "").strip()
            if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", time_text):
                time_text = time_text[:5]
            clock = _label(time_text or "时间未记录", "actionTime")
            clock.setMinimumWidth(self._u(37))
            record_layout.addWidget(clock, 0, Qt.AlignmentFlag.AlignTop)
            content = str(action.get("content") or "行动内容未记录")
            content_label = _label(content, "actionContent", wrap=True)
            content_label.setTextFormat(Qt.TextFormat.PlainText)
            content_label.setToolTip(content)
            content_label.setMinimumWidth(0)
            record_layout.addWidget(content_label, 1)
            if self.service_mode:
                minutes = _minutes(action.get("duration_minutes"))
                if minutes is not None:
                    record_layout.addWidget(
                        _label(f"{minutes} 分钟", "actionTime"),
                        0, Qt.AlignmentFlag.AlignTop,
                    )
            row.addWidget(record, 1)
            self.action_layout.addWidget(row_widget)
        if not actions:
            empty = _label(
                empty_message or ("今日行动记录读取中。" if self.service_mode
                                  else "今天还没有行动记录。"),
                "mutedText")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.action_layout.addWidget(empty)
        self.action_layout.addStretch(1)
        QTimer.singleShot(
            0, lambda: bar.setValue(min(position, bar.maximum())) if isValid(bar) else None
        )

    def _position_panels(self, *, wide: bool) -> None:
        for panel in (self.plan_panel, self.status_panel, self.action_panel):
            self.middle.removeWidget(panel)
        if wide:
            self.status_panel.show()
            self.action_panel.show()
            self.middle.addWidget(self.plan_panel, 0, 0, 2, 1)
            self.middle.addWidget(self.status_panel, 0, 1)
            self.middle.addWidget(self.action_panel, 1, 1)
            self.middle.setColumnStretch(0, 3)
            self.middle.setColumnStretch(1, 2)
            self.middle.setRowStretch(0, 1)
            self.middle.setRowStretch(1, 1)
            self.task_scroll.setMinimumHeight(self._u(260))
            self.task_scroll.setMaximumHeight(self._u(305))
        else:
            self.middle.addWidget(self.plan_panel, 0, 0)
            self.status_panel.hide()
            self.action_panel.hide()
            self.middle.setColumnStretch(0, 1)
            self.middle.setColumnStretch(1, 0)
            self.middle.setRowStretch(0, 1)
            self.middle.setRowStretch(1, 0)
            self.task_scroll.setMinimumHeight(self._u(145))
            self.task_scroll.setMaximumHeight(self._u(305))
        if hasattr(self, "summary_panel"):
            self.summary_panel.setVisible(wide)
        if hasattr(self, "pet_image"):
            self.pet_image.setVisible(wide or self.width() >= self._u(600))
        self._wide_layout = wide

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        wide = self.width() >= self._u(820)
        if hasattr(self, "middle"):
            if wide != self._wide_layout:
                self._position_panels(wide=wide)
            elif not wide:
                self.pet_image.setVisible(self.width() >= self._u(600))

    def _on_task_checked(self, index: int, checked: bool) -> None:
        task = self.tasks[index]
        task["done"] = checked
        card = task["_card"]
        card.setProperty("done", checked)
        card.title_label.setProperty("done", checked)
        card.style().unpolish(card)
        card.style().polish(card)
        card.title_label.style().unpolish(card.title_label)
        card.title_label.style().polish(card.title_label)
        self._sync_progress()

    def _today(self) -> str:
        return self.growth_manager.now_provider().date().isoformat()

    def _show_notice(self, message: str) -> None:
        self.notice_label.setText(message)
        self.notice_label.show()

    def _sync_task_widgets(self) -> None:
        for task in self.tasks:
            checkbox = task["_card"].check_box
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(task.get("done")))
            checkbox.blockSignals(False)
            checkbox.setEnabled(
                self._service_ready and not task.get("done")
                and bool(task.get("uid")) and bool(task.get("persisted_uid")))

    def _set_summary_display(self, day: str | None, status: str,
                             text: str, notice: str = "") -> None:
        if self.summary_status_label.text() != status:
            self.summary_status_label.setText(status)
        if self.summary_notice_label.text() != notice:
            self.summary_notice_label.setText(notice)
        self.summary_notice_label.setVisible(bool(notice))
        day_changed = day != self._summary_display_date
        if self.summary_text.toPlainText() != text:
            bar = self.summary_text.verticalScrollBar()
            position = 0 if day_changed else bar.value()
            self.summary_text.setPlainText(text)
            QTimer.singleShot(
                0, lambda: bar.setValue(min(position, bar.maximum())) if isValid(bar) else None
            )
        elif day_changed:
            self.summary_text.verticalScrollBar().setValue(0)
        self._summary_display_date = day

    @staticmethod
    def _review_version_notice(review: dict, facts: dict, day: str) -> str:
        saved_plans = review.get("plan_snapshot")
        saved_actions = review.get("action_records")
        required_plan_fields = {
            "stable_id", "title", "done", "status", "date", "time_slot",
            "duration_minutes",
        }
        if (not isinstance(saved_plans, list) or not isinstance(saved_actions, list)
                or any(not isinstance(item, dict)
                       or not required_plan_fields.issubset(item)
                       for item in saved_plans)
                or any(not isinstance(item, dict) for item in saved_actions)):
            return "旧复盘缺少完整快照，无法核对当前记录是否变化。"
        current_plans = [
            {
                "stable_id": str(task.get("uid") or task.get("id") or ""),
                "title": str(task.get("title", "")).strip(),
                "done": bool(task.get("done", False)),
                "status": str(task.get("status", "pending") or "pending"),
                "date": str(task.get("date") or day),
                "time_slot": str(task.get("time_slot", "")).strip(),
                "duration_minutes": task.get("duration_minutes"),
            }
            for task in facts["tasks"]
        ]
        current_actions = [dict(action) for action in facts["actions"]]
        if saved_plans != current_plans or saved_actions != current_actions:
            return "记录已有变化，当前显示的是此前保存的复盘"
        return ""

    def _refresh_summary_from_service(self, day: str, facts: dict) -> None:
        try:
            entries = self.growth_manager.entries(strict_read=True)
            entry = next((item for item in entries if item.get("date") == day), None)
            if entry is None:
                self._set_summary_display(
                    day, "尚未保存",
                    "今天还没有保存复盘。可在聊天或原成长面板保存。",
                )
                return
            review = entry.get("review")
            if not isinstance(review, dict) or not isinstance(review.get("text"), str):
                raise ValueError("invalid saved review")
            notice = self._review_version_notice(review, facts, day)
            panel_summary = review.get("panel_summary")
            display_text = (
                panel_summary if isinstance(panel_summary, str) and panel_summary.strip()
                else review["text"]
            )
            self._set_summary_display(day, "已保存复盘", display_text, notice)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._set_summary_display(day, "读取失败", "今日复盘读取失败，请稍后刷新。")

    def _refresh_recent_completion(self, day: str) -> None:
        try:
            recent = self.learning_loop.recent_plan_completion(day, strict_read=True)
            total = int(recent["recent_total"])
            completed = int(recent["recent_completed"])
            if total < 0 or not 0 <= completed <= total:
                raise ValueError("Invalid recent plan counts")
            value = f"{round(completed / total * 100)}%" if total else "暂无计划数据"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            value = "读取失败"
        label = self.recent_metric.value_label
        if label.text() != value:
            label.setText(value)
        compact = value in {"暂无计划数据", "读取失败"}
        if bool(label.property("compact")) != compact:
            label.setProperty("compact", compact)
            label.style().unpolish(label)
            label.style().polish(label)

    def refresh_from_service(self) -> bool:
        if not self.service_mode:
            return False
        day = None
        try:
            day = self._today()
            facts = self.learning_loop.day_snapshot(
                day, strict_actions=True, strict_plans=True)
            if self._today() != day:
                day = self._today()
                facts = self.learning_loop.day_snapshot(
                    day, strict_actions=True, strict_plans=True)
            if str(facts.get("date")) != day:
                raise ValueError("snapshot date mismatch")
            persisted_uids = self.growth_manager.persisted_task_uids(
                day, strict_read=True)
            tasks = []
            for item in facts["tasks"]:
                if not isinstance(item, dict) or str(item.get("date")) != day:
                    raise ValueError("task date mismatch")
                tasks.append({
                    "uid": str(item.get("uid") or ""),
                    "persisted_uid": str(item.get("uid") or "") in persisted_uids,
                    "date": day,
                    "title": str(item.get("title") or "未命名计划"),
                    "description": str(item.get("note") or ""),
                    "duration_minutes": item.get("duration_minutes"),
                    "done": bool(item.get("done")),
                })
            if len(tasks) != int(facts["total"]):
                raise ValueError("snapshot count mismatch")
            raw_actions = facts["actions"]
            if not isinstance(raw_actions, list):
                raise ValueError("snapshot actions mismatch")
            actions = []
            for item in raw_actions:
                if not isinstance(item, dict):
                    raise ValueError("invalid action record")
                if item.get("date") and str(item["date"]) != day:
                    raise ValueError("action date mismatch")
                actions.append(dict(item))
            duration_count = int(facts["actual_duration_count"])
            if not 0 <= duration_count <= len(actions):
                raise ValueError("action duration count mismatch")
            if int(facts["actual_minutes"]) < 0:
                raise ValueError("action duration mismatch")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._service_ready = False
            if day:
                self._refresh_recent_completion(day)
            else:
                self.recent_metric.value_label.setText("读取失败")
            self._sync_task_widgets()
            self._show_notice("今日计划读取失败，请稍后刷新。")
            self._set_summary_display(day, "读取失败", "今日复盘读取失败，请稍后刷新。")
            if not self._display_date or (day and self._display_date != day):
                self.actions = []
                self._rendered_action_date = None
                self._populate_action_list(
                    [], empty_message="今日行动记录读取失败，请稍后刷新。",
                    reset_scroll=True,
                )
                self.actions_metric.value_label.setText("读取失败")
                self.duration_label.setText("今日数据读取失败，请稍后刷新。")
                self.action_notice_label.setText(
                    "日期已变化，旧日行动已隐藏。" if self._display_date
                    else "今日行动记录读取失败，请稍后刷新。"
                )
            else:
                self.action_notice_label.setText(
                    "暂未刷新成功，显示上次成功记录。" if self._display_date
                    else "今日行动记录读取失败，请稍后刷新。"
                )
            self.action_notice_label.show()
            self._read_failed = True
            return False

        signature = tuple((task["uid"], task["date"], task["title"],
                           task["description"], task["duration_minutes"],
                           task["done"], task["persisted_uid"]) for task in tasks)
        action_signature = tuple((action.get("time"), action.get("content"),
                                  action.get("duration_minutes")) for action in actions)
        if self._read_failed:
            self.notice_label.hide()
            self._read_failed = False
        self.action_notice_label.hide()
        self._service_ready = True
        self._display_date = day
        self._day_facts = facts
        self.date_label.setText(date.fromisoformat(day).strftime("%Y年%m月%d日"))
        if signature != self._rendered_tasks:
            self.tasks = tasks
            self._populate_task_list()
            self._rendered_tasks = signature
        if day != self._rendered_action_date or action_signature != self._rendered_actions:
            self.actions = actions
            self._populate_action_list(
                actions,
                empty_message="今天还没有行动记录。",
                reset_scroll=day != self._rendered_action_date,
            )
            self._rendered_actions = action_signature
            self._rendered_action_date = day
        self._sync_task_widgets()
        self._sync_progress()
        self._refresh_recent_completion(day)
        self._refresh_summary_from_service(day, facts)
        return True

    def _on_service_task_checked(self, uid: str, task_date: str,
                                 checked: bool) -> None:
        if not checked:
            self._sync_task_widgets()
            return
        if not self._service_ready or task_date != self._today():
            self._sync_task_widgets()
            self.refresh_from_service()
            self._show_notice("日期已变化，请核对今日计划。")
            return
        try:
            _task, changed = self.growth_manager.complete_today_by_uid(uid, task_date)
        except (OSError, ValueError):
            self.refresh_from_service()
            self._show_notice("保存结果异常，请核对当前计划状态。")
            return
        if not self.refresh_from_service():
            self._show_notice("保存后的状态暂无法核验，请稍后刷新。")
            return
        current = next((task for task in self.tasks if task["uid"] == uid
                        and task["date"] == task_date), None)
        if changed and current is not None and current["done"]:
            self.notice_label.hide()
        elif current is not None and current["done"]:
            self._show_notice("这项计划已在其他入口完成。")
        else:
            self._show_notice("计划已变化，未执行完成。")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.service_mode:
            self.refresh_from_service()
            self.refresh_timer.start()

    def hideEvent(self, event) -> None:
        self.refresh_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.refresh_timer.stop()
        super().closeEvent(event)

    def _sync_progress(self) -> None:
        if self.service_mode:
            facts = self._day_facts
            total = int(facts.get("total", 0))
            done = int(facts.get("completed", 0))
            pending = int(facts.get("pending", 0))
            percent = round(float(facts.get("completion_rate") or 0) * 100)
            self.count_label.setText(f"已完成  {done}/{total}")
            self.percent_label.setText(f"{percent}%" if total else "暂无计划")
            self.progress_bar.setValue(percent)
            self.today_metric.value_label.setText(f"{percent}%" if total else "暂无计划")
            self.pending_metric.value_label.setText(f"{pending} 项")
            if not facts:
                self.actions_metric.value_label.setText("读取中")
                self.duration_label.setText("今日数据读取中。")
                return
            self.actions_metric.value_label.setText(f"{len(facts['actions'])} 条")
            planned = (f"{facts['planned_minutes']} 分钟"
                       if facts.get("planned_duration_count") else "未记录")
            duration_count = int(facts.get("actual_duration_count", 0))
            action_count = len(facts["actions"])
            actual = (f"{facts['actual_minutes']} 分钟" if duration_count
                      else "时长未记录")
            if action_count:
                actual += f" · {duration_count}/{action_count} 条有时长"
            self.duration_label.setText(
                f"已记录计划时长  {planned}    /    已记录实际时长  {actual}")
            return
        total = len(self.tasks)
        done = sum(bool(task.get("done")) for task in self.tasks)
        percent = round(done / total * 100) if total else 0
        self.count_label.setText(f"已完成  {done}/{total}")
        self.percent_label.setText(f"{percent}%" if total else "暂无计划")
        self.progress_bar.setValue(percent)
        self.today_metric.value_label.setText(f"{percent}%" if total else "暂无计划")
        self.pending_metric.value_label.setText(f"{total - done} 项")
        self.actions_metric.value_label.setText(f"{len(self.snapshot.get('actions', []))} 条")
        planned = [_minutes(task.get("duration_minutes")) for task in self.tasks]
        actual = [_minutes(action.get("duration_minutes")) for action in self.snapshot.get("actions", [])]
        planned_text = f"{sum(value for value in planned if value is not None)} 分钟" if any(value is not None for value in planned) else "未记录"
        actual_text = f"{sum(value for value in actual if value is not None)} 分钟" if any(value is not None for value in actual) else "未记录"
        self.duration_label.setText(f"已记录计划时长  {planned_text}    /    已记录实际时长  {actual_text}")
