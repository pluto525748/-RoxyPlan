from datetime import datetime
from pathlib import Path

from modules.chat_history_manager import ChatHistoryManager
from tests.v18_test_support import RecordingLLM, build_service


def test_short_session_finalize_records_episodic_metadata_and_filters_transients(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    history.new_session(session_id="old")
    history.add_message("old", "user", "我们决定先完善人格包，之后继续讨论蒸馏流程。")
    history.add_message("old", "user", "这个话题下次继续。")
    history.add_message(
        "old",
        "user",
        "确认 ID confirmation_abc123，刚才那个选第二个。",
        metadata={"confirmation_id": "confirmation_abc123"},
    )

    assert history.finalize_session("old", persona_id="roxy") is True
    record = history.summary_data["summaries"]["old"]

    assert record["persona_id"] == "roxy"
    assert record["source_message_count"] == 3
    assert record["time_range"]["start"]
    assert "人格包" in record["summary"]
    assert "下次" in record["summary"]
    assert "confirmation_abc123" not in record["summary"]
    assert "刚才那个" not in record["summary"]


def test_relevant_summaries_exclude_current_session_and_persona(tmp_path):
    history = ChatHistoryManager(tmp_path / "private")
    for session_id, text, persona_id in (
        ("roxy-old", "我们讨论过人格包蒸馏的下一步。", "roxy"),
        ("other-persona", "我们讨论过人格包蒸馏的下一步。", "other"),
        ("unrelated", "今天的跑步计划已经完成。", "roxy"),
    ):
        history.new_session(session_id=session_id)
        history.add_message(session_id, "user", text)
        assert history.finalize_session(session_id, persona_id=persona_id)

    found = history.relevant_summaries(
        "我们之前说的人格包接下来怎么办？",
        exclude_session_id="roxy-old",
        persona_id="roxy",
        limit=3,
        char_budget=300,
    )
    unrelated = history.relevant_summaries(
        "明天早餐吃什么？", exclude_session_id="current", persona_id="roxy"
    )

    assert found == []
    # Current session exclusion is exact; the old Roxy summary can be found when
    # it is not the active session, while a different persona never leaks in.
    found = history.relevant_summaries(
        "我们之前说的人格包接下来怎么办？",
        exclude_session_id="current",
        persona_id="roxy",
        limit=3,
        char_budget=300,
    )
    assert [item["session_id"] for item in found] == ["roxy-old"]
    assert found[0]["time_range"]["start"]
    assert found[0]["time_range"]["end"]
    assert unrelated == []


def test_new_window_gets_related_summary_but_not_old_messages_or_working_state(tmp_path):
    service, _growth, memory, history = build_service(tmp_path, RecordingLLM())
    history.new_session(session_id="old")
    history.add_message("old", "user", "我们决定让人格模块以后支持蒸馏角色包。")
    history.add_message("old", "assistant", "好的，下一步可以先定义可导入的人格包格式。")
    assert service.conversation_service.finalize_conversation("old")
    history.new_session(session_id="new")

    before_memories = [item["id"] for item in memory.memories()]
    messages = service.conversation_service.build_llm_messages(
        "我们之前说的人格包接下来怎么办？", "new"
    )
    combined = "\n".join(item["content"] for item in messages)

    assert "相关旧会话摘要" in combined
    assert "蒸馏角色包" in combined
    assert "下一步可以先定义可导入的人格包格式" not in combined
    assert service.interaction_coordinator.current("new").state == "idle"
    assert [item["id"] for item in memory.memories()] == before_memories


def test_summary_persists_after_manager_restart_and_respects_budget(tmp_path):
    private_dir = tmp_path / "private"
    first = ChatHistoryManager(private_dir)
    first.new_session(session_id="persistent")
    first.add_message("persistent", "user", "讨论人格包的上下文预算和裁剪规则。")
    assert first.finalize_session("persistent")

    restarted = ChatHistoryManager(private_dir)
    result = restarted.relevant_summaries(
        "人格包上下文预算", exclude_session_id="new", char_budget=12, limit=1
    )

    assert len(result) == 1
    assert result[0]["session_id"] == "persistent"
    assert result[0]["trimmed"] is True
    assert len(result[0]["summary"]) <= 12


def test_old_summary_uses_source_time_in_prompt_and_refresh_time_only_in_diagnostics(
    tmp_path,
    capsys,
):
    llm = RecordingLLM("早上好，我们可以接着之前的话题聊。")
    service, _growth, _memory, history = build_service(tmp_path, llm)
    service.conversation_service.now_provider = lambda: datetime(2026, 9, 3, 9, 0, 0)
    classify = service.conversation_service._summary_temporal_relation
    current_date = datetime(2026, 9, 3).date()

    assert classify("", "", current_date=current_date) == "unknown"
    assert classify(
        "2026-09-01T10:00:00",
        "2026-09-02T10:00:00",
        current_date=current_date,
    ) == "multi_day"
    assert classify(
        "2026-09-02T10:00:00",
        "2026-09-02T11:00:00",
        current_date=current_date,
    ) == "yesterday"
    assert classify(
        "2026-09-03T10:00:00",
        "2026-09-03T11:00:00",
        current_date=current_date,
    ) == "today"
    assert classify(
        "2026-09-04T10:00:00",
        "2026-09-04T11:00:00",
        current_date=current_date,
    ) == "future"

    history.now_provider = lambda: datetime(2026, 8, 20, 10, 0, 0)
    history.new_session(session_id="old-agent-topic")
    history.add_message(
        "old-agent-topic",
        "user",
        "你好啊，今天继续完成 agent 自然语言处理。",
    )
    history.now_provider = lambda: datetime(2026, 9, 2, 11, 46, 28)
    assert history.finalize_session("old-agent-topic")

    response = service.handle("agent 自然语言处理和普通程序有什么区别？", "new-greeting")
    combined = "\n".join(item["content"] for item in llm.calls[-1])
    record = service.diagnostic_snapshot()[-1]
    provenance = record["relevant_summary_provenance"][0]

    assert response.status == "chat"
    assert "source_message_time_start=2026-08-20T10:00:00" in combined
    assert "relation_to_current_date=earlier" in combined
    assert "只有 relation_to_current_date 明确为 today 或 yesterday" in combined
    assert "2026-09-02T11:46:28" not in combined
    assert provenance == {
        "session_id": "old-agent-topic",
        "source_message_time_start": "2026-08-20T10:00:00",
        "source_message_time_end": "2026-08-20T10:00:00",
        "summary_refreshed_at": "2026-09-02T11:46:28",
        "relation_to_current_date": "earlier",
        "source_message_count": 1,
        "trimmed": False,
    }
    assert "agent 自然语言处理" not in str(provenance)
    assert "[Context] relevant conversation summaries included:" in capsys.readouterr().out
