# V1.8 语义动作协议

单条用户消息最多执行一次主语义解析：

```text
LocalFeatureExtractor
  -> SemanticActionParser
  -> ActionCandidate[]
  -> BusinessResolver
```

## 职责

- 固定命令：处理稳定、明确、低歧义入口。
- 本地特征：提取否定、操作、领域、日期、时长、顺序和指代，不决定真实 ID。
- DeepSeek / Ollama：必要时提出受限工具候选和参数；原生 tool call 与 JSON fallback 使用同一候选结构。
- 程序：校验工具白名单、参数、风险、真实对象、重复和执行后状态。

`ActionCandidate` 包含工具名、原始参数、引用文字、置信度、歧义、依赖和顺序。模型返回的 `plan_id / memory_id / candidate_id` 不被直接信任；用户明确说出的编号仍需本地查询验证。

明显操作请求无法安全解析时必须澄清，不能落入普通聊天后由模型声称已经完成。普通咨询、否定表达和能力讨论不自动执行工具。

## 降级与开关

- 原生 tool call 不可用时可使用受限 JSON fallback。
- 参数只允许一次修复，仍不确定则澄清。
- `unified_semantic_parser_enabled=false` 可回退旧路由。
