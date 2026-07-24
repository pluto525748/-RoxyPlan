# Tool Schema and Validation

## Schema 来源

`ToolRegistry.model_tool_contracts()` 从已注册工具导出只读元数据：

- `name`
- `description`
- `parameters`
- `risk_level`
- `side_effect`
- `confirmation_policy`
- `sequential`
- `parallel_safe`
- `model_visible`

导出结果只包含 JSON 基础类型，不包含 handler、Manager、Qt 对象、本地路径或函数引用。

`model_tool_schemas()` 将同一份契约转换为 Ollama / OpenAI-compatible function tools；`json_fallback_instruction()` 将同一份契约转换为受限 JSON 提示，避免维护两份工具协议。

## 参数限制

模型可见 JSON Schema 支持：

- string、integer、boolean、object
- required
- enum
- minimum / maximum
- minLength / maxLength
- pattern 元数据
- `additionalProperties=false`

模型输出到达执行器前会再次进行本地校验。模型声称参数正确并不构成信任依据。

## 可见性和风险

工具必须显式标记 `model_visible=true` 才能进入模型 schema。批量删除和内部审计等工具默认不直接暴露给模型。高风险工具即使目标唯一，也继续经过二次确认。

## 顺序与并行

当前版本稳定性优先，执行循环默认串行。只读工具可声明 `side_effect=false` 和 `parallel_safe=true`，为未来并行保留元数据；本轮没有开启实际并行执行。

所有计划、行动、复盘、记忆审核和共享 JSON 写入必须顺序执行。前一项写操作失败后，后续依赖操作停止。

## 错误回传

可回传一次的安全错误包括缺少字段、类型错误、枚举无效、范围错误和多余字段。内部异常统一转换为安全错误码；堆栈、Token、绝对路径和完整私人正文不会进入 ToolMessage。
