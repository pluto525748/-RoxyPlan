# RoxyPlan Changelog

## V1.8 - Unified Interaction and Reliable Execution

### Added

- `InteractionState` 与 `InteractionStateCoordinator`，统一会话内澄清、确认、候选选择、取消、超时和完成状态。
- `LocalFeatureExtractor`、`SemanticActionParser` 和统一 `ActionCandidate`，将本地稳定特征与模型语义提议分离。
- `PlanService` 与 `BusinessResolver`，使用真实业务数据解析计划、行动记录和记忆对象。
- `ActionBatch`、`ToolExecutionPlan` 和 `ResponseComposer`，支持多动作顺序执行、依赖跳过、部分成功与确定性事实回复。
- 中文交互黄金用例，以及状态、语义、业务解析、记忆、计划、行动和跨域流程测试。

### Changed

- 桌面端与 Local Web 继续共用 `ConversationService`，并接入同一个状态协调器、语义动作入口和业务解析器。
- 明确记录指令直接写入行动记录；普通进展陈述先询问，确认后只执行一次。
- 相似计划不静默重复添加；计划引用、候选编号和会话内序号均需本地校验。
- `ActionClaimGuard` 同时校验成功声明与“没有计划/记忆/行动”等空数据声明，查询工具不能证明写操作成功。
- 多动作连接词保留后续动作谓词，避免“再记录”在拆句时退化为普通陈述。

### Compatibility

- 新链路由五个默认开启的 feature flags 控制；旧路径暂不删除。
- 未修改真实私人 JSON、`modules/llm_client.py` 核心、PySide6 动画、Web API 或数据格式。
- 未引入 LangGraph、Pydantic AI、数据库、Redis、Docker 或向量数据库。

## V1.7.3-A - Memory Conversation Closure

- 新增 `MemoryDataQueryGuard`，用查询语气、记忆领域和正式/候选/冲突/归档范围特征识别记忆数据查询。
- 新增会话内 `MemoryInteractionState`，保存最近候选真实 ID、来源轮次和过期时间。
- 新增批量候选接受与忽略工具，支持稳定的成功、已处理、失败和部分成功结果。
- 修正记忆只读工具确认策略、候选审核重复确认和单条忽略工具返回值。
- `ActionClaimGuard` 现在拦截未经真实查询的“没有记忆”声明和未经工具结果的候选审核声明。
- 新增 41 项记忆对话闭环验收测试，并保持既有记忆、Agent、工具和桌面候选测试通过。

## V1.7.2 - Unified Memory Business Service (Phase 1)

### Added

- 新增 `MemoryService` 和稳定的 `MemoryOperationResult`，统一长期记忆、候选、归档、冲突和审计操作结果。
- `ToolRegistry` 增加正式的记忆工具名称与正整数 ID schema；旧工具名继续作为兼容入口。
- 新增临时目录集成测试，覆盖桌面面板、Agent、新会话、候选确认、归档恢复、错误 ID 和事实声明校验。

### Changed

- 桌面记忆面板、桌面聊天 Agent 和 Local Web 适配器复用同一个 `MemoryService`、Repository 与数据路径。
- 新对话只更换会话 ID 并清理会话级确认，不替换长期记忆、候选、冲突或数据根目录。
- “你记住了哪些信息”“待审核记忆有哪些”“确认候选4”等确定性表达直接进入真实记忆工具。
- `ActionClaimGuard` 要求记忆完成式声明必须有对应的成功记忆 `ToolResult`，无关工具成功不能放行。

### Compatibility

- 保留现有 `memory.json`、候选、冲突和审计 JSON 格式，以及 Repository 的原子写入和轻量锁。
- 未修改真实私人数据、`modules/llm_client.py`、桌宠动作、计划或成长数据。

## V1.7.1 - Provider-neutral Tool Protocol Hardening

### Added

- `ProposedAction`、`AssistantToolCall` 和去敏 `ToolMessage` 契约，保持 JSON 可序列化和 Provider 解耦。
- `ToolRegistry` 模型 schema exporter，统一生成 OpenAI-compatible tools 与受限 JSON fallback 提示。
- 独立中文时间实体解析和 `ReferenceResolver`，支持时段、时长、相对调整和当前会话结构化指代。
- 可复现 Ollama 原生工具实验脚本与受控诊断报告；实验不会执行真实业务工具。
- 工具适配、schema、回环预算、事实声明、指代和中文实体专项测试。

### Changed

- DeepSeek 原生 `tool_calls` 优先；协议不可用时才使用在线 JSON fallback，Ollama 继续只承担普通聊天降级。
- 模型工具回环限制为两轮、三次工具调用和一次参数修复；写操作串行，重复 `call_id` 进程内幂等。
- 开启模型工具层时不再重复调用旧 LLM 意图层，避免同一消息发生两次模型动作决策。
- 确认契约增加会话、调用 ID、风险和状态指纹，并兼容 `tool_name / immutable_arguments / safe_summary` 规范字段。

### Fixed

- “刚才那个改成 50 分钟”中的 `50` 不再被误认成任务编号。
- 只有多个候选而没有当前会话结构化目标时，不再擅自选择最后一个对象。
- 没有成功 `ToolResult` 的模型完成式声明继续由 `ActionClaimGuard` 阻止，同时不误伤用户自述“我刚刚完成作业”。

### Experiment

- 本机 Ollama `qwen3:4b` 13 个中文样例精确工具匹配 8 个（61.5%），无乱造工具和非法参数，但自然完成、指代更新、查看、删除和多工具场景不稳定。
- 工具失败结果回传后未观察到虚假成功声明；本地工具回环仍存在明显延迟波动，不作为默认可靠写操作主路。

## V1.7 - DeepSeek Provider, Model Routing and Local Fallback

### Added

- `modules/llm/` 统一 Provider 层、内部响应契约、集中模型设置、ProviderFactory、ModelRouter 和 RoutedLLMClient。
- 可选 DeepSeek 在线聊天、原生 Tool Calling、ToolResult 回传、非思考/思考模式和受控错误分类。
- `model_visible` 工具白名单和 JSON Schema 导出；模型不接触 Python handler。
- `ModelActionAdapter` 负责有界工具循环，最多两轮、三次调用，并保持匹配 `tool_call_id`。
- `ActionClaimGuard` 阻止没有成功工具结果的完成式陈述。
- 环境变量与 `data/private/llm_secrets.json` 两级 Key 读取，以及本地 `model_usage.json` 统计。
- 桌面设置页增加在线开关、三种路由模式、默认/复杂模型、Key 管理、后台连接测试、Ollama 降级和用量摘要。
- Local Web 新增 `/v1/model-status`、`/v1/model-usage`，并在状态面板显示 Provider、模式和降级状态。
- Provider、模型路由、工具闭环和设置安全专项测试。

### Safety

- 固定命令与高置信规则继续优先，本地确定性工具不需要模型参与。
- DeepSeek 只能提出注册工具；参数、风险、确认、执行和后置校验仍由本地 Agent 链负责。
- 多个需要确认的写操作不会部分执行；工具成功后的续写失败不会触发工具重放。
- DeepSeek 首轮失败只降级为 Ollama 普通聊天，Ollama 不驱动模糊或高风险写工具。
- Key、完整 prompt、内部思考文本和本地绝对路径不进入聊天历史、公开 API 或用量统计。

### Compatibility

- 保持 `modules/llm_client.py` 原接口、旧 Ollama 配置、桌面 QThread、Local Web `/v1/status` 和全部本地 JSON 格式兼容。
- 未调用真实收费 API，未修改私人数据，未引入 Agent 框架、数据库、Redis 或 Docker。

## V1.6 - Reliable Agent Execution and Context Governance

### Added

- `ConversationStateManager`：按会话保存最近工具目标、当前明确事实和敏感话题抑制，不写入长期文件。
- `IntentResult` 增加澄清问题与候选动作；可选 LLM 意图解析增加枚举、实体、危险操作和 JSON 复核。
- 今日计划增加更新、改期、重新打开和取消能力，并支持时长、时间段、日期、优先级和备注字段。
- 长期记忆增加兼容的时空作用域字段；当前对话明确状态优先于稳定身份、历史状态和未来意向。
- `tests/test_agent_reliability_v16.py` 覆盖 57 个真实流程场景，包括按会话隔离、并发确认、开放记忆查询、新兴趣澄清和跨主题检索。

### Changed

- 桌面端和 Web 端继续共用 `ConversationService`；桌面模型意图兜底移入现有回复 Worker，避免阻塞 Qt 主线程。
- 工具结果增加 `message_code` 与 `display_message`，关键写操作只有通过持久化后置校验才会返回成功文案。
- 相似计划不再静默重复添加；模糊愿望、缺失字段和多个目标分别进入确认或精确澄清。
- 普通消息会打断等待确认，确认只执行原始绑定的工具参数；进程重启后的旧确认明确失效。
- 当天复盘可以重复生成；成长日志按日期更新同一条记录并维护 `updated_at` 和 `revision`。
- 敏感记忆和敏感近期对话按当前问题逐轮筛选，上一轮健康上下文不会自动黏附到下一轮。
- UTF-8 解码失败的知识文件会记录受控警告并跳过，不再以忽略错误的方式加载损坏内容。
- 欢迎语不再产生“欢迎回来，我。”，启动日志不输出长期记忆正文。
- “认识/熟悉我”等开放表达确定性查询正式记忆；引用上一条助手建议保存时只进入候选审核。
- 泛化的“学习/模型/任务”类别词不再单独触发具体学习记忆，默认回复先回答当前问题且不强行追加练习。

### Compatibility

- 保持旧成长存储、旧 `memory.json`、桌面 QThread、桌宠动作、dance、Local Web API 和本地 JSON 路径兼容。
- 未修改私人数据内容，未部署云端，未引入数据库、Redis、Docker 或向量数据库。

## V1.5 - Memory Candidate Governance

### Added

- 新增纯 Python `MemoryGovernanceService`，统一桌面端和 Web 的候选、审核、冲突与审计流程。
- 候选增加来源、置信度、敏感级别、原因、重复目标和冲突目标等兼容字段。
- 新增 `data/private/memory_audit.json`，记录修改、归档、恢复、候选审核和冲突决策。
- Local Web 增加候选接受/拒绝/编辑接受、低价值批量拒绝、冲突处理和审计 API。
- Web 长期记忆区增加待审核、冲突和审计三个治理视图。
- 新增专项测试，覆盖敏感边界、去重、五种冲突决策、上下文隔离、鉴权和损坏文件降级。

### Changed

- 显式“记住”现在只生成候选，不再直接写入正式长期记忆。
- 普通聊天只从用户原始表达中提取稳定候选，不使用模型回复推断用户信息。
- 待审核候选、归档记忆和未解决冲突默认不进入普通聊天上下文。

### Safety

- 敏感信息默认不生成；明确要求保存后仍需人工确认。
- Web 接受、编辑接受和冲突决策要求显式确认，所有写操作继续鉴权。
- 未修改 `memory.json` schema、`llm_client.py`、桌宠动作或 Qt 线程。

### Release Hardening

- Local JSON Repository 改用唯一临时文件、`flush`、`fsync` 和 `os.replace`，写入失败时保留原文件。
- 成长、会话、记忆、候选、冲突和审计写操作增加本机轻量文件事务锁，并在修改前刷新最新快照。
- 成长和会话 JSON 损坏时创建时间戳备份，保留原损坏文件并安全降级。
- Web 审计接口不再返回完整变更正文，只返回必要元数据和正文长度。
- 桌面启动脚本改为相对自身目录定位，不再包含私人绝对路径。
- 新增发布就绪测试，覆盖 Ollama 离线、损坏知识文件、并发更新、确认重启失效和源码隐私检查。

## V1.4 - Local Web Console and Knowledge Context

### Added

- 新增纯 Python `KnowledgeManager`，桌面端和 Web 端共享现有 `.txt` / `.md` 扫描与限长检索。
- Local Web 增加今日计划、行动记录、今日复盘、成长日志、长期记忆和 Web 会话确定性 API。
- 手机优先的六页签控制台，支持计划增删完成、行动记录、复盘保存、记忆搜索和会话恢复/改名。
- 新增运行状态接口，独立显示 Ollama 连接状态、模型名和知识文件数量。
- 新增 Web 控制台隔离测试，覆盖知识上下文、业务 API、会话、鉴权、离线状态和页面转义。

### Improved

- `ContextBuilder` 明确区分人格、长期记忆、知识片段和最近会话日志。
- Web 记忆列表和会话详情经过适配层字段白名单，不返回业务对象的内部字段。
- 删除计划沿用 Agent Core 的短时确认，不由前端或路由层直接写 JSON。

### Boundaries

- 未修改私人数据格式、`llm_client.py` 核心行为或桌面端 Qt 线程与动作。
- 未部署公网，未新增数据库、账号、Docker、Supabase 或大型前端框架。

## V1.3 Follow-up - Unified Conversation Service

### Added

- 新增纯 Python `ConversationService`，统一意图识别、Agent 执行、相关记忆检索、上下文构建与普通 LLM 回复。
- 桌面聊天继续使用原有 `QThread` 等待模型回复，Web 通过同一服务的同步入口处理请求。
- Local Web 以稳定 `conversation_id` 保存最近消息，并复用现有 `ChatHistoryManager` 与规则会话摘要。
- Agent 工具链补齐今日行动记录查询。
- 新增 Web Agent 流程测试，覆盖聊天、记忆、计划、行动、复盘、人格、敏感记忆过滤和会话恢复。

### Boundaries

- 未修改 `llm_client.py` 核心逻辑、Web API schema 或私人数据格式。
- 桌面专属界面命令和 Qt 动作仍留在桌面壳层；服务层不导入 PySide6。
- 未新增数据库、向量检索或公网部署。

## V1.4.0 - Intelligent Memory and Context Refinement

### Improved

- 长期记忆兼容 `confidence`、`last_used` 等可选字段，同时保留旧字段读取。
- V2 记忆缺少新可选字段时只做运行时补值，不因加载而强制改写本地文件。
- `MemoryRetriever` 增加类别优先级、多词组合、置信度和最近使用时间评分。
- 项目、学习和健康问题使用不同类别策略；无关健康或关系记忆继续被过滤。
- `ContextBuilder` 按类别记录注入数量，并明确记录被跳过的敏感记忆类别。
- 新增记忆检索质量测试，覆盖项目、学习、健康、普通聊天和旧数据兼容。

### Boundaries

- 记忆候选仍需用户确认，不自动写入长期记忆。
- 未引入向量数据库、外部 embedding 模型或新依赖。
- 未修改 `modules/llm_client.py`、Web 接口、桌宠动作或私人记忆内容。

## Local Web V0.1 - LAN Access Skeleton

### Added

- `server/` FastAPI 适配层、请求 schema、AgentService 和轻量静态聊天页。
- `GET /health` 与 `POST /v1/agent/requests`。
- AgentService 复用现有 IntentRouter、AgentCore、ToolExecutor、GrowthManager、MemoryManager 和 LLMClient。
- `ROXY_LOCAL_TOKEN` 与 `X-Roxy-Token` 局域网访问保护。
- 无 Token 时仅允许回环地址调用 Agent API。
- `run_roxy_web.bat` 和固定的 Python 3.8 兼容依赖。
- HTTP、访问控制、静态资源和真实核心组合测试。

### Improved

- Web 普通聊天读取现有 `data/roxy_personality.json`，并复用 `MemoryRetriever` 与 `ContextBuilder`。
- 普通聊天只注入与当前问题相关的长期记忆，无关敏感记忆不会进入模型上下文。
- `show_memory` 等记忆查询继续通过 AgentCore 与 MemoryManager 确定性返回。
- `AgentService` 改为调用共享 `ConversationService`，不再自行编排另一套聊天流程。
- Web 会话接入本地 `ChatHistoryManager`，刷新后同一 `conversation_id` 可继续最近上下文。

### Boundaries

- 未部署公网、未新增账号、数据库、Docker、Supabase 或数据迁移。
- 未导入 PySide6 界面，也未复制 Agent、成长或记忆业务逻辑。
- 候选记忆、冲突处理和桌宠动作仍未接入 Web。
- 未修改 `llm_client.py` 核心行为或任何私人数据格式。

## V1.3.1 - Cloud Migration Preparation Refactor

### Added

- `modules/contracts.py` 统一可序列化的意图、工具、Agent、确认与客户端动作契约。
- 契约版本号，以及 `request_id`、`tool_call_id`、`step_id`、`action_id` 等稳定标识。
- `modules/repositories/` 持久化接口和 Local JSON Growth、Memory、Chat 适配器。
- `modules/client_action_policy.py` 客户端动作白名单、参数 schema 和过期检查。
- 桌面端声明式客户端动作执行入口，使用固定映射再次校验，不进行任意方法调用。
- 新任务、行动、复盘、长期记忆、候选与冲突的稳定字符串 `uid`。
- 契约、Repository 与客户端动作策略测试。

### Compatibility

- `GrowthManager`、`MemoryManager`、`MemoryCandidateManager` 和 `ChatHistoryManager` 继续提供原有业务接口。
- 旧 JSON 路径、旧整数 `id`、缺失文件创建和长期记忆迁移备份保持兼容。
- 正常读取旧文件时不会仅为补充 `uid` 强制改写私人数据。
- 原有 `AgentResponse`、`ToolResult`、`AgentStep` 和 `PendingConfirmation` 导入路径继续可用。

### Scope

- 本版本没有新增服务器、Web 服务、数据库、部署配置或 AI provider。
- 未修改 `modules/llm_client.py`、`memory.json` 或 `data/private/` 中的私人内容。

## V1.3 - Agent Core and Safe Tool Execution Prototype

### Added

- `ToolRegistry` 注册 19 个现有内部工具，并为每个工具声明参数、风险、确认要求与启用状态。
- 统一 `ToolResult`，工具异常、非法参数、未知工具和禁用工具均安全返回失败。
- `SafetyPolicy` 提供低、中、高三级风险判定，模型输出不能绕过策略。
- `ConfirmationManager` 保存单个短时待确认操作，并绑定工具与参数。
- `AgentPlanner` 支持规则单步骤、受限多步骤和可选 LLM JSON 规划，最多三步。
- `ToolExecutor` 负责白名单查询、严格参数校验、安全检查与异常隔离。
- `AgentCore` 统一执行计划，并根据确定性结果生成完成、澄清、确认或失败回复。
- 支持“完成计划并记录行动”“生成复盘并保存”等两步请求。
- 设置面板新增 Agent Core、多步骤、LLM Planner、最大步骤与中风险阈值配置。
- 新增五组 Agent Core 单元测试与公开仓库回归检查。

### Safety

- 删除、归档、恢复、清空和覆盖类操作不自动执行。
- 否定表达与“怎么删除”之类询问不会执行危险工具。
- 当前没有 Shell、CMD、PowerShell、任意文件访问或任意 Python 执行工具。
- 未修改 `modules/llm_client.py`、`memory.json` 或私有数据目录。

## V1.2 - Natural Language Intent and Safe Execution Prototype

### Added

- `IntentRouter` 统一返回 intent、confidence、entities、needs_confirmation 和 source。
- 本地同义表达、句式、实体和相似度规则，覆盖计划、行动、复盘、记忆与提醒控制。
- “你都记住了我的什么信息”等自然表达直接进入长期记忆查看，不再交给模型自由回答。
- 可选 `LLMIntentParser`：通过现有模型接口请求受限 JSON；模型不可用或输出异常时回退本地规则。
- 设置面板新增智能意图开关、模型意图辅助、自动执行置信度。
- 删除计划改为二次确认；固定命令继续优先走既有处理逻辑。
- 当前输入优先的记忆检索，以及按当前主题筛选近期敏感历史，减少健康信息对无关学习/编程问题的干扰。
- `test_intent_understanding.py` 覆盖自然记忆查询、意图回退、删除确认及敏感上下文隔离。

### Compatibility

- 未修改 `modules/llm_client.py`、`memory.json` 或私有成长数据。
- 模型辅助默认关闭；核心功能不依赖特定模型或特定 provider。

## V1.1 - Intelligent Memory Retrieval Prototype

V1.1 在现有本地记忆和会话上下文基础上增加轻量检索与整理，不引入向量数据库或外部模型。

### Added

- `MemoryManager` 统一管理长期记忆读取、保存、分类、搜索、归档和恢复。
- 旧 `memory.json` 自动兼容迁移，写入新结构前先备份到 `data/private/backups/`。
- `MemoryRetriever` 综合关键词、标签、文本包含、相似度、类别、重要度和使用信息排序。
- 普通聊天最多注入 5 条相关记忆，明确回忆最多 8 条。
- health、relationship 敏感记忆仅在明显相关时进入上下文。
- 完全重复和高相似记忆去重，候选记忆同步进行近似去重。
- 明显冲突写入 `data/private/memory_conflicts.json`，不自动覆盖旧记忆。
- 记忆搜索、分类查看、归档、恢复、二次确认删除和只读整理建议命令。
- 四区记忆管理面板及聊天窗口“记忆”、桌宠“记忆管理”入口。
- 迁移、检索、敏感注入、冲突、归档和兼容性测试。

### Privacy

- 迁移备份、记忆冲突和候选继续位于 Git 忽略的 `data/private/`。
- 会话摘要、计划和行动记录不会自动转为长期记忆。
- 整理功能只给出建议，不自动删除、覆盖或合并记忆。

## V1.0 - Natural Language Growth Assistant Prototype

V1.0 在 V0.9 成长闭环上增加本地规则意图识别，让常见成长操作可以通过自然表达触发，同时保留固定命令和普通聊天流程。

### Added

- `IntentRouter` 结构化识别普通聊天、计划、行动记录、复盘、成长日志和明确记忆请求。
- 自然表达添加单个或多个今日计划。
- 自然表达完成/删除计划，并复用现有 `GrowthManager` 数据层。
- 使用文本包含关系和 `difflib.SequenceMatcher` 匹配计划名称。
- 多个相近计划的候选确认提示，避免不明确时直接修改数据。
- 自然表达行动记录、今日复盘、复盘保存和成长数据查看。
- 自然语言路由与聊天流程回归测试。
- `ProactiveManager` 主动陪伴规则层，不调用 LLM、不修改成长数据。
- 未完成计划、无行动记录、晚间复盘、完成鼓励和未互动五类提醒。
- 桌宠 Qt 定时检查，正式间隔默认 10 分钟，测试模式每 30 秒检查。
- 主动提醒冷却、用户消息后两分钟保护、晚间每日一次和 dance 避让。
- 设置面板中的主动陪伴开关、提醒间隔及晚间/未互动提醒选项。
- “先别提醒我”“晚点提醒我”“恢复提醒”等运行时控制表达。
- `MemoryCandidateManager` 私有候选存储，支持 pending、accepted、rejected 状态。
- 对稳定偏好、长期目标、习惯、健康/生活倾向和项目边界的谨慎候选识别。
- “查看/确认/保存/忽略/删除/清空待确认记忆”等聊天命令。
- 记忆候选面板和桌宠右键入口，可查看分类、内容及原始来源。
- 候选确认后复用现有 `memory.json` 保存逻辑并进行内容去重。
- `ChatHistoryManager` 本地多会话存储，支持恢复、新建、切换、删除与显式确认清空。
- `ContextBuilder` 按人格、长期记忆、会话摘要、最近消息、知识片段和当前输入组装有限上下文。
- 聊天窗口新增会话标题、“新对话”和“历史”入口，以及轻量历史会话面板。
- 会话超过消息数或字符阈值时生成规则摘要；用户也可说“总结这段对话”主动生成。
- 设置面板新增聊天历史保存、最近会话恢复、上下文消息数量、自动摘要和确认清空选项。
- `data/private/chat_history.json` 与 `chat_summaries.json` 分别保存本地会话和摘要。

### Compatibility

- 原有固定命令保持最高优先级并继续走原处理逻辑。
- 未命中规则的内容继续进入原有普通聊天和异步模型回复流程。
- 长期记忆只在用户明确提出“记住”请求时保存；成长数据不会自动写入 `memory.json`。
- 模糊稳定信息只写入 Git 忽略的候选文件，未经确认不会进入长期记忆。
- 聊天历史和会话摘要位于 Git 忽略的私有目录；摘要不会自动进入长期记忆。
- 普通模型请求只使用最近指定数量的消息，不会发送全部聊天历史。
- 未修改模型客户端、底层动作、知识库或私有成长数据结构。

## V0.9 - Growth Loop Prototype

V0.9 将 RoxyPlan 从桌宠聊天 Demo 推进为桌面成长伙伴原型，重点完成本地、轻量、可验证的每日成长闭环。

### Added

- 多帧 dance 动画模块，可读取透明 PNG 帧并在播放结束后恢复待机图。
- 今日计划：添加、查看、完成和删除计划。
- 行动记录：通过聊天命令或成长面板记录当天行动。
- 今日复盘：使用规则汇总计划、完成情况和行动记录。
- 成长日志：按日期保存每日复盘，同一天重复保存会覆盖当天记录。
- 成长面板：集中管理今日计划、行动记录、复盘和最近成长日志。
- `data/private/` 私有成长数据目录，统一保存计划、行动和复盘数据。
- 桌宠右键菜单中的聊天、成长面板、设置、dance、鼓励、睡眠和唤醒入口。
- 聊天窗口中的成长按钮与成长类聊天命令入口。

### Quality

- 增加成长管理器、聊天命令、成长面板和动作菜单测试。
- 增加公开仓库检查，避免私人数据、配置、缓存和虚拟环境被误提交。
- 保留旧成长数据兼容迁移，不自动删除本地私人文件。

### Scope

V0.9 仍是本地桌面原型，不包含云端账号、手机同步、语音交互、周报/月报或完整长期趋势分析。
