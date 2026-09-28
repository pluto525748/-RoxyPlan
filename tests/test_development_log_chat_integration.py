import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from frontend.pet_app import ChatWindow
import modules.development_log as log_module
from modules.contracts import AgentResponse
from modules.development_log import DevelopmentLog
from modules.intent_router import LLMIntentParser
from modules.response_composer import ResponseComposer
from v18_test_support import NoCallLLM, RecordingLLM, build_service


@pytest.fixture
def evidence_log(tmp_path, monkeypatch):
    logger = DevelopmentLog(tmp_path / "development", enabled=True)
    monkeypatch.setattr(log_module, "_default_log", logger)
    return logger


def events(logger):
    return sorted(
        [json.loads(line) for path in logger.directory.rglob("events*.jsonl")
         for line in path.read_text(encoding="utf-8").splitlines()],
        key=lambda item: item["sequence"],
    )


def test_repeated_phrase_gets_distinct_traces_and_saved_message_metadata(tmp_path, evidence_log):
    service, _growth, _memory, history = build_service(tmp_path / "app", NoCallLLM())
    session = history.new_session()["session_id"]
    for _ in range(2):
        service.conversation_service.handle("今日计划", session, record_history=True)
    messages = history.messages(session)
    assert len(messages) == 4
    first, second = messages[0]["metadata"], messages[2]["metadata"]
    assert first["trace_id"] != second["trace_id"]
    assert first["trace_id"] == messages[1]["metadata"]["trace_id"]
    assert second["trace_id"] == messages[3]["metadata"]["trace_id"]
    assert "request_id" not in first  # must not make real history transient
    assert all(row["session_id"] == session for row in events(evidence_log))
    assert len({row["trace_id"] for row in events(evidence_log)}) == 2


def test_worker_completion_keeps_original_trace_without_extra_model_calls(tmp_path, evidence_log):
    llm = RecordingLLM("你好，慢慢来。")
    service, _growth, _memory, history = build_service(tmp_path / "app", llm)
    session = history.new_session()["session_id"]
    trace = evidence_log.new_trace(session_id=session)
    turn = service.conversation_service.prepare(
        "你好啊", session, record_history=True,
        allow_llm_intent=False, trace_context=trace,
    )
    assert turn.trace_context == trace
    assert evidence_log.current_trace() is None
    with ThreadPoolExecutor(max_workers=1) as worker:
        response = worker.submit(service.conversation_service.complete, turn).result()
    assert response.status == "chat"
    assert len(llm.calls) == 1
    assert {row["trace_id"] for row in events(evidence_log)} == {trace.trace_id}
    assert {row["session_id"] for row in events(evidence_log)} == {session}
    assert all(item["metadata"]["trace_id"] == trace.trace_id for item in history.messages(session))
    assert "model_request_finished" in {row["event"] for row in events(evidence_log)}


def test_disabled_log_preserves_old_history_metadata_and_creates_no_files(tmp_path, monkeypatch):
    logger = DevelopmentLog(tmp_path / "disabled", enabled=False)
    monkeypatch.setattr(log_module, "_default_log", logger)
    service, _growth, _memory, history = build_service(tmp_path / "app", NoCallLLM())
    session = history.new_session()["session_id"]
    service.conversation_service.handle("今日计划", session, record_history=True)
    assert history.messages(session)[0]["metadata"] == {}
    assert history.messages(session)[1]["metadata"] == {"status": "completed"}
    assert not logger.directory.exists()


def test_auto_session_is_resolved_before_one_immutable_trace_is_allocated(tmp_path, evidence_log):
    service, _growth, _memory, history = build_service(tmp_path / "app", NoCallLLM())
    response = service.conversation_service.handle("今日计划", "", record_history=True)
    rows = events(evidence_log)
    assert len({row["trace_id"] for row in rows}) == 1
    assert {row["session_id"] for row in rows} == {response.conversation_id}
    assert len(history.messages(response.conversation_id)) == 2


def test_injected_logger_owns_tool_and_reply_events_without_configuring_global(tmp_path):
    logger = DevelopmentLog(tmp_path / "injected", enabled=True)
    service, _growth, _memory, history = build_service(tmp_path / "app", NoCallLLM())
    service.conversation_service.development_log = logger
    session = history.new_session()["session_id"]
    service.conversation_service.handle("今日计划", session)
    rows = events(logger)
    assert {"tool_started", "parameter_validation", "reply_composed"} <= {row["event"] for row in rows}
    assert len({row["trace_id"] for row in rows}) == 1


def test_minimal_semantic_log_does_not_require_detailed_diagnostics(tmp_path, evidence_log):
    service, _growth, _memory, history = build_service(tmp_path / "app", NoCallLLM())
    shared = service.conversation_service
    shared.interaction_diagnostics.enabled = False
    trace = evidence_log.new_trace(session_id=history.new_session()["session_id"])
    secret = "PRIVATE_MODEL_ARGUMENT_sk-test-do-not-leak"
    with evidence_log.bind(trace):
        shared._update_semantic_diagnostics(trace.session_id, {
            "mode": "write", "intent": "add_plan", "proposed_tool": "add_plan",
            "entities": {"title": secret}, "pipeline_outcome": "reject",
        }, None)
    row = events(evidence_log)[0]
    assert row["event"] == "semantic_decision"
    assert row["fields"]["status"] == "failed"
    assert row["fields"]["reason_code"] == "schema_rejected"
    assert row["fields"]["tool"] == "add_plan"
    assert secret not in json.dumps(events(evidence_log))


def test_invalid_tool_arguments_log_schema_failure_without_dumping_payload(tmp_path, evidence_log):
    service, growth, _memory, _history = build_service(tmp_path / "app", NoCallLLM())
    secret = "PRIVATE_ARGUMENT_BODY"
    trace = evidence_log.new_trace()
    with evidence_log.bind(trace):
        result = service.agent_core.executor.execute("add_plan", {"title": secret, "unsupported": secret})
    assert not result.success
    assert growth.tasks() == []
    validation = next(row for row in events(evidence_log) if row["event"] == "parameter_validation")
    assert validation["fields"]["reason_code"] == "schema_rejected"
    assert secret not in json.dumps(events(evidence_log))


def test_reply_guard_logs_rule_without_model_or_user_body(evidence_log):
    raw = "好，我记住了你喜欢PRIVATE_FAVORITE。"
    trace = evidence_log.new_trace()
    with evidence_log.bind(trace):
        response = ResponseComposer().compose(AgentResponse("chat", raw))
    rows = events(evidence_log)
    assert any(row["fields"].get("rule_code") == "memory_save_without_tool" for row in rows)
    composed = next(row for row in rows if row["event"] == "reply_composed")
    assert composed["fields"]["changed"] is True
    assert composed["fields"]["raw_model_response_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert "PRIVATE_FAVORITE" not in json.dumps(rows)
    assert response.message != raw


def test_same_input_with_different_model_replies_can_be_diagnosed_by_turn(tmp_path, evidence_log):
    from scripts.inspect_recent_interactions import inspect_recent_interactions

    class VaryingReplyLLM:
        def __init__(self):
            self.semantic_calls = 0
            self.reply_calls = 0

        def chat(self, messages):
            if "You classify one Chinese user message" in messages[0]["content"]:
                self.semantic_calls += 1
                return json.dumps({
                    "mode": "chat", "intent": "chat", "entities": {},
                    "proposed_tool": None, "confidence": 0.98,
                    "follow_up_target": None, "needs_confirmation": False,
                    "warnings": [], "clarification_question": None,
                    "candidate_actions": [], "subject": "self", "polarity": "positive",
                    "modality": "", "request_mode": "discuss", "explicit_command": False,
                })
            self.reply_calls += 1
            return "你好，我是你的成长伙伴。" if self.reply_calls == 1 else "好，我记住了PRIVATE_VARIATION。"

    llm = VaryingReplyLLM()
    app_root = tmp_path / "app"
    service, growth, memory, history = build_service(app_root, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    session = history.new_session()["session_id"]
    for _ in range(2):
        response = service.conversation_service.handle("说说你自己", session, record_history=True)
        assert response.status == "chat"
        assert response.tool_results == []
    assert llm.reply_calls == 2
    assert llm.semantic_calls == 2
    assert growth.tasks() == []
    assert memory.data.get("memories", []) == []
    report = inspect_recent_interactions(app_root / "data/private/chat_history.json", evidence_log.directory)
    assert len(report["chat_turns"]) == 2
    assert report["chat_turns"][0]["first_deviation"] is None
    assert report["chat_turns"][1]["first_deviation"]["reason_code"] == "memory_save_without_tool"
    assert "PRIVATE_VARIATION" not in json.dumps(report)


@pytest.fixture
def chat_window(tmp_path, evidence_log):
    from PySide6.QtWidgets import QApplication
    from modules.chat_history_manager import ChatHistoryManager
    from modules.growth_manager import GrowthManager
    from tests.isolation_support import build_isolated_memory_service
    app = QApplication.instance() or QApplication([])
    window = ChatWindow(
        growth_service=GrowthManager(tmp_path / "growth"),
        memory_service=build_isolated_memory_service(tmp_path / "memory"),
        chat_history_manager=ChatHistoryManager(tmp_path / "history"),
        development_log=evidence_log,
    )
    yield window
    window.close()
    app.processEvents()


def test_desktop_input_and_reply_share_trace(chat_window, evidence_log):
    chat_window.input_box.setText("今日计划")
    chat_window.send_message()
    rows = events(evidence_log)
    submission = next(row for row in rows if row["event"] == "chat_submitted")
    display = next(row for row in rows if row["event"] == "reply_displayed")
    assert display["trace_id"] == submission["trace_id"]
    messages = chat_window.chat_history_manager.messages(chat_window.current_session_id)
    assert messages[-2]["metadata"]["trace_id"] == submission["trace_id"]
    assert messages[-1]["metadata"]["trace_id"] == submission["trace_id"]


def test_async_reply_is_not_saved_in_a_switched_session(chat_window, evidence_log):
    old_session = chat_window.current_session_id
    trace = evidence_log.new_trace(session_id=old_session)
    chat_window._active_reply_trace = trace
    chat_window._message_sequence = chat_window._active_reply_sequence = 1
    chat_window.new_chat_session()
    new_session = chat_window.current_session_id
    before = chat_window.chat_history_manager.messages(new_session)
    chat_window.finish_ai_reply(AgentResponse("chat", "STALE_PRIVATE_RESPONSE"), 1)
    assert chat_window.chat_history_manager.messages(new_session) == before
    ignored = next(row for row in events(evidence_log) if row["event"] == "reply_not_displayed")
    assert ignored["session_id"] == old_session
    assert ignored["fields"]["reason_code"] == "session_changed"
    assert "STALE_PRIVATE_RESPONSE" not in json.dumps(events(evidence_log))


def test_local_history_command_records_visible_terminal_reply(chat_window, evidence_log):
    chat_window.input_box.setText("查看历史对话")
    chat_window.send_message()
    rows = events(evidence_log)
    trace = next(row for row in rows if row["event"] == "chat_submitted")["trace_id"]
    displayed = [row for row in rows if row["event"] == "reply_displayed"]
    assert len(displayed) == 1
    assert displayed[0]["trace_id"] == trace
    assert displayed[0]["fields"]["terminal"] is True


def test_string_reply_compatibility_uses_active_worker_trace(chat_window, evidence_log):
    trace = evidence_log.new_trace(session_id=chat_window.current_session_id)
    chat_window._active_reply_trace = trace
    chat_window._message_sequence = chat_window._active_reply_sequence = 1
    chat_window.finish_ai_reply("这是一条兼容回复。", 1)
    messages = chat_window.chat_history_manager.messages(trace.session_id)
    assert messages[-1]["metadata"]["trace_id"] == trace.trace_id
    assert next(row for row in events(evidence_log) if row["event"] == "reply_displayed")["trace_id"] == trace.trace_id
