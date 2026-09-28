import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from frontend.pet_app import ChatWindow
from modules.chat_history_manager import ChatHistoryManager
from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.memory_service import MemoryService
from tests.isolation_support import build_isolated_memory_service


def test_desktop_send_message_lists_completed_plan_without_claim_guard_block():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        task = growth.plan_store.add_task("验证桌面查询")
        growth.plan_store.complete_by_id(int(task["id"]))
        memory_service = build_isolated_memory_service(root)
        window = ChatWindow(
            growth_service=growth,
            memory_service=memory_service,
            chat_history_manager=ChatHistoryManager(root / "history"),
        )
        replies = []
        original_add_message = window.add_message

        def capture(sender, text, *args, **kwargs):
            replies.append((sender, str(text)))
            return original_add_message(sender, text, *args, **kwargs)

        window.add_message = capture
        window.input_box.setText("今日计划")
        window.send_message()

        roxy_reply = next(text for sender, text in reversed(replies) if sender == "Roxy")
        assert "验证桌面查询" in roxy_reply
        assert "[已完成]" in roxy_reply
        assert "目前还没有执行这项操作" not in roxy_reply
        window.close()
        app.processEvents()


def test_desktop_consumes_business_input_in_conversation_service_once():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        window = ChatWindow(
            growth_service=growth,
            memory_service=build_isolated_memory_service(root),
            chat_history_manager=ChatHistoryManager(root / "history"),
        )

        def legacy_handler(*_args, **_kwargs):
            raise AssertionError("legacy business handler must not run after ConversationService")

        window.handle_plan_command = legacy_handler
        window.handle_action_log_command = legacy_handler
        window.handle_growth_command = legacy_handler
        window.handle_memory_command = legacy_handler
        window.handle_dance_command = legacy_handler
        window.handle_natural_intent = legacy_handler

        window.input_box.setText("添加计划：只由会话服务写入")
        window.send_message()

        assert [item["title"] for item in growth.tasks()] == ["只由会话服务写入"]
        window.close()
        app.processEvents()


def test_desktop_renders_plan_query_variants_and_read_recheck_from_tool_results():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        growth = GrowthManager(root / "private")
        growth.plan_store.add_task("复习随机森林")
        window = ChatWindow(
            growth_service=growth,
            memory_service=build_isolated_memory_service(root),
            chat_history_manager=ChatHistoryManager(root / "history"),
        )
        replies = []
        original_add_message = window.add_message

        def capture(sender, text, *args, **kwargs):
            replies.append((sender, str(text)))
            return original_add_message(sender, text, *args, **kwargs)

        window.add_message = capture
        for text in ("今天还有什么计划", "我今天还有什么计划", "我今天还有什么没完成？"):
            window.input_box.setText(text)
            window.send_message()
            reply = next(item for sender, item in reversed(replies) if sender == "Roxy")
            assert "复习随机森林" in reply
            assert "目前还没有执行这项操作" not in reply

        growth.plan_store.add_task("补充桌面复查")
        window.input_box.setText("你确定吗？")
        window.send_message()
        reply = next(item for sender, item in reversed(replies) if sender == "Roxy")
        assert "补充桌面复查" in reply
        assert "目前还没有执行这项操作" not in reply
        state = window.conversation_service.interaction_coordinator.current(
            window.current_session_id
        )
        assert state.state != "candidates_listed"
        window.close()
        app.processEvents()


if __name__ == "__main__":
    test_desktop_send_message_lists_completed_plan_without_claim_guard_block()
    print("desktop V1.8 entry tests passed")
