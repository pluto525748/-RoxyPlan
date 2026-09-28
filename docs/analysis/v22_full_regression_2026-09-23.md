# RoxyPlan V2.2 全量回归与真实模型复核（2026-09-23）

## 结论

本轮覆盖除“基本设置”外的 V2.2 主要能力，并使用当前正式 DeepSeek 配置执行真实语义链。初始回归发现 6 类产品缺陷；后续已按产品边界完成小范围修复并重新验证。最新 production 为 `1229 passed, 1 skipped, 243 deselected`，可以作为当前生产契约证据；compatibility 和 historical 的旧冻结契约仍单独报告，不冒充当前生产失败。

本报告只记录合成测试语句、结果类别和责任代码，不记录正式用户的计划、记忆或历史会话正文。

## 后续修复闭环

- 明确开始／启动计时的请求在语义模型前由类型化安全门拦截，固定零工具、零计划写入并诚实降级。
- 功能帮助分类不再把通用“可以吗／能……吗”当成帮助目的；计划查询、建议聊天和已验证建议快照重新进入各自语义链。
- 自定义正式记忆属性仍可类型化读取，但包含嵌套疑问结构的普通问题不再升级成记忆查询。
- `show_growth_log` 接受模型产生的“本月／这个月／当前月／当月”，统一映射到当前 `YYYY-MM`。
- “接下来先别提醒我”等完整提醒控制请求走确定性工具链；回复边界在没有 ToolResult 时拒绝声称状态已改变。
- 桌宠休眠保留为对话能力；“洛琪希，你休息一下”明确执行，普通“我休息一下”仍是聊天。操作说明已同步为明确表达。
- 候选记忆和正式记忆展示的三项旧 production 测试契约已同步到当前产品决定，没有恢复候选入口或强制本地模板主路径。

验证结果：聚焦相邻组合 `187 passed`；初始 6 个 production 失败点及新增契约 `18 passed`；完整 production `1229 passed, 1 skipped, 243 deselected`；compatibility `149 passed, 2 failed, 1322 deselected`，两项均为旧 `multi_004` 混合指令契约；historical `79 passed, 13 failed, 1381 deselected`，13 项均为已冻结旧能力。Python 静态编译和 `git diff --check` 通过。

当前 DeepSeek 对六组表达各重复三次：计划查询每次成功执行 `show_plan`；建议表达和普通问题每次保持 chat／零工具；本月查询每次成功执行 `show_growth_log`；计时请求每次诚实失败且零工具；明确桌宠休眠每次产生成功 `sleep_pet` ToolResult 和 `sleep` 客户端动作。测试前后正式计划数量均为 3。

## 验证范围与证据

### 自动化分层

| 分层 | 结果 | 证据文件 |
| --- | --- | --- |
| production | 1218 passed, 6 failed, 1 skipped, 243 deselected | `C:\Users\16127\AppData\Local\Temp\roxy-full-production-20260923.xml` |
| compatibility | 148 passed, 3 failed, 1317 deselected | `C:\Users\16127\AppData\Local\Temp\roxy-full-compatibility-20260923.xml` |
| historical | 78 passed, 14 failed, 1376 deselected | `C:\Users\16127\AppData\Local\Temp\roxy-full-historical-20260923.xml` |

production 不是全绿，不能沿用旧证据声称当前工作树已经通过全量回归。historical 的旧冻结失败单独保留，不与当前生产失败混算。

### 真实 DeepSeek 链

- 首轮：44 个连续回合，报告 `C:\Users\16127\AppData\Local\Temp\RoxyPlan-full-regression-20260923-154905\full_chain_results.json`。
- 定向复核：18 个回合，报告 `C:\Users\16127\AppData\Local\Temp\RoxyPlan-full-recheck-20260923-155244\recheck_results.json`。
- 两轮都由真实 `AgentService`、当前 `deepseek/deepseek-flash`、正式计划／行动／成长／记忆／历史存储组成，不是只调用下游工具的模拟。
- 首轮记录器错误读取了不存在的 `ToolResult.changed_resource_ids` 属性，导致 23 个实际成功的工具回合被报告脚本记成 `AttributeError`。这属于测试记录器缺陷；工具结果、开发日志和最终数据仍可核验，第二轮已用正确字段复核关键路径。

### 数据影响与清理

- 合成计划和临时正式记忆已精确清理；计划数量恢复到测试前 3 条，正式记忆数量恢复到测试前 21 条。
- 误路由产生的计划 ID `7`、UID `task_321b4f2cb3ef4a948f091c7392831958` 已按 UID 核对后删除。
- 按用户授权保留了 1 条测试行动记录、测试会话历史和本轮生成／修订的今日复盘。当前产品没有安全的逐条行动／会话回滚入口，因此未直接改写底层文件。
- 未修改 provider、`modules/llm_client.py`、`memory.json` 或私人配置；未提交、未推送。

## 真实模型确认的产品缺陷

### P0：明确“开始计时”会被错误写成今日计划

复现语句：`现在开始计时25分钟，直接替我启动。`

- 一次真实调用返回澄清；另一次真实调用错误执行 `add_plan`，创建标题为“计时25分钟”的计划，并声称已加入。
- 这是同一句话跨调用结果不稳定，而且错误路径产生了真实写入。
- `ConversationService._V22_DEGRADED_PLAN_TOOLS` 只包含复杂计划修改类工具（`modules/conversation_service.py:173`），没有对明确计时目的做前置拦截。
- 当前工具目录没有可靠计时能力；模型一旦把请求错报为 `add_plan`，现有降级门只检查“模型最终提出的工具”，无法根据原始用户目的否决计划写入（`modules/conversation_service.py:1027`、`modules/conversation_service.py:6730`）。
- 修复方向：在语义候选进入计划授权前增加“明确开始／启动计时目的”类型校验。若模型提出 `add_plan`，但原句目的为计时，必须零写入并返回现有降级说明。不能靠继续增加计划标题正则来修。

### P1：功能帮助识别范围过宽，吞掉真实查询和快照引用

复现语句：`请今日计划可以吗？`

- 被判为 `capability_help`，没有执行 `show_plan`，只追问用户是不是想查看计划。
- `CapabilityRegistry.is_user_help_query()` 将通用的“可以吗”“能……吗”与任意计划／记忆词组合都当成功能咨询（`modules/capability_registry.py:107`、`modules/capability_registry.py:126`）。
- `ConversationService` 随后直接绕过语义执行并强制改成 chat（`modules/conversation_service.py:849`、`modules/conversation_service.py:896`）。
- 同一过宽规则还破坏“能把你说的加入今日计划吗”这类已保存建议快照的引用链。
- 修复方向：帮助分类必须要求明确“能力／方法目的”，不能仅凭疑问语气；已存在待办状态、已验证建议快照或明确读写动作时，类型化状态应先于帮助分类。

### P1：普通问题被误判为长期记忆查询

复现语句：`你知道我的问题出在哪吗？`

- 实际调用 `list_memories`，参数被解析为 `query_mode=existence`、`attribute=fact`、`topic=问题出在哪`。
- `MemoryDataQueryGuard.route()` 在做更窄的记忆域判断前，先接受 `MemoryReadRequest.from_user_text()` 的类型化结果（`modules/memory_data_query_guard.py:50`、`modules/memory_data_query_guard.py:62`）。
- `MemoryReadRequest` 的泛化“我的 + 主题 + 是什么／有哪些／吗”规则会把普通上下文问题当成稳定用户属性（`modules/memory_read.py:168`）。
- 修复方向：类型化记忆读取应同时满足“用户稳定事实／正式记忆目的”，普通的“我的问题／我的意思／我的话”必须留在聊天；应通过语义类别和最小反例契约解决，而不是无限列禁词。

### P1：自然月参数未统一规范化

对照语句：

- `查看本月成长日志，只给真实统计。` → `show_growth_log(month="本月")`，工具失败。
- `这个月我真实记录了哪些成长？` → 参数规范为 `2026-09`，工具成功。

工具 schema 只限制字符串长度，没有声明或统一执行 `YYYY-MM` 规范化（`modules/tool_registry.py:1350`）；处理器原样把参数交给成长模块（`modules/tool_registry.py:773`），而成长模块严格要求 `YYYY-MM`（`modules/growth_manager.py:620`、`modules/growth_manager.py:782`）。

修复方向：把“本月／这个月／上月／明确年月”的解析放进统一结构化参数规范化层，工具执行前只允许标准月份或空值；不能让同义表达依赖模型是否主动换算。

### P1：提醒控制存在无工具的虚假成功

对照语句：

- `先别提醒我。` → 能提出 `pause_reminders`。
- `接下来先别提醒我。` → 普通 chat，回复“好，那我不提醒你”，没有 ToolResult。

本地确定性入口只接受精确短句或有限词组（`modules/intent_router.py:473`、`modules/intent_router.py:830`）。带自然前缀的等价表达落回模型聊天后，真实性保护没有阻止“已暂停”式承诺。

修复方向：提醒控制先形成类型化 `reminder_control(action=pause|resume)`，再要求成功 ToolResult 才能声称状态已改变；未执行时必须明确说明没有完成并给出合法表达。

### P2：功能说明承诺“休息一下”，路由却不支持

复现语句：`休息一下。`

- 返回普通聊天，没有 `sleep_pet` 或客户端动作。
- 能力说明明确把“休息一下”列为可用表达（`modules/capability_registry.py:239`），但本地睡眠词组只有“进入睡眠／睡一会儿／去睡吧”（`modules/intent_router.py:824`）。
- 修复方向：能力目录、提示词、路由和测试必须共享同一个受支持表达契约；如果不准备支持，就从用户说明删除，不能继续承诺。

## 自动化失败分类

### 当前核心缺陷

1. `test_chinese_context_matrix.py::test_memory_query_guard_uses_minimal_contrasts_not_broad_keywords`：对应“你知道我的问题出在哪吗”误读记忆。
2. `test_real_conversation_sequences.py::test_real_service_keeps_wrapped_queries_read_only_and_stable`：对应“请今日计划可以吗”被帮助分类吞掉。
3. `test_v22_collection_continuation_regressions.py::test_assistant_reference_reuses_full_verified_snapshot_without_shrinking`：非活动状态下的已验证建议快照被帮助分类吞掉；正常即时 `awaiting_choice` 链在真实模型测试中成功。

### 当前测试契约需要同步，不应恢复旧产品行为

1. 候选记忆旧用例仍期望候选工具，但候选记忆已退出当前用户交互。
2. Local Web 旧用例仍要求正式记忆完全本地模板回答，但当前确认设计是“已核验正式记忆交给 DeepSeek 自然组织，本地只作失败兜底”。
3. `chat_022` 要求每个安全建议句都必须调用一次语义模型；当前功能帮助门零工具、零写入，属于测试对内部调用次数的旧约束，而非用户可见错误。

### compatibility / historical

- compatibility 的两个 `multi_004` 失败来自旧混合指令“记住目标，但不要加计划”契约与当前保守多意图策略冲突，需要产品契约选择，不直接算当前核心故障。
- historical 共 14 项失败，其中 13 项是已知冻结的 V2.1 旧能力；新增 1 项就是本轮确认的普通问题误读记忆。

## 已通过的主要真实链

- 闲聊、身份回答、否定计划读取／建议、功能介绍均保持零工具。
- 一次添加 2 项和 3 项计划、后续继续添加、查看计划、按真实展示序号完成、按标题完成、明确单项删除与确认均成功。
- 昨天／今天计划日期查询成功；复杂修改／合并能诚实降级且零写入。
- 行动记录新增与读取、今日复盘生成与保存成功。
- 明确长期记忆保存、类型化读取、宽泛正式记忆读取、`忘记：完整内容` 预览／确认／删除成功；否定保存零工具；候选记忆入口按退役边界拒绝。
- 历史会话命中和无匹配路径都由真实工具返回。
- `来段舞`、`醒醒` 已确认产生成功 ToolResult 和客户端动作请求，但没有验证桌宠是否真的播放动画。

## 尚需真实桌面验证的部分

本轮 `AgentService` 组合没有桌宠控制器，因此精确的暂停／恢复提醒返回 `pet_unavailable`，不能据此判断正式桌宠运行时是否失败。跳舞和唤醒只验证到了客户端动作请求，未验证动画、窗口或宠物状态变化。这三项应在修复上述语义缺陷后，从 `roxy.bat` 启动并通过“小桌宠双击两下 → 打开聊天”的真实入口验收。

## 静态检查

- `compileall`：通过，缓存输出到仓库外临时目录。
- `git diff --check`：通过；仅有现有 LF/CRLF 提示。
- 工作树仍包含大量既有修改、未跟踪生产文件和历史 pytest 临时目录；本轮未清理、未暂存、未提交、未推送。

## 已执行的修复顺序

1. 已封堵“计时请求写成计划”的错误写入。
2. 已收窄帮助分类，恢复计划查询和已验证建议快照引用。
3. 已修复普通问题误读长期记忆。
4. 已统一本月参数规范化。
5. 已为提醒控制补上 ToolResult 真实性约束，并同步明确的桌宠休眠表达。
6. 已同步三项过时 production 契约，并完成 production、compatibility、historical 分层回归和真实 DeepSeek 三轮验证。
