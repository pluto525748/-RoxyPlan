# V2.2 真实桌面验收问题经验报告

日期：2026-08-27

## 1. 本次处理范围

本轮目标不是扩展产品能力，而是稳定 V2.2 已有链路。`SemanticDecision` 主层保持冻结，重点修复真实桌面验收中已经选对工具之后的执行分叉、错误的 Interaction pending、ToolResult 后状态未收口，以及没有 ToolResult 却声称操作成功的问题。

本轮没有修改 `modules/llm_client.py`、模型供应商集成、私有配置或用户数据，也没有新增产品功能。

## 2. 原始日志证据

原始会话中存在两组关键证据。

第一组是相同的 `add_plan` 路径：

- `req_a218d686d117411788be5bb16efbbf50` 已经出现 `SemanticDecision=add_plan`、`Pipeline=execute`、`Tool selected=add_plan`，但随后直接进入 `Agent clarification required`，没有出现 `Tool execute`。
- `req_07658ec19f944c9aace0dbda7bbd3e01` 同样选择 `add_plan`，随后出现 `Tool execute`、`Tool success` 和 Interaction `completed`。

这说明第一处偏离不在 SemanticDecision，也不在工具选择，而在 ToolExecutor 的安全裁决阶段。代码证据是 `add_plan` 属于 medium 风险，而 SafetyPolicy 仍会再次用模型 confidence 与 `0.82` 阈值比较。业务解析已经完成后，confidence 仍能单独推翻本地解析和授权结果，造成同类请求随模型分数波动而分叉。

第二组是“完成事项但没有匹配计划”的路径：

1. 用户报告“我完成来实习的机器臂调试”。
2. 系统回复“没有找到对应的未完成计划。要不要改为记录一条行动？”
3. 用户回复“好”。
4. 系统声称“那我记下了……今天这一项就算完成了”。
5. 用户随后查看行动记录，事实是没有添加。

这里的第一处偏离发生在 BusinessResolver 文本与 Interaction 状态之间：文案提出的是 `add_action_log` 替代操作，但保存的 pending 不是结构化的 `action_log_offer`，所以“好”无法恢复为一次受控的 `add_action_log`。后续又因为普通 CHAT 回复没有 ToolResult，ResponseComposer 未识别“我记下了”等表达，最终形成虚假成功声明。

## 3. 根因结论

### 3.1 confidence 被错误地当成第二次最终裁判

confidence 适合表达模型语义判断的不确定度，但它不应覆盖已经完成的本地事实链：

```text
Schema 校验通过
→ BusinessResolver resolved
→ PlanAuthorizationPolicy = EXECUTE
→ 工具为可逆、非高风险操作
```

旧实现中，这条链完成以后，SafetyPolicy 仍能仅凭 confidence 低于 `0.82` 返回 clarification。于是 confidence 不是辅助风险信号，而变成了重复执行裁判。

### 3.2 文本承诺与 pending 契约不一致

“要不要改为记录一条行动”不是普通提示语，它代表一个可继续的交互契约。只要系统提出这个选择，就必须同时保存：

- `interaction_kind=action_log_offer`
- `state=awaiting_confirmation`
- `tool_name=add_action_log`
- 用户刚才报告的真实内容

旧实现只输出了文案，没有保存同等语义的结构化状态。

### 3.3 ToolResult 没有成为回复事实的硬边界

写操作的成功只能由匹配的成功 ToolResult 证明。普通 CHAT 且 `ToolResult=[]` 时，“我记下了”“已经帮你加入了”“这项算完成了”“我会保存到长期记忆”等表达都没有事实依据。

旧 guard 只能识别一部分“已经……保存”句式，漏掉了短句、口语句和未来承诺。

### 3.4 Interaction 生命周期没有按执行结果闭合

旧链路在执行前统一进入 `awaiting_tool_result`，但只处理成功和确认分支。普通失败后状态仍可能停留在 `awaiting_tool_result`，导致下一轮被当成仍有 pending 的旧任务。

## 4. 为什么不能简单降低置信度阈值

降低阈值只能让更多 medium 风险操作通过，但不能解决根因。

它有四个直接问题：

1. 全局阈值会同时影响所有 medium 风险工具，而不是只修复已经被本地链路确认的请求。
2. 阈值从 `0.82` 调到 `0.75` 后，未来仍会在 `0.74/0.76` 等位置产生同样的随机分叉。
3. 它会削弱真正缺少本地授权、参数不完整或来源可疑请求的保护。
4. 它完全不能修复错误 pending、状态残留和无 ToolResult 虚假成功声明。

因此本轮保留原阈值，只增加一个窄范围、可审计的 `execution_authorized` 信号。

## 5. 修复后的执行契约

### 5.1 confidence 作为第二裁判的正确含义

confidence 仍然参与 SafetyPolicy，但只在“没有获得本地完整授权”时拥有阻断权。

本地授权必须同时满足：

- Semantic Pipeline 已完成 schema 校验；
- BusinessResolver 产出非空 `resolved_actions`；
- PlanAuthorizationPolicy 明确返回 `EXECUTE`；
- 每个工具均已注册且启用；
- 风险级别仅为 low 或 medium；
- 工具可逆；
- 工具不是 `confirmation_policy=always`。

即使传入了 `execution_authorized=True`，ToolExecutor 仍会再次检查工具的风险、可逆性和确认策略。高风险、否定请求、咨询请求和必须确认的操作不能借此绕过。

### 5.2 Interaction 状态收口规则

| 执行结果 | Interaction 目标状态 |
| --- | --- |
| 成功 | `completed` |
| 部分成功 | `partial_success` |
| 需要确认 | `awaiting_confirmation` |
| 需要补充信息 | `awaiting_clarification` |
| 普通失败 | `cancelled`，并标记 consumed |

因此 `awaiting_tool_result` 只表示工具结果尚未返回，不能成为一轮响应结束后的稳定状态。

### 5.3 行动记录替代流程

当 `complete_plan` 找不到对应未完成计划并询问是否改记行动时，系统现在创建真实的 `action_log_offer`。用户在该 scoped pending 下回复“好”“好的”“可以”“行”“记下来”或“确认”时，恢复为一个 `add_action_log` SemanticParseResult，并只通过正常 Agent/ToolExecutor 链执行。

### 5.4 回复事实不变量

对写操作，最终回复遵守以下不变量：

```text
声称写入成功
⇒ 必须存在 success=true 的匹配写 ToolResult
```

反向不要求所有 ToolResult 都使用固定文案，但没有 ToolResult 时绝不允许完成式声明或写入承诺。

## 6. 修改位置

- `modules/conversation_service.py`：生成本地执行授权；建立 `action_log_offer`；识别 scoped 确认；按响应结果收口 Interaction。
- `modules/agent_core.py`：把本地授权传入单工具和批量执行链。
- `modules/tool_execution_plan.py`：批量执行透传授权。
- `modules/tool_executor.py`：只对可逆 low/medium 且非 always-confirm 工具接受授权。
- `modules/safety_policy.py`：授权仅绕过 medium confidence 阈值，不绕过否定、咨询和确认保护。
- `modules/response_composer.py`：扩大无 ToolResult 写操作声明和承诺的结构化拦截。
- `tests/test_v22_reliability.py`、`tests/test_safety_policy.py`、`tests/test_response_composer.py`：增加真实故障回归测试。

## 7. 验证结果

新增回归覆盖：

1. 同一 resolved `add_plan` 在 confidence `0.8` 和 `0.9` 时行为一致。
2. `complete_plan not_found` 创建真实 `action_log_offer`。
3. 用户回复“好”后执行一次 `add_action_log`。
4. 工具失败后不遗留 `awaiting_tool_result`。
5. CHAT 且无 ToolResult 时拦截“我记下了”“帮你加入了”“这项算完成了”和“我会保存”等声明。
6. 本地授权不能绕过高风险确认或否定请求。

测试结果：

- 核心故障回归：`46 passed`
- Agent、ToolExecutor、Interaction、PlanAuthorization、ResponseComposer 等定向回归：`90 passed`
- 受影响的旧版回放与桌面事实边界回归：`48 passed`
- 全量：`927 passed, 14 failed`

修复前已知基线为 `920 passed, 15 failed`，且本轮新增了 6 项测试。换算后，本轮新增测试全部通过，并使旧测试净减少 1 个失败，没有增加失败总数。

剩余 14 项失败集中在既有的长期记忆引用解析、在线验收样例、重复计划识别和助手规划引用等问题，属于本轮冻结范围之外，不能混入本次修复继续扩大改动。

## 8. 可复用经验

### 经验一：找“第一处偏离”，不要只修最终表现

看到“有时执行、有时澄清”时，不能先改提示词或阈值。应逐层对照：

```text
SemanticDecision
→ Interaction
→ 参数解析与 Schema
→ BusinessResolver
→ Agent/Planner
→ ToolExecutor/SafetyPolicy
→ ToolResult
→ ResponseComposer
```

哪个阶段第一次从“预期继续执行”变成“实际停止”，哪个阶段才是首要根因。

### 经验二：模型置信度不是权限

confidence 是概率信号，不是业务授权。真正的执行授权必须由可解释的结构化条件组成，并且能够在日志和测试中复现。

### 经验三：任何选择文案都必须有同构状态

只要助手说“要不要……”，系统就必须保存用户下一句能够继续的 Interaction 数据。文案、pending kind、immutable arguments 和恢复逻辑必须表达同一件事。

### 经验四：ToolResult 是外部事实，不是文风建议

“不要虚假声称成功”不能只写在 system prompt 中。模型可能忽略提示，所以必须在最终 Response 边界用结构化事实强制执行。

### 经验五：临时状态必须有终态

`awaiting_tool_result`、`processing`、`pending` 一类状态必须为每一种返回路径定义终态，包括异常、业务失败和参数失败。否则状态机会把一次已结束的失败错误地带进下一轮。

## 9. 后续防复发检查表

以后新增或修改写工具时，应逐项确认：

- 是否只有一个明确的业务授权点；
- confidence 是否只是辅助信号，而不是重复最终裁判；
- Resolver 的每种状态是否都有对应 Interaction 状态；
- 提出的每个确认/选择是否保存了 immutable arguments；
- 每个执行返回路径是否离开 `awaiting_tool_result`；
- 每个成功声明是否能追溯到匹配 ToolResult；
- 高风险、不可逆和 always-confirm 工具是否无法使用普通授权绕过；
- 是否有 0.8/0.9 等边界值测试，而不是只测高置信度理想样例。

## 10. 2026-08-27 续审计补充

在完成上述 V2.2 修复后的继续回归中，又确认并收窄了一个重复计划边界：

- 同一核心任务，已有计划带 40 分钟、当前请求未给时长时，必须进入澄清，不能直接再写一条。
- 同一核心任务但明确给出不同的 30/50 分钟时，仍允许并存，不能用无差别模糊匹配误伤用户的不同安排。

实现位于 `PlanService.find_semantic_duplicates`，由 `BusinessResolver` 在写入前调用；它只做标题 canonicalization 和时长/时段兼容性比较，不改变计划引用场景的 fuzzy lookup。新增回归覆盖了“再加一条”命令外壳、缺失时长重复和不同时长并存。

同一轮审计还发现长句否定防护曾误伤简单句“我不想跳舞”。本地命令信封现在同时覆盖短否定和长 payload 否定，且仍由 `SemanticActionParser` 的 `local_negation_veto` 作为最终写入硬拦截。

验证结果：计划/业务/历史可靠性及 V2.2 回归合计 `106 passed`；否定与本地特征专项 `24 passed`。全量审计运行得到 `931 passed, 15 failed`，其中失败集中在旧模型夹具缺少 V2.2 结构化字段、已停用 candidate/不可见 read 工具的历史验收契约、旧多意图文案断言和在线模型样例漂移；未发现本轮新增的写入安全回归。全量数字包含最终否定修复前的那次运行，专项结果已覆盖最终修复。

审计追加的非业务改进：历史会话行内“打开/删除”按钮增加稳定 `accessibleName`，用于桌面自动化和无障碍定位，不改变用户数据或交互语义。

继续审计又发现一处目录配置遗漏：`show_recent_conversation` 的实现、路由和只读属性都存在，但没有进入 `ToolRegistry` 的模型可见白名单，导致在线 `read_010` 在 Validation 阶段被 `tool_not_model_visible` 拒绝。现已同步加入 `ToolRegistry` 和 `CapabilityRegistry`，并补充模型 schema 回归。修复后 `read_010` 共享管线通过，注册表/模型调用专项 `16 passed`。

同时清理了 `ToolDefinition` 中重复声明的 `aliases`/`field_aliases` dataclass 字段。重复声明会让后一个默认值静默覆盖前一个定义，虽然当前运行未直接报错，但会削弱工具 schema 维护的可解释性。工具注册、能力注册、语义管线及 V2.2 回归合计 `105 passed`。

## 11. 2026-08-28 测试契约分轨与纯指代续接修复

在继续处理旧全量失败时，先重新取得当前 dirty worktree 基线：`933 passed, 13 failed`。逐项检查表明，失败混合了三种不同含义：旧模型夹具缺少 V2.2 结构化授权字段、历史测试仍断言已经改变的路由/确认策略，以及只绑定旧文案或旧 `NoCallLLM` 路径的断言。初始 13 项中没有出现新的错误工具执行或写入安全回归。

为避免以后继续混报，测试现在集中分为三条可执行 lane：

- `production_contract`：当前 unified 主链、当前模型 schema 和当前安全边界；
- `compatibility_contract`：仍承诺支持的 legacy API、回滚面与 V2.1 夹具适配；
- `historical_baseline`：冻结评测证据，不代表当前产品行为。

三类 marker 只用于归因，不自动 skip 或 xfail。严格 current fixture 必须通过 `build_current_semantic_payload` 并自行给出 `subject`、`polarity`、`modality`、`request_mode` 和 `explicit_command`；旧 V2.1 数据只能通过 `adapt_legacy_v21_semantic_payload`，兼容推断不会进入当前生产夹具。在线验收子集新增独立的 V2.2 overlay，冻结盲测 runner 和 V2.1 原始矩阵没有为了跑绿而改写。

候选记忆的四条旧普通聊天入口也完成了身份收口：当前 `tools=[]`，原工具保存在 `legacy_tools`，并标记 `retired_from_main_chat=true`。底层 candidate API 的兼容测试继续执行，但不能再被解释为普通聊天生产入口。

聚焦回归过程中出现了第一处新的真实偏离。流程是：

```text
“我今天想学半小时机器学习”
→ awaiting_choice（已知 title=学习机器学习，duration=30）
“把它加入今天计划”
→ 旧逻辑误判为完整新命令
→ 取消 pending
→ add_plan(title="它")
```

根因是 command envelope 虽然只提取到纯指代 payload `它`，fresh-command 抢占判断仍把“payload 非空”当作内容完整。最小修复只在该判断处排除“它 / 这个 / 这件事”等纯指代；具体的新计划内容仍可抢占旧 pending。新增回归同时验证标题、30 分钟时长和 diagnostics 中的 `awaiting_choice -> pending_continuation`，并保留完整新命令抢占旧状态的反向测试。

最终分轨与全量结果：

- production：`724 passed`
- compatibility：`153 passed`
- historical：`77 passed, 2 failed`
- unfiltered：`954 passed, 2 failed`

两项剩余失败都在未修改的冻结 `test_v21_blind_runner.py`：`write_009` 和 `clarify_007` 仍按 V2.1 raw payload 调当前解析器，缺少 `request_mode=execute` 与 `explicit_command=true` 后被安全降为 chat。当前生产 memory-reference 合同和 legacy adapter 都已有独立全绿覆盖，因此这两项只代表历史 runner 与当前 schema 的不兼容，不代表当前产品回归。

## 12. 2026-09-02 “计划一”错误完成第二项

真实故障原句为“我的计划一已经完成了”。当时界面第一项仍未完成，第二项被完成；持久化 diagnostics 显示 Resolver 已选择第二项 UID，后续 ToolExecutor、ToolResult、postcondition 和回复都一致执行第二项，因此不存在执行器的 `+1/-1` 下标错误。

第一处可由生产代码确定并修正的偏离位于对象绑定边界：

- `LocalFeatureExtractor` 的显式计划编号只识别阿拉伯数字，“计划一”没有进入 `explicit_ids`；
- `ReferenceResolver` 原先没有把中文计划序号映射到当前显示列表；
- `BusinessResolver` 在缺少本地结构化序号时，只能继续采用不可信模型候选中的 `match_text/task_ref`；
- diagnostics 出于脱敏目的不持久化完整模型 arguments，因此不能反推模型最初究竟给了标题、数字还是 UID，但可以确认 Resolver 输出时目标已经是第二项。

最小修复没有增加新路由、关键词执行门或模型提示词，而是在既有 `ReferenceResolver → BusinessResolver` 链中补齐 1-based 显示序号绑定：

- 支持“计划一/计划二”“第一项/第二项”“第一个/第二个”及 `第1项/第2个`；
- 将明确显示序号绑定为 `PlanService.list_plans()` 对应位置的稳定 UID；
- 本地明确序号覆盖模型提供的错误目标；
- 越界序号进入澄清，不执行工具；
- “计划1”继续沿用已有持久任务 ID 契约，避免破坏高风险删除确认的参数一致性。

回归过程中确实捕获到一次兼容性回归：初版曾把“计划1”也改写为显示序号 UID，导致“确认删除计划1”与待确认参数 `task_ref=UID` 不一致。该偏离没有被掩盖，现已通过语法分流修正，并增加反向回归。

最终证据：

- Resolver、BusinessResolver、旧删除确认、会话组合根等最终聚焦回归 `24 passed`；
- 语义管线、后置条件和 V2.2 相邻回归曾取得 `122 passed`；
- production `737 passed, 232 deselected`；
- compatibility `153 passed, 816 deselected`；
- 编译检查与 `git diff --check` 通过；
- 真实 DeepSeek `deepseek-v4-flash` 在隔离临时数据根中处理新自然表达，只完成虚拟第一项；diagnostics 的 `selected_object_ids`、工具结果、变更资源和 postcondition 全部对应同一第一项 UID。

本轮没有清理正式计划、修改 `memory.json`、私人配置或 provider，也没有提交或推送。用户在 2026-09-02 明确调整验收分工：代码修复不再要求 agent 每次都完成真实桌面点击，最终 UI 验收可由用户自行执行。因此本节只声明代码、自动回归和隔离在线链通过，不声明真实聊天窗口已复验。

## 13. 2026-09-03 其余五项真实故障收口

本节与第 12 节合起来覆盖本批六项真实验收故障。修复继续沿用既有 `SemanticDecision → Validation → BusinessResolver → ToolExecutor → ToolResult → ResponseComposer` 链，没有新增 provider、第二套路由或架构分支。

### 13.1 时间表候选与多计划批次

真实证据显示，助手生成的闭合时间段安排在 fallback 中得到 0 个候选；用户把同类时间表直接发回并确认“加入计划”时，SemanticDecision 只有一个 `add_plan`，其标题来自 `entities.tasks[0]`，因此第一处数据丢失发生在候选形成阶段。进一步审计发现，多个同名工具即使被展开，校验后的参数回写也会按工具名互相覆盖。

当前在既有中文实体解析器中只识别行首闭合范围，例如“18:00 到 18:30 吃饭”；保留标题与精确时长，不猜 AM/PM，也不把开放范围当任务。包含“或者/还是/二选一”等选择的行保留为 unresolved。语义层把明确事项展开为最多 3 个独立 `add_plan`，会话 pending 保存这组不可变候选，用户确认后仍通过正常工具链逐项执行。同名工具调用的归一化结果按顺序一一回写，不再全部变成最后一项。模糊项和超过 3 项的部分会在确认文案中明确说明“暂不加入”。

助手计划引用复用同一闭合时间表解析；模型候选不足时可形成三个原文候选，但仍要求用户选择具体一项，不直接批量写入。

### 13.2 跨轮记忆槽位

真实对话“我以后要早睡才行 → 你今天打算几点睡？ → 10点你记住”中，正式记忆工具成功执行，但正文仅为“10点”。修复位于既有消息引用补全边界：只有当前会话上一条助手确实提出时间问题、上一条用户消息是非问句、且当前保存正文是纯时间时，才合并为“上一条用户事实，时间是当前值”。无上一轮事实、上一轮不是时间问题或当前正文不是纯时间时均不补写。

### 13.3 中午、昨天与明天

`add_plan.time_slot` 原枚举只有上午、下午、晚上，模型给出的精确“中午”会进入结构修复。当前实体解析与工具 schema 都把“中午”作为独立合法值，使它直接验证通过，不再让修复模型改写业务含义。

计划读链的相对日期原先没有稳定穿过 fixed/business read 路由，且 ResponseComposer 对所有 `show_plan` 成功结果固定显示“今天”。当前解析器补齐昨天/昨日，并在 `show_plan` 候选缺少日期时从本地实体结果补入；最终回复根据 ToolResult 中的实际日期显示昨天、今天、明天或 ISO 日期。

### 13.4 验证与范围

- 新增实体、会话组合根、候选 fallback、批量工具、跨轮记忆、日期和回复标签回归；
- 组件与相邻链：`144 passed`；
- production：`746 passed, 232 deselected`；
- compatibility：`153 passed, 825 deselected`；
- 编译检查与 `git diff --check` 通过；
- 未重跑 frozen historical；其原两项失败仍只作为历史证据；
- 测试均使用仓库外临时数据，没有启动桌宠、写入正式计划/记忆、修改 provider、提交或推送。

用户已选择自行完成最终桌面验收，因此这里明确记录 UI 为“未执行”，不能把自动回归结果描述成真实聊天窗口验收通过。

## 14. 2026-09-03 旧摘要刷新时间被误当成对话发生时间

新会话第一轮“你好啊，新的一天又见面了”曾得到“昨天提到想继续推进 agent 自然语言处理”的回复。持久化 diagnostics 显示当轮实际选中了 3 条相关旧会话摘要，因此“没有任何历史上下文”和“历史工具没有调用”都不是正确根因：相关摘要在模型调用前由 `ChatHistoryManager` 直接读取，不会出现在业务 `tool_calls` 中。

第一处真实错误是时间 provenance 在检索返回边界丢失。摘要存储记录同时具有两种时间：

- `time_range` 是生成摘要所依据消息的实际开始和结束时间；
- `updated_at` 是摘要被生成或重新刷新的时间。

旧 `relevant_summaries()` 只返回 `updated_at`，`ConversationService` 又把它直接写进模型可见提示。一条实际来自 8 月 20 日至 27 日的摘要在 9 月 2 日被刷新后，模型看到的唯一日期就是 9 月 2 日，于是可能把摘要刷新日误当成话题发生日。相关摘要因为普通问候中的重叠短语入选，这可以解释话题来源；但不能证明它发生在昨天。

最小修复保留现有检索和排序策略，只收紧时间事实边界：

- `relevant_summaries()` 返回已保存的来源消息 `time_range`，对旧式或损坏记录安全回退为空范围；
- `ConversationService` 按当前本地日期把单日来源标为 `today`、`yesterday`、`earlier` 或 `future`，跨日标为 `multi_day`，缺失或非法时间标为 `unknown`；
- 模型提示不再包含摘要刷新时间，并明确只有 `today/yesterday` 能支持“今天/昨天”，其余关系只能说“之前/有次”；
- diagnostics 新增不含摘要正文的 `relevant_summary_provenance`，记录 session ID、来源起止时间、摘要刷新时间、来源消息数、日期关系和裁剪状态；
- 终端新增相关旧摘要的入选数量、实际字符数和裁剪状态，避免把只针对当前会话摘要的旧日志误读为没有历史上下文。

回归用例固定复现“2026-08-20 来源消息、2026-09-02 刷新摘要、2026-09-03 新问候”，验证模型上下文只包含 8 月来源时间和 `earlier`，不包含 9 月 2 日刷新时间；diagnostics 则能同时区分两种时间且不保留摘要正文。

最终证据：

- 首轮聚焦回归 `10 passed`；
- 上下文、会话连续性、隔离、真实对话序列和 V2.2 相邻链 `215 passed`；
- production `747 passed, 232 deselected`；
- compatibility `153 passed, 826 deselected`；
- 编译检查与 `git diff --check` 通过；
- frozen historical 未重跑，原历史契约结论不变。

production 首次尝试因机器上不存在文档示例所用的 `C:\Temp` 父目录而在 pytest setup 阶段报 `FileNotFoundError`，没有执行测试正文；创建该普通临时目录并使用全新 `basetemp` 后完整重跑通过。该环境错误不计作生产失败。本轮没有启动桌宠、操控聊天窗口、修改正式计划/记忆或 provider，也没有提交或推送；真实 UI 复验由用户自行执行。

## 15. 2026-09-04 canonical execute、引用入口、pending 与记忆正文边界

本轮依据真实聊天历史与 interaction diagnostics 分别修复四条边界，没有新增 provider、第二套 NLP Router 或中文短语白名单。

### 15.1 canonical add_plan 不再被本地短语二次否决

真实失败中模型已输出 `write/add_plan/execute`，管线结果也是 `execute`，但 `_plan_choice_candidates()` 又依据 `explicit_command` 和少量中文表达模板创建 `awaiting_choice`。当前逻辑只信任验证后的结构化 `request_mode`：所有计划候选均为 `execute` 且没有时间表遗漏项时直接进入 Resolver；`possible_action` 和真实遗漏仍保留澄清。回归同时覆盖非模板表达、愿望不直写和计划授权策略。

### 15.2 assistant plan reference 不再形成前置意图入口

原 `early_assistant_plan_reference` 在 SemanticDecision 前根据原文直接进入候选提取。当前已移除该入口；只有 Normalize/Validate/Repair 后仍是单一 `add_plan/execute`，ReferenceResolver 才能定位上一条 assistant 计划并提取选项。canonical chat 不会被引用关键词改写成计划写入。现有多候选选择、自然中文序号 fallback 和选中后单次工具执行行为保持不变。

### 15.3 scoped pending 提供计划领域

真实 continuation“直接加入”包含已支持的加入动作，但没有重复说“计划/任务/清单”，旧兼容检查因而在 diagnostics 开始前取消 pending。当前 `advice_or_action_choice` 的 scoped state 已明确 `domain=plan` 时，不再要求用户重复领域词；仍要求加入动作、非问题且无否定。回归确认第二轮为 `awaiting_choice -> pending_continuation -> add_plan -> completed`，标题、时段和时长均取自原 pending；普通新话题仍会取消旧状态。

### 15.4 assistant reference source 与 persisted content 分离

旧 `_hydrate_memory_message_reference()` 把 ReferenceResolver 返回的整段 assistant source 直接写入 `candidate.arguments["content"]`，因此总结后的确认说明也会进入正式记忆。当前 previous assistant 引用增加受限结构化提取：模型只能选择 source 中连续出现的用户事实、目标、偏好、约束或计划；程序再验证内容确实来自 source。提取失败、空内容或来源外编造都会进入澄清，不会回退保存原回复。previous user 引用保持原行为。

在线验收基础设施把 SemanticDecision 与 memory reference extraction 分开计数；`write_008` 为一次语义决策加一次受限提取，其余案例提取次数为零。最终证据：相邻回归 `108 passed`，在线隔离验收 `20 passed`，compatibility `153 passed, 829 deselected`，production `749 passed, 1 failed, 232 deselected`。唯一 production 失败是未修改的旧泛化学习澄清文案断言，与上述路径无关。编译和 `git diff --check` 通过；未修改 `llm_client.py`、provider、正式计划、`memory.json` 或 `data/private`，未提交或推送，真实 UI 由用户自行验收。

### 15.5 泛化学习安排不再误入愿望选择门

后续核查确认，上述唯一 production 失败不是过时文案断言，而是结构化语义边界回归。规则路由已经把“给我下午安排学习任务”识别为明确命令，并生成了询问学习主题和时长的澄清内容；但 `_request_mode()` 又因为存在 `clarification_question` 把它统一降为 `possible_action`，会话层随后从整条原文回退出伪标题，覆盖了正确澄清。

当前规则结果显式携带 `request_mode`：裸学习愿望为 `possible_action`，明确安排但缺字段的请求为 `execute`；统一解析器优先采用已经校验的结构化模式。本地模型误判保护同时补齐 `local_wish_guard` 的 `possible_action` 标记。`mode=clarify` 仍负责阻止缺字段请求执行，因此没有降低写入安全边界，也没有增加新的中文关键词判断。

修复后，“我想学 cosplay”仍进入 `advice_or_action_choice` 且不写计划；“唱完了，给我下午安排学习任务”进入 `missing_slots`，明确询问学习内容和时长且不写计划。最终验证：关键边界 `13 passed`，production `750 passed, 232 deselected`，compatibility `153 passed, 829 deselected`，编译和 `git diff --check` 通过。本轮未执行真实 UI，最终桌面验收由用户自行完成。

## 16. 2026-09-04 候选完成死循环、批量完成与建议续接

真实验收暴露了同一条计划交互链上的四类问题：选择“写小说”后仍反复出现“写小说/写小说至少五百字”候选；“都处理”被当成新请求；“前三个”“除了早睡”“写小说和机器学习都完成了”无法形成目标集合；听完建议后“合适安排进计划吧”又回到标准化三选一。

第一处确定性错误不是模型没听懂编号，而是对象身份在两处被重新降级为标题：会话层选中候选 UID 后写回候选标题，BusinessResolver 完成解析后又把唯一计划改回标题交给工具。短标题是长标题的子串时，下一次模糊查找必然重新歧义。当前候选选择和 Resolver 都保留真实数字 ID，ToolExecutor 直接按 ID 完成；标题只用于展示，不再承担已解析对象的身份。

对象选择状态现在支持“都处理/全部完成”和多个明确序号，并把每个选择展开成独立的 `complete_plan` 动作。对当前今日计划的批量表达则先从实时未完成列表绑定目标：“前三个”按当前显示顺序选择；消息中明确出现多个计划标题时绑定这些标题对应的计划；“除了某项都完成”只有在排除提示唯一对应一条计划时才成立。系统会先列出最多三条真实目标并等待“确认”，确认后再经既有 Pipeline、Resolver、工具执行与写入后验证逐条处理；超过三条要求用户拆分，无法唯一绑定时继续走原澄清链，绝不靠猜测直接写入。

建议链没有用新的通用中文关键词路由替代模型。用户在 `advice_or_action_choice` 中选择听建议后，系统先生成自然回复，再调用受限结构化提取，从这条助手原回复中抽取最多三项可执行候选，并验证标题能够锚定原文。后续上下文引用直接消费该会话状态：多项先让用户选择，单项才可直接加入。仅对“你建议”这类已知 pending 的短控制回答保留了局部兼容识别，它不参与开放文本意图判断。

“你好”续写旧睡眠建议属于普通回复相关性问题，不是工具或 pending 泄漏。当前系统提示明确要求最后一条用户输入优先；问候、纠正和新话题必须先响应当前输入，不能视为上一轮确认。

最终将 V2.2 reliability、建议边界、计划交互、postcondition、交互状态、统一语义管线、真实对话序列、BusinessResolver、ToolRegistry、AgentPlanner、TodayPlan 与 GrowthManager 合并运行，结果为 `172 passed`。相关 Python 文件编译通过，`git diff --check` 无错误。本轮没有修改 `llm_client.py`、provider、正式计划、`memory.json` 或私人配置，没有清理现有测试计划，没有提交或推送。按用户当前分工，真实桌面 UI 由用户自行验收，因此本节不宣称桌面复验已通过。

## 17. 2026-09-07 用户进展、记录事实与候选续选

后续真实验收确认了两个尚未闭合的边界。第一，“我今日计划除了早睡都完成了”在前两条计划已经完成、只剩“机器学习”和早睡计划待完成时，排除早睡后只得到一个目标；旧 `_start_plan_batch_completion()` 要求至少两个目标才介入，因此整句落回模型。模型第一次可能给出普通聊天并间接暗示计划都已完成，第二次又可能在看到刚列出的计划后生成单个 `complete_plan`。这种差异不是“有没有说今日计划”造成的确定性规则，而是相同缺口下的上下文与模型输出变化。

本轮用两层修复闭合该问题：

- 排除式、前 N 项和明确多标题完成继续由当前未完成计划绑定真实对象；过滤后只剩一个目标也会进入展示与确认，不再无条件退回普通聊天。
- 新增 `VerifiedTurnContext`，把本轮已核验的语义模式、工具是否执行、经验证修改、用户是否只是在报告现实进展，以及相关计划的本地完成/未完成状态作为只读事实交给回复模型。该上下文不包含计划 ID、文件路径、prompt 或诊断正文；diagnostics 只保留工具数、修改数和计划数，不保留标题。
- `ResponseComposer` 仍是最终公开回复的事实边界。没有工具执行时，模型不能直接或间接声称本地记录已变更；如果模型已经正确区分“用户现实中完成”和“本地记录尚未同步”，自然回复保持不变。这里的有限文本检查只验证输出有没有违反已知事实，不承担开放文本意图识别。

第二，候选列表中选择“1.”完成第一项后，原 `object_selection` 状态会在工具执行前被通用 `missing_slot` 状态覆盖，所以紧接着说“2.这个也完成了”已经找不到上一轮编号。当前执行交接会保留原候选稳定 ID；完成后仅在同一会话、配置的 continuation 延续窗口、明确编号加完成措辞、且该候选仍为本地待完成时允许继续选择。无关的“第二章也可以讨论了”会进入正常语义路由，不会修改计划。

完整 production 首轮得到 `768 passed, 1 failed, 232 deselected`。唯一失败稳定复现于聊天窗口计划完成通知：计划数据已经按稳定数字 ID 完成，ToolResult、状态链和 postcondition 也全部成功，但 `complete_plan()` 的数字 ID 快速分支在通知前提前返回；旧标题匹配分支才调用 `notify_plan_completed()`。这是先前用稳定 ID 消除标题歧义时遗漏的行为对齐，不是 Qt 污染或模型错误。当前数字 ID 分支只在 `changed=true` 时补发同一通知，没有修改 client-action dispatcher 架构。原失败和相关计划执行回归 `6 passed`，随后完整 production 重跑为 `769 passed, 232 deselected`。

其余验证证据：新增直接回归 `7 passed`；候选续选及反例 `6 passed`；V2.2 reliability、VerifiedTurnContext、上下文、回复边界、建议、计划交互、postcondition、交互状态、统一语义、真实对话序列、BusinessResolver、ToolRegistry、AgentPlanner、TodayPlan 与 GrowthManager 合并运行 `141 passed`。相关 Python 文件编译通过，`git diff --check` 无错误。本轮未执行 compatibility/historical 分轨，也没有真实桌面复验，因此不能据此宣布 V2.2 封版。未修改 `llm_client.py`、provider、正式计划、`memory.json` 或私人配置，未清理既有测试计划，未提交或推送。

## 18. 2026-09-07 重复计划决策链与合并能力

真实验收中的“更新计划”“仍然添加：早睡”和“更新原计划”失败来自同一个状态缺口：重复添加只提供了提示文案，却没有保存一套能够继续消费这些选择的明确操作状态。因此后续短句会重新进入开放语义解析，模型生成的 `changes` 可能落到 `add_plan`，原计划目标也会丢失。

当前修复将重复计划处理建成有界状态：候选和待添加参数以稳定标识保存，更新、仍然添加、合并和取消分别进入既有工具链。新增的重复检查为只读操作；新增合并为高风险写操作，必须经过预览和确认。字段冲突先询问，合并写入由 GrowthManager 在单次仓储事务内完成并执行 postcondition 验证。状态合并遵循保守规则：混合完成状态降为待完成，全部完成才保持完成。成功回复来自真实 ToolResult，并明确保留标题和最终状态。

“记住我今天晚上要早睡”同时具有记忆措辞和当天范围，旧链会直接保存为长期记忆。当前只在结构化特征同时确认这两点时建立 `memory_or_plan_destination`，询问加入今日计划还是长期记忆；稳定习惯不受影响。

本轮新增回归覆盖重复项更新、保留添加、合并预览与确认、字段冲突、无重复空态、混合与全部完成状态，以及短期记忆两种去向。一次全量运行还暴露了“归档”计划标题被误当作记忆领域的真实回归；根因是短期记忆去向判断只看领域词，没有要求结构化候选本身是记忆写入。当前已收紧为只有 `save_formal_memory` 或 `request_add_memory` 候选才能建立去向状态，明确 `add_plan` 不会被标题词改道。

最终验证为：相邻回归 `197 passed`；production `780 passed, 232 deselected`；compatibility `153 passed, 859 deselected`；frozen historical `77 passed, 2 failed, 933 deselected`。两条 historical 失败仍固定在 `write_009` 和 `clarify_007` 的旧 V2.1 引用契约。首次不筛选全量与首次 production 的测试正文分别完成 `1008 passed` 和 `780 passed`，但当时用户已启动的 `frontend/pet_app.py` 在长测试期间并发改写正式 `chat_history.json`，私人数据快照因此在 teardown 主动报错；测试未清理或覆盖该数据。次日桌宠关闭且文件不再变化后，production 干净通过。相关 Python 编译与 `git diff --check` 通过。本轮没有清理正式计划、修改 `memory.json`、`data/private`、`llm_client.py` 或 provider，没有提交或推送。

## 19. 2026-09-09 完成、重开、指代修改与无工具成功声明

本轮沿真实桌面链继续追踪“完成后重新打开，再用指代修改”的失败。修复保持既有架构：开放中文语义仍由模型形成 canonical decision，本地代码只负责已知事实、对象身份、schema 修复和执行真实性边界。

### 19.1 模型 chat 误判完成陈述

模型曾把包含完整待完成计划标题的肯定完成陈述判成普通 chat。修复没有建立新的开放式中文意图路由，只在模型已经返回 chat 后执行一条有事实依据的恢复规则：当前计划必须待完成，标题必须完整出现在用户原文中，唯一最长匹配必须成立，并排除否定、疑问和假设。满足这些条件时才生成绑定真实 UID 的 `complete_plan`；标题互为前缀时只选择更长的精确标题。

### 19.2 `changes` 结构错误不再抹掉正确意图

真实 diagnostics 显示模型已经给出 `update_plan`，但 `entities.changes` 是字符串。旧 schema 校验因此以 `schema_invalid_entities` 丢弃整个语义结果，后续 Resolver 和同会话 `last_task` 根本没有机会工作。当前只要求顶层 `entities` 为对象；嵌套 `changes` 交给已有归一化/修复边界处理。恢复值只来自当前用户原文经过 `ChineseEntityParser` 得到的结构化时段和分钟数，绝不执行模型自由文本中的 changes。若原文没有可恢复字段，则强制 clarification、零工具、零写入。

### 19.3 最终回复不能把提示词当安全边界

跨日继续旧验收会话时，9 月 8 日的计划不属于 9 月 9 日“今日计划”范围；模型在没有工具、没有数据变化的 chat 轮里仍回复“已经改到晚上”。落盘记录保持 `completed/下午/30`，diagnostics 为 `chat/tools=0`，所以第一处事实错误在回复阶段。`VerifiedTurnContext` 已经把 `execution.performed=false` 提供给模型，但提示词不能作为最终安全保证。

`ResponseComposer` 现在复用项目已有的中文实体解析结果识别计划写操作，并结合完成式断言标记执行结构约束：没有 ToolResult 时，不能声称已新增、完成、更新、改期、重开、取消、删除或合并计划。询问“要不要改到晚上？”以及建议“可以改到晚上”继续作为普通聊天保留。这里校验的是助手输出与结构化执行事实是否矛盾，不负责判断用户意图。

### 19.4 真实桌面闭环证据

彻底重启后，通过 `roxy.bat --open-chat` 打开生产 PySide6 聊天窗口并新建空白会话 `session_15a92db10266487087799dce597d7973`。本轮使用新的自然表达完成：

1. 新增“整理今天的验收记录”，下午 30 分钟；
2. 将其标记完成；
3. 对刚完成项提出改到晚上，系统零工具并要求先重新打开；
4. 回复“那就重新打开它吧”，执行一次 `reopen_plan`；
5. 回复“现在把它调整到晚上，时间仍然留三十分钟”，执行一次 `update_plan`。

最终聊天回复为“计划已经更新”，成长面板显示该计划待完成、晚上、30 分钟；`today_plan.json` 的同一 UID 也为 `status=pending`、`done=false`、`time_slot=晚上`、`duration_minutes=30`。五轮 diagnostics 的工具和状态与界面、数据一致。证据文件为 `logs/desktop_acceptance_20260908/final_truth_guard_verified_20260909.json`；证据脚本同时移除了写死的 2026-09-08 日期，默认使用当前日期并支持 `--date` 显式回放。

### 19.5 验证与范围

- 最终回复、意图、统一语义、Resolver、模型工具和当前引用链：`134 passed`；
- 新增真实性直接回归及相关引用链：`22 passed`；
- 系统临时 pytest 目录权限错误发生在 setup，使用独立 `basetemp` 后完整通过；
- 本轮没有清理已有测试计划，没有修改 `llm_client.py`、provider、`memory.json` 或私人配置，没有提交或推送。

## 20. 2026-09-11 至 2026-09-12 新用户介绍、成功事实与删除确认事故

用户提供的完整桌面聊天与终端日志暴露了同一责任链上的四类问题：能力询问被宽泛记忆读取规则提前截获；当前聊天轮没有 ToolResult 被错误理解为上一轮成功操作也不存在；模型用澄清文本提出删除时没有建立真实危险操作确认；会话继续后错误工具收到 `task_ref` 并把内部字段展示给用户。固定档案式长期记忆回答又放大了体验问题。

修复没有增加 provider、第二套路由或面向单句的写操作正则。`MemoryDataQueryGuard` 只增加能力询问与否定披露的负向边界，并收紧“我以前告诉过你什么”的过去披露结构。`VerifiedTurnContext` 只把上一项成功工具的操作类型、计划标题和成功状态提供给回复层，不暴露稳定 ID、tool call ID 或诊断正文；当前轮 `execution.performed=false` 只约束当前轮。`ResponseComposer` 在用户明确质疑上一轮结果时以该核验事实答复，并可从后续自然聊天中移除模型生成的虚假撤回段落。

删除澄清现在先经 `BusinessResolver` 绑定当前计划稳定 ID，再调用现有 executor 以 `require_confirmation=True` 创建指纹化 pending。向用户显示的确认标题只取自解析到的真实计划对象，不采用模型写出的标题；“确认”由同一 ConfirmationManager 执行删除并清除待处理链，不再掉入添加计划的通用缺槽流程。Schema 拒绝信息把 `task_ref`、`target_ref`、`match_text` 和 `changes` 映射为公开中文字段，避免内部参数泄露。用户要求避免固定版式时，只在当前 ConversationState 中保存运行期回复偏好，后续正式记忆读取最多选择四条相关事实并自然续问，不写入长期记忆或配置。

本轮还对“我今天要完成简历包装”的真实模型矛盾标注作了有界修复：仅当同一结构化结果同时声明 `explicit_command=true`、`subject=self`、`polarity=positive`、`modality=commitment` 时，才把错误的 `possible_action` 修为 `execute`；desire、hypothetical、question 仍不得直接写计划。

最终审查进一步用现实世界“加入项目群”“完成入职”“删除群聊”、同名新目标、否定/疑问、外部系统和多写入声明做对抗，确保上一条已验证计划成功事实不会串入普通聊天。泛指现实“完成了”不再被当成本地写入；保存、记录、加入等应用写入仍要求 ToolResult；若用户报告 UI 与上一轮成功结果不一致，回复会诚实要求重新读取真实记录，不武断选择任何一方。验证使用仓库外全新临时目录和私有数据防火墙：受影响组件与事故回归 `136 passed`，release closure 与 reliability `88 passed`，核心学习闭环 `127 passed, 1 deselected`，production `849 passed, 241 deselected`，compatibility `151 passed, 939 deselected`。historical 为 `78 passed, 12 failed, 1000 deselected`，失败集合与封版前相同，仍是冻结的复杂合并、复杂修改/上一任务引用、旧记忆直存与 `opt_v2_006` 契约。全范围编译和 `git diff --check` 通过。本轮没有操控桌面重放该会话，也没有运行在线模型，因此这里证明的是程序合同修复，不声称真实 UI 或真实 OpenAI 语义体验已通过；没有修改 `modules/llm_client.py`、provider、`memory.json`、私人配置或正式用户数据，没有提交或推送。

## 21. 2026-09-14 至 2026-09-15 计划续接、复盘快照与隐式历史连续性

用户最新验收对话和日志显示：模型已理解完成意图时，“第二个也搞定了”仍会丢失最近展示顺序；“刚才展示的未完成计划我都完成了”又把目标状态中的“未完成”当成全句否定；批量完成成功后逐项重复同一句回复。后续“另外，今天还要做……”可能被降成开始/建议/加入三选一，只有命令壳的“加入今日计划”则会错误进入执行或询问时长。模型直提复杂修改/合并时还会留下不应存在的 pending。

修复保持“模型判语义、本地核验实体”的边界：只有模型已经选择 `complete_plan` 且当前句出现可解析序号时，才从最近 `ReadSnapshot` 取对应稳定 ID 并重读实时待完成对象；真实对象解析成功后清除原澄清 hint。批量否定检查先剥离“未完成计划”这类目标状态修饰，确认后回复层把多个成功 `complete_plan` ToolResult 聚合成一条。计划新增只在结构化语义为 `self + positive + commitment` 且每项都有具体标题时提升为执行；空命令壳建立只缺 `title` 的 typed pending。默认关闭的修改、合并、改期及模型直提重复检查在建立 pending 前统一诚实降级。

删除工具继续保持模型不可见。若当前句本地特征明确为肯定、可执行、无否定的删除命令，程序才允许把模型识别到的单一删除意图送入现有 Resolver；目标必须解析成唯一实时计划，随后由真实 ConfirmationManager 建立一次不可变确认。行动记录则在业务持久化边界统一去掉命令冒号和“这件事不在计划里”等分类说明，模型直接提供的内容也经过同一清洗。

成长链区分“查看”和“保存”：普通“看看今天完成了什么”只读生成复盘；明确“生成/保存今天复盘”从最新计划和行动保存或修订当天稳定条目，因此月度查看不会再读到旧快照。旧会话主要用于普通对话中的隐式连续性：本地最多选择两条与当前句直接相关的真实摘要片段，泛化词单独重合不召回；当前表达优先，日期/session/source 默认只作内部证据，显式询问来源时才展示。旧片段不会创建长期记忆、计划或行动。

diagnostics 同步修复了配置语义：`enabled=false` 即使存在持久化路径也不写文件；初始记录包含 `pipeline_diagnostics` 和 `pipeline_outcome`，开启时可完整持久化。测试夹具必须显式开启诊断，不能继续依赖旧缺陷。

最终隔离证据为：受影响组件 `94 passed`，桌面运行链与 Agent reliability `77 passed`，历史/上下文 `14 passed`；production `870 passed, 241 deselected`，compatibility `151 passed, 960 deselected`，均 exit 0；historical `77 passed, 13 failed, 1021 deselected`，13 项均在 taxonomy 的冻结旧契约清单内。全范围 `compileall` 和 `git diff --check` exit 0。用户提供了真实对话与日志，但本轮 agent 没有再次操控桌面，也没有运行在线模型；固定 DeepSeek 的旧 runner 未被用来冒充当前 OpenAI provider。没有修改 provider、`modules/llm_client.py`、`memory.json`、私人配置或正式用户数据，没有提交或推送。
