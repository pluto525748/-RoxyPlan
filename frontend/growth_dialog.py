from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from modules.growth_manager import GrowthManager
from modules.learning_loop import LearningLoopService
from modules.development_log import get_development_log


class GrowthDialog(QDialog):
    """Tabbed presentation of the existing detailed growth workflow."""

    def __init__(self, growth_service: GrowthManager, parent=None) -> None:
        super().__init__(parent)
        self.growth_manager = growth_service
        self.learning_loop = LearningLoopService(
            growth_service, now_provider=growth_service.now_provider,
        )
        self._refreshing_tasks = False
        self._review_is_preview = False

        self.setWindowTitle("RoxyPlan · 今日学习面板")
        self.resize(620, 720)
        self.setMinimumSize(500, 520)
        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(16, 12, 16, 16)
        dialog_layout.setSpacing(12)
        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(4)
        title = QLabel("成长管理")
        title.setObjectName("growthTitle")
        subtitle = QLabel("把计划放进今天，也把真实行动留下来。")
        subtitle.setObjectName("growthSubtitle")
        subtitle.setWordWrap(True)
        heading_text.addWidget(title)
        heading_text.addWidget(subtitle)
        heading.addLayout(heading_text, 1)
        artwork = QLabel()
        artwork.setFixedSize(96, 64)
        artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        artwork.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        image = QPixmap(str(Path(__file__).resolve().parents[1]
                            / "assets" / "growth" / "roxy_growth.png"))
        if not image.isNull():
            ratio = self.devicePixelRatioF()
            image = image.scaled(round(96 * ratio), round(64 * ratio),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            image.setDevicePixelRatio(ratio)
            artwork.setPixmap(image)
            heading.addWidget(artwork)
        dialog_layout.addLayout(heading)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("growthTabs")
        self.tabs.tabBar().setExpanding(True)
        dialog_layout.addWidget(self.tabs, 1)
        self.panel_scroll, plan_page = self._add_tab("计划与行动")
        _, review_page = self._add_tab("今日复盘")
        _, log_page = self._add_tab("成长日志")

        self.today_summary = QLabel()
        self.today_summary.setObjectName("todayLearningSummary")
        self.today_summary.setWordWrap(True)
        self.today_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        review_page.addWidget(self.today_summary)

        self.plan_group = QGroupBox("今日计划")
        plan_layout = QVBoxLayout(self.plan_group)
        plan_layout.setSpacing(10)
        plan_input_row = QHBoxLayout()
        self.plan_input = QLineEdit()
        self.plan_input.setPlaceholderText("添加一个今天能推进的小计划...")
        self.plan_input.returnPressed.connect(self.add_plan)
        self.add_plan_button = QPushButton("添加")
        self.add_plan_button.clicked.connect(self.add_plan)
        plan_input_row.addWidget(self.plan_input, 1)
        plan_input_row.addWidget(self.add_plan_button)
        plan_layout.addLayout(plan_input_row)

        self.plan_table = QTableWidget(0, 3)
        self.plan_table.setHorizontalHeaderLabels(["完成", "计划", "操作"])
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.plan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plan_table.setShowGrid(False)
        self.plan_table.setAlternatingRowColors(True)
        self.plan_table.setWordWrap(True)
        self.plan_table.setMinimumHeight(210)
        self.plan_table.setTextElideMode(Qt.TextElideMode.ElideNone)
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
        # ResizeToContents measures table items, not embedded delete buttons.
        self.plan_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.plan_table.setColumnWidth(2, 84)
        self.plan_table.itemChanged.connect(self._on_plan_item_changed)
        self.plan_table.horizontalHeader().sectionResized.connect(
            lambda *_args: self._resize_plan_rows()
        )
        plan_layout.addWidget(self.plan_table, 1)
        plan_page.addWidget(self.plan_group, 3)

        action_group = QGroupBox("行动记录")
        action_layout = QVBoxLayout(action_group)
        action_layout.setSpacing(10)
        action_input_row = QHBoxLayout()
        self.action_input = QLineEdit()
        self.action_input.setPlaceholderText("记录刚刚完成或推进的事情...")
        self.action_input.returnPressed.connect(self.add_action)
        self.action_minutes = QSpinBox()
        self.action_minutes.setRange(0, 1440)
        self.action_minutes.setSuffix(" 分钟")
        self.action_minutes.setSpecialValueText("时长未记录")
        self.action_minutes.setMinimumWidth(142)
        self.action_minutes.setToolTip("可选；未填写不计入实际学习时长")
        self.action_minutes.setEnabled(callable(getattr(self.growth_manager, "add_record", None)))
        self.add_action_button = QPushButton("记录")
        self.add_action_button.clicked.connect(self.add_action)
        action_input_row.addWidget(self.action_input, 1)
        action_input_row.addWidget(self.add_action_button)
        action_layout.addLayout(action_input_row)
        duration_row = QHBoxLayout()
        duration_row.addWidget(QLabel("实际时长（可选）"))
        duration_row.addWidget(self.action_minutes)
        duration_row.addStretch(1)
        action_layout.addLayout(duration_row)
        self.action_list = QListWidget()
        self._configure_list(self.action_list)
        self.action_list.setMinimumHeight(90)
        action_layout.addWidget(self.action_list, 1)
        plan_page.addWidget(action_group, 2)

        review_group = QGroupBox("今日复盘")
        review_layout = QVBoxLayout(review_group)
        self.review_text = QTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setAcceptRichText(False)
        self.review_text.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.review_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.review_text.setMinimumHeight(160)
        self.review_text.setPlaceholderText("生成复盘后会显示今天的计划与行动。")
        review_layout.addWidget(self.review_text, 1)
        self.review_status = QLabel()
        self.review_status.setObjectName("growthSubtitle")
        self.review_status.setWordWrap(True)
        review_layout.addWidget(self.review_status)
        review_buttons = QHBoxLayout()
        review_buttons.addStretch(1)
        self.generate_button = QPushButton("生成复盘")
        self.generate_button.clicked.connect(self.generate_review)
        self.save_button = QPushButton("保存到成长日志")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_review)
        review_buttons.addWidget(self.generate_button)
        review_buttons.addWidget(self.save_button)
        review_layout.addLayout(review_buttons)
        review_page.addWidget(review_group, 1)

        log_group = QGroupBox("成长日志")
        log_layout = QVBoxLayout(log_group)
        month_row = QHBoxLayout()
        month_row.addWidget(QLabel("自然月"))
        self.month_selector = QComboBox()
        self.month_selector.currentIndexChanged.connect(
            lambda _index: self.refresh_growth_logs(refresh_months=False)
        )
        month_row.addWidget(self.month_selector, 1)
        log_layout.addLayout(month_row)
        self.month_summary_label = QLabel()
        self.month_summary_label.setObjectName("todayLearningSummary")
        self.month_summary_label.setWordWrap(True)
        log_layout.addWidget(self.month_summary_label)
        self.growth_log_list = QListWidget()
        self._configure_list(self.growth_log_list)
        self.growth_log_list.setMinimumHeight(180)
        self.refresh_log_button = QPushButton("查看本月成长日志")
        self.refresh_log_button.clicked.connect(self.refresh_growth_logs)
        log_layout.addWidget(self.growth_log_list, 1)
        log_layout.addWidget(self.refresh_log_button, alignment=Qt.AlignmentFlag.AlignRight)
        log_page.addWidget(log_group, 1)
        # Enter in an editor must not accidentally invoke another tab's button.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

        self.setStyleSheet(
            """
            QDialog { background: #FAF8F2; }
            QWidget { color: #35415A; font-family: "Microsoft YaHei"; font-size: 14px; }
            QScrollArea#growthPanelScroll, QWidget#growthPanelContent { background: #FAF8F2; }
            QLabel#growthTitle { font-size: 22px; font-weight: 600; color: #3E4770; }
            QLabel#growthSubtitle { font-size: 12px; color: #78829A; }
            QTabWidget::pane { border: 1px solid #DDD8EB; border-radius: 8px; }
            QTabBar::tab { background: #EEEAF7; color: #536392; border: 1px solid #DDD8EB;
                        padding: 10px 20px; margin-right: 3px; border-radius: 6px; }
            QTabBar::tab:selected { background: #829CCD; color: white; border-color: #829CCD; }
            QTabBar::tab:hover:!selected { background: #EAF1FA; }
            QLabel#todayLearningSummary { background: #EAF1FA; border: 1px solid #D5DEEF;
                        border-radius: 8px; padding: 10px; color: #3E4770; }
            QGroupBox { background: #FFFEFB; border: 1px solid #DDD8EB; border-radius: 8px;
                        margin-top: 9px; padding: 10px 8px 8px 8px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #59658F; }
            QLineEdit, QTextEdit, QListWidget, QTableWidget { background: #FFFFFF; border: 1px solid #DDD6F6;
                        border-radius: 6px; padding: 6px; color: #35415A; font-weight: 400; }
            QSpinBox, QComboBox { background: #FFFFFF; border: 1px solid #DDD6F6;
                        border-radius: 6px; padding: 6px; }
            QListWidget::item { padding: 8px; }
            QListWidget::item:selected { background: #EEEAF7; color: #35415A; }
            QTableWidget { alternate-background-color: #F8FAFF; }
            QTableWidget::item { padding: 5px 7px; }
            QHeaderView::section { background: #EAF4FF; color: #59658F; border: none;
                        border-bottom: 1px solid #D8CCFF; padding: 5px; }
            QPushButton { background: #EAF4FF; color: #536392; border: 1px solid #C7D9F7;
                        border-radius: 6px; padding: 7px 12px; font-weight: 500; }
            QPushButton:hover { background: #DCEBFF; }
            QPushButton#primaryButton { background: #829CCD; color: white; border-color: #829CCD; }
            QPushButton#primaryButton:hover { background: #708ABD; }
            """
        )

    def _add_tab(self, title: str):
        scroll = QScrollArea()
        scroll.setObjectName("growthPanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QWidget()
        panel.setObjectName("growthPanelContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        scroll.setWidget(panel)
        self.tabs.addTab(scroll, title)
        return scroll, layout

    @staticmethod
    def _configure_list(widget: QListWidget) -> None:
        widget.setWordWrap(True)
        widget.setTextElideMode(Qt.TextElideMode.ElideNone)
        widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _resize_plan_rows(self) -> None:
        self.plan_table.resizeRowsToContents()
        for row in range(self.plan_table.rowCount()):
            self.plan_table.setRowHeight(row, max(44, self.plan_table.rowHeight(row)))

    def refresh_all(self) -> None:
        self.refresh_plans()
        self.refresh_actions()
        self.refresh_today_summary()
        self.refresh_today_review()
        self.refresh_growth_logs()

    def refresh_today_summary(self) -> None:
        snapshot = self.learning_loop.day_snapshot()
        evidence = self.learning_loop.planning_evidence()
        rate = (
            f"{snapshot['completion_rate']:.0%}"
            if snapshot["completion_rate"] is not None else "暂无计划"
        )
        planned = (
            f"{snapshot['planned_minutes']} 分钟（{snapshot['planned_duration_count']}/{snapshot['total']} 项有时长）"
            if snapshot["planned_duration_count"] else "未记录"
        )
        actual = (
            f"{snapshot['actual_minutes']} 分钟（{snapshot['actual_duration_count']}/{len(snapshot['actions'])} 条有时长）"
            if snapshot["actual_duration_count"] else "未记录"
        )
        self.today_summary.setText(
            f"完成 {snapshot['completed']}/{snapshot['total']} · 完成率 {rate} · "
            f"行动 {len(snapshot['actions'])} 条\n"
            f"计划时长 {planned} · 实际时长 {actual}\n"
            f"过期未完成 {len(evidence['overdue_tasks'])} 项"
            + (
                "：" + "、".join(str(task.get("title", ""))[:24]
                                for task in evidence["overdue_tasks"][:3])
                if evidence["overdue_tasks"] else ""
            )
        )

    def refresh_today_review(self) -> None:
        # A generated review is a UI preview, not a persisted growth entry.
        # Read-only refreshes (including reopening) must leave it available.
        if self._review_is_preview:
            return
        today = self.growth_manager.now_provider().date().isoformat()
        entries = (
            self.growth_manager.entries()
            if callable(getattr(self.growth_manager, "entries", None))
            else self.growth_manager.growth_store.entries()
        )
        entry = next((item for item in entries if item.get("date") == today), None)
        review = entry.get("review", {}) if isinstance(entry, dict) else {}
        if not isinstance(review, dict):
            self.review_text.clear()
            self.review_status.setText("今天还没有保存复盘。")
            return
        text = str(review.get("text", ""))
        snapshot = self.learning_loop.day_snapshot()
        if text and (
            review.get("total") != snapshot["total"]
            or review.get("done") != snapshot["completed"]
            or review.get("action_count") != len(snapshot["actions"])
        ):
            text = "已保存复盘与当前记录不同；可重新生成并保存。\n\n" + text
        if self.review_text.toPlainText() != text:
            self.review_text.setPlainText(text)
        self.review_status.setText(f"{today} · 已保存复盘" if text else "今天还没有保存复盘。")

    def refresh_plans(self) -> None:
        self._refreshing_tasks = True
        self.plan_table.setRowCount(0)
        tasks = self.growth_manager.plan_store.tasks()
        persisted_uids = (
            self.growth_manager.persisted_task_uids(str(tasks[0]["date"]))
            if tasks and isinstance(self.growth_manager, GrowthManager) else set()
        )
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
            delete_button.setAutoDefault(False)
            delete_button.setMinimumHeight(32)
            row_task = dict(task, _persistent_uid=str(task.get("uid", "")) in persisted_uids)
            delete_button.clicked.connect(
                lambda checked=False, row_task=row_task: self.delete_plan(row_task)
            )
            operation = QWidget()
            operation_layout = QHBoxLayout(operation)
            operation_layout.setContentsMargins(4, 4, 4, 4)
            operation_layout.addWidget(delete_button, alignment=Qt.AlignmentFlag.AlignVCenter)
            self.plan_table.setCellWidget(row, 2, operation)
        self._resize_plan_rows()
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
            duration = record.get("duration_minutes")
            suffix = f" · {duration} 分钟" if isinstance(duration, int) and duration > 0 else ""
            self.action_list.addItem(f"{record.get('time', '')}  {record.get('content', '')}{suffix}")
            self.action_list.item(self.action_list.count() - 1).setToolTip(
                self.action_list.item(self.action_list.count() - 1).text()
            )

    def refresh_growth_logs(self, *, refresh_months: bool = True) -> None:
        selected_month = str(self.month_selector.currentData() or "").strip()
        if refresh_months:
            entries = self.growth_manager.growth_store.entries()
            available = sorted(
                {
                    str(entry.get("date", ""))[:7]
                    for entry in entries
                    if isinstance(entry, dict)
                    and len(str(entry.get("date", ""))) >= 7
                },
                reverse=True,
            )
            current_month = (
                self.growth_manager.current_month()
                if hasattr(self.growth_manager, "current_month")
                else ""
            )
            target_month = selected_month or current_month
            if current_month and current_month not in available:
                available.insert(0, current_month)
            self.month_selector.blockSignals(True)
            self.month_selector.clear()
            for month in available:
                self.month_selector.addItem(month, month)
            index = self.month_selector.findData(target_month)
            self.month_selector.setCurrentIndex(index if index >= 0 else 0)
            self.month_selector.blockSignals(False)
        month = str(self.month_selector.currentData() or "").strip()
        self.growth_log_list.clear()
        if hasattr(self.growth_manager, "entries_for_month") and month:
            entries = self.growth_manager.entries_for_month(month)
            statistics = self.growth_manager.monthly_statistics(month)
        else:
            entries = list(reversed(self.growth_manager.growth_store.entries()))[:7]
            statistics = {
                "logged_days": len(entries),
                "total_plans": sum(
                    int(entry.get("review", {}).get("total", 0) or 0)
                    for entry in entries
                    if isinstance(entry, dict)
                ),
                "completed_plans": sum(
                    int(entry.get("review", {}).get("done", 0) or 0)
                    for entry in entries
                    if isinstance(entry, dict)
                ),
                "action_count": sum(
                    len(entry.get("review", {}).get("actions", []))
                    for entry in entries
                    if isinstance(entry, dict)
                ),
            }
        self.month_summary_label.setText(
            "记录 {logged_days} 天 · 计划 {completed_plans}/{total_plans} 完成 · "
            "计划外行动 {action_count} 条".format(**statistics)
        )
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
        self._run_logged_write(
            "ui_plan_add", lambda: self.growth_manager.plan_store.add_task(title)
        )
        self.plan_input.clear()
        self.refresh_plans()
        self.refresh_today_summary()
        self.refresh_today_review()

    def delete_plan(self, row_task: dict) -> None:
        target = self._locate_plan_task(row_task)
        if target is None:
            self._report_plan_delete_skipped()
            return
        title = str(target.get("title", "")).strip()
        answer = QMessageBox.question(
            self,
            "删除计划",
            f"确定删除计划「{title}」吗？\n删除后无法恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self._run_logged_write(
            "ui_plan_delete",
            lambda: self._delete_verified_plan(row_task),
        )
        if result is None:
            self._report_plan_delete_skipped()
            return
        self.refresh_plans()
        self.refresh_today_summary()
        self.refresh_today_review()

    def _delete_verified_plan(self, row_task: dict):
        store = self.growth_manager.plan_store
        repository = getattr(store, "repository", None)
        with repository.transaction("plan") if repository is not None else nullcontext():
            target = self._locate_plan_task(row_task)
            if target is None:
                return None
            if isinstance(store, GrowthManager):
                return store.delete_by_id(int(target["id"]), date=str(target["date"]))
            return store.delete_by_id(int(target["id"]))

    def _locate_plan_task(self, row_task: dict) -> Optional[Dict[str, object]]:
        """Re-read the live plan and match the task behind a rendered row."""
        task_id = int(row_task.get("id", -1))
        matches = [
            task for task in self.growth_manager.plan_store.tasks()
            if int(task.get("id", -1)) == task_id
        ]
        if len(matches) != 1:
            return None
        candidate = matches[0]
        if row_task.get("date") and candidate.get("date") != row_task["date"]:
            return None
        row_uid = str(row_task.get("uid", "") or "")
        if row_uid and row_uid == str(candidate.get("uid", "") or ""):
            return candidate
        if row_task.get("_persistent_uid"):
            return None
        if isinstance(self.growth_manager, GrowthManager):
            persisted_uids = self.growth_manager.persisted_task_uids(str(candidate["date"]))
            if str(candidate.get("uid", "")) in persisted_uids:
                return None
        same_title = str(candidate.get("title", "")) == str(row_task.get("title", ""))
        same_created = str(candidate.get("created_at", "")) == str(
            row_task.get("created_at", "")
        )
        return candidate if same_title and same_created else None

    def _report_plan_delete_skipped(self) -> None:
        self.refresh_plans()
        self.refresh_today_summary()
        self.refresh_today_review()
        QMessageBox.information(
            self,
            "计划已变化",
            "该计划已变化或已不存在，本次没有删除任何内容。",
        )

    def _on_plan_item_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing_tasks or item.column() != 0:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked and task_id is not None:
            self._run_logged_write(
                "ui_plan_complete",
                lambda: self.growth_manager.plan_store.complete_by_id(int(task_id)),
            )
        self.refresh_plans()
        self.refresh_today_summary()
        self.refresh_today_review()

    def add_action(self) -> None:
        content = self.action_input.text().strip()
        if not content:
            return
        kwargs = (
            {"duration_minutes": self.action_minutes.value() or None}
            if self.action_minutes.isEnabled() else {}
        )
        self._run_logged_write(
            "ui_growth_add_action",
            lambda: self.growth_manager.action_store.add_record(
                content, source="manual", **kwargs,
            ),
        )
        self.action_input.clear()
        self.action_minutes.setValue(0)
        self.refresh_actions()
        self.refresh_today_summary()
        self.refresh_today_review()

    def generate_review(self) -> None:
        review = self.growth_manager.generate_review()
        self.review_text.setPlainText(str(review["text"]))
        self._review_is_preview = True
        day = self.growth_manager.now_provider().date().isoformat()
        self.review_status.setText(f"{day} · 当前复盘尚未保存；保存时使用最新计划与行动。")

    def save_review(self) -> None:
        self._run_logged_write(
            "ui_growth_save_review", self.growth_manager.save_today_review
        )
        self.generate_review()
        self._review_is_preview = False
        self.refresh_today_review()
        self.refresh_growth_logs()
        QMessageBox.information(self, "已保存", "今天的复盘已保存到本地成长日志。")

    @staticmethod
    def _run_logged_write(source, callback):
        """Keep UI writes distinct from chat, even inside an existing trace."""
        logger = get_development_log()
        trace = logger.new_trace(source=source)
        logger.event(trace, "ui_operation_started")
        try:
            with logger.bind(trace):
                result = callback()
        except Exception as error:
            logger.record_exception(trace, error)
            logger.event(
                trace, "ui_operation_finished", status="failed",
                success=False, persisted=False,
                error_type=type(error).__name__,
            )
            raise

        changed = True
        record = result
        if isinstance(result, tuple) and len(result) == 2:
            record, changed = result
        if not isinstance(record, dict):
            logger.event(
                trace, "ui_operation_finished", status="not_found"
                if record is None and source != "ui_growth_save_review" else "unknown",
                success=False, persisted=False,
            )
            return result
        if record.get("duplicate"):
            changed = False
        fields = {
            "status": "success" if changed else "unchanged",
            "success": True,
            "changed": bool(changed),
            "persisted": bool(changed),
            "date": record.get("date"),
            "record_id": record.get("uid"),
            "changed_resource_ids": [record["uid"]]
            if changed and record.get("uid") else [],
        }
        if source == "ui_growth_save_review":
            fields["revision"] = record.get("revision")
            review = record.get("review")
            if isinstance(review, dict):
                fields.update({
                    key: review.get(key)
                    for key in ("total", "done", "pending", "action_count")
                })
        logger.event(trace, "ui_operation_finished", **fields)
        return result

    def showEvent(self, event) -> None:  # noqa: N802
        print("[GrowthUI] open", flush=True)
        self.refresh_all()
        super().showEvent(event)
