# RoxyPlan V2.2 代码与框架学习指南

> 适用对象：理解 Python 类的基本概念，但独立阅读和分析项目代码仍有困难的学习者。
> 代码基线：2026-09-03 当前 dirty worktree。
> 文档用途：交给 ChatGPT 作为长期教学依据，同时供学习者查阅。
> 范围：现有架构、模块职责、调用链、本地规则、状态、工具、数据与诊断；不重新设计架构，不介绍尚未实现的新功能。

## 0. 给 ChatGPT 的教学约定

当 ChatGPT 使用本指南指导学习时，请遵守以下约定：

1. 默认学习者知道类、对象、函数和基本 Python 语法，但不能稳定地独立追踪跨文件调用。
2. 一次只讲一个明确主题，先讲它解决什么问题，再讲对象关系，最后进入关键函数。
3. 所有结论优先以当前工作树代码为准；文档与代码冲突时，要指出冲突并回到代码核验。
4. 讲解一个文件时，需要同时说明：谁创建它、谁调用它、它依赖谁、它返回什么、它不能做什么。
5. 不要求学习者背正则表达式或整段代码。重点是识别入口、数据结构、状态变化和安全边界。
6. 不生成小型排错练习、练习答案或自查清单。可以围绕学习者提出的真实疑问逐行解释。
7. 不读取或复述 `memory.json`、`data/private/`、密钥、私人聊天和真实计划内容。示例必须使用本文中的脱敏内容。
8. 不把模型输出当成事实。涉及写操作时，必须沿 `ToolResult` 和持久化后置条件确认结果。
9. 推荐先让学习者打开本文指出的文件和函数，再解释 20-80 行相关代码；不要一次倾倒整个大文件。
10. 学习目标是让学习者以后能定位小问题并尝试最小修复，而不是把所有业务逻辑重新实现一遍。

可以把下面这段话直接发给 ChatGPT：

```text
请以 docs/learning/roxyplan_v2_2_code_learning_guide.md 为课程总纲，结合当前工作树中的真实代码指导我学习。一次只讲一个主题。先说明该模块解决的问题、上下游和关键数据结构，再带我阅读相关函数。不要出练习题、答案或自查清单，不要读取私人数据，也不要假设代码一定与旧文档一致。
```

## 1. 先建立一个正确的总体印象

RoxyPlan 当前是一个 Windows 本地 PySide6 桌宠与聊天原型。它同时包含四类能力：

- 桌面表现：桌宠窗口、聊天窗口、设置、动画、气泡和提醒。
- 对话理解：判断普通聊天、查询、写操作、澄清、确认、取消和多轮指代。
- 业务能力：计划、行动记录、复盘、长期记忆、会话历史和桌宠动作。
- 基础设施：模型路由、本地 JSON 仓储、人格包、上下文构建、诊断日志和 Local Web 适配层。

最重要的架构原则是：**模型只能提出语义建议，不能直接写数据或操控桌面。**

一条自然语言请求的主链可以先记成：

```text
聊天窗口收到文字
  -> ConversationService 统一编排
  -> 本地特征 + 单次 SemanticDecision
  -> 规范化、校验和必要修复
  -> BusinessResolver 绑定真实对象
  -> PlanAuthorizationPolicy / SafetyPolicy 决定是否允许或确认
  -> ToolExecutor 只执行 ToolRegistry 白名单工具
  -> ToolResult 记录真实结果
  -> ResponseComposer 根据真实结果生成最终可见回复
  -> UI 展示文字或执行声明式桌宠动作
```

这里有三类“真相”，学习时不要混淆：

| 真相类型 | 代表内容 | 权威来源 |
| --- | --- | --- |
| 用户表达 | 用户说了什么、是否否定、是在咨询还是命令 | 当前消息、同会话结构化状态 |
| 业务事实 | 某计划是否存在、记忆是否保存、工具是否成功 | Service、Repository、`ToolResult`、后置条件 |
| 自然回复 | 如何把结果说得自然 | 模型与 `ResponseComposer`，但不能覆盖业务事实 |

## 2. 项目目录与启动方式

### 2.1 启动入口

[`roxy.bat`](../../roxy.bat) 做两件事：切换到项目根目录，然后使用项目自己的 `.venv` 运行 [`frontend/pet_app.py`](../../frontend/pet_app.py)。

`frontend.pet_app.main()` 创建 `QApplication`，再创建 `DesktopPet`。聊天窗口不是一开始就独立启动，而是作为 `DesktopPet` 的 `chat_factory`，在需要时创建或聚焦。

真实入口链：

```text
roxy.bat
  -> frontend.pet_app.main()
  -> DesktopPet(chat_factory=...)
  -> 用户双击桌宠
  -> DesktopPet.mouseDoubleClickEvent()
  -> DesktopPet.open_chat_window()
  -> ChatWindow(...)
```

### 2.2 目录职责

| 目录 | 当前实际职责 |
| --- | --- |
| `frontend/` | PySide6 桌宠、聊天窗口、设置和桌面动作派发 |
| `modules/` | 对话、语义、状态、工具、计划、记忆、人格、知识和模型等核心模块 |
| `modules/llm/` | 在线/离线模型 Provider、模型路由、设置、密钥读取和用量记录 |
| `modules/repositories/` | 业务层与本地 JSON 文件之间的仓储接口和实现 |
| `server/` | Local Web 适配层；复用核心服务，不复制业务逻辑 |
| `data/personas/` | 人格包的固定核心、行为、文风、关系与示例素材 |
| `data/private/` | 本机私人运行数据；学习材料不读取正文、不拿来做测试 |
| `tests/` | 生产契约、兼容契约、历史基线、隔离测试和验收工具 |
| `docs/` | 状态、交接、设计边界与学习材料 |

### 2.3 当前工作树意味着什么

当前分支是 `feature/v2.0-conversation-continuity`，但工作树叠加了大量未提交的 V2.2 稳定化修改。因此：

- 分支名不能代表当前全部功能。
- `AGENTS.md` 的产品阶段表述、V2.1 文档和 V2.2 工作流名称可能同时存在。
- 学习时以当前文件内容为准，不根据版本名称猜实现。
- 不使用 `git reset`、`checkout` 或批量格式化把工作树恢复成旧状态。

## 3. 对象是怎样组装起来的

理解 [`ChatWindow.__init__`](../../frontend/pet_app.py) 是理解整个项目框架的最快入口。它承担“组合根”职责：创建对象并把它们连接起来，而不是独自实现所有业务。

主要装配顺序如下：

```text
MemoryService / MemoryManager / MemoryRetriever
KnowledgeManager
GrowthManager -> PlanService
ChatHistoryManager -> ContextBuilder
PersonaRegistry
RoutedLLMClient -> IntentRouter
ToolRegistry + ConfirmationManager + SafetyPolicy -> ToolExecutor
AgentPlanner -> AgentCore
InteractionStateCoordinator
LocalFeatureExtractor -> SemanticActionParser
BusinessResolver
ResponseComposer
ConversationService（把以上对象统一编排）
```

这种写法属于依赖注入的朴素版本：`ConversationService` 不在内部重新创建数据库、模型和计划服务，而是接收已经创建好的对象。因此测试可以换成临时 Repository、固定时钟或 Stub 模型。

### 3.1 为什么 UI 要分 `prepare()` 和 `complete()`

聊天窗口在 UI 线程先调用 `ConversationService.prepare()`。如果结果需要模型，`ChatReplyWorker` 会在 `QThread` 中调用 `complete()`，防止在线模型请求冻结界面。

```text
ChatWindow.send_message()
  -> handle_agent_request()
  -> ConversationService.prepare(... allow_llm_intent=False)
  -> 如果需要模型：start_ai_reply()
  -> ChatReplyWorker.run()
  -> ConversationService.complete()
  -> finish_ai_reply()
  -> _present_agent_response()
```

`ConversationTurn` 是 prepare 与 complete 之间的载体。它可以携带立即响应、待调用的模型消息、助手计划引用来源以及 request_id。

## 4. 第一重点：本地规则到底怎样约束模型

“本地规则”不是一个单独文件，而是一组分布在不同阶段、职责不同的约束。判断问题时必须先确认是哪一道规则生效。

### 4.1 第一层：Feature Flag 决定启用哪条架构路径

文件：[`modules/feature_flags.py`](../../modules/feature_flags.py)、[`data/pet_config.json`](../../data/pet_config.json)

当前关键开关包括：

- `unified_semantic_parser_enabled=true`
- `interaction_coordinator_enabled=true`
- `business_resolver_enabled=true`
- `action_batch_enabled=true`
- `deterministic_response_enabled=true`
- `legacy_intent_path_enabled=false`
- `unified_client_action_dispatcher_enabled=true`
- `legacy_direct_pet_action_enabled=false`

`validate_feature_flags()` 只报告危险组合，不替代业务验证。`compatibility_rollback_profile()` 提供回滚组合，并刻意避免同时开启两条写路径。

### 4.2 第二层：固定命令和命令外壳

文件：[`modules/intent_router.py`](../../modules/intent_router.py)、[`modules/local_feature_extractor.py`](../../modules/local_feature_extractor.py)

精确、低歧义的固定表达可以走确定性路径，例如明确查看计划、确认或取消。开放式自然语言则交给统一语义判断。

`LocalFeatureExtractor._extract_command_envelope()` 会把“命令外壳”和“用户正文”分开。例如：

```text
请记住我偏好安静的学习环境
```

会被理解成：

```text
command_text = 请记住
payload_text = 我偏好安静的学习环境
polarity = command
```

这样做是为了防止正文里的“删除”“跳舞”“计划”等词再次被当成另一条命令。正文是需要保存的内容，不是给路由器重复扫描的控制语言。

该层还会识别：

- 操作词：添加、查看、完成、删除、保存等。
- 领域词：计划、记忆、行动、成长、桌宠、会话。
- 查询、建议、否定、确认、取消、风险和连接词。
- 日期、时段、时长、序号、显式 ID 和指代表达。
- 当前句子更像问题、命令还是普通陈述。

重要边界：`LocalFeatureExtractor` 的类注释明确说它**提取稳定信号，但不决定工具或资源 ID**。

### 4.3 第三层：中文实体解析

文件：[`modules/chinese_entity_parser.py`](../../modules/chinese_entity_parser.py)

`ChineseEntityParser` 专注计划时间与引用实体：

- 把今天、明天、后天、昨天转换成 ISO 日期。
- 把上午、中午、下午、晚上转换成时段。
- 解析“下午三点”“半小时”“两个小时”等。
- 识别“下班后”这类仍缺具体时间的事件锚点并要求澄清。
- 清理计划标题外围的礼貌词和命令词。
- 从多行文字提取闭合时间段，例如 `18:00 到 18:40 阅读资料`。
- 遇到二选一、开放区间或无效时间时不擅自猜测。

这是“规则比模型更可信”的典型位置：时间计算、合法范围和相对日期适合由确定性代码完成。

### 4.4 第四层：唯一的 `SemanticDecision`

文件：[`modules/semantic_action_parser.py`](../../modules/semantic_action_parser.py)、[`modules/intent_router.py`](../../modules/intent_router.py)

标准入口是 `SemanticActionParser.parse_unified()`。它要求一轮自然语言只形成一份 canonical `SemanticDecision`。

核心字段包括：

| 字段 | 含义 |
| --- | --- |
| `mode` | `chat`、`read`、`write`、`clarify` 或 `multi_action` |
| `intent` | 业务意图，例如 `add_plan`、`show_plan` |
| `entities` | 标题、日期、时长、引用等参数 |
| `subject` | 动作主体，例如用户自己还是他人 |
| `polarity` | 正向或否定 |
| `modality` | 命令、愿望、假设、建议等语气 |
| `request_mode` | chat、advice、query、execute、correction 等 |
| `explicit_command` | 是否明确要求执行 |
| `tool_calls` | 模型提出的候选工具；仍然不是执行结果 |

`parse_unified()` 会再次实施硬边界：

- 本地检测到否定时，清空所有副作用候选，形成 `local_negation_veto`。
- advice/discuss 不能保留写工具。
- 主聊天入口禁用旧的候选记忆意图。
- 多项时间表最多展开为 3 个动作。
- 模糊愿望只能形成 `possible_action`，不能伪装成已授权执行。
- `chat` 且没有工具时不能保留伪澄清状态。

模型在这里是“不可信提案者”。即使返回了 `add_plan`，后续各层仍可归一化、拒绝、澄清或要求确认。

### 4.5 第五层：Normalize -> Validate -> Repair

文件：[`modules/semantic_normalizer.py`](../../modules/semantic_normalizer.py)、[`modules/schema_validator.py`](../../modules/schema_validator.py)、[`modules/structure_repair.py`](../../modules/structure_repair.py)、[`modules/semantic_pipeline.py`](../../modules/semantic_pipeline.py)

`SemanticPipeline.process()` 的职责顺序是：

```text
原始 SemanticDecision
  -> DeterministicNormalizer
  -> SchemaValidator
  -> 如果错误可修复：StructureRepairer
  -> 再次 Normalize
  -> 再次 Validate
  -> EXECUTE / CLARIFY / REJECT / CHAT_ONLY
```

各文件的区别：

- `DeterministicNormalizer`：工具别名、字段别名、枚举别名和安全可忽略字段归一化。
- `SchemaValidator`：按 ToolRegistry schema 检查必填字段、类型、枚举、长度、范围和工具可见性。
- `StructureRepairer`：只修可恢复的结构问题；不能把否定、无授权或不存在的业务对象“修”成可执行动作。
- `SemanticPipeline`：管理整个结果状态和诊断信息。

这层约束的是**结构正确性**，不是“计划一到底是哪条真实计划”。真实对象解析属于下一层。

### 4.6 第六层：BusinessResolver 绑定真实对象

文件：[`modules/business_resolver.py`](../../modules/business_resolver.py)、[`modules/reference_resolver.py`](../../modules/reference_resolver.py)

模型可能说“完成计划一”，也可能给出错误标题或临时编号。`BusinessResolver` 不能直接相信它，而要调用 `PlanService`、`MemoryService` 和 `InteractionStateCoordinator` 查真实对象。

它处理三类结果：

- `resolved`：唯一对象和参数已经确定。
- `ambiguous` / `clarification_required`：候选不唯一或字段缺失。
- `forbidden` / `not_found`：重复、越界、不存在或违反业务约束。

`ReferenceResolver` 负责把同会话中的“第一个”“刚才那个”“它”等，绑定到结构化候选或稳定 UID。当前实现中特别区分：

- “计划一 / 第一项”是当前可见列表的 1-based 序号。
- “计划1”保留旧式持久任务 ID 语义。
- 明确序号比模型猜出的错误目标优先。
- 超出候选范围必须澄清，不能默认选择第一项。

### 4.7 第七层：Capability 和 ToolRegistry 是能力白名单

文件：[`modules/capability_registry.py`](../../modules/capability_registry.py)、[`modules/tool_registry.py`](../../modules/tool_registry.py)

`CapabilityRegistry` 描述产品“允许有什么能力”：领域、请求模式、工具名、风险、确认策略、是否支持批量/引用/澄清以及是否对模型可见。

`ToolRegistry` 描述“具体怎样调用”：

- 工具名称与别名。
- 参数 schema 与字段别名。
- 实际 Python handler。
- 风险等级、是否有副作用、是否可逆。
- `never`、`when_ambiguous`、`always` 确认策略。
- 是否允许模型看到该工具。

二者的关系是：Capability 是产品能力上界，ToolDefinition 是可执行合同。一个 handler 已经写好，并不等于模型就能使用；它还必须同时通过模型可见白名单和 capability 可见性。

### 4.8 第八层：PlanAuthorizationPolicy 和 SafetyPolicy

文件：[`modules/plan_authorization_policy.py`](../../modules/plan_authorization_policy.py)、[`modules/safety_policy.py`](../../modules/safety_policy.py)

`PlanAuthorizationPolicy` 根据结构化语义判断计划类写操作属于：

- `EXECUTE`：确实是用户本人、正向、明确执行。
- `PENDING`：像愿望、建议转操作或仍有歧义，需要选择/确认。
- `NO_ACTION`：否定、假设、他人动作、普通讨论等，不执行。

`SafetyPolicy` 更靠近工具层，综合工具风险、置信度、歧义和确认策略。高风险删除类工具通常必须确认，中风险工具在歧义时确认，纯读取通常直接执行。

二者不能合并理解：前者回答“用户有没有授权这个计划动作”，后者回答“这个已解析动作按风险是否还需要确认”。

### 4.9 第九层：ToolExecutor 才拥有执行入口

文件：[`modules/tool_executor.py`](../../modules/tool_executor.py)、[`modules/confirmation_manager.py`](../../modules/confirmation_manager.py)

`ToolExecutor.execute()` 的典型顺序：

```text
按名称从 ToolRegistry 取工具
  -> 校验参数
  -> SafetyPolicy 评估
  -> 必要时创建带不可变参数的 PendingConfirmation
  -> 否则调用 handler
  -> 捕获异常并构造 ToolResult
  -> 绑定 tool_call_id / operation_kind
```

`execute_confirmed()` 不会让模型重新生成参数，而是执行确认时保存的不可变参数。这样“确认”不会确认到另一条计划或另一份记忆。

### 4.10 第十层：ToolResult 和回复真实性

文件：[`modules/contracts.py`](../../modules/contracts.py)、[`modules/response_composer.py`](../../modules/response_composer.py)、[`modules/action_claim_guard.py`](../../modules/action_claim_guard.py)

`ToolResult` 至少表达：工具名、是否成功、结构化数据、错误和 tool_call_id。`AgentResponse` 再组合最终状态、文字、工具结果、确认和客户端动作。

`ResponseComposer` 的关键原则是：

- 成功回复必须有对应成功 `ToolResult`。
- 工具失败不能说“已经完成”。
- 没有工具调用的普通聊天不能冒充保存、删除或修改成功。
- 查询回复应依据工具返回的数据，而不是模型记忆。
- 客户端动作只有真正被桌面派发后，才允许声称已执行。

因此，调试“回复说成功但数据没变”时，第一步不是改文案，而是找相应的 `ToolResult` 和后置条件。

## 5. 沿一段真实风格对话追踪调用链

下面使用脱敏示例，不读取真实用户数据。

### 5.1 第一轮：建议，不允许写入

用户：

```text
我今晚想学一会儿，你帮我想想怎么安排，先别加到计划里。
```

预期链路：

1. `ChatWindow.send_message()` 显示用户文字。
2. `ConversationService.prepare()` 建立本轮 diagnostics。
3. `LocalFeatureExtractor` 识别“怎么安排”的 advice 信号和“先别加”的否定信号。
4. `SemanticActionParser.parse_unified()` 即使模型提出 `add_plan`，也会由 advice/negation 边界去掉写工具。
5. 没有业务工具执行，`ConversationTurn` 携带聊天上下文。
6. `ChatReplyWorker` 在线程中调用 `complete()`。
7. `ContextBuilder` 组合人格、必要上下文和最近消息。
8. 模型生成建议；`ResponseComposer` 确保没有无依据的“已加入计划”。
9. diagnostics 应显示零写工具，最终状态为 chat。

这说明“句子里出现计划”不等于计划写入；请求模式和否定优先。

### 5.2 第二轮：引用助手建议，进入候选选择

假设助手刚才给出了三个有序建议。用户说：

```text
那就把你刚才列的安排放进今天计划。
```

`ConversationService` 会先检查同会话 pending 和助手引用。它不会把整段助手回复保存成一个巨大标题，而是：

1. 从最近 assistant 消息取得明确有序事项。
2. 优先尝试模型提取候选；候选不足时，只从原文的明确序号结构做确定性 fallback。
3. 调用 `InteractionStateCoordinator.awaiting_choice()` 保存候选 ID、领域、请求模式和不可变工具名。
4. 回复候选列表，要求用户选择。
5. 这一轮仍然没有 `add_plan` ToolResult，因此没有写数据。

### 5.3 第三轮：“第二项”续接状态

用户：

```text
第二项。
```

该短句脱离上下文没有业务含义，所以不能重新交给模型猜。处理顺序是：

1. `InteractionStateCoordinator.handle_control()` 或 continuation 分支优先读取当前 conversation 的 `awaiting_choice`。
2. `_select()` 把“第二项”绑定到候选列表第二个稳定对象。
3. `ConversationService._semantic_from_continuation()` 恢复结构化 `add_plan` 候选。
4. `BusinessResolver` 检查标题、日期和重复计划。
5. `PlanAuthorizationPolicy` 确认这是用户明确选择后的执行。
6. `AgentCore` 生成有界执行计划。
7. `ToolExecutor` 调用 `add_plan` handler。
8. handler 通过 `PlanService` / `GrowthManager` 写入 Repository，并验证后置条件。
9. 产生唯一成功 `ToolResult`。
10. `InteractionStateCoordinator.finish()` 收口到 completed，防止“第二项”再次重复消费。
11. `ResponseComposer` 才允许回复“已加入”。

关键点：多轮理解依赖的是结构化状态，不只是把全部聊天文本再次塞给模型。

### 5.4 同一句话在不同位置会有不同结果

| 输入 | 前置状态 | 正确结果 |
| --- | --- | --- |
| “第二项” | `awaiting_choice` 且有 3 个候选 | 选择第二个候选 |
| “第二项” | `idle` | 询问它指什么，不能执行 |
| “确认” | `awaiting_confirmation` | 执行已冻结参数 |
| “确认” | `idle` | 告知没有待确认操作 |
| “先别加” | 任意写提案 | 否定保护，零写入 |
| “把复习加入计划” | 标题唯一且授权明确 | 解析并进入实际 add_plan 链 |

## 6. ConversationService：为什么它是最重要也最难的文件

文件：[`modules/conversation_service.py`](../../modules/conversation_service.py)

该文件当前接近 3900 行，是整个对话路径的编排器。它大，不代表每项业务都应该继续写进去。阅读时按阶段分块，不要从第 1 行顺序读到结尾。

### 6.1 `handle()`、`prepare()`、`complete()`

- `handle()`：同步便利入口，本质上是 prepare 后 complete。
- `prepare()`：处理历史记录、pending、控制表达、本地/模型语义、校验、Resolver、授权和可立即完成的工具分支。
- `complete()`：执行延迟的模型调用、助手计划候选提取和最终聊天回复。

### 6.2 `prepare()` 的阅读分段

建议按以下责任块定位：

1. 创建/恢复会话，写入 diagnostics。
2. 恢复 pending interaction，优先处理确认、取消和选择。
3. 处理当前会话引用、读结果跟进和助手建议引用。
4. 形成唯一 SemanticDecision。
5. Normalize -> Validate -> Repair。
6. 把 candidate 交给 BusinessResolver。
7. PlanAuthorizationPolicy 与 AgentCore 执行。
8. 根据 AgentResponse 更新 interaction state。
9. 非聊天结果立即返回；普通聊天构造 LLM messages。

### 6.3 为什么保留很多兼容函数

当前代码仍包含旧命令、候选记忆、旧 intent 和桌面管理入口的兼容面。标准桌面自然语言入口已经通过 `_retire_legacy_natural_handler()` 和 feature flags 防止二次扫描，但这些函数仍可能被旧测试或管理 UI 调用。

判断一个函数是不是生产主链，至少查看：

- 它是否从 `ChatWindow.send_message()` 可达。
- 对应 feature flag 是否开启。
- `ConversationService.prepare()` 是否调用它。
- tests 中是 production、compatibility 还是 historical lane。

## 7. 多轮状态系统

文件：[`modules/interaction_state.py`](../../modules/interaction_state.py)、[`modules/interaction_state_coordinator.py`](../../modules/interaction_state_coordinator.py)、[`modules/conversation_state.py`](../../modules/conversation_state.py)、[`modules/confirmation_manager.py`](../../modules/confirmation_manager.py)

### 7.1 InteractionStateCoordinator

它管理“当前这件操作进行到哪里”，按 `conversation_id` 隔离。主要状态：

```text
idle
candidates_listed
awaiting_clarification
awaiting_choice
awaiting_confirmation
awaiting_tool_result
completed / partial_success / cancelled / expired
```

状态中保存的不只是名字，还包括：

- interaction kind 和 domain。
- 原始请求与 request mode。
- 已知字段、缺失字段和候选项。
- 列出的真实对象 ID。
- 不可变参数和 action preview。
- originating turn、创建时间、过期时间和 consumed 标志。

当前默认 TTL 分三类：普通 continuation 20 分钟，正式 confirmation 3 分钟，其他短 pending 5 分钟。

### 7.2 ConversationStateManager

它保留较轻量的当前会话事实，例如当前地点、候选记忆选择、抑制类别和上一条用户事实。它不是长期记忆，也不应该跨 conversation 泄漏。

### 7.3 ConfirmationManager

它专门保存正式工具确认：工具名、不可变参数、确认 ID、过期时间和状态指纹。进程重启后确认失效，这是安全设计，不是数据丢失 bug。

## 8. 上下文、历史、人格和知识

### 8.1 ContextBuilder

文件：[`modules/context_builder.py`](../../modules/context_builder.py)

`ContextBuilder.build()` 将上下文分成有顺序、有字符预算的 system sections：

1. 安全与工具事实。
2. 人格固定核心。
3. 用户重要目标。
4. 今日未完成计划。
5. 今日完成摘要与行动记录。
6. 当前会话摘要。
7. 相关正式记忆。
8. 相关旧会话摘要。
9. 人格 lore、关系和对话示例。
10. 相关本地知识。
11. 最近对话和当前用户消息。

必须保留的 section 优先占预算；可选 section 按既定顺序裁剪。`last_diagnostics` 记录每个 section 的来源、字符数、条目数和是否裁剪。

注意：内部上下文读取不是 ToolExecutor 业务工具，因此 diagnostics 的 `tool_calls=[]` 不表示没有读取历史、计划或人格。

### 8.2 ChatHistoryManager

文件：[`modules/chat_history_manager.py`](../../modules/chat_history_manager.py)

它负责会话、消息、标题、摘要和相关旧摘要检索。旧会话连续性只注入摘要，不注入完整旧消息 transcript。

摘要包含两种时间：

- `updated_at`：摘要被重新生成的时间。
- `time_range`：摘要所依据消息的实际开始/结束时间。

当前 `relevant_summaries()` 使用英文 token 与中文双字组合计算交集，再按得分和 `updated_at` 排序。提示词目前只序列化 `updated_at`。因此普通问候可能因共享“你好”“一天”等双字命中旧摘要，模型也可能把摘要刷新时间误当成原话发生时间。这是阅读 diagnostics 时必须知道的现有行为。

### 8.3 PersonaRegistry 与 PersonaPack

文件：[`modules/persona_registry.py`](../../modules/persona_registry.py)、[`modules/persona_pack.py`](../../modules/persona_pack.py)、[`data/personas/roxy/`](../../data/personas/roxy)

人格包包括固定核心、行为边界、文风、世界观资料、关系资料和对话示例。固定核心始终进入上下文，其他材料按当前输入检索。人格只能改变表达方式，不能改变工具权限、确认策略或业务事实。

### 8.4 KnowledgeManager

文件：[`modules/knowledge_manager.py`](../../modules/knowledge_manager.py)

它扫描允许的本地知识文本并提取有限片段，受文件大小、片段长度和匹配数量限制。知识内容是回答参考，不能授权写操作，也不能覆盖 ToolResult。

### 8.5 MemoryRetriever

文件：[`modules/memory_retriever.py`](../../modules/memory_retriever.py)

它从正式长期记忆中按关键词、标签、类别、范围、时效和敏感性评分。检索与保存是两件事：能把一条记忆放进提示词，不代表这一轮创建或更新了记忆。

## 9. 计划功能是怎样实现的

主要文件：

- [`modules/chinese_entity_parser.py`](../../modules/chinese_entity_parser.py)：时间和标题实体。
- [`modules/plan_service.py`](../../modules/plan_service.py)：业务查询、匹配、重复判断和更新门面。
- [`modules/growth_manager.py`](../../modules/growth_manager.py)：计划、行动和成长数据所有者。
- [`modules/today_plan.py`](../../modules/today_plan.py)：旧版/轻量计划存储兼容。
- [`modules/repositories/local_json_growth_repository.py`](../../modules/repositories/local_json_growth_repository.py)：实际 JSON 持久化。
- [`modules/tool_registry.py`](../../modules/tool_registry.py)：add/show/complete/delete/update/reschedule/reopen/cancel handler。

### 9.1 新增计划

```text
add_plan SemanticDecision
  -> 标题/日期/时段/时长规范化
  -> BusinessResolver 检查必填和重复
  -> PlanAuthorizationPolicy 判断授权
  -> ToolExecutor 参数与风险检查
  -> ToolRegistry.add_plan handler
  -> GrowthManager / Repository 写入
  -> 重新读取并验证 task UID、字段和状态
  -> ToolResult(postcondition_verified=true)
```

### 9.2 完成、删除与修改

- 完成：必须先把用户引用绑定到唯一任务，成功后检查该 UID 的状态确实为 completed。
- 删除：高风险且不可逆，必须确认；确认参数包含已解析的真实目标。
- 更新/改期：先确定真实任务，再验证 changes；事件锚点缺具体时间时澄清。
- 取消与重开：保留任务记录，通过状态转换实现，可逆性高于永久删除。

### 9.3 多项计划

当前单轮最多 3 个动作。多项时间表可在一份 SemanticDecision 中展开，但每个候选仍要分别解析和验证。执行结果按原顺序回写，不能只看工具名建立字典，否则多个 `add_plan` 会互相覆盖。

## 10. 记忆功能是怎样实现的

### 10.1 四层职责

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 语义入口 | `semantic_action_parser.py` | 区分查询、明确保存、普通聊天和已退出主入口的候选流程 |
| 业务服务 | `memory_service.py` | 统一返回 `MemoryOperationResult`，处理校验、重复、冲突和状态变化 |
| 治理 | `memory_governance.py` | 判断候选价值、敏感性、来源可信度、重复和冲突关系 |
| 数据所有者 | `memory_manager.py` + Repository | ID、正式记忆、归档、冲突、审计、迁移和原子保存 |

### 10.2 正式记忆与候选记忆

- 用户明确说“请记住……”时，可以进入 `save_formal_memory`。
- 普通聊天不会因为看起来重要就自动写正式记忆。
- 候选记忆文件、服务、旧 API 和处理器仅为内部兼容保留；候选意图已退出普通聊天，候选控件也不加入桌面或 Local Web 的正常可见界面。
- 模型不能自己编造 candidate ID、memory ID 或 conflict ID。
- 删除、更新和冲突处理要按风险与确认策略执行。

### 10.3 为什么需要 Governance

如果只看关键词，“记住删除计划这个命令”可能同时包含记忆和删除。命令外壳先保护 payload，Governance 再判断内容来源、敏感性和是否像助手/工具文本，最后 Service 与 Repository 才能落盘。

## 11. 行动记录、复盘与主动提醒

文件：[`modules/growth.py`](../../modules/growth.py)、[`modules/growth_manager.py`](../../modules/growth_manager.py)、[`modules/proactive_manager.py`](../../modules/proactive_manager.py)

- 行动记录描述已经真实发生的事情，与未来计划不同。
- `GrowthManager` 是当前主要数据门面；`GrowthService` 保留轻量兼容实现。
- 每日复盘读取计划统计和行动记录，再形成结构化 review。
- 保存复盘是写操作，生成复盘是只读操作。
- `ProactiveManager` 只根据现有数据生成提醒，不修改计划或记忆。
- 聊天存在 pending 时，桌面端会避免插入主动提醒，以免破坏“刚才那个”的可见指代来源。

## 12. 桌宠动作与 UI 线程边界

主要文件：

- [`modules/client_action.py`](../../modules/client_action.py)：声明式客户端动作数据。
- [`frontend/client_action_dispatcher.py`](../../frontend/client_action_dispatcher.py)：桌面唯一动作派发入口。
- [`modules/client_action_policy.py`](../../modules/client_action_policy.py)：动作允许条件。
- [`modules/client_action_claim_guard.py`](../../modules/client_action_claim_guard.py)：防止无执行证据的动作声明。
- [`frontend/pet_action_manager.py`](../../frontend/pet_action_manager.py)：动作状态机。
- [`frontend/desktop_pet.py`](../../frontend/desktop_pet.py)：实际 PySide6 绘制和动画。

工具 handler 不直接碰 Qt，而是返回：

```text
ToolResult.data.client_action
  -> AgentResponse.client_actions
  -> ChatWindow._present_agent_response()
  -> DesktopClientActionDispatcher
  -> PetActionManager / DesktopPet
```

原因是 Qt 窗口和计时器必须在 UI 线程操作。在线模型与业务工具可能运行在 worker thread，直接操作 `DesktopPet` 会造成线程安全问题。

双击桌宠入口位于 `DesktopPet.mouseDoubleClickEvent()`，它调用 `open_chat_window()`；右键菜单的“打开聊天”也复用同一方法。

## 13. 模型层怎样工作

主要文件：[`modules/llm/`](../../modules/llm)

### 13.1 RoutedLLMClient

[`modules/llm/routed_client.py`](../../modules/llm/routed_client.py) 是桌面和 Local Web 共享的模型入口。它组合：

- `ModelSettings`：从设置映射出模式、模型、超时和 fallback。
- `SecretStore`：读取密钥，避免密钥进入普通配置和日志。
- `ProviderFactory`：创建 DeepSeek 或 Ollama provider。
- `ModelRouter`：按任务复杂度和配置选择 online/offline/complex 模型。
- `ModelUsageStore`：记录模型、延迟与 token 使用。
- `ProviderResponse`：把不同 provider 返回统一成一个合同。

### 13.2 模型在一轮中可能承担两类工作

1. 语义判断：输出结构化 SemanticDecision。
2. 普通聊天：在工具路径结束或确定为 chat 后生成自然回复。

当前主链明确禁止在自然回复阶段重新开启第二套工具循环。`model_action_adapter` 在 `ConversationService` 中只保留构造兼容槽，旧 `ModelToolCallLoop` 不再接到标准桌面链。

### 13.3 为什么 ToolRegistry 会生成模型文档

`ToolRegistry.model_visible_parameter_docs()` 把当前允许的工具参数和约束提供给语义模型。模型只能看到 `model_visible=true` 的工具；别名和 schema 由本地层再次归一化与验证。

## 14. Repository 与本地数据

文件：[`modules/repositories/`](../../modules/repositories)

抽象 Repository 定义业务层需要的存取动作，本地 JSON 实现负责：

- 读取、默认值和旧格式兼容。
- 文件锁/事务边界。
- 写临时文件后替换，降低半写文件风险。
- 会话、摘要、计划、行动、成长、记忆、候选、冲突和审计的分离存储。

业务层不应该到处出现 `json.load()` / `json.dump()`。如果一个小问题涉及“写到了错误文件”“测试碰了真实数据”，先查对象创建时注入的 Repository 和 data root。

测试通常使用完整临时数据根，而不是只替换一个文件。否则 MemoryManager 虽然指向临时 memory，候选、冲突或审计仍可能落到真实 `data/private/`。

## 15. Local Web 为什么不是第二套后端

文件：[`server/main.py`](../../server/main.py)、[`server/agent_service.py`](../../server/agent_service.py)、[`server/schemas.py`](../../server/schemas.py)

Local Web 的允许结构是：

```text
HTTP request
  -> server.main 本机访问限制和路由
  -> Pydantic schema
  -> AgentService
  -> 同一 ConversationService / PlanService / MemoryService
  -> AgentResponse 转 JSON
```

`AgentService` 是无 PySide6 的组合根，与 `ChatWindow` 创建近似的核心对象。服务端不能执行桌宠动画；它只能把 client action 声明返回给支持该动作的客户端。

项目规则只允许在 `server/` 下实现已批准的 Local Web 适配，不允许复制另一套 Agent、记忆、计划或聊天业务逻辑。

## 16. Diagnostics：怎样看出第一处真实错误

文件：[`modules/interaction_diagnostics.py`](../../modules/interaction_diagnostics.py)，开发日志：`logs/interaction_diagnostics.jsonl`

每条诊断记录使用 request_id 关联一轮，并且不保存用户正文，只保存长度和 SHA-256。重要字段：

| 字段 | 先回答的问题 |
| --- | --- |
| `route_source` | 本轮来自固定命令、本地 guard、模型还是状态续接？ |
| `local_features` | 本地层看到了哪些领域、操作、否定、引用和时长？ |
| `semantic_decision` | 最终意图、模式和候选工具是什么？ |
| `validation_notes` | schema、修复或候选提取发生了什么？ |
| `resolver_result` | 是否绑定到真实对象，为什么澄清或拒绝？ |
| `confirmation_policy` | 哪个工具按什么策略需要确认？ |
| `tool_calls` | 真正执行了哪些业务工具？ |
| `tool_results` | 是否成功、错误码和 changed resource 是什么？ |
| `postcondition_result` | 写后重新读取是否验证成功？ |
| `interaction_state_before/after` | pending 是否正确续接和收口？ |
| `context_sections` | 哪些上下文进入聊天模型，数量和字符数是多少？ |
| `response_source` / `pipeline` | 最终回复来自哪个分支，经历了哪些阶段？ |

推荐的定位顺序是沿数据向前找第一处偏离：

```text
用户原意
  -> LocalFeatures
  -> SemanticDecision
  -> Validation/Repair
  -> Resolver
  -> Authorization/Safety
  -> ToolCall
  -> ToolResult/Postcondition
  -> InteractionState
  -> FinalResponse/UI
```

几个典型判断：

- `SemanticDecision` 已错：看 command envelope、特征、模型 schema 与本地 veto。
- Decision 正确但对象错：看 ReferenceResolver / BusinessResolver。
- 对象正确但没有调用：看授权、风险、确认和 pending。
- ToolResult 失败：进入 handler、Service、Repository 和后置条件。
- ToolResult 成功但回复错：看 ResponseComposer 或 UI 展示。
- `tool_calls=[]` 但回复提到历史：检查 `context_sections`；上下文读取不是业务工具。
- UI 没动作但 ToolResult 含 client_action：检查 Dispatcher、Policy、ClaimGuard 和 Qt 线程。

## 17. 测试框架与三种合同

文件：[`tests/README.md`](../../tests/README.md)、[`tests/contract_taxonomy.py`](../../tests/contract_taxonomy.py)、[`tests/conftest.py`](../../tests/conftest.py)

当前测试必须按性质区分：

| Lane | 含义 |
| --- | --- |
| `production_contract` | 当前桌面/服务统一链、安全边界和当前 schema |
| `compatibility_contract` | 仍承诺保留的旧 API、适配器和回滚面 |
| `historical_baseline` | 冻结的历史评测证据，不等于当前生产要求 |

默认测试使用 Stub 或固定 SemanticDecision，重点验证程序边界。真实在线模型测试单独运行；桌面 UI 验收又是另一层，三者不能互相冒充。

安全测试环境需要：

- 项目 `.venv`。
- `QT_QPA_PLATFORM=offscreen`。
- 禁用或隔离 pytest cache。
- 仓库外、每轮独立的 basetemp。
- 临时完整 data root 和 Repository。
- 测试前后私人数据不变证明。

## 18. 逐文件阅读地图

这一节用于“所有模块都过一遍”。第一遍只需要知道职责和上下游；进入专题时再读具体函数。

### 18.1 `frontend/`

| 文件 | 主要职责 | 重点入口 |
| --- | --- | --- |
| `pet_app.py` | 聊天 UI、组合根、发送消息、worker thread、呈现 AgentResponse | `ChatWindow.__init__`、`send_message`、`handle_agent_request`、`start_ai_reply` |
| `desktop_pet.py` | 桌宠窗口、绘制、双击/菜单、提醒和动画连接 | `mouseDoubleClickEvent`、`open_chat_window` |
| `client_action_dispatcher.py` | 声明式桌面动作唯一派发入口 | `dispatch` |
| `pet_action_manager.py` | idle/thinking/dancing/sleeping 等动作状态 | `play_action`、`restore_idle` |
| `pet_actions.py` | 具体动作控制兼容层 | `PetActionController` |
| `pet_bubble.py` | 桌宠旁气泡展示 | `PetBubble` |
| `settings_dialog.py` | 设置读取、保存和连接测试 | `load_pet_settings`、`SettingsDialog` |
| `chat_history_dialog.py` | 历史会话查看、切换和管理 | `ChatHistoryDialog` |
| `memory_dialog.py` | 正式/候选/冲突/审计管理 UI | `MemoryDialog` |
| `growth_dialog.py` | 计划、行动与成长展示 | `GrowthDialog` |

### 18.2 语义与编排模块

| 文件 | 主要职责 |
| --- | --- |
| `conversation_service.py` | 整轮对话的唯一主编排器 |
| `intent_router.py` | 固定命令、业务只读 guard、结构化模型语义入口及兼容路由 |
| `local_feature_extractor.py` | 提取命令、领域、否定、引用、问题和时长等稳定信号 |
| `chinese_entity_parser.py` | 日期、时段、时长、计划标题和时间表解析 |
| `semantic_action_parser.py` | 形成唯一 SemanticDecision 和 ActionCandidate |
| `semantic_normalizer.py` | 工具、字段和枚举别名归一化 |
| `schema_validator.py` | 根据工具 schema 验证结构 |
| `structure_repair.py` | 对有限的可修复结构错误进行一次修复 |
| `semantic_pipeline.py` | 编排 normalize/validate/repair 并输出 canonical outcome |
| `business_resolver.py` | 把候选绑定到真实计划、记忆或行动对象 |
| `reference_resolver.py` | 解析同会话序号、指代和最近结构化对象 |
| `plan_authorization_policy.py` | 计划写操作的主体、极性、语气和授权判断 |

### 18.3 状态、执行与合同模块

| 文件 | 主要职责 |
| --- | --- |
| `contracts.py` | IntentResult、ToolResult、AgentResponse、ClientAction 等共享数据合同 |
| `interaction_state.py` | 一次交互状态的数据结构和序列化 |
| `interaction_state_coordinator.py` | 按 conversation 管理澄清、选择、确认、消费和 TTL |
| `conversation_state.py` | 当前会话事实、抑制项和轻量引用状态 |
| `confirmation_manager.py` | 带 TTL 的正式工具确认和不可变参数 |
| `capability_registry.py` | 产品能力、风险、模式和模型可见性的权威目录 |
| `tool_registry.py` | 工具 schema、handler、别名、风险和白名单 |
| `safety_policy.py` | 按风险、歧义和置信度判断执行/确认/拒绝 |
| `tool_executor.py` | 参数验证、确认和真实 handler 执行 |
| `tool_execution_plan.py` | 有界多工具顺序执行与批次结果 |
| `action_batch.py` | 多动作批次数据结构 |
| `action_preview.py` | 确认前可见的安全动作摘要 |
| `agent_planner.py` | 将已解析 intent/resolved actions 转成有限 AgentStep |
| `agent_core.py` | 执行 AgentPlan、收集 ToolResult 和生成 AgentResponse |
| `response_composer.py` | 根据事实和状态生成确定性最终回复 |
| `action_claim_guard.py` | 防止没有成功结果却声称业务动作完成 |
| `interaction_diagnostics.py` | 脱敏记录整条流水线 |
| `feature_flags.py` | 新旧路径开关组合与启动告警 |

### 18.4 计划、行动与成长模块

| 文件 | 主要职责 |
| --- | --- |
| `plan_service.py` | 计划查询、匹配、重复判断和业务更新门面 |
| `growth_manager.py` | 当前计划、行动与成长的统一数据所有者 |
| `today_plan.py` | 旧计划存储 API 与轻量兼容 |
| `growth.py` | 轻量 GrowthService、行动和成长日志兼容实现 |
| `proactive_manager.py` | 只读数据后产生克制的主动提醒 |

### 18.5 记忆、历史、上下文与人格模块

| 文件 | 主要职责 |
| --- | --- |
| `memory_service.py` | 记忆业务操作统一门面和结构化结果 |
| `memory_governance.py` | 候选分类、来源、敏感性、重复与冲突策略 |
| `memory_manager.py` | 正式记忆数据、迁移、归档、冲突和审计 |
| `memory_candidate_manager.py` | 候选记忆的增删改查和状态 |
| `memory_retriever.py` | 对正式记忆进行相关性与敏感性检索 |
| `memory_data_query_guard.py` | 区分查询自己的记忆数据与知识性“记忆”问题 |
| `chat_history_manager.py` | 会话、消息、摘要和旧摘要检索 |
| `context_builder.py` | 有预算、有来源诊断的模型上下文拼装 |
| `persona_registry.py` | 人格发现、选择和 fallback |
| `persona_pack.py` | 加载人格固定核心和可检索材料 |
| `knowledge_manager.py` | 有界读取本地知识文本 |

### 18.6 模型模块 `modules/llm/`

| 文件 | 主要职责 |
| --- | --- |
| `base.py` | Provider 抽象接口 |
| `contracts.py` | ProviderResponse、ProviderError、ModelRouteDecision |
| `routed_client.py` | 桌面/Web 共享模型调用与 fallback |
| `model_router.py` | online/offline/复杂模型路由决策 |
| `provider_factory.py` | 根据设置创建 provider |
| `deepseek_provider.py` | DeepSeek provider 适配 |
| `ollama_provider.py` | 本地 Ollama provider 适配 |
| `openai_compatible.py` | OpenAI-compatible HTTP 请求实现 |
| `settings.py` | 模型设置解析和规范化 |
| `secret_store.py` | 私密密钥读取边界 |
| `usage_store.py` | 模型调用用量与延迟持久化 |
| `response_sanitizer.py` | 最终公开文本清理 |

`modules/llm_client.py` 是旧版兼容客户端。项目规则明确禁止在未获得专项授权时修改它。

### 18.7 Repository 模块

| 文件 | 主要职责 |
| --- | --- |
| `chat_repository.py` | 会话仓储抽象合同 |
| `growth_repository.py` | 计划/行动/成长仓储抽象合同 |
| `memory_repository.py` | 正式记忆/候选/冲突/审计仓储抽象合同 |
| `local_json_chat_repository.py` | 会话与摘要 JSON 实现 |
| `local_json_growth_repository.py` | 计划、行动和成长 JSON 实现 |
| `local_json_memory_repository.py` | 记忆相关 JSON 实现 |
| `local_json_utils.py` | 原子 JSON 读写与文件级事务辅助 |

### 18.8 Local Web 与测试

| 文件 | 主要职责 |
| --- | --- |
| `server/main.py` | 本机访问限制和 HTTP 路由 |
| `server/agent_service.py` | 无 UI 的核心对象组合根 |
| `server/schemas.py` | HTTP 输入输出 schema |
| `tests/conftest.py` | 统一 marker、临时路径和夹具 |
| `tests/private_data_guard.py` | 阻止测试触碰真实私人数据 |
| `tests/contract_taxonomy.py` | production/compatibility/historical 分类 |
| `tests/semantic_contract_fixtures.py` | 当前与旧语义 payload 适配 |
| `tests/replay_acceptance_runner.py` | 隔离重放真实故障类型 |
| `scripts/run_online_chinese_semantic_acceptance.py` | 隔离在线模型验收 |

## 19. 推荐学习顺序

这不是练习清单，而是后续和 ChatGPT 逐章学习的顺序：

1. `roxy.bat`、`frontend.pet_app.main()`、`DesktopPet` 和 `ChatWindow.__init__`。
2. `contracts.py` 中的 SemanticDecision、ActionCandidate、ToolResult 和 AgentResponse。
3. `LocalFeatureExtractor` 与 `ChineseEntityParser`。
4. `IntentRouter.route_semantic_decision()` 与 `SemanticActionParser.parse_unified()`。
5. SemanticPipeline 四个文件。
6. `ReferenceResolver` 与 `BusinessResolver`。
7. CapabilityRegistry、ToolRegistry、SafetyPolicy 和 ToolExecutor。
8. `AgentPlanner`、`AgentCore` 与 `ResponseComposer`。
9. InteractionStateCoordinator、ConversationStateManager 与 ConfirmationManager。
10. ContextBuilder、ChatHistoryManager、MemoryRetriever、PersonaPack 与 KnowledgeManager。
11. PlanService/GrowthManager 和 MemoryService/MemoryManager 两条业务链。
12. ClientAction 到 DesktopPet 的 UI 线程链。
13. RoutedLLMClient 与 `modules/llm/`。
14. Repository、Local Web 和测试合同。
15. 最后分段阅读 `ConversationService.prepare()` 与 `complete()`，把前面所有对象连起来。

把 `ConversationService` 放到最后精读，是因为它引用了几乎所有概念。先认识零件，再看总装，理解成本最低。

## 20. 遇到小问题时怎样缩小范围

不需要一看到异常就通读 3900 行 `conversation_service.py`。先按现象判断责任层：

| 现象 | 首先查看 |
| --- | --- |
| 普通聊天误触发写入 | LocalFeatureExtractor、IntentRouter、SemanticActionParser |
| 明确命令被当聊天 | command envelope、SemanticDecision schema、模型可见工具 |
| “它/第二项”选错对象 | InteractionStateCoordinator、ReferenceResolver、BusinessResolver |
| 日期、时段、时长错 | ChineseEntityParser、Normalizer、Tool schema |
| 明明解析正确却要求确认 | PlanAuthorizationPolicy、SafetyPolicy、ToolDefinition policy |
| 工具未调用 | SemanticPipeline outcome、Resolver、Authorization、AgentPlanner |
| 工具调用失败 | ToolExecutor、ToolRegistry handler、Service、Repository |
| 数据已变但回复说失败 | ToolResult、postcondition、ResponseComposer |
| 回复说成功但数据没变 | ActionClaimGuard、ToolResult、postcondition；这是高优先级真实性问题 |
| 多轮短句失效 | conversation_id、pending state、TTL、consumed |
| 历史内容时间说错 | relevant summaries、摘要 `updated_at` 与 `time_range`、context diagnostics |
| 桌宠动作不播放 | client_action、Dispatcher、Policy、ClaimGuard、Qt 线程 |
| 桌面正常而 Web 异常 | AgentService 装配、server schema、client_actions 序列化 |
| 测试碰到真实数据 | fixture data root、Repository 注入、private_data_guard |

最小修复通常落在“第一处偏离”的模块，而不是最终出现症状的 UI 文案。

## 21. 关键术语

| 术语 | 通俗解释 |
| --- | --- |
| canonical | 全系统最终认可的统一形式 |
| candidate | 尚未获得执行权的候选动作 |
| resolve | 把模糊文字绑定成真实业务对象和完整参数 |
| authorization | 判断用户是否真的授权执行 |
| validation | 判断结构、类型和取值是否合法 |
| confirmation | 在参数冻结后再次征得同意 |
| postcondition | 写完后重新读取，确认结果真的发生 |
| pending | 多轮操作尚未完成，等待澄清、选择或确认 |
| conversation_id | 多轮上下文的隔离边界 |
| tool_call_id | 一次真实工具调用的关联 ID |
| resource_id / UID | 真实计划、记忆等业务对象的稳定标识 |
| Repository | 隔离业务逻辑与具体 JSON 文件的存取层 |
| Provider | 具体在线或本地模型服务适配器 |
| fallback | 主路径不可用时受控使用的后备路径 |
| side effect | 会改变计划、记忆、文件或桌宠状态的动作 |

## 22. 本指南的边界

- 本指南描述的是 2026-09-03 当前 dirty worktree，不是永久不变的 API 文档。
- 它没有宣称所有测试、在线模型或桌面验收当前都通过。
- 它没有重新设计 `ConversationService` 或删除兼容层。
- 它不包含真实私人数据、密钥和聊天正文。
- 它不替代源码；学习时应让 ChatGPT 针对当前文件重新核验关键函数。
- 项目代码发生结构性变化后，应更新本指南的模块地图和调用链。
