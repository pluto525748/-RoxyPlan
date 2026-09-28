from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modules.chat_history_manager import ChatHistoryManager


class ChatHistoryDialog(QDialog):
    """Compact local session browser for the chat window."""

    def __init__(
        self,
        history_manager: ChatHistoryManager,
        open_callback: Callable[[str], None],
        delete_callback: Callable[[str], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.history_manager = history_manager
        self.open_callback = open_callback
        self.delete_callback = delete_callback
        self.setWindowTitle("RoxyPlan · 历史对话")
        self.resize(680, 430)
        self.setMinimumSize(580, 340)
        self._build_ui()
        self.refresh_sessions()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("本地历史对话")
        title.setObjectName("historyTitle")
        subtitle = QLabel("聊天记录只保存在 data/private/，不会进入长期记忆。")
        subtitle.setObjectName("historySubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["标题", "开始时间", "最近更新", "消息", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("historyStatus")
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_sessions)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        button_row.addWidget(refresh_button)
        button_row.addWidget(close_button)
        root.addLayout(button_row)

        self.setStyleSheet(
            """
            QDialog { background: #F7F4FF; color: #333333; font-family: "Microsoft YaHei"; }
            QLabel#historyTitle { font-size: 20px; font-weight: 600; color: #3E4770; }
            QLabel#historySubtitle, QLabel#historyStatus { font-size: 12px; color: #777777; }
            QTableWidget { background: #FFFFFF; border: 1px solid #D8CCFF; border-radius: 8px;
                           alternate-background-color: #F8FAFF; color: #333333; }
            QHeaderView::section { background: #EAF4FF; color: #59658F; border: none;
                                   border-bottom: 1px solid #D8CCFF; padding: 7px; }
            QPushButton { background: #EAF4FF; color: #536392; border: 1px solid #C7D9F7;
                          border-radius: 6px; padding: 6px 10px; }
            QPushButton:hover { background: #DCEBFF; }
            QPushButton#openButton { background: #8EA7FF; color: white; border: none; }
            QPushButton#openButton:hover { background: #7894F8; }
            """
        )

    def refresh_sessions(self) -> None:
        sessions = self.history_manager.sessions()
        self.table.setRowCount(0)
        for session in sessions:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(session.get("title", "新对话"))))
            self.table.setItem(row, 1, QTableWidgetItem(self._format_time(session.get("started_at"))))
            self.table.setItem(row, 2, QTableWidgetItem(self._format_time(session.get("updated_at"))))
            self.table.setItem(row, 3, QTableWidgetItem(str(session.get("message_count", 0))))
            self.table.setCellWidget(
                row, 4, self._operation_widget(str(session.get("session_id", "")))
            )
        self.table.resizeRowsToContents()
        self.status_label.setText(
            f"共有 {len(sessions)} 个本地会话。" if sessions else "还没有历史会话。"
        )

    def _operation_widget(self, session_id: str) -> QWidget:
        widget = QWidget()
        widget.setAccessibleName(f"会话操作 {session_id}")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        open_button = QPushButton("打开")
        open_button.setObjectName("openButton")
        open_button.setAccessibleName(f"打开会话 {session_id}")
        open_button.clicked.connect(
            lambda checked=False, value=session_id: self._open(value)
        )
        delete_button = QPushButton("删除")
        delete_button.setAccessibleName(f"删除会话 {session_id}")
        delete_button.clicked.connect(
            lambda checked=False, value=session_id: self._delete(value)
        )
        layout.addWidget(open_button)
        layout.addWidget(delete_button)
        return widget

    def _open(self, session_id: str) -> None:
        self.open_callback(session_id)
        self.accept()

    def _delete(self, session_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "删除对话",
            "确定删除这个本地会话吗？此操作不会删除长期记忆。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_callback(session_id)
        self.refresh_sessions()

    @staticmethod
    def _format_time(value) -> str:
        text = str(value or "")
        return text.replace("T", " ")[:16]
