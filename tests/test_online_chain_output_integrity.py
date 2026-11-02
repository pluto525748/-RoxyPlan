"""Recorded transport shapes must preserve final history and saved ToolResult."""
import json
from test_beta_online_failure_replay import _build_service, _provider_body

def test_saved_review_polish_does_not_mutate_persisted_tool_fact(tmp_path):
    calls=[]
    def transport(method, url, headers, payload, timeout):
        calls.append(payload)
        return _provider_body({"content": "今天完成了阅读，做得不错，可以安心休息。"})
    service=_build_service(tmp_path,transport)
    task=service.growth_manager.add_task("阅读")
    service.growth_manager.complete_by_id(task["id"])
    response=service.handle("今日复盘", "review-integrity")
    assert [t.tool for t in response.tool_results] == ["save_daily_review"]
    data=response.tool_results[0].data
    stored=json.loads(service.growth_manager.growth_file.read_text(encoding='utf-8'))['entries'][task['date']]
    assert data['postcondition_verified']
    assert data['entry'] == stored
    assert "今天完成了阅读" in response.message
    assert len(calls)==1
    assert service.chat_history_manager.messages('review-integrity')[-1]['content']==response.message

def test_memory_polish_is_not_retried_after_history_was_recorded(tmp_path):
    calls=[]
    def transport(method,url,headers,payload,timeout):
        calls.append(payload)
        answer={"answer":"我记得你喜欢拼图。", "used_memory_ids":[999 if len(calls)<=2 else 1]}
        return _provider_body({"content":json.dumps(answer,ensure_ascii=False)})
    service=_build_service(tmp_path,transport)
    service.memory_manager.add_memory("我喜欢拼图",category="preference")
    response=service.handle("你记得我什么", "memory-integrity")
    history=service.chat_history_manager.messages("memory-integrity")
    assert len(calls)==2  # one attempt + existing bounded repair, no complete() replay
    assert history[-1]['content']==response.message
    assert [m['role'] for m in history]==['user','assistant']

def test_multiday_recurring_request_cannot_be_silently_added_today(tmp_path):
    captured={"mode":"write","intent":"add_plan","entities":{"title":"学一小时","duration_minutes":60,"note":"连续30天，每天学一小时"},
        "proposed_tool":"add_plan","confidence":0.78,"follow_up_target":"","needs_confirmation":False,"warnings":[],
        "clarification_question":None,"candidate_actions":[],"subject":"self","polarity":"positive","modality":"commitment",
        "request_mode":"execute","explicit_command":True}
    def transport(method,url,headers,payload,timeout):
        return _provider_body({'content':json.dumps(captured,ensure_ascii=False)})
    service=_build_service(tmp_path,transport)
    response=service.handle('这30天我每天都要学一小时，帮我记下来。','recurrence')
    assert not response.tool_results
    assert service.growth_manager.tasks()==[]
    assert '今天' in response.message

def test_recurring_scope_preserves_explicit_today_item_and_quoted_title():
    from datetime import datetime
    from modules.request_boundary import RequestBoundary
    today=datetime.now().date().isoformat()
    for text in ['把每天阅读加入今天计划。','今天加一项计划：每天背单词。','把“每天进步一点”加入今天计划。']:
        assert RequestBoundary('scope',today,text,datetime.now).plan_add_date_allowed()
    for text in ['这30天每天学习一小时。','每周整理笔记，帮我记到计划。','未来两个月每晚练习口语。']:
        assert not RequestBoundary('scope',today,text,datetime.now).plan_add_date_allowed()
