import json

from modules.intent_router import LLMIntentParser
from v18_test_support import build_service


class UpdatePreviousTaskLLM:
    def __init__(self, explicit_ref=None):
        self.explicit_ref = explicit_ref

    def chat(self, messages, **kwargs):
        if "You classify one Chinese user message" not in str(messages[0].get("content", "")):
            return ""
        entities = {"changes": {"duration_minutes": 30}}
        if self.explicit_ref:
            entities["task_ref"] = self.explicit_ref
        text = str(messages[0].get("content", "")).split("\nUser message: ", 1)[-1]
        is_add = text.startswith("添加计划：")
        if is_add:
            entities = {"title": text.split("：", 1)[1]}
        return json.dumps({
            "mode": "write", "intent": "add_plan" if is_add else "update_plan", "entities": entities,
            "proposed_tool": "add_plan" if is_add else "update_plan", "confidence": 0.98,
            "follow_up_target": None if is_add else "last_task", "needs_confirmation": False,
            "warnings": [], "clarification_question": None, "candidate_actions": [],
            "subject": "self", "polarity": "positive", "modality": "commitment",
            "request_mode": "execute", "explicit_command": True,
        }, ensure_ascii=False)


class ImplicitPronounTaskLLM(UpdatePreviousTaskLLM):
    def chat(self, messages, **kwargs):
        payload = json.loads(super().chat(messages, **kwargs))
        if payload["intent"] == "update_plan":
            payload["follow_up_target"] = None
            payload["entities"]["reference_text"] = "它"
        return json.dumps(payload, ensure_ascii=False)


class ChatMisclassifyingCompletionLLM:
    def chat(self, messages, **kwargs):
        if "You classify one Chinese user message" not in str(messages[0].get("content", "")):
            return "我听见了，我们可以继续聊聊。"
        return json.dumps({
            "mode": "chat", "intent": "chat", "entities": {},
            "proposed_tool": None, "confidence": 0.91,
            "follow_up_target": None, "needs_confirmation": False,
            "warnings": [], "clarification_question": None,
            "candidate_actions": [], "subject": "self",
            "polarity": "positive", "modality": "commitment",
            "request_mode": "discuss", "explicit_command": False,
        }, ensure_ascii=False)


class MalformedChangesLLM(UpdatePreviousTaskLLM):
    def chat(self, messages, **kwargs):
        payload = json.loads(super().chat(messages, **kwargs))
        if payload["intent"] == "update_plan":
            payload["follow_up_target"] = None
            payload["entities"] = {
                "reference_text": "它",
                "changes": "晚上，三十分钟",
            }
        return json.dumps(payload, ensure_ascii=False)


def configured_service(tmp_path, llm):
    service, growth, *_ = build_service(tmp_path, llm)
    service.intent_router.configure_llm(LLMIntentParser(llm.chat), True)
    service.conversation_service.semantic_decision_compatibility_enabled = False
    return service, growth


def test_model_last_task_reference_uses_successful_task_before_schema_validation(tmp_path):
    service, growth = configured_service(tmp_path, UpdatePreviousTaskLLM())
    service.handle("添加计划：整理验收练习", "same-session")
    original = growth.tasks()[0]
    response = service.handle("刚才那项改成三十分钟，其他照旧。", "same-session")
    assert response.status == "completed"
    assert [r.tool for r in response.tool_results] == ["update_plan"]
    assert growth.tasks()[0]["uid"] == original["uid"]
    assert growth.tasks()[0]["duration_minutes"] == 30


def test_last_task_reference_does_not_cross_conversations(tmp_path):
    service, growth = configured_service(tmp_path, UpdatePreviousTaskLLM())
    service.handle("添加计划：整理验收练习", "original-session")
    response = service.handle("把刚才那项改成三十分钟。", "new-session")
    assert response.status == "clarification"
    assert response.tool_results == []
    assert "task_ref" not in response.message
    assert growth.tasks()[0]["duration_minutes"] is None


def test_explicit_plan_target_wins_over_last_task_marker(tmp_path):
    llm = UpdatePreviousTaskLLM()
    service, growth = configured_service(tmp_path, llm)
    first = growth.add_task("整理验收练习甲")
    service.handle("添加计划：整理验收练习乙", "explicit-session")
    llm.explicit_ref = first["uid"]
    response = service.handle("把验收练习甲改成三十分钟。", "explicit-session")
    assert response.status == "completed"
    assert growth.tasks()[0]["duration_minutes"] == 30
    assert growth.tasks()[1]["duration_minutes"] is None


def test_structured_pronoun_prefers_the_single_session_task_over_global_plans(tmp_path):
    service, growth = configured_service(tmp_path, ImplicitPronounTaskLLM())
    unrelated = growth.add_task("整理旧记录")
    service.handle("添加计划：整理验收练习", "pronoun-session")

    response = service.handle("现在把它改成三十分钟。", "pronoun-session")

    assert response.status == "completed"
    target = next(item for item in growth.tasks() if item["title"] == "整理验收练习")
    assert target["duration_minutes"] == 30
    other = next(item for item in growth.tasks() if item["uid"] == unrelated["uid"])
    assert other["duration_minutes"] is None


def test_exact_pending_plan_completion_repairs_model_chat_decision(tmp_path):
    service, growth = configured_service(tmp_path, ChatMisclassifyingCompletionLLM())
    target = growth.add_task("这轮验收记录按时间顺序整理")
    other = growth.add_task("整理其他材料")

    response = service.handle("这轮验收记录按时间顺序整理已经完成了。", "grounded-session")

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["complete_plan"]
    tasks = {item["uid"]: item for item in growth.tasks()}
    assert tasks[target["uid"]]["status"] == "completed"
    assert tasks[other["uid"]]["status"] == "pending"


def test_longest_exact_pending_title_wins_over_its_shorter_prefix(tmp_path):
    service, growth = configured_service(tmp_path, ChatMisclassifyingCompletionLLM())
    short = growth.add_task("写小说")
    long = growth.add_task("写小说至少五百字")

    response = service.handle("写小说至少五百字已经做完了。", "specific-session")

    assert response.status == "completed"
    tasks = {item["uid"]: item for item in growth.tasks()}
    assert tasks[short["uid"]]["status"] == "pending"
    assert tasks[long["uid"]]["status"] == "completed"


def test_grounded_completion_guard_keeps_negative_question_and_unrelated_chat_read_only(tmp_path):
    service, growth = configured_service(tmp_path, ChatMisclassifyingCompletionLLM())
    target = growth.add_task("整理验收材料")

    for index, text in enumerate((
        "整理验收材料还没完成。",
        "整理验收材料完成了吗？",
        "如果整理验收材料完成了会怎样？",
        "我今天学习了随机森林。",
    )):
        response = service.handle(text, f"negative-{index}")
        assert response.tool_results == []

    task = next(item for item in growth.tasks() if item["uid"] == target["uid"])
    assert task["status"] == "pending"


def test_malformed_model_changes_are_recovered_from_grounded_user_time_fields(tmp_path):
    service, growth = configured_service(tmp_path, MalformedChangesLLM())
    unrelated = growth.add_task("整理旧记录")
    service.handle("添加计划：整理验收练习", "malformed-session")

    response = service.handle(
        "现在把它改到晚上，三十分钟保持不变。",
        "malformed-session",
    )

    assert response.status == "completed"
    assert [item.tool for item in response.tool_results] == ["update_plan"]
    target = next(item for item in growth.tasks() if item["title"] == "整理验收练习")
    assert target["time_slot"] == "晚上"
    assert target["duration_minutes"] == 30
    other = next(item for item in growth.tasks() if item["uid"] == unrelated["uid"])
    assert other["time_slot"] == ""
    assert other["duration_minutes"] is None


def test_malformed_model_changes_without_grounded_values_asks_for_clarification(tmp_path):
    service, growth = configured_service(tmp_path, MalformedChangesLLM())
    service.handle("添加计划：整理验收练习", "clarify-session")

    response = service.handle("现在把它改一下。", "clarify-session")

    assert response.status == "clarification"
    assert response.tool_results == []
    assert "具体要修改" in response.message
    target = next(item for item in growth.tasks() if item["title"] == "整理验收练习")
    assert target["time_slot"] == ""
    assert target["duration_minutes"] is None
