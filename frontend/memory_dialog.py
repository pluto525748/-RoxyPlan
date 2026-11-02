from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
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

# 已验收设置界面的调色板定义在 frontend/settings_dialog.py，这里只读复用同一份常量，
# 避免“奶白 / 浅蓝 / 雾紫”在两处各自维护后慢慢漂移。
from frontend.settings_dialog import SETTINGS_COLORS as UI_COLORS
from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MEMORY_CATEGORIES, MemoryManager
from modules.memory_service import MemoryOperationResult, MemoryService
from modules.development_log import get_development_log


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 记忆面板没有专属素材：表头徽章、分隔星饰与下拉箭头沿用素材包 V1 已生成的切片，
# 这里只读引用 assets/ui/settings/。如以后为记忆面板新增素材，放到独立目录
# assets/ui/memory/，不要覆盖其他界面正在使用的图片。
UI_ASSET_DIR = PROJECT_ROOT / "assets" / "ui" / "settings"

# 表格单元格最多显示的字数：长记忆在表格里只看摘要，完整内容仍可通过悬停提示
# 和“详情”弹窗查看，避免一条长记忆把整张表格撑成大段文字。
CONTENT_PREVIEW_LIMIT = 96

# 表格专用补充色（隔行底色与行分隔线），其余令牌与设置界面完全一致。
MEMORY_COLORS = {
    **UI_COLORS,
    "row_alt": "#F7F8FE",
    "row_line": "#EEF0F8",
}

MEMORY_QSS = Template(
    """
QDialog { background: $bg; }
QLabel { font-family: 'Microsoft YaHei'; font-size: 13px; color: $text; }
QLabel#dialogTitle { font-size: 20px; font-weight: 600; color: $title; }
QLabel#dialogSubtitle { font-size: 12px; color: $muted; }
QLabel#memoryStatus { font-size: 12px; color: $muted; }
QLabel#emptyHint { font-size: 12px; color: $muted; padding: 8px 2px; }
QFrame#headerLine { border: none; border-top: 1px solid $card_border; max-height: 1px; }

/* 页签做成浅色胶囊，内容面板做成与设置界面一致的白色卡片。 */
QTabWidget#memoryTabs::pane {
    background: $card;
    border: 1px solid $card_border;
    border-radius: 14px;
}
QTabBar::tab {
    background: transparent;
    color: $muted;
    padding: 7px 18px;
    margin: 0px 6px 6px 0px;
    border: 1px solid transparent;
    border-radius: 9px;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
}
QTabBar::tab:hover { background: $accent_soft; color: $accent_text; }
QTabBar::tab:selected {
    background: $card;
    color: $accent_text;
    border: 1px solid $accent_border;
    font-weight: 600;
}

QTableWidget {
    background: transparent;
    border: none;
    color: $text;
    alternate-background-color: $row_alt;
    gridline-color: transparent;
    outline: none;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
}
QTableWidget::item { padding: 7px 8px; border-bottom: 1px solid $row_line; }
QHeaderView::section {
    background: $accent_soft;
    color: $button_text;
    border: none;
    border-bottom: 1px solid $card_border;
    padding: 8px;
    font-family: 'Microsoft YaHei';
    font-size: 12px;
    font-weight: 600;
}
QTableCornerButton::section { background: $accent_soft; border: none; }

QLineEdit, QComboBox {
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
QLineEdit:focus, QComboBox:focus { border-color: $accent; }
/* 下拉箭头：自定义 drop-down 后 Qt 不再绘制默认箭头，这里沿用素材包箭头图片。 */
QComboBox { padding-right: 30px; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow { image: url("$combo_arrow"); width: 8px; height: 5px; }
QComboBox QAbstractItemView {
    background: $card;
    border: 1px solid $card_border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_soft;
    selection-color: $text;
}

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
QPushButton#primaryButton {
    background: $accent;
    color: #FFFFFF;
    border: none;
    padding: 7px 22px;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: $accent_hover; }
QPushButton#primaryButton:pressed { background: $accent_pressed; }
/* 表格行内按钮：缩小内边距与字号，避免一列按钮挤占内容列。 */
QPushButton#rowButton {
    background: $card;
    color: $button_text;
    border: 1px solid $input_border;
    border-radius: 7px;
    padding: 3px 10px;
    font-size: 12px;
}
QPushButton#rowButton:hover { background: $accent_soft; border-color: $accent_border; }
QPushButton#dangerButton {
    background: $danger_soft;
    color: $danger_text;
    border: 1px solid $danger_border;
    border-radius: 7px;
    padding: 3px 10px;
    font-size: 12px;
}
QPushButton#dangerButton:hover { background: $danger_soft_hover; border-color: $danger_text; }

QToolTip {
    background: $card;
    color: $text;
    border: 1px solid $card_border;
    padding: 4px 8px;
    font-family: 'Microsoft YaHei';
    font-size: 12px;
}

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll; border-radius: 4px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: $scroll_hover; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
"""
).substitute(
    {
        **MEMORY_COLORS,
        "combo_arrow": (UI_ASSET_DIR / "spin_down.png").as_posix(),
    }
)


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


def _asset_pixmap(name: str, size: int) -> Optional[QPixmap]:
    """按 2 倍逻辑尺寸解码素材切片，Windows 150% 缩放下依旧清晰。"""
    path = UI_ASSET_DIR / name
    if not path.exists():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    ratio = 2.0
    scaled = pixmap.scaled(
        int(size * ratio),
        int(size * ratio),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(ratio)
    return scaled


def _preview_text(content: str, limit: int = CONTENT_PREVIEW_LIMIT) -> str:
    """单元格摘要：折叠换行与连续空白，超长时截断。"""
    text = " ".join(str(content).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


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
        # 记忆内容普遍比设置项长，窗口允许比聊天窗口更宽，避免内容列被迫换行裁切。
        # 尺寸按逻辑像素给出，在 Windows 2560×1600 / 150% 缩放下仍有余量。
        self.resize(1060, 700)
        self.setMinimumSize(900, 560)
        self._build_ui()
        self.refresh_all()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("memoryTabs")
        self.tabs.addTab(self._build_active_tab(), "长期记忆")
        self.tabs.addTab(self._build_conflict_tab(), "冲突")
        self.tabs.addTab(self._build_archived_tab(), "已归档")
        # Keep the compatibility widget alive for advanced callers without
        # exposing candidate review as a normal user tab.
        self._candidate_compatibility_tab = self._build_candidate_tab()
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("memoryStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        close_row = QHBoxLayout()
        close_row.setSpacing(10)
        close_row.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_all)
        close_button = QPushButton("关闭")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.close)
        close_row.addWidget(refresh_button)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        self.setStyleSheet(MEMORY_QSS)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        emblem = QLabel()
        emblem_pixmap = _asset_pixmap("header_emblem.png", 44)
        if emblem_pixmap is not None:
            emblem.setPixmap(emblem_pixmap)
        title_row.addWidget(emblem)
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("记忆管理")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("长期记忆保存在本地，可在这里查看、编辑、归档和搜索。")
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
        star_pixmap = _asset_pixmap("title_star.png", 16)
        if star_pixmap is not None:
            star.setPixmap(star_pixmap)
        divider_row.addWidget(star, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addLayout(divider_row)
        return header

    def _build_active_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索长期记忆...")
        self.search_input.returnPressed.connect(self.refresh_memories)
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部类别", "")
        for category in sorted(MEMORY_CATEGORIES):
            self.category_filter.addItem(MEMORY_CATEGORY_LABELS[category], category)
        self.category_filter.setMinimumWidth(132)
        self.category_filter.currentIndexChanged.connect(self.refresh_memories)
        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.refresh_memories)
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.category_filter)
        toolbar.addWidget(search_button)
        layout.addLayout(toolbar)
        # 空状态提示紧跟搜索栏：筛选后没有结果时，说明文字就在触发它的输入旁边，
        # 而不是浮在整张空表格的最底部。
        self.memory_empty_hint = self._empty_hint("没有符合条件的长期记忆。")
        layout.addWidget(self.memory_empty_hint)
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
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        self.conflict_table = QTableWidget(0, 4)
        self.conflict_table.setHorizontalHeaderLabels(["类别", "旧记忆", "新信息", "处理"])
        self._configure_table(self.conflict_table)
        self._align_headers(
            self.conflict_table,
            {
                0: Qt.AlignmentFlag.AlignCenter,
                1: Qt.AlignmentFlag.AlignLeft,
                2: Qt.AlignmentFlag.AlignLeft,
                3: Qt.AlignmentFlag.AlignCenter,
            },
        )
        header = self.conflict_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.conflict_empty_hint = self._empty_hint("当前没有待处理的冲突。")
        layout.addWidget(self.conflict_empty_hint)
        layout.addWidget(self.conflict_table, 1)
        return tab

    def _build_archived_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        self.archived_empty_hint = self._empty_hint("归档区还是空的。")
        layout.addWidget(self.archived_empty_hint)
        self.archived_table = self._memory_table()
        layout.addWidget(self.archived_table, 1)
        return tab

    def _empty_hint(self, text: str) -> QLabel:
        hint = QLabel(text)
        hint.setObjectName("emptyHint")
        hint.setVisible(False)
        return hint

    def _memory_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["ID", "类别", "内容", "重要度", "操作"])
        self._configure_table(table)
        self._align_headers(
            table,
            {
                0: Qt.AlignmentFlag.AlignCenter,
                1: Qt.AlignmentFlag.AlignCenter,
                2: Qt.AlignmentFlag.AlignLeft,
                3: Qt.AlignmentFlag.AlignCenter,
                4: Qt.AlignmentFlag.AlignCenter,
            },
        )
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
        table.horizontalHeader().setHighlightSections(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(True)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        # 像素级滚动：长列表在 150% 缩放下不会出现半行跳动。
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    @staticmethod
    def _align_headers(table: QTableWidget, alignments: dict) -> None:
        """表头对齐跟随列内容：短列居中，文字列左对齐，空表时也保持一致。"""
        for column, alignment in alignments.items():
            item = table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)

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
        self._fill_memory_table(
            self.memory_table,
            memories,
            archived=False,
            empty_hint=self.memory_empty_hint,
            loaded=operation.success,
        )
        if not operation.success:
            self.status_label.setText(operation.safe_message)

    def refresh_archived(self) -> None:
        operation = self.memory_service.list_memories(status="archived")
        memories = operation.data.get("memories", []) if operation.success else []
        self._fill_memory_table(
            self.archived_table,
            memories,
            archived=True,
            empty_hint=self.archived_empty_hint,
            loaded=operation.success,
        )

    def _fill_memory_table(
        self,
        table,
        memories,
        *,
        archived: bool,
        empty_hint: QLabel,
        loaded: bool = True,
    ) -> None:
        table.setRowCount(0)
        for memory in memories:
            row = table.rowCount()
            table.insertRow(row)
            memory_id = int(memory.get("id", 0))
            table.setItem(row, 0, self._cell(str(memory_id), centered=True))
            table.setItem(
                row,
                1,
                self._cell(
                    MEMORY_CATEGORY_LABELS.get(
                        str(memory.get("category", "other")), "其他"
                    ),
                    centered=True,
                ),
            )
            table.setItem(row, 2, self._content_cell(str(memory.get("content", ""))))
            table.setItem(
                row, 3, self._cell(str(memory.get("importance", 3)), centered=True)
            )
            table.setCellWidget(row, 4, self._memory_actions(memory_id, archived))
        table.resizeRowsToContents()
        # 读取失败时不显示“没有记忆”提示，避免把读取错误说成空数据。
        empty_hint.setVisible(bool(loaded and not memories))

    @staticmethod
    def _cell(text: str, *, centered: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if centered:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    @staticmethod
    def _content_cell(content: str) -> QTableWidgetItem:
        item = QTableWidgetItem(_preview_text(content))
        full_text = " ".join(str(content).split())
        if len(full_text) > CONTENT_PREVIEW_LIMIT:
            # 表格只显示摘要，完整内容在“详情”弹窗和这里保留。
            item.setToolTip(full_text)
        return item

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
                row,
                0,
                self._cell(MEMORY_CATEGORY_LABELS.get(category, "其他"), centered=True),
            )
            self.conflict_table.setItem(
                row, 1, self._content_cell(str(old.get("content", "")))
            )
            self.conflict_table.setItem(
                row, 2, self._content_cell(str(conflict.get("new_content", "")))
            )
            self.conflict_table.setCellWidget(
                row, 3, self._conflict_actions(int(conflict.get("id", 0)))
            )
        self.conflict_table.resizeRowsToContents()
        self.conflict_empty_hint.setVisible(
            bool(operation.success and not conflicts)
        )

    def _memory_actions(self, memory_id: int, archived: bool) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        detail_button = self._row_button("详情", lambda: self._show_detail(memory_id))
        layout.addWidget(detail_button)
        if archived:
            restore_button = self._row_button("恢复", lambda: self._restore(memory_id))
            layout.addWidget(restore_button)
        else:
            edit_button = self._row_button("编辑", lambda: self._edit(memory_id))
            archive_button = self._row_button("归档", lambda: self._archive(memory_id))
            layout.addWidget(edit_button)
            layout.addWidget(archive_button)
        delete_button = self._row_button(
            "删除", lambda: self._delete(memory_id), object_name="dangerButton"
        )
        layout.addWidget(delete_button)
        return widget

    @staticmethod
    def _row_button(text: str, slot, *, object_name: str = "rowButton") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.clicked.connect(lambda checked=False: slot())
        return button

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
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        for label, resolution in (
            ("用新的", "use_new"),
            ("留旧的", "keep_old"),
            ("都保留", "keep_both"),
        ):
            button = self._row_button(
                label,
                lambda choice=resolution: self._resolve_conflict(conflict_id, choice),
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
