# RoxyPlan API 契约

> 当前阶段：V1.3.1 本地契约冻结
> 代码位置：`modules/contracts.py`
> 本文不代表服务器或 HTTP API 已经实现

## 1. 通用规则

- 所有契约只包含 JSON 基础类型。
- 每个对象包含 `schema_version`，当前为 `1.0`。
- 未知字段在反序列化时忽略，便于兼容未来新增字段。
- 缺失必要业务字段会明确失败，不构造不完整操作。
- Qt 对象、异常实例、函数、文件句柄、集合和非有限浮点数不能进入契约。
- 内部异常统一转换为 `ToolError`，不返回堆栈或本地绝对路径。

## 2. 核心契约

### IntentResult

表示意图识别结果，包含：

- `request_id`
- `intent`
- `confidence`
- `entities`
- `needs_confirmation`
- `source`
- `warnings`

`IntentRouter` 仍返回兼容字典，并继续附带旧调用使用的 `slots` 和 `reason`；契约核心字段由 `IntentResult` 生成。

### ToolResult 与 ToolError

`ToolResult` 包含：

- `tool_call_id`
- `tool`
- `success` 与 `status`
- `message`
- JSON 安全的 `data`
- 可为空的结构化 `ToolError`

`ToolError` 只公开错误码、简短安全消息、是否可重试和受控详情。现有代码仍可用 `result.error == "not_found"` 比较错误码。

### AgentResponse

`AgentResponse` 包含：

- `request_id` 与可选 `conversation_id`
- `status` 和展示消息
- `AgentStep` 列表
- `ToolResult` 列表
- 可选 `PendingConfirmation`
- `ClientAction` 列表

Agent 响应构造时会验证所有客户端动作，不允许未知动作进入序列化结果。

## 3. 辅助契约

- `AgentStep`：稳定 `step_id`、工具名、参数、依赖关系和状态。
- `ClientAction`：稳定 `action_id`、动作名、参数和可选 `expires_at`。
- `PendingConfirmation`：确认 ID、绑定工具和参数、创建/过期时间及摘要。

这些对象均提供 `to_dict()` 和 `from_dict()`。

## 4. 客户端动作白名单

公开动作仅包括：

| 动作 | 参数 |
| --- | --- |
| `nod` | 无 |
| `jump` | 无 |
| `show_bubble` | `text` 必填；`duration_ms` 可选且限制范围 |
| `play_dance` | 无 |
| `sleep` | 无 |
| `wake` | 无 |
| `scale` | 当前使用桌面端固定自然幅度 |

`modules/client_action_policy.py` 负责白名单、参数 schema 和过期检查。`AgentResponse` 在输出前校验一次，`DesktopPet.execute_client_action()` 在本机执行前再次校验。

明确禁止：Shell、文件读写、任意 Python 函数、任意 URL、动态 Qt 方法名和未注册动作。桌面端使用固定动作映射，不根据响应内容调用 `getattr`。

## 5. ID 关系

- 一次用户请求使用同一个 `request_id` 贯穿意图和 Agent 响应。
- 每次工具调用使用独立 `tool_call_id`。
- 每个计划步骤使用独立 `step_id`。
- 每个桌面客户端动作使用独立 `action_id`。
- 当前 ID 在本地生成；未来服务端可生成同格式的稳定字符串。

## 6. 兼容路径

为减少当前桌面端改动，原导入路径继续有效：

- `modules.agent_core.AgentResponse`
- `modules.agent_planner.AgentStep`
- `modules.tool_registry.ToolResult`
- `modules.confirmation_manager.PendingConfirmation`

它们实际引用 `modules/contracts.py` 中的统一类型。当前没有网络传输代码，契约测试只验证 JSON 往返和安全边界。
