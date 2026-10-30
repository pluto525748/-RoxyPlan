"""Synthetic offline learning loop: inspect real requests, not invented outcomes."""
from datetime import datetime
import json

import pytest

from modules.growth_manager import GrowthManager
from modules.learning_loop import LearningLoopService
from modules.learning_context import is_learning_context
from test_beta_online_failure_replay import _build_service, _provider_body


DAY = "2026-10-29"
NOW = datetime(2026, 10, 29, 10)


def fingerprint(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}


def facts_from(messages):
    block = next(message["content"] for message in messages
                 if message["content"].startswith("以下是本次请求绑定业务日期"))
    return json.loads(block[block.index("\n{") + 1:])


class Transport:
    def __init__(self):
        self.requests = []
        self.reply = "根据昨天的过滤条件练习，今天继续核对边界：\n1. 练习Python过滤条件，15分钟\n2. 整理异常示例，10分钟"

    def __call__(self, method, url, headers, payload, timeout):
        self.requests.append(payload)
        if "You classify one Chinese user message" in payload["messages"][0]["content"]:
            decision = {"mode": "chat", "intent": "chat", "entities": {}, "confidence": 0.99,
                "proposed_tool": None, "needs_confirmation": False, "warnings": [],
                "clarification_question": None, "candidate_actions": [], "follow_up_target": None,
                "subject": "", "polarity": "", "modality": "", "request_mode": "advice", "explicit_command": False}
            return _provider_body({"content": json.dumps(decision)})
        return _provider_body({"content": self.reply})


@pytest.fixture
def learning_service(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    transport = Transport()
    service = _build_service(tmp_path, transport)
    service.growth_manager.now_provider = lambda: NOW
    service.conversation_service.business_now_provider = lambda: NOW
    growth = service.growth_manager
    growth.add_task("Python过滤条件仍不理解", date="2026-10-26", duration_minutes=25, note="空列表与边界值混淆")
    growth.add_record("练过Python过滤", date="2026-10-26", duration_minutes=12)
    growth.save_today_review("2026-10-26")
    growth.add_task("早期Python循环", date="2026-09-01")
    growth.save_today_review("2026-09-01")
    service.memory_manager.add_memory("长期目标是通过Python面试", category="goal")
    service.memory_manager.add_memory("我的长期目标是成为AI应用工程师", category="goal", allow_similar=True)
    service.memory_manager.add_memory("偏好先自己试一遍，再看例子", category="preference")
    service.memory_manager.add_memory("伴侣的私人关系信息", category="relationship")
    session = service.chat_history_manager.ensure_daily_learning_session("2026-10-28")["session_id"]
    service.chat_history_manager.add_message(session, "user", "Python过滤条件昨天还没懂，空列表为什么返回这个值？")
    service.chat_history_manager.add_message(session, "assistant", "过滤条件先判断，再将符合条件的元素放入结果。")
    yield service, transport
    (tmp_path / "learning-context-request-evidence.json").write_text(json.dumps(
        {"requests": transport.requests, "plans": growth.tasks(DAY),
         "diagnostics": service.conversation_service.diagnostic_snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_generic_planning_request_receives_specific_history_and_profile(learning_service):
    service, transport = learning_service
    response = service.handle("请给我两个今天的学习计划候选，先不要加入。", "learning-day")
    assert response.tool_results == []
    facts = facts_from(transport.requests[-1]["messages"])
    assert facts["business_date"] == DAY
    text = json.dumps(facts, ensure_ascii=False)
    for expected in ("空列表与边界值混淆", "2026-09-01", "早期Python循环", "通过Python面试", "成为AI应用工程师", "先自己试一遍", "过滤条件昨天还没懂"):
        assert expected in text
    assert "伴侣的私人" not in text
    assert service.growth_manager.tasks(DAY) == []
    assert transport.requests[-1]["messages"][-1]["role"] == "user"


def test_followup_uses_history_but_topic_change_does_not(learning_service):
    service, transport = learning_service
    service.handle("给两个今天Python学习候选，先不加入计划。", "learning-day")
    transport.reply = "按你现在20分钟的限制，只解释昨天那个过滤条件，不执行任务。"
    response = service.handle("今天只有20分钟，昨天那个还没懂。", "learning-day")
    assert response.tool_results == []
    contextual_requests = [request for request in transport.requests if any(
        item["content"].startswith("以下是本次请求绑定业务日期") for item in request["messages"])]
    assert facts_from(contextual_requests[-1]["messages"])["user_request"].startswith("今天只有20分钟")
    transport.reply = "可以叫小团。"
    service.handle("换个话题，给白猫起个名字。", "learning-day")
    assert not any(m["content"].startswith("以下是本次请求绑定业务日期") for m in transport.requests[-1]["messages"])
    assert service.growth_manager.tasks(DAY) == []


def test_rich_projection_stale_future_missing_and_no_write(tmp_path):
    growth = GrowthManager(tmp_path, now_provider=lambda: NOW)
    task = growth.add_task("过滤条件边界", date="2026-10-28", note="仍然困惑", duration_minutes=15)
    saved = growth.save_today_review("2026-10-28")
    growth.complete_by_id(task["id"], "2026-10-28")
    growth.add_task("未来不可使用", date="2026-10-30")
    growth.save_today_review("2026-10-30")
    before = fingerprint(tmp_path)
    history = LearningLoopService(growth).learning_history(DAY)
    yesterday = history["recent_days"][0]
    assert yesterday["saved_review"]["status"] == "stale"
    assert yesterday["saved_review"]["text"] == saved["review"]["text"]
    assert yesterday["plans"][0]["done"] is True
    assert yesterday["actual_minutes"] is None
    assert history["recent_days"][1]["saved_review"]["status"] == "missing"
    assert "未来不可使用" not in json.dumps(history, ensure_ascii=False)
    assert fingerprint(tmp_path) == before


def test_read_failure_is_explicit_not_empty_fact(tmp_path, monkeypatch):
    growth = GrowthManager(tmp_path, now_provider=lambda: NOW)
    def denied(*args, **kwargs):
        raise OSError("synthetic read failure")
    monkeypatch.setattr(growth, "tasks", denied)
    result = LearningLoopService(growth).learning_history(DAY)
    assert result["today"]["plans_count"] is None
    assert result["read_errors"] and result["recent_days"][0]["plans_count"] is None


def test_profile_is_read_only_and_excludes_archived_conflicted_temporary(learning_service):
    from modules.learning_context import learning_profile
    service, _ = learning_service
    manager = service.memory_manager
    archived = manager.add_memory("学习偏好已归档", category="preference")
    service.memory_service.archive_memory(archived["memory"]["id"])
    manager.add_memory("学习时临时要休息", category="habit", scope="temporary_state")
    before = fingerprint(service.project_root)
    result = learning_profile(service.memory_service)
    text = json.dumps(result, ensure_ascii=False)
    assert "偏好先自己试一遍" in text
    assert "已归档" not in text and "临时要休息" not in text and "伴侣" not in text
    assert fingerprint(service.project_root) == before


def test_context_scope_needs_real_learning_predecessor():
    assert is_learning_context("今天只有20分钟", [{"role": "user", "content": "Python过滤条件没懂"}])
    assert not is_learning_context("今天只有20分钟", [{"role": "user", "content": "给猫起名字"}])
    assert not is_learning_context("天气如何", [{"role": "user", "content": "Python过滤条件没懂"}])
    prior = [{"role": "user", "content": "帮我安排学习"}, {"role": "user", "content": "今天只有20分钟"}]
    assert is_learning_context("换个例子", prior)
    assert is_learning_context("再短一点", prior)
    assert is_learning_context("昨天那个没弄懂", [])
    assert not is_learning_context("换个例子", prior + [{"role": "user", "content": "换个话题，给猫起名"}])


@pytest.mark.parametrize("followup", [
    "我还是没看明白。", "这里有点绕，能拆开讲吗？", "能从最简单的例子开始吗？",
    "能讲慢一点吗？",
])
def test_natural_short_tutoring_followup_needs_learning_predecessor(followup):
    learning = [{"role": "user", "content": "请讲解Python过滤条件"}]
    assert is_learning_context(followup, learning)
    assert not is_learning_context(followup, [])
    assert not is_learning_context(followup, [{"role": "user", "content": "猫咪名字怎么起"}])
    assert not is_learning_context(followup, learning + [{"role": "user", "content": "换个话题，给猫起名"}])


def test_natural_tutoring_followup_gets_real_history_without_writes(learning_service):
    service, transport = learning_service
    transport.reply = "先用最简单的过滤条件示例一步一步看。"
    service.handle("请讲解Python过滤条件，不要安排计划。", "natural-followup")
    before = fingerprint(service.project_root / "data/private")
    reply = service.handle("我还是没看明白，能拆开讲吗？", "natural-followup")
    assert reply.tool_results == []
    facts = facts_from(transport.requests[-1]["messages"])
    assert "空列表与边界值混淆" in json.dumps(facts, ensure_ascii=False)
    assert facts["learning_discussions"]["included_count"] >= 1
    for name, value in before.items():
        if name.endswith(("today_plan.json", "action_log.json", "growth_log.json")):
            assert (service.project_root / "data/private" / name).read_bytes() == value


def test_today_review_and_budget_keep_today_before_older_material(tmp_path):
    from datetime import timedelta
    growth = GrowthManager(tmp_path, now_provider=lambda: NOW)
    today = growth.add_task("今天真实的边界条件", duration_minutes=20)
    saved = growth.save_today_review(DAY)
    for index in range(1, 24):
        day = (NOW.date() - timedelta(days=index)).isoformat()
        growth.add_task("旧Python练习" + str(index), date=day, note="长的学习困难" * 500)
        growth.save_today_review(day)
    before = fingerprint(tmp_path)
    result = LearningLoopService(growth).learning_history(DAY)
    assert result["today"]["plans"][0]["uid"] == today["uid"]
    assert result["today"]["saved_review"]["text"] == saved["review"]["text"]
    assert result["today"]["saved_review"]["status"] == "current"
    assert result["older_saved_reviews"]["available_count"] == 16
    assert result["coverage"]["truncated"]
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 26000
    assert fingerprint(tmp_path) == before


def test_compatibility_projection_only_stub_does_not_break_chat(learning_service):
    from types import SimpleNamespace
    service, _ = learning_service
    conversation = service.conversation_service
    original = conversation.learning_loop_service
    conversation.learning_loop_service = SimpleNamespace(planning_evidence=original.planning_evidence)
    messages = conversation.build_llm_messages("请安排今天的学习", "compat", planning_advice=True)
    facts = facts_from(messages)
    assert facts["learning_history"]["read_errors"][0]["error"] == "service_unavailable"
    assert facts["today"]["total"] == 0


def test_actual_adoption_keeps_uid_duration_and_cancel_writes_nothing(learning_service):
    service, transport = learning_service
    first = service.handle("给两个今天Python学习候选，先不加入计划。", "adoption")
    assert first.tool_results == []
    selected = service.handle("第二个加入今天计划", "adoption")
    assert [(result.tool, result.success) for result in selected.tool_results] == [("add_plan", True)]
    task = service.growth_manager.tasks(DAY)[0]
    assert task["duration_minutes"] == 10 and task["uid"]
    again = service.handle("第一个也加入今天计划", "adoption")
    assert [(result.tool, result.success) for result in again.tool_results] == [("add_plan", True)]
    tasks = service.growth_manager.tasks(DAY)
    assert tasks[0]["uid"] == task["uid"] and tasks[1]["duration_minutes"] == 15
    service.handle("再给两个今天Python学习候选，只展示。", "cancel")
    before = fingerprint(service.project_root / "data/private")
    cancelled = service.handle("这两条算了，都别加。", "cancel")
    assert cancelled.tool_results == []
    # Chat history records the cancellation; business files must not change.
    for name, value in before.items():
        if name.endswith(("today_plan.json", "action_log.json", "growth_log.json")):
            assert (service.project_root / "data/private" / name).read_bytes() == value


def test_second_plain_learning_followup_and_future_discussion_filter(learning_service):
    service, transport = learning_service
    transport.reply = "先看过滤条件如何判断，然后再决定保留哪个元素。"
    service.handle("请讲解Python过滤条件，不要给计划。", "followup")
    service.handle("今天只有20分钟", "followup")
    service.handle("换个例子，再短一点", "followup")
    assert facts_from(transport.requests[-1]["messages"])["learning_discussions"]["included_count"] >= 1
    history = service.chat_history_manager
    future = history.ensure_daily_learning_session("2026-10-30")["session_id"]
    history.add_message(future, "user", "Python未来的秘密过滤练习")
    history.add_message(future, "assistant", "未来内容不能作为昨天事实。")
    legacy = history.new_session("旧手动学习会话")["session_id"]
    history.now_provider = lambda: datetime(2026, 10, 30, 10)
    history.add_message(legacy, "user", "Python跨日手动会话的未来秘密过滤练习")
    before = fingerprint(service.project_root)
    context = history.learning_fragments("Python过滤", business_date=DAY)
    assert "未来的秘密" not in json.dumps(context, ensure_ascii=False)
    assert "手动会话的未来秘密" not in json.dumps(context, ensure_ascii=False)
    assert fingerprint(service.project_root) == before
