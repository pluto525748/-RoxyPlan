"""A historical evidence date must not erase today's displayed candidates."""
from datetime import datetime
import json
import pytest
from v18_test_support import build_service
from test_v22_typed_memory_and_suggestions import SemanticChatLLM
from modules.llm.deepseek_provider import DeepSeekProvider
from modules.llm.routed_client import RoutedLLMClient

class AdviceLLM(SemanticChatLLM):
    def chat(self, messages):
        result = super().chat(messages)
        if messages and "You classify one Chinese user message" in messages[0].get("content", ""):
            decision = json.loads(result)
            # Match the actual pilot's captured semantic response; the base
            # helper defaults to discussion rather than an advice request.
            decision["request_mode"] = "advice"
            return json.dumps(decision, ensure_ascii=False)
        return result

def service_with_transport(root, wrong_read=False):
    stub = AdviceLLM("1. 阅读文档，15分钟\n2. 写一段总结，10分钟")
    captured=[]
    def transport(method, url, headers, payload, timeout):
        captured.append(payload['messages'])
        content = stub.chat(payload["messages"])
        if wrong_read and "You classify one Chinese user message" in payload['messages'][0].get('content',''):
            decision=json.loads(content)
            decision.update(mode='read',intent='show_plan',proposed_tool='show_plan',request_mode='query',entities={'date':'today'})
            content=json.dumps(decision,ensure_ascii=False)
        return {"model": "offline-recorded-shape", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
    provider = DeepSeekProvider(api_key="synthetic-test-key", base_url="https://api.deepseek.com", model_name="deepseek-v4-flash", transport=transport)
    client = RoutedLLMClient(root, settings={"online_model_enabled": True, "model_mode": "economy",
            "offline_fallback_enabled": False, "enable_auto_escalation": False}, legacy_config={}, deepseek_provider=provider)
    result=build_service(root, client)
    result[0]._captured_advice_requests=captured
    return result

REQUESTS = [
    "今天想练习Python作用域。请参考已保存目标、昨日复盘和真实执行，给两条今天可做的具体建议，先别加入计划。",
    "昨天的执行留在复盘里了。今天的主题是文件读写，结合实际完成和用时给我两个可选行动；只展示，等我选。",
    "帮我为今天的算法列两个可执行候选。请检查已有目标、昨日完成情况和计划外行动，不把未记录的用时编出来。",
    "我今天打算继续阅读。按真实学习记录建议两件小事，遵守我的偏好；没有历史就直说，不直接保存。",
    "今天练排序该怎样起步？结合昨天的事实给两个编号建议，计划与实际时长分开，暂时不写计划。",
    "先帮我想两个今天练习课程的任务。看看昨日复盘与真实执行，再解释建议依据，我随后选择。",
]

@pytest.mark.parametrize("user_text", REQUESTS)
def test_today_suggestions_survive_retrospective_context(tmp_path, user_text):
    service, growth, _, _ = service_with_transport(tmp_path)
    response = service.handle(user_text, "date-context")
    snapshot = service.interaction_coordinator.current("date-context").last_suggestion_snapshot
    assert not response.tool_results
    assert growth.tasks() == []
    assert len(snapshot.get("objects", [])) == 2
    assert snapshot["target_date"] == datetime.now().date().isoformat()

@pytest.mark.parametrize('user_text', REQUESTS)
def test_explicit_candidates_receive_planning_facts_even_if_model_proposes_read(tmp_path,user_text):
    service, growth, _, _=service_with_transport(tmp_path,wrong_read=True)
    response=service.handle(user_text,'misread-advice')
    assert not response.tool_results
    snapshot=service.interaction_coordinator.current('misread-advice').last_suggestion_snapshot
    assert len(snapshot.get('objects',[]))==2
    sent=json.dumps(service._captured_advice_requests,ensure_ascii=False)
    assert 'last_7_days' in sent and 'last_30_days' in sent
    assert growth.tasks()==[]

@pytest.mark.parametrize("user_text", [
    "参考昨天的复盘，请给明天两条学习建议。",
    "今天先聊一下，帮我安排明天的学习建议。",
    "给我后天的学习任务建议，不要加入计划。",
])
def test_real_other_day_suggestions_never_become_today_snapshot(tmp_path, user_text):
    service, growth, _, _ = service_with_transport(tmp_path)
    service.handle(user_text, "other-day")
    assert service.interaction_coordinator.current("other-day").last_suggestion_snapshot == {}
    assert growth.tasks() == []

def test_bare_learning_wish_keeps_existing_local_choice_policy(tmp_path):
    service, growth, _, _ = service_with_transport(tmp_path)
    response = service.handle("今天想学习Python。", "bare-wish")
    assert response.status == "clarification"
    assert not response.tool_results
    assert growth.tasks() == []
    assert service.interaction_coordinator.current("bare-wish").last_suggestion_snapshot == {}

def test_plain_read_keeps_local_read_guard(tmp_path):
    service, growth, _, _ = service_with_transport(tmp_path)
    growth.add_task("阅读文档")
    response = service.handle("查看今天计划", "read-control")
    assert [r.tool for r in response.tool_results] == ["show_plan"]
    assert len(growth.tasks()) == 1
