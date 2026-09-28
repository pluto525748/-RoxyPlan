# ClientAction 协议（V1.8.2）

## 原则

ClientAction 是 Agent 向本地桌面客户端提出的声明式动作，不是 Python 函数调用。模型能力已提升，但模型输出仍是不可信提议；只有本地白名单、参数校验、过期校验和 Dispatcher 接受后才能执行。

## ClientAction

字段：

- `schema_version`：当前契约版本。
- `action_id`：每次动作唯一 ID；新的用户请求必须生成新值。
- `request_id`：所属 Agent 请求。
- `conversation_id`：所属会话。
- `name`：白名单动作名。
- `arguments`：JSON-safe 参数，禁止函数、Qt 对象、路径注入和任意模块名。
- `created_at` / `expires_at`：创建与过期时间。
- `idempotency_key`：短窗口网络重放去重键。
- `source`：`tool_registry`、`desktop_menu` 或兼容入口等安全来源标记。
- `status`：初始为 `requested`。

白名单：`play_dance`、`jump`、`shake`、`scale`、`sleep`、`wake`、`nod`、`show_bubble`。

示例：

```json
{
  "schema_version": "1.0",
  "action_id": "action_<uuid>",
  "request_id": "req_<uuid>",
  "conversation_id": "local_default",
  "name": "play_dance",
  "arguments": {"dance_id": null},
  "created_at": "2026-07-28T08:00:00+00:00",
  "expires_at": "2026-07-28T08:00:30+00:00",
  "idempotency_key": "idem_<uuid>",
  "source": "tool_registry",
  "status": "requested"
}
```

## ClientActionResult

状态集合：

- `requested`
- `accepted`
- `running`
- `completed`
- `rejected`
- `skipped_busy`
- `skipped_duplicate`
- `expired`
- `failed`
- `cancelled`

结果同时记录 `accepted`、`started`、`completed`、`reason_code`、安全展示文案、开始/完成时间和最小错误类型。诊断不得记录 API Key、reasoning_content 或完整私人消息。

## 派发和去重

1. UI 收到 AgentResponse。
2. Dispatcher 校验协议、白名单、参数和 expires_at。
3. 30 秒窗口内，同一 `action_id` 或 `idempotency_key` 只消费一次。
4. 不同 ID 的同名动作可以再次执行。
5. PetActionManager busy 时返回 `skipped_busy`；V1.8.2 不实现队列。
6. Dispatcher 捕获异常，不把未处理异常抛到 UI 事件循环。
7. ClaimGuard 根据实际接受结果修正可见回复。

## 禁止项

- Shell/CMD/PowerShell。
- 任意 Python 函数名或模块导入。
- 任意本地路径、图片路径或 URL。
- Qt 对象、回调对象或非 JSON 数据。
- 服务端直接操作桌面窗口。
- 仅依据回复文字反向猜测动作。
