"""Compact, read-only help for the existing desktop learning loop.

本窗口只做两件事：把当前确实支持的操作整理成少量按钮和短示例，以及保留
已经确认过的完整说明原文（折叠展示，不强迫每次读全文）。

点击示例只会发出 ``example_selected`` 信号：不执行工具、不写数据、不自动发送，
也不查找或操作聊天窗口。把句子填入输入框并保留草稿由调用方负责。
"""

from __future__ import annotations

from html import escape
from string import Template

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from frontend.settings_dialog import SETTINGS_COLORS


USAGE_SECTIONS = (
    ("先聊起来", (
        "可以自然聊天、提问或讨论学习建议，不需要每句话都写成命令。自然聊天中建议还没有加入计划；只有实际保存成功，才算完成操作。",
        "要执行具体操作时，下面的完整表达更容易核对。一次优先处理一类操作；程序询问确认时，回答“确认”或“取消”。",
    )),
    ("今天的计划与学习", (
        "添加今天的计划：今天加一项计划：复习 Python，30 分钟。当前聊天入口只支持新增今天的计划。",
        "先听建议：给我一个今天的学习计划。如果程序询问“你想现在开始、先听建议，还是把它加入今天计划？”，可以回复“建议”。看过建议后，再明确说“第二项加入今天计划”或“把这些加入今日计划”等。",
        "开始学习：说“开始学习”，选择让洛琪希带着你学，或按自己的规划学习。也可以直接说“带着我学习 Python”。开始学习不等于加入或完成计划。",
        "查询计划：在对话中说：查看今天的计划。也可以在今日成长面板自行查看勾选；也可以说“把今天的复习 Python 标记完成”。刚展示的有效计划列表可用“第二项已经完成了”指明对象。",
    )),
    ("行动记录与今日复盘", (
        "记录已经做过的事（计划之外的事情）：记录今天的计划外行动：复习算法，实际用了 30 分钟。没有填写时长，不代表实际用了 0 分钟。",
        "生成并保存当天复盘：今日复盘。只查看已保存内容：查看今日复盘。保存成功后，今日成长面板会同步显示小结。自然询问“我今天都干了什么”可能得到聊天总结；要明确生成并保存，请说“今日复盘”，并核对保存结果。",
        "聊天窗口的“成长”直接打开今日成长面板，适合随手查看与勾选；面板内的“详情”打开完整的计划、行动、复盘与成长日志管理。",
    )),
    ("长期记忆：先展示，再核对", (
        "保存长期信息：记住：我喜欢清淡饮食。普通聊天中提到一件事，不等于要求保存。",
        "精确查看：展示长期记忆。也可说“查看长期记忆”或“查看记忆”。这些入口按编号展示正式记忆原文，方便核对；想了解“你了解我什么”可以自然问，管理和删除时先展示长期记忆。",
        "删除：从展示结果复制要删除的完整原文，不要包含编号，再说“忘记：我喜欢清淡饮食”，核对真实目标后回答“确认”或“取消”。编号用于阅读，不是删除命令；不要用模型改写后的句子代替原文。",
        "也可以在记忆管理窗口查看、编辑、归档或处理冲突。搜索使用关键词匹配。",
    )),
    ("每日对话与内测反馈", (
        "一天使用一个学习会话；开启聊天历史保存时，同一天重开继续当天记录。历史入口只读回看，点击“返回今天”继续聊天。关闭保存后，重启不能恢复这些聊天。",
        "反馈导出：设置 → 隐私与数据 → 导出内测反馈。填写问题、选择范围、查看清单并明确同意后，生成本地 ZIP；没有自动上传，密钥文件不导出。生成后是否交给开发者，由你决定。",
    )),
    ("内测与使用边界", (
        "这是 Beta 内测版本，可能出现理解或执行错误。请核对实际保存的计划、记忆与复盘；遇到问题保留记录，回复文字本身不能证明操作成功。",
        "聊天与学习记录保存在本机。使用 DeepSeek 时，需要联网并向模型服务发送处理请求所需的内容；请避免输入密码、密钥或不愿交给服务处理的信息。",
        "内测软件包与人物素材仅限获准范围使用，未经许可请勿再次传播。这里是使用提示，不是正式法律协议；具体授权以提供方说明为准。",
    )),
)


# 常用操作：按钮标题、一句边界说明、短示例句子。
# 示例沿用当前能力边界中已经确认的表达，不把尚未实现的行为写成现有能力。
OPERATION_EXAMPLES = (
    (
        "查看今日计划",
        "聊天新增只支持今天的计划；明天、后天等非今天日期不会写入。",
        (
            "查看今天的计划。",
            "查看昨天的计划。",
        ),
    ),
    (
        "请求学习建议",
        "建议本身不写入计划；看过之后再明确说“把第二项加入今天计划”才会新增。",
        (
            "给我一个今天的学习计划。",
            "先只给建议，不要加入计划。",
        ),
    ),
    (
        "记录实际行动",
        "记录已经做过的事情，与计划分开；没有填写时长不代表实际用了 0 分钟。",
        (
            "记录今天的计划外行动：复习算法，实际用了 30 分钟。",
            "记录今天的计划外行动：整理书桌。",
        ),
    ),
    (
        "今日复盘",
        "要保存请说“今日复盘”；保存成功后，今日成长面板会同步显示小结。",
        (
            "今日复盘。",
            "查看今日复盘。",
        ),
    ),
    (
        "展示长期记忆",
        "先展示原文再核对；删除要说“忘记：完整原文”再确认，编号不是删除命令。",
        (
            "展示长期记忆。",
            "查看长期记忆。",
        ),
    ),
)


def usage_html() -> str:
    """Static help only; opening it never reads user records or calls a model."""
    sections = []
    for title, paragraphs in USAGE_SECTIONS:
        sections.append(f"<h3>{escape(title)}</h3>")
        sections.extend(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return "".join(sections)


USAGE_QSS = Template("""
    QDialog { background: $bg; }
    QLabel { color: $muted; font-family: 'Microsoft YaHei'; font-size: 12px; }
    QLabel#usageTitle { color: $title; font-size: 20px; font-weight: 600; }
    QLabel#usageStatus { color: $accent_text; font-size: 12px; }
    QPushButton { background: $accent_soft; color: $accent_text;
        border: 1px solid $accent_border; border-radius: 8px; padding: 6px 18px; }
    QPushButton:hover { background: $accent_soft_pressed; }
    QPushButton#operationButton { padding: 7px 8px; font-size: 13px; }
    QPushButton#operationButton:checked { background: $accent; color: #FFFFFF;
        border-color: $accent; }
    QFrame#usageCard { background: $card; border: 1px solid $card_border;
        border-radius: 12px; }
    QLabel#exampleTitle { color: $title; font-size: 14px; font-weight: 600; }
    QPushButton#exampleButton { background: $accent_soft; color: $text;
        border: 1px solid $card_border; border-radius: 8px; padding: 8px 12px;
        text-align: left; font-size: 13px; }
    QPushButton#exampleButton:hover { background: $accent_soft_pressed;
        color: $accent_text; border-color: $accent_border; }
    QToolButton#detailsToggle { color: $accent_text; background: transparent;
        border: none; padding: 4px 2px; font-family: 'Microsoft YaHei';
        font-size: 13px; }
    QToolButton#detailsToggle:hover { color: $accent_hover; }
    QTextBrowser { background: $card; color: $text; border: 1px solid $card_border;
        border-radius: 12px; padding: 10px; font-family: 'Microsoft YaHei'; font-size: 13px; }
""").substitute(SETTINGS_COLORS)


class UsageDialog(QDialog):
    """只读操作说明窗口；点击示例只发信号，不执行任何业务动作。"""

    # 参数为示例句子原文；调用方负责填入聊天输入框并保留草稿。
    example_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("洛琪希 · 操作说明")
        self.setModal(False)
        self.resize(520, 600)
        self.setMinimumSize(380, 460)
        self.setStyleSheet(USAGE_QSS)
        self.operation_buttons = []
        self.example_buttons = []
        self.operation_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        title = QLabel("和洛琪希一起学习", self)
        title.setObjectName("usageTitle")
        layout.addWidget(title)

        self.subtitle = QLabel(
            "可以自由聊天，也可以安排今天、记录行动、做复盘。"
            "下面都是当前支持的表达。点击只选中这句话，不会执行操作，也不会自动发送。",
            self,
        )
        self.subtitle.setObjectName("usageSubtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        layout.addLayout(self._build_operation_buttons())
        layout.addWidget(self._build_example_card(), 1)

        self.status = QLabel("", self)
        self.status.setObjectName("usageStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        boundary = QLabel(
            "Beta 内测版本，可能出现理解或执行错误；请以实际保存的计划、记忆与复盘为准。",
            self,
        )
        boundary.setObjectName("usageBoundary")
        boundary.setWordWrap(True)
        layout.addWidget(boundary)

        layout.addWidget(self._build_details())

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.select_operation(0)

    # ------------------------------------------------------------------ 构建

    def _build_operation_buttons(self):
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        self.operation_group = QButtonGroup(self)
        self.operation_group.setExclusive(True)
        for index, (label, _note, _examples) in enumerate(OPERATION_EXAMPLES):
            button = QPushButton(label, self)
            button.setObjectName("operationButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, position=index: self.select_operation(position)
            )
            self.operation_group.addButton(button, index)
            self.operation_buttons.append(button)
            grid.addWidget(button, index // 3, index % 3)
        return grid

    def _build_example_card(self):
        card = QFrame(self)
        card.setObjectName("usageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        self.example_title = QLabel("", card)
        self.example_title.setObjectName("exampleTitle")
        card_layout.addWidget(self.example_title)

        self.example_note = QLabel("", card)
        self.example_note.setObjectName("exampleNote")
        self.example_note.setWordWrap(True)
        card_layout.addWidget(self.example_note)

        self.examples_host = QWidget(card)
        self.examples_layout = QVBoxLayout(self.examples_host)
        self.examples_layout.setContentsMargins(0, 0, 0, 0)
        self.examples_layout.setSpacing(6)
        card_layout.addWidget(self.examples_host)
        card_layout.addStretch(1)
        return card

    def _build_details(self):
        """折叠区：保留原说明全文，默认收起，不强迫每次读全文。"""
        box = QWidget(self)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(6)

        self.details_toggle = QToolButton(box)
        self.details_toggle.setObjectName("detailsToggle")
        self.details_toggle.setText("完整说明（含边界与内测提示）")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setChecked(False)
        self.details_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.toggled.connect(self._toggle_details)
        box_layout.addWidget(self.details_toggle)

        self.content = QTextBrowser(box)
        self.content.setObjectName("usageContent")
        self.content.setReadOnly(True)
        self.content.setOpenExternalLinks(False)
        self.content.setOpenLinks(False)
        self.content.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content.document().setDefaultStyleSheet(
            "h3 { margin-top: 18px; margin-bottom: 8px; } "
            "p { margin-top: 5px; margin-bottom: 10px; }"
        )
        self.content.setHtml(usage_html())
        self.content.setVisible(False)
        box_layout.addWidget(self.content)
        return box

    # ------------------------------------------------------------------ 行为

    def set_chat_destination_ready(self) -> None:
        """Update guidance only after the caller connects the chat destination."""
        self.subtitle.setText(
            "可以自由聊天，也可以安排今天、记录行动、做复盘。"
            "点击示例会填入聊天输入框，不会自动发送；已有内容时会询问是否替换。"
        )

    def select_operation(self, index: int) -> None:
        """切换展示的操作与示例；只改界面，不执行工具、不写数据。"""
        index = max(0, min(int(index), len(OPERATION_EXAMPLES) - 1))
        label, note, examples = OPERATION_EXAMPLES[index]
        self.operation_index = index
        if not self.operation_buttons[index].isChecked():
            self.operation_buttons[index].setChecked(True)
        self.example_title.setText(f"{label} · 可以这样说")
        self.example_note.setText(note)
        self.status.setText("")
        self._clear_examples()
        for sentence in examples:
            button = QPushButton(sentence, self.examples_host)
            button.setObjectName("exampleButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, text=sentence: self._select_example(text)
            )
            self.examples_layout.addWidget(button)
            self.example_buttons.append(button)

    def example_texts(self):
        """当前展示的示例句子（按展示顺序）。"""
        return [button.text() for button in self.example_buttons]

    def is_details_visible(self) -> bool:
        """完整说明是否已展开（不受窗口本身是否显示影响）。"""
        return not self.content.isHidden()

    def _clear_examples(self) -> None:
        self.example_buttons = []
        while self.examples_layout.count():
            item = self.examples_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _select_example(self, sentence: str) -> None:
        """只发出示例句子：不执行工具、不写数据、不自动发送、不操作聊天窗口。"""
        self.status.setText(f"已选中：{sentence}")
        self.example_selected.emit(sentence)

    def _toggle_details(self, expanded: bool) -> None:
        self.details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(bool(expanded))
