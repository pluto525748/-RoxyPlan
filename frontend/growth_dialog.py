from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from modules.growth_manager import GrowthManager


class GrowthDialog(QDialog):
    """Small V0.9 panel for today's plan, actions, and review."""

    def __init__(self, growth_service: GrowthManager, parent=None) -> None:
        super().__init__(parent)
        self.growth_manager = growth_service
        self._refreshing_tasks = False

        self.setWindowTitle("RoxyPlan · 成长面板")
        self.resize(560, 720)
        self.setMinimumSize(500, 620)
        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(0, 0, 0, 0)

        self.panel_scroll = QScrollArea()
        self.panel_scroll.setObjectName("growthPanelScroll")
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.panel_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        panel = QWidget()
        panel.setObjectName("growthPanelContent")
        self.panel_scroll.setWidget(panel)
        dialog_layout.addWidget(self.panel_scroll)

        root = QVBoxLayout(panel)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("今日成长")
        title.setObjectName("growthTitle")
        subtitle = QLabel("把计划放进今天，也把真实行动留下来。")
        subtitle.setObjectName("growthSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.plan_group = QGroupBox("今日计划")
        self.plan_group.setMinimumHeight(280)
        plan_layout = QVBoxLayout(self.plan_group)
        plan_input_row = QHBoxLayout()
        self.plan_input = QLineEdit()
        self.plan_input.setPlaceholderText("添加一个今天能推进的小计划...")
        self.plan_input.returnPressed.connect(self.add_plan)
        add_plan_button = QPushButton("添加")
        add_plan_button.clicked.connect(self.add_plan)
        plan_input_row.addWidget(self.plan_input, 1)
        plan_input_row.addWidget(add_plan_button)
        plan_layout.addLayout(plan_input_row)

        self.plan_table = QTableWidget(0, 3)
        self.plan_table.setHorizontalHeaderLabels(["完成", "计划", "操作"])
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.plan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plan_table.setShowGrid(False)
        self.plan_table.setAlternatingRowColors(True)
        self.plan_table.setWordWrap(True)
        self.plan_table.setMinimumHeight(175)
        self.plan_table.setMaximumHeight(320)
        self.plan_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.plan_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.plan_table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.plan_table.verticalHeader().setDefaultSectionSize(44)
        self.plan_table.verticalHeader().setMinimumSectionSize(40)
        self.plan_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.plan_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.plan_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.plan_table.itemChanged.connect(self._on_plan_item_changed)
        plan_layout.addWidget(self.plan_table)
        root.addWidget(self.plan_group)

        action_group = QGroupBox("行动记录")
        action_group.setMinimumHeight(190)
        action_layout = QVBoxLayout(action_group)
        action_input_row = QHBoxLayout()
        self.action_input = QLineEdit()
        self.action_input.setPlaceholderText("记录刚刚完成或推进的事情...")
        self.action_input.returnPressed.connect(self.add_action)
        add_action_button = QPushButton("记录")
        add_action_button.clicked.connect(self.add_action)
        action_input_row.addWidget(self.action_input, 1)
        action_input_row.addWidget(add_action_button)
        action_layout.addLayout(action_input_row)
        self.action_list = QListWidget()
        action_layout.addWidget(self.action_list)
        root.addWidget(action_group)

        review_group = QGroupBox("今日复盘")
        review_group.setMinimumHeight(210)
        review_layout = QVBoxLayout(review_group)
        self.review_text = QTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setPlaceholderText("生成复盘后会显示今天的计划与行动。")
        review_layout.addWidget(self.review_text)
        review_buttons = QHBoxLayout()
        review_buttons.addStretch(1)
        generate_button = QPushButton("生成复盘")
        generate_button.clicked.connect(self.generate_review)
        save_button = QPushButton("保存到成长日志")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self.save_review)
        review_buttons.addWidget(generate_button)
        review_buttons.addWidget(save_button)
        review_layout.addLayout(review_buttons)
        root.addWidget(review_group)

        log_group = QGroupBox("成长日志")
        log_group.setMinimumHeight(170)
        log_layout = QVBoxLayout(log_group)
        self.growth_log_list = QListWidget()
        self.growth_log_list.setMaximumHeight(110)
        refresh_log_button = QPushButton("查看最近成长日志")
        refresh_log_button.clicked.connect(self.refresh_growth_logs)
        log_layout.addWidget(self.growth_log_list)
        log_layout.addWidget(refresh_log_button)
        root.addWidget(log_group)
        root.addStretch(1)

        self.setStyleSheet(
            """
            QDialog, QScrollArea#growthPanelScroll,
            QWidget#growthPanelContent, QScrollArea#growthPanelScroll > QWidget > QWidget {
                        background: #F7F4FF; color: #333333; font-family: "Microsoft YaHei"; }
            QLabel#growthTitle { font-size: 20px; font-weight: 600; color: #3E4770; }
            QLabel#growthSubtitle { font-size: 12px; color: #777777; }
            QGroupBox { background: #FFFFFF; border: 1px solid #D8CCFF; border-radius: 8px;
                        margin-top: 9px; padding: 10px 8px 8px 8px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #59658F; }
            QLineEdit, QTextEdit, QListWidget, QTableWidget { background: #FFFFFF; border: 1px solid #DDD6F6;
                        border-radius: 6px; padding: 6px; color: #333333; font-weight: 400; }
            QTableWidget { alternate-background-color: #F8FAFF; }
            QTableWidget::item { padding: 5px 7px; }
            QHeaderView::section { background: #EAF4FF; color: #59658F; border: none;
                        border-bottom: 1px solid #D8CCFF; padding: 5px; }
            QPushButton { background: #EAF4FF; color: #536392; border: 1px solid #C7D9F7;
                        border-radius: 6px; padding: 7px 12px; font-weight: 500; }
            QPushButton:hover { background: #DCEBFF; }
            QPushButton#primaryButton { background: #8EA7FF; color: white; border: none; }
            QPushButton#primaryButton:hover { background: #7894F8; }
            """
        )

    def refresh_all(self) -> None:
        self.refresh_plans()
        self.refresh_actions()
        self.refresh_growth_logs()

    def refresh_plans(self) -> None:
        self._refreshing_tasks = True
        self.plan_table.setRowCount(0)
        tasks = self.growth_manager.plan_store.tasks()
        self.plan_group.setTitle(f"今日计划 · {len(tasks)} 项")
        for task in tasks:
            row = self.plan_table.rowCount()
            self.plan_table.insertRow(row)
            done_item = QTableWidgetItem()
            done_item.setData(Qt.ItemDataRole.UserRole, int(task["id"]))
            done_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            done_item.setCheckState(
                Qt.CheckState.Checked if task.get("done", False) else Qt.CheckState.Unchecked
            )
            self.plan_table.setItem(row, 0, done_item)
            title_item = QTableWidgetItem(self._task_display_text(task))
            title_item.setToolTip(self._task_tooltip(task))
            title_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.plan_table.setItem(row, 1, title_item)
            delete_button = QPushButton("删除")
            delete_button.setMinimumHeight(32)
            delete_button.clicked.connect(
                lambda checked=False, task_id=int(task["id"]): self.delete_plan(task_id)
            )
            self.plan_table.setCellWidget(row, 2, delete_button)
        self.plan_table.resizeRowsToContents()
        for row in range(self.plan_table.rowCount()):
            self.plan_table.setRowHeight(row, max(44, self.plan_table.rowHeight(row)))
        self._refreshing_tasks = False

    @staticmethod
    def _task_display_text(task: dict) -> str:
        title = str(task.get("title", "")).strip()
        details = []
        time_slot = str(task.get("time_slot", "")).strip()
        start_time = str(task.get("start_time", "")).strip()
        duration = task.get("duration_minutes")
        if time_slot:
            details.append(time_slot)
        if start_time:
            details.append(start_time)
        if isinstance(duration, (int, float)) and duration > 0:
            details.append(f"{int(duration)} 分钟")
        return title + (f"\n{' · '.join(details)}" if details else "")

    @staticmethod
    def _task_tooltip(task: dict) -> str:
        lines = [str(task.get("title", "")).strip()]
        labels = (
            ("日期", "date"),
            ("时段", "time_slot"),
            ("开始", "start_time"),
            ("时长", "duration_minutes"),
            ("优先级", "priority"),
            ("备注", "notes"),
        )
        for label, key in labels:
            value = task.get(key)
            if value not in (None, "", []):
                suffix = " 分钟" if key == "duration_minutes" else ""
                lines.append(f"{label}：{value}{suffix}")
        return "\n".join(lines)

    def refresh_actions(self) -> None:
        self.action_list.clear()
        for record in self.growth_manager.action_store.records_for_date():
            self.action_list.addItem(f"{record.get('time', '')}  {record.get('content', '')}")

    def refresh_growth_logs(self) -> None:
        self.growth_log_list.clear()
        if hasattr(self.growth_manager, "recent_entries"):
            entries = self.growth_manager.recent_entries(7)
        else:
            entries = list(reversed(self.growth_manager.growth_store.entries()))[:7]
        if not entries:
            self.growth_log_list.addItem("还没有保存过复盘。")
            return
        for entry in entries:
            review = entry.get("review", {})
            if not isinstance(review, dict):
                review = {}
            self.growth_log_list.addItem(
                f"{entry.get('date', '')}  完成 {review.get('done', 0)}/{review.get('total', 0)}，"
                f"行动 {len(review.get('actions', []))} 条"
            )

    def add_plan(self) -> None:
        title = self.plan_input.text().strip()
        if not title:
            return
        self.growth_manager.plan_store.add_task(title)
        self.plan_input.clear()
        self.refresh_plans()

    def delete_plan(self, task_id: int) -> None:
        self.growth_manager.plan_store.delete_by_id(task_id)
        self.refresh_plans()

    def _on_plan_item_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing_tasks or item.column() != 0:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked and task_id is not None:
            self.growth_manager.plan_store.complete_by_id(int(task_id))
        self.refresh_plans()

    def add_action(self) -> None:
        content = self.action_input.text().strip()
        if not content:
            return
        self.growth_manager.action_store.add_record(content, source="manual")
        self.action_input.clear()
        self.refresh_actions()

    def generate_review(self) -> None:
        review = self.growth_manager.generate_review()
        self.review_text.setPlainText(str(review["text"]))

    def save_review(self) -> None:
        self.growth_manager.save_today_review()
        self.generate_review()
        self.refresh_growth_logs()
        QMessageBox.information(self, "已保存", "今天的复盘已保存到本地成长日志。")

    def showEvent(self, event) -> None:  # noqa: N802
        print("[GrowthUI] open", flush=True)
        self.refresh_all()
        super().showEvent(event)
