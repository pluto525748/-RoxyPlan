"""内测反馈导出对话框：填反馈 → 选范围 → 看清单 → 明确同意 → 写 ZIP。

这一步是“最少步骤可运行”的首版：不做逐条聊天编辑器、不做脱敏平台、不做独立
日志系统，只把 ``modules/feedback_export.py`` 的清单与导出结果呈现给人看。

两条硬约束体现在界面流程里：

* **先看清单再同意**：未点击“查看待导出内容”之前，同意勾选与导出按钮都不可用；
  导出前还会重新生成一次清单，若与刚看过的不一致就刷新并要求重新确认，不拿旧的
  同意去导出新的内容。
* **取消与失败都不算成功**：取消保存对话框、清单为空、写入失败都走明确的失败
  提示，不显示成功文案，也不留下文件。

界面沿用设置窗口已验收的 ``SETTINGS_COLORS`` 与 ``SETTINGS_QSS``，只补充本窗口
用到的控件样式，不引入第二套配色。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from string import Template

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from modules.feedback_export import (
    CATEGORY_CHAT,
    CATEGORY_DIAGNOSTICS,
    CATEGORY_GROWTH,
    CATEGORY_MEMORY,
    CATEGORY_HINTS,
    CATEGORY_LABELS,
    FeedbackText,
    ExportScope,
    default_scope,
)

from frontend.settings_dialog import SETTINGS_COLORS, SETTINGS_QSS


FEEDBACK_QSS = SETTINGS_QSS + Template(
    """
QLabel#sectionTitle { font-size: 14px; font-weight: 600; color: $title; }
QLabel#warnNote { font-size: 12px; color: $danger_text; }
QLabel#resultLine { font-size: 12px; color: $accent_text; }
QPlainTextEdit {
    background: $card;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 5px 9px;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
    color: $text;
    selection-background-color: $accent;
    selection-color: #FFFFFF;
}
QPlainTextEdit:focus { border-color: $accent; }
QDateEdit {
    background: $card;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 4px 8px;
    min-height: 22px;
    font-family: 'Microsoft YaHei';
    font-size: 13px;
    color: $text;
}
QDateEdit:focus { border-color: $accent; }
QListWidget {
    background: $card;
    border: 1px solid $input_border;
    border-radius: 8px;
    padding: 4px;
    font-family: 'Microsoft YaHei';
    font-size: 12px;
    color: $text;
}
QListWidget::item { padding: 3px 6px; border-radius: 6px; }
QListWidget::item:selected { background: $accent_soft; color: $accent_text; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""
).substitute(SETTINGS_COLORS)


def _scope_signature(scope: ExportScope) -> tuple:
    """用于判断“刚看过的清单”和当前选择是否还是同一件事。"""
    return (
        scope.start.isoformat(),
        scope.end.isoformat(),
        scope.include_chat,
        scope.include_growth,
        scope.include_diagnostics,
        scope.include_memory,
    )


def _entry_text(entry) -> str:
    line = f"[{entry.status_label}] {entry.category_label} · {entry.archive_path}"
    if entry.detail:
        line += f" — {entry.detail}"
    return line


class FeedbackExportDialog(QDialog):
    """把反馈导出服务包成一个能点的窗口；本身不写文件，写文件交给服务。"""

    feedback_exported = Signal(str)

    def __init__(self, service, parent=None, display_info=None):
        super().__init__(parent)
        self.service = service
        self.display_info = dict(display_info or {})
        self.service_available = False
        self._plan = None
        self._plan_signature = None

        self.setWindowTitle("导出内测反馈")
        self.setMinimumSize(520, 560)
        self.resize(640, 680)
        if parent is not None:
            # 不阻塞事件循环，但挡住父窗口，避免清单看过之后范围又被改掉。
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build_ui()
        self._apply_availability()

    # ------------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("导出内测反馈")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtitle = QLabel("本地打包成一个 ZIP，没有后台上传；上传与否由你自己决定。")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 2)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._build_feedback_card())
        content_layout.addWidget(self._build_scope_card())
        content_layout.addWidget(self._build_review_card())
        content_layout.addStretch(1)
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, 1)

        self.result_label = QLabel()
        self.result_label.setObjectName("resultLine")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        buttons.rejected.connect(self.reject)

        self.export_button = QPushButton("导出 ZIP…")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        buttons.addButton(self.export_button, QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(buttons)

        self.setStyleSheet(FEEDBACK_QSS)

    def _card(self, title_text: str) -> tuple:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 16)
        card_layout.setSpacing(10)
        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        card_layout.addWidget(title)
        return card, card_layout

    def _build_feedback_card(self):
        card, card_layout = self._card("反馈内容")
        self.problem_edit = self._text_field("发生了什么问题？", 64, card_layout, "问题")
        self.expected_edit = self._text_field("你希望它怎么做？", 52, card_layout, "预期")
        self.actual_edit = self._text_field("它实际做成了什么？", 52, card_layout, "实际")
        note = QLabel("可以只填其中一项；文字会原样放进反馈包。")
        note.setObjectName("rowHint")
        note.setWordWrap(True)
        card_layout.addWidget(note)
        return card

    def _text_field(self, placeholder: str, height: int, card_layout, label_text: str):
        label = QLabel(label_text)
        label.setObjectName("rowLabel")
        card_layout.addWidget(label)
        edit = QPlainTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(height)
        edit.textChanged.connect(self._on_feedback_changed)
        card_layout.addWidget(edit)
        return edit

    def _build_scope_card(self):
        card, card_layout = self._card("导出范围")

        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        default = self.service.suggested_scope()
        self.start_date = QDateEdit(QDate(default.start.year, default.start.month, default.start.day))
        self.end_date = QDateEdit(QDate(default.end.year, default.end.month, default.end.day))
        for edit in (self.start_date, self.end_date):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.dateChanged.connect(self._on_scope_changed)
        recent_button = QPushButton("最近 7 天")
        recent_button.clicked.connect(self._reset_range)

        range_row.addWidget(QLabel("时间范围"))
        range_row.addWidget(self.start_date)
        range_row.addWidget(QLabel("至"))
        range_row.addWidget(self.end_date)
        range_row.addWidget(recent_button)
        range_row.addStretch(1)
        card_layout.addLayout(range_row)

        self.chat_check = self._category_check(
            CATEGORY_CHAT, card_layout, default=False
        )
        self.growth_check = self._category_check(
            CATEGORY_GROWTH, card_layout, default=True
        )
        self.diagnostics_check = self._category_check(
            CATEGORY_DIAGNOSTICS, card_layout, default=True
        )
        self.memory_check = self._category_check(
            CATEGORY_MEMORY, card_layout, default=False
        )

        warn = QLabel(
            "“已保存的聊天”和“长期记忆”是正文内容，选中后会一起交给开发者；默认不选。"
        )
        warn.setObjectName("warnNote")
        warn.setWordWrap(True)
        card_layout.addWidget(warn)
        return card

    def _category_check(self, category: str, card_layout, default: bool) -> QCheckBox:
        check = QCheckBox(f"{CATEGORY_LABELS[category]}（{CATEGORY_HINTS[category]}）")
        check.setChecked(default)
        check.toggled.connect(self._on_scope_changed)
        card_layout.addWidget(check)
        return check

    def _build_review_card(self):
        card, card_layout = self._card("待导出内容")

        self.preview_button = QPushButton("查看待导出内容")
        self.preview_button.clicked.connect(self._preview)
        card_layout.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.entries_list = QListWidget()
        self.entries_list.setFixedHeight(150)
        self.entries_list.setVisible(False)
        card_layout.addWidget(self.entries_list)

        self.notes_label = QLabel()
        self.notes_label.setObjectName("rowHint")
        self.notes_label.setWordWrap(True)
        self.notes_label.setVisible(False)
        card_layout.addWidget(self.notes_label)

        self.consent_check = QCheckBox(
            "我已看过上面的清单，同意把列出的内容交给开发者"
        )
        self.consent_check.setEnabled(False)
        self.consent_check.toggled.connect(
            lambda checked: self.export_button.setEnabled(checked and self.service_available)
        )
        card_layout.addWidget(self.consent_check)

        note = QLabel("清单确认后才会写 ZIP；取消或写入失败都不会生成文件。")
        note.setObjectName("rowHint")
        note.setWordWrap(True)
        card_layout.addWidget(note)
        return card

    # ------------------------------------------------------------------ 状态

    def _apply_availability(self) -> None:
        self.service_available = bool(
            self.service is not None and getattr(self.service, "available", False)
        )
        if self.service_available:
            return
        reason = (
            getattr(self.service, "unavailable_reason", "")
            or "未接入导出服务，导出不可用。"
        )
        for widget in (
            self.problem_edit, self.expected_edit, self.actual_edit,
            self.start_date, self.end_date, self.preview_button,
            self.chat_check, self.growth_check,
            self.diagnostics_check, self.memory_check,
        ):
            widget.setEnabled(False)
        self.consent_check.setEnabled(False)
        self.export_button.setEnabled(False)
        self._show_result(reason, warn=True)

    def _show_result(self, text: str, warn: bool = False) -> None:
        self.result_label.setText(text)
        self.result_label.setObjectName("warnNote" if warn else "resultLine")
        self.result_label.setVisible(bool(text))
        # 换了 objectName 要重新套用样式表，否则颜色不跟着变。
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)

    def _set_notes(self, notes) -> None:
        text = "\n".join(f"· {note}" for note in notes)
        self.notes_label.setText(text)
        self.notes_label.setVisible(bool(text))

    def _current_scope(self) -> ExportScope:
        start = self.start_date.date()
        end = self.end_date.date()
        return ExportScope(
            start=date(start.year(), start.month(), start.day()),
            end=date(end.year(), end.month(), end.day()),
            include_chat=self.chat_check.isChecked(),
            include_growth=self.growth_check.isChecked(),
            include_diagnostics=self.diagnostics_check.isChecked(),
            include_memory=self.memory_check.isChecked(),
        )

    def _current_feedback(self) -> FeedbackText:
        return FeedbackText(
            problem=self.problem_edit.toPlainText(),
            expected=self.expected_edit.toPlainText(),
            actual=self.actual_edit.toPlainText(),
        )

    # ------------------------------------------------------------ 变更与失效

    def _reset_range(self) -> None:
        default = self.service.suggested_scope()
        self.start_date.setDate(QDate(default.start.year, default.start.month, default.start.day))
        self.end_date.setDate(QDate(default.end.year, default.end.month, default.end.day))

    def _on_scope_changed(self, *_args) -> None:
        """范围变了，刚看过的清单就作废，必须重新看一眼再同意。"""
        self._plan = None
        self._plan_signature = None
        self.entries_list.clear()
        self.entries_list.setVisible(False)
        self.notes_label.setVisible(False)
        self.consent_check.setChecked(False)
        self.consent_check.setEnabled(False)
        self.export_button.setEnabled(False)
        self._show_result("")

    def _on_feedback_changed(self, *_args) -> None:
        """反馈文字也算“以上内容”，改过就要重新确认。"""
        if self.consent_check.isChecked():
            self.consent_check.setChecked(False)
        self.export_button.setEnabled(False)

    # ------------------------------------------------------------------ 清单

    def _preview(self) -> None:
        scope = self._current_scope()
        problem = scope.validate()
        if problem:
            self._show_result(f"{problem}，请先改好时间范围。", warn=True)
            return
        plan = self.service.plan(scope)
        self._plan = plan
        self._plan_signature = _scope_signature(scope)
        self._populate(plan)
        if plan.included:
            self.consent_check.setEnabled(True)
            self._show_result("清单已列出，请确认后勾选同意。")
        else:
            self.consent_check.setEnabled(False)
            self.export_button.setEnabled(False)
            self._show_result("所选范围内没有可导出的内容，请调整范围。", warn=True)

    def _populate(self, plan) -> None:
        self.entries_list.clear()
        for entry in plan.entries:
            self.entries_list.addItem(_entry_text(entry))
        self.entries_list.setVisible(bool(plan.entries))
        self._set_notes(plan.notes)

    # ------------------------------------------------------------------ 导出

    def _export(self) -> None:
        if not self.service_available:
            self._show_result(
                getattr(self.service, "unavailable_reason", "") or "导出不可用。", warn=True
            )
            return
        scope = self._current_scope()
        if self._plan is None or _scope_signature(scope) != self._plan_signature:
            self._show_result("导出范围已改变，请先重新查看清单再确认。", warn=True)
            return
        if not self.consent_check.isChecked():
            self._show_result("请先勾选同意，再导出。", warn=True)
            return

        # 重新取一次清单：目录在这期间可能变了，不能拿旧的同意导出新的内容。
        fresh = self.service.plan(scope)
        if [entry.as_document() for entry in fresh.entries] != [
            entry.as_document() for entry in self._plan.entries
        ]:
            self._plan = fresh
            self._plan_signature = _scope_signature(scope)
            self._populate(fresh)
            self.consent_check.setChecked(False)
            self.consent_check.setEnabled(bool(fresh.included))
            self.export_button.setEnabled(False)
            self._show_result(
                "待导出内容与刚才查看的清单不一致，清单已刷新，请重新确认。", warn=True
            )
            return

        target, _filter = QFileDialog.getSaveFileName(
            self, "保存反馈包", str(self._default_target()), "ZIP 压缩包 (*.zip)"
        )
        if not target:
            # 用户取消保存：不写文件，也不算成功。
            self._show_result("已取消，没有生成文件。", warn=True)
            return

        outcome = self.service.export(
            fresh, self._current_feedback(), Path(target), environment=self.display_info
        )
        if not outcome.ok:
            self._show_result(f"导出失败：{outcome.error}", warn=True)
            return
        self.export_button.setEnabled(False)
        self.consent_check.setEnabled(False)
        self._show_result(
            f"已导出 {outcome.file_count} 个文件（{outcome.byte_count} 字节）到：{outcome.target}"
        )
        self.feedback_exported.emit(str(outcome.target))

    @staticmethod
    def _default_target() -> Path:
        return Path.home() / f"RoxyPlan-反馈-{date.today().isoformat()}.zip"
