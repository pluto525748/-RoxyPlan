# V1.6 Tool Calling 手动验收

本清单用于验收 V1.6 协议在当前 V1.7 DeepSeek 路由下的表现，不会自动调用收费 API。

## 自动测试

```powershell
.\.venv\Scripts\python.exe tests\test_model_action_adapter.py
.\.venv\Scripts\python.exe tests\test_tool_schema_exporter.py
.\.venv\Scripts\python.exe tests\test_tool_call_loop.py
.\.venv\Scripts\python.exe tests\test_action_claim_guard.py
.\.venv\Scripts\python.exe tests\test_reference_resolver.py
.\.venv\Scripts\python.exe tests\test_chinese_entity_parsing.py
.\.venv\Scripts\python.exe tests\test_ollama_tool_calling_contract.py
.\.venv\Scripts\python.exe tests\test_agent_reliability_v16.py
```

真实 Ollama 诊断是可选集成实验，不执行业务工具：

```powershell
.\.venv\Scripts\python.exe tools\experiment_ollama_tool_calling.py --model qwen3:4b --output docs\research\ollama_tool_calling_experiment_local.json
```

## 桌面端验收

1. 输入“把学习机器学习 50 分钟加入今天计划”，确认控制台出现工具选择与成功结果，查看计划能看到一条真实记录。
2. 输入“我下午想学一会儿”，应询问主题或是否加入计划，不能直接声称已添加。
3. 添加计划后输入“刚才那个改成 50 分钟”，应修改刚才的结构化任务，不能把 `50` 当任务编号。
4. 没有当前目标时输入“把这个加入计划”，应澄清“这个”是什么。
5. 输入“删除刚才那个”，必须显示二次确认；先修改目标内容再确认时，旧确认应失效。
6. 输入普通咨询“下午可以学点什么”，不能写入计划。
7. 模拟 Provider 离线后执行查看计划，确定性本地工具仍可用；普通聊天显示可理解的降级信息。

## 事实声明验收

1. 让 Fake / 调试 Provider 返回“我已经帮你添加计划了”但不返回 tool call，应被改写为未执行说明。
2. 工具返回失败时，最终回复不得声称成功。
3. 用户说“我刚刚完成作业了”时，Roxy 可以正常肯定用户进度，不应被误判为系统工具声明。

## 验收日志

可关注：

```text
[Tool] selected: add_plan
[Tool] execute: add_plan
[Tool] success: add_plan
[Tool] duplicate call skipped: ...
[ActionClaimGuard] blocked unverified completion claim
```

日志不得出现 API Key、完整私人记忆、绝对路径或内部堆栈。
