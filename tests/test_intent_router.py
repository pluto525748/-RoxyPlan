import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from frontend import pet_app
from frontend.pet_app import ChatWindow
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter, LLMIntentParser, match_plan_task


class FakePetController:
    def __init__(self):
        self.paused = 0
        self.resumed = 0
        self.completed = 0

    def record_interaction(self):
        return False

    def pause_proactive_reminders(self):
        self.paused += 1

    def resume_proactive_reminders(self):
        self.resumed += 1

    def notify_plan_completed(self):
        self.completed += 1


def test_natural_add_plan():
    result = IntentRouter().route("我今天想学半小时机器学习")

    assert result["intent"] == "add_plan"
    assert result["slots"]["tasks"] == ["学半小时机器学习"]


def test_one_sentence_adds_multiple_plans():
    result = IntentRouter().route("帮我安排一下今天：学机器学习、测试洛琪希")

    assert result["intent"] == "add_plan"
    assert result["slots"]["tasks"] == ["学机器学习", "测试洛琪希"]


def test_future_completion_is_a_plan_not_a_completed_task():
    result = IntentRouter().route("一会儿我要去完成 RoxyPlan 文档")

    assert result["intent"] == "add_plan"
    assert result["slots"]["tasks"] == ["RoxyPlan 文档"]


def test_natural_complete_plan_extracts_query():
    router = IntentRouter()

    assert router.route("我学完机器学习了")["slots"]["query"] == "机器学习"
    assert router.route("刚才把 RoxyPlan 文档整理完了")["slots"]["query"] == "RoxyPlan 文档"
    assert router.route("帮我把成长面板标记完成")["intent"] == "complete_plan"


def test_fuzzy_task_match_and_ambiguous_candidates():
    tasks = [
        {"id": 1, "title": "学习机器学习30分钟", "done": False},
        {"id": 2, "title": "整理 RoxyPlan 文档", "done": False},
    ]

    matched = match_plan_task("Roxy 文档整理", tasks)
    ambiguous = match_plan_task(
        "英语",
        [
            {"id": 1, "title": "学习英语30分钟", "done": False},
            {"id": 2, "title": "学习英语听力20分钟", "done": False},
        ],
    )

    assert matched["status"] == "matched"
    assert matched["task"]["id"] == 2
    assert ambiguous["status"] == "ambiguous"
    assert [item["id"] for item in ambiguous["candidates"]] == [1, 2]


def test_natural_action_review_memory_and_fallback():
    router = IntentRouter()

    action = router.route("记录一下：我刚才学习了逻辑回归")
    implicit_action = router.route("今天推进了舞蹈模块")
    review = router.route("今天状态怎么样")
    contextual_review = router.route("今天状态一般，复盘一下吧")
    memory = router.route("以后你要记得我不喜欢熬夜")
    fallback = router.route("今天天气怎么样")

    assert action["intent"] == "add_action_log"
    assert action["slots"]["content"] == "我刚才学习了逻辑回归"
    assert implicit_action["intent"] == "add_action_log"
    assert review["intent"] == "daily_review"
    assert contextual_review["intent"] == "daily_review"
    assert memory["intent"] == "add_memory_request"
    assert memory["slots"]["content"] == "我不喜欢熬夜"
    assert fallback["intent"] == "chat"


def test_show_and_save_intents():
    router = IntentRouter()

    assert router.route("我今天还有什么没做")["intent"] == "show_plan"
    assert router.route("看看行动记录")["intent"] == "show_action_log"
    assert router.route("看看成长日志")["intent"] == "show_growth_log"
    assert router.route("保存今天的复盘")["intent"] == "save_review"


def test_reminder_control_intents():
    router = IntentRouter()

    paused = router.route("先别提醒我")
    later = router.route("晚点提醒我")
    resumed = router.route("恢复提醒")

    assert paused["intent"] == "reminder_control"
    assert paused["slots"]["action"] == "pause"
    assert paused["source"] == "fixed_command"
    assert later["slots"]["action"] == "pause"
    assert resumed["slots"]["action"] == "resume"
    assert resumed["source"] == "fixed_command"


def test_exact_reminder_controls_keep_direction_without_semantic_model():
    def fail_if_called(_messages):
        raise AssertionError("exact reminder controls must not call the model")

    router = IntentRouter(
        enable_llm=True,
        llm_parser=LLMIntentParser(fail_if_called),
    )

    for text in ("先别提醒我", "暂停提醒", "接下来先别提醒我"):
        result = router.route_semantic_decision(text)
        assert result["intent"] == "reminder_control"
        assert result["slots"]["action"] == "pause"
    for text in ("恢复提醒", "继续提醒我", "重新开启提醒"):
        result = router.route_semantic_decision(text)
        assert result["intent"] == "reminder_control"
        assert result["slots"]["action"] == "resume"


def test_explicit_pet_sleep_command_does_not_capture_user_rest_chat():
    def fail_if_called(_messages):
        raise AssertionError("explicit pet sleep must not call the model")

    router = IntentRouter(
        enable_llm=True,
        llm_parser=LLMIntentParser(fail_if_called),
    )

    sleep = router.route_semantic_decision("洛琪希，你休息一下")
    assert sleep["intent"] == "sleep_pet"
    assert sleep["source"] == "fixed_command"
    assert IntentRouter().route("我休息一下")["intent"] == "chat"


def test_fixed_commands_keep_priority_with_structured_intents():
    router = IntentRouter()
    fixed_commands = {
        "今日计划：学习机器学习30分钟": "add_plan",
        "完成计划1": "complete_plan",
        "删除计划1": "delete_plan",
        "记录：完成测试": "add_action_log",
        "今日复盘": "daily_review",
        "记住：我喜欢蓝色": "add_memory_request",
        "忘记：我喜欢蓝色": "delete_memory",
    }

    for command, intent in fixed_commands.items():
        result = router.route(command)
        assert result["intent"] == intent
        assert result["source"] == "fixed_command"


def test_structured_request_authority_fields_survive_router_enrichment():
    payload = json.dumps(
        {
            "mode": "write",
            "intent": "add_plan",
            "confidence": 0.97,
            "entities": {"title": "阅读算法"},
            "proposed_tool": "add_plan",
            "follow_up_target": None,
            "needs_confirmation": False,
            "warnings": [],
            "clarification_question": None,
            "candidate_actions": [],
            "subject": "self",
            "polarity": "positive",
            "modality": "desire",
            "request_mode": "execute",
            "explicit_command": True,
        },
        ensure_ascii=False,
    )
    router = IntentRouter(LLMIntentParser(lambda _messages: payload), enable_llm=True)

    result = router.route_semantic_decision("把阅读算法加入今天计划")

    assert result["request_mode"] == "execute"
    assert result["explicit_command"] is True


def test_chat_window_natural_growth_flow_does_not_call_llm():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = GrowthManager(
            root / "private",
            now_provider=lambda: datetime(2026, 7, 15, 18, 0, 0),
        )
        original_memory_file = pet_app.MEMORY_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        try:
            window = ChatWindow(growth_service=manager)

            def fail_if_model_called(_user_text):
                raise AssertionError("Natural growth intents must not call the LLM")

            window.start_ai_reply = fail_if_model_called
            for message in (
                "我今天想学半小时机器学习",
                "把它加入今天计划",
                "把机器学习计划标记完成",
                "确认",
                "今天推进了舞蹈模块",
                "今天状态怎么样",
                "保存今天的复盘",
            ):
                window.input_box.setText(message)
                window.send_message()

            tasks = manager.tasks()
            assert len(tasks) == 1
            assert tasks[0]["done"] is True, tasks
            assert len(manager.records_for_date()) == 1
            assert len(manager.entries()) == 1
            assert "标记完成" in window.transcript.toPlainText()
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file


def test_chat_window_controls_reminders_and_notifies_completion():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = GrowthManager(
            root / "private",
            now_provider=lambda: datetime(2026, 7, 15, 18, 30, 0),
        )
        pet = FakePetController()
        original_memory_file = pet_app.MEMORY_FILE
        pet_app.MEMORY_FILE = root / "memory.json"
        try:
            window = ChatWindow(pet_controller=pet, growth_service=manager)
            window.start_ai_reply = lambda *_args: (_ for _ in ()).throw(
                AssertionError("Reminder controls must not call the LLM")
            )
            for message in (
                "把整理测试文档加入今天计划",
                "测试文档整理完了",
                "接下来先别提醒我",
                "恢复提醒",
            ):
                window.input_box.setText(message)
                window.send_message()

            assert pet.completed == 1
            assert pet.paused == 1
            assert pet.resumed == 1
            window.close()
            app.processEvents()
        finally:
            pet_app.MEMORY_FILE = original_memory_file


if __name__ == "__main__":
    test_natural_add_plan()
    test_one_sentence_adds_multiple_plans()
    test_future_completion_is_a_plan_not_a_completed_task()
    test_natural_complete_plan_extracts_query()
    test_fuzzy_task_match_and_ambiguous_candidates()
    test_natural_action_review_memory_and_fallback()
    test_show_and_save_intents()
    test_reminder_control_intents()
    test_fixed_commands_keep_priority_with_structured_intents()
    test_chat_window_natural_growth_flow_does_not_call_llm()
    test_chat_window_controls_reminders_and_notifies_completion()
    print("intent router tests passed")
