# RoxyPlan Local Web V0.1

> 范围：同一可信局域网内的移动控制台
> 当前状态：V1.5 可日常试用原型
> 不包含：公网部署、账号、数据库、数据迁移或完整桌面功能迁移

## 1. 桌面端真实调用链

当前 `ChatWindow.send_message()` 的主要顺序是：

```text
用户输入
  -> 聊天历史/候选记忆/确认命令
  -> ConversationService
     -> IntentRouter
     -> AgentCore
     -> AgentPlanner
     -> SafetyPolicy
     -> ToolExecutor
     -> ToolRegistry
     -> GrowthManager / MemoryManager / 桌宠控制器
  -> 未被 Agent 处理时继续旧命令与人格规则
  -> 普通聊天 ConversationService.build_llm_messages()
  -> QThread + ChatReplyWorker
  -> RoutedLLMClient / 当前 Provider
  -> Qt 聊天记录渲染
```

桌面端仍承担 Qt 异步线程、消息渲染和桌宠动作；聊天历史、摘要、相关记忆、知识检索和普通模型上下文由共享的纯 Python 模块编排。

## 2. AgentCore 独立复用结论

以下模块导入后不会加载 PySide6，也不要求 `QApplication`：

- `IntentRouter`
- `AgentCore` / `AgentPlanner`
- `ToolExecutor` / `ToolRegistry`
- `SafetyPolicy` / `ConfirmationManager`
- `GrowthManager`
- `MemoryManager`
- `ChatHistoryManager`
- `ContextBuilder`
- `RoutedLLMClient` 与当前 Provider（保留旧 `LLMClient` 兼容）
- contracts 与 repositories

`AgentCore` 可以脱离桌面窗口运行。`create_roxy_tool_registry()` 的桌宠控制器参数可为空；此时桌宠动作工具不可用，但计划、成长和长期记忆工具仍可复用。

强依赖 PySide6 的部分包括：

- `frontend/pet_app.py` 的窗口、Signal、QThread 和渲染。
- `frontend/desktop_pet.py` 的透明窗口、菜单、定时器和屏幕位置。
- `frontend/pet_actions.py` 与 `pet_action_manager.py` 的 Qt 动画。
- settings、growth、memory、history 等 Dialog。

服务端不导入任何 frontend 模块。

## 3. Local Web 调用链

```text
浏览器
  -> POST /v1/agent/requests
  -> 本机/Token 访问校验
  -> AgentService
  -> ConversationService
     -> 待确认操作检查
     -> IntentRouter
     -> AgentCore
     -> ChatHistoryManager
     -> 普通 chat 意图
        -> roxy_personality.json
        -> MemoryRetriever
        -> KnowledgeManager
        -> ContextBuilder
        -> RoutedLLMClient
           -> DeepSeek 在线 Provider（可选）
           -> Ollama 本地降级
  -> AgentResponse JSON
```

`AgentService` 是组合适配层，不重新实现计划、记忆或 Agent 规则。服务启动和 `/health` 不初始化 Manager；第一次 Agent 请求才延迟创建默认服务。

## 4. V0.1 已接入能力

- 普通 LLM 聊天。
- 普通聊天读取现有 `data/roxy_personality.json` 人格配置。
- 普通聊天通过 `MemoryRetriever` 选择当前问题相关记忆，并由 `ContextBuilder` 组装有限上下文。
- 普通聊天通过共享 `KnowledgeManager` 读取现有 `.txt` / `.md`，只注入当前问题相关的有限片段。
- 通过稳定 `conversation_id` 保存和恢复最近消息；超出阈值时保存规则会话摘要。
- `show_memory` 等长期记忆查询继续由 AgentCore 确定性返回，不交给模型猜测。
- 今日计划添加、查看、完成和二次确认删除。
- 行动记录添加。
- 行动记录查看。
- 今日复盘、复盘保存和成长日志查看。
- 长期记忆查看、搜索、明确保存、归档、恢复和二次确认删除。
- Agent Core 的受限多步骤与短时确认。
- 手机优先的聊天、今日计划、行动记录、成长日志、长期记忆和会话记录页面。
- 确定性的计划列表、添加、完成和二次确认删除 API。
- 行动记录、今日复盘、复盘保存和成长日志 API。
- 只读长期记忆列表与搜索，响应仅保留页面所需字段。
- Web 会话列表、新建、恢复和修改显示标题；桌面会话不会被 Web 列表混入。
- Web、DeepSeek、Ollama、当前模型、路由模式和知识文件数量状态检查；Provider 离线不会拖垮主页面。

所有写操作仍调用现有 Manager 和 Local JSON Repository，数据格式没有复制或迁移。

## 5. 当前暂未接入

- 待确认记忆、记忆冲突管理面板。
- 主动提醒、桌宠气泡、dance、sleep/wake 和其他 Qt 动作。
- Web 端直接新增、编辑长期记忆。
- Web 会话删除和桌面会话管理。
- 人格规则中的桌面专属固定回复匹配。

这些能力仍正常保留在桌面端。后续扩展仍应复用纯 Python 模块，不应在 `server/` 复制桌面逻辑。

## 6. 安装与启动

当前项目虚拟环境使用 Python 3.8.8。它可以运行本次锁定的 FastAPI 版本，但 Python 3.8 已较旧；在更长期的 Web 工作前建议单独规划升级，不在 V0.1 中改变桌面环境。

安装 Local Web 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
```

仅在电脑本机测试时，可直接运行：

```powershell
.\run_roxy_web.bat
```

本机地址：`http://127.0.0.1:8000`

## 7. 手机局域网访问

手机访问前必须设置本地 Token。请在准备启动服务的同一个 CMD 窗口执行：

```bat
set ROXY_LOCAL_TOKEN=请换成你自己的较长随机字符串
run_roxy_web.bat
```

然后：

1. 使用 `ipconfig` 查看电脑当前局域网 IPv4。
2. 确认手机与电脑连接同一可信 Wi-Fi。
3. 如 Windows 首次弹出防火墙提示，由用户手动选择是否允许专用网络访问。
4. 手机打开 `http://电脑局域网IPv4:8000`。
5. 点击页面右上角访问设置，输入同一个 Token。

程序不会自动修改 Windows 防火墙，也不会把 Token 写入仓库。浏览器只在当前标签页的 `sessionStorage` 保存 Token。

## 8. 访问保护边界

- 未设置 `ROXY_LOCAL_TOKEN`：Agent API 仅接受回环地址请求。
- 已设置 Token：Agent API 要求 `X-Roxy-Token` 请求头完全匹配。
- `/health` 和静态登录页面不返回私人数据，因此允许加载。
- 不信任 `X-Forwarded-For`，直接使用连接来源地址。
- 服务关闭 OpenAPI 和交互式 API 文档入口。
- 删除等运行时确认不会持久化；服务重启后旧确认 ID 明确失效，页面也不会保存待确认状态。
- 记忆审计接口只返回操作元数据和正文长度，不返回完整记忆正文。

V0.1 使用局域网 HTTP，不提供 TLS。Token 会在可信局域网中传输，因此不能用于公共 Wi-Fi、端口映射或公网暴露。

## 9. 数据边界

- 没有修改或迁移任何 JSON schema。
- 没有数据库或云端存储。
- 测试使用 Fake Agent 或临时目录，不接触真实私人数据。
- 运行时的计划/记忆写操作仍由用户请求触发，并使用现有 Manager。
- 服务端响应不返回 API Key、Token、本机路径或原始异常信息。
- 动态页面内容使用文本节点渲染，不把用户输入拼成 HTML。
- 路由层不直接读写 JSON；计划、成长、记忆和会话继续通过现有 Manager、Repository 与 ToolExecutor。

## 10. 页面与 API

页面包含：聊天、今日计划、行动记录、成长日志、长期记忆和会话记录。浏览器在 `localStorage` 中保存当前 Web `conversation_id`，在 `sessionStorage` 中保存访问 Token；两者都不会写入公开网页源码。

主要 API：

- `GET /health`、`GET /v1/status`、`GET /v1/model-status`
- `GET /v1/model-usage`、`DELETE /v1/model-usage`
- `POST /v1/agent/requests`
- `GET/POST /v1/plans`、`POST /v1/plans/{id}/complete`、`DELETE /v1/plans/{id}`
- `GET/POST /v1/actions`
- `GET /v1/growth/review`、`POST /v1/growth/review/save`、`GET /v1/growth/logs`
- `GET /v1/memories?query=...`
- `GET /v1/memory-candidates` 与候选接受、拒绝、编辑接受、低价值批量拒绝接口
- `GET /v1/memory-conflicts`、`POST /v1/memory-conflicts/{id}/resolve`
- `GET /v1/memory-audit`
- `GET/POST /v1/conversations`、`GET/PATCH /v1/conversations/{conversation_id}`
- `POST /v1/confirmations/{confirmation_id}`

除 `/health` 和静态页面外，以上 `/v1/` 请求均使用相同的本机/Token 访问校验。
