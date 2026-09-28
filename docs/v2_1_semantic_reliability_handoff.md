# RoxyPlan V2.1 统一语义与中文交互可靠性交接

文档日期：2026-08-04（最终更新）

## 1. 交接状态

- 工作分支：`feature/v2.0-conversation-continuity`
- 本轮开始基线：`944e0f5`（`fix: save previous assistant replies as formal memory`）
- 当前里程碑：**V2.1 中文语境稳定化已完成**（离线矩阵、盲测、在线验收、全量回归、交接文档全部完成）
- 本轮后续 commit：待用户决定是否提交
- push：未授权，不得 push
- 在线真实模型：**已完成 Flash 和 Pro 双模型验收**
  - deepseek-v4-flash：3 轮，44/60 = 73.3%
  - deepseek-v4-pro：3 轮，46/60 = 76.7%（Pro 修复了 write_004 编码问题 0/3→2/3）

工作区可能保留用户原有 `data/pet_config.json` 修改。它不属于功能或文档提交，禁止修改、还原或暂存。

## 2. 当前产品基线

已完成的关键里程碑：

- V1.9：命令/载荷隔离、模糊写批次零执行、会话状态隔离、正式记忆直存与 scoped undo、候选主入口降级、桌面旧 handler 收口。
- V2.0：PersonaPack / PersonaRegistry、设置面板 persona 选择、统一 ContextBuilder、情景摘要和跨窗口相关历史检索。
- V2.1：唯一 SemanticDecision 合同、非精确自然语言统一主链、竞争 fallback 退役、只读结果事实保护、正式记忆自然表达、上一条用户/assistant 消息正式保存和 MemoryUI 刷新。

当前不做：第二人格包、聊天切换角色、角色专属 Repository、每日自动新窗口、完整历史原文 RAG、向量数据库、语音、蒸馏训练或公网部署。

## 3. 生产调用链

```text
ChatWindow / Local Web
→ ConversationService.prepare
→ 精确命令门（只限完整匹配）
→ 单一 SemanticDecision
→ BusinessResolver / ReferenceResolver / SafetyPolicy
→ AgentPlanner / ActionBatch
→ ToolExecutor / ToolRegistry
→ ToolResult
→ ResponseComposer / ActionClaimGuard
→ ConversationService.complete
→ DesktopClientActionDispatcher（桌面声明式动作）
→ 最终回复与历史记录
```

关键不变量：

- 一条非精确消息只有一次正式语义决策。
- Resolver 只能校验、绑定、拒绝或澄清，不重新猜意图。
- 回复模型不能在 FinalResponse 后提出新工具。
- read ToolResult 是查询事实证据，但不能证明写入成功。
- write 只有匹配工具成功并返回必要资源 ID，才能用完成式回复。
- UI 不直接写 `memory.json` 或成长 JSON。

## 4. 精确命令与自然语言边界

保留本地精确入口：

- 完整“跳舞”等无歧义桌宠动作。
- 设置、面板和 UI 按钮命令。
- 当前会话确有 pending 时的确认/取消。
- 少量高精度正式数据读取合同可以直接产生同一 SemanticDecision；它不能在主链后再次扫描或产生第二次决策。

必须进入统一语义合同的自然语言示例：

- “我喜欢看你跳舞”
- “我正在做一个 AI 陪伴计划桌宠”
- “今天还有什么计划”
- “把复习随机森林加入今天计划”
- “请记住我更适合早上学习”
- “把你刚才的回复加入长期记忆”
- “给我看看计划，顺便讲讲逻辑回归”

禁止恢复：宽泛关键词 IntentRouter、普通回复后的 JSON action fallback、UI 旧 natural-intent handlers、候选记忆对普通聊天的抢占。

## 5. 记忆边界

### 正式保存

canonical tool：`save_formal_memory`

- 显式“记住/保存长期记忆”本身是低风险完整内容的授权。
- 普通陈述默认只聊天。
- 内容不明确、敏感、冲突或覆盖时最多确认/澄清一次。
- 成功必须返回真实 `memory_id`；否则回复不能声称已经保存。
- `last_created_memory_id` 只存在当前 conversation 的短 TTL 内，撤销调用 archive。

### 消息引用

- `previous_user_message`：当前 conversation 最近一条适合保存的用户消息。
- `previous_assistant_message`：当前 conversation 最近一条 assistant 回复。
- 不保存当前命令本身，不引用其他窗口，不从整段摘要猜测原句。
- 缺少来源时返回 clarification，工具数为零。

### 候选兼容

候选、冲突、审计和归档代码及数据保留，但普通聊天主链不创建、列出或确认候选；普通用户界面不把候选作为主要入口。不要删除或迁移已有候选数据。

## 6. 人格与上下文

- 当前人格包：`data/personas/roxy/`。
- `PersonaRegistry` 扫描与选择；`PersonaPack` 加载和校验。
- `active_persona_id` 只从设置/配置入口修改。
- 固定注入：安全事实边界、identity、behavior、speaking style、truth boundaries。
- 按需检索：relationships、lore、dialogue examples。
- `ContextBuilder` 保证当前用户消息只出现一次且位于最后；旧摘要进入 reference/summary section，不伪装为 user message。
- 旧会话摘要按当前 query 每轮重新检索，排除当前 session，并默认限制当前 persona。
- persona 只能润色 ToolResult 事实，不能改变结果。

## 7. 中文可靠性矩阵

夹具：`tests/fixtures/v21_chinese_context_matrix.json`

测试：`tests/test_chinese_context_matrix.py`

当前设计为 77 条研究驱动合成案例：

- chat 30
- read 16
- write 26
- clarify 5

方法与论文来源见 `docs/chinese_interaction_reliability.md`。这些是合成最小对照，不是真实用户语料，也不应被描述成真实在线模型通过结果。

扩展原则：先归纳语言现象和失败层，再添加最小对照；禁止为单句增加关键词特判。

## 8. 自动验证

建议顺序：

```powershell
# 中文矩阵
.\.venv\Scripts\python.exe -m pytest tests\test_chinese_context_matrix.py -q --basetemp C:\Users\Public\RoxyPlan-chinese-matrix

# ConversationService、路由、状态、计划、记忆、人格、ContextBuilder 和桌面组合根相关测试
.\.venv\Scripts\python.exe -m pytest tests\test_unified_semantic_pipeline.py tests\test_client_action_runtime.py tests\test_natural_language_route_repair.py tests\test_v20_persona_packs.py tests\test_v20_conversation_continuity.py -q --basetemp C:\Users\Public\RoxyPlan-core

# 全量
.\.venv\Scripts\python.exe -m pytest -q --basetemp C:\Users\Public\RoxyPlan-full

# 编译和 diff
.\.venv\Scripts\python.exe -m compileall -q modules frontend server tests
git diff --check
```

最终结果占位：

- 中文矩阵：**113 passed（78 案例 + 标点不变性 15 案例 + 合同 20 案例）**
- 核心联合：**48 passed**
- 全量 pytest：**759 passed**
- Python 编译：**Exit 0**
- `git diff --check`：**Exit 0（仅 Windows CRLF 标准警告）**
- 私人数据清单/SHA-256：**全部未变化，测试防火墙有效**

**不要在没有退出码和报告的情况下把占位改成”通过”——本条已完成，以下为实际运行结果。**

## 9. 在线真实模型验收

脚本：`scripts/run_online_chinese_semantic_acceptance.py`

它只从环境变量读取 Key，使用隔离虚构数据，默认把 JSON 报告写到项目外。示例：

```powershell
$env:DEEPSEEK_API_KEY = "仅当前终端"
.\.venv\Scripts\python.exe scripts\run_online_chinese_semantic_acceptance.py --limit 5
```

当前状态：**已完成**（2026-08-04，双模型各 3 轮 × 20 案例）。

| 模型 | 通过 | 一致通过 | 一致失败 | 不稳定 | Token/case | 延迟 |
|---|---:|---:|---:|---:|---:|---:|
| deepseek-v4-flash | 44/60 (73.3%) | 14 | 5 | 1 | ~972 | ~1066ms |
| deepseek-v4-pro | 46/60 (76.7%) | 14 | 4 | 2 | ~994 | ~4692ms |

Pro 改进：write_004 编码问题从 Flash 的 0/3 修复到 Pro 的 2/3。
共同持续失败（4 cases）：均为模型端问题（编码、多意图理解、意图粒度、实体推断），程序校验层正确拒绝。

报告位置：
- Flash：`C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-123*.json`
- Pro：`C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-13*.json`

## 10. 诊断日志

默认路径：`logs/interaction_diagnostics.jsonl`

期望写工具成功链：

```text
SemanticDecision
→ Validation / ReferenceResolution
→ ToolExecution:save_formal_memory
→ ToolResult:save_formal_memory:success
→ MemoryAudit:create|update
→ FinalResponse
```

期望普通聊天链：

```text
SemanticDecision
→ Validation
→ FinalResponse
```

审查要点：

- 同 request_id 只有一个 `SemanticDecision`。
- `tool_calls` 与 `tool_results` 数量和名称匹配。
- chat/read 不产生无关 write pending。
- FinalResponse 后无第二工具来源。
- `normalized_text` 只有 length 和 SHA-256。
- 日志不含用户全文、正式记忆正文、Key 或模型内部推理。

日志可以证明工具链，不能单独证明自然语言文风。缺少时间戳或 runtime 标签时，不要猜测记录来自哪次运行。

## 11. 测试数据隔离

必须保持：

- 所有自动测试使用完整临时 Repository，而不是只替换一个 memory path。
- 使用项目外 `basetemp`。
- pytest 期间阻止写入项目根 `memory.json` 和 `data/private/**`。
- 测试前后比较真实敏感文件清单、大小和 SHA-256。
- 防火墙失败时立即停止，不自动恢复、删除或清理真实数据。
- 在线验收也使用临时数据；环境变量 Key 不写文件、不进日志。

禁止触碰：

- `data/pet_config.json`
- `memory.json`
- `data/private/**`
- 用户指定受保护 memory_id

## 12. 资料与提交来源

建议从以下来源理解项目，而不是只读 README：

- Git 历史：V1.9、V2.0 和 V2.1 的原子提交。
- `modules/conversation_service.py`：生产编排。
- `modules/semantic_action_parser.py` 与 `modules/intent_router.py`：统一合同和精确入口。
- `modules/business_resolver.py`、`modules/reference_resolver.py`：参数、真实对象和消息引用。
- `modules/tool_registry.py`、`modules/tool_executor.py`：canonical tool 与执行。
- `modules/response_composer.py`、`modules/action_claim_guard.py`：最终回复真实性。
- `modules/persona_registry.py`、`modules/persona_pack.py`、`modules/context_builder.py`：人格和上下文。
- `modules/chat_history_manager.py`：情景摘要。
- `frontend/pet_app.py`、`server/agent_service.py`：桌面与 Web composition root。
- `tests/`：隔离数据、生产型入口和回归事实。
- `logs/interaction_diagnostics.jsonl`：脱敏真实运行证据。

## 13. 下一位开发者的停止条件

出现以下任一情况应停止提交并先修复：

- 自动测试触碰真实私人数据。
- 一条非精确消息出现两次 SemanticDecision。
- FinalResponse 后出现新工具来源。
- tools=0 或工具失败，却声称写入成功。
- read ToolResult 被当成未执行写操作。
- 新 conversation 消费旧窗口 pending、序号或“刚才”。
- 普通聊天创建正式记忆或候选。
- 在线凭据进入日志、测试夹具或 Git diff。

## 14. 建议的后续顺序

1. 完成本轮离线矩阵和全量回归。
2. 运行小规模在线语义验收，按失败层分类。
3. 将真实失败转成最小对照，不写逐句补丁。
4. 更新本交接文档中的实际测试结果、最终 commit 和私人数据哈希结论。
5. 用户确认真实桌面体验后再考虑 V2.1 封版。
6. 封版前不开始第二角色、语音、数据库或蒸馏工具。
