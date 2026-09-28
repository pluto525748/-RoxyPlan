# RoxyPlan Roadmap

RoxyPlan 当前核心处于 **V1.8.3 中文交互可靠性工程原型**阶段。路线图区分已实现版本与计划内容，不代表固定交付承诺。

## V1.8.3：可靠性、能力目录与评测闭环

- 恢复真实舞蹈派发，统一 UTC 过期判断并通过两次 PySide6 offscreen 播放。
- 建立 CapabilityRegistry、ActionPreview、feature flag 矩阵和 authoritative semantic 边界。
- 建立 3500 条单轮、400 组多轮的中文闭集数据与冻结 final hash。
- 保存基线、10 轮改进记录和最终报告；不把 benchmark 结果宣称为全部中文能力。
- 下一步只补人工桌面验收、人工 gold entities/引用/多动作指标和真实用户反馈，不扩展业务功能。

## V1.8.2：桌宠客户端动作收口

已实现：

- 固定命令、Agent Tool Calling 和桌宠菜单统一生成声明式 ClientAction。
- DesktopClientActionDispatcher 在 UI 线程执行白名单动作。
- 动作请求、运行、busy、重复、过期、失败、取消和 idle 释放闭环。
- 回复在动作 accepted/rejected 后修正，避免“说已跳舞但实际未播放”。
- 模型能力提升与本地执行权限解耦：更强模型不绕过 ToolRegistry、SafetyPolicy 或 Dispatcher。
- 架构清单、调用链、技术债、协议、状态机和验收基线。

## V0.8：桌宠交互底座

已实现：

- PySide6 桌宠与轻量聊天窗口。
- 设置面板和本地 JSON 配置。
- 本地记忆示例与私人记忆文件分离。
- `.txt` / `.md` 本地知识库读取与轻量关键词匹配。
- `jump`、`nod`、`thinking / shake`、`study / scale`、`sleep / wake` 动作系统。
- QThread / Worker 异步模型回复，避免请求阻塞界面。

## V0.9：成长闭环封板

已实现并通过当前自动化验收：

- 多帧 dance 动画、素材切帧脚本和三循环播放。
- 今日计划的添加、查看、完成和删除。
- 行动记录的添加与查看。
- 规则生成今日复盘。
- 按日期保存成长日志。
- 四区成长面板与聊天命令入口。
- `GrowthManager` 和 `data/private/` 私有成长数据目录。
- 整理后的桌宠右键菜单。
- 成长系统、动作系统、记忆逻辑和公开仓库检查。

## V1.0：自然语言成长助手

当前已实现：

- 纯规则 `IntentRouter`，不依赖大模型结构化输出。
- 自然表达添加单个或多个今日计划。
- 自然表达完成和删除计划。
- 计划名称包含匹配与 `SequenceMatcher` 模糊匹配。
- 自然表达添加行动记录、触发今日复盘和保存成长日志。
- 明确表达“记住”时复用现有长期记忆逻辑。
- 固定命令优先、普通聊天回退及歧义候选确认。
- 基于 `GrowthManager` 的本地主动陪伴与智能提醒。
- 未完成计划、无行动记录、晚间复盘、完成鼓励和未互动提醒。
- Qt 定时检查、提醒冷却、近期互动保护和 dance 冲突避让。
- 设置面板主动提醒开关，以及聊天暂停/恢复提醒。
- 规则识别用户偏好、长期目标、稳定习惯、健康/生活倾向和项目偏好候选。
- `data/private/memory_candidates.json` 私有候选存储与状态管理。
- 记忆候选确认/忽略聊天命令和桌宠右键面板。
- 明确记忆与模糊稳定信息都先进入候选审核，成长数据不自动转为长期记忆。
- 本地多会话聊天历史，支持恢复、新建、查看、切换和删除。
- 超长会话的规则摘要与最近消息裁剪，避免上下文无限增长。
- 设置面板中的历史保存、最近会话恢复、上下文数量和自动摘要开关。

## V1.1：智能记忆检索与整理

当前已实现：

- 旧版 `memory.json` 安全迁移与 `data/private/backups/` 私有备份。
- preference、goal、habit、project、learning、health、relationship、rule、other 分类。
- 关键词、标签、文本包含、`SequenceMatcher`、类别、重要度和使用信息综合排序。
- 普通对话最多注入 5 条相关记忆，明确回忆最多 8 条。
- health、relationship 敏感类别的严格相关性门槛。
- 完全重复和高相似记忆去重。
- 私有记忆冲突列表与“使用新/保留旧/两条都保留”处理。
- 记忆归档、恢复、搜索、编辑、删除和整理建议。
- 长期记忆、候选、冲突和归档四区记忆管理面板。

## V1.2：自然语言意图理解与安全执行

当前已实现：

- 统一的意图、置信度、实体、确认需求和来源结构。
- 固定命令优先，本地规则语义层识别自然表达，并保留普通聊天回退。
- “你都记住了我的什么信息”“你了解我什么”等表达直接查看本地长期记忆，不交给模型猜测。
- 可选模型辅助意图解析，默认关闭；格式错误、连接失败或不可用时自动回退本地规则。
- 删除计划等风险操作改为二次确认。
- 长期记忆检索以当前用户输入为主，不让会话摘要把旧健康话题误判为新问题的相关内容。
- 最近对话按当前主题过滤无关健康或关系话题，避免污染学习、编程和项目回答。
- 设置面板新增智能意图、模型辅助、自动执行置信度与上下文数量配置。

## V1.3：Agent Core 与安全工具执行框架

当前已实现：

- `ToolRegistry`、`AgentPlanner`、`SafetyPolicy`、`ToolExecutor`、`ConfirmationManager` 和 `AgentCore` 分层协作。
- 计划、行动、复盘、成长日志、长期记忆、提醒和桌宠动作等现有能力注册为内部工具。
- 统一 `ToolResult`，工具异常在执行边界捕获，界面不依赖 Manager 的特殊返回结构。
- 规则优先的单步骤和最多三步骤规划；前一步失败时停止依赖步骤。
- 可选 LLM Planner 只接受注册工具和受限 JSON，失败时回退规则规划。
- 低、中、高三级风险策略；删除、清空和不可逆高风险操作始终二次确认，可逆归档/恢复按当前统一策略执行。
- 确认绑定工具和参数并在运行时过期，不写入长期记忆。
- 设置面板提供 Agent Core、多步骤规划、模型规划辅助、最大步骤数和中风险阈值。
- 明确排除 Shell、任意文件读写和任意电脑控制。

## V1.3.1：云端迁移前置重构

当前已实现：

- `IntentResult`、`ToolResult`、`ToolError`、`AgentResponse`、`AgentStep`、`ClientAction` 和 `PendingConfirmation` 轻量契约。
- 契约带 `schema_version` 与稳定请求/调用/动作 ID，并支持 JSON 序列化和反序列化。
- Growth、Memory、MemoryCandidate、ChatHistory 的 Manager 与持久化职责分离。
- `GrowthRepository`、`MemoryRepository`、`ChatRepository` 及当前 Local JSON 适配器。
- 旧 JSON 路径、文件结构、缺失文件创建、记忆迁移备份和界面整数序号继续兼容。
- 新任务、记忆、候选和冲突增加稳定字符串 `uid`，不强制改写旧记录。
- Agent 响应与桌面执行端共同使用客户端动作白名单、参数 schema 和过期检查。
- 契约、Repository、客户端动作策略及既有功能回归测试。

当前仍未实现：

- RoxyPlan-Web。
- 云端 Agent API 或网络客户端。
- 数据库适配器、账号、鉴权与跨设备同步。
- 本地 JSON 到云数据库的实际迁移工具。

## V1.4.0：智能记忆与上下文优化

当前已实现：

- 已确认长期记忆增加 `confidence` 与 `last_used` 可选字段，并继续兼容旧 `last_used_at`。
- 当前 V2 文件缺少可选字段时仅在运行内存补默认值，不因启动读取强制改写私人文件。
- 轻量检索综合类别优先级、关键词、多词组合、相似度、重要度、置信度和最近使用时间。
- 项目与学习问题优先检索 project、learning 和 goal；健康记忆仅在明确健康语境下参与。
- `ContextBuilder` 分开组织长期记忆、会话摘要、最近消息、知识片段和当前问题。
- 记忆候选仍需用户确认后才进入长期记忆。

## V1.5：记忆候选、冲突检测与人工治理

当前已实现：

- 纯 Python `MemoryGovernanceService` 供桌面端、统一会话服务和 Web 共用。
- 稳定偏好、长期目标、项目状态、习惯和持续约束可生成待审核候选。
- 普通知识问答、一次性计划、临时情绪和模型推断不会生成长期记忆候选。
- 敏感信息默认跳过；用户明确要求记住时只进入高敏感候选，仍需确认。
- 接受、拒绝、编辑后接受和低价值候选批量拒绝。
- 重复、近似重复、可合并、新信息和明显冲突关系判断。
- 冲突支持保留旧、使用新、合并、两条都保留和暂不处理。
- `data/private/memory_audit.json` 记录记忆治理动作；损坏的私有 JSON 会先备份再安全降级。
- 待审核和未解决冲突不会进入普通聊天上下文，归档记忆继续默认排除。
- Local Web 长期记忆区新增待审核、冲突和审计视图及确定性 API。
- 发布前工程体检已补齐原子写入、损坏备份、本机多进程文件锁、离线降级、审计脱敏和源码路径检查。
- 当前 V1.5 已进入封板验收阶段，详细清单见 `docs/release_v1_5.md`。

## V1.6：Agent 可靠执行、计划生命周期与上下文治理

当前已实现：

- 固定命令、本地语义规则、可选 LLM 结构化兜底组成三层意图识别；模型只能返回受限意图和实体。
- 模糊愿望先澄清或确认，普通消息会取消等待中的运行时确认，服务重启后旧确认明确失效。
- `ToolResult` 作为操作事实来源；关键写操作验证数据确实变化后才返回完成式文案。
- 计划支持更新、改期、完成、重新打开、取消和确认删除，内部稳定 ID 与界面序号继续分离。
- 相似计划检测和指代解析不会在目标不明确时静默修改数据。
- 今日复盘可按最新计划与行动重复生成；同一天成长日志更新同一条记录并增加修订号。
- 敏感长期记忆按当前消息逐轮检索，会话可临时抑制话题，明确相关问题可临时恢复使用。
- 记忆兼容当前状态、稳定身份、历史状态、未来意向、偏好、临时状态和约束等作用域。
- 当前会话明确地点优先于旧长期状态；永久变化仍先进入候选审核，不静默覆盖正式记忆。
- 桌面普通聊天和可选模型意图兜底继续在 QThread Worker 中运行。
- 新增 57 个 V1.6 真实流程回归场景，覆盖意图、确认、计划、成长、记忆、会话、知识库及跨主题连续对话。

当前限制：

- 语义规则和小模型结构化解析仍可能需要用户澄清，不等同于通用自然语言理解。
- 等待确认按会话保存在当前进程内，进程重启后不会恢复。
- JSON 文件使用轻量本机锁与原子替换，不提供分布式事务。

## V1.7：在线模型路由与安全工具调用

当前已实现：

- `modules/llm/` 统一 Provider 接口和内部响应契约，桌面端与 Local Web 共用。
- 可选 DeepSeek 在线 Provider，支持普通聊天、tools、tool choice、ToolResult 回传和可选思考模式。
- 集中维护 `default_model` 与 `complex_model`，提供省钱、自动和高质量三种用户模式。
- `ModelRouter` 根据意图、复杂指代、多动作、记忆冲突和长复盘选择模型；工具执行前最多自动升级一次。
- `model_visible` 工具白名单从现有 ToolRegistry 导出 JSON Schema，不向模型暴露 handler。
- `ModelActionAdapter` 将模型工具提议送入既有 AgentCore、SafetyPolicy、ConfirmationManager 和 ToolExecutor。
- 工具结果通过匹配的 `tool_call_id` 回传；已执行写操作不会因后续网络错误在其他 Provider 重放。
- `ActionClaimGuard` 拦截没有成功 ToolResult 的完成式声明。
- DeepSeek 缺少 Key 或不可用时，普通聊天可降级到本地 Ollama；本地小模型不接管模糊、高风险写工具。
- DeepSeek Key 使用环境变量或 Git 忽略的本地私有文件，设置页提供密码输入、显示/隐藏、连接测试和清除。
- 本地用量统计只保存 Provider、模型、token、延迟、结果、工具数和路由原因。
- Local Web 保持原 `/v1/status` 兼容，新增模型状态和用量接口，不返回 Key。
- 完成 provider-neutral `ProposedAction / AssistantToolCall / ToolMessage` 协议，原生 `tool_calls` 与 JSON fallback 共用本地白名单和校验。
- `ModelToolCallLoop` 限制为最多两次模型回环、三次工具调用和一次参数修复，重复 `call_id` 不会重复写入。
- 中文时间实体与 `ReferenceResolver` 独立化；不确定时间、跨会话指代和多个候选改为澄清。
- 当前 DeepSeek 作为原生工具主路；`qwen3:4b` 实测支持 tools，但中文复杂工具匹配不足，因此只保留为普通聊天降级。

当前限制：

- 真实 DeepSeek 调用默认不进入自动测试，需要用户配置 Key 后按手动清单验收。
- 模型 Tool Calling 仍受模型服务稳定性影响；最终安全和事实判断以本地 ToolResult 为准。
- 当前不提供流式输出、费用自动估算或跨设备模型配置同步。

## V1.7.2：统一记忆业务入口（第一阶段）

当前已实现：

- `MemoryService` 统一组合现有 `MemoryManager`、`MemoryCandidateManager`、治理逻辑和 Repository，不复制存储。
- 桌面记忆面板、桌面聊天 Agent 与 Local Web 适配器使用同一结果契约和数据根目录。
- 正式记忆、候选、归档和冲突分别查询；切换新会话不会替换记忆服务或私人数据源。
- ToolRegistry 提供正式记忆工具 schema，旧工具名继续兼容固定命令。
- 记忆按钮操作按 `success / not_found / already_processed / conflict / failed` 刷新并显示安全提示。
- 明确候选编号支持确定性确认或忽略。

## V1.7.3-A：记忆对话语义路由与候选审核状态闭环

已实现：

- `MemoryDataQueryGuard` 将正式记忆、候选、冲突和归档查询确定性路由到真实数据工具。
- 只读记忆工具不再创建通用确认；明确的候选接受或忽略直接执行。
- 当前会话保存最近列出的真实候选 ID，支持单条、序号、前两条和全部等指代。
- 批量接受/忽略返回逐条结果和部分成功状态，不编造候选 ID。
- 未经真实查询，普通模型不能声称“没有长期记忆”或“没有候选”。

## V1.8：通用交互状态与跨业务可靠执行

已实现：

- `InteractionStateCoordinator` 统一当前会话的候选列表、澄清、确认、取消、超时和消费状态。
- `LocalFeatureExtractor` 只提取操作、领域、否定、指代、时间、时长和连接关系，不直接选择业务对象。
- `SemanticActionParser` 将固定命令、本地组合语义、原生 Tool Calling 和 JSON fallback 收口为 `ActionCandidate`。
- `BusinessResolver` 使用真实 `MemoryService`、`PlanService` 和 `GrowthManager` 解析对象，区分 resolved、ambiguous、missing、not_found 和 forbidden。
- 计划、行动记录和记忆审核共享同一条 `ConversationService` 执行链，模型不能直接写 JSON 或信任自造 ID。
- `ActionBatch` 和 `ToolExecutionPlan` 支持最多三项的顺序执行、依赖跳过、幂等和部分成功。
- `ResponseComposer` 依据真实 `ToolResult` 生成事实回复；`ActionClaimGuard` 校验修改和空数据声明。
- 确定性查询固定走本地只读工具；缺字段、建议选择和对象选择可在后续短句中继续。
- 计划写工具执行后重新读取真实数据验证后置条件，失败时不产生成功声明或最近对象引用。
- `ToolRegistry` 统一 `never / when_ambiguous / always` 确认策略，候选审核和可逆操作不重复确认。
- 可选脱敏诊断快照记录路由、候选、解析、工具结果和 Provider 元数据，默认关闭。
- 新会话保留长期业务数据，但不会继承上一会话的“刚才那个”和待确认操作。
- 中文黄金用例和跨业务临时目录测试覆盖记忆、计划、行动、普通聊天与桌宠舞蹈路由。

兼容策略：

- 旧语义路径暂时保留，六个 feature flag 可分别回滚。
- 不迁移现有 JSON，不升级 Python/Pydantic，不引入 Agent 大型框架。

## Local Web V0.1 / V1.5：局域网移动控制台

当前已实现：

- `server/` 下独立 FastAPI 适配层，不导入 PySide6 界面。
- `/health`、统一 Agent 请求、运行状态和确定性业务 API。
- 计划、成长、长期记忆复用现有 AgentCore 与 Manager。
- 普通聊天复用现有 `LLMClient`、人格 JSON、`MemoryRetriever` 和 `ContextBuilder`。
- 桌面端和 Web 端通过纯 Python `ConversationService` 共享意图、Agent、上下文和 LLM 调用流程。
- 普通聊天只注入当前问题相关记忆，敏感记忆继续按现有类别策略过滤。
- 长期记忆查询由现有 AgentCore 确定性处理。
- 今日计划、行动记录查询与添加、复盘和成长日志均走共享工具链。
- Web 使用稳定 `conversation_id` 恢复最近消息，并在超出阈值时保存规则会话摘要。
- 共享 `KnowledgeManager` 读取现有 `.txt` / `.md`，普通聊天只注入相关且限长的知识片段。
- 手机优先的六页签控制台：聊天、今日计划、行动记录、成长日志、长期记忆、会话记录。
- 页面按钮通过确定性 API 调用现有 ToolExecutor；删除计划继续要求短时二次确认。
- Web 会话支持列表、新建、恢复和改名，并与桌面会话列表隔离。
- 状态接口显示 Web、Ollama、模型和知识文件数量；Ollama 离线时安全降级。
- 无 Token 时仅允许本机 Agent 请求；配置 Token 后通过请求头保护局域网请求。
- 使用 `sessionStorage` 的手机适配网页，不在前端保存 API Key。
- `run_roxy_web.bat` 以 `0.0.0.0:8000` 启动，不自动操作防火墙。
- HTTP、访问保护、静态页面和真实 Agent 组合的隔离测试。

后续再评估：

- 更细致的候选合并建议和审计筛选。
- 更完整的会话删除确认、成长日志筛选和错误恢复体验。
- Python 运行时升级、TLS 终止方式与更完整的局域网安全策略。

后续计划：

- 继续稳定成长数据、会话历史的异常恢复和交互体验。
- 周报与月报的轻量生成和查看。
- 继续完善记忆审计筛选、候选批量治理和与成长日志的显式联动规则。
- 继续稳定局域网网页入口，保留本地数据与隐私边界。
- 扩大自动化测试，整理原型阶段兼容代码。

## 暂不包含

- 云端账号与跨设备同步。
- 手机端原生应用。
- 语音输入与语音播报。
- 数据库、向量数据库或复杂文档解析。
