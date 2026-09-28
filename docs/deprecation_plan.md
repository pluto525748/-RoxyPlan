# RoxyPlan 兼容与停用计划

更新时间：2026-07-29

本计划只分类，不在 V1.8.3 大规模删除代码。删除必须同时通过全局引用搜索、桌面与 Web 启动链、feature flag 矩阵和全量测试。

| 对象 | 分类 | 当前使用 | 替代/收口目标 | 停用条件 |
|---|---|---|---|---|
| `SemanticActionParser` | active | 桌面/Web 主语义入口 | 保留为唯一公开语义入口 | 不停用 |
| `IntentRouter` | active/internal compatibility | 由 SemanticActionParser 内部处理确定性规则；兼容 profile 可直接调用 | 逐步缩为 `DeterministicCommandRouter` 职责，不再独立竞争执行 | 无直接真实调用且兼容 profile 已跨版本退役 |
| `LLMIntentParser` | compatibility | 可选规则后结构化辅助 | 模型提案统一进入 ActionCandidate | 原生 tool call/JSON fallback 全量验证后 |
| `LLMPlanner` | deprecated for unified path | 仅 legacy 且 flag 显式开启时可读取原文 | unified path 中 AgentPlanner 只消费已解析动作 | legacy intent profile 退役后删除 |
| `AgentPlanner` | active | 规划已解析/已验证动作，最多 3 步 | 保留，不再理解 authoritative 原文 | 不停用 |
| `ModelActionAdapter` | active | 原生 tool_calls 与 JSON 转 ActionCandidate | 保留一个适配契约 | 不停用 |
| `ModelToolCallLoop` | compatibility | 可选 Provider tool calling | 只允许有界提案、验证与执行 | 与主语义链完全合并并有真实 Provider 回归后 |
| `ConversationStateManager` | compatibility | 部分历史候选/引用状态 | 新交互统一转 InteractionStateCoordinator | 所有历史对话回归覆盖且存量状态可读后 |
| `InteractionStateCoordinator` | active | 补槽、选择、确认、执行中、完成/取消/过期 | 唯一交互状态入口 | 不停用 |
| UI 固定命令 wrappers | compatibility | 菜单与历史按钮入口 | 输出与主链相同契约，不直接写业务数据 | 所有 UI 调用改为 ConversationService 后 |
| `DesktopPet.start_dance/execute_client_action` | compatibility | 菜单/紧急回滚 wrapper | 委托统一 Dispatcher | legacy pet flag 跨版本关闭且引用归零后 |
| `legacy_intent_path_enabled` | compatibility flag | 默认 false | 一键语义回滚 | 两个稳定版本无回滚需求后 |
| `legacy_direct_pet_action_enabled` | compatibility flag | 默认 false | 仅紧急动作回滚 | 统一 Dispatcher 两个稳定版本后 |
| `modules/client_action.py` 重导出 | compatibility API | 新旧 import 稳定层 | 后续统一契约物理位置 | 全部下游 import 迁移并验证后 |

## 禁止的停用方式

- 不凭文件名判断 dead。
- 不删除真实数据兼容读写代码来换取目录整洁。
- 不同时开启新旧写路径做 shadow 测试。
- 不用大规模移动文件代替职责收口。
- 未通过 `git diff --check`、全量 pytest、桌面 Qt 回归与 Local Web 回归不得删除 compatibility 代码。
