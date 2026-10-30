"""Natural next-day chat receives actual growth data without write authority."""
from datetime import datetime
import json

import pytest

from test_learning_context_continuity import learning_service, facts_from, fingerprint


@pytest.mark.parametrize('text', [
    '新的一天干点什么呢',
    '我昨天睡得很晚今天状态好差',
    '想找点不费力的事让今天不至于完全空着',
    '我昨天任务也没有完成',
])
def test_natural_daily_chat_has_saved_review_and_real_yesterday_tasks(learning_service,text):
    service,transport=learning_service
    growth=service.growth_manager
    growth.add_task('核对电机转速例子',date='2026-10-28',duration_minutes=15)
    growth.add_record('整理相关矩阵卡片',date='2026-10-28',duration_minutes=8)
    growth.save_today_review('2026-10-28')
    session=service.chat_history_manager.ensure_daily_learning_session('2026-10-29')['session_id']
    before=fingerprint(service.project_root/'data/private')
    messages=service.conversation_service.build_llm_messages(text,session)
    facts=facts_from(messages)
    history=facts['learning_history']
    yesterday=next(d for d in history['recent_days'] if d['date']=='2026-10-28')
    assert yesterday['plans'][0]['title']=='核对电机转速例子'
    assert yesterday['actions'][0]['content']=='整理相关矩阵卡片'
    assert '核对电机转速例子' in yesterday['saved_review']['text']
    assert yesterday['saved_review']['status']=='current'
    assert facts['business_date']=='2026-10-29'
    assert fingerprint(service.project_root/'data/private')==before


def test_summary_and_manual_chat_do_not_inherit_daily_growth_baseline(learning_service):
    service,_=learning_service
    session=service.chat_history_manager.ensure_daily_learning_session('2026-10-29')['session_id']
    for args in [('普通会话',False),(session,True)]:
        messages=service.conversation_service.build_llm_messages('给猫起名字',args[0],summary_mode=args[1])
        assert not any('daily_growth_reference' in m['content'] for m in messages)


def test_daily_reference_stays_in_current_instance_and_uses_business_day(learning_service):
    service,_=learning_service
    from modules.growth_manager import GrowthManager
    other=GrowthManager(service.project_root.parent/(service.project_root.name+'-other')/'data/private')
    other.add_task('未授权的其它实例内容',date='2026-10-31')
    other.save_today_review('2026-10-31')
    service.conversation_service.business_now_provider=lambda:datetime(2026,11,1,10)
    service.growth_manager.add_task('该实例昨天的独有任务',date='2026-10-31')
    service.growth_manager.save_today_review('2026-10-31')
    session=service.chat_history_manager.ensure_daily_learning_session('2026-11-01')['session_id']
    messages=service.conversation_service.build_llm_messages('今天状态不太好',session)
    facts=facts_from(messages)
    assert facts['business_date']=='2026-11-01'
    assert '该实例昨天的独有任务' in json.dumps(facts,ensure_ascii=False)
    assert '未授权的其它实例内容' not in json.dumps(facts,ensure_ascii=False)


def test_natural_daily_reply_sends_growth_reference_through_real_provider_adapter(learning_service):
    service,transport=learning_service
    growth=service.growth_manager
    growth.add_task('对照电机例子',date='2026-10-28')
    growth.save_today_review('2026-10-28')
    session=service.chat_history_manager.ensure_daily_learning_session('2026-10-29')['session_id']
    before={p.name:p.read_bytes() for p in growth.private_dir.glob('*.json')
            if p.name in {'today_plan.json','action_log.json','growth_log.json'}}
    transport.reply='昨天的电机练习还没完成，今天可以只看一个小例子，不急着补量。'
    response=service.handle('我昨天任务也没有完成',session)
    assert not response.tool_results
    requests=[r for r in transport.requests if any(m['content'].startswith('以下是本次请求绑定业务日期') for m in r['messages'])]
    assert requests
    facts=facts_from(requests[-1]['messages'])
    assert facts['learning_history']['recent_days'][0]['plans'][0]['title']=='对照电机例子'
    assert all((growth.private_dir/n).read_bytes()==v for n,v in before.items())
