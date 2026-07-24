# ModelActionAdapter

`modules/model_action_adapter.py` 分为两个职责明确的对象。

## ModelActionAdapter

这是纯解析层，不执行工具：

1. 读取原生 Provider `tool_calls`，或读取受限 JSON fallback。
2. 检查工具是否在 `ToolRegistry` 的 `model_visible` 白名单内。
3. 检查参数根节点是否为 JSON object。
4. 使用本地 schema 检查必填字段、类型、枚举、范围和多余字段。
5. 统一返回 `ProposedAction`。

提案来源支持 `native_tool_call`、`json_fallback`、`chat_fallback`；固定命令和本地规则继续由 `IntentRouter` / `AgentCore` 的确定性路径处理。

## ModelToolCallLoop

这是受限编排层：

- 原生工具调用优先。
- Provider 不支持或原生协议错误时使用 JSON fallback。
- 参数错误最多安全回传一次。
- 高风险工具参数不明确时直接询问用户，不交给模型猜第二次。
- 所有工具最终仍通过 `AgentCore -> ToolExecutor` 执行。
- 写工具按模型返回顺序串行执行；关键写失败后短路。
- 同一 `call_id` 不会重复执行。
- 工具执行后，将匹配 `call_id` 的 `ToolMessage` 返回同一 Provider，再生成最终回复。

## 路由关系

```text
固定命令 > 本地规则 > ModelActionAdapter > 普通聊天
```

启用模型工具层后，不再先调用一次 LLM 意图解析、再调用一次 Tool Calling 做重复决策。模型提案若为普通聊天，才进入自然语言回复；回复最后仍经过 `ActionClaimGuard`。

## 降级原则

- 原生参数合法：进入本地安全链。
- 原生参数可修复：只允许一次修复。
- 工具未知或未开放：拒绝，不执行。
- JSON 无效：澄清，不猜测。
- 在线 Provider 网络失败：只降级为 Ollama 普通聊天，不在另一个 Provider 重放写操作。
