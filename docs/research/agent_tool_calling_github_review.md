# GitHub Agent 工具调用专项研究

研究日期：2026-07-22

本报告只做架构评审，不引入依赖，不修改 RoxyPlan 业务代码。源码链接固定到研究时使用的发布标签或分支；分支 HEAD 之后可能继续变化。

## 1. 研究快照

| 项目 | 研究版本/分支 | 许可证 | 运行时要求 | 本轮定位 |
| --- | --- | --- | --- | --- |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | `v2.15.0`；研究时 `main` 为 `24d105d` | MIT | Python >= 3.10，Pydantic >= 2.12 | 深入追踪工具闭环 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | `1.2.9`；研究时 `main` 为 `31f90df` | MIT | Python >= 3.10，Pydantic >= 2.7.4 | 深入追踪工具节点和可恢复状态 |
| [Semantic Kernel](https://github.com/microsoft/semantic-kernel) | Python `1.44.0`；研究时 `main` 为 `e15ae16` | MIT | Python >= 3.10，Pydantic 2 | 对照插件和自动函数调用 |

RoxyPlan 当前为 Python 3.8.8、Pydantic 1.10.15。上述三者都不能作为“安装后直接替换”的无风险依赖。

## 2. RoxyPlan 当前真实调用链

桌面端和 Local Web 已共享同一业务入口：

```text
ChatWindow / AgentService
  -> ConversationService.prepare
     -> ConversationStateManager（当前事实与抑制）
     -> AgentCore.handle_confirmation（已有待确认操作）
     -> IntentRouter（固定命令 -> 本地规则 -> 可选 LLM JSON）
     -> AgentCore.process
        -> AgentPlanner（IntentResult -> 最多 3 个 AgentStep）
        -> ToolExecutor
           -> ToolRegistry 白名单
           -> 参数 schema 校验
           -> SafetyPolicy
           -> ConfirmationManager
           -> Manager handler
           -> ToolResult 与后置条件校验
        -> AgentResponse（只按真实 ToolResult 报告）
     -> 普通 chat 才构建 ContextBuilder 上下文并调用 LLMClient
```

关键代码：

- [`modules/conversation_service.py`](../../modules/conversation_service.py)：统一桌面/Web 会话入口。
- [`modules/intent_router.py`](../../modules/intent_router.py)：三层意图识别。
- [`modules/agent_core.py`](../../modules/agent_core.py)：澄清、规划、执行和结果汇总。
- [`modules/agent_planner.py`](../../modules/agent_planner.py)：意图到受限工具步骤。
- [`modules/tool_registry.py`](../../modules/tool_registry.py)：工具名、描述、风险、参数和 handler。
- [`modules/tool_executor.py`](../../modules/tool_executor.py)：白名单、参数、风险、确认、异常边界。
- [`modules/confirmation_manager.py`](../../modules/confirmation_manager.py)：按会话隔离的短时确认。
- [`modules/safety_policy.py`](../../modules/safety_policy.py)：确定性允许/澄清/确认/拒绝。
- [`modules/contracts.py`](../../modules/contracts.py)：`IntentResult`、`ToolResult`、`AgentResponse` 等契约。

### 已经做对的部分

1. 模型不能直接拿到任意 Python 函数，只能提议白名单意图/工具。
2. 参数在执行前再次校验，额外参数和类型错误会拒绝。
3. 写操作与危险操作有置信度和确认门。
4. handler 返回后会检查结构和关键后置条件。
5. 工具未成功时，`AgentCore` 不使用完成式回复。
6. 确认与具体工具参数绑定，并按会话隔离、限时失效。

### 与成熟工具协议的主要差距

1. `LLMIntentParser` 和 `LLMPlanner` 使用提示词 JSON，不消费模型原生 `tool_calls`。
2. 工具 schema 尚未统一转换为 OpenAI/Ollama function tool JSON Schema。
3. 参数验证错误不会作为匹配 `tool_call_id` 的工具消息反馈给模型修复。
4. 没有统一的 `assistant tool_call -> tool result -> assistant final` 消息回环。
5. pending confirmation 仅在进程内；重启后明确失效，但不能恢复。
6. 多工具目前按计划顺序执行，没有读工具并行策略和显式工具调用预算。
7. 指代解析仍在 IntentRouter 与工具 handler 周边分散，没有独立引用解析器。

## 3. Pydantic AI 工具调用源码追踪

### 3.1 工具注册与 schema

关键源码：

- [`pydantic_ai/tools.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/tools.py)：`Tool`、`ToolDefinition`、参数 JSON Schema、严格模式、返回 schema。
- [`pydantic_ai/_function_schema.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_function_schema.py)：从函数签名和类型提示构建 Pydantic Core Schema、`SchemaValidator` 与 JSON Schema。
- [`pydantic_ai/toolsets/function.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/toolsets/function.py)：`FunctionToolset.add_function` 与 `call_tool`。
- [`pydantic_ai/agent/abstract.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/agent/abstract.py)：Agent 注册工具及运行入口。

函数名成为工具名，docstring/显式 description 成为模型可见描述；普通参数被转换为 JSON Schema，`RunContext` 等依赖参数不会暴露给模型。参数不仅有模型侧 schema，还有执行前的 `SchemaValidator`。

### 3.2 模型请求与原始 tool call

OpenAI-compatible 适配关键源码：

- [`models/openai.py#L1277-L1322`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/models/openai.py#L1277-L1322)：决定 `tool_choice` 并准备工具列表。
- [`models/openai.py#L1585-L1604`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/models/openai.py#L1585-L1604)：把 `ToolDefinition` 映射成 provider tool schema。
- [`models/openai.py#L1110-L1172`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/models/openai.py#L1110-L1172)：把 provider 返回的 `id + function.name + function.arguments` 转成 `ToolCallPart`。
- [`messages.py#L1470-L1604`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/messages.py#L1470-L1604)：`ToolReturnPart` 和 `RetryPromptPart`。
- [`messages.py#L1990`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/messages.py#L1990)：`ToolCallPart`。

抽象后的原始结构是：

```json
{
  "tool_call_id": "call_123",
  "tool_name": "add_plan",
  "args": {"title": "学习机器学习 30 分钟"}
}
```

### 3.3 参数校验、重试与执行

完整路径：

```text
ModelRequestNode
  -> provider request(tools + messages)
  -> ModelResponse(parts=[ToolCallPart...])
  -> CallToolsNode._handle_tool_calls
  -> process_tool_calls
  -> ToolManager.validate_tool_call
  -> ToolManager.execute_tool_call
  -> ToolReturnPart / RetryPromptPart
  -> ModelRequestNode（携带真实工具结果再次请求模型）
  -> 最终 TextPart 或结构化输出
```

核心文件和函数：

- [`_agent_graph.py#L1634`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1634)：`CallToolsNode`。
- [`_agent_graph.py#L1848-L1925`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L1848-L1925)：`_handle_tool_calls` 调用 `process_tool_calls` 并收集工具返回。
- [`_tool_execution.py#L111`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_tool_execution.py#L111)：工具批次与并行/顺序策略。
- [`_tool_execution.py#L506-L575`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_tool_execution.py#L506-L575)：把执行值、拒绝或重试转换为匹配 call ID 的消息 part。
- [`tool_manager.py#L158-L193`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/tool_manager.py#L158-L193)：顺序屏障、重试上限、`ValidationError/ModelRetry -> RetryPromptPart`。
- [`tool_manager.py#L773-L938`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/tool_manager.py#L773-L938)：校验、执行、延迟调用和审批恢复。
- [`usage.py#L283-L369`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/usage.py#L283-L369)：请求、token 和工具调用预算。

不存在的工具由解析层转换为模型可见的 retry 信息；参数 Pydantic 校验失败也会附到对应 `tool_call_id`，模型在受限重试次数内重新给参数。超过每工具重试上限会抛出 `UnexpectedModelBehavior`，避免无限循环。

### 3.4 多工具与并行

`_tool_execution.py` 把普通函数工具分段并行；标记 `sequential=True` 的工具成为屏障。研究版本还支持全局顺序模式。对 RoxyPlan 的启示不是“全部并行”，而是：

- `show_plan`、`search_memory` 等无副作用读取可并行。
- `add_plan -> complete_plan -> save_review` 等共享 JSON 状态的写操作必须顺序执行。
- 同一轮多个写操作需要逐步后置条件和失败短路。

### 3.5 人工审批

关键源码和测试：

- [`toolsets/approval_required.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py)：通过 `ApprovalRequired` 把调用转成延迟请求。
- [`tests/test_tools.py#L1575-L1594`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/tests/test_tools.py#L1575-L1594)：审批后以 `DeferredToolResults` 恢复，并允许覆盖参数。
- [`tests/test_tools.py#L3810-L4017`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/tests/test_tools.py#L3810-L4017)：重试预算与审批恢复。

恢复审批不只是重新说“确认”，而是保存原消息历史和 `tool_call_id`，再提供 `DeferredToolResults`。嵌套 Agent 甚至要分别保留父子消息历史。这比 RoxyPlan 当前进程内 `PendingConfirmation` 更完整，但复杂度也明显更高。

## 4. LangGraph 工具调用源码追踪

### 4.1 图结构

关键源码：

- [`chat_agent_executor.py#L278`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py#L278)：`create_react_agent`。
- [`chat_agent_executor.py#L586`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py#L586)：`model.bind_tools(...)`。
- [`chat_agent_executor.py#L831-L872`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py#L831-L872)：检查最后一条 `AIMessage.tool_calls`，路由到 `tools` 或结束。
- [`chat_agent_executor.py#L958-L990`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py#L958-L990)：条件边和 `tools -> agent` 回边。

完整流程：

```text
State.messages + system prompt
  -> agent node 调用 bind_tools 后的 ChatModel
  -> AIMessage(content, tool_calls=[{id,name,args}])
  -> should_continue
     -> 无 tool_calls: END
     -> 有 tool_calls: ToolNode
  -> ToolMessage(name, tool_call_id, content/status)
  -> tools -> agent
  -> 模型读取真实 ToolMessage 并给最终 AIMessage
```

LangGraph 本身主要负责状态、节点、路由、检查点和恢复；参数 schema 和模型绑定来自 LangChain Core `BaseTool`/Pydantic。

### 4.2 ToolNode

- [`tool_node.py#L622`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/tool_node.py#L622)：`ToolNode`。
- [`tool_node.py#L922-L1012`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/tool_node.py#L922-L1012)：注入状态后调用工具，Pydantic 错误转换为 `ToolInvocationError`，可按策略返回错误 `ToolMessage`。
- [`tool_node.py#L1268-L1279`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/tool_node.py#L1268-L1279)：未知工具返回带可用工具名的错误 `ToolMessage`。
- [`tool_node.py#L821-L823`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/tool_node.py#L821-L823)：同步多个调用通过 executor 并行。
- [`tests/test_tool_node.py#L565`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/tests/test_tool_node.py#L565)：错误工具名测试。
- [`tests/test_tool_node.py#L269-L536`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/tests/test_tool_node.py#L269-L536)：不同异常处理配置。
- [`tests/test_react_agent.py#L599`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/tests/test_react_agent.py#L599)：并行工具调用测试。

### 4.3 状态、限制与人工介入

- [`chat_agent_executor.py#L62-L74`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py#L62-L74)：`remaining_steps`。
- [`langgraph/types.py#L759-L783`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/langgraph/langgraph/types.py#L759-L783)：`Command(resume=...)`。
- [`langgraph/types.py#L811-L930`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/langgraph/langgraph/types.py#L811-L930)：`interrupt()` 与恢复语义。

`interrupt()` 依赖 checkpointer。恢复时节点会从开头重新执行，因此 interrupt 之前的副作用必须幂等或移到恢复之后。它解决的是“可恢复执行状态”，不是自动的安全策略；仍需业务层决定哪个工具必须确认、确认摘要是什么、参数是否可修改。

## 5. Semantic Kernel 对照结论

关键源码：

- [`kernel_function_decorator.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/functions/kernel_function_decorator.py)：从装饰器、签名、`Annotated` 和 docstring 生成函数元数据。
- [`kernel_function_from_method.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/functions/kernel_function_from_method.py)：方法参数解析。
- [`function_calling_utils.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/connectors/ai/function_calling_utils.py)：生成 provider 可用函数配置。
- [`kernel.py#L326-L463`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/kernel.py#L326-L463)：`invoke_function_call`，查找函数、调用过滤器、写入 `FunctionResultContent`。
- [`chat_completion_with_auto_function_calling.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/samples/concepts/auto_function_calling/chat_completion_with_auto_function_calling.py)：自动 function calling 示例。
- [`test_chat_completion_with_function_calling.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/tests/integration/completions/test_chat_completion_with_function_calling.py)：OpenAI、Ollama 等 connector 的自动/手动调用测试。

它的 plugin、filter、`FunctionChoiceBehavior` 和 `FunctionResultContent` 模式成熟，但 RoxyPlan 已有 Manager、Registry、Policy 和 Service；整体引入会形成重复抽象，并要求 Python/Pydantic 升级。适合参考“调用前后 filter”和“provider connector 统一格式”，当前不适合直接作为核心依赖。

## 6. 十五个工具调用问题的结论

| 问题 | 成熟实现的共同答案 | RoxyPlan 建议 |
| --- | --- | --- |
| 工具如何注册 | 函数/对象 + 名称 + 描述 + JSON Schema | 保留 `ToolRegistry`，增加只读 schema exporter |
| 原始 tool call | `id + name + arguments`，arguments 可能是 JSON 字符串或对象 | 统一为新的提议对象，不直接执行 |
| 类型校验 | Pydantic/Core Schema 在执行前校验 | Python 3.8 阶段继续轻量 schema，补枚举/范围/格式 |
| 业务校验 | 工具内部解析目标和检查当前状态 | 保留 Manager 与 handler，模型不能代替 |
| 不存在工具 | 返回绑定 call ID 的模型可见错误，或直接拒绝 | 白名单拒绝；仅在可修复时允许一次模型重试 |
| 缺参数/错类型 | 返回结构化 validation error | 精确澄清优先；小模型最多一次修复 |
| 目标不存在 | 工具返回 `not_found/ambiguous` | 不让模型声称完成，展示候选让用户选 |
| 错误是否反馈模型 | 只反馈安全、可修复、去隐私的错误 | 写操作失败优先确定性回复，避免模型粉饰 |
| 工具结果如何入历史 | 匹配 `tool_call_id` 的 ToolMessage/ToolReturnPart | 未来新增协议消息，但业务历史仍存摘要结果 |
| 最终回复 | 模型读取真实结果后生成；框架负责链路，不必然保证语义诚实 | 写操作成功/失败仍由 `AgentCore` 确定性汇报 |
| 多工具 | 顺序或分段并行，失败可短路 | 默认顺序；只读且独立才并行 |
| 最大次数 | request/tool/remaining step budget | 保留 3 步，再增加每轮模型回环和工具调用预算 |
| 危险工具 | 注册白名单 + policy/filter + HITL | 保留 `SafetyPolicy`，模型无权降级风险 |
| 人工确认 | 保存 call ID、参数、上下文和有效期后恢复 | 当前保持短时失效；未来持久化必须重验状态 |
| 防止虚假完成 | 只把真实结果交给最终回复；但自然语言仍需产品层约束 | 加“动作声明门”：没有成功 ToolResult 就禁止完成式文案 |

## 7. 最重要的工程判断

Pydantic AI 和 LangGraph 都不能单独保证模型绝不说谎。它们保证的是：工具调用有结构、结果能回到模型、错误可恢复、循环可限制、状态可追踪。RoxyPlan 仍应保留自己的事实门：

1. 写操作完成式回复只能由成功 `ToolResult` 触发。
2. 普通聊天路径不允许输出“已添加/已保存/已删除/已完成”等系统事实；触发时应改为未执行说明或重新路由。
3. LLM 只是 `ProposedAction` 生成器，不是执行者、确认者或事实来源。
4. 小模型输出的工具名和参数一律视为不可信输入。
5. 框架迁移不能替代中文实体、指代、时间和当前业务状态的专门测试。

## 8. 对 RoxyPlan 的直接结论

- 当前 `AgentCore + ToolExecutor + SafetyPolicy` 的方向正确，不需要为了获得工具闭环整体推翻。
- 下一步最有价值的是增加 provider-neutral 的“模型动作提议适配器”，支持原生 `tool_calls` 和 JSON fallback 两种输入，最终都进入现有 `AgentPlanner/ToolExecutor`。
- Pydantic AI 最值得借鉴参数 schema、retry part、工具预算和审批恢复。
- LangGraph 最值得借鉴显式状态、检查点、interrupt 和工具消息回边。
- 在 Python 3.8.8 上不建议直接引入两者；先建立兼容层和评测集，之后再单独决策 Python 3.10+ 升级。

## 9. qwen3:4b 原生工具调用实测

测试于 2026-07-22 在本机执行，环境为 Ollama `0.9.1`、`qwen3:4b`、Python `3.8.8`。测试直接请求 `POST /api/chat` 并传入原生 `tools`，没有经过 RoxyPlan 的规则路由，也没有读写真实计划、记忆或聊天数据。工具集合包含 `add_plan`、`update_plan`、`complete_plan`、`add_action_log` 和 `show_plan`。

### 9.1 低生成预算探针

使用 `temperature=0.2`、三个不同 seed、`num_predict=320`，覆盖明确添加、模糊添加、咨询、信息不足、未注册能力、完成计划、多工具和带上下文指代，共 24 次：

| 指标 | 结果 |
| --- | --- |
| 全部样本精确通过 | 10/24 |
| 需要实际动作的样本 | 1/15 |
| 应保持不执行的样本 | 9/9 |
| `done_reason=length` | 22/24 |
| 乱造工具名 | 0 |
| 已返回调用中的必填参数缺失 | 0 |

失败的主要原因不是参数格式错误，而是模型把预算消耗在分析文本中，截断前没有生成 `tool_calls`。即使请求包含 `think=false`，响应 `content` 仍出现了较长推理文本。

### 9.2 充足预算控制实验

将 `num_predict` 提高到 1024 后，对五类需要动作的样本各运行三个 seed：

| 场景 | 通过 | 观察 |
| --- | ---: | --- |
| “把学习机器学习加入计划，安排50分钟” | 3/3 | 工具和参数正确 |
| “我今天下午想学半小时机器学习” | 3/3 | 正确提取下午、30 分钟和主题 |
| “机器学习学完了” | 0/3 | 三次都错误选择 `add_action_log`，没有选择 `complete_plan` |
| 添加计划并同时记录行动 | 1/3 | 一次返回两个调用，两次耗尽 1024 token 后仍无调用 |
| 给定成功工具结果后“把刚才那个改成50分钟” | 3/3 | 正确引用结构化 `task_id` 并调用 `update_plan` |

精确通过为 10/15，平均生成 714.3 token，平均约 11.2 秒。单独追加 `/no_think` 能减少一次样本的推理长度，但没有完全消除推理内容，因此不能把它当作可靠的性能开关。

### 9.3 当前 JSON fallback 对照

两种 JSON 路径也做了实测：

1. 使用 Ollama `format` 强制一个通用动作 JSON schema，21/21 都能解析为 JSON，但 0/21 完全满足业务字段和动作要求。典型错误包括把单动作塞入多动作数组、遗漏标题或引用、把咨询误判为完成计划，以及信息不足时不要求澄清。
2. 直接调用项目现有 `LLMIntentParser + LLMClient`，五个样本中只有咨询问题成功解析为 `chat`；明确添加、完成、多动作和指代更新均返回 `None`。耗时约 14.1 到 38.9 秒。

这不表示 JSON 永远不可用，而是说明“强制可解析 JSON”不能代替针对每个 intent 的严格 schema、实体校验和模型语料评测。当前 fallback 可以安全拒绝无效结果，但还不能稳定承担动作路由。

### 9.4 实证结论

- `qwen3:4b` **支持 Ollama 原生 tools 参数和多 tool call 格式**。
- 单一添加和带结构化工具结果的指代更新可以成功，但需要较高生成预算。
- 相邻意图区分不可靠：完成计划会被当成行动记录。
- 多工具调用不稳定，存在很高的长推理和截断概率。
- 暂时不能直接启用“模型自动调用并执行”；应先经过 `ProposedAction`、本地 schema、业务目标解析、确认和 ToolExecutor。
- JSON fallback 也必须按 intent 设计窄 schema，并在独立评测达到阈值后才可用于可逆写操作。
