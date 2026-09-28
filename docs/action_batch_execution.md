# V1.8 多动作执行

`ActionBatch` 保存单轮最多三个有序动作，`ToolExecutionPlan` 负责执行预算、依赖和幂等。

## 策略

- 写操作默认顺序执行。
- 依赖动作失败后，后续依赖项标记为跳过。
- 独立动作使用 `best_effort`，允许得到 `partial_success`。
- 同一 batch 内相同 `tool_call_id` 或 `idempotency_key` 不重复执行。
- 相同 `request_id` 会得到稳定 batch 标识，重放同一请求不会重复写入。
- 新 batch 可以再次执行相同的用户操作，不把跨轮正常请求误判为重放。
- 模型最终回复失败不会重放已经完成的工具。

每个 `ToolResult` 独立保留成功、状态、错误码和变更资源。最终回复逐项说明成功、失败和跳过，不能用一句笼统的“都完成了”掩盖部分失败。

语义解析或业务解析阶段若某项失败，和它没有依赖关系的后续动作仍可执行。例如找不到要修改的计划时，独立的行动记录仍会写入并返回 `partial_success`。

配置 `action_batch_enabled=false` 可退回旧的逐步执行路径。
