"""G1 online defect regressions for prompt visibility and review date wording.

These tests replay offline provider responses through the real service, router,
pipeline, managers and persistence.  They never call a real model or the
desktop, and they do not change the captured online evidence.
"""
import json
from copy import deepcopy
from datetime import datetime

import pytest

from modules.agent_planner import INTENT_TOOL_MAP
from modules.chinese_entity_parser import ChineseEntityParser
from test_beta_online_failure_replay import _build_service, _provider_body


SEMANTIC_MARKER = "You classify one Chinese user message"
SCOPE = "g1-first-batch-regression"
SPECIAL_INTENT_TOOLS = {
    "reminder_control": ("pause_reminders", "resume_reminders"),
    "dance": ("play_dance",),
    "sleep_pet": ("sleep_pet",),
    "wake_pet": ("wake_pet",),
}


class Transport:
    def __init__(self, semantic, reply="好的。"):
        self.semantic = semantic
        self.reply = reply
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        assert method == "POST" and str(url).startswith("https://api.deepseek.com/")
        self.calls.append(deepcopy(payload))
        assert len(self.calls) <= 2, "unexpected provider retry or repair"
        system_prompt = str(payload["messages"][0].get("content", ""))
        if SEMANTIC_MARKER in system_prompt:
            return _provider_body(
                {"content": json.dumps(self.semantic, ensure_ascii=False)}
            )
        return _provider_body({"content": self.reply})


def chat_decision():
    return {
        "mode": "chat",
        "intent": "chat",
        "entities": {},
        "proposed_tool": None,
        "confidence": 0.9,
        "modality": "question",
        "request_mode": "discuss",
        "explicit_command": False,
    }


def daily_review_decision():
    # Mirrors the captured g1-d4-t01 semantic response: the model says 昨天,
    # and the request boundary must resolve it to the business yesterday.
    return {
        "mode": "read",
        "intent": "daily_review",
        "entities": {"date": "昨天"},
        "proposed_tool": "generate_daily_review",
        "confidence": 0.85,
        "modality": "question",
        "request_mode": "query",
        "explicit_command": False,
    }


@pytest.fixture
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-replay-no-credential")
    runs = []

    def make(semantic, reply="好的。"):
        transport = Transport(deepcopy(semantic), reply)
        service = _build_service(tmp_path / str(len(runs)), transport)
        responses = []
        runs.append((service, transport, responses))
        return service, transport, responses

    yield make
    (tmp_path / "semantic-visibility-review-date-evidence.json").write_text(
        json.dumps(
            [
                {
                    "requests": transport.calls,
                    "semantic_response": transport.semantic,
                    "responses": [response.to_dict() for response in responses],
                    "plans": service.growth_manager.tasks(),
                    "reviews": service.growth_manager.entries(),
                    "memories": service.memory_manager.memories(),
                    "diagnostics": service.diagnostic_snapshot(),
                }
                for service, transport, responses in runs
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def allowed_intents(system_prompt):
    text = str(system_prompt).split("Allowed intents: ", 1)[1]
    text = text.split(". ", 1)[0]
    return {item.strip() for item in text.split(",") if item.strip()}


def use_business_clock(service, moment):
    clock = {"now": moment}
    now = lambda: clock["now"]  # noqa: E731 - test clock holder
    service.growth_manager.now_provider = now
    service.growth_manager.timestamp_provider = now
    conversation = service.conversation_service
    conversation.now_provider = now
    conversation.business_now_provider = now
    learning_loop = getattr(service, "learning_loop_service", None)
    if learning_loop is not None:
        learning_loop.now_provider = now
    business_parser = ChineseEntityParser(now_provider=now)
    conversation.semantic_action_parser.feature_extractor.entity_parser = (
        business_parser
    )
    conversation.business_resolver.entity_parser = business_parser
    conversation.response_composer.now_provider = now
    return clock


def test_prompt_advertises_only_registry_visible_intents(build):
    service, transport, responses = build(chat_decision())
    response = service.handle(
        "你确定这是我说的吗？请区分刚保存的内容和你的推测。", SCOPE
    )
    responses.append(response)
    assert transport.calls, "the semantic provider was not called"
    prompt = str(transport.calls[0]["messages"][0]["content"])
    advertised = allowed_intents(prompt)

    registry = service.tool_registry
    for intent in advertised - {"chat", "multi_action"}:
        tool_names = SPECIAL_INTENT_TOOLS.get(intent) or (INTENT_TOOL_MAP[intent],)
        for tool_name in tool_names:
            tool = registry.get(tool_name)
            assert tool is not None, (intent, tool_name)
            assert tool.enabled and tool.model_visible, (intent, tool_name)

    for hidden_intent in (
        "show_memory_audit",
        "show_archived_memories",
        "show_memory_conflicts",
        "update_plan",
        "delete_memory",
        "inspect_plan_duplicates",
    ):
        assert hidden_intent not in advertised
    assert {"save_review", "show_memory", "show_plan"} <= advertised
    # Source challenges and audit boundaries are classification guidance, not
    # a second visibility list.
    assert "source challenge" in prompt
    assert "internal audit records" in prompt


def test_hidden_audit_proposal_stays_rejected_not_downgraded(build):
    semantic = {
        "mode": "read",
        "intent": "show_memory_audit",
        "entities": {},
        "proposed_tool": "show_memory_audit",
        "confidence": 0.72,
        "modality": "question",
        "request_mode": "query",
        "explicit_command": False,
    }
    service, transport, responses = build(semantic)
    plans_before = deepcopy(service.growth_manager.tasks())
    reviews_before = deepcopy(service.growth_manager.entries())
    memories_before = deepcopy(service.memory_manager.memories())

    response = service.handle(
        "你确定这是我说的吗？请区分刚保存的内容和你的推测。", SCOPE
    )
    responses.append(response)

    assert response.status == "failed"
    assert response.tool_results == []
    diagnostic = service.diagnostic_snapshot()[-1]
    assert diagnostic["pipeline_outcome"] == "reject"
    errors = diagnostic["pipeline_diagnostics"]["validation_errors_before_repair"]
    assert [item["code"] for item in errors] == ["tool_not_model_visible"]
    assert service.growth_manager.tasks() == plans_before
    assert service.growth_manager.entries() == reviews_before
    assert service.memory_manager.memories() == memories_before


def test_yesterday_review_prompt_carries_query_date_and_business_today(build):
    reply = "十一月四日那份复盘里，你完成了异常处理练习，还有一项整理笔记没完成。"
    service, transport, responses = build(daily_review_decision(), reply)
    growth = service.growth_manager
    clock = use_business_clock(service, datetime(2026, 11, 4, 20, 0))
    task = growth.add_task("练习Python异常处理", duration_minutes=30)
    growth.add_task("整理异常处理笔记", duration_minutes=20)
    growth.complete_by_id(task["id"])
    growth.add_record("额外复看异常处理示例", duration_minutes=10)
    saved = growth.save_today_review()
    assert saved["date"] == "2026-11-04"
    clock["now"] = datetime(2026, 11, 5, 9, 0)
    plans_before = deepcopy(growth.tasks())
    tasks_1104_before = deepcopy(growth.tasks("2026-11-04"))
    actions_before = deepcopy(growth.records_for_date("2026-11-04"))
    reviews_before = deepcopy(growth.entries())
    memories_before = deepcopy(service.memory_manager.memories())

    response = service.handle(
        "请查看昨天练习Python异常处理的复盘，核对昨天实际完成和没完成的部分。",
        SCOPE,
    )
    responses.append(response)

    assert [(item.tool, item.success) for item in response.tool_results] == [
        ("generate_daily_review", True)
    ]
    review = response.tool_results[0].data["review"]
    assert review["date"] == "2026-11-04"
    assert response.message == reply
    assert len(transport.calls) == 2
    system_prompts = "\n".join(
        str(message.get("content", ""))
        for message in transport.calls[-1]["messages"]
    )
    assert "2026-11-04" in system_prompts
    assert "2026-11-05" in system_prompts
    assert "已核验的指定日期复盘 JSON" in system_prompts
    assert "已核验的今日复盘 JSON" not in system_prompts
    assert "练习Python异常处理" in system_prompts
    assert "整理异常处理笔记" in system_prompts
    assert growth.tasks() == plans_before
    assert growth.tasks("2026-11-04") == tasks_1104_before
    assert growth.records_for_date("2026-11-04") == actions_before
    assert growth.entries() == reviews_before
    assert service.memory_manager.memories() == memories_before


def test_current_day_review_prompt_keeps_today_wording(build):
    reply = "今天完成了异常处理练习，整理笔记还在等下一次。"
    decision = daily_review_decision()
    decision["entities"] = {}
    service, transport, responses = build(decision, reply)
    growth = service.growth_manager
    clock = use_business_clock(service, datetime(2026, 11, 5, 20, 0))
    task = growth.add_task("练习Python异常处理", duration_minutes=30)
    growth.complete_by_id(task["id"])
    plans_before = deepcopy(growth.tasks())
    entries_before = deepcopy(growth.entries())

    response = service.handle("查看今日复盘。", SCOPE)
    responses.append(response)

    assert [(item.tool, item.success) for item in response.tool_results] == [
        ("generate_daily_review", True)
    ]
    assert response.tool_results[0].data["review"]["date"] == clock["now"].date().isoformat()
    assert response.message == reply
    system_prompts = "\n".join(
        str(message.get("content", ""))
        for message in transport.calls[-1]["messages"]
    )
    assert "2026-11-05" in system_prompts
    assert "已核验的今日复盘 JSON" in system_prompts
    assert growth.tasks() == plans_before
    assert growth.entries() == entries_before
