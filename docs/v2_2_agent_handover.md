# RoxyPlan V2.2 Agent 交接文档

初版日期：2026-09-03
最后同步日期：2026-09-17
本轮证据：2026-09-17 新增事故回归、核心闭环、完整 production、compatibility、historical、compileall 与 diff 已完成，最终结果和精确安全快照以 current_status 为准；完整 production 首次原生崩溃记录保留，测试资源修补后复跑通过，但具体崩溃因果未确认；桌面 UI、在线模型和 unfiltered 未运行

本文件用于让后续 agent 在**不重新设计架构、不覆盖现有 dirty worktree**的前提下继续 V2.2 稳定化与验收工作。开始任何新结论前，应先刷新 Git 状态、相关测试和桌面运行状态；本文测试数字均带阶段日期，只是历史已记录证据，不应被表述为当天重新验证的结果。

当前能力范围与推荐人类表达以 [capability_boundaries.md](capability_boundaries.md) 为准，协作与经验流程以 [project_workflow.md](project_workflow.md) 为准，最新结果只在 [current_status.md](current_status.md) 维护。旧交接不覆盖用户新决定；模糊输入先给完整自然句，不借此扩大功能，也不把已承诺快捷能力失败归给用户。

## 1. 一句话状态

2026-09-17 继续收口集合续接、完成状态与回复契约：最近真实计划集合完成统一确认，建议集合保留未消费范围并逐成功结果消费；疑问、数量不符、失效与跨日期引用安全停止且失败不残留 pending。已完成／已删除目标终结而不转为行动记录，精确标题和稳定 UID 不转绑相似／重用 ID 目标。单项删除保留可靠本地一次确认，模型不能把集合删除缩为单项，当前真实标题／ID优先于旧引用。`confirmation_pending` 脱敏投影只说明真实未来操作，不授权已执行断言；回复守卫区分能力介绍与虚假保存。五组新回归及现有候选／无目标删除夹具按新契约同步，没有重分类历史 lane。最终完整结果、首次失败、数据指纹干扰及中断证据须读 current_status，不沿用 2026-09-16 的数字。

RoxyPlan 是 Windows 本地成长陪伴桌宠原型；V2.2 是对既有中文自然语言、工具执行、交互状态和回复真实性链路的稳定化工作流，不是新产品功能立项，也不是封版结论。

2026-09-16 本轮继续用户已批准的记忆与建议引用修复，新增 `modules/memory_read.py`、`modules/suggestion_snapshot.py` 及两组聚焦契约。称呼、偏好、习惯、目标、项目和当前信息按类型从正式证据读取；身份共用正式优先、有效 profile 回退的入口。temporary_state 保留存储但退出工作读取；使用状态仅在最终回答确实引用事实后更新；正文更新刷新标签。读写模型参数说明和合法枚举都来自现有工具注册表。普通真实进展可以建立 typed action_log_offer，纯文本回复不能独自承诺写入。

建议列表快照独立于 pending，“第二个也加入”经既有语义链选择 add_plan 后绑定真实建议标题；同一项仅在成功 ToolResult 后消费，重复确认或有界字段澄清保留内部来源，内部 ID 不进入工具参数。数量表达不能被当成序号，当前句标题及合法时间字段优先；不增加普通计划强制时长，不放宽两轮澄清。两种自然月总结表达、敏感记忆宽泛展示和旧兼容处理器删除按用户决定暂缓。本轮仍未运行在线模型或操控真实桌面。

2026-09-14 至 2026-09-15 又依据用户提供的最新真实对话和日志完成收口：序号同义完成使用最近展示快照的稳定 ID；“未完成计划”不再被误当否定；批量完成回复聚合；后续明确承诺可新增计划；空命令壳只追问标题；显式删除经本地真实对象绑定后只确认一次；行动记录不保存分类说明；明确生成今日复盘会修订最新快照；隐式历史连续性只注入最多两条高相关真实片段；diagnostics 关闭时不再因配置了路径而落盘。最新 production 与 compatibility 均通过；historical 的 13 项失败全部来自 taxonomy 已冻结的旧契约，须单独保留，不能归为当前产品失败。用户已自行提供真实体验证据，但本轮 agent 没有操控桌面或运行在线模型，不能写成新的 UI/在线验收通过。

2026-09-11 封版候选实现已统一以下契约：成功计划读取记录带稳定 ID 与展示顺序的 `ReadSnapshot`，后续序号按当时展示顺序绑定并在写入前实时复核；否定读取但实际求建议的句子保持零工具；当前句明确日期覆盖旧上下文和模型残留日期；最近展示计划的批量完成只等待一次整体确认；同一 pending 最多澄清两轮。隐式普通稳定记忆先询问且确认前零落盘，隐式敏感信息不主动建议保存，显式且内容完整的“记住……”走正式记忆工具。复杂修改、合并与候选/记忆维护工具默认对模型隐藏，越权提议统一诚实降级。历史会话自然查询只返回真实旧摘要、片段、日期或来源；没有匹配时明确说明未找到，不从当前对话编造历史。

成长闭环现已支持全局设置“启动时自动补全昨日成长复盘”：默认开启并与晚间复盘提醒独立。后台只检查昨日，已有记录不覆盖、空日不创建、不调用模型；完整快照含计划稳定 ID、标题、完成状态、时段、时长与行动记录，计划外完成项单列为“计划外行动”。普通失败先写脱敏诊断，连续失败或数据损坏才提醒且不阻断启动；用户明确要求重新生成昨天复盘时才允许修订。成长日志按 `YYYY-MM` 自然月统计，模型只能基于成功工具结果总结，失败时返回确定性统计。

2026-09-02 又完成“计划一错误完成第二项”的 Resolver 最小修复：中文显示序号现在绑定当前计划列表的稳定 UID，并能覆盖模型错误目标；旧“计划1”持久 ID 与删除确认契约保持不变。当前 production `737 passed, 232 deselected`，compatibility `153 passed, 816 deselected`；historical 本轮未重跑，仍保留原两项冻结失败。真实 DeepSeek 隔离链已验证第一项单独完成。用户已调整分工，后续不要求 agent 每次代码修复都亲自点击桌面，用户可自行进行最终 UI 验收；没有实际操作 UI 时必须明确写成“未执行”，不得推断为通过。

2026-09-03 已将同一批真实验收的其余五项故障收口：时间表候选提取、直接多计划批处理、跨轮记忆时间槽补全、“中午”时段保持，以及昨天/明天计划查询与显示。加上上一轮的“计划一”序号修复，本批六项均已有生产回归。当前 production `746 passed, 232 deselected`，compatibility `153 passed, 825 deselected`；组件与相邻链 `144 passed`。本轮未执行桌面 UI，最终验收由用户自行完成。

2026-09-03 又完成旧会话摘要时间归属修复。此前相关摘要确实被读取，但 `relevant_summaries()` 丢弃了已保存的来源消息 `time_range`，模型提示只暴露摘要刷新 `updated_at`；一条 8 月会话在 9 月 2 日刷新摘要后，因而可能被误说成“昨天”。同时终端的 `[Context] summary included` 只代表当前会话摘要，未显示相关旧摘要是否入选。这不是工具调用声明错误：旧摘要读取发生在模型调用前，不属于 ToolExecutor。当前提示只使用来源消息起止时间及其相对当前日期的可信分类；刷新时间只进入不含正文的 diagnostics provenance。production `747 passed, 232 deselected`，compatibility `153 passed, 826 deselected`，相邻链 `215 passed`。未执行桌面 UI，最终验收仍由用户自行完成。

版本口径必须分开：`AGENTS.md` 与 `docs/current_status.md` 都将产品阶段保持为 V0.9 prototype；V2.2 只是语义可靠性与成长闭环的稳定化工作流名称。不得据此擅自改变产品阶段、技术栈或范围。

## 2. 不可突破的边界

- 不新增产品功能、AI provider、语音、数据库、复杂文档解析、Docker、账户、公网部署或云端服务。
- 不修改 `modules/llm_client.py`、`memory.json`、私有本地配置，或 `data/private/` 中的用户数据，除非用户明确指定该文件和变更目的。
- 保持 PySide6 桌宠、聊天、设置和本地数据兼容；Local Web 仅能适配调用既有核心模块，不能复制第二套业务路由。
- 本次 Codex 封版协作使用用户指定的既有 OpenAI provider，不修改 RoxyPlan provider 配置。DeepSeek 只保留为带日期的 RoxyPlan 历史验收证据，不代表本次 Codex provider；`aivalux` 既不是当前或备用 provider，也不得被提议用于新请求。
- 不提交、不推送、不擅自重置、回滚或清理工作树。仓库当前有大量已修改文件，以及历史 `.codex_pytest_*` 临时目录的权限残留；它们均应视为既有用户工作，不能为“恢复干净状态”而删除。
- 自动化测试必须使用完整临时数据根和仓库外 `basetemp`，不得读写、清空、模板覆盖或恢复真实 `memory.json` 与 `data/private/`。

完整规则以 [AGENTS.md](../AGENTS.md) 为准。

## 3. 当前设计与执行契约

自然语言每轮只形成一份 canonical `SemanticDecision`。模型或本地解析只能提出 `chat/read/write/clarify`、意图、实体和候选工具；它们都没有执行权限。执行链必须保持：

```text
SemanticDecision
→ SchemaValidator / SemanticNormalizer
→ BusinessResolver
→ PlanAuthorizationPolicy
→ CapabilityRegistry（模型可见性与执行策略唯一权威）
→ ToolRegistry（参数 schema / alias / handler）
→ ToolExecutor（白名单）
→ ToolResult
→ ResponseComposer / ClaimGuard
```

必须保持的事实边界：

- `BusinessResolver` 绑定真实对象并校验必填字段；`ToolExecutor` 才能执行已注册工具。
- `CapabilityRegistry` 唯一决定 `model_visible`、副作用、风险等级、确认策略和可逆性；`ToolRegistry` 继续拥有 schema 与 handler，并从能力目录投影策略，不能维护第二份独立可见性/策略真相。
- 写入成功的回复必须有匹配的成功 `ToolResult`；没有工具结果时，不得说“已经记下”“已加入”“已经完成”等。
- `awaiting_tool_result` 不是稳定终态。成功、部分成功、需确认、需澄清和普通失败均须收口到对应 Interaction 状态。
- 确认、澄清、序号、上一轮事实和“刚才/它/这个”等引用仅在当前 `conversation_id` 内有效。
- 成功 `show_plan` 的会话快照保留稳定 ID 与展示顺序；后续引用不能按变化后的实时下标重绑，写入前必须读取实时对象复核。
- 否定建议必须零工具；当前句明确日期优先；批量完成确认前零写入且只确认一次；同一 pending 最多澄清两轮。
- 普通聊天不自动保存正式记忆。隐式普通稳定事实先询问且确认前零落盘；隐式敏感事实不主动建议保存。候选记忆底层 API 仍保留，但已退出普通聊天主入口。
- 默认隐藏的复杂工具被模型提议时，不进入执行链，统一以零 ToolResult、数据不变的诚实降级收口。
- 历史会话查询只做本地轻量模糊检索，排除当前会话，并只返回已保存摘要、片段、日期或来源；没有证据时明确说没有找到。
- 昨日自动补齐默认开启且与晚间提醒独立，只处理昨日并保持幂等；自然月总结只能使用成功工具返回的已核验数据。
- Desktop 与 Local Web 共享 `ConversationService`、人格、上下文和业务服务，不维护第二套语义/工具路由。

更完整的状态机见 [interaction_state_machine.md](interaction_state_machine.md)，回复真实性规则见 [response_truthfulness.md](response_truthfulness.md)。

## 4. V2.2 已完成的修复与技术决策

| 问题 | 已采用的最小修复 | 不应回退的理由 |
| --- | --- | --- |
| 已完成本地解析与授权的中风险操作会被模型 confidence 二次拦截 | 增加窄范围、可审计的 `execution_authorized`；只适用于已解析、可逆、low/medium、非 `always-confirm` 工具 | confidence 是概率信号，不是执行权限；高风险、否定、咨询和必须确认的操作仍不能绕过保护 |
| “找不到待完成计划，要不要改记行动”后回复“好”却没有实际写入 | 将该文案和 `action_log_offer` 的 scoped pending 同步保存，再从确认恢复正常 `add_action_log` 链 | 每个选择文案都必须有同构的结构化状态与 immutable arguments |
| 工具失败后状态卡在 `awaiting_tool_result` | 按 ToolResult/业务失败结果显式收口 Interaction | 已结束失败不能污染下一轮上下文 |
| 无 ToolResult 的聊天会声称已写入 | ResponseComposer/ClaimGuard 在最终回复边界拦截无事实依据的写入声明 | ToolResult 是外部事实依据，不是文风建议 |
| 重复计划保护过松或过宽 | 以 canonical 标题加时长/时段兼容性判断；缺时长的同任务进入澄清，显式不同时长可并存 | 不能用模糊匹配误伤不同的真实安排 |
| 否定语句可能误写，或“不要展示计划，我该先做什么”仍触发读取 | 本地命令信封覆盖短/长否定，`SemanticActionParser.local_negation_veto` 保持最终硬拦截；否定读取而实际求建议时固定为 advice/chat 且读写工具均为零 | 否定与建议边界优先于业务名词命中 |
| `show_recent_conversation` 已实现却被模型可见性校验拒绝，且可见性曾有双重真相 | `CapabilityRegistry` 成为模型可见性与执行策略唯一权威；`ToolRegistry` 只保留 schema/alias/handler，并在组装时接收能力策略投影 | 实现、schema 与策略各有单一职责，避免两份白名单漂移 |
| `ToolDefinition` 的 `aliases` / `field_aliases` 重复声明 | 清理重复 dataclass 字段 | 避免后定义静默覆盖前定义 |
| pending 后的“把它加入今天计划”写出标题“它” | fresh-command 抢占判断排除纯指代 payload；具体新计划内容仍可抢占旧 pending | 指代必须续接已确认的标题与时长，不能被误判成新的完整命令 |
| 助手列出“第一、第二、第三”计划后候选选择失败 | 当模型候选不足且上一条助手原文可确定解析时，按自然序号回退解析 | 不放宽中文语义相似度，不让模糊模型输出直接写入 |
| 助手生成的时间段安排无法形成候选 | 在既有计划候选提取层识别闭合时间范围；仍通过 `assistant_plan_selection` 让用户选择 | 只提取原文明确事项，不把开放时间或二选一猜成任务 |
| 用户直接粘贴多项时间表只保存第一项 | 在单一 SemanticDecision 内展开最多 3 个 `add_plan`；同名工具结果按出现顺序回写；确认后恢复为同一有界批次 | 保持单次 3 项上限，模糊项和溢出项明确提示，绝不静默丢失或重复第一项 |
| “早睡”后说“10点你记住”只保存“10点” | 仅当当前会话上一轮确实询问时间、且本轮记忆正文只是纯时间时，将时间槽补回上一条用户事实 | 使用结构化会话上下文，不跨会话、不对普通独立记忆擅自扩写 |
| `中午` 被结构修复为 `下午` | 将“中午”纳入实体解析和 `add_plan.time_slot` 合法枚举 | 精确业务值应直接验证通过，不应交给模型修复猜测 |
| 昨天/明天计划返回今天，或上一轮日期污染“今天的呢” | 相对日期由实体解析补入 `show_plan`；当前句明确日期覆盖 pending、旧读结果和模型残留实体；回复根据 ToolResult 的日期标注 | 当前用户句是日期范围的最高优先级证据 |
| 列表变化后“第二项”按实时下标错绑 | 成功 `show_plan` 保存 `ReadSnapshot` 的稳定 ID 与 `display_order`；后续按展示顺序取稳定 ID，写入前读取实时对象复核 | 用户引用的是看到的列表，不是后来变化的下标 |
| “刚才展示的都完成了”逐项确认或误套新增计划三项上限 | 按快照稳定 ID 绑定可完成目标，展示一次预览并只等待一次整体确认；确认前与确认时复核，失效则整批安全停止 | 批量完成与批量新增是不同契约；确认前必须零写入 |
| 澄清可无限循环 | 每个有界 pending 最多两轮澄清；仍不完整则取消状态、零写入并返回统一可执行示例 | 防止状态黏连和反复追问 |
| 隐式稳定事实自动保存，或主动建议保存敏感信息 | 普通稳定非敏感事实只建立进程内保存确认，确认前不写候选或正式记忆；隐式敏感事实不建议、不建 pending、不落盘 | 长期记忆需要用户知情，敏感信息默认更保守 |
| 模型自由提议复杂修改、合并或候选维护工具 | `update_plan`、`merge_plan`、`reschedule_plan` 及候选/冲突维护等默认 `model_visible=false`；越权模型提议统一返回“本次没有完成”的诚实降级，零 ToolResult、数据不变 | 保留本地兼容能力不等于开放模型规划能力 |
| 昨日复盘遗漏或启动补齐覆盖已有记录 | 启动只检查昨日；有记录原样保留、空日不创建、无模型依赖；普通失败先写脱敏诊断，连续失败或数据损坏再提醒且不阻断启动 | 自动补齐必须幂等、最小且可诊断 |
| 月度成长记录混入相邻月份或由模型补事实 | `show_growth_log` 与统计按 `YYYY-MM` 自然月筛选；自然总结只消费成功 ToolResult 返回的已核验月度 JSON | 月度边界与总结事实来源必须可复核 |

根因、责任链和回归依据详见 [V2.2 真实桌面验收问题经验报告](v2_2_real_desktop_incident_experience_report.md)。

## 5. 测试契约：必须分开报告

每个 pytest 用例由 [tests/contract_taxonomy.py](../tests/contract_taxonomy.py) 归入且只归入一条 lane：

2026-09-16 新增记忆/建议最终聚焦 `35 passed`，核心闭环最终 final-04 `139 passed, 1 deselected`，production final-03 `905 passed, 241 deselected`（826.84 秒），compatibility `151 passed, 995 deselected`，historical `77 passed, 13 failed, 1056 deselected`；编译与原生 diff-check 均 exit 0。完整标题内数量/序号误绑曾实际写入错误目标，已通过标题优先、稳定 ID 来源锁定以及实时顺序变化/失效对象回归修复。核心闭环 final-03 曾为 `138 passed, 1 failed, 1 deselected`：隔离目录保存成长日志出现一次 `PermissionError`，该模块独立复跑 `11 passed`，整组复跑通过；期间未改代码，失败 XML 保留，具体外部权限原因未确认。一轮 production 的 Qt 原生异常中断不计为通过，最终 offscreen 重跑完整 lane 通过，没有删除事故模块。没有通过重分类或删除旧断言隐藏失败；最终记录和仓库外 XML 路径以 [current_status.md](current_status.md) 为准。

本轮开始前的精确补丁基线位于 `C:/Users/16127/.codex/backups/RoxyPlan/v22-memory-snapshot-prework-20260916-095215/`；最终完整 dirty-worktree 安全快照位于 `C:/Users/16127/.codex/backups/RoxyPlan/v22-memory-snapshot-postwork-20260916-161331/`，包含 tracked patch、清单、SHA-256、24 个未跟踪生产/测试/文档副本和最终报告。它不是发布包，已有 `data/pet_config.json` 变更未授权直接打包；没有暂存、提交或推送。

2026-09-15 既有实际结果如下。测试使用仓库外独立 `basetemp`、关闭 pytest cache，并由私有数据防火墙保护正式数据：

| 选择器 / 检查 | 最新结果 | 解释 |
| --- | --- | --- |
| 最新受影响组件 | `94 passed` | 语义、Resolver、回复、行动、成长、历史与 diagnostics |
| Desktop runtime + Agent reliability | `77 passed` | 桌面共用链、能力/记忆边界与状态收口 |
| 历史与上下文 | `14 passed` | 高相关片段、当前句优先、来源时间与上下文预算 |
| `production_contract` | `870 passed, 241 deselected`，exit 0 | 当前生产契约通过 |
| `compatibility_contract` | `151 passed, 960 deselected`，exit 0 | 仍承诺的兼容面通过 |
| `historical_baseline` | `77 passed, 13 failed, 1021 deselected`，exit 1 | 13 项均为 taxonomy 已冻结的旧契约 |
| 全范围 `compileall` | exit 0 | Python 编译通过 |
| `git diff --check` | exit 0 | 仅 LF/CRLF 行尾转换警告 |

historical 的 13 项失败分类为：3 项模型驱动重复检查/复杂合并、6 项复杂修改/上一任务引用、2 项旧记忆直存、2 项 `opt_v2_006`/汇总。这些能力与当前“复杂修改默认隐藏、普通隐式记忆先确认”的安全边界冲突，因此保留为历史证据，不修改生产合同来追求全绿。

unfiltered、本轮 agent 在线模型语义验收与 agent 桌面 UI 均未运行。用户提供了最新真实对话和日志作为故障证据；自动化复验使用隔离结构化语义 fixture。现有在线 runner 固定 DeepSeek，不能在“继续使用现有 OpenAI provider、不得新增或切换 provider”的边界下冒充本轮 OpenAI 验收；本轮没有读取或改写私有 provider 配置，也没有联网、切换 provider 或新增集成。

本次真实会话事故修复保持最小边界：能力询问与否定记忆纠正不再被记忆读取规则抢占；待审核候选在普通聊天不可用时给出真实 UI 路径；上一项成功 ToolResult 以脱敏事实续接，当前无工具聊天不能虚假撤回它；删除澄清由本地稳定 ID 和真实 ConfirmationManager 绑定，展示标题不信任模型文本；内部参数名改为用户可理解的公开字段；“不要固定这种版式回答我”只形成当前会话的运行期展示偏好。没有修改 `modules/llm_client.py`、provider、`memory.json`、私人配置或正式用户数据，没有暂存、提交或推送。

下表保留 2026-08-28 历史分轨快照，只用于说明当时 taxonomy，不代表当前工作树结果：

| Lane | 含义 | 历史记录结果（2026-08-28） |
| --- | --- | --- |
| `production_contract` | 当前统一桌面/服务链、当前模型 schema、安全边界 | `724 passed` |
| `compatibility_contract` | 仍承诺的 legacy API、回滚面、V2.1 夹具适配 | `153 passed` |
| `historical_baseline` | 冻结评测证据；不等于当前产品契约 | `77 passed, 2 failed` |
| unfiltered | 三条 lane 全部执行 | `954 passed, 2 failed` |

marker 只做归因，不会自动 skip 或 xfail。不要将历史失败降级、删除或改写成通过，也不要把它们作为生产回归报告。

在上述 2026-08-28 快照中，当时两项历史失败位于 `tests/test_v21_blind_runner.py`：

- `write_009`
- `clarify_007`

当时的原因是冻结 V2.1 raw payload 未提供当前 schema 所需的 `request_mode=execute` 与 `explicit_command=true`，当前解析器将其安全降为 chat。2026-09-11 taxonomy 已把更多与当前冻结边界不一致的旧复杂能力归入 historical，因此“仅两项失败”不再是当前结论。当前 V2.2 production fixture 和显式 legacy adapter 已各自独立覆盖；除非用户明确要求迁移历史基线，否则不得改写 blind runner 或 V2.1 原始矩阵来“跑绿”。

### 5.1 Fixture 规则

- 当前生产模型响应必须用 [`build_current_semantic_payload`](../tests/semantic_contract_fixtures.py) 构造，并显式给出 `subject`、`polarity`、`modality`、`request_mode`、`explicit_command`。
- V2.1 数据只能经 [`adapt_legacy_v21_semantic_payload`](../tests/semantic_contract_fixtures.py) 进入兼容测试；推断出的默认值不得复制到 current production fixture。
- 当前在线验收字段写在 [`v22_online_semantic_contract.json`](../tests/fixtures/v22_online_semantic_contract.json) overlay 中，不改写 V2.1 源矩阵。
- 普通聊天 golden case 的当前 `tools` 必须为空；旧候选工具仅保留在 `legacy_tools`，并标记 `retired_from_main_chat=true`。

### 5.2 V2.2 封版安全基线

- `tests/test_v22_release_closure.py` 覆盖否定建议零工具、当前句日期优先、`ReadSnapshot` 稳定 ID/展示顺序/实时复核、批量完成一次确认、隐式记忆确认、敏感记忆静默保护、历史会话模糊查询、隐藏能力诚实降级、能力目录权威与两轮澄清上限。
- `tests/test_v22_growth_closure.py` 覆盖昨日复盘设置默认值及与晚间提醒的独立性、补齐幂等/空日/已有记录/损坏与失败规则、自然月隔离统计、Growth UI 和只基于已核验 ToolResult 的月度总结。
- `tests/test_v22_latest_plan_state_regressions.py`、`test_v22_delete_action_regressions.py` 与 `test_v22_latest_growth_regressions.py` 固定最新真实计划续接、删除确认、行动清洗和当日复盘修订故障；`test_v22_history_context.py` 固定最多两条高相关隐式历史片段、当前表达优先及不转长期记忆；`test_interaction_diagnostics.py` 固定显式开关和 pipeline 字段持久化。
- `tests/test_v22_typed_memory_and_suggestions.py`、`test_v22_memory_reference_contract.py` 固定类型化读取、正式身份优先、临时/有效期过滤、最终引用计数、建议连续选择、重复确认来源、数量反例、否定读取与裸写入声明；程序组装测试仍核验工具次数、可见正文和零动作，只将旧 spy 改为共享类型化服务。
- 五个未跟踪生产文件 `memory_read.py / suggestion_snapshot.py / read_snapshot.py / review_backfill.py / verified_turn_context.py` 均为当前运行依赖，交接时必须保留；不代表已经暂存或提交，且正式用户配置不属于发布素材。
- 测试必须使用完整临时数据根、仓库外全新 `basetemp` 和私有数据防火墙；任何专项或单条 lane 通过都不能写成“全部测试通过”。

### 5.3 推荐验证命令

使用项目虚拟环境，禁用 pytest cache，并为每次运行指定一个新的**仓库外**临时目录：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider `
  -m production_contract --basetemp 'C:\Temp\roxyplan-production'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider `
  -m compatibility_contract --basetemp 'C:\Temp\roxyplan-compatibility'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider `
  -m historical_baseline --basetemp 'C:\Temp\roxyplan-historical'
.\.venv\Scripts\python.exe -m compileall -q modules tests scripts
git diff --check
```

在报告结果时，必须同时写明运行日期、lane、确切通过/失败数字，以及是否触碰真实数据。

## 6. 真实桌面验收工作流

**2026-09-02 分工更新：** 下述流程仍是需要真实桌面验收时的标准清单，但不再是每一处代码修复的强制完成门槛。用户可以自行完成最终桌面验收；agent 应完成最小代码修复、受影响回归和必要的隔离在线验证，并准确标注 UI 是否实际执行。

自动化测试不能替代真实桌面验收。用户已明确要求 agent 在需要验收时扮演真实用户：自己发起新的、自然中文多轮对话，观察 UI、终端日志和 `interaction_diagnostics`，失败时保留证据并定位**第一处**偏离。

### 6.1 正确入口

1. 从项目根启动 `roxy.bat`。
2. 通过小桌宠双击两下，选择“打开聊天”。只看到已有聊天窗口不等于验证了桌宠入口。
3. 新建空白会话，不复用可能含 pending 的旧窗口。
4. 使用自己编写的自然中文，不照抄旧测试句；覆盖多轮上下文、指代、澄清、确认、候选选择和真实工具执行。
5. 同时检查聊天显示、终端输出、计划/行动等真实结果，以及 `logs/interaction_diagnostics.jsonl` 中脱敏的状态/路由/工具链。
6. 每次代码修改后彻底重启程序并复验同一风险链。

已验证的定向链路包括：长等待后 pending continuation、助手计划的自然序号候选选择、唯一一次成功 `add_plan`、以及 `idle -> awaiting_choice -> interaction_state -> add_plan` 等状态证据。它们不能替代启动、精确命令、聊天隔离、读写、多步、边界和 Local Web 的完整烟测。

Windows 自动化对无标题 `Qt.Tool` 桌宠不能稳定作为唯一目标。若自动化无法可靠点中桌宠，应明确记录为工具限制，保留已获得的日志/窗口证据；可在用户协助打开新的聊天窗口后继续完成真实对话验收。不得将“进程内自动显示聊天窗口”称为等价的桌宠双击验收。

真实验收产生的计划/聊天记录是用户数据：使用可识别的验收前缀，保留失败当时的完整对话、状态链和日志，不删除正式数据以伪造干净结果。

## 7. 当前工作树与安全操作

当前工作树不是干净提交基线：已有大量跨 `frontend/`、`modules/`、`tests/`、`docs/` 和人格资源的修改，且 V2.2 相关文件中部分尚未被 Git 跟踪。接手者应：

1. 先运行只读 `git status --short` 和 `git diff --check`；
2. 用文件级 diff 和测试证据判断修改归属；
3. 只在当前任务必需的最小文件范围内编辑；
4. 不用 reset、checkout、clean 或批量格式化来“整理”工作树；
5. 不处理既有的重复计划失败或清理那批 pytest 临时目录，除非用户另行授权。

本轮交接文件本身是文档同步，未改变运行代码、provider、私人数据或用户配置。

## 8. 推荐接续顺序

1. **刷新基线**：后续修改后重新确认 Git 状态与受影响 lane，记录新数字，不复述旧数字为新证据。
2. **按用户要求执行桌面验收**：本轮 UI 未执行。只有用户明确要求时，才按 [V2.1 最终交接](v2_1_handoff_final.md) 第 9 节逐项验收；没有实际操作时始终标为“未执行”。
3. **若发现缺陷**：先保存完整聊天、diagnostics、终端和真实数据变化；沿“SemanticDecision → Resolver → Authorization → Executor → ToolResult → Response”找到第一处偏离。先向用户说明症状、责任链和最小修复，再编辑。
4. **修复后验证**：新增针对真实故障的最小回归，运行核心闭环及完整 production、compatibility、historical；历史失败按证据报告，不为追求全绿而改写。unfiltered 只在用户要求或需要额外汇总时运行，不替代分轨报告；桌面复验同样由用户决定。
5. **历史基线只做证据维护**：不为了全绿改写 V2.1 盲测。若用户明确要迁移，单独设计兼容迁移方案并保留原始证据。
6. **最后同步文档**：仅在结果真实、证据可复查时更新 `docs/current_status.md` 和经验报告；不要提前宣布封版。

## 9. 接手前阅读顺序

1. [AGENTS.md](../AGENTS.md)：硬边界与目录规则。
2. [current_status.md](current_status.md)：项目总体状态、隐私边界、未完成烟测。
3. [V2.2 真实桌面验收问题经验报告](v2_2_real_desktop_incident_experience_report.md)：V2.2 根因、修复和测试证据。
4. [tests/README.md](../tests/README.md)：数据隔离、三条测试契约及运行方式。
5. [V2.1 最终交接](v2_1_handoff_final.md)：完整桌面冒烟清单。
6. [chinese_interaction_reliability.md](chinese_interaction_reliability.md)：在线中文验收安全边界和报告格式。

## 10. 交接完成定义

接手 agent 应以“保持当前安全与契约”为完成标准，而不是追求 unfiltered 全绿。V2.2 代码封版结论应基于本轮实际运行的聚焦/核心闭环、production、compatibility、historical、编译、diff 与私有数据防火墙结果；冻结 historical 失败应如实保留。unfiltered 和真实桌面 UI 均按用户要求执行，本轮未运行就明确写“未执行”，不得推断为通过。
