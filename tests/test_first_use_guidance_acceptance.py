"""首次使用（全新合成实例）独立验收 —— 2026-10-18 独立引导验收批。

本模块面向“第一次使用”的离线验收，复用现有组合根与界面组件，不新建引导系统，
不替换产品判断函数，不调用在线模型，不读写正式用户数据。

范围说明：

- A 组：打开聊天、帮助入口、示例只填入不发送、草稿替换取消、历史只读、
  帮助回看、实例隔离、设置保存保留旧字段、设置连接/反馈/清空历史的原机制。
- B 组：真实首次提示（`ChatWindow.showEvent` → `first_use_hint` → 实例配置
  `first_use_hint_seen`）——首次出现、同根重启不再出现、不同根独立提示、
  “看看示例”复用操作说明且不自动发送；原互动菜单的操作说明可回看。
- 三个核心业务对照（建议未采用零写入、明确采用后 UID/字段与 ToolResult 一致、
  取消后数据不变）属于后续同版本完整服务链场景，见准备清单 C 节，本模块不以
  按钮文字或旧回放结果替代。

组合方式：`_acceptance_desktop_pet` 生成的 ChatWindow 带有桌宠控制器，
帮助窗口由桌宠持有并复用（`pet.usage_dialog`），示例信号经桌宠转回聊天窗口；
这正是桌面入口的真实链路。

“同根重启”在本模块中通过关闭旧窗口/服务、在同一数据根重新构造组合根并从
落盘配置读取标志来验证；它不是独立进程或 EXE 重启，报告中不得写成后者已验。

全部 offscreen；无可见窗口、截图、在线请求或正式数据写入。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from frontend.first_use_hint import HINT_TEXT
from frontend.pet_app import ChatWindow, _acceptance_desktop_pet, _initialize_desktop_runtime
from frontend.settings_dialog import (
    SettingsDialog,
    first_use_hint_seen,
    save_pet_settings,
)
from modules.chat_history_manager import ChatHistoryManager
from modules.development_log import configure_development_log
from modules.llm.secret_store import SecretStore
from modules.llm.usage_store import ModelUsageStore
from modules.runtime_paths import RuntimePaths, SOURCE_ROOT
from modules.simulation_clock import SimulatedBusinessClock


BUSINESS_DATE = "2026-10-18"
PAST_DATE = "2026-10-17"


class _NoModelClient:
    """任何模型调用都直接失败：首次使用验收不得发起在线请求。"""

    def chat(self, _messages):
        raise AssertionError("首次使用验收不得调用模型")


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _snapshot(root: Path) -> dict[str, bytes]:
    """只比较业务数据文件；日志、锁文件与临时诊断不属于业务状态。"""
    paths = [root / "data" / "pet_config.json", root / "memory.json"]
    private = root / "data" / "private"
    if private.exists():
        paths.extend(sorted(private.glob("*.json")))
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in paths
        if path.is_file()
    }


def _non_chat_snapshot(root: Path) -> dict[str, bytes]:
    """排除聊天会话文件和实例 UI 配置，用于观察计划／行动／复盘／记忆。"""
    excluded = {
        str(Path("data/pet_config.json")),
        str(Path("data/private/chat_history.json")),
        str(Path("data/private/chat_summaries.json")),
    }
    return {
        name: data for name, data in _snapshot(root).items() if name not in excluded
    }


def _fresh_pet(
    tmp_path: Path, monkeypatch, name: str = "fresh-instance"
) -> tuple[object, Path]:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    root = tmp_path / name
    clock = SimulatedBusinessClock(root, BUSINESS_DATE)
    pet = _acceptance_desktop_pet(
        root, configure_development_log(root / "logs" / "development"), clock
    )
    return pet, root


def _restart_pet(root: Path) -> object:
    """同一数据根重建窗口与服务；从落盘配置读取，不复用旧对象的内存标志。"""
    clock = SimulatedBusinessClock(root)
    return _acceptance_desktop_pet(
        root, configure_development_log(root / "logs" / "development"), clock
    )


def _open_chat(pet):
    pet.open_chat_window()
    chat = pet.chat_window
    assert isinstance(chat, ChatWindow)
    chat.llm_client = _NoModelClient()
    chat.conversation_service.llm_client = chat.llm_client
    return chat


def _find_button(parent, text: str):
    for button in parent.findChildren(QPushButton):
        if button.text() == text:
            return button
    return None


def _close_pet(pet) -> None:
    chat = getattr(pet, "chat_window", None)
    if chat is not None:
        chat.close()
        chat.deleteLater()
    dialog = getattr(pet, "usage_dialog", None)
    if dialog is not None:
        dialog.close()
        dialog.deleteLater()
    pet.close()
    pet.deleteLater()


# ── A1：打开聊天，保留原操作说明入口 ────────────────────────────────


def test_fresh_instance_opens_chat_and_help_entries(tmp_path, monkeypatch) -> None:
    app = _app()
    pet, _root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        assert chat.isVisible()
        assert chat.usage_action.text() == "操作说明"
        assert not chat.findChildren(QPushButton, "usageLinkButton")
        assert chat.usage_action in chat.interaction_menu.actions()

        chat.usage_action.trigger()
        app.processEvents()
        dialog = pet.usage_dialog
        assert dialog is not None and dialog.isVisible()
        assert dialog.example_texts(), "操作说明应展示当前支持的短示例"
        assert "填入聊天输入框" in dialog.subtitle.text()

        settings = SettingsDialog(
            config_file=tmp_path / "help-entry-config.json",
            secret_store=SecretStore(tmp_path / "secrets"),
            usage_store=ModelUsageStore(tmp_path / "usage"),
            knowledge_dir=tmp_path / "knowledge",
        )
        try:
            assert _find_button(settings, "打开操作说明") is not None
        finally:
            settings.close()
            app.processEvents()
    finally:
        _close_pet(pet)
        app.processEvents()


# ── A2：示例只填入、不发送 ────────────────────────────────────────────


def test_example_fills_input_without_sending_or_writing(tmp_path, monkeypatch) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        chat.open_usage_dialog()
        dialog = pet.usage_dialog
        session_id = chat.current_session_id
        assert session_id
        before_count = chat.chat_history_manager.get_session(session_id)["message_count"]
        before_transcript = chat.transcript.toPlainText()
        before_files = _snapshot(root)

        sentence = dialog.example_texts()[0]
        dialog.example_buttons[0].click()
        app.processEvents()

        assert dialog.status.text() == f"已选中：{sentence}"
        assert chat.input_box.text() == sentence
        assert chat.reply_thread is None, "示例不得自动发送"
        assert chat.transcript.toPlainText() == before_transcript
        assert chat.chat_history_manager.get_session(session_id)["message_count"] == before_count
        assert _snapshot(root) == before_files, "查看/填入示例不得写业务数据"
    finally:
        _close_pet(pet)
        app.processEvents()


# ── A3：已有草稿默认不替换；取消保留、确认替换 ───────────────────────


def test_existing_draft_is_kept_on_cancel_and_replaced_on_confirm(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        chat.open_usage_dialog()
        dialog = pet.usage_dialog
        sentence = dialog.example_texts()[0]
        session_id = chat.current_session_id
        chat.input_box.setText("原有草稿")
        before_count = chat.chat_history_manager.get_session(session_id)["message_count"]

        prompts = []

        def fake_question(*args, **_kwargs):
            prompts.append(args)
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", fake_question)
        before_files = _snapshot(root)
        dialog.example_buttons[0].click()
        app.processEvents()

        assert prompts, "已有草稿时应询问是否替换"
        _parent, title, _text, buttons, default = prompts[0]
        assert title == "替换输入草稿？"
        assert buttons == (
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        assert default == QMessageBox.StandardButton.No, "默认应为不替换"
        assert chat.input_box.text() == "原有草稿", "取消后必须保留原草稿"
        assert chat.reply_thread is None
        assert chat.chat_history_manager.get_session(session_id)["message_count"] == before_count
        assert _snapshot(root) == before_files

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        dialog.example_buttons[0].click()
        app.processEvents()
        assert chat.input_box.text() == sentence, "确认后才替换草稿"
        assert chat.reply_thread is None, "替换草稿不等于发送"
        assert chat.chat_history_manager.get_session(session_id)["message_count"] == before_count
        assert _snapshot(root) == before_files, "替换草稿不得写业务数据"
    finally:
        _close_pet(pet)
        app.processEvents()


# ── A4：历史只读；示例不能借机写入 ────────────────────────────────────


def test_history_view_is_read_only_and_example_only_fills(tmp_path, monkeypatch) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        manager = chat.chat_history_manager
        today_session = manager.ensure_daily_learning_session(BUSINESS_DATE)
        past = manager.ensure_daily_learning_session(PAST_DATE)
        manager.add_message(past["session_id"], "user", "昨天的提问", intent="chat")
        assert chat.first_use_hint.isVisible(), "全新实例首次打开聊天应看到提示"
        assert chat.switch_chat_session(past["session_id"]) is True

        assert chat._viewing_history is True
        assert "历史对话 · 只读" in chat.session_title_label.text()
        assert chat.input_box.isEnabled() is False
        assert chat.send_button.isEnabled() is False
        assert chat.return_today_button.isVisible()
        assert chat.first_use_hint.isVisible() is False, "历史只读视图不保留首次提示"

        chat.input_box.clear()
        before_files = _snapshot(root)
        before_past = manager.get_session(past["session_id"])
        before_count = before_past["message_count"]

        chat.select_usage_example("查看今天的计划。")

        assert chat._viewing_history is False, "选示例应回到今天，不得停留在历史会话"
        assert chat.input_box.text() == "查看今天的计划。"
        assert chat.input_box.isEnabled() is True
        assert chat.send_button.isEnabled() is True
        assert chat.reply_thread is None
        assert chat.first_use_hint.isVisible() is False, "本次运行不重复弹出提示"
        after_past = manager.get_session(past["session_id"])
        assert after_past["message_count"] == before_count
        assert [item["content"] for item in after_past["messages"]] == [
            item["content"] for item in before_past["messages"]
        ]
        # 回到今天时允许读取/创建今天的会话；除此之外不得改动业务文件。
        after_files = _snapshot(root)
        changed = {
            name
            for name in set(before_files) | set(after_files)
            if before_files.get(name) != after_files.get(name)
        }
        assert changed <= {str(manager.history_file.relative_to(root))}
        assert manager.get_session(today_session["session_id"]) is not None
    finally:
        _close_pet(pet)
        app.processEvents()


# ── A5：帮助可反复回看 ───────────────────────────────────────────────


def test_help_can_be_reopened_and_keeps_full_text(tmp_path, monkeypatch) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        chat.open_usage_dialog()
        first = pet.usage_dialog
        assert "内测与使用边界" in first.content.toPlainText()
        assert first.is_details_visible() is False
        first.reject()
        app.processEvents()

        before_files = _snapshot(root)
        chat.usage_action.trigger()
        app.processEvents()
        assert pet.usage_dialog is first, "回看应复用同一帮助窗口"
        assert first.isVisible()
        assert "内测与使用边界" in first.content.toPlainText()
        assert _snapshot(root) == before_files, "回看帮助不得写业务数据"
    finally:
        _close_pet(pet)
        app.processEvents()


# ── A6：不同实例互不覆盖 ─────────────────────────────────────────────


def test_two_fresh_instances_keep_separate_identity_and_data(tmp_path) -> None:
    root_a = (tmp_path / "instance-a").resolve()
    root_b = (tmp_path / "instance-b").resolve()
    runtime_a = RuntimePaths(
        SOURCE_ROOT, SOURCE_ROOT, root_a, "developer", data_source="engineering"
    )
    runtime_b = RuntimePaths(
        SOURCE_ROOT, SOURCE_ROOT, root_b, "developer", data_source="engineering"
    )
    first_a = _initialize_desktop_runtime(runtime_a)
    first_b = _initialize_desktop_runtime(runtime_b)
    assert first_a["instance_id"] != first_b["instance_id"]

    save_pet_settings(
        {"chat_history_enabled": False, "first_use_marker": "instance-a"},
        root_a / "data" / "pet_config.json",
    )
    b_config = root_b / "data" / "pet_config.json"
    assert not b_config.exists(), "A 的写入不得出现在 B 的数据根"

    second_a = _initialize_desktop_runtime(runtime_a)
    assert second_a["instance_id"] == first_a["instance_id"], "重启 A 应保持自身实例身份"
    assert not b_config.exists()
    assert json.loads(
        (root_a / "data" / "pet_config.json").read_text(encoding="utf-8")
    )["first_use_marker"] == "instance-a"


# ── A7：保存设置不丢旧配置与隐藏字段 ─────────────────────────────────


def test_settings_save_keeps_unknown_and_hidden_values(tmp_path, monkeypatch) -> None:
    app = _app()
    config = tmp_path / "pet_config.json"
    config.write_text(
        json.dumps(
            {
                "pet_asset_path": "assets/pet/default.png",
                "model_name": "deepseek-chat",
                "default_model": "deepseek-v4-flash",
                "complex_model": "deepseek-v4-pro",
                "pet_always_on_top": False,
                "future_legacy_key": {"nested": [1, 2, 3]},
                "restore_last_session": False,
                "enable_memory_candidates": True,
                "summary_message_threshold": 45,
                "summary_character_threshold": 9000,
                "proactive_check_seconds_test": 17,
                "study_reminder_seconds_test": 7,
                "sleep_seconds_test": 9,
                "test_mode": True,
                "intent_auto_execute_confidence": 0.91,
                "agent_max_steps": 2,
            }
        ),
        encoding="utf-8",
    )
    saved = []
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: notices.append(True))
    dialog = SettingsDialog(
        config_file=config,
        secret_store=SecretStore(tmp_path / "secrets"),
        usage_store=ModelUsageStore(tmp_path / "usage"),
        knowledge_dir=tmp_path / "knowledge",
    )
    try:
        dialog.settings_saved.connect(saved.append)
        save_button = _find_button(dialog, "保存")
        assert save_button is not None
        save_button.click()
        app.processEvents()
        assert len(saved) == 1
        assert notices == [True]

        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["future_legacy_key"] == {"nested": [1, 2, 3]}
        assert data["restore_last_session"] is False
        assert data["enable_memory_candidates"] is True
        assert data["summary_message_threshold"] == 45
        assert data["summary_character_threshold"] == 9000
        assert data["proactive_check_seconds_test"] == 17
        assert data["study_reminder_seconds_test"] == 7
        assert data["sleep_seconds_test"] == 9
        assert data["test_mode"] is True
        assert data["intent_auto_execute_confidence"] == 0.91
        assert data["agent_max_steps"] == 2
        assert "api_key" not in data
    finally:
        dialog.close()
        app.processEvents()


# ── A8：设置连接 / 反馈 / 清空历史的原机制 ───────────────────────────


def test_settings_keep_connection_feedback_and_clear_history_guards(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    manager = ChatHistoryManager(tmp_path / "private", enabled=True)
    session = manager.ensure_daily_learning_session(BUSINESS_DATE)
    manager.add_message(session["session_id"], "user", "保留我", intent="chat")
    history_file = manager.history_file
    memory_file = tmp_path / "private" / "memory_probe.json"
    memory_file.write_text("{}", encoding="utf-8")

    config = tmp_path / "pet_config.json"
    config.write_text(
        json.dumps(
            {
                "pet_asset_path": "assets/pet/default.png",
                "model_name": "deepseek-chat",
            }
        ),
        encoding="utf-8",
    )
    dialog = SettingsDialog(
        config_file=config,
        chat_history_manager=manager,
        secret_store=SecretStore(tmp_path / "secrets"),
        usage_store=ModelUsageStore(tmp_path / "usage"),
        knowledge_dir=tmp_path / "knowledge",
    )
    try:
        # 连接：无 Key 时只提示未配置，不创建线程、不发请求。
        connection_button = _find_button(dialog, "测试连接")
        assert connection_button is not None
        connection_button.click()
        app.processEvents()
        assert dialog.connection_status.text() == "未配置 API Key"
        assert dialog.connection_thread is None

        # 反馈：未注入导出服务时入口明确不可用，没有上传路径。
        feedback_button = _find_button(dialog, "导出内测反馈")
        assert feedback_button is not None
        assert feedback_button.isEnabled() is False

        # 清空历史：默认 No 不删除；Yes 才删除并发出信号。
        cleared = []
        dialog.history_cleared.connect(lambda: cleared.append(True))
        prompts = []

        def question_no(*args, **_kwargs):
            prompts.append(args)
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", question_no)
        before = history_file.read_bytes()
        _find_button(dialog, "清空聊天历史").click()
        app.processEvents()
        assert prompts and prompts[0][0] is dialog
        assert prompts[0][4] == QMessageBox.StandardButton.No, "默认应为不删除"
        assert history_file.read_bytes() == before
        assert cleared == []

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
        _find_button(dialog, "清空聊天历史").click()
        app.processEvents()
        assert cleared == [True]
        assert manager.sessions() == []
        assert memory_file.read_text(encoding="utf-8") == "{}", "清空聊天历史不得改动长期记忆文件"
    finally:
        dialog.close()
        app.processEvents()


# ── B1：首次提示出现；只写实例 UI 标志；关闭后帮助仍可回看 ───────────


def test_first_use_hint_appears_once_and_only_writes_instance_flag(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        config_file = root / "data" / "pet_config.json"
        config_before = json.loads(config_file.read_text(encoding="utf-8"))
        business_before = _non_chat_snapshot(root)

        chat = _open_chat(pet)

        assert chat.first_use_hint.isVisible()
        assert chat.first_use_hint.hint_text() == HINT_TEXT
        assert "安排今天" in HINT_TEXT
        assert chat.first_use_hint.isModal() is False
        assert chat.input_box.isEnabled() and chat.send_button.isEnabled()
        assert chat.usage_action in chat.interaction_menu.actions()
        assert first_use_hint_seen(config_file) is True

        config_after = json.loads(config_file.read_text(encoding="utf-8"))
        assert config_after.pop("first_use_hint_seen") is True
        assert config_after == config_before, "首次提示只允许追加实例 UI 标志"
        assert _non_chat_snapshot(root) == business_before, (
            "首次提示不得写计划／行动／复盘／记忆"
        )

        chat.first_use_hint.close_button.click()
        app.processEvents()
        assert chat.first_use_hint.isVisible() is False
        assert first_use_hint_seen(config_file) is True

        chat.usage_action.trigger()
        app.processEvents()
        assert pet.usage_dialog is not None and pet.usage_dialog.isVisible()
        assert chat.first_use_hint.isVisible() is False, "打开帮助不应重新弹出提示"
    finally:
        _close_pet(pet)
        app.processEvents()


# ── B2：同根重启不再出现（重新构造窗口与服务） ───────────────────────


def test_first_use_hint_stays_away_after_same_root_restart(
    tmp_path, monkeypatch
) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        assert chat.first_use_hint.isVisible()
        assert first_use_hint_seen(chat.settings_config_file) is True
        config_bytes = chat.settings_config_file.read_bytes()
    finally:
        _close_pet(pet)
        app.processEvents()

    # 新组合根：新窗口、新服务，从落盘配置读取，不复用旧对象的内存标志。
    restarted = _restart_pet(root)
    try:
        chat = _open_chat(restarted)
        assert chat.first_use_hint.isVisible() is False, "同实例重启不应再次提示"
        assert chat.usage_action in chat.interaction_menu.actions()
        assert chat.settings_config_file.read_bytes() == config_bytes, (
            "已看过时重启不应重写配置"
        )
    finally:
        _close_pet(restarted)
        app.processEvents()


# ── B3：不同数据根独立提示 ───────────────────────────────────────────


def test_first_use_hint_is_independent_per_data_root(tmp_path, monkeypatch) -> None:
    app = _app()
    pet_a, root_a = _fresh_pet(tmp_path, monkeypatch, "instance-a")
    try:
        chat_a = _open_chat(pet_a)
        assert chat_a.first_use_hint.isVisible()
        assert first_use_hint_seen(root_a / "data" / "pet_config.json") is True
    finally:
        _close_pet(pet_a)
        app.processEvents()

    pet_b, root_b = _fresh_pet(tmp_path, monkeypatch, "instance-b")
    try:
        b_config = root_b / "data" / "pet_config.json"
        assert first_use_hint_seen(b_config) is False, "B 不应继承 A 的已看标志"
        chat_b = _open_chat(pet_b)
        assert chat_b.first_use_hint.isVisible(), "另一数据根仍应独立提示"
        assert first_use_hint_seen(b_config) is True
    finally:
        _close_pet(pet_b)
        app.processEvents()


# ── B4：“看看示例”复用操作说明，不自动发送 ──────────────────────────


def test_first_use_hint_example_opens_help_without_sending(tmp_path, monkeypatch) -> None:
    app = _app()
    pet, root = _fresh_pet(tmp_path, monkeypatch)
    try:
        chat = _open_chat(pet)
        hint = chat.first_use_hint
        assert hint.isVisible()
        session_id = chat.current_session_id
        before_count = chat.chat_history_manager.get_session(session_id)["message_count"]
        before_transcript = chat.transcript.toPlainText()
        before_files = _snapshot(root)

        hint.example_button.click()
        app.processEvents()

        assert hint.isVisible() is False, "打开示例后提示应关闭"
        assert pet.usage_dialog is not None and pet.usage_dialog.isVisible()
        assert chat.input_box.text() == ""
        assert chat.reply_thread is None, "首次提示示例不得自动发送"
        assert chat.chat_history_manager.get_session(session_id)["message_count"] == before_count
        assert chat.transcript.toPlainText() == before_transcript
        assert _snapshot(root) == before_files, "打开示例不得写业务数据"
    finally:
        _close_pet(pet)
        app.processEvents()
