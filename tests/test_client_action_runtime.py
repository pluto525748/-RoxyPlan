import json
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from frontend.desktop_pet import DesktopPet
from frontend.pet_app import ChatWindow
from frontend.settings_dialog import DEFAULT_SETTINGS
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryService
from modules.intent_router import LLMIntentParser
from modules.llm.routed_client import RoutedLLMClient
from tests.semantic_contract_fixtures import build_current_semantic_payload


def build_real_runtime(root: Path, *, force_routed_client: bool = False):
    app = QApplication.instance() or QApplication([])
    private = root / "data" / "private"
    growth = GrowthManager(private)
    memory = MemoryManager(
        root / "memory.json",
        backup_dir=private / "backups",
        conflict_file=private / "memory_conflicts.json",
        audit_file=private / "memory_audit.json",
    )
    history = ChatHistoryManager(private)
    with patch("frontend.desktop_pet.GrowthManager", return_value=growth), patch(
        "frontend.desktop_pet.ChatHistoryManager", return_value=history
    ):
        pet = DesktopPet()
    llm_config = (
        {
            "provider": "deepseek",
            "base_url": "https://example.invalid",
            "model": "test-semantic-model",
        }
        if force_routed_client
        else None
    )
    if force_routed_client:
        class IsolatedRoutedLLMClient(RoutedLLMClient):
            def __init__(self, _project_root, **kwargs):
                super().__init__(root, **kwargs)

        routed_client_patch = patch(
            "frontend.pet_app.RoutedLLMClient",
            IsolatedRoutedLLMClient,
        )
    else:
        routed_client_patch = nullcontext()
    llm_config_patch = (
        patch("frontend.pet_app.load_llm_config", return_value=llm_config)
        if llm_config is not None
        else nullcontext()
    )
    isolated_pet_settings = dict(DEFAULT_SETTINGS)
    isolated_pet_settings["interaction_diagnostics_enabled"] = True
    with llm_config_patch, routed_client_patch, patch(
        "frontend.pet_app.load_pet_settings",
        return_value=isolated_pet_settings,
    ):
        window = ChatWindow(
            pet_controller=pet,
            growth_service=growth,
            memory_manager=memory,
            memory_service=MemoryService(memory),
            chat_history_manager=history,
        )
    return app, pet, window


def run_desktop_conversation_turn(window: ChatWindow, text: str):
    """Run the same UI-thread preparation and worker completion synchronously."""
    turn = window.conversation_service.prepare(
        text,
        window.current_session_id,
        record_history=False,
        allow_llm_intent=False,
    )
    response = window.conversation_service.complete(turn)
    window._present_agent_response(
        response,
        intent=str(turn.intent_result.get("intent", "")) or None,
    )
    return response


def test_proactive_reminder_waits_for_pending_chat_interaction(tmp_path):
    _app, pet, window = build_real_runtime(tmp_path)
    pet.chat_window = window
    calls = []
    pet.proactive_manager.check = lambda **kwargs: calls.append(kwargs) or None
    window.interaction_coordinator.awaiting_choice(
        window.current_session_id,
        "advice_or_action_choice",
        domain="plan",
        known_fields={"title": "整理演讲引用"},
    )

    pet._check_proactive_reminder()

    assert calls == []

    window.interaction_coordinator.cancel(window.current_session_id)
    pet._check_proactive_reminder()

    assert len(calls) == 1
    window.close()
    pet.close()


def test_proactive_reminder_waits_for_confirmation_manager_only(tmp_path):
    _app, pet, window = build_real_runtime(tmp_path)
    pet.chat_window = window
    calls = []
    pet.proactive_manager.check = lambda **kwargs: calls.append(kwargs) or None
    window.confirmation_manager.create(
        "delete_chat_session",
        {"session_id": "session-local-management"},
        "删除本地聊天会话",
        scope=window.current_session_id,
    )

    pet._check_proactive_reminder()

    assert calls == []
    window.confirmation_manager.cancel(scope=window.current_session_id)
    pet._check_proactive_reminder()

    assert len(calls) == 1
    window.close()
    pet.close()


class _DesktopSemanticLLM:
    """A DeepSeek-shaped double for the production desktop composition root."""

    def __init__(
        self,
        decisions,
        *,
        reply="这是正常聊天回复。",
        memory_extractions=None,
    ):
        self.decisions = {str(key): dict(value) for key, value in decisions.items()}
        self.reply = str(reply)
        self.memory_extractions = {
            str(key): str(value)
            for key, value in dict(memory_extractions or {}).items()
        }
        self.semantic_messages = []
        self.reply_messages = []
        self.memory_extraction_messages = []

    def chat(self, messages, **_kwargs):
        system_text = str(messages[0].get("content", "")) if messages else ""
        if "You classify one Chinese user message" in system_text:
            text = system_text.rsplit("\nUser message: ", 1)[-1]
            self.semantic_messages.append(text)
            return json.dumps(self.decisions[text], ensure_ascii=False)
        if "助手回复只是引用来源，不等于最终记忆正文" in system_text:
            payload = json.loads(str(messages[-1].get("content", "{}")))
            source = str(payload.get("assistant_source", ""))
            self.memory_extraction_messages.append(messages)
            content = self.memory_extractions.get(source, "")
            return json.dumps(
                {
                    "status": "extracted" if content else "no_stable_content",
                    "content": content,
                },
                ensure_ascii=False,
            )
        self.reply_messages.append(messages)
        return self.reply


def _decision(
    mode,
    intent,
    entities=None,
    *,
    subject,
    polarity,
    modality,
    request_mode,
    explicit_command,
):
    return build_current_semantic_payload({
        "mode": mode,
        "intent": intent,
        "entities": dict(entities or {}),
        "proposed_tool": None,
        "confidence": 0.97,
        "follow_up_target": None,
        "needs_confirmation": False,
        "warnings": [],
        "clarification_question": None,
        "candidate_actions": [],
        "subject": subject,
        "polarity": polarity,
        "modality": modality,
        "request_mode": request_mode,
        "explicit_command": explicit_command,
    }, case_id=f"desktop:{intent}")


def test_real_desktop_dance_dispatch_and_repeat():
    with tempfile.TemporaryDirectory() as temp:
        app, pet, window = build_real_runtime(Path(temp))
        assert len(pet._load_dance_frames()) >= 2
        assert window.pet_settings["unified_client_action_dispatcher_enabled"] is True
        assert window.pet_settings["legacy_direct_pet_action_enabled"] is False

        first = window.conversation_service.handle(
            "跳舞", window.current_session_id, record_history=False
        )
        first_results = window._present_agent_response(first, intent="dance")
        assert len(first.client_actions) == 1
        assert len(first_results) == 1
        assert first_results[0].reason_code == "running"
        assert first_results[0].accepted is True
        assert pet.action_manager.current_state == "dancing"
        assert pet.dance_timer.isActive()

        QTimer.singleShot(4000, app.quit)
        app.exec()
        assert pet.action_manager.current_state == "idle"

        second = window.conversation_service.handle(
            "跳舞", window.current_session_id, record_history=False
        )
        second_results = window._present_agent_response(second, intent="dance")
        assert second.client_actions[0].action_id != first.client_actions[0].action_id
        assert second_results[0].accepted is True
        assert pet.action_manager.current_state == "dancing"
        pet.action_manager.cancel_current_action()
        assert pet.action_manager.current_state == "idle"

        window.close()
        pet.close()


def test_real_desktop_memory_query_with_dance_text_is_not_action_dispatch():
    with tempfile.TemporaryDirectory() as temp:
        _app, pet, window = build_real_runtime(Path(temp))
        saved = window.memory_service.save_formal_memory(
            "我喜欢看你跳舞",
            category="preference",
        )
        assert saved.success
        visible_messages = []
        window.add_message = lambda sender, message, **_kwargs: visible_messages.append(
            (sender, message)
        )

        response = window.conversation_service.handle(
            "你知道我什么",
            window.current_session_id,
            record_history=False,
        )
        action_results = window._present_agent_response(
            response,
            intent="show_memory",
        )

        assert action_results == []
        assert response.client_actions == []
        assert visible_messages[-1][0] == "Roxy"
        assert "你喜欢看我跳舞" in visible_messages[-1][1]
        assert "没有成功派发舞蹈动作" not in visible_messages[-1][1]

        window.close()
        pet.close()


def test_production_desktop_root_queries_formal_memories_once_without_actions():
    with tempfile.TemporaryDirectory() as temp:
        _app, pet, window = build_real_runtime(
            Path(temp),
            force_routed_client=True,
        )
        assert isinstance(window.llm_client, RoutedLLMClient)
        assert window.conversation_service.semantic_decision_compatibility_enabled is False

        for content, category in (
            ("我正在学习机器学习", "learning"),
            ("我的肠胃比较敏感", "health"),
            ("我喜欢看你跳舞", "preference"),
        ):
            result = window.memory_service.save_formal_memory(
                content,
                category=category,
            )
            assert result.success

        semantic_calls = []

        class ChatOnlySemanticParser:
            def parse(self, text, context):
                semantic_calls.append((text, context))
                return {
                    "intent": "chat",
                    "confidence": 0.9,
                    "entities": {},
                    "needs_confirmation": False,
                    "source": "llm",
                    "warnings": [],
                    "clarification_question": None,
                    "candidate_actions": [],
                    "mode": "chat",
                    "proposed_tool": None,
                    "follow_up_target": None,
                }

        window.intent_router.configure_llm(ChatOnlySemanticParser(), True)
        list_calls = []
        original_list = window.memory_service.read_typed_memory

        def counted_list_memories(*args, **kwargs):
            list_calls.append((args, kwargs))
            return original_list(*args, **kwargs)

        window.memory_service.read_typed_memory = counted_list_memories
        presented = []
        original_present = window._present_agent_response

        def capture_response(response, **kwargs):
            presented.append(response)
            return original_present(response, **kwargs)

        window._present_agent_response = capture_response
        visible_messages = []
        window.add_message = lambda sender, message, **_kwargs: visible_messages.append(
            (sender, message)
        )

        response = run_desktop_conversation_turn(window, "你知道我什么")

        assert semantic_calls == []
        assert len(list_calls) == 1
        assert len(presented) == 1
        assert presented[0] is response
        assert response.status != "chat"
        assert [result.tool for result in response.tool_results] == ["list_memories"]
        assert response.client_actions == []
        assert "机器学习" in response.message
        assert "肠胃" in response.message
        assert "你喜欢看我跳舞" in response.message
        assert visible_messages[-1][0] == "Roxy"

        window.close()
        pet.close()


def test_production_desktop_root_memory_query_follow_up_and_new_conversation():
    with tempfile.TemporaryDirectory() as temp:
        _app, pet, window = build_real_runtime(
            Path(temp),
            force_routed_client=True,
        )
        saved = window.memory_service.save_formal_memory(
            "我更适合早上学习",
            category="learning",
        )
        assert saved.success

        list_calls = []
        original_list = window.memory_service.read_typed_memory

        def counted_list_memories(*args, **kwargs):
            list_calls.append((args, kwargs))
            return original_list(*args, **kwargs)

        window.memory_service.read_typed_memory = counted_list_memories
        presented = []
        original_present = window._present_agent_response

        def capture_response(response, **kwargs):
            presented.append(response)
            return original_present(response, **kwargs)

        window._present_agent_response = capture_response
        window.add_message = lambda *_args, **_kwargs: None

        run_desktop_conversation_turn(window, "你记得我什么")
        run_desktop_conversation_turn(
            window,
            "我问你你知道我什么，就是想让你表达你对我的了解"
        )
        new_session = window.chat_history_manager.new_session()
        window.current_session_id = str(new_session["session_id"])
        run_desktop_conversation_turn(window, "说说你对我的了解")

        assert len(list_calls) == 3
        assert len(presented) == 3
        for response in presented:
            assert response.status != "chat"
            assert [result.tool for result in response.tool_results] == [
                "list_memories"
            ]
            assert response.client_actions == []
            assert "早晨学习" in response.message

        window.close()
        pet.close()


def test_production_desktop_root_has_one_semantic_pipeline_and_no_legacy_fallbacks():
    with tempfile.TemporaryDirectory() as temp:
        _app, pet, window = build_real_runtime(Path(temp), force_routed_client=True)
        assert window.model_action_adapter is None
        assert window.conversation_service.model_action_adapter is None

        prior_statement = "我正在做一个 AI 陪伴计划桌宠"
        memory_reference = "把刚才说的加入长期记忆"
        decisions = {
            "我怕我没有钱": _decision(
                "chat", "chat", subject="self", polarity="", modality="",
                request_mode="discuss", explicit_command=False,
            ),
            "今天还有什么计划": _decision(
                "read", "show_plan", subject="self", polarity="positive",
                modality="question", request_mode="query", explicit_command=False,
            ),
            prior_statement: _decision(
                "chat", "chat", subject="self", polarity="positive", modality="",
                request_mode="discuss", explicit_command=False,
            ),
            memory_reference: _decision(
                "write",
                "add_memory_request",
                {"content": "previous_user_message"},
                subject="self",
                polarity="positive",
                modality="commitment",
                request_mode="execute",
                explicit_command=True,
            ),
            "我喜欢看你跳舞": _decision(
                "chat", "chat", subject="self", polarity="positive",
                modality="desire", request_mode="discuss", explicit_command=False,
            ),
        }
        llm = _DesktopSemanticLLM(decisions)
        window.llm_client.chat = llm.chat
        window.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
        window.add_message = lambda *_args, **_kwargs: None
        window.growth_service.add_task("今晚学习机器学习")

        original_list = window.memory_service.read_typed_memory
        list_calls = []

        def counted_list(*args, **kwargs):
            list_calls.append((args, kwargs))
            return original_list(*args, **kwargs)

        window.memory_service.read_typed_memory = counted_list
        responses = {}
        for text in (
            "我怕我没有钱",
            "今天还有什么计划",
            prior_statement,
            memory_reference,
            "你知道我什么",
            "我喜欢看你跳舞",
        ):
            responses[text] = run_desktop_conversation_turn(window, text)
            record = window.conversation_service.diagnostic_snapshot()[-1]
            assert record["pipeline"].count("SemanticDecision") == 1
            assert record["pipeline"][-1] == "FinalResponse"
            assert "FinalResponse" not in record["pipeline"][:-1]

        assert responses["我怕我没有钱"].tool_results == []
        assert [item.tool for item in responses["今天还有什么计划"].tool_results] == [
            "show_plan"
        ]
        assert responses[prior_statement].tool_results == []
        assert [item.tool for item in responses[memory_reference].tool_results] == [
            "save_formal_memory"
        ]
        assert [item.tool for item in responses["你知道我什么"].tool_results] == [
            "list_memories"
        ]
        assert responses["我喜欢看你跳舞"].tool_results == []
        assert all(not response.client_actions for response in responses.values())
        assert len(list_calls) == 1
        assert llm.semantic_messages == [
            "我怕我没有钱",
            prior_statement,
            memory_reference,
            "我喜欢看你跳舞",
        ]

        dance = run_desktop_conversation_turn(window, "跳舞")
        dance_results = window._present_agent_response(dance, intent="dance")
        assert [item.tool for item in dance.tool_results] == ["play_dance"]
        assert len(dance.client_actions) == 1
        assert len(dance_results) == 1
        assert llm.semantic_messages[-1] == "我喜欢看你跳舞"
        pet.action_manager.cancel_current_action()

        assert window.handle_natural_intent("今天还有什么计划") is False
        assert window.handle_dance_command("我喜欢看你跳舞") is False
        assert window.handle_plan_command("添加计划：不应直写") is False
        assert window.growth_service.tasks()[0]["title"] == "今晚学习机器学习"

        window.close()
        pet.close()


def test_production_desktop_root_keeps_current_plan_clarification_in_scope():
    with tempfile.TemporaryDirectory() as temp:
        _app, pet, window = build_real_runtime(Path(temp), force_routed_client=True)
        window.add_message = lambda *_args, **_kwargs: None
        window.interaction_coordinator.awaiting_clarification(
            window.current_session_id,
            "missing_slots",
            domain="plan",
            known_fields={"title": "学习机器学习", "duration_minutes": 60},
            missing_fields=["time_slot"],
            immutable_arguments={
                "tool_name": "add_plan",
                "title": "学习机器学习",
                "duration_minutes": 60,
            },
            safe_summary="请补充计划时间。",
        )

        response = run_desktop_conversation_turn(window, "晚上8点")

        assert [item.tool for item in response.tool_results] == ["add_plan"]
        task = window.growth_service.tasks()[0]
        assert task["title"] == "学习机器学习"
        assert task["time_slot"] == "晚上"
        assert window.interaction_coordinator.current(
            window.current_session_id
        ).state == "completed"

        window.close()
        pet.close()


@pytest.mark.parametrize(
    "command",
    (
        "把你刚才的回复加入长期记忆",
        "记住你刚才说的",
        "把上一条回答保存下来",
    ),
)
def test_semantic_contract_uses_previous_assistant_message_marker(command):
    captured = []

    def decide(messages):
        captured.append(str(messages[0]["content"]))
        return json.dumps(
            _decision(
                "write",
                "add_memory_request",
                {"content": "previous_assistant_message"},
                subject="self",
                polarity="positive",
                modality="commitment",
                request_mode="execute",
                explicit_command=True,
            ),
            ensure_ascii=False,
        )

    parsed = LLMIntentParser(decide).parse(
        command,
        {
            "conversation_id": "same-window",
            "last_assistant_message": "请先完成一个很小的步骤。",
        },
    )

    assert parsed["intent"] == "add_memory_request"
    assert parsed["entities"]["content"] == "previous_assistant_message"
    assert "previous_assistant_message" in captured[0]


def test_production_desktop_saves_previous_assistant_reply_and_refreshes_memory_ui(
    tmp_path,
):
    _app, pet, window = build_real_runtime(tmp_path, force_routed_client=True)
    memory_content = (
        "学习时先看一小节课程，再整理三条概念笔记，"
        "练习一道配套题目。"
    )
    assistant_reply = (
        f"可以试试这个学习方式：{memory_content}\n\n"
        "如果适合你，也可以明确说“记住：……”来要求长期保存。"
    )
    command = "把你刚才的回复加入长期记忆"
    llm = _DesktopSemanticLLM(
        {
            "给我一句行动建议": _decision(
                "chat", "chat", subject="self", polarity="positive",
                modality="question", request_mode="discuss", explicit_command=False,
            ),
            command: _decision(
                "write",
                "add_memory_request",
                {"content": "previous_assistant_message"},
                subject="self",
                polarity="positive",
                modality="commitment",
                request_mode="execute",
                explicit_command=True,
            ),
        },
        reply=assistant_reply,
        memory_extractions={assistant_reply: memory_content},
    )
    window.llm_client.chat = llm.chat
    window.intent_router.configure_llm(LLMIntentParser(llm.chat), True)

    save_calls = []
    original_save = window.memory_service.save_formal_memory

    def counted_save(content, **kwargs):
        save_calls.append((content, dict(kwargs)))
        return original_save(content, **kwargs)

    window.memory_service.save_formal_memory = counted_save
    first = run_desktop_conversation_turn(window, "请给我一句行动建议")
    assert first.message == assistant_reply
    assert save_calls == []
    assert window.memory_service.list_memories().data["memories"] == []

    window.open_memory_dialog()
    assert window.memory_dialog.memory_table.rowCount() == 0
    saved = run_desktop_conversation_turn(window, command)

    assert len(save_calls) == 1
    assert save_calls[0][0] == memory_content
    assert [item.tool for item in saved.tool_results] == ["save_formal_memory"]
    result = saved.tool_results[0]
    assert result.success is True
    operation = result.data["memory_operation"]
    assert operation["memory_id"] is not None
    assert operation["data"]["final_content"] == memory_content
    assert window.memory_dialog.memory_table.rowCount() == 1
    assert window.memory_dialog.memory_table.item(0, 2).text() == memory_content
    listed = window.memory_service.list_memories().data["memories"]
    assert [item["content"] for item in listed] == [memory_content]
    assert len(llm.memory_extraction_messages) == 1

    diagnostic = next(
        item
        for item in window.conversation_service.diagnostic_snapshot()
        if item["request_id"] == saved.request_id
    )
    assert diagnostic["pipeline"] == [
        "SemanticDecision",
        "Validation",
        "ReferenceResolution",
        "ToolExecution:save_formal_memory",
        "ToolResult:save_formal_memory:success",
        "MemoryAudit:create",
        "FinalResponse",
    ]
    print(f"REFERENCE_SAVE_REQUEST_ID={saved.request_id}")

    new_session = window.chat_history_manager.new_session()
    window.current_session_id = str(new_session["session_id"])
    unavailable = run_desktop_conversation_turn(window, command)
    assert unavailable.status == "clarification"
    assert unavailable.tool_results == []
    assert len(save_calls) == 1

    window.memory_dialog.close()
    window.close()
    pet.close()


def test_assistant_memory_extraction_never_falls_back_to_raw_or_invented_content(
    tmp_path,
):
    _app, pet, window = build_real_runtime(tmp_path, force_routed_client=True)
    source = "如果你愿意，我可以之后再帮你整理。"
    invented = "用户每天早上六点学习。"
    command = "把你刚才的回复加入长期记忆"
    llm = _DesktopSemanticLLM(
        {
            command: _decision(
                "write",
                "add_memory_request",
                {"content": "previous_assistant_message"},
                subject="self",
                polarity="positive",
                modality="commitment",
                request_mode="execute",
                explicit_command=True,
            ),
        },
        memory_extractions={source: invented},
    )
    window.llm_client.chat = llm.chat
    window.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    window.chat_history_manager.add_message(
        window.current_session_id,
        "assistant",
        source,
        intent="chat",
    )
    window.conversation_service.state_manager.observe_assistant(
        window.current_session_id,
        source,
    )

    response = run_desktop_conversation_turn(window, command)

    assert response.status == "clarification"
    assert response.tool_results == []
    assert window.memory_service.list_memories().data["memories"] == []
    assert len(llm.memory_extraction_messages) == 1

    window.close()
    pet.close()


def test_tools_zero_cannot_promise_formal_memory_save(tmp_path):
    _app, pet, window = build_real_runtime(tmp_path, force_routed_client=True)
    command = "把上一条回答保存下来"
    llm = _DesktopSemanticLLM(
        {
            command: _decision(
                "chat", "chat", subject="self", polarity="", modality="",
                request_mode="discuss", explicit_command=False,
            )
        },
        reply="好，我会把上一条回答保存到长期记忆里。",
    )
    window.llm_client.chat = llm.chat
    window.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    window.add_message = lambda *_args, **_kwargs: None

    response = run_desktop_conversation_turn(window, command)

    assert response.tool_results == []
    # Without any ToolResult, the CHAT guard blocks false execution claims.
    # The precise wording is structural, not per-sentence.
    assert "保存" not in response.message
    assert window.memory_service.list_memories().data["memories"] == []

    window.close()
    pet.close()
