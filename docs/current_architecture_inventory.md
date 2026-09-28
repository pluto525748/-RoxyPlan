# RoxyPlan 当前架构清单（V1.8.3）

更新时间：2026-07-29

本文依据真实 import、构造位置、调用引用和测试入口整理，不以文件名推测职责。项目当前采用“PySide6 桌面壳 + Qt-free Conversation/Agent 核心 + 本地 JSON Repository + 可选 Local Web 适配层”。

## 入口与组合根

| 入口 | 文件与符号 | 调用关系 | 线程/边界 | 结论 |
|---|---|---|---|---|
| Windows 桌面启动 | `roxy.bat` → `frontend/pet_app.py:main` | 创建 `QApplication`、`DesktopPet`，按需创建 `ChatWindow` | UI 主线程 | 真实桌面入口，保留 |
| PySide6 桌宠 | `frontend/desktop_pet.py:DesktopPet` | 组合 `PetActionManager`、`DesktopClientActionDispatcher`、气泡、面板、计时器 | UI 主线程，含 Qt | 真实动画宿主，保留 |
| 桌面聊天 | `frontend/pet_app.py:ChatWindow` | 组合 Manager、Repository、Provider、Agent、ConversationService | 输入/UI 主线程；模型在 QThread | 仍偏重，后续渐进瘦身 |
| Local Web | `run_roxy_web.bat` → `uvicorn server.main:app` | `server.main:create_app` 组合 `AgentService` 和静态页面 | 普通 Python/ASGI | 仅本地/局域网适配层，保留 |
| Web 业务适配 | `server/agent_service.py:AgentService` | 复用 ConversationService、AgentCore、Manager、Repository | 无 Qt | 保留，不复制业务逻辑 |

## 主要模块职责与依赖

表中“工具决定”表示可能确定 `tool_name`；“直接动作”表示是否直接执行桌宠动画。

| 文件/模块 | 主要符号 | 当前职责 | 谁调用它 | 它调用谁 | UI | 文件 I/O | 模型 | 工具决定 | 直接动作 | 状态/建议 |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| `modules/conversation_service.py` | `ConversationService` | 单轮对话准备、路由、Agent 编排、上下文、记录回复 | 桌面 `ChatWindow`、Web `AgentService` | Intent、Semantic、Resolver、Agent、LLM、History | 否 | 间接 | 是 | 间接 | 否 | 真实主链，保留 |
| `modules/intent_router.py` | `IntentRouter` | 固定规则意图；规则不足时可选 LLM 结构化辅助 | ConversationService、SemanticActionParser | 中文实体解析、ReferenceResolver | 否 | 否 | 可选 | 是 | 否 | 真实主链，兼容保留 |
| `modules/semantic_action_parser.py` | `SemanticActionParser` | 生成不可信 `ActionCandidate`，限制动作数量和参数形状 | ConversationService | IntentRouter、LocalFeatureExtractor、ModelActionAdapter | 否 | 否 | 可选 | 是 | 否 | V1.8 主链，保留 |
| `modules/capability_registry.py` | `CapabilityRegistry` | 已有功能闭集、canonical tool、风险、确认和评测标签权威目录 | ToolRegistry、ActionPreview、评测器 | 纯数据契约 | 否 | 否 | 否 | 权威映射 | 否 | V1.8.3 新增，保留 |
| `modules/business_resolver.py` | `BusinessResolver` | 把候选绑定为真实计划/记忆对象与稳定 ID | ConversationService | PlanService、MemoryService、ReferenceResolver | 否 | 间接 | 否 | 校验 | 否 | V1.8 主链，保留 |
| `modules/action_preview.py` | `ActionPreview` | 从已解析、已绑定的不可变参数生成确认预览 | ConversationService | CapabilityRegistry | 否 | 否 | 否 | 否 | 否 | V1.8.3 新增，保留 |
| `modules/feature_flags.py` | defaults/validator/rollback | 统一语义和客户端动作 flags、冲突报警、一键兼容 profile | 桌面、Web、设置 | 纯配置策略 | 否 | 否 | 否 | 否 | 否 | V1.8.3 新增，保留 |
| `modules/agent_core.py` | `AgentCore` | 有界计划、工具执行、ToolResult、声明式 ClientAction | ConversationService、ModelToolCallLoop | AgentPlanner、ToolExecutor | 否 | 间接 | 否 | 间接 | 否 | 真实主链，保留 |
| `modules/agent_planner.py` | `AgentPlanner` | 意图到最多 3 步受限工具计划 | AgentCore | ToolRegistry、可选 LLMPlanner | 否 | 否 | 可选 | 是 | 否 | 保留 |
| `modules/tool_registry.py` | `create_roxy_tool_registry` | 注册计划、成长、记忆、提醒、声明式桌宠动作 | 桌面/Web 组合根、测试 | Manager/Service；动作只返回 `client_action` 数据 | 否 | 间接 | 否 | 定义 | **否** | V1.8.2 已收口，保留 |
| `modules/tool_executor.py` | `ToolExecutor` | 参数验证、安全策略、确认、执行、标准 ToolResult | AgentCore、ToolExecutionPlan | ToolRegistry、SafetyPolicy、ConfirmationManager | 否 | 间接 | 否 | 否 | 否 | 保留 |
| `modules/response_composer.py` | `ResponseComposer` | 根据真实 ToolResult 生成确定性回复，并附加 ClientAction | ConversationService | ActionClaimGuard、ClientAction | 否 | 否 | 否 | 否 | 否 | 保留 |
| `modules/action_claim_guard.py` | `ActionClaimGuard` | 阻止无 ToolResult 的业务成功声明 | ResponseComposer | 正则/ToolResult | 否 | 否 | 否 | 否 | 否 | 保留 |
| `modules/client_action_claim_guard.py` | `ClientActionClaimGuard` | 根据 Dispatcher 接受结果修正舞蹈成功/失败回复 | 桌面 ChatWindow | ClientActionResult | 否 | 否 | 否 | 否 | 否 | V1.8.2 新增，保留 |
| `modules/contracts.py` | `AgentResponse`、`ToolResult`、`ClientAction` | 进程间 JSON 安全契约及稳定 ID | 全层 | 无业务对象 | 否 | 否 | 否 | 否 | 否 | 保留；ClientAction 可从独立模块导入 |
| `modules/client_action.py` | `ClientAction` | 动作契约稳定公开导入点 | 新代码/测试 | contracts 兼容导出 | 否 | 否 | 否 | 否 | 否 | V1.8.2 新增 |
| `modules/client_action_result.py` | `ClientActionResult` | requested→accepted/running/completed/rejected 等结果契约 | Dispatcher、回复守卫、测试 | 无 | 否 | 否 | 否 | 否 | 否 | V1.8.2 新增 |
| `frontend/client_action_dispatcher.py` | `DesktopClientActionDispatcher` | 白名单、过期、重放去重、UI 线程派发、诊断结果 | DesktopPet、ChatWindow | ClientActionPolicy、PetActionManager | 是 | 否 | 否 | 否 | **唯一 Agent 动作入口** | V1.8.2 新增，保留 |
| `modules/client_action_policy.py` | `ClientActionPolicy` | 动作白名单与参数、过期校验 | AgentResponse、Dispatcher | ClientAction | 否 | 否 | 否 | 否 | 否 | 保留 |
| `frontend/pet_action_manager.py` | `PetActionManager` | 动作冲突、busy 策略、状态释放、取消和 failsafe | Dispatcher、DesktopPet 内部提醒 | DesktopPet 帧播放器/动作控制器 | 是 | 否 | 否 | 否 | 是 | V1.8.2 完善，保留 |
| `frontend/desktop_pet.py` | `_start/_advance/_stop_dance_frames` | 固定素材帧加载、顺序、时长、三循环与绘制 | PetActionManager | QPixmap、QTimer | 是 | 只读素材 | 否 | 否 | 是 | 播放器保留，不改素材 |
| `modules/growth_manager.py` | `GrowthManager` | 今日计划、行动记录、复盘/成长聚合 | PlanService、ToolRegistry、UI 面板、Web | GrowthRepository | 否 | 间接 | 否 | 否 | 否 | 保留 |
| `modules/plan_service.py` | `PlanService` | 计划查询、真实对象解析和业务校验 | BusinessResolver | GrowthManager | 否 | 间接 | 否 | 否 | 否 | 保留 |
| `modules/memory_service.py` | `MemoryService` | 长期记忆、候选、冲突、归档的统一业务入口 | UI、Resolver、ToolRegistry、Web | MemoryManager/Governance/Repository | 否 | 间接 | 否 | 否 | 否 | 保留 |
| `modules/memory_manager.py` | `MemoryManager` | 兼容 memory.json、结构升级、冲突与审计协调 | MemoryService、桌面/Web 组合根 | MemoryRepository | 否 | 间接 | 否 | 否 | 否 | 保留 |
| `modules/repositories/` | Repository 与 LocalJson 实现 | 原子 JSON、轻量锁、数据接口隔离 | 各 Manager | `data/private`、`memory.json` | 否 | **是** | 否 | 否 | 否 | 存储主链，保留 |
| `modules/llm/routed_client.py` | `RoutedLLMClient` | DeepSeek/Ollama 路由与降级 | 桌面/Web ConversationService | ProviderFactory、ModelRouter | 否 | 用量/密钥间接 | **是** | 否 | 否 | 保留 |
| `modules/llm/*provider.py` | Provider 实现 | OpenAI-compatible 请求和响应归一化 | RoutedLLMClient | HTTP transport | 否 | 否 | **是** | 否 | 否 | 保留 |
| `frontend/settings_dialog.py` | `DEFAULT_SETTINGS`、`SettingsDialog` | 本地设置 UI 与 feature flags 默认值 | DesktopPet、ChatWindow | ModelSettings、SecretStore | 是 | **是** | 连接测试 | 否 | 否 | UI 配置入口，保留 |
| `server/schemas.py` | Pydantic schemas | Web 输入输出边界，包含 `client_actions` | FastAPI routes | Pydantic | 否 | 否 | 否 | 否 | 否 | 保留 |

## 存储来源

| 数据 | 默认位置 | 写入者 | 备注 |
|---|---|---|---|
| 长期记忆 | `memory.json` | MemoryRepository/MemoryService | 私人文件，禁止提交 |
| 候选、冲突、审计、备份 | `data/private/*.json` | Memory/Growth/Chat Repository | 私有目录，禁止迁移或公开 |
| 今日计划、行动、成长日志 | `data/private/*.json` | LocalJsonGrowthRepository | 桌面/Web 共用 |
| 聊天历史与摘要 | `data/private/*.json` | LocalJsonChatRepository | 会话级数据 |
| 人格、桌宠、提示语 | `data/*.json` | 设置 UI 或只读加载 | `pet_config.json` 是本地设置入口 |
| 本地知识 | `data/knowledge/*.txt|*.md` | 用户放入，KnowledgeManager 只读 | 无复杂解析 |
| 舞蹈素材 | `assets/pet/dance/dance_*.png` | 代码只读 | 顺序按数字编号，时长常量未改 |

## Feature flags

V1.8.3 权威语义/动作默认值位于 `modules/feature_flags.py`，设置 UI 合并这些值。unified semantic、Interaction、Resolver、Preview、Batch、确定性回复和统一 Dispatcher 默认开启；legacy intent 与 legacy pet 默认关闭。完整矩阵、冲突报警和兼容回滚见 `docs/feature_flags.md`。

## 测试入口

- 全量：`.venv\Scripts\python.exe -m pytest -q`
- 动作专项：`tests/test_client_action_*.py`、`test_pet_action_state_machine.py`、`test_dance_repeat.py`
- 真实入口：`tests/test_desktop_v18_entry.py`、`test_action_runtime_entrypoints.py`
- 架构边界：`tests/test_architecture_boundaries.py`
- Web：`tests/test_local_web.py`、`test_web_agent_flow.py`、`test_web_console.py`
- 公共仓库隐私：`tests/check_public_repo.py`

## V1.8.3 审计结论（可量化）

1. 默认主链每条单动作消息只有一次 `SemanticActionParser.parse` 主要语义解析；其中 `IntentRouter` 是其内部确定性路由，不再是并行执行入口。多动作会对拆分后的最多 3 个子句做本地路由，不增加模型调用。
2. 能提出或映射 `tool_name` 的代码有 4 类：SemanticActionParser/IntentRouter 映射、ModelActionAdapter 提案适配、AgentPlanner 兼容映射、ToolRegistry 权威定义。真实可执行名称最终必须存在于 ToolRegistry，CapabilityRegistry 统一其产品语义和策略。
3. 能修改 arguments 的主要边界有 4 个：实体解析、SemanticActionParser 候选、BusinessResolver 真实对象绑定、AgentPlanner 契约整形。ToolExecutor 只验证，不让模型直接修改真实 ID。
4. 确认状态有两个层次：ToolExecutor/ConfirmationManager 创建执行凭证；ConversationService 通过 InteractionStateCoordinator 保存用户交互预览。UI 历史 wrappers 仍有 compatibility 调用，列入停用计划。
5. 真实桌宠动作只能由 `DesktopClientActionDispatcher` 调用 `PetActionManager`。DesktopPet 的两个旧方法只是委托 wrapper；默认 legacy flag 为 false。
6. 新旧代码仍共存，但默认配置只运行新主链；旧 intent 和旧 pet 入口均为显式回滚，不做双写 shadow。
7. 部分单元测试直接调用 Router/Manager，和桌面入口不同；V1.8.3 增加真实 AgentService 临时 Repository 链与真实 PySide6 offscreen Dispatcher/帧播放器链弥补差异。
8. 可能全部关闭的组合为 unified/legacy intent 同时 false，或 unified/legacy pet 同时 false；启动验证器会报警。新旧同时 true 也会报警。
9. 职责重复主要是 IntentRouter 与 SemanticActionParser、ConversationStateManager 与 InteractionStateCoordinator、LLMPlanner 与语义模型提案、UI 固定命令与 ConversationService。
10. 名称不同但同功能的 alias 包括 `show_memory/list_memories`、`search_memory/search_memories`、候选查询/批处理别名；CapabilityRegistry 将其归到同一能力，canonical tool 为 `tool_names[0]`。

最新全量结果由 `docs/v1_8_3_completion_report.md` 记录，不在本清单中写死运行中的统计。
