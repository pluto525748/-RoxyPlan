# RoxyPlan V1.8.2 完成与继续推进报告

更新时间：2026-07-28

本文是本轮工作的最终接手文档，记录结论、文件来源、验证证据、未解决风险和下一阶段入口。

## 1. 当前总体结构

RoxyPlan 是 Windows 本地 Python + PySide6 桌宠。`frontend/` 是桌面壳，`modules/` 是 Qt-free 对话/Agent/业务核心，`modules/repositories/` 负责本地 JSON，`server/` 是复用核心的 Local Web 适配层，`assets/` 保存桌宠素材，`tests/` 验证真实入口和模块边界。

详细清单见 `docs/current_architecture_inventory.md`。

## 2. 真实启动入口

- 桌面：`roxy.bat` → `frontend/pet_app.py:main()`。
- Local Web：`run_roxy_web.bat` → `uvicorn server.main:app`。
- 当前恢复环境：项目 `.venv`，Python 3.10.20；桌面与 Web 依赖、pytest 已安装。

## 3. 桌面聊天真实调用链

`ChatWindow.send_message → ConversationService.prepare → Intent/Semantic/BusinessResolver → AgentCore/LLM → AgentResponse → UI线程 _present_agent_response → 显示消息`。

模型请求在 QThread；Qt 动作只在 UI 线程执行。模型能力已提升，可提高理解和工具提议质量，但不会扩大本机权限。

## 4. 桌宠动作真实调用链

`Intent/固定兼容命令 → play_dance ToolResult → AgentResponse.client_actions → DesktopClientActionDispatcher → PetActionManager → DesktopPet 帧播放器 → restore_idle`。

## 5. 发现的重复动作入口

本轮前存在：

1. `ChatWindow.handle_dance_command` 直接调用 `DesktopPet.start_dance()`。
2. ToolRegistry `play_dance` 直接调用 pet controller。
3. AgentResponse 已有 client_actions 字段，但真实 UI 完成路径只显示 message。
4. DesktopPet 还提供 `execute_client_action`，但未成为统一入口。

本轮后，旧方法仅作为 wrapper，全部委托同一个 Dispatcher。

## 6. “再跳一个舞”只回复不跳的根因

“再跳一个舞”不进入 UI 的简单固定短语分支，而进入 Semantic/Agent 路径。该路径可能在 ToolRegistry 阶段尝试从后台线程直接调用 Qt，同时 UI 在拿到 AgentResponse 后只渲染 `message`，不处理 `client_actions`。因此工具层的成功文本和桌面实际播放没有共同执行凭证。

## 7. client_action 丢失位置

契约序列化本身没有丢失；核心问题是：

- ToolResult 没有稳定地产生声明式动作；
- AgentCore/ResponseComposer 没有保证附加动作；
- ChatWindow 的同步、异步和确认回复路径都没有统一消费动作。

三个位置现均已补齐，并有序列化与真实入口测试。

## 8. PetActionManager 状态问题

旧播放器在 timer 已激活时会先停止旧舞蹈再启动新舞蹈，缺少明确 busy 结果；异常释放只依赖正常帧链。本轮采用策略 B：运行中第二次请求返回 `skipped_busy`；异常、取消和 30 秒 failsafe 均恢复 idle。

## 9. 最终统一动作入口

正式入口是 `frontend/client_action_dispatcher.py:DesktopClientActionDispatcher`。右键菜单、固定命令兼容入口和 Agent 路径都生成 ClientAction。`modules/` 不 import PySide6 或 PetActionManager。

## 10. 回复—动作一致性

桌面收到 AgentResponse 后：先验证并派发动作，获取 running/rejected 初步结果，再由 `ClientActionClaimGuard` 修正消息，最后显示。busy、expired、failed 不允许继续显示“好，我跳一小段”。

## 11. 新增和修改文件

新增代码：

- `modules/client_action.py`
- `modules/client_action_result.py`
- `modules/client_action_claim_guard.py`
- `frontend/client_action_dispatcher.py`

修改代码：

- `modules/contracts.py`
- `modules/client_action_policy.py`
- `modules/tool_registry.py`
- `modules/agent_core.py`
- `modules/response_composer.py`
- `modules/intent_router.py`
- `frontend/pet_action_manager.py`
- `frontend/desktop_pet.py`
- `frontend/pet_app.py`
- `frontend/settings_dialog.py`

新增测试：八个附件指定的动作/架构测试文件；调整 `test_tool_call_loop.py` 的旧直接调用预期。

新增文档：架构清单、运行链、动作协议、状态机、技术债、手动验收和本报告。

## 12. 删除或兼容的旧代码

没有大规模删除。`start_dance()`、`execute_client_action()`、`handle_dance_command()` 被保留为兼容 wrapper，但不再独立执行动画。没有删除 IntentRouter、Agent 路径、Manager 或历史数据兼容。

## 13. 技术债

见 `docs/technical_debt_register.md`。高优先级动作链问题已处理；ChatWindow 过重、Intent/固定命令历史并存、Web 动作能力协商和 feature flag 退役仍需后续渐进处理。

## 14. Feature flags

- `unified_client_action_dispatcher_enabled = true`
- `client_action_claim_guard_enabled = true`
- `pet_action_state_machine_enabled = true`
- `legacy_direct_pet_action_enabled = false`

默认值在 `frontend/settings_dialog.py`，未强制改写用户现有 `data/pet_config.json`。

## 15. 自动测试结果

- 全量 pytest：385 passed，5 warnings。
- 动作旧测试：39 passed。
- V1.8.2 新测试：18 passed。
- Python compileall：通过。
- JavaScript `server/web/app.js` 语法检查：通过（使用工作区 Node）。
- `git diff --check`：通过，仅有 Git 的 LF→CRLF 工作区提示。
- `tests/check_public_repo.py`：通过。
- 舞蹈素材 Git 状态：无修改。

5 条 warning 是既有辅助 `TestClock` 类有自定义构造器，不影响测试执行。

## 16. 真实对话验收

自动序列已覆盖：`跳舞 → 再跳一次 → 再跳一个舞`，三次均产生 `play_dance` 且 action_id/idempotency_key 各自唯一；`我不想跳舞`、`真棒` 无动作。UI Dispatcher 的真实 Qt 帧完成恢复由既有和新增状态机测试覆盖。

## 17. 原舞蹈资源兼容

未修改任何 `assets/pet/dance/` 文件、排序函数、帧时长常量、循环次数和绘制代码。

## 18. 未解决风险

- 完整人工可视验收仍需在 Windows 桌面实际观察三段舞蹈。
- 动作完成状态没有异步回传给远端 Agent；当前只需 accepted/running 即显示回复。
- Web 返回 ClientAction，但网页不是桌宠动画执行端。
- 模型升级后的 Provider 名称、上下文长度和成本策略需单独验收，不能与本轮动作权限边界混合。

## 19. 回滚方式

优先通过 flags 回滚：关闭统一 Dispatcher/ClaimGuard，并仅在紧急情况下开启 `legacy_direct_pet_action_enabled`。不得同时开启新旧真实执行造成双动作。Git 未 commit/push，亦可按本轮文件清单逐项审查后回退；禁止回退私人 JSON。

## 20. 下一阶段建议

1. 先按 `manual_acceptance_client_actions.md` 做 Windows 可视验收。
2. 将模型能力提升单独建立 Provider/Tool Calling 验收矩阵，确认理解提升但权限不扩大。
3. 渐进瘦身 ChatWindow：优先迁移剩余 UI 固定命令为同一 Service 契约，不重写计划/记忆。
4. 为 ClientAction 增加可选的异步完成遥测，仅在确有云端同步需求时实施。
5. 清理 pytest `TestClock` 警告和 flag 退役表。

## 文件来源索引

后续推进时优先阅读：

1. 产品/当前状态：`README.md`、`docs/current_status.md`。
2. 当前架构：`docs/current_architecture_inventory.md`。
3. 真实调用链：`docs/runtime_call_chains.md`。
4. 动作契约：`docs/client_action_protocol.md`。
5. 状态机：`docs/pet_action_state_machine.md`。
6. 技术债：`docs/technical_debt_register.md`。
7. 手动验收：`docs/manual_acceptance_client_actions.md`。
8. 真实代码入口：`frontend/pet_app.py`、`server/main.py`。
9. 核心业务入口：`modules/conversation_service.py`、`modules/agent_core.py`、`modules/tool_registry.py`。
10. 动作执行入口：`frontend/client_action_dispatcher.py`、`frontend/pet_action_manager.py`、`frontend/desktop_pet.py`。
11. 测试证据：`tests/test_client_action_*.py`、`tests/test_dance_repeat.py`、`tests/test_action_runtime_entrypoints.py`、`tests/test_architecture_boundaries.py`。
