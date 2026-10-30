# RoxyPlan 极简文件地图

更新时间：2026-09-28

这份文档只回答一个问题：**这个目录或文件主要负责什么？**

不用一次读完。先看下面的“最先理解”，以后遇到具体问题再查完整索引。

## 1. 先记住这些目录

| 目录 | 最简单的理解 |
| --- | --- |
| `frontend/` | 桌宠、聊天窗口、设置窗口等桌面界面。 |
| `modules/` | Agent、语义、工具、计划、记忆等核心逻辑。 |
| `server/` | Local Web 适配层，复用核心逻辑。 |
| `data/` | 人格、提示语和本地运行设置；私人数据不能随便操作。 |
| `assets/` | 桌宠图片和动画素材。 |
| `docs/` | 项目说明、当前进度、工程经验、技术专题和研究资料。 |
| `tests/` | 自动测试代码和测试数据，不是正式用户数据。 |
| `test_workspace/` | 测试运行时的隔离区；成功后自动清理，失败现场可暂时保留。 |
| `scripts/` | 评测、诊断和可靠性检查脚本。 |
| `tools/` | 一次性实验和素材处理工具。 |
| `backend/` | 目前主要是预留目录，不是桌面程序主入口。 |

## 2. 最先理解的 10 个文件

| 文件 | 最简单的理解 |
| --- | --- |
| `README.md` | 项目说明书。 |
| `AGENTS.md` | Codex 在这个项目里的工作规则。 |
| `docs/current_status.md` | 当前开发到哪里、哪些已经验证。 |
| `docs/project_workflow.md` | 可公开的项目工作顺序、诊断、隐私和验证规则。 |
| `docs/agent_engineering_experience.md` | 真实开发中总结出的 Agent 工程经验。 |
| `docs/capability_boundaries.md` | 当前产品能做什么、不能做什么。 |
| `docs/learning/roxyplan_v2_2_code_learning_guide.md` | 深入学习代码时使用的详细教程。 |
| `roxy.bat` | 启动桌宠。 |
| `frontend/pet_app.py` | 创建聊天窗口并连接整个桌面程序。 |
| `modules/conversation_service.py` | 一轮对话从理解到执行再到回复的总调度中心。 |

## 3. 本地可见但不属于正式源码的内容

| 对象 | 作用 |
| --- | --- |
| `.git/` | Git 历史和分支信息，不能手工删除。 |
| `.venv/` | Python 虚拟环境，可以重建，但当前启动依赖它。 |
| `__pycache__/`、`.pytest_cache*/` | Python／pytest 缓存，可重新生成。 |
| `logs/` | 本地运行和诊断日志。 |
| `tmp/` | 临时运行内容。 |
| `output/` | 本地生成的报告或导出文件。 |
| `docs/local/project_preferences.local.md` | 仅本机保存的个人协作偏好和路径，不进入 GitHub。 |
| `memory.json`、`data/private/` | 正式私人记忆和业务数据，不进入公开 GitHub。 |
| `config.json`、本地 `.env` | 本地配置和密钥入口，不进入公开 GitHub。 |

## 4. 全部正式文件索引

下面按当前 Git 跟踪文件列出。测试文件很多，但规则很简单：`test_xxx.py` 就是验证 `xxx` 是否正常。

### 项目根目录

- `.env.example`：环境变量示例，不保存真实密钥。
- `.gitignore`：规定哪些本地文件不进入 Git。
- `AGENTS.md`：规定 Codex 在本项目中的工作边界和协作规则。
- `memory.example.json`：长期记忆文件的示例结构，不是真实记忆。
- `README.md`：项目总说明，介绍功能、安装和启动方式。
- `requirements.txt`：桌面程序需要安装的 Python 依赖。
- `roxy.bat`：启动 Windows 桌宠主程序。
- `run_roxy_web.bat`：启动本地 Web 控制台。

### `frontend/`

- `frontend/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `frontend/chat_history_dialog.py`：显示和管理历史会话窗口。
- `frontend/client_action_dispatcher.py`：把 Agent 请求的桌宠动作安全派发到界面。
- `frontend/desktop_pet.py`：实现桌宠窗口、双击、菜单、提醒和动画连接。
- `frontend/growth_dialog.py`：显示计划、行动记录和成长信息。
- `frontend/memory_dialog.py`：显示和管理长期记忆。
- `frontend/pet_action_manager.py`：管理 idle、thinking、sleeping、dancing 等动作状态。
- `frontend/pet_actions.py`：提供具体桌宠动作的兼容控制层。
- `frontend/pet_app.py`：桌面程序核心入口；创建聊天窗口并连接各模块。
- `frontend/pet_bubble.py`：显示桌宠旁边的气泡文字。
- `frontend/README.md`：说明桌面界面目录的用途。
- `frontend/settings_dialog.py`：读取、修改和保存设置。

### `modules/`

- `modules/__init__.py`：让这个目录可以作为 Python 包导入。
- `modules/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `modules/action_batch.py`：保存一批待处理动作的数据结构。
- `modules/action_claim_guard.py`：阻止工具没成功但回复声称成功。
- `modules/action_preview.py`：生成执行前给用户看的操作预览。
- `modules/agent_core.py`：执行 Agent 计划并收集 ToolResult。
- `modules/agent_planner.py`：把理解结果整理成有限的执行步骤。
- `modules/business_resolver.py`：把自然语言目标绑定到真实计划、记忆或行动对象。
- `modules/capability_registry.py`：记录产品允许哪些能力、风险和模型可见范围。
- `modules/chat_history_manager.py`：保存会话、消息、摘要并检索旧会话。
- `modules/chinese_entity_parser.py`：解析中文日期、时段、时长和事项。
- `modules/client_action_claim_guard.py`：阻止未执行的桌宠动作被说成已经完成。
- `modules/client_action_policy.py`：判断客户端动作是否允许执行。
- `modules/client_action_result.py`：定义客户端动作执行结果。
- `modules/client_action.py`：定义桌宠客户端动作的数据格式。
- `modules/confirmation_manager.py`：管理需要用户确认的操作及有效时间。
- `modules/context_builder.py`：把人格、记忆、历史和当前状态组合成模型上下文。
- `modules/contracts.py`：定义 SemanticDecision、ToolResult、AgentResponse 等共享数据格式。
- `modules/conversation_service.py`：整轮对话的总调度中心。
- `modules/conversation_state.py`：保存当前会话的轻量状态和最近事实。
- `modules/development_log.py`：记录便于排查问题的最小开发日志。
- `modules/feature_flags.py`：控制新旧功能路径是否启用。
- `modules/growth_manager.py`：统一管理计划、行动和成长数据。
- `modules/growth.py`：提供轻量成长、行动和日志兼容服务。
- `modules/intent_router.py`：判断输入应进入聊天、查询、工具还是兼容路径。
- `modules/interaction_diagnostics.py`：脱敏记录一轮对话各阶段发生了什么。
- `modules/interaction_state_coordinator.py`：按会话管理交互状态、消费和过期。
- `modules/interaction_state.py`：定义澄清、选择和确认等交互状态。
- `modules/knowledge_manager.py`：从本地知识文件中有界读取相关片段。
- `modules/llm_client.py`：旧版模型客户端兼容层；没有专项授权不要修改。
- `modules/llm/__init__.py`：让这个目录可以作为 Python 包导入。
- `modules/llm/base.py`：定义模型 Provider 的统一接口。
- `modules/llm/contracts.py`：定义模型响应、错误和路由决定的数据格式。
- `modules/llm/deepseek_provider.py`：连接 DeepSeek 模型。
- `modules/llm/model_router.py`：决定使用在线模型、本地模型或降级路径。
- `modules/llm/ollama_provider.py`：连接本地 Ollama 模型。
- `modules/llm/openai_compatible.py`：实现 OpenAI-compatible HTTP 调用格式。
- `modules/llm/provider_factory.py`：根据设置创建正确的模型 Provider。
- `modules/llm/response_sanitizer.py`：清理最终给用户显示的模型文本。
- `modules/llm/routed_client.py`：桌面端和 Web 共用的模型调用入口。
- `modules/llm/secret_store.py`：安全读取模型密钥。
- `modules/llm/settings.py`：读取并规范化模型设置。
- `modules/llm/usage_store.py`：保存模型用量、耗时等统计。
- `modules/local_feature_extractor.py`：提取否定、疑问、领域、引用和时长等稳定信号。
- `modules/memory_candidate_manager.py`：维护旧候选记忆数据，主要用于兼容。
- `modules/memory_data_query_guard.py`：区分“查询我的记忆”与普通知识问题。
- `modules/memory_governance.py`：处理记忆敏感性、重复、来源和冲突规则。
- `modules/memory_manager.py`：管理正式记忆、归档、冲突和审计数据。
- `modules/memory_read.py`：定义和整理长期记忆读取结果。
- `modules/memory_retriever.py`：按相关性和敏感性检索正式记忆。
- `modules/memory_service.py`：提供统一的记忆业务操作入口。
- `modules/model_action_adapter.py`：把模型动作转换为项目内部动作格式。
- `modules/persona_pack.py`：加载一套人格包的固定内容和材料。
- `modules/persona_registry.py`：发现、选择并回退人格包。
- `modules/plan_authorization_policy.py`：判断计划写操作是否真的是用户授权。
- `modules/plan_service.py`：提供计划查询、匹配、去重和更新服务。
- `modules/proactive_manager.py`：根据真实数据生成克制的主动提醒。
- `modules/read_snapshot.py`：保存用户刚看到的真实列表和稳定对象 ID。
- `modules/README.md`：说明核心业务模块目录的用途。
- `modules/reference_resolver.py`：核心业务模块：reference resolver。
- `modules/repositories/__init__.py`：让这个目录可以作为 Python 包导入。
- `modules/repositories/chat_repository.py`：定义会话数据仓储接口。
- `modules/repositories/growth_repository.py`：定义计划、行动和成长仓储接口。
- `modules/repositories/local_json_chat_repository.py`：用本地 JSON 保存会话和摘要。
- `modules/repositories/local_json_growth_repository.py`：用本地 JSON 保存计划、行动和成长。
- `modules/repositories/local_json_memory_repository.py`：用本地 JSON 保存记忆相关数据。
- `modules/repositories/local_json_utils.py`：提供带锁和原子替换的 JSON 读写。
- `modules/repositories/memory_repository.py`：定义记忆相关仓储接口。
- `modules/response_composer.py`：根据 ToolResult 和状态生成最终回复。
- `modules/review_backfill.py`：启动时按规则补齐昨日复盘。
- `modules/safety_policy.py`：按风险和歧义决定执行、确认或拒绝。
- `modules/schema_validator.py`：检查工具名和参数结构是否合法。
- `modules/semantic_action_parser.py`：把自然语言整理为唯一结构化语义决定。
- `modules/semantic_normalizer.py`：统一工具名、字段名和枚举别名。
- `modules/semantic_pipeline.py`：串联归一化、校验、修复和再次校验。
- `modules/structure_repair.py`：只修复有限且明确的结构错误。
- `modules/suggestion_snapshot.py`：保存助手实际展示过的计划建议列表。
- `modules/today_plan.py`：旧版今日计划存储兼容接口。
- `modules/tool_execution_plan.py`：按顺序执行有界的多工具操作。
- `modules/tool_executor.py`：真正调用已注册工具并返回 ToolResult。
- `modules/tool_registry.py`：登记工具参数、别名和执行函数。
- `modules/verified_turn_context.py`：记录本轮哪些事实和修改已经得到验证。

### `server/`

- `server/__init__.py`：让这个目录可以作为 Python 包导入。
- `server/agent_service.py`：在没有桌面 UI 时组合并调用现有 Agent 核心。
- `server/main.py`：Local Web 的本机访问限制和 HTTP 路由。
- `server/requirements.txt`：Local Web 额外需要的依赖。
- `server/schemas.py`：定义 Local Web 请求和响应格式。
- `server/web/app.js`：Local Web 页面交互逻辑。
- `server/web/index.html`：Local Web 页面结构。
- `server/web/style.css`：Local Web 页面样式。

### `data/`

- `data/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `data/knowledge/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `data/knowledge/README.md`：说明本地知识文件应放在哪里。
- `data/personas/roxy/assets.json`：登记洛琪希人格使用的资源。
- `data/personas/roxy/behavior.md`：描述洛琪希的行为规则。
- `data/personas/roxy/evaluation/persona_cases.jsonl`：保存人格表现评测案例。
- `data/personas/roxy/examples/dialogue_examples.jsonl`：保存脱敏的人格对话示例。
- `data/personas/roxy/identity.md`：描述洛琪希是谁。
- `data/personas/roxy/lore/worldview.md`：描述洛琪希的世界观背景。
- `data/personas/roxy/manifest.json`：人格包的入口清单。
- `data/personas/roxy/relationships.json`：描述人格与用户等对象的关系边界。
- `data/personas/roxy/speaking_style.md`：描述洛琪希的说话风格。
- `data/personas/roxy/truth_and_boundaries.md`：描述人格不能编造和越界的内容。
- `data/pet_config.json`：保存桌宠和模型的本地设置。
- `data/pet_tips.json`：保存桌宠可展示的提示语。
- `data/README.md`：说明运行数据目录的用途和隐私边界。
- `data/roxy_personality.json`：旧版人格配置兼容文件。

### `assets/`

- `assets/pet/dance/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `assets/pet/dance/dance_000.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_001.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_002.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_003.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_004.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_005.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_006.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/dance_007.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/dance/preview.gif`：桌宠界面或动画使用的图片资源。
- `assets/pet/roxy_idle.svg`：桌宠界面或动画使用的图片资源。
- `assets/pet/roxy_pet_transparent.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/roxy_pet.png`：桌宠界面或动画使用的图片资源。
- `assets/pet/source/roxy_dance_sheet.png`：桌宠界面或动画使用的图片资源。

### `scripts/`

- `scripts/generate_chinese_nlu_benchmark.py`：生成中文 NLU 评测数据。
- `scripts/inspect_recent_interactions.py`：只读查看最近交互和诊断。
- `scripts/run_blind_test.py`：运行封闭盲测。
- `scripts/run_chinese_nlu_benchmark.py`：运行中文 NLU 基准评测。
- `scripts/run_isolated_tests.ps1`：在 `test_workspace/` 中运行 pytest，避免测试产物污染正式工作区。
- `scripts/run_online_chinese_semantic_acceptance.py`：用真实在线模型做隔离语义验收。
- `scripts/run_reliability_loop.py`：重复运行可靠性检查。

### `tools/`

- `tools/experiment_ollama_tool_calling.py`：试验 Ollama Tool Calling 能力。
- `tools/extract_dance_frames.py`：从舞蹈素材中提取动画帧。

### `docs/`

- `docs/agent_core.md`：文档：RoxyPlan Agent Core。
- `docs/agent_engineering_experience.md`：文档：RoxyPlan Agent Engineering 经验库。
- `docs/analysis/v22_full_regression_2026-09-23.md`：文档：RoxyPlan V2.2 全量回归与真实模型复核（2026-09-23）。
- `docs/api_contracts.md`：文档：RoxyPlan API 契约。
- `docs/api_key_security.md`：文档：API Key Security。
- `docs/capability_boundaries.md`：文档：RoxyPlan 能力边界与交互约定。
- `docs/CHANGELOG.md`：文档：RoxyPlan Changelog。
- `docs/chat_history_system.md`：文档：对话历史与上下文连续性。
- `docs/chinese_interaction_reliability.md`：文档：V2.2 中文交互可靠性与评测方法。
- `docs/client_action_protocol.md`：文档：ClientAction 协议（V1.8.2）。
- `docs/cloud_migration_review.md`：文档：RoxyPlan 云端迁移准备评审。
- `docs/current_status.md`：文档：RoxyPlan 当前项目状态。
- `docs/deepseek_provider.md`：文档：DeepSeek Provider。
- `docs/deprecation_plan.md`：文档：RoxyPlan 兼容与停用计划。
- `docs/development_logging.md`：文档：开发日志与最近交互排查。
- `docs/feature_flags.md`：文档：Feature flag 组合与回滚。
- `docs/growth_system.md`：文档：成长日志系统设计。
- `docs/interaction_state_machine.md`：文档：V2.2 交互状态机。
- `docs/learning/roxyplan_file_map_simple.md`：本文件：用最简单的话说明项目目录和文件职责。
- `docs/learning/roxyplan_v2_2_code_learning_guide.md`：文档：RoxyPlan V2.2 代码与框架学习指南。
- `docs/learning/roxyplan_v2_2_code_learning_guide.pdf`：同一学习指南的便于阅读和分享的 PDF 版本。
- `docs/local_web_v0_1.md`：文档：RoxyPlan Local Web V0.1。
- `docs/memory_system.md`：文档：RoxyPlan 智能记忆系统。
- `docs/model_routing.md`：文档：Model Routing。
- `docs/pet_action_state_machine.md`：文档：桌宠动作状态机（V1.8.2）。
- `docs/product_principles.md`：文档：RoxyPlan 产品与可靠性原则。
- `docs/project_workflow.md`：文档：RoxyPlan 项目工作流程。
- `docs/research/agent_tool_calling_github_review.md`：文档：GitHub Agent 工具调用专项研究。
- `docs/research/local_rules_and_llm_routing_review.md`：文档：本地规则、LLM 路由与中文实体研究。
- `docs/research/ollama_tool_calling_experiment_2026_07_22.json`：研究或实验产生的结构化资料。
- `docs/research/open_source_conversation_agent_architecture.md`：文档：RoxyPlan 开源对话 Agent 架构专项研究。
- `docs/research/open_source_reuse_candidates.md`：文档：RoxyPlan 开源组件复用候选。
- `docs/research/roxy_agent_architecture_options.md`：文档：RoxyPlan Agent 架构候选方案。
- `docs/response_truthfulness.md`：文档：V2.2 回复事实一致性。
- `docs/ROADMAP.md`：文档：RoxyPlan Roadmap。
- `docs/roxy_personality.md`：文档：洛琪希计划人格系统。
- `docs/storage_architecture.md`：文档：RoxyPlan 存储架构。
- `docs/technical_debt_register.md`：文档：RoxyPlan 技术债登记（V1.8.3）。

### `tests/`

- `tests/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `tests/check_public_repo.py`：检查公开仓库是否包含不该发布的内容。
- `tests/conftest.py`：提供 pytest 公共配置和测试夹具。
- `tests/contract_taxonomy.py`：把测试分为生产、兼容和历史契约。
- `tests/eval/interaction_golden_cases.json`：测试数据或预期结果：interaction_golden_cases.json。
- `tests/eval/test_interaction_golden_cases.py`：验证 interaction golden cases 是否正常。
- `tests/eval/test_v18_real_dialogues.py`：验证 v18 real dialogues 是否正常。
- `tests/fixtures/v21_blind_set.json`：测试数据或预期结果：v21_blind_set.json。
- `tests/fixtures/v21_chinese_context_matrix.json`：测试数据或预期结果：v21_chinese_context_matrix.json。
- `tests/fixtures/v21_incident_regression.json`：测试数据或预期结果：v21_incident_regression.json。
- `tests/fixtures/v21_optimization_set.json`：测试数据或预期结果：v21_optimization_set.json。
- `tests/fixtures/v21_optimization_v2.json`：测试数据或预期结果：v21_optimization_v2.json。
- `tests/fixtures/v22_online_semantic_contract.json`：测试数据或预期结果：v22_online_semantic_contract.json。
- `tests/isolation_support.py`：帮助测试使用隔离数据目录。
- `tests/nlu_benchmark/adversarial_cases.jsonl`：测试数据或预期结果：adversarial_cases.jsonl。
- `tests/nlu_benchmark/capability_catalog.json`：测试数据或预期结果：capability_catalog.json。
- `tests/nlu_benchmark/dev.jsonl`：测试数据或预期结果：dev.jsonl。
- `tests/nlu_benchmark/final_test.sha256`：固定最终测试集的哈希，防止内容被悄悄修改。
- `tests/nlu_benchmark/generated_cases.jsonl`：测试数据或预期结果：generated_cases.jsonl。
- `tests/nlu_benchmark/multi_turn_cases.jsonl`：测试数据或预期结果：multi_turn_cases.jsonl。
- `tests/nlu_benchmark/reports/baseline_dev.json`：测试数据或预期结果：baseline_dev.json。
- `tests/nlu_benchmark/reports/final_test_report.json`：测试数据或预期结果：final_test_report.json。
- `tests/nlu_benchmark/reports/iteration_01.json`：测试数据或预期结果：iteration_01.json。
- `tests/nlu_benchmark/reports/iteration_02.json`：测试数据或预期结果：iteration_02.json。
- `tests/nlu_benchmark/reports/iteration_03.json`：测试数据或预期结果：iteration_03.json。
- `tests/nlu_benchmark/reports/iteration_04.json`：测试数据或预期结果：iteration_04.json。
- `tests/nlu_benchmark/reports/iteration_05.json`：测试数据或预期结果：iteration_05.json。
- `tests/nlu_benchmark/reports/iteration_06.json`：测试数据或预期结果：iteration_06.json。
- `tests/nlu_benchmark/reports/iteration_07.json`：测试数据或预期结果：iteration_07.json。
- `tests/nlu_benchmark/reports/iteration_08.json`：测试数据或预期结果：iteration_08.json。
- `tests/nlu_benchmark/reports/iteration_09.json`：测试数据或预期结果：iteration_09.json。
- `tests/nlu_benchmark/reports/iteration_10.json`：测试数据或预期结果：iteration_10.json。
- `tests/nlu_benchmark/schemas/case_schema.json`：测试数据或预期结果：case_schema.json。
- `tests/nlu_benchmark/seed_cases.jsonl`：测试数据或预期结果：seed_cases.jsonl。
- `tests/nlu_benchmark/style_coverage_cases.jsonl`：测试数据或预期结果：style_coverage_cases.jsonl。
- `tests/nlu_benchmark/test.jsonl`：测试数据或预期结果：test.jsonl。
- `tests/nlu_benchmark/train.jsonl`：测试数据或预期结果：train.jsonl。
- `tests/private_data_guard.py`：阻止测试读写正式私人数据。
- `tests/README.md`：说明测试怎么分类、隔离和运行。
- `tests/replay_acceptance_runner.py`：在隔离环境重放真实故障场景。
- `tests/semantic_contract_fixtures.py`：提供新旧语义契约测试数据适配。
- `tests/test_action_batch.py`：验证 action batch 是否正常。
- `tests/test_action_claim_guard.py`：验证 action claim guard 是否正常。
- `tests/test_action_log_interaction_flow.py`：验证 action log interaction flow 是否正常。
- `tests/test_action_preview.py`：验证 action preview 是否正常。
- `tests/test_action_runtime_entrypoints.py`：验证 action runtime entrypoints 是否正常。
- `tests/test_advice_action_boundary.py`：验证 advice action boundary 是否正常。
- `tests/test_agent_core.py`：验证 agent core 是否正常。
- `tests/test_agent_planner.py`：验证 agent planner 是否正常。
- `tests/test_agent_reliability_v16.py`：验证 agent reliability v16 是否正常。
- `tests/test_agent_response_client_actions.py`：验证 agent response client actions 是否正常。
- `tests/test_architecture_boundaries.py`：验证 architecture boundaries 是否正常。
- `tests/test_business_resolver.py`：验证 business resolver 是否正常。
- `tests/test_candidate_flow_retirement.py`：验证 candidate flow retirement 是否正常。
- `tests/test_capability_registry.py`：验证 capability registry 是否正常。
- `tests/test_chat_history.py`：验证 chat history 是否正常。
- `tests/test_chinese_context_matrix.py`：验证 chinese context matrix 是否正常。
- `tests/test_chinese_entity_parsing.py`：验证 chinese entity parsing 是否正常。
- `tests/test_client_action_claim_guard.py`：验证 client action claim guard 是否正常。
- `tests/test_client_action_contract.py`：验证 client action contract 是否正常。
- `tests/test_client_action_dispatcher.py`：验证 client action dispatcher 是否正常。
- `tests/test_client_action_policy.py`：验证 client action policy 是否正常。
- `tests/test_client_action_runtime.py`：验证 client action runtime 是否正常。
- `tests/test_confirmation_policy.py`：验证 confirmation policy 是否正常。
- `tests/test_context_builder.py`：验证 context builder 是否正常。
- `tests/test_contract_taxonomy.py`：验证 contract taxonomy 是否正常。
- `tests/test_contracts.py`：验证 contracts 是否正常。
- `tests/test_conversation_state_isolation.py`：验证 conversation state isolation 是否正常。
- `tests/test_cross_domain_interaction_flow.py`：验证 cross domain interaction flow 是否正常。
- `tests/test_dance_repeat.py`：验证 dance repeat 是否正常。
- `tests/test_data_isolation.py`：验证 data isolation 是否正常。
- `tests/test_deepseek_integration.py`：验证 deepseek integration 是否正常。
- `tests/test_desktop_v18_entry.py`：验证 desktop v18 entry 是否正常。
- `tests/test_development_log_chat_integration.py`：验证 development log chat integration 是否正常。
- `tests/test_development_log_ui_sources.py`：验证 development log ui sources 是否正常。
- `tests/test_development_log.py`：验证 development log 是否正常。
- `tests/test_duplicate_plan_resolution.py`：验证 duplicate plan resolution 是否正常。
- `tests/test_feature_flag_matrix.py`：验证 feature flag matrix 是否正常。
- `tests/test_formal_memory_presentation.py`：验证 formal memory presentation 是否正常。
- `tests/test_formal_memory_save.py`：验证 formal memory save 是否正常。
- `tests/test_growth_logic.py`：验证 growth logic 是否正常。
- `tests/test_growth_manager.py`：验证 growth manager 是否正常。
- `tests/test_intent_router.py`：验证 intent router 是否正常。
- `tests/test_intent_understanding.py`：验证 intent understanding 是否正常。
- `tests/test_interaction_diagnostics.py`：验证 interaction diagnostics 是否正常。
- `tests/test_interaction_query_stability.py`：验证 interaction query stability 是否正常。
- `tests/test_interaction_state_coordinator.py`：验证 interaction state coordinator 是否正常。
- `tests/test_llm_providers.py`：验证 llm providers 是否正常。
- `tests/test_local_feature_extractor.py`：验证 local feature extractor 是否正常。
- `tests/test_local_web.py`：验证 local web 是否正常。
- `tests/test_memory_candidates.py`：验证 memory candidates 是否正常。
- `tests/test_memory_conversation_closure.py`：验证 memory conversation closure 是否正常。
- `tests/test_memory_governance.py`：验证 memory governance 是否正常。
- `tests/test_memory_interaction_flow.py`：验证 memory interaction flow 是否正常。
- `tests/test_memory_logic.py`：验证 memory logic 是否正常。
- `tests/test_memory_manager.py`：验证 memory manager 是否正常。
- `tests/test_memory_retrieval_quality.py`：验证 memory retrieval quality 是否正常。
- `tests/test_memory_retriever.py`：验证 memory retriever 是否正常。
- `tests/test_memory_service_integration.py`：验证 memory service integration 是否正常。
- `tests/test_model_action_adapter.py`：验证 model action adapter 是否正常。
- `tests/test_model_routing.py`：验证 model routing 是否正常。
- `tests/test_model_settings.py`：验证 model settings 是否正常。
- `tests/test_model_tool_calling.py`：验证 model tool calling 是否正常。
- `tests/test_natural_language_route_repair.py`：验证 natural language route repair 是否正常。
- `tests/test_nlu_benchmark_quality.py`：验证 nlu benchmark quality 是否正常。
- `tests/test_ollama_tool_calling_contract.py`：验证 ollama tool calling contract 是否正常。
- `tests/test_online_semantic_acceptance_runner.py`：验证 online semantic acceptance runner 是否正常。
- `tests/test_pet_action_manager.py`：验证 pet action manager 是否正常。
- `tests/test_pet_action_state_machine.py`：验证 pet action state machine 是否正常。
- `tests/test_plan_authorization_policy.py`：验证 plan authorization policy 是否正常。
- `tests/test_plan_interaction_flow.py`：验证 plan interaction flow 是否正常。
- `tests/test_plan_last_task_reference.py`：验证 plan last task reference 是否正常。
- `tests/test_plan_postconditions.py`：验证 plan postconditions 是否正常。
- `tests/test_proactive_manager.py`：验证 proactive manager 是否正常。
- `tests/test_qt_resource_lifecycle.py`：验证 qt resource lifecycle 是否正常。
- `tests/test_real_conversation_sequences.py`：验证 real conversation sequences 是否正常。
- `tests/test_recent_interaction_inspector.py`：验证 recent interaction inspector 是否正常。
- `tests/test_reference_resolver.py`：验证 reference resolver 是否正常。
- `tests/test_release_readiness.py`：验证 release readiness 是否正常。
- `tests/test_repositories.py`：验证 repositories 是否正常。
- `tests/test_response_composer.py`：验证 response composer 是否正常。
- `tests/test_response_sanitizer.py`：验证 response sanitizer 是否正常。
- `tests/test_safe_interaction_baseline.py`：验证 safe interaction baseline 是否正常。
- `tests/test_safety_policy.py`：验证 safety policy 是否正常。
- `tests/test_semantic_action_parser.py`：验证 semantic action parser 是否正常。
- `tests/test_semantic_decision.py`：验证 semantic decision 是否正常。
- `tests/test_semantic_pipeline.py`：验证 semantic pipeline 是否正常。
- `tests/test_today_plan.py`：验证 today plan 是否正常。
- `tests/test_tool_call_loop.py`：验证 tool call loop 是否正常。
- `tests/test_tool_executor.py`：验证 tool executor 是否正常。
- `tests/test_tool_registry.py`：验证 tool registry 是否正常。
- `tests/test_tool_schema_exporter.py`：验证 tool schema exporter 是否正常。
- `tests/test_unified_semantic_pipeline.py`：验证 unified semantic pipeline 是否正常。
- `tests/test_v19_final_interaction_matrix.py`：验证 v19 final interaction matrix 是否正常。
- `tests/test_v20_conversation_continuity.py`：验证 v20 conversation continuity 是否正常。
- `tests/test_v20_milestone_finalization.py`：验证 v20 milestone finalization 是否正常。
- `tests/test_v20_persona_packs.py`：验证 v20 persona packs 是否正常。
- `tests/test_v21_blind_runner.py`：验证 v21 blind runner 是否正常。
- `tests/test_v21_fullchain_assertions.py`：验证 v21 fullchain assertions 是否正常。
- `tests/test_v21_optimization_runner.py`：验证 v21 optimization runner 是否正常。
- `tests/test_v21_replay_acceptance.py`：验证 v21 replay acceptance 是否正常。
- `tests/test_v22_business_read_guard.py`：验证 v22 business read guard 是否正常。
- `tests/test_v22_collection_continuation_regressions.py`：验证 v22 collection continuation regressions 是否正常。
- `tests/test_v22_completion_state_regressions.py`：验证 v22 completion state regressions 是否正常。
- `tests/test_v22_confirmation_flow_regressions.py`：验证 v22 confirmation flow regressions 是否正常。
- `tests/test_v22_delete_action_regressions.py`：验证 v22 delete action regressions 是否正常。
- `tests/test_v22_final_defect_closure.py`：验证 v22 final defect closure 是否正常。
- `tests/test_v22_growth_closure.py`：验证 v22 growth closure 是否正常。
- `tests/test_v22_history_context.py`：验证 v22 history context 是否正常。
- `tests/test_v22_latest_growth_regressions.py`：验证 v22 latest growth regressions 是否正常。
- `tests/test_v22_latest_plan_state_regressions.py`：验证 v22 latest plan state regressions 是否正常。
- `tests/test_v22_memory_main_chain.py`：验证 v22 memory main chain 是否正常。
- `tests/test_v22_memory_reference_contract.py`：验证 v22 memory reference contract 是否正常。
- `tests/test_v22_release_closure.py`：验证 v22 release closure 是否正常。
- `tests/test_v22_reliability.py`：验证 v22 reliability 是否正常。
- `tests/test_v22_reply_contract_regressions.py`：验证 v22 reply contract regressions 是否正常。
- `tests/test_v22_typed_memory_and_suggestions.py`：验证 v22 typed memory and suggestions 是否正常。
- `tests/test_v22_verified_confirmation_context.py`：验证 v22 verified confirmation context 是否正常。
- `tests/test_verified_turn_context.py`：验证 verified turn context 是否正常。
- `tests/test_web_agent_flow.py`：验证 web agent flow 是否正常。
- `tests/test_web_console.py`：验证 web console 是否正常。
- `tests/v18_test_support.py`：提供 V1.8 历史测试辅助函数。

### `backend/`

- `backend/.gitkeep`：空目录占位文件，让 Git 保留这个目录。
- `backend/README.md`：说明 backend 目录的预留用途；当前不是主运行入口。

## 5. 建议学习顺序

1. 先读本文件第 1～3 节。
2. 打开 `roxy.bat`，再看 `frontend/pet_app.py`。
3. 理解 `modules/conversation_service.py` 是总调度中心。
4. 遇到计划问题再看 plan 文件，遇到记忆问题再看 memory 文件。
5. 需要深入学习时再使用详细的代码学习指南。

这份地图只说明职责，不代替源码。代码结构发生明显变化后，应重新生成或更新。
