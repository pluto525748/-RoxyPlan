# RoxyPlan 云端迁移准备评审

> 文档性质：架构评审与接口草案
> 评审范围：当前桌面端核心模块
> 本次不包含：云端部署、Web 服务实现、数据库实现、账号系统或现有代码迁移

## 1. 结论

RoxyPlan 适合采用“云端 Agent 核心 + 本地桌宠客户端”的渐进式架构，而不是把整个桌面程序搬到云端。

- 云端未来可承载：意图理解、Agent 编排、安全策略、成长数据、长期记忆、聊天历史、上下文构建和模型调用。
- 桌面端应继续承载：PySide6 界面、透明桌宠窗口、鼠标交互、气泡、动画、舞蹈帧播放和本机设置。
- 云端不能直接操作桌宠。Agent 只能返回受限的 `client_actions`，由桌面端按本地白名单校验后执行。
- 当前纯 Python 逻辑已有较好的复用基础，但部分 Manager 混合了业务规则和本地 JSON 读写。正式迁移前，应先定义存储接口，并保留 JSON 作为本地适配器。
- 不建议一次性迁移全部数据。先稳定 API 契约，再逐项迁移聊天、成长和记忆数据，桌宠在离线状态下仍应具备基本可用性。

### V1.3.1 前置进度

本评审提出的前三项本地前置工作已在 V1.3.1 落地：

- 核心对象已集中到 `modules/contracts.py`，并通过 JSON 往返测试冻结基础字段。
- Growth、Memory、MemoryCandidate 和 ChatHistory 已通过 Repository 接口访问当前 Local JSON。
- Agent 响应与桌面端共用客户端动作白名单，并在本机执行前再次检查参数和过期时间。

这不代表云端迁移已经开始。当前没有 HTTP 客户端、服务器、数据库适配器、账号系统或数据上传逻辑。

## 2. 当前模块边界

### 2.1 可直接迁移或复用的纯 Python 逻辑

| 模块 | 可复用内容 | 迁移注意事项 |
| --- | --- | --- |
| `modules/intent_router.py` | 固定命令、规则语义、结构化意图结果 | LLM 解析器应通过接口注入，避免绑定具体模型 |
| `modules/agent_core.py` | Agent 主流程、步骤协调、统一响应 | 不应直接引用 Qt 对象或本地窗口 |
| `modules/agent_planner.py` | 意图到步骤的规划、步骤数量限制 | 规划结果必须可序列化和可审计 |
| `modules/tool_executor.py` | 参数校验、安全检查、执行结果包装 | 云端执行工具与本地客户端动作必须分开 |
| `modules/safety_policy.py` | 风险分级、确认要求、危险操作阻断 | 确认状态未来需要服务端共享存储 |
| `modules/confirmation_manager.py` | 待确认操作、过期时间、确认/取消语义 | 当前运行时状态将来需持久化或集中管理 |
| `modules/context_builder.py` | 人格、记忆、摘要、近期消息和知识片段的组装顺序 | 输入应改为领域接口返回的数据，不直接读文件 |
| `modules/memory_retriever.py` | 关键词、类别、相似度和重要度排序 | 依赖 `MemoryManager` 的部分应改为记忆仓储接口 |
| `modules/proactive_manager.py` | 计划状态、时间区间、冷却和提醒规则 | Qt 定时触发留在桌面端，规则判断可放云端 |

这些模块的核心价值是“输入普通 Python 数据，输出普通 Python 数据”。迁移时应保持这一特点，禁止在领域逻辑中引入 HTTP、Qt 或数据库对象。

### 2.2 业务可复用，但需要拆分本地存储的模块

| 模块 | 可迁移部分 | 当前耦合 |
| --- | --- | --- |
| `modules/growth_manager.py` | 计划、行动记录、复盘和成长日志规则 | 本地 JSON 路径、文件创建和读写 |
| `modules/memory_manager.py` | 分类、去重、冲突、归档、检索元数据 | `memory.json`、备份目录和迁移文件操作 |
| `modules/memory_candidate_manager.py` | 候选添加、确认、拒绝和去重 | `data/private/` JSON 持久化 |
| `modules/chat_history_manager.py` | 会话、消息、标题和摘要规则 | 聊天历史及摘要 JSON 持久化 |
| 当前知识库读取逻辑 | 文件扫描、切片和关键词匹配规则 | 本机 `data/knowledge/` 文件系统，部分流程位于 UI 代码 |

建议未来为这些模块引入抽象仓储，例如 `GrowthRepository`、`MemoryRepository`、`ChatRepository`。当前 JSON 实现继续作为 `LocalJson...Repository`，云端再提供数据库适配器。这样可以保留现有桌面行为，也便于逐步迁移和测试。

### 2.3 强依赖 PySide6、应留在桌面端的模块

| 模块 | 留在桌面端的原因 |
| --- | --- |
| `frontend/pet_app.py` | Qt 窗口、输入发送、消息渲染、QThread/Signal 和桌面生命周期 |
| `frontend/desktop_pet.py` | 透明无边框窗口、置顶、拖动、右键菜单、屏幕定位和本机交互 |
| `frontend/pet_actions.py` | `QPropertyAnimation`、窗口几何位置和透明度动画 |
| `frontend/pet_action_manager.py` | 桌宠状态冲突管理及本地动作调度 |
| `frontend/pet_bubble.py` | 桌面气泡窗口及其定位 |
| `frontend/settings_dialog.py` | 本地设置表单和 Qt 控件 |
| `frontend/growth_dialog.py` | PySide6 成长面板；数据来源未来可改为 API |
| `frontend/memory_dialog.py` | PySide6 记忆管理面板；数据来源未来可改为 API |
| `frontend/chat_history_dialog.py` | PySide6 会话历史界面；数据来源未来可改为 API |
| `assets/pet/` 与舞蹈帧播放器 | 本机视觉资源和实时渲染，不属于云端 Agent |

`frontend/pet_app.py` 当前还承担部分业务编排。未来迁移时可逐步缩减为：收集输入、调用 Agent API、渲染 `AgentResponse`、执行经过白名单校验的 `client_actions`。

### 2.4 模型调用位置

`modules/llm_client.py` 当前是本地模型/provider 适配层。未来云端架构中，模型凭据、模型路由和重试策略更适合留在服务端；桌面端只调用 Agent API，不应持有云端模型密钥。本评审不修改现有模型模块，也不新增 provider。

## 3. 本地 JSON 的未来归属

### 3.1 建议迁移为云数据库的数据

| 当前数据 | 云端领域对象 | 说明 |
| --- | --- | --- |
| `memory.json` | `memories`、`memory_tags`、`memory_usage` | 高敏感长期记忆，应加密、按用户隔离并支持导出/删除 |
| `data/private/memory_candidates.json` | `memory_candidates` | 候选确认状态和来源 |
| `data/private/memory_conflicts.json` | `memory_conflicts` | 冲突双方及用户处理结果 |
| `data/private/today_plan.json` | `daily_tasks` | 按用户和日期索引，任务 ID 不再使用易冲突的本地序号 |
| `data/private/action_log.json` | `action_logs` | 行动记录、来源和时间 |
| `data/private/growth_log.json` | `daily_reviews` 或 `growth_logs` | 每日复盘及成长日志 |
| `data/private/chat_history.json` | `chat_sessions`、`chat_messages` | 消息量较大，应按会话分页读取 |
| `data/private/chat_summaries.json` | `chat_summaries` | 与会话绑定，不等同于长期记忆 |

旧的 `data/today_plan.json`、`data/action_log.json` 和 `data/growth_log.json` 若仍用于兼容，不应在云端形成第二套表结构。迁移时统一导入上述领域对象，并记录来源版本。

### 3.2 建议继续留在本地的数据

| 数据 | 原因 |
| --- | --- |
| 桌宠位置、缩放、置顶、图片路径 | 设备相关，上传后对其他设备没有意义 |
| `test_mode` 和本地动画参数 | 开发或设备运行参数 |
| 桌宠图片、舞蹈帧和临时素材 | 本机渲染资源，可随应用发布 |
| 本地 Ollama 地址和本地模型选择 | 只对当前设备有效；不应作为用户云数据 |

`pet_config.json` 应在概念上拆成“设备设置”和“用户偏好”。设备设置留在本机；跨设备真正有价值的提醒偏好、上下文长度等可以在未来单独同步。无需为了云迁移直接上传整个配置文件。

### 3.3 可选迁移的数据

`data/knowledge/` 可能包含私人文档，不能默认上传。未来应由用户显式选择：

- 仅本地检索：文档和片段始终留在电脑上。
- 云端检索：原文件进入对象存储，文档元数据和片段索引进入数据库。
- 混合模式：桌面端检索后只把必要片段发送给 Agent API。

无论采用哪种方式，都应保留来源、更新时间和删除能力，并避免把本机绝对路径发送到云端。

### 3.4 数据迁移通用要求

- 所有云端对象增加 `user_id`，团队场景再增加 `workspace_id`。
- 主键使用稳定字符串或 UUID，不复用界面列表序号。
- 时间统一采用带时区的 ISO 8601，服务端存储建议使用 UTC。
- 每条记录包含 `schema_version`、`created_at`、`updated_at`。
- 写操作携带幂等键，防止网络重试导致重复计划、记忆或消息。
- 迁移工具必须先备份、可重复运行，并提供条数校验；本评审不实现迁移工具。

## 4. 可序列化 API 契约

所有契约只允许 JSON 基础类型：字符串、数字、布尔值、数组、对象和 `null`。不得携带 Python 异常、Qt 对象、回调、文件句柄或任意类实例。

### 4.1 IntentResult

```json
{
  "schema_version": "1.0",
  "request_id": "req_01...",
  "intent": "add_plan",
  "confidence": 0.93,
  "entities": {
    "tasks": ["学习机器学习 30 分钟"]
  },
  "needs_confirmation": false,
  "source": "rule",
  "warnings": []
}
```

约束：

- `intent` 使用稳定枚举；未知意图统一为 `chat`，不要把模型自由文本当意图名。
- `confidence` 范围为 `0.0` 到 `1.0`。
- `entities` 只放已验证的结构化实体，不暴露内部匹配对象。
- `source` 建议限定为 `fixed_command`、`rule`、`llm`、`fallback`。
- `needs_confirmation` 由安全策略最终复核，不能仅相信 LLM 输出。
- 当前兼容字段如 `slots`、内部 `reason` 可留在进程内，但不建议成为长期公共契约。

### 4.2 ToolResult

```json
{
  "schema_version": "1.0",
  "tool_call_id": "call_01...",
  "tool": "add_plan",
  "status": "completed",
  "success": true,
  "message_code": "plan.added",
  "display_message": "好，我已经把这件事加入今天的计划。",
  "data": {
    "task": {
      "id": "task_01...",
      "title": "学习机器学习 30 分钟",
      "done": false
    }
  },
  "error": null
}
```

失败时的 `error` 应为受控结构：

```json
{
  "code": "TASK_NOT_FOUND",
  "message": "没有找到对应计划。",
  "retryable": false,
  "details": {}
}
```

约束：

- `status` 建议使用 `completed`、`failed`、`clarification_required`、`confirmation_required`、`cancelled`。
- 不把堆栈、数据库错误或本地路径直接返回客户端。
- `data` 应按工具定义响应 schema，避免长期使用不可控的任意字典。
- `display_message` 用于界面展示，业务判断使用 `status` 和 `message_code`。

### 4.3 AgentResponse

```json
{
  "schema_version": "1.0",
  "request_id": "req_01...",
  "conversation_id": "conv_01...",
  "status": "completed",
  "message": "今天的计划已经记好了。",
  "steps": [
    {
      "index": 0,
      "tool": "add_plan",
      "status": "completed"
    }
  ],
  "tool_results": [],
  "pending_confirmation": null,
  "client_actions": [
    {
      "action_id": "action_01...",
      "name": "nod",
      "arguments": {},
      "expires_at": "2026-07-16T10:10:00+08:00"
    }
  ]
}
```

约束：

- `status` 与当前 Agent 语义保持一致，可使用 `completed`、`chat`、`clarification`、`confirmation_required`、`failed`。
- `pending_confirmation` 只返回确认 ID、摘要和过期时间，不返回可被篡改后直接执行的内部对象。
- `client_actions` 只能使用公开白名单，如 `nod`、`jump`、`show_bubble`、`play_dance`。
- 禁止通过 `client_actions` 下发 shell、任意文件读写、任意模块调用或未经允许的 URL 打开操作。
- 桌面端应再次校验动作名、参数和过期时间；云端响应不等于本机执行授权。

## 5. 桌面端调用云端 Agent API 的最小草案

以下仅为 HTTP/JSON 契约草案，不是可运行 Web 代码。

### 5.1 发送用户请求

`POST /v1/agent/requests`

请求：

```json
{
  "request_id": "req_01...",
  "conversation_id": "conv_01...",
  "message": {
    "id": "msg_01...",
    "content": "我今天想学习半小时机器学习",
    "created_at": "2026-07-16T10:00:00+08:00"
  },
  "client": {
    "platform": "windows",
    "app_version": "1.3",
    "device_id": "device_01...",
    "locale": "zh-CN"
  },
  "capabilities": {
    "client_actions": ["nod", "jump", "show_bubble", "play_dance"]
  }
}
```

响应：`AgentResponse`。

请求头建议包含认证令牌、`Idempotency-Key` 和客户端版本。服务端必须按用户隔离数据，并独立执行权限检查和安全策略。

### 5.2 确认或取消高风险操作

`POST /v1/agent/confirmations/{confirmation_id}`

```json
{
  "request_id": "req_02...",
  "decision": "confirm"
}
```

`decision` 仅允许 `confirm` 或 `cancel`。服务端根据已保存、未过期的确认对象执行，不能接受客户端重新提交的任意工具参数。

### 5.3 恢复会话消息

`GET /v1/conversations/{conversation_id}/messages?limit=20&cursor=...`

响应包含消息列表和下一页游标。桌面端只加载展示所需消息；Agent 构建上下文时仍由服务端执行摘要和裁剪，不依赖客户端上传全部历史。

### 5.4 报告本地动作结果

`POST /v1/client-actions/{action_id}/results`

```json
{
  "status": "completed",
  "error_code": null
}
```

此接口只用于可选的状态同步。舞蹈、点头等展示动作执行失败不应导致计划、记忆或聊天事务回滚。

## 6. 推荐的迁移顺序

1. **已完成：冻结契约**。`IntentResult`、`ToolResult`、`AgentResponse` 等契约已有独立序列化测试。
2. **已完成：抽离本地存储接口**。当前保留 Local JSON 适配器，Manager 不再直接读写文件。
3. **已完成：收紧客户端动作**。Agent 响应和桌面执行端使用同一白名单策略。
4. **待实施：先迁移低风险读取**。会话读取、计划查看、记忆检索可先走云端；本地仍保留回退路径。
5. **待实施：再迁移带状态写入**。计划、行动、候选记忆等写操作需要服务端幂等与冲突处理。
6. **待实施：最后迁移敏感数据**。长期记忆和知识文档必须先完成权限、加密、导出和删除方案。

## 7. 主要风险与验收条件

### 主要风险

- 网络重试导致重复添加计划、行动记录或记忆。
- 本地与云端同时修改产生覆盖冲突。
- 敏感记忆或聊天内容进入日志、错误信息或监控系统。
- 云端错误地下发本机动作，或客户端执行未声明能力。
- 把全部聊天历史、记忆和知识文档上传，造成上下文膨胀与隐私风险。
- 桌面断网后完全不可用，破坏桌宠的常驻体验。

### 进入实现阶段前的最低条件

- 三个核心契约有版本号、字段定义、错误码和兼容策略。
- 数据所有权、导出、删除、备份和加密策略明确。
- 桌面动作白名单与云端工具权限完全分离。
- 所有写操作具备幂等设计，高风险操作仍需二次确认。
- 桌面端断网策略明确：只读缓存、可排队操作和禁止离线执行的操作分别定义。
- 迁移可以逐模块回退，不要求一次替换当前本地版本。

## 8. 本次评审边界

本次只形成迁移准备结论和 API 契约草案。没有新增 Web 服务、数据库、部署配置、云端模型调用或网络客户端代码，也没有修改现有桌宠、聊天、动作、记忆、成长和本地数据逻辑。
