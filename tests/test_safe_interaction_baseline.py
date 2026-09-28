import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.intent_router import IntentRouter
from modules.local_feature_extractor import LocalFeatureExtractor
from modules.semantic_action_parser import SemanticActionParser
from v18_test_support import NoCallLLM, build_service


@pytest.mark.parametrize(
    ("text", "tool", "payload", "polarity"),
    [
        ("跳舞", "play_dance", "", "command"),
        ("开始跳舞", "play_dance", "", "command"),
        ("我喜欢你跳舞", "", "", "statement"),
        ("你跳舞真好看", "", "", "statement"),
        ("我不想跳舞", "", "", "negated"),
        ("你会跳舞吗", "", "", "question"),
        ("记住我喜欢你跳舞", "save_formal_memory", "我喜欢你跳舞", "command"),
        ("保存我喜欢你跳舞的记忆", "save_formal_memory", "我喜欢你跳舞", "command"),
        ("把跳舞加入今天计划", "add_plan", "跳舞", "command"),
        ("记录跳舞", "add_action_log", "跳舞", "command"),
    ],
)
def test_command_payload_envelope_routes_only_the_command(text, tool, payload, polarity):
    parser = SemanticActionParser(IntentRouter(enable_llm=False))
    parsed = parser.parse(text, allow_llm=False)

    assert parsed.local_features.polarity == polarity
    assert parsed.local_features.payload_text == payload
    assert [item.tool_name for item in parsed.candidates] == ([tool] if tool else [])
    assert all(item.tool_name != "play_dance" for item in parsed.candidates if payload)
    if payload:
        assert parsed.local_features.command_text
        assert parsed.local_features.command_span
        assert parsed.local_features.protected_payload_span is not None
        assert parsed.clauses[0]["original_text"] == text
        assert parsed.clauses[0]["parse_status"] == "parsed"


def test_repeat_dance_requires_a_real_previous_dance_result():
    parser = SemanticActionParser(IntentRouter(enable_llm=False))
    no_context = parser.parse("再跳一次", allow_llm=False)
    after_dance = parser.parse(
        "再跳一次",
        {"last_tool_result": {"tool": "play_dance", "success": True}},
        allow_llm=False,
    )
    assert no_context.candidates == []
    assert [item.tool_name for item in after_dance.candidates] == ["play_dance"]


@pytest.mark.parametrize(
    "text",
    (
        "这些加入今天计划",
        "把这些加进计划",
        "刚才那些安排一下",
        "你上面说的加入今天计划",
    ),
)
def test_unresolved_content_reference_never_becomes_a_plan_title(text):
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle(text, "reference_missing")

        assert response.status == "clarification"
        assert growth.tasks() == []
        assert not response.tool_results
        assert "没有修改计划" in response.message


def test_unique_structured_plan_reference_resolves_but_multiple_requires_selection():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("把复习随机森林加入今天计划", "unique_reference")
        unique = service.handle("把那个改成一小时", "unique_reference")
        assert unique.status == "completed"
        assert int(growth.tasks()[0]["duration_minutes"]) == 60

        service.handle("把学习线性代数加入今天计划", "multiple_reference")
        service.handle("把复习概率论加入今天计划", "multiple_reference")
        before = [dict(item) for item in growth.tasks()]
        multiple = service.handle("把那个改成一小时", "multiple_reference")
        assert multiple.status == "clarification"
        assert growth.tasks() == before


def test_compound_write_with_one_unresolved_clause_executes_no_write():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        response = service.handle(
            "把复习随机森林加入今天计划，然后把这些加入今天计划",
            "atomic_write",
        )

        assert response.status == "clarification"
        assert response.tool_results == []
        assert growth.tasks() == []
        assert "没有执行其中任何一项" in response.message


def test_local_feature_extractor_preserves_clause_level_command_payload_metadata():
    features = LocalFeatureExtractor().extract("保存我喜欢你跳舞的记忆")
    data = features.to_dict()
    assert data["command_text"] == "保存记忆"
    assert data["payload_text"] == "我喜欢你跳舞"
    assert data["command_span"]
    assert data["protected_payload_span"]
    assert data["polarity"] == "command"
    assert data["clause_parse_status"] == "parsed"
