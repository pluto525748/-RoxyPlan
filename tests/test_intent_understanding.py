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

from frontend.pet_app import ChatWindow
from modules.chat_history_manager import ChatHistoryManager
from modules.context_builder import ContextBuilder
from modules.contracts import AgentResponse
from modules.growth_manager import GrowthManager
from modules.intent_router import IntentRouter, LLMIntentParser
from modules.memory_manager import MemoryManager
from modules.memory_retriever import MemoryRetriever


def test_natural_memory_recall_phrases():
    router = IntentRouter()
    for text in (
        "你都记住了我的什么信息",
        "你了解我什么",
        "我之前告诉过你哪些事",
        "看看关于我的长期信息",
    ):
        result = router.route(text)
        assert result["intent"] == "show_memory"
        assert result["source"] in {"rule", "memory_query_guard"}
        assert result["confidence"] >= 0.9


def test_natural_growth_memory_and_delete_intents():
    router = IntentRouter()
    assert router.route("我今天想学半小时机器学习")["intent"] == "add_plan"
    assert router.route("机器学习学完了")["intent"] == "complete_plan"
    assert router.route("我刚刚测试了成长面板")["intent"] == "add_action_log"
    assert router.route("今天状态一般，复盘一下")["intent"] == "daily_review"
    assert router.route("帮我记住我喜欢晚上学习")["intent"] == "add_memory_request"
    delete = router.route("帮我把机器学习计划删掉")
    assert delete["intent"] == "delete_plan"
    assert delete["needs_confirmation"] is True
    assert router.route("今天天气如何")["intent"] == "chat"


def test_llm_parser_failure_falls_back_to_rules():
    def broken_chat(_messages):
        raise OSError("model offline")

    router = IntentRouter(LLMIntentParser(broken_chat), enable_llm=True)
    result = router.route("这是一个没有匹配规则的普通问题")
    assert result["intent"] == "chat"
    assert result["source"] == "fallback"


def test_learning_query_does_not_use_health_from_summary_or_recent_history():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        manager = MemoryManager(
            root / "memory.json",
            backup_dir=root / "backups",
            conflict_file=root / "conflicts.json",
            now_provider=lambda: datetime(2026, 7, 16, 10, 0, 0),
        )
        manager.add_memory("我正在学习机器学习", category="learning")
        manager.add_memory("我的肠胃不稳定", category="health")
        retriever = MemoryRetriever(manager)
        retrieved = retriever.retrieve(
            "机器学习应该怎么继续",
            "用户刚才提到肠胃不舒服。",
            update_usage=False,
        )
        assert all(item["memory"]["category"] != "health" for item in retrieved)

        builder = ContextBuilder(recent_message_limit=8)
        messages = builder.build(
            personality_context="人格",
            memory_context="长期记忆：\n- 我正在学习机器学习",
            session_summary="",
            recent_messages=[
                {"role": "user", "content": "我的肠胃不舒服"},
                {"role": "assistant", "content": "先喝温水，注意清淡饮食。"},
                {"role": "user", "content": "我想学习机器学习"},
                {"role": "assistant", "content": "可以从线性回归开始。"},
            ],
            knowledge_context="",
            current_user_input="机器学习应该怎么继续",
        )
        combined = "\n".join(item["content"] for item in messages)
        assert "肠胃不舒服" not in combined
        assert "线性回归" in combined


def test_chat_window_routes_memory_recall_without_model_call():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        memory_manager = MemoryManager(root / "memory.json")
        memory_manager.add_memory("我正在学习机器学习", category="learning")
        window = ChatWindow(
            memory_manager=memory_manager,
            growth_service=GrowthManager(root / "growth"),
            chat_history_manager=ChatHistoryManager(root / "private"),
        )
        window.start_ai_reply = lambda _text: (_ for _ in ()).throw(
            AssertionError("memory recall must not call the model")
        )
        window.input_box.setText("你都记住了我的什么信息")
        window.send_message()
        assert "我正在学习机器学习" in window.transcript.toPlainText()
        window.close()
        app.processEvents()


def test_v171_daily_action_and_history_phrases():
    router = IntentRouter()
    context = {
        "last_user_message": "我早上听了三个 AI 前沿电台",
        "last_assistant_message": "我可以展示一段舞蹈。",
    }
    cases = {
        "把这个也记录为行动": "add_action_log",
        "我今天早上坐地铁时听了三个 AI 前沿电台，把这个记成今天的行动": "add_action_log",
        "下午帮我留 50 分钟学特征工程，放进今天要做的事里": "add_plan",
        "记住我最近压力有点大": "add_memory_request",
        "这点请记住：我的肠胃比较敏感": "add_memory_request",
        "你记得我以前跟你的对话吗，不是今天的": "show_conversation_history",
    }
    for text, expected in cases.items():
        assert router.route(text, context)["intent"] == expected


def test_dance_execution_is_distinct_from_dance_discussion():
    router = IntentRouter()
    context = {"last_assistant_message": "我可以展示一段舞蹈。"}
    for text in (
        "跳舞",
        "跳个舞看看",
        "给我跳一个",
        "来一段舞",
        "表演一下舞蹈",
        "能跳一段给我看看吗",
        "展示一下你的舞蹈",
        "dance for me",
        "能给我来一段吗",
    ):
        assert router.route(text, context)["intent"] == "dance"
    for text in (
        "你会跳舞吗",
        "跳舞有什么好处",
        "我不想跳舞",
        "怎么制作跳舞动画",
    ):
        assert router.route(text, context)["intent"] == "chat"


def test_chat_window_ignores_stale_worker_reply():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        window = ChatWindow(
            memory_manager=MemoryManager(root / "memory.json"),
            growth_service=GrowthManager(root / "growth"),
            chat_history_manager=ChatHistoryManager(root / "private"),
        )
        window.transcript.clear()
        window._message_sequence = 2
        window._active_reply_sequence = 1
        window.finish_ai_reply(AgentResponse("chat", "过期回复"), 1)
        assert "过期回复" not in window.transcript.toPlainText()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    test_natural_memory_recall_phrases()
    test_natural_growth_memory_and_delete_intents()
    test_llm_parser_failure_falls_back_to_rules()
    test_learning_query_does_not_use_health_from_summary_or_recent_history()
    test_chat_window_routes_memory_recall_without_model_call()
    test_v171_daily_action_and_history_phrases()
    test_dance_execution_is_distinct_from_dance_discussion()
    test_chat_window_ignores_stale_worker_reply()
    print("intent understanding tests passed")
