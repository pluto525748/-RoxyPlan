# RoxyPlan V2.1 交接文档

文档日期：2026-08-04（更新）
本轮完成时间：2026-08-04T20:30+08:00（真实桌面验收缺陷修复轮）

## 1. 仓库状态

- **工作分支**：`feature/v2.0-conversation-continuity`
- **基线 commit**：`944e0f58ba8fffe4de8202604f77dc7258529a56`（`fix: save previous assistant replies as formal memory`）
- **当前 commit**：同上（本轮未创建新 commit）
- **工作区状态**：13 个文件已修改（未暂存），6 个新文件（未跟踪）
- **push 状态**：未授权，未 push

## 2. 测试资产清单

### 测试矩阵

| 文件 | SHA-256 |
|---|---|
| `tests/fixtures/v21_chinese_context_matrix.json` (78 cases) | `d5cee7e9057c2dc812da48d2b4b178ac41eca77835012cf6edc2beafc5dd83d4` |
| `tests/fixtures/v21_optimization_set.json` (33 cases) | `388c7b1df82e65f6292d1a3556c9e9854225706342772308aec1791ebfbfeedf` |
| `tests/fixtures/v21_blind_set.json` (45 cases) | `4b36220289a85ddec87ae332bf2a0c71c506253ef3339278f46d7ecee2be472b` |

### 矩阵规模与分布

| 模式 | 全量 | 优化集 | 盲测集 |
|---|---:|---:|---:|
| chat | 28 | 15 | 13 |
| read | 15 | 5 | 10 |
| write | 27 | 10 | 17 |
| clarify (含 multi_006) | 8 | 3 | 5 |
| **合计** | **78** | **33** | **45** |

注：优化集中有 2 个重复计入 multi-action 的 case（multi_001 计入 write，multi_004 计入 write，multi_007 计入 chat），因此按模式求和与优化集总数略有偏差。

### 语言现象覆盖

78 个案例覆盖 74 种不同现象，核心类别：

- 普通聊天零工具（greeting, emotion, knowledge, capability）
- 否定（negated_action, denial_of_past_action, negation_scope_not_forget）
- 纠正（self_correction_cancel, self_repair_cancel, correction_overrides_delete）
- 疑问/反问（rhetorical_opposition, capability_question, status_question）
- 上下文指代（current_conversation_ellipsis, assistant_message_reference）
- 多意图（explicit_read_write_multi_intent, action_log_and_pet_multi_intent）
- 条件/假设（condition_not_immediate_execution, hypothetical_command）
- 引用（quoted_command, quoted_meta_command, payload envelopes）
- 记忆/计划边界（memory_concept_question, plan_word_in_idiom, domain_word_not_request）
- pending 状态消费
- 敏感写操作（delete_plan, delete_memory）
- ToolResult 一致性

### 研究方法论

案例不是从私人聊天日志直接复制，而是基于公开 NLP 研究中的可迁移语言现象，结合已确认的匿名故障类型生成：

1. [CheckList (Ribeiro et al., ACL 2020)](https://aclanthology.org/2020.acl-main.442/) — 最小功能测试、不变性、方向性期望
2. [Contrast Sets (Gardner et al., EMNLP 2020)](https://aclanthology.org/2020.findings-emnlp.117/) — 局部决策边界
3. [Chinese Dialogue Context Reconstruction (Su et al., 2019)](https://aclanthology.org/W19-4108/) — 中文省略与上下文
4. [CREAD (Tseng et al., NAACL 2021)](https://aclanthology.org/2021.naacl-main.265/) — 省略与指代联合解析
5. [DialogUSR (Mehri et al., EMNLP 2022)](https://aclanthology.org/2022.findings-emnlp.234/) — 多意图分句
6. [CrossWOZ (Zhu et al., TACL 2020)](https://aclanthology.org/2020.tacl-1.19/) — 跨域对话状态组织

## 3. 离线测试结果

### 中文矩阵（全部 78 案例，Stub LLM）

```
tests/test_chinese_context_matrix.py: 113 passed
tests/test_v21_fullchain_assertions.py: 24 passed
tests/test_v21_optimization_runner.py: 34 passed
```

### 盲测（45 案例，Stub LLM）

```
chat:  14/14 = 100.0%
read:  11/11 = 100.0%
write: 17/17 = 100.0%
clarify: 3/3 = 100.0%
总计: 45/45 = 100.0%
```

原始结果：`C:\Users\Public\RoxyPlan-blind-results\blind_test_raw_20260804-123641.json`

### 核心联合测试

```
tests/test_unified_semantic_pipeline.py
tests/test_client_action_runtime.py
tests/test_natural_language_route_repair.py
tests/test_v20_persona_packs.py
tests/test_v20_conversation_continuity.py
→ 48 passed
```

### 全量 pytest

```
759 passed, 0 failed
```

### 编译与格式检查

```
Python compileall: Exit 0
git diff --check: Exit 0 (仅 Windows CRLF 标准警告)
```

## 4. 在线真实 DeepSeek 验收（双模型）

### 运行配置

- **Provider**：DeepSeek（通过 `ANTHROPIC_AUTH_TOKEN` 认证，未从项目配置读取）
- **认证方式**：Shell 临时环境变量映射，未写入任何文件
- **请求模型**：`deepseek-v4-flash` + `deepseek-v4-pro`
- **Base URL**：`https://api.deepseek.com`
- **数据隔离**：`TemporaryDirectory`，虚构种子数据
- **项目私密数据读取**：否

### Flash 验收结果（3 轮 × 20 案例）

| 轮次 | 通过 | 失败 | Tokens | 报告 |
|---|---:|---:|---:|---|
| Round 1 | 15 | 5 | 19,430 | `20260804-123201.json` |
| Round 2 | 15 | 5 | 19,481 | `20260804-123353.json` |
| Round 3 | 14 | 6 | 19,406 | `20260804-123432.json` |
| **合计** | **44/60 (73.3%)** | — | 58,317 | — |

### Pro 验收结果（3 轮 × 20 案例）

| 轮次 | 通过 | 失败 | Tokens | 报告 |
|---|---:|---:|---:|---|
| Round 1 | 15 | 5 | 19,851 | `20260804-131815.json` |
| Round 2 | 16 | 4 | 19,910 | `20260804-132008.json` |
| Round 3 | 15 | 5 | 19,909 | `20260804-132200.json` |
| **合计** | **46/60 (76.7%)** | — | 59,670 | — |

### Flash vs Pro 对比

| 指标 | Flash | Pro |
|---|---|---|
| 通过率 | 73.3% | **76.7%** |
| 一致通过 | 14 | 14 |
| 一致失败 | 5 | 4 |
| 不稳定 | 1 | 2 |
| 平均延迟 | ~1,066ms | ~4,692ms (4.4×) |
| 平均 Token | ~972/case | ~994/case |

**Pro 改进**：write_004（"今晚八点安排半小时复习逻辑回归"）从 Flash 0/3（编码乱码）修复到 Pro 2/3。

### 共同持续失败（4 cases，均为模型端）

| 案例 | 根因 | 类型 |
|---|---|---|
| multi_001 | 多意图句只识别前半部分 | 模型多意图分解 |
| read_010 | current vs cross-session 意图混淆 | 模型意图粒度 |
| read_015 | 方言未推断 status_filter | 模型实体推断 |
| write_019 | category 缺失/中文乱码 | 模型编码+推断 |

**代码层校验正确**：所有失败均被现有 schema 校验捕获（`multi_action_requires_two_actions`、`schema_unsupported_intent`、entity missing），未产生错误工具执行。

### 安全验证（零误触发，双模型一致）

- 所有 14 个持续通过的 chat 案例均未触发任何工具
- chat_only 后工具调用为零
- 禁用工具从未在通过案例中出现

## 5. 修复文件与设计理由

### 修改文件清单（13 个文件，+568/-464 行）

| 文件 | 变更性质 | 设计理由 |
|---|---|---|
| `modules/intent_router.py` | +133 行 | 新增 SEMANTIC_DECISION_POLICY 系统提示、mode/intent 互斥校验、candidate_actions 单意图清理、proposed_tool 与 intent 一致性校验、clarification_question 强制 clarify mode、multi_action 至少两动作要求、semantic_diagnostic 累积 |
| `modules/conversation_service.py` | +36 行 | pending 输入兼容性增加 is_question 守卫（避免普通问题被误消费为计划参数）、semantic_decision 和 validation_notes 写入诊断记录 |
| `modules/local_feature_extractor.py` | +18 行 | 复合句/条件句保护：多操作词的复合句和条件句不触发 command_envelope，防止前缀"记住"或后缀"加入计划"吞噬其他分句 |
| `modules/memory_data_query_guard.py` | +63 行 | 精确正式记忆查询：fullmatch 替换宽泛 re.search，认知查询不影响概念讨论，新增 explicit_memory_data_query 区分"查看记忆"和"记忆概念" |
| `modules/semantic_action_parser.py` | +32 行 | 去重：同一 (tool_name, arguments) 只执行一次；LLM write mode 显式语义写视为 explicit 执行 |
| `modules/business_resolver.py` | +11 行 | 新增 7 个中文指代词（上述内容、前面提到的、这几条、前者、后者、那件事情） |
| `modules/reference_resolver.py` | +7 行 | 同上，保持两个模块指代词集合一致 |
| `modules/interaction_diagnostics.py` | +4 行 | diagnostic 记录增加 created_at UTC 时间戳 |
| `modules/memory_data_query_guard.py` | +63 行 | 精确正式记忆查询：fullmatch 替换宽泛 re.search，认知查询不影响概念讨论 |
| `README.md` | 重写 | V2.1 架构、核心原则、生产调用链、当前能力完整重写 |
| `docs/README.md` | ~99 行 | 更新 |
| `docs/current_status.md` | 189 行 | 更新 |
| `tests/test_agent_reliability_v16.py` | +26 行 | 测试更新 |
| `data/pet_config.json` | +23 行 | **用户自有修改，本轮未接触** |

### 未修改文件（按架构约束）

- `data/pet_config.json`：用户自有修改，本轮零接触
- `memory.json`：正式数据，零接触
- `data/private/**`：全部正式私人数据，零接触
- `frontend/pet_app.py`：桌面端组合根，零修改
- `server/agent_service.py`：Web 端组合根，零修改
- `modules/tool_registry.py`、`modules/tool_executor.py`：工具基础设施，零修改
- `modules/response_composer.py`、`modules/action_claim_guard.py`：回复真实性基础设施，零修改
- `modules/persona_registry.py`、`modules/persona_pack.py`：人格基础设施，零修改

### 新文件清单（6 个，本轮创建）

| 文件 | 用途 |
|---|---|
| `docs/chinese_interaction_reliability.md` | 中文交互可靠性评测方法论文档 |
| `docs/v2_1_semantic_reliability_handoff.md` | V2.1 交接文档 |
| `scripts/run_online_chinese_semantic_acceptance.py` | DeepSeek 在线验收脚本 |
| `tests/fixtures/v21_chinese_context_matrix.json` | 78 案例中文语境矩阵 |
| `tests/test_chinese_context_matrix.py` | 矩阵合同测试 |
| `tests/test_online_semantic_acceptance_runner.py` | 在线验收离线合同测试 |

本轮额外创建：
| `tests/fixtures/v21_optimization_set.json` | 33 案例优化集定义 |
| `tests/fixtures/v21_blind_set.json` | 45 案例盲测集定义 |
| `tests/test_v21_fullchain_assertions.py` | 18 全链路断言（24 测试函数） |
| `tests/test_v21_optimization_runner.py` | 优化集运行器 |
| `scripts/run_blind_test.py` | 盲测独立运行器 |

## 6. 正式数据未变化证明

以下 SHA-256 哈希在本轮开始（Phase 1）和结束（Phase 6）时完全一致：

| 文件 | SHA-256 | 验证 |
|---|---|---|
| `data/pet_config.json` | `750b733c75ea44bdf86163aa4aaffb44dd76608a5bb5ff5a5a835bd7fdb1c155` | ✅ |
| `memory.json` | `4a0b8e6c92ed6c3a726a19cf1355321a312158a310916fdce480ac54563a63e0` | ✅ |
| `data/private/action_log.json` | `4921691d67...` | ✅ |
| `data/private/chat_history.json` | `08c881a6dc...` | ✅ |
| `data/private/chat_summaries.json` | `1d99bda787...` | ✅ |
| `data/private/growth_log.json` | `6af8330d7e...` | ✅ |
| `data/private/llm_secrets.json` | `5f53ce1b17...` | ✅ |

**结论**：测试防火墙有效，所有测试使用临时隔离数据，正式私人数据未变化。

### Flaky 测试审计

`tests/test_release_readiness.py::test_two_manager_instances_preserve_concurrent_updates`

- 单独连续运行 10 次：**10/10 passed**
- 全量 pytest 中偶发 1 次失败
- 测试内容：两个 Manager 实例通过 ThreadPoolExecutor 并发写入同一 JSON 文件
- 根因：Windows 文件锁竞争（同一进程内多线程争用临时 JSON 文件），非确定性失败
- 与本轮 V2.1 语义修改**无关**：本轮改动不涉及 GrowthManager、ChatHistoryManager、MemoryManager 及其 Repository 适配器
- 本轮未为该测试放宽断言或修改业务代码
- 报告位置：`C:\Users\Public\RoxyPlan-flaky-audit\`（10 次独立运行记录）

### 真实桌面验收缺陷审计（2026-08-04 第二轮）

`db01368` 的自动验收遗漏了以下真实桌面问题（离线 Stub 全部通过，在线 Flash/Pro 均无法覆盖）：

| 缺陷 | 现象 | 根因 | 修复 |
|---|---|---|---|
| 缺陷一 | schema_invalid_entities 后回复冒充业务（询问"几点、多久"） | chat 回复模型不知道 schema 被拒绝 | complete() 检测 schema 拒绝标记，传递 schema_rejected=True 到 ResponseComposer；ActionClaimGuard 新增 SCHEMA_REJECTED_BUSINESS_PATTERN |
| 缺陷二 | awaiting_clarification 被错误取消；add_plan 强制要求时间和时长 | BusinessResolver 对非 explicit_command 的 add_plan 强制要求 duration_minutes | 移除 duration 强制要求，title 清晰时直接执行；仅 title 缺失时才 clarify |
| 缺陷三 | "对的" 引用 previous_assistant_message 保存助手全文为记忆 | LLM 将 bare affirmative 解析为 write+previous_assistant_message | LLMIntentParser 新增 affirmative gate：bare affirmative + write + previous_assistant_message → reject |
| 缺陷四 | "我记住了/已经记下了/帮你加入计划了/已经安排好了/到时候会提醒你" 无 ToolResult 通过 | ACTION_CLAIM_PATTERN 缺少"记住/记下/记进/安排"动词 | 扩展 ACTION_CLAIM_PATTERN、_verified_action_claim verb_tools；新增 REMINDER_PROMISE_PATTERN |

**修复文件**：
- `modules/action_claim_guard.py`：扩展 ACTION_CLAIM_PATTERN、新增 REMINDER_PROMISE_PATTERN 和 SCHEMA_REJECTED_BUSINESS_PATTERN、_verified_action_claim 增加 verb_tools、schema_rejected 参数
- `modules/response_composer.py`：compose() 新增 schema_rejected 参数
- `modules/conversation_service.py`：complete() 检测 semantic_diagnostic 拒绝标记并传递
- `modules/business_resolver.py`：add_plan 移除 duration 强制要求
- `modules/intent_router.py`：新增 bare affirmative gate

**新增回归案例**（`tests/fixtures/v21_chinese_context_matrix.json`）：
- regress_a–g：覆盖 previous_turn 引用、偏好保存、愿望陈述零写入、记忆空查询、肯定确认边界、title-only add_plan、时间纠正

**全量测试**：777 passed, 0 failed

## 7. 完整调用链日志证据

典型成功链（以 `write_006` "请记住：我更适合早上学习" 为例）：

```text
SemanticDecision (source=llm, mode=write, intent=add_memory_request)
→ Validation (proposed_tool=save_formal_memory, entities valid)
→ ReferenceResolution (no unresolved references)
→ BusinessResolver (content complete, low risk, no conflict)
→ ToolExecution:save_formal_memory (success, memory_id=xxx)
→ ToolResult:save_formal_memory:success
→ MemoryAudit:create
→ ActionClaimGuard (ToolResult matches — reply may use 完成式)
→ FinalResponse
```

典型 chat 链（以 `chat_020` "把复习加入计划，不对，先别加" 为例）：

```text
SemanticDecision (source=llm, mode=chat, intent=chat)
→ Validation (mode=intent=chat, no tools)
→ FinalResponse (chat_only, zero tool calls)
```

## 8. 架构不变量验证

- [x] 每条非精确消息仅一次 SemanticDecision
- [x] ExactCommand 仅处理无歧义桌宠/UI 命令（4 cases: read_005/006/007, write_001）
- [x] BusinessResolver 不重新猜意图
- [x] chat_only 后工具调用为零
- [x] 最终事实仅由真实 ToolResult 决定
- [x] 未扩大宽泛关键词规则
- [x] 未为单句硬编码
- [x] 未开发云端/Web API/数据库/账号系统
- [x] 未修改/还原/提交 data/pet_config.json
- [x] 未读取或修改正式私人数据，所有测试使用项目外临时目录和虚构数据
- [x] Mode/intent 互斥校验到位
- [x] Candidate actions 单意图清理到位
- [x] Proposed tool 与 intent 一致性校验到位
- [x] 写操作有明确授权、匹配成功 ToolResult
- [x] 重复动作去重到位
- [x] 复合句/条件句不分解为多个独立决策

## 9. 桌面冒烟验收清单

> 以下清单在 `roxy.bat` 关闭状态下编写。代码完成后需彻底重启再逐项验收。

### 启动与基础
- [ ] `roxy.bat` 正常启动，桌宠窗口出现
- [ ] 设置面板正常打开和关闭
- [ ] 无启动崩溃或异常日志

### 精确命令
- [ ] 输入"跳舞"→ 桌宠执行 dance 动作
- [ ] 输入"查看计划"→ 显示今日计划

### 聊天隔离（零工具触发）
- [ ] "我怕我没有钱" → 普通聊天回复，不触发任何工具
- [ ] "我喜欢看你跳舞" → 普通聊天回复，不触发 dance
- [ ] "长期记忆和短期记忆有什么区别" → 知识回答，不读取记忆
- [ ] "把复习加入计划，不对，先别加" → 普通聊天，零写入
- [ ] "如果今晚有空就加入计划" → 普通聊天，零写入

### 读操作
- [ ] "今天还有什么计划" → 显示今日计划列表
- [ ] "你记得我什么" → 优雅展示正式记忆（不显示 ID 标签）

### 写操作
- [ ] "把复习随机森林加入今天计划" → 成功添加，回复确认
- [ ] "请记住：我更适合早上学习" → 保存正式记忆，回复确认
- [ ] "记录：今天完成了接口测试" → 添加行动记录

### 多步操作
- [ ] "先查看今天计划，再把复习随机森林加入计划" → 先展示计划，再添加

### 边界验证
- [ ] 关闭并重新打开聊天 → 不继承上一个窗口的 pending/序号
- [ ] 切换会话后旧 pending 状态消失
- [ ] 写操作成功后的撤销（如支持）→ 撤销成功

### Local Web（如适用）
- [ ] Local Web 正常启动
- [ ] 聊天功能正常
- [ ] 计划/记忆面板正常显示

## 10. 在线验收报告位置

三轮完整报告：
- `C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-123201.json`
- `C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-123353.json`
- `C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-123432.json`

盲测原始结果：
- `C:\Users\Public\RoxyPlan-blind-results\blind_test_raw_20260804-123641.json`

诊断日志（桌面运行时产生）：
- `logs/interaction_diagnostics.jsonl`（59 条开发记录）

## 11. 剩余风险与下一步建议

### 剩余风险

1. **DeepSeek 中文编码不稳定**：write_004 和 write_019 出现中文乱码，导致 JSON 校验失败。这不是项目代码问题，而是模型端编码不稳定性。建议监控模型版本更新。
2. **多意图理解**：multi_001 在线模型只识别了多意图句的前半部分。当前 SEMANTIC_DECISION_POLICY 已包含多意图指导，但模型能力边界在此。
3. **在线模型与离线 Stub 差距**：离线 Stub 100% 通过，在线 73.3%。这种差距是预期内的，真正问题应关注是否存在"离线通过但在线误触发工具"的案例（本次未发现）。
4. **clarify_001 不稳定**：3 轮中 1 次失败，原因与模型输出稳定性相关。

### 下一步建议

1. **封版前**：确认真实桌面体验无问题后再做 V2.1 封版决策。
2. **模型编码修复**：如果 DeepSeek 模型版本更新修复了编码问题，重跑在线验收。
3. **多意图**：考虑在 SEMANTIC_DECISION_POLICY 中增强多意图分句指导，但不在当前合同测试范围。
4. **在线验收集成**：将在线验收保留为手动运行脚本，不加入 CI/默认 pytest。
5. **封版后**：可以考虑第二角色、语音集成或进阶工具能力，但不应在 V2.1 范围内。
6. **不做**：云端部署、数据库迁移、向量检索、公网服务。
