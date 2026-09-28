# RoxyPlan 文档索引

更新时间：2026-09-27

RoxyPlan 的产品阶段仍为 **V0.9 本地成长陪伴桌宠原型**；V2.2 是既有中文交互与成长闭环的稳定化工作流，不代表新增产品阶段。文档分为“当前约定与状态”“专题设计”“版本历史”和“研究资料”；旧版本文档保留带日期的证据，不覆盖最新明确决定。文档不能替代真实执行结果或当天验证。

## 建议阅读顺序

1. [`../README.md`](../README.md)：项目说明书、安装、启动、核心能力和隐私边界。
2. [`../AGENTS.md`](../AGENTS.md)：硬限制、目录边界与接手规则。
3. [`capability_boundaries.md`](capability_boundaries.md)：当前能力范围、推荐自然表达、确认与可靠退出契约；上线操作说明的维护依据。
4. [`project_workflow.md`](project_workflow.md)：稳定项目档案、用户开发习惯、协作偏好、最小修复和数据保护。
5. [`agent_engineering_experience.md`](agent_engineering_experience.md)：历史来源去重后的 Agent Engineering 经验、认知修正、适用边界和复盘模板。
6. [`current_status.md`](current_status.md)：带日期的完成度、验证状态和剩余问题；最新测试结果只在此维护。
7. [`chinese_interaction_reliability.md`](chinese_interaction_reliability.md) 与 [`../tests/README.md`](../tests/README.md)：中文资料来源、隔离验证、测试 lane 和报告方式。
8. [`v2_2_agent_handover.md`](v2_2_agent_handover.md)：V2.2 带日期的历史基线与交接证据，接手时仍需刷新实际状态。
9. [`current_architecture_inventory.md`](current_architecture_inventory.md) 与 [`runtime_call_chains.md`](runtime_call_chains.md)：模块职责和运行链。两者仍含 V1.8.3 历史描述，遇到冲突时核对当前约定、状态及实际代码，不能用旧架构说明扩大能力。

## 当前专题

- [`product_principles.md`](product_principles.md)：产品与安全原则。
- [`semantic_action_protocol.md`](semantic_action_protocol.md)：结构化语义动作契约的历史演进。
- [`business_resolver.md`](business_resolver.md)：业务对象、参数和引用校验边界。
- [`tool_schema_and_validation.md`](tool_schema_and_validation.md)：工具 schema 与执行校验。
- [`response_truthfulness.md`](response_truthfulness.md)：ToolResult 与最终回复事实边界。
- [`interaction_state_machine.md`](interaction_state_machine.md)：按 conversation 隔离的澄清、确认和引用状态。
- [`memory_system.md`](memory_system.md)：正式记忆、候选兼容、冲突、审计和归档。
- [`chat_history_system.md`](chat_history_system.md)：消息、摘要与上下文连续性。
- [`development_logging.md`](development_logging.md)：最小开发事件、逐轮关联、按钮来源与只读最近交互排查。
- [`roxy_personality.md`](roxy_personality.md)：洛琪希人格设计原则；运行时人格包位于 `data/personas/roxy/`。
- [`client_action_protocol.md`](client_action_protocol.md) 与 [`pet_action_state_machine.md`](pet_action_state_machine.md)：声明式客户端动作和桌宠状态机。
- [`local_web_v0_1.md`](local_web_v0_1.md)：可信局域网 Local Web 边界。
- [`storage_architecture.md`](storage_architecture.md)：本地 Repository 和 JSON 存储边界。

## 当前里程碑

- [`CHANGELOG.md`](CHANGELOG.md)：版本变更历史；V1.9—V2.1 的最终条目仍需在里程碑封板时统一补齐。
- [`ROADMAP.md`](ROADMAP.md)：产品路线；其中 V1.8.3 以前的版本描述保留历史意义。
- [`technical_debt_register.md`](technical_debt_register.md)：技术债登记。
- [`deprecation_plan.md`](deprecation_plan.md)：旧入口的兼容与停用计划。

## 历史与研究

- `analysis/`：阶段性架构审计、基线和完成报告；[`roxyplan_local_asset_map_2026-09-27.md`](analysis/roxyplan_local_asset_map_2026-09-27.md) 是本地目录整理前的只读资产快照，不作为永久运行状态。
- `research/`：开源架构、工具调用和本地规则研究。
- `v1_*_completion_report.md`、`release_*.md`、`manual_acceptance_*.md`：对应版本的历史验收记录。
- `product_design.md`、`memory_design.md`、`voice_system.md`、`mobile_design.md`、`knowledge_feed_design.md`：包含长期设计或未来规划，不能全部视为已实现。

## 状态用语

- “已实现”：代码已经存在，并有隔离自动测试或真实运行证据。
- “离线验证通过”：只代表 Stub/Fake 与本地程序边界通过，不代表在线模型质量。
- “真实模型验收通过”：必须有真实 Provider、模型名、时间、案例数和脱敏报告。
- “计划中”：尚未完成，不应出现在用户能力承诺中。
- 任何缺少真实运行证据的在线验收都必须标记为“待运行”。
