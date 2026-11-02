"""Synthetic transcripts; real shared/provider request assembly, no HTTP."""
from datetime import datetime, timedelta
import json

from modules.chat_history_manager import ChatHistoryManager
from test_learning_context_continuity import (
    DAY, NOW, facts_from, fingerprint, learning_service,
)


def add_day(history, offset, exchanges):
    day = (NOW.date() - timedelta(days=offset)).isoformat()
    session = history.ensure_daily_learning_session(day)["session_id"]
    for user, assistant in exchanges:
        history.add_message(session, "user", user)
        history.add_message(session, "assistant", assistant)
    return session


def test_provider_receives_different_wording_continuous_questions_across_days(learning_service):
    service, transport = learning_service
    history = service.chat_history_manager
    expected = []
    for offset in (1, 2, 3):
        question = f"我还是没看明白，能拆开讲吗？第{offset}处卡住了"
        answer = f"第{offset}处先比较左右两边，再判断是否保留。"
        session = add_day(history, offset, [("请讲解Python推导式", "先判断元素是否符合条件。"),
                                                (question, answer)])
        expected.append((session, question, answer))
    transport.reply = "先接着你之前卡住的地方拆开解释，不写入计划。"
    before = fingerprint(service.project_root / "data/private")
    response = service.handle("今天继续学习，请接着前几天讲。", "dialogue-today")
    assert response.tool_results == []
    discussions = facts_from(transport.requests[-1]["messages"])["learning_discussions"]
    for session, question, answer in expected:
        fragment = next(item for item in discussions["fragments"]
                        if item["session_id"] == session and item["messages"][0]["content"] == question)
        assert [m["role"] for m in fragment["messages"]] == ["user", "assistant"]
        assert [m["content"] for m in fragment["messages"]] == [question, answer]
        assert all(m["created_at"] for m in fragment["messages"])
        assert fragment["messages"] == [
            {key: message[key] for key in ("role", "content", "created_at")}
            for message in history.messages(session) if message["content"] in (question, answer)]
        assert any(item["session_id"] == session and item["source_message_index"] == fragment["anchor_message_index"]
                   for item in discussions["fragments"])
        assert fragment["selection_reason"] == "recent_learning_continuation"
    for name, value in before.items():
        if name.endswith(("today_plan.json", "action_log.json", "growth_log.json")):
            assert (service.project_root / "data/private" / name).read_bytes() == value


def test_date_round_robin_prevents_latest_day_monopoly_and_keeps_older_match(tmp_path):
    history = ChatHistoryManager(tmp_path, now_provider=lambda: NOW)
    for offset in range(1, 8):
        add_day(history, offset, [(f"Python过滤条件第{offset}天第{i}问", f"第{offset}天解释{i}")
                                  for i in range(30 if offset == 1 else 3)])
    add_day(history, 12, [("Python过滤的早期边界", "旧边界说明")])
    add_day(history, 13, [("英语阅读需要练习", "与本轮没有词面联系的旧主题")])
    before = fingerprint(tmp_path)
    result = history.learning_fragments("Python过滤", business_date=DAY)
    days = {item["business_date"] for item in result["fragments"]}
    assert {(NOW.date() - timedelta(days=i)).isoformat() for i in range(1, 8)} <= days
    assert (NOW.date() - timedelta(days=12)).isoformat() in days
    assert (NOW.date() - timedelta(days=13)).isoformat() not in days
    assert result["included_count"] <= 24
    assert result["omitted_exchange_count"] == result["eligible_exchange_count"] - result["included_count"]
    assert result["truncated"]
    assert all(day["eligible_count"] >= day["included_count"] for day in result["daily_coverage"])
    assert fingerprint(tmp_path) == before


def test_sensitive_topic_switch_persona_future_current_and_instance_isolation(tmp_path):
    history = ChatHistoryManager(tmp_path / "one", now_provider=lambda: NOW)
    current = add_day(history, 0, [("Python当前会话不重复", "当前回答")])
    add_day(history, 1, [("请讲解Python循环", "循环逐项访问。"),
                         ("为什么？", "先读取当前项。"),
                         ("换个话题，给猫起名", "叫团团。"),
                         ("为什么？", "因为它圆。"),
                         ("Python练习和我的银行卡123", "含隐私"),
                         ("为什么？", "敏感话题的邻接也不能带出"),
                         ("讲解Python异常", "我的密码是secret，不能输出"),
                         ("然后呢？", "敏感回复之后也切断邻接")])
    add_day(history, -1, [("Python未来私密标记", "未来回答")])
    other = history.new_session("不同persona")["session_id"]
    history.add_message(other, "user", "Python其他角色标记")
    sessions = history.repository.load_sessions()
    next(s for s in sessions if s["session_id"] == other)["persona_id"] = "other"
    history.repository.save_sessions(sessions)
    legacy_persona = history.new_session("仅摘要标记角色")["session_id"]
    history.add_message(legacy_persona, "user", "Python摘要其他角色标记")
    history.save_summary(legacy_persona, "Python摘要其他角色标记", persona_id="other")
    # Legacy session spans dates; adjacent assistant on a later date is excluded.
    legacy = history.new_session("跨日旧会话")["session_id"]
    history.now_provider = lambda: datetime(2026, 10, 28, 23)
    history.add_message(legacy, "user", "Python跨日的问题")
    history.now_provider = lambda: datetime(2026, 10, 30, 1)
    history.add_message(legacy, "assistant", "Python次日未来回答标记")
    other_instance = ChatHistoryManager(tmp_path / "two", now_provider=lambda: NOW)
    add_day(other_instance, 1, [("Python另一个实例标记", "其他实例回答")])
    before = fingerprint(tmp_path)
    result = history.learning_fragments("Python", business_date=DAY, exclude_session_id=current)
    text = json.dumps(result, ensure_ascii=False)
    assert "先读取当前项" in text
    for forbidden in ("当前会话不重复", "猫", "圆", "银行卡", "secret", "敏感", "未来", "其他角色", "另一个实例"):
        assert forbidden not in text
    assert "Python跨日的问题" in text
    assert fingerprint(tmp_path) == before


def test_budget_omits_whole_exchanges_and_discloses_coverage(tmp_path):
    history = ChatHistoryManager(tmp_path, now_provider=lambda: NOW)
    add_day(history, 1, [("Python最近困难", "近" * 300), ("为什么？", "保留整对角色")])
    add_day(history, 2, [("Python过长旧问题", "旧" * 4000)])
    result = history.learning_fragments("Python", business_date=DAY, char_budget=1800)
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    assert len(text) <= 1800
    assert result["truncated"] and result["omitted_exchange_count"] > 0
    for item in result["fragments"]:
        assert item["messages"][-1]["content"] in ("近" * 300, "保留整对角色")
        assert [m["role"] for m in item["messages"]] == ["user", "assistant"]


def test_combined_context_budget_keeps_current_facts_and_recent_difficulty(learning_service):
    service, _ = learning_service
    history = service.chat_history_manager
    for offset in range(1, 8):
        add_day(history, offset, [(f"Python第{offset}天的困难{i}", "解释" * 430) for i in range(5)])
        day = (NOW.date() - timedelta(days=offset)).isoformat()
        service.growth_manager.add_task("Python旧计划", date=day, note="旧备注" * 700)
        service.growth_manager.save_today_review(day)
    today = service.growth_manager.add_task("本轮优先的真实任务", duration_minutes=20)
    # Force an actual combined overflow, rather than assuming the independent
    # projections always consume their maximum sizes on this fixture.
    service.conversation_service.instruction += "保留本轮安全要求。" * 1800
    before = fingerprint(service.project_root)
    messages = service.conversation_service.build_llm_messages("今天继续学习Python", "budget-request")
    assert sum(len(m["content"]) for m in messages) <= 48000
    facts = facts_from(messages)
    assert facts["today"]["plans"][0]["uid"] == today["uid"]
    assert "Python第1天的困难4" in json.dumps(facts, ensure_ascii=False)
    assert facts["context_budget"]["truncated"]
    assert facts["learning_discussions"]["included_count"] == len(facts["learning_discussions"]["fragments"])
    assert messages[-1] == {"role": "user", "content": "今天继续学习Python"}
    assert fingerprint(service.project_root) == before


def test_no_relevant_history_does_not_invent_learning(tmp_path):
    history = ChatHistoryManager(tmp_path, now_provider=lambda: NOW)
    add_day(history, 1, [("给猫起名字", "叫团团"), ("为什么？", "因为圆")])
    result = history.learning_fragments("继续学习", business_date=DAY)
    assert result["fragments"] == [] and result["included_count"] == 0


def test_long_latest_exchange_reserves_room_for_other_recent_days(tmp_path):
    history = ChatHistoryManager(tmp_path, now_provider=lambda: NOW)
    add_day(history, 1, [("Python最近的短困难", "短解释"), ("Python超长讲解", "长" * 15000)])
    for offset in range(2, 8):
        add_day(history, offset, [(f"Python第{offset}天困难", "说明" * 200)])
    result = history.learning_fragments("Python", business_date=DAY)
    assert {(NOW.date() - timedelta(days=i)).isoformat() for i in range(1, 8)} <= {
        item["business_date"] for item in result["fragments"]}
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 18000


def test_continuous_segments_reserve_slots_for_days_four_to_seven(tmp_path):
    history = ChatHistoryManager(tmp_path, now_provider=lambda: NOW)
    for offset in range(1, 8):
        add_day(history, offset, [("请讲解Python过滤", "逐项判断")]
                + [("为什么？", f"第{i}步解释") for i in range(6 if offset <= 3 else 0)])
    result = history.learning_fragments("Python", business_date=DAY)
    assert {(NOW.date() - timedelta(days=i)).isoformat() for i in range(1, 8)} <= {
        item["business_date"] for item in result["fragments"]}
    for item in result["fragments"]:
        assert any(anchor["session_id"] == item["session_id"] and
            anchor["source_message_index"] == item["anchor_message_index"] for anchor in result["fragments"])


def test_profile_dedup_preserves_other_verified_retrieved_memories(learning_service, monkeypatch):
    service, _ = learning_service
    blue = service.memory_manager.add_memory("写示例时喜欢用蓝色标注", category="preference")["memory"]
    goal = next(item for item in service.memory_service.read_typed_memory(query_mode="overview").data["memories"]
                if item["category"] == "goal")
    # Isolate the section adapter with two real active records. Do not change
    # MemoryService validity/conflict policy or the provider/write pipeline.
    monkeypatch.setattr(service.memory_retriever, "retrieve", lambda *args, **kwargs: [
        {"memory": blue}, {"memory": goal}])
    supplied = []
    service.conversation_service.memory_context_provider = lambda records: supplied.append(records) or json.dumps(records, ensure_ascii=False)
    before = fingerprint(service.project_root)
    messages = service.conversation_service.build_llm_messages("请继续学习Python", "memory-dedup")
    assert [item["id"] for item in supplied[-1]] == [blue["id"]]
    assert any(goal["id"] == item["id"] for item in facts_from(messages)["learning_profile"]["records"])
    service.conversation_service.build_llm_messages("给白猫起个名字", "ordinary-memory")
    assert {item["id"] for item in supplied[-1]} == {blue["id"], goal["id"]}
    assert fingerprint(service.project_root) == before


def test_hard_required_overflow_is_visible_without_partial_learning_json():
    from modules.context_builder import ContextBuilder
    builder = ContextBuilder()
    facts = {"business_date": DAY, "user_request": "继续学习", "learning_history": {}}
    instruction = "必须保留的安全要求。" * 6000
    messages = builder.build(personality_context="人格", memory_context="", session_summary="",
        recent_messages=[], knowledge_context="", current_user_input="继续学习",
        instruction=instruction, learning_context=facts)
    assert any(item["content"] == instruction for item in messages)
    assert messages[-1] == {"role": "user", "content": "继续学习"}
    assert facts["context_budget"]["reference_omitted"] is True
    assert any(item.name == "hard_context_overflow" for item in builder.last_diagnostics)
    assert not any(item["content"].startswith("以下是本次请求绑定业务日期") for item in messages)
