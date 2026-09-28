import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from modules.intent_router import IntentRouter
from modules.semantic_action_parser import SemanticActionParser
from tests.v18_test_support import NoCallLLM, RecordingLLM, build_service


def _tools(response):
    return [item.tool for item in response.tool_results]


def test_v19_safe_interaction_matrix_uses_shared_business_semantics():
    parser = SemanticActionParser(IntentRouter(enable_llm=False))
    assert [item.tool_name for item in parser.parse("跳舞", allow_llm=False).candidates] == ["play_dance"]
    assert parser.parse("我喜欢你跳舞", allow_llm=False).candidates == []

    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())

        remembered = service.handle("记住我喜欢你跳舞", "matrix-a")
        assert _tools(remembered) == ["save_formal_memory"]
        assert "play_dance" not in _tools(remembered)

        undone = service.handle("撤销刚才保存", "matrix-a")
        assert _tools(undone) == ["archive_memory"]
        assert service.handle("撤销刚才保存", "matrix-b").status == "clarification"

        ambiguous = service.handle(
            "把你说的这些先添加进今天的计划，然后把我说的长期目标放进长期记忆",
            "matrix-ambiguous",
        )
        assert ambiguous.status == "clarification"
        assert ambiguous.tool_results == []
        assert growth.tasks() == []

        plan = service.handle("添加计划：复习随机森林", "matrix-plan")
        assert _tools(plan) == ["add_plan"]
        assert [item["title"] for item in growth.tasks()] == ["复习随机森林"]


def test_ordinary_statement_and_local_web_entry_share_non_write_result():
    with tempfile.TemporaryDirectory() as temp:
        desktop, desktop_growth, _desktop_memory, _desktop_history = build_service(
            Path(temp) / "desktop", RecordingLLM("普通聊天")
        )
        local_web, web_growth, _web_memory, _web_history = build_service(
            Path(temp) / "web", RecordingLLM("普通聊天")
        )

        desktop_result = desktop.handle("我喜欢喝冰可乐", "same-text")
        web_result = local_web.handle("我喜欢喝冰可乐", "same-text")

        assert desktop_result.status == web_result.status == "chat"
        assert _tools(desktop_result) == _tools(web_result) == []
        assert desktop_growth.tasks() == web_growth.tasks() == []
        assert desktop.memory_service.list_memories().data["memories"] == []
        assert local_web.memory_service.list_memories().data["memories"] == []
