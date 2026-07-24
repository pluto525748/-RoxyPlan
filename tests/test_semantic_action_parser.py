import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.intent_router import IntentRouter
from modules.semantic_action_parser import SemanticActionParser


class ProposalAdapter:
    @staticmethod
    def from_native_response(_response):
        return [
            SimpleNamespace(
                kind="tool",
                tool_name="add_plan",
                arguments={"title": "学习算法", "duration_minutes": 40},
                confidence=0.9,
                needs_clarification=False,
                warnings=[],
                proposal_id="p1",
            )
        ]

    @staticmethod
    def from_json_text(_text, provider=""):
        del provider
        return ProposalAdapter.from_native_response(None)


def test_explicit_composed_plan_and_vague_wish():
    parser = SemanticActionParser(IntentRouter())
    explicit = parser.parse("把整理测试文档加入今天计划")
    assert explicit.candidates[0].tool_name == "add_plan"
    assert explicit.candidates[0].arguments["title"] == "整理测试文档"
    assert explicit.candidates[0].explicit_command is True

    vague = parser.parse("我下午想学一会儿")
    assert vague.candidates[0].tool_name == "add_plan"
    assert vague.candidates[0].explicit_command is False
    assert vague.needs_clarification is True
    assert vague.candidates[0].ambiguities


def test_progress_chat_negation_and_multi_action():
    parser = SemanticActionParser(IntentRouter())
    progress = parser.parse("我已经理解随机森林的两个随机性来源")
    assert progress.candidates[0].tool_name == "add_action_log"
    assert progress.candidates[0].requires_confirmation_hint is True

    assert parser.parse("我不想跳舞").candidates == []
    assert parser.parse("什么是随机森林").candidates == []

    multi = parser.parse("机器学习学完了，然后记录今天完成了模型测试")
    assert [item.tool_name for item in multi.candidates] == [
        "complete_plan",
        "add_action_log",
    ]


def test_native_and_json_proposals_share_action_candidate_contract():
    parser = SemanticActionParser(
        IntentRouter(), proposal_adapter=ProposalAdapter()
    )
    native = parser.parse_native_response(
        SimpleNamespace(provider="deepseek", model="deepseek-chat")
    )
    fallback = parser.parse_json_text("{}", provider="ollama", model="qwen")
    assert native.candidates[0].tool_name == fallback.candidates[0].tool_name
    assert native.candidates[0].arguments == fallback.candidates[0].arguments
    assert native.source == "native_tool_call"
    assert fallback.source == "json_fallback"


if __name__ == "__main__":
    test_explicit_composed_plan_and_vague_wish()
    test_progress_chat_negation_and_multi_action()
    test_native_and_json_proposals_share_action_candidate_contract()
    print("semantic action parser tests passed")
