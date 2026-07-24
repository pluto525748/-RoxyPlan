# DeepSeek Provider

## 定位

V1.7 将 DeepSeek 作为可选在线 Provider，Ollama 保留为离线降级。桌面端和 Local Web 都通过 `RoutedLLMClient` 与 `ConversationService` 使用同一条业务链，不在 UI 或 `server/` 复制 Agent、计划、记忆逻辑。

```text
ConversationService
  -> 固定命令 / 本地规则 -> AgentCore
  -> 普通或未识别表达 -> ModelRouter
     -> DeepSeekProvider
        -> 普通回复
        -> tool_calls -> ModelActionAdapter -> 本地安全工具链
     -> OllamaProvider（仅普通聊天降级）
```

## 集中配置

默认配置集中在 `modules/llm/settings.py`：

- `default_model`: `deepseek-v4-flash`
- `complex_model`: `deepseek-v4-pro`
- `deepseek_base_url`: `https://api.deepseek.com`
- `offline_model`: `qwen3:4b`
- `ollama_base_url`: `http://localhost:11434/v1`

模型名不会散落在业务 Manager 中。DeepSeek 官方模型名称发生变化时，应先修改设置或集中默认值，再运行 Provider 测试。参考：[DeepSeek API 文档](https://api-docs.deepseek.com/) 与 [更新记录](https://api-docs.deepseek.com/updates/)。

## Provider 契约

统一 Provider 支持：

- `chat(...)`
- `chat_with_tools(...)`
- `health_check()`
- `provider_name / model_name`
- `supports_tools / supports_thinking`

响应转换为 `ProviderResponse`，包含 provider、model、content、tool_calls、usage、finish_reason、latency_ms 和受控 error。`reasoning_content` 只允许在同一 Provider 的即时工具回环中按协议回传，不进入公开 `to_dict()`、用户界面或普通聊天历史。

## Tool Calling

```text
模型提出 tool_calls
  -> ToolRegistry.model_tool_schemas() 白名单
  -> 严格参数校验，拒绝额外参数
  -> SafetyPolicy
  -> ConfirmationManager
  -> ToolExecutor
  -> ToolResult + 原 tool_call_id
  -> 回传同一 DeepSeek 模型
  -> 最终回复经过 ActionClaimGuard
```

模型不能执行 Python 函数。高风险工具仍需确认；愿望式或不明确写入也会先确认。单轮最多两次工具回环、三次工具调用。写操作已执行后，后续模型请求失败只使用确定性 ToolResult 生成回复，不切换 Provider 重放。

## 错误与降级

- 未配置 Key：DeepSeek 状态为 `not_configured`，普通聊天使用 Ollama。
- 网络、超时或服务错误：返回受控错误；普通聊天可降级 Ollama。
- 401、402、429 与服务端错误使用不同错误码，不显示堆栈、Key 或绝对路径。
- tools 请求首轮失败：只允许 Ollama 给普通回答，不能执行写工具。
- 默认模型在工具执行前返回不可解析结构时，自动模式最多升级复杂模型一次。
- DeepSeek 与 Ollama 都不可用：返回明确失败，不让桌面或 Web 崩溃。

## 当前未包含

- 流式输出。
- 公网部署或云端账户。
- 自动费用估算；未提供价格配置时只显示 token。
- 自动运行真实收费 API 测试。
