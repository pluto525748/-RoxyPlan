from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MEMORY_CATEGORIES, MemoryManager
from modules.memory_service import MemoryOperationResult, MemoryService
from modules.development_log import get_development_log


CATEGORY_LABELS = {
    "user_preference": "用户偏好",
    "long_term_goal": "长期目标",
    "stable_habit": "稳定习惯",
    "health_lifestyle": "健康/生活",
    "project_preference": "项目偏好",
}

MEMORY_CATEGORY_LABELS = {
    "preference": "偏好",
    "goal": "长期目标",
    "habit": "习惯",
    "project": "项目",
    "learning": "学习",
    "health": "健康/生活",
    "relationship": "重要关系",
    "rule": "明确规则",
    "other": "其他",
}


class MemoryDialog(QDialog):
    """Unified local memory panel while retaining candidate compatibility."""

    def __init__(
        self,
        candidate_manager: Optional[MemoryCandidateManager] = None,
        accept_callback: Optional[Callable[[int], bool]] = None,
        reject_callback: Optional[Callable[[int], bool]] = None,
        parent=None,
        *,
        memory_manager: Optional[MemoryManager] = None,
        memory_service: Optional[MemoryService] = None,
    ) -> None:
        super().__init__(parent)
        self._legacy_accept_callback = None
        self._legacy_reject_callback = None
        if memory_service is None:
            if candidate_manager is None and memory_manager is None:
                raise ValueError("MemoryDialog requires a MemoryService or memory managers")
            if memory_manager is None and candidate_manager is not None:
                repository = candidate_manager.repository
                memory_manager = MemoryManager(
                    getattr(repository, "memory_file"),
                    backup_dir=getattr(repository, "backup_dir"),
                    conflict_file=getattr(repository, "conflict_file"),
                    audit_file=getattr(repository, "audit_file", None),
                    repository=repository,
                )
                self._legacy_accept_callback = accept_callback
                self._legacy_reject_callback = reject_callback
            memory_service = MemoryService(memory_manager, candidate_manager)
        self.memory_service = memory_service
        self.candidate_manager = memory_service.candidate_manager
        self.memory_manager = memory_service.memory_manager

        self.setWindowTitle("RoxyPlan · 记忆管理")
        self.resize(840, 570)
        self.setMinimumSize(700, 480)
        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("记忆管理")
        title.setObjectName("memoryTitle")
        subtitle = QLabel("长期记忆保存在本地，可在这里查看、编辑、归档和搜索。")
        subtitle.setObjectName("memorySubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_active_tab(), "长期记忆")
        self.tabs.addTab(self._build_conflict_tab(), "冲突")
        self.tabs.addTab(self._build_archived_tab(), "已归档")
        # Keep the compatibility widget alive for advanced callers without
        # exposing candidate review as a normal user tab.
        self._candidate_compatibility_tab = self._build_candidate_tab()
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("memoryStatus")
        root.addWidget(self.status_label)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_all)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        close_row.addWidget(refresh_button)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        self.setStyleSheet(
            """
            QDialog { background: #F7F4FF; color: #333333; font-family: "Microsoft YaHei"; }
            QLabel#memoryTitle { font-size: 20px; font-weight: 600; color: #3E4770; }
            QLabel#memorySubtitle, QLabel#memoryStatus { font-size: 12px; color: #777777; }
            QTabWidget::pane { background: #FFFFFF; border: 1px solid #D8CCFF; border-radius: 7px; }
            QTabBar::tab { background: #EAF4FF; color: #59658F; padding: 8px 14px;
                           border: 1px solid #D8CCFF; border-bottom: none; }
            QTabBar::tab:selected { background: #FFFFFF; color: #3E4770; }
            QTableWidget { background: #FFFFFF; border: none; alternate-background-color: #F8FAFF;
                           color: #333333; }
            QHeaderView::section { background: #EAF4FF; color: #59658F; border: none;
                                   border-bottom: 1px solid #D8CCFF; padding: 7px; }
            QLineEdit, QComboBox, QSpinBox { background: #FFFFFF; border: 1px solid #D8CCFF;
                                            border-radius: 6px; padding: 6px; }
            QPushButton { background: #EAF4FF; color: #536392; border: 1px solid #C7D9F7;
                          border-radius: 6px; padding: 6px 9px; }
            QPushButton:hover { background: #DCEBFF; }
            QPushButton#primaryButton { background: #8EA7FF; color: white; border: none; }
            QPushButton#dangerButton { background: #FFF1F3; color: #A04A5A; border-color: #F3C7CF; }
            """
        )

    def _build_active_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索长期记忆...")
        self.search_input.returnPressed.connect(self.refresh_memories)
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部类别", "")
        for category in sorted(MEMORY_CATEGORIES):
            self.category_filter.addItem(MEMORY_CATEGORY_LABELS[category], category)
        self.category_filter.currentIndexChanged.connect(self.refresh_memories)
        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.refresh_memories)
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.category_filter)
        toolbar.addWidget(search_button)
        layout.addLayout(toolbar)
        self.memory_table = self._memory_table()
        layout.addWidget(self.memory_table, 1)
        return tab

    def _build_candidate_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["分类", "候选内容", "来源", "操作"])
        self._configure_table(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        return tab

    def _build_conflict_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.conflict_table = QTableWidget(0, 4)
        self.conflict_table.setHorizontalHeaderLabels(["类别", "旧记忆", "新信息", "处理"])
        self._configure_table(self.conflict_table)
        header = self.conflict_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.conflict_table)
        return tab

    def _build_archived_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.archived_table = self._memory_table()
        layout.addWidget(self.archived_table)
        return tab

    def _memory_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["ID", "类别", "内容", "重要度", "操作"])
        self._configure_table(table)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        return table

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(True)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)

    def refresh_all(self) -> None:
        self.refresh_memories()
        self.refresh_conflicts()
        self.refresh_archived()

    def refresh_memories(self) -> None:
        query = self.search_input.text().strip()
        category = str(self.category_filter.currentData() or "")
        operation = (
            self.memory_service.search_memories(query, category=category or None)
            if query
            else self.memory_service.list_memories(
                status="active",
                category=category or None,
            )
        )
        memories = operation.data.get("memories", []) if operation.success else []
        self._fill_memory_table(self.memory_table, memories, archived=False)
        if not operation.success:
            self.status_label.setText(operation.safe_message)

    def refresh_archived(self) -> None:
        operation = self.memory_service.list_memories(status="archived")
        memories = operation.data.get("memories", []) if operation.success else []
        self._fill_memory_table(self.archived_table, memories, archived=True)

    def _fill_memory_table(self, table, memories, *, archived: bool) -> None:
        table.setRowCount(0)
        for memory in memories:
            row = table.rowCount()
            table.insertRow(row)
            memory_id = int(memory.get("id", 0))
            table.setItem(row, 0, QTableWidgetItem(str(memory_id)))
            table.setItem(
                row,
                1,
                QTableWidgetItem(
                    MEMORY_CATEGORY_LABELS.get(
                        str(memory.get("category", "other")), "其他"
                    )
                ),
            )
            table.setItem(row, 2, QTableWidgetItem(str(memory.get("content", ""))))
            table.setItem(row, 3, QTableWidgetItem(str(memory.get("importance", 3))))
            table.setCellWidget(row, 4, self._memory_actions(memory_id, archived))
        table.resizeRowsToContents()

    def refresh_candidates(self) -> None:
        operation = self.memory_service.list_candidates(status="pending")
        pending = operation.data.get("candidates", []) if operation.success else []
        self.table.setRowCount(0)
        for candidate in pending:
            row = self.table.rowCount()
            self.table.insertRow(row)
            category = CATEGORY_LABELS.get(
                str(candidate.get("category", "")), str(candidate.get("category", "其他"))
            )
            self.table.setItem(row, 0, QTableWidgetItem(category))
            self.table.setItem(row, 1, QTableWidgetItem(str(candidate.get("content", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(candidate.get("source_text", ""))))
            self.table.setCellWidget(row, 3, self._candidate_actions(int(candidate["id"])))
        self.table.resizeRowsToContents()
        self.status_label.setText(
            f"共有 {len(pending)} 条候选等待确认。" if pending else "当前没有待确认记忆。"
        )

    def refresh_conflicts(self) -> None:
        self.conflict_table.setRowCount(0)
        operation = self.memory_service.list_conflicts(status="pending")
        conflicts = operation.data.get("conflicts", []) if operation.success else []
        for conflict in conflicts:
            row = self.conflict_table.rowCount()
            self.conflict_table.insertRow(row)
            old = conflict.get("old_memory", {})
            old = old if isinstance(old, dict) else {}
            category = str(conflict.get("category", "other"))
            self.conflict_table.setItem(
                row, 0, QTableWidgetItem(MEMORY_CATEGORY_LABELS.get(category, "其他"))
            )
            self.conflict_table.setItem(row, 1, QTableWidgetItem(str(old.get("content", ""))))
            self.conflict_table.setItem(row, 2, QTableWidgetItem(str(conflict.get("new_content", ""))))
            self.conflict_table.setCellWidget(
                row, 3, self._conflict_actions(int(conflict.get("id", 0)))
            )
        self.conflict_table.resizeRowsToContents()

    def _memory_actions(self, memory_id: int, archived: bool) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        detail_button = QPushButton("详情")
        detail_button.clicked.connect(
            lambda checked=False, value=memory_id: self._show_detail(value)
        )
        layout.addWidget(detail_button)
        if archived:
            restore_button = QPushButton("恢复")
            restore_button.clicked.connect(
                lambda checked=False, value=memory_id: self._restore(value)
            )
            layout.addWidget(restore_button)
        else:
            edit_button = QPushButton("编辑")
            edit_button.clicked.connect(
                lambda checked=False, value=memory_id: self._edit(value)
            )
            archive_button = QPushButton("归档")
            archive_button.clicked.connect(
                lambda checked=False, value=memory_id: self._archive(value)
            )
            layout.addWidget(edit_button)
            layout.addWidget(archive_button)
        delete_button = QPushButton("删除")
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(
            lambda checked=False, value=memory_id: self._delete(value)
        )
        layout.addWidget(delete_button)
        return widget

    def _show_detail(self, memory_id: int) -> None:
        operation = self.memory_service.get_memory(memory_id)
        memory = operation.data.get("memory", {}) if operation.success else {}
        if not memory:
            self.status_label.setText(operation.safe_message)
            return
        QMessageBox.information(
            self,
            "记忆详情",
            f"ID：{memory.get('id')}\n"
            f"类别：{MEMORY_CATEGORY_LABELS.get(str(memory.get('category', 'other')), '其他')}\n"
            f"重要度：{memory.get('importance', 3)}\n"
            f"使用次数：{memory.get('use_count', 0)}\n"
            f"更新时间：{memory.get('updated_at', '')}\n\n"
            f"{memory.get('content', '')}",
        )

    def _candidate_actions(self, candidate_id: int) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        accept_button = QPushButton("确认")
        accept_button.setObjectName("primaryButton")
        accept_button.clicked.connect(
            lambda checked=False, value=candidate_id: self._accept(value)
        )
        reject_button = QPushButton("忽略")
        reject_button.clicked.connect(
            lambda checked=False, value=candidate_id: self._reject(value)
        )
        layout.addWidget(accept_button)
        layout.addWidget(reject_button)
        return widget

    def _conflict_actions(self, conflict_id: int) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        for label, resolution in (
            ("用新的", "use_new"),
            ("留旧的", "keep_old"),
            ("都保留", "keep_both"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, value=conflict_id, choice=resolution: self._resolve_conflict(value, choice)
            )
            layout.addWidget(button)
        return widget

    def _accept(self, candidate_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_accept_candidate")
        if self._legacy_accept_callback is not None:
            accepted = bool(self._operation_call(
                logger, trace, lambda: self._legacy_accept_callback(candidate_id)
            ))
            operation = MemoryOperationResult(
                accepted,
                "success" if accepted else "not_found",
                "accept_memory_candidate",
                candidate_id=candidate_id,
                safe_message=(
                    "候选已进入长期记忆或冲突处理。"
                    if accepted
                    else "没有找到这条待确认记忆。"
                ),
            )
        else:
            operation = self._operation_call(
                logger, trace,
                lambda: self.memory_service.accept_candidate(
                    candidate_id, source="desktop_panel"
                ),
            )
        self._finish_operation(logger, trace, operation)
        self.refresh_all()
        self.status_label.setText(operation.safe_message)

    def _reject(self, candidate_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_reject_candidate")
        if self._legacy_reject_callback is not None:
            rejected = bool(self._operation_call(
                logger, trace, lambda: self._legacy_reject_callback(candidate_id)
            ))
            operation = MemoryOperationResult(
                rejected,
                "success" if rejected else "not_found",
                "reject_memory_candidate",
                candidate_id=candidate_id,
                safe_message=(
                    "候选已忽略，不会写入长期记忆。"
                    if rejected
                    else "没有找到这条待确认记忆。"
                ),
            )
        else:
            operation = self._operation_call(
                logger, trace,
                lambda: self.memory_service.reject_candidate(
                    candidate_id, source="desktop_panel"
                ),
            )
        self._finish_operation(logger, trace, operation)
        self.refresh_all()
        self.status_label.setText(operation.safe_message)

    def _edit(self, memory_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_update", memory_id)
        found = self._operation_call(
            logger, trace, lambda: self.memory_service.get_memory(memory_id)
        )
        memory = found.data.get("memory", {}) if found.success else {}
        if not memory:
            self._finish_operation(logger, trace, found)
            self.status_label.setText(found.safe_message)
            return
        content, accepted = QInputDialog.getMultiLineText(
            self, "编辑记忆", "记忆内容", str(memory.get("content", ""))
        )
        if not accepted or not content.strip():
            logger.event(trace, "ui_operation_cancelled", status="cancelled")
            return
        importance, accepted = QInputDialog.getInt(
            self,
            "重要度",
            "重要度（1-5）",
            int(memory.get("importance", 3)),
            1,
            5,
        )
        if accepted:
            operation = self._operation_call(
                logger, trace,
                lambda: self.memory_service.update_memory(
                    memory_id, content=content, importance=importance
                ),
            )
            self._finish_operation(logger, trace, operation)
            self.refresh_all()
            self.status_label.setText(operation.safe_message)
        else:
            logger.event(trace, "ui_operation_cancelled", status="cancelled")

    def _delete(self, memory_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_delete", memory_id)
        answer = QMessageBox.question(
            self,
            "删除长期记忆",
            "确定删除这条长期记忆吗？该操作不会自动撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            operation = self._operation_call(
                logger, trace, lambda: self.memory_service.delete_memory(memory_id)
            )
            self._finish_operation(logger, trace, operation)
            self.refresh_all()
            self.status_label.setText(operation.safe_message)
        else:
            logger.event(trace, "ui_operation_cancelled", status="cancelled")

    def _archive(self, memory_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_archive", memory_id)
        operation = self._operation_call(
            logger, trace, lambda: self.memory_service.archive_memory(memory_id)
        )
        self._finish_operation(logger, trace, operation)
        self.refresh_all()
        self.status_label.setText(operation.safe_message)

    def _restore(self, memory_id: int) -> None:
        logger, trace = self._begin_operation("ui_memory_restore", memory_id)
        operation = self._operation_call(
            logger, trace, lambda: self.memory_service.restore_memory(memory_id)
        )
        self._finish_operation(logger, trace, operation)
        self.refresh_all()
        self.status_label.setText(operation.safe_message)

    def _resolve_conflict(self, conflict_id: int, resolution: str) -> None:
        logger, trace = self._begin_operation("ui_memory_resolve_conflict")
        operation = self._operation_call(
            logger, trace,
            lambda: self.memory_service.resolve_conflict(
                conflict_id, resolution, source="desktop_panel"
            ),
        )
        self._finish_operation(logger, trace, operation)
        self.refresh_all()
        self.status_label.setText(operation.safe_message)

    @staticmethod
    def _begin_operation(source, memory_id=None):
        logger = get_development_log()
        trace = logger.new_trace(source=source)
        logger.event(trace, "ui_operation_started", memory_id=memory_id)
        return logger, trace

    @staticmethod
    def _operation_call(logger, trace, callback):
        try:
            with logger.bind(trace):
                return callback()
        except Exception as error:
            logger.record_exception(trace, error)
            logger.event(
                trace, "ui_operation_finished", status="failed",
                error_type=type(error).__name__,
            )
            raise

    @staticmethod
    def _finish_operation(logger, trace, operation):
        # The result data and summary may contain the edited/deleted private text.
        # Only symbolic outcome and object IDs are projected into developer logs.
        logger.event(
            trace, "ui_operation_finished",
            status=operation.status,
            success=operation.success,
            changed=operation.success and operation.status != "already_processed",
            memory_id=operation.memory_id,
            reason_code=operation.error_code or operation.status,
            tool=operation.operation,
        )

    def showEvent(self, event) -> None:  # noqa: N802
        print("[MemoryUI] open", flush=True)
        self.refresh_all()
        super().showEvent(event)
