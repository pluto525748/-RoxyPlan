# Agent Tool Call Protocol

RoxyPlan 保留自研 Agent 架构，并借鉴 Pydantic AI 的 schema 校验、错误回传与预算思想，以及 LangGraph 的显式状态和工具回环思想。当前没有引入这些框架，原因是项目仍运行于 Python 3.8，且现有 Manager、Repository、PySide6 和 Local Web 已共享稳定业务边界。

## 完整调用链

```text
用户消息
  -> 固定命令 / 本地规则
  -> ModelActionAdapter（必要时）
  -> ProposedAction
  -> ToolRegistry 白名单与 JSON Schema 校验
  -> SafetyPolicy / ConfirmationManager
  -> AgentCore / ToolExecutor
  -> ToolResult
  -> ToolMessage（最小安全摘要）
  -> 同一 Provider 再次生成最终回复
  -> ActionClaimGuard 事实声明复核
```

模型只能提出工具名和 JSON 参数。它不能获得 Python handler、Manager、文件路径或任意函数对象，也不能绕过本地执行器。

## Provider 策略

- DeepSeek：当前在线主路，优先使用 OpenAI-compatible 原生 `tool_calls`。
- JSON fallback：原生工具协议不可用或响应格式错误时，由在线 Provider 输出受限 JSON；写操作默认要求确认。
- Ollama `qwen3:4b`：当前仅作为普通聊天降级，不默认驱动模糊或高风险写操作。

2026-07-22 的本机实验中，`qwen3:4b` 13 个中文场景精确工具匹配 8 个（61.5%），没有乱造工具和非法参数，但自然完成、指代更新、查看、删除和多工具场景不稳定。诊断数据见 `docs/research/ollama_tool_calling_experiment_2026_07_22.json`。

## 内部消息

- `ProposedAction`：不可信的模型动作提案，包含 `call_id`、工具名、参数、来源、置信度、澄清标记和警告。
- `AssistantToolCall`：Provider-neutral 的工具调用记录。
- `ToolResult`：本地业务执行事实。
- `ToolMessage`：与原 `call_id` 匹配的最小结果摘要，不包含堆栈、Token、绝对路径或完整敏感数据。

工具失败时优先由 AgentCore 使用确定性文案结束，不让模型把失败润色成成功。只有当前轮存在匹配的成功 `ToolResult`，最终回复才能声明“已添加、已保存、已删除、已完成”等系统状态。

## 确认语义

`PendingConfirmation` 同时保留旧字段和 V1.6 规范别名：

- `tool_name`
- `immutable_arguments`
- `safe_summary`
- `conversation_id`
- `call_id`
- `risk_level`
- `state_fingerprint`

确认时只使用服务端保存的原参数，不接受模型替换参数；目标摘要发生变化时，旧确认失效。确认保存在进程内，普通消息可打断，程序重启后不会恢复。

## Python 兼容

当前实现只使用标准库、dataclass 和项目已有依赖，兼容 Python 3.8。将来升级到 Python 3.10+ 后，可评估用 Pydantic v2 或 Pydantic AI 替换部分契约解析，但 Manager、ToolRegistry、SafetyPolicy 与 ToolExecutor 的本地安全边界仍应保留。
