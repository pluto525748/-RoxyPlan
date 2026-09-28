# RoxyPlan V1.8.3 基线文件清单

> 审计时间：2026-07-29
> 审计对象：创建阶段 0 分支前的 `main` 工作区
> 初始状态：25 个 tracked 修改、75 个 untracked 文件；无 staged diff
> 分类：A＝纳入基线，B＝运行时/私人数据排除，C＝用途尚待确认且不删除、不暂存

## 审计结论

- 初始 25 个 tracked 修改均属于当前 V1.8.2/V1.8.3 已接入架构、测试或同步文档，分类 A。
- 75 个 untracked 文件中，59 个分类 A，16 个分类 C，0 个属于尚未忽略的 B。
- 另将本清单作为阶段 0 审计文档纳入第一个基线提交。
- `memory.json`、`config.json`、`.env`、`data/private/`、本地计划/行动/成长数据和私人知识均已被 `.gitignore` 排除，未进入 staging 候选。
- 对全部 changed/untracked 候选做了高置信密钥与本机用户绝对路径扫描：未发现真实 API key、token、password 或用户目录绝对路径。若干 `skipped_*` 状态名曾被宽泛 `sk-` 模式命中，经逐行检查均为误报。
- 16 个 C 类文件是 benchmark 生成的详细 train/dev split 报告：未被源码、测试或文档精确引用，但可能有复盘价值。本轮保留在工作区和外部只读备份，不删除、不提交、不加入忽略规则。

## A 类：tracked 修改

| 文件 | tracked状态 | 分类 | 是否真实引用 | 是否纳入基线 | 理由 |
|---|---|---|---|---|---|
| `README.md` | M | A | 是，项目入口文档 | 是 | 与当前架构和运行方式同步 |
| `docs/CHANGELOG.md` | M | A | 是，发布记录 | 是 | 记录 V1.8.2/V1.8.3 已完成内容 |
| `docs/ROADMAP.md` | M | A | 是，路线文档 | 是 | 与当前阶段状态同步 |
| `frontend/desktop_pet.py` | M | A | 是，桌面入口实例化 | 是 | 统一 ClientAction 与动作状态机接线 |
| `frontend/pet_action_manager.py` | M | A | 是，DesktopPet 调用 | 是 | 桌宠动作执行与状态保护 |
| `frontend/pet_app.py` | M | A | 是，`roxy.bat` 主入口 | 是 | 统一服务、flags、dispatcher 的真实 composition root |
| `frontend/settings_dialog.py` | M | A | 是，桌面设置入口 | 是 | 当前 feature flag/设置兼容 |
| `modules/agent_core.py` | M | A | 是，ConversationService 构造 | 是 | ActionBatch、ClientAction 和结果闭环 |
| `modules/agent_planner.py` | M | A | 是，AgentCore 调用 | 是 | 权威语义结果与最多三步规划 |
| `modules/chinese_entity_parser.py` | M | A | 是，解析链调用 | 是 | 当前中文实体兼容修复 |
| `modules/client_action_policy.py` | M | A | 是，dispatcher 调用 | 是 | action 过期、白名单和重放策略 |
| `modules/contracts.py` | M | A | 是，多模块导入 | 是 | AgentResponse/ClientAction 共享契约 |
| `modules/conversation_service.py` | M | A | 是，桌面/Web 主链 | 是 | 统一会话编排与当前 V1.8.3 接线 |
| `modules/intent_router.py` | M | A | 是，SemanticActionParser 调用 | 是 | 当前确定性中文路由基线 |
| `modules/interaction_state.py` | M | A | 是，coordinator 使用 | 是 | 当前交互状态字段 |
| `modules/interaction_state_coordinator.py` | M | A | 是，ConversationService 使用 | 是 | 会话状态、候选、预览与确认协调 |
| `modules/local_feature_extractor.py` | M | A | 是，语义解析调用 | 是 | 当前局部特征基线 |
| `modules/response_composer.py` | M | A | 是，AgentCore/Service 使用 | 是 | ToolResult 事实回复与批结果 |
| `modules/semantic_action_parser.py` | M | A | 是，默认权威语义入口 | 是 | V1.8.3 统一语义入口 |
| `modules/tool_registry.py` | M | A | 是，ToolExecutor 使用 | 是 | 能力执行与 declarative client action |
| `server/agent_service.py` | M | A | 是，Local Web 主入口 | 是 | Web 复用统一服务和当前 flags |
| `tests/test_agent_planner.py` | M | A | 是，pytest | 是 | 当前权威规划回归 |
| `tests/test_interaction_state_coordinator.py` | M | A | 是，pytest | 是 | 当前交互状态回归 |
| `tests/test_semantic_action_parser.py` | M | A | 是，pytest | 是 | 当前语义解析回归 |
| `tests/test_tool_call_loop.py` | M | A | 是，pytest | 是 | 当前模型工具调用闭环回归 |

## A 类：untracked 源码、测试与文档

| 文件 | tracked状态 | 分类 | 是否真实引用 | 是否纳入基线 | 理由 |
|---|---|---|---|---|---|
| `docs/analysis/persona_memory_continuity_architecture_review.md` | ?? | A | 是，阶段评审文档 | 是 | 下一阶段边界与风险的审计依据 |
| `docs/client_action_protocol.md` | ?? | A | 是，协议文档 | 是 | ClientAction 结构与结果语义 |
| `docs/current_architecture_inventory.md` | ?? | A | 是，架构文档 | 是 | 记录真实运行组件 |
| `docs/deprecation_plan.md` | ?? | A | 是，兼容文档 | 是 | 旧路径停用边界 |
| `docs/feature_flags.md` | ?? | A | 是，flags 文档 | 是 | 当前默认矩阵和回滚说明 |
| `docs/manual_acceptance_client_actions.md` | ?? | A | 是，验收文档 | 是 | 桌宠动作人工验收步骤 |
| `docs/pet_action_state_machine.md` | ?? | A | 是，动作架构文档 | 是 | PetActionManager 状态约束 |
| `docs/product_principles.md` | ?? | A | 是，产品边界文档 | 是 | V1.8.3 安全原则 |
| `docs/runtime_call_chains.md` | ?? | A | 是，调用链文档 | 是 | 真实入口与执行链说明 |
| `docs/technical_debt_register.md` | ?? | A | 是，风险文档 | 是 | 已知双轨与债务记录 |
| `docs/v1_8_2_completion_report.md` | ?? | A | 是，完成报告 | 是 | V1.8.2 基线证据 |
| `docs/v1_8_3_completion_report.md` | ?? | A | 是，完成报告 | 是 | 直接引用 benchmark 指标和当前验收 |
| `frontend/client_action_dispatcher.py` | ?? | A | 是，`pet_app.py` 直接 import | 是 | 桌面唯一声明式动作分发器 |
| `modules/action_preview.py` | ?? | A | 是，`conversation_service.py` import | 是 | 已接入动作预览数据结构 |
| `modules/capability_registry.py` | ?? | A | 是，`tool_registry.py` 等 import | 是 | canonical capability 元数据 |
| `modules/client_action.py` | ?? | A | 文档定义的兼容导入面 | 是 | 保留公共 ClientAction 导入兼容 |
| `modules/client_action_claim_guard.py` | ?? | A | 是，`pet_app.py` 直接 import | 是 | 以桌面实际 dispatch 结果校正文案 |
| `modules/client_action_result.py` | ?? | A | 是，dispatcher/guard 导入 | 是 | 客户端执行结果契约 |
| `modules/feature_flags.py` | ?? | A | 是，桌面/Web composition root import | 是 | 当前统一/legacy 路径开关 |
| `scripts/generate_chinese_nlu_benchmark.py` | ?? | A | 是，生成评测语料 | 是 | benchmark 可复现生成器 |
| `scripts/run_chinese_nlu_benchmark.py` | ?? | A | 是，质量测试/循环引用 | 是 | benchmark 执行器 |
| `scripts/run_reliability_loop.py` | ?? | A | 是，完成报告流程 | 是 | train/dev/final 可靠性循环 |
| `tests/nlu_benchmark/adversarial_cases.jsonl` | ?? | A | 是，benchmark 读取 | 是 | 否定/误写安全集 |
| `tests/nlu_benchmark/capability_catalog.json` | ?? | A | 是，生成器/质量测试资产 | 是 | 能力闭集元数据 |
| `tests/nlu_benchmark/dev.jsonl` | ?? | A | 是，benchmark split | 是 | 固定开发集 |
| `tests/nlu_benchmark/final_test.sha256` | ?? | A | 是，runner 和测试读取 | 是 | 冻结 final 集完整性 |
| `tests/nlu_benchmark/generated_cases.jsonl` | ?? | A | 是，质量测试读取 | 是 | 受控生成语料 |
| `tests/nlu_benchmark/multi_turn_cases.jsonl` | ?? | A | 是，benchmark 资产 | 是 | 多轮评测池 |
| `tests/nlu_benchmark/schemas/case_schema.json` | ?? | A | 是，语料 schema | 是 | 数据格式契约 |
| `tests/nlu_benchmark/seed_cases.jsonl` | ?? | A | 是，质量测试读取 | 是 | 权威 seed 集 |
| `tests/nlu_benchmark/style_coverage_cases.jsonl` | ?? | A | 是，质量测试读取 | 是 | 中文表达维度评审集 |
| `tests/nlu_benchmark/test.jsonl` | ?? | A | 是，冻结 final split | 是 | 冻结验收集 |
| `tests/nlu_benchmark/train.jsonl` | ?? | A | 是，benchmark split | 是 | 训练/改进集 |
| `tests/nlu_benchmark/reports/baseline_dev.json` | ?? | A | 是，V1.8.3 报告引用 | 是 | 改造前指标证据 |
| `tests/nlu_benchmark/reports/final_test_report.json` | ?? | A | 是，可靠性脚本与完成报告 | 是 | 冻结集最终聚合指标 |
| `tests/nlu_benchmark/reports/iteration_01.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_02.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_03.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_04.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_05.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_06.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_07.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_08.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_09.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/nlu_benchmark/reports/iteration_10.json` | ?? | A | 是，完成报告引用的汇总序列 | 是 | 迭代摘要证据 |
| `tests/test_action_preview.py` | ?? | A | 是，pytest | 是 | ActionPreview 回归 |
| `tests/test_action_runtime_entrypoints.py` | ?? | A | 是，pytest | 是 | 真实入口执行测试 |
| `tests/test_agent_response_client_actions.py` | ?? | A | 是，pytest | 是 | AgentResponse client action 契约 |
| `tests/test_architecture_boundaries.py` | ?? | A | 是，pytest | 是 | UI/Agent/Tool/客户端边界测试 |
| `tests/test_capability_registry.py` | ?? | A | 是，pytest | 是 | capability 元数据回归 |
| `tests/test_client_action_claim_guard.py` | ?? | A | 是，pytest | 是 | 实际动作结果文案保护 |
| `tests/test_client_action_contract.py` | ?? | A | 是，pytest | 是 | ClientAction 序列化/契约 |
| `tests/test_client_action_dispatcher.py` | ?? | A | 是，pytest | 是 | dispatcher 白名单/重放测试 |
| `tests/test_client_action_runtime.py` | ?? | A | 是，pytest/Qt | 是 | 桌面真实动作运行链 |
| `tests/test_dance_repeat.py` | ?? | A | 是，pytest | 是 | 舞蹈重复动作回归 |
| `tests/test_feature_flag_matrix.py` | ?? | A | 是，pytest | 是 | flags 组合安全校验 |
| `tests/test_nlu_benchmark_quality.py` | ?? | A | 是，pytest | 是 | 语料规模/冻结 hash 门槛 |
| `tests/test_pet_action_state_machine.py` | ?? | A | 是，pytest | 是 | busy/sleep/idle 状态机 |
| `tests/test_real_conversation_sequences.py` | ?? | A | 是，pytest | 是 | 真实会话序列端到端回归 |

## C 类：生成型详细评测报告

| 文件 | tracked状态 | 分类 | 是否真实引用 | 是否纳入基线 | 理由 |
|---|---|---|---|---|---|
| `tests/nlu_benchmark/reports/iteration_01_dev.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 dev 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_02_dev.json` | ?? | C | 未发现精确引用 | 否 | 同上 |
| `tests/nlu_benchmark/reports/iteration_03_dev.json` | ?? | C | 未发现精确引用 | 否 | 同上 |
| `tests/nlu_benchmark/reports/iteration_04_dev.json` | ?? | C | 未发现精确引用 | 否 | 同上 |
| `tests/nlu_benchmark/reports/iteration_04_train.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 train 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_05_dev.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 dev 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_06_dev.json` | ?? | C | 未发现精确引用 | 否 | 同上 |
| `tests/nlu_benchmark/reports/iteration_07_train.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 train 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_08_dev.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 dev 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_08_train.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 train 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_09_dev.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 dev 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_09_train.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 train 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_10_dev.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 dev 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_10_train.json` | ?? | C | 未发现精确引用 | 否 | 可再生成的详细 train 输出；用途待确认 |
| `tests/nlu_benchmark/reports/iteration_10b_dev.json` | ?? | C | 未发现精确引用 | 否 | 非主编号的详细输出，保留待确认 |
| `tests/nlu_benchmark/reports/iteration_10b_train.json` | ?? | C | 未发现精确引用 | 否 | 非主编号的详细输出，保留待确认 |

## B 类：明确排除的运行时和私人路径

这些路径已被忽略，不在上述 changed/untracked 初始清单中。它们不进入任何阶段 0 commit，也不因本轮测试自动清理。

| 路径/模式 | 分类 | 是否纳入基线 | 理由 |
|---|---|---|---|
| `.venv/` | B | 否 | 本机虚拟环境 |
| `__pycache__/`、`*.pyc`、`*.pyo`、`*.pyd` | B | 否 | Python 缓存/本机构建产物 |
| `.pytest_cache/`、pytest basetemp | B | 否 | 测试缓存与临时输出 |
| `.env`、`.env.*`（保留 `.env.example`） | B | 否 | 本地密钥与环境配置 |
| `memory.json`、`config.json` | B | 否 | 正式私人记忆和本机配置 |
| `data/private/` | B | 否 | 聊天、摘要、候选、冲突、审计、归档、锁和 secrets |
| `data/today_plan.json`、`data/action_log.json`、`data/growth_log.json` | B | 否 | 用户真实计划、行动、成长数据 |
| `data/knowledge/*`（仅白名单 README/.gitkeep） | B | 否 | 用户私人知识内容 |
| `logs/`、`*.log` | B | 否 | 运行日志 |
| `*.lock` | B | 否 | 运行/测试锁文件 |
| `*.tmp`、coverage、test-results | B | 否 | 本地运行和测试输出 |
| `apikey.txt`、`api_key.txt`、`secret.txt`、`token.txt` | B | 否 | 常见本地密钥文件 |

`data/private/` 中如未来确需版本控制 README 或 schema 示例，只能使用单文件白名单；本轮没有放开整个目录，也没有纳入其中任何文件。

## 阶段 0 新增文件归属

| 文件 | 计划提交 | 理由 |
|---|---|---|
| `docs/analysis/v183_baseline_file_inventory.md` | 第一个基线提交 | 固化 staging 决策和未决文件 |
| `.gitignore` 的测试/锁补充 | 第二个测试隔离提交 | 属于隔离保护，不混入既有架构快照 |
| 测试 fixture/guard/隔离说明 | 第二个测试隔离提交 | 不改变产品行为 |

## 未决项处理

- 16 个 C 类报告已包含在工作区外只读 untracked 备份中。
- 本轮不删除、不改写、不 stage，也暂不加入 `.gitignore`。
- 后续由维护者选择：若需要完整迭代可追溯性，则单独审阅提交；若确认可再生成，则在独立清理任务中加入明确 ignore 规则。不能在本阶段猜测处理。
