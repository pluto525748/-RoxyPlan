import sys
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.intent_router import IntentRouter, LLMIntentParser
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


def test_model_multi_action_keeps_all_three_explicit_tasks():
    def chat_decision(_messages):
        return json.dumps(
            {
                "mode": "write",
                "intent": "multi_action",
                "entities": {},
                "proposed_tool": None,
                "confidence": 0.98,
                "follow_up_target": None,
                "needs_confirmation": False,
                "warnings": [],
                "clarification_question": None,
                "candidate_actions": [
                    {"intent": "add_plan", "entities": {"title": "查车票"}},
                    {"intent": "add_plan", "entities": {"title": "列行李清单"}},
                    {"intent": "add_plan", "entities": {"title": "确定返程日期"}},
                ],
                "subject": "self",
                "polarity": "positive",
                "modality": "commitment",
                "request_mode": "execute",
                "explicit_command": True,
            },
            ensure_ascii=False,
        )

    parser = SemanticActionParser(
        IntentRouter(LLMIntentParser(chat_decision), enable_llm=True),
    )

    parsed = parser.parse_unified(
        "1. 查车票\n2. 列行李清单\n3. 确定返程日期，把这些加入今日计划",
        allow_llm=True,
    )

    assert [item.arguments["title"] for item in parsed.candidates] == [
        "查车票",
        "列行李清单",
        "确定返程日期",
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


def test_generic_conversational_wrappers_do_not_change_core_semantics():
    parser = SemanticActionParser(IntentRouter(enable_llm=False))
    queries = (
        "请今日计划可以吗",
        "麻烦你看看今天的计划谢谢",
        "洛琪希，能不能帮我查看行动记录一下",
    )
    parsed = [parser.parse(text, allow_llm=False) for text in queries]
    assert [item.request_mode for item in parsed] == ["query", "query", "query"]
    assert [item.candidates[0].tool_name for item in parsed] == [
        "show_plan",
        "show_plan",
        "show_action_log",
    ]

    negative = parser.parse("麻烦你不要跳舞可以吗", allow_llm=False)
    assert negative.candidates == []
    assert negative.request_mode == "cancellation"

    advice = parser.parse("cosplay该怎么入门", allow_llm=False)
    assert advice.request_mode == "advice"
    assert advice.candidates == []


def test_unified_parser_recovers_only_safe_bare_wishes_from_model_chat():
    def chat_decision(_messages):
        return (
            '{"mode":"chat","intent":"chat","entities":{},'
            '"proposed_tool":null,"confidence":0.9,'
            '"follow_up_target":null,"needs_confirmation":false,'
            '"warnings":[],"clarification_question":null,'
            '"candidate_actions":[],"subject":"self",'
            '"polarity":"positive","modality":"desire"}'
        )

    parser = SemanticActionParser(
        IntentRouter(LLMIntentParser(chat_decision), enable_llm=True)
    )

    wish = parser.parse_unified("我想学 cosplay", allow_llm=True)
    future_intention = parser.parse_unified("我准备晚上学习", allow_llm=True)
    question = parser.parse_unified("我想学 cosplay 是什么意思？", allow_llm=True)
    hypothetical = parser.parse_unified(
        "如果以后有空，也许想学3D建模",
        allow_llm=True,
    )

    assert wish.source == "local_wish_guard"
    assert wish.request_mode == "possible_action"
    assert wish.candidates[0].tool_name == "add_plan"
    assert wish.candidates[0].explicit_command is False
    assert future_intention.source == "llm"
    assert future_intention.candidates == []
    assert question.candidates == []
    assert hypothetical.candidates == []


if __name__ == "__main__":
    test_explicit_composed_plan_and_vague_wish()
    test_progress_chat_negation_and_multi_action()
    test_native_and_json_proposals_share_action_candidate_contract()
    print("semantic action parser tests passed")
