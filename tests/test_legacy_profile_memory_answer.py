"""A verified legacy nickname has no formal-memory ID; do not call it empty."""
import json
from test_beta_online_failure_replay import _build_service, _provider_body

def test_verified_legacy_name_does_not_enter_empty_formal_model_prompt(tmp_path):
    calls=[]
    def transport(method,url,headers,payload,timeout):
        calls.append(payload)
        return _provider_body({'content':json.dumps({'answer':'我还没有记下你的称呼。','used_memory_ids':[]},ensure_ascii=False)})
    service=_build_service(tmp_path,transport)
    service.memory_manager.update_profile({'nickname':'小南'})
    before=service.memory_manager.memory_file.read_bytes()
    response=service.handle('我叫什么','legacy-name')
    result=response.tool_results[0]
    assert result.success and result.tool=='list_memories'
    assert result.data['memory_read']['facts'][0]['authority']=='legacy_profile'
    assert '小南' in response.message
    assert calls==[]  # existing typed fallback can render this ID-less fact
    assert service.memory_manager.memory_file.read_bytes()==before
    assert service.chat_history_manager.messages('legacy-name')[-1]['content']==response.message
