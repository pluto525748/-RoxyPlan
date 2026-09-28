# RoxyPlan 技术债登记（V1.8.3）

| 优先级 | 问题 | 相关文件 | 当前影响/真实运行 | 建议 | 风险 | 工作量 | 本轮 |
|---|---|---|---|---|---|---|---|
| P0 | Agent 路径曾从 ToolRegistry 直接调用 Qt 桌宠 | `modules/tool_registry.py`、`frontend/pet_app.py` | 真实桌面链；QThread 可能触碰 UI，回复与动画脱节 | Tool 只声明 ClientAction，UI Dispatcher 执行 | 虚假成功、线程不安全 | 中 | **已处理** |
| P0 | UI 曾只渲染 AgentResponse.message，不消费 client_actions | `frontend/pet_app.py` | 真实桌面链 | 显示前统一派发并获取结果 | 动作丢失 | 中 | **已处理** |
| P0 | 舞蹈运行中可被 `_start_dance_frames` 静默停止并重启 | `frontend/desktop_pet.py`、`pet_action_manager.py` | 真实播放链 | Manager 先判 busy，策略 B | 状态错乱 | 小 | **已处理** |
| P0 | 动作成功文案只依赖 ToolResult，不依赖桌面接受结果 | `agent_core.py`、`pet_app.py` | 真实回复链 | ClientActionClaimGuard 覆盖 busy/expired/failed | 虚假成功 | 中 | **已处理** |
| P1 | `ChatWindow` 同时承担组合根、UI 和大量历史命令兼容 | `frontend/pet_app.py` | 真实入口 | 后续按领域逐步迁移到 Service；不一次性重写 | 回归面大 | 大 | 登记 |
| P1 | IntentRouter、SemanticActionParser 与部分 UI 固定命令并存 | `intent_router.py`、`semantic_action_parser.py`、`pet_app.py` | 真实兼容链 | 固定高精度命令统一输出相同契约，逐项退役 | 双路径 | 中/大 | 动作部分已收口 |
| P1 | `ClientAction` 仍为 contracts 中定义、独立模块重导出 | `modules/contracts.py`、`client_action.py` | 真实契约 | 等外部导入稳定后再物理迁移，避免循环依赖 | 导入兼容 | 小 | 兼容保留 |
| P1 | Web 返回动作但当前页面不是桌宠执行客户端 | `server/schemas.py`、`server/web/app.js` | Web 真实链 | 明确客户端能力协商；服务端禁止执行 Qt | 体验误解 | 中 | 文档化 |
| P1 | Feature flags 多且部分只影响组合时配置 | `settings_dialog.py`、`conversation_service.py` | 真实启动链 | 建立 flag 退役表与组合测试 | 组合复杂 | 中 | 动作 flags 已登记 |
| P1 | 模型能力提升后可能扩大 Tool Calling 覆盖 | `modules/llm/`、`model_action_adapter.py` | 可选真实链 | 保持“更强理解，不扩大本地权限”；增加模型版本验收集 | 安全/一致性 | 中 | 原则已记录 |
| P2 | pytest 中 5 个 `TestClock` 类有收集警告 | 多个测试文件 | 不影响 385 项通过 | 重命名辅助类或设置 `__test__ = False` | 噪声 | 小 | 未处理 |
| P2 | `backend/` 只有意图 README | `backend/README.md` | 不在运行链 | 继续作为边界说明，避免误认为真实服务 | 认知成本 | 小 | 文档化 |
| P2 | 动作完成结果当前主要记录在 Manager/日志，未回写远端 Agent | Dispatcher/Manager | 桌面动作不需阻塞回复 | 若未来云端需要遥测，再设计异步回执；当前不扩展 | 观测有限 | 中 | 未处理 |

本轮没有删除业务模块、IntentRouter、Manager 或兼容数据路径。唯一行为替换是让旧动作 wrapper 委托统一 Dispatcher；没有无证据的大规模死代码删除。

## V1.8.3 新增/变化

| 优先级 | 问题 | 处理 | 剩余风险 |
|---|---|---|---|
| P0 | UTC aware 动作过期时间被本地 naive 时间按 UTC 强行解释，所有舞蹈动作在 UTC+8 立即过期 | ClientActionPolicy 默认时钟改为 UTC aware；naive/aware 比较使用本地时区转换；真实 Qt 两次舞蹈回归通过 | 可见桌面手工验收仍需在可用 GUI 会话完成 |
| P0 | 礼貌包装让稳定查询落入添加计划 | 统一移除通用对话外壳；只增加“查看动作 + 业务域”的高精度查询规则 | 当前评测变体有限，不代表任意方言 |
| P1 | LLMPlanner 可在 unified 解析后再次理解原文 | authoritative semantic result 禁止进入 LLMPlanner | legacy profile 仍允许，计划后续停用 |
| P1 | Feature flags 可全关或新旧同开 | 权威默认、矩阵验证、启动警告和兼容回滚 profile | 旧私有配置可能触发警告，需要用户决定是否重写配置；本轮不改私有文件 |
| P1 | 基准实体、引用和多轮自动指标覆盖仍不完整 | 单轮闭集、对抗、多轮语料与真实回归已建立 | 后续需增加人工校验 gold entities 和独立生成的语言改写 |
| P2 | IntentRouter 体积较大并混合历史兼容规则 | 作为 SemanticActionParser 内部路由保留，记录 deprecation plan | 不进行高风险大拆分 |
