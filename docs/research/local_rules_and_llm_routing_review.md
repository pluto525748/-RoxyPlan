# 本地规则、LLM 路由与中文实体研究

研究日期：2026-07-22

## 1. 问题不是“再补几个关键词”

RoxyPlan 当前遇到的是三个不同问题混在一起：

1. **语言理解**：用户是在陈述、询问、表达愿望，还是要求执行？
2. **实体解析**：动作对象、时间、时长、任务标题、指代分别是什么？
3. **安全执行**：信息是否足够、是否需要确认、执行是否真的成功？

单纯增加同义词只能提高局部命中率，不能解决边界。例如：

| 表达 | 语言行为 | 正确处理 |
| --- | --- | --- |
| 我下午想学机器学习 | 愿望/弱计划 | 询问是否加入计划，或只给建议 |
| 把下午学习机器学习加入计划 | 明确执行请求 | 提取任务与时段，进入添加流程 |
| 下午可以学点什么？ | 咨询 | 普通回答，不写计划 |
| 我下午要安排 3 小时机器学习 | 较明确安排 | 解析时间段和时长；置信度足够时执行 |
| 机器学习学完了 | 完成事实 + 可能操作 | 匹配唯一计划后完成；不唯一时澄清 |
| 这个改成 50 分钟 | 指代操作 | 先解析“这个”，再更新；不能只解析时长 |

核心原则：**先判断语言行为，再解析实体，最后才决定是否执行。**

## 2. 推荐的三层职责

### 2.1 固定命令层：确定性入口

适合：

- 精确查看命令：`查看计划`、`我的记忆`。
- 带编号操作：`完成计划1`、`确认记忆2`。
- 确认/取消：`确认`、`取消`。
- 高风险命令：删除、清空、覆盖、冲突决策。
- UI 按钮产生的内部动作。

特点：高精度、易测试、不需要模型、离线可用。固定命令应保留，但不应承担全部自然语言。

### 2.2 本地语义层：高精度结构和边界

适合：

- 否定、疑问、信息性表达：`怎么删除计划`不是删除请求。
- 常见中文动作句式和显式操作词。
- 数字、编号、时长、时间段等局部实体。
- 当前 pending confirmation、最近工具结果和“刚才那个”的确定引用。
- 低风险确定性查询。
- 小模型不可用时的安全降级。

本地层应按“模式族”组织，而不是散落关键词：

```text
语言行为模式：陈述 / 愿望 / 请求 / 询问 / 否定 / 确认
动作模式：添加 / 完成 / 更新 / 删除 / 查看 / 记录
实体模式：标题 / 日期 / 时段 / 时长 / 编号 / 引用
风险模式：只读 / 可逆写入 / 不可逆 / 敏感
```

规则层只负责高精度场景。不能确定时返回 `unknown` 或候选，不应假装命中。

### 2.3 模型动作提议层：开放表达

适合：

- 规则未覆盖、但明显可能涉及产品能力的开放表达。
- 多个实体之间的语义关系。
- “这件事”“你刚才说的那个”等需要受控会话上下文的指代候选。
- 一句话多个动作的候选拆分。

模型只能输出：

```json
{
  "kind": "action | chat | clarification",
  "intent": "add_plan",
  "confidence": 0.86,
  "entities": {
    "title": "学习机器学习",
    "time_slot": "下午",
    "duration_minutes": 180,
    "target_ref": null
  },
  "missing_fields": [],
  "needs_confirmation": false,
  "evidence": ["安排", "3个小时", "机器学习"]
}
```

该结果仍要经过枚举、类型、实体来源、业务状态和风险复核。模型不得调用 Manager。

## 3. IntentResult 与原生 tool calling 的边界

`LLM IntentResult` 和原生 tool calling 都在回答“用户是否想调用哪个能力、参数是什么”。同一条消息连续跑两套模型决策，容易重复、冲突和浪费。

推荐按模型能力二选一：

```text
固定命令/高精度规则命中
  -> 直接生成规范 ProposedAction

规则未命中
  -> 模型支持并通过 tool-calling 评测
       -> 原生 tool_calls -> ProposedAction
  -> 模型不支持或不稳定
       -> 受限 JSON IntentResult -> ProposedAction

ProposedAction
  -> 本地校验/澄清/确认/执行
```

因此：

- `IntentResult` 是兼容接口，不应和原生 tool calling 串联两次判断。
- 原生 tool call 是“动作提议”，不是执行授权。
- 两条路径最终必须规范化为同一内部对象，进入同一个 `SafetyPolicy + ToolExecutor`。
- 普通聊天与动作提议必须互斥；只要存在未处理动作候选，就不能直接让普通聊天模型声称完成。

## 4. 防止“规则失败 -> 普通聊天 -> 虚构已完成”

至少需要三道门：

### 4.1 路由门

规则无法确定，但文本含有明显执行语义时，不直接 fallback chat。进入动作提议器或澄清：

- 写入词：加入、添加、安排、记录、保存、记住。
- 状态变更词：完成、改成、推迟、取消、删除、恢复。
- 指代 + 变更：这个改成、刚才那个完成、把上面的存下来。

### 4.2 执行门

任何模型提议都必须满足：

1. 工具存在且启用。
2. 参数仅包含 schema 字段。
3. 参数可追溯到用户文本或受控上下文，不能凭空补充。
4. 业务目标存在且唯一。
5. 风险和置信度允许执行。
6. 工具返回成功且后置条件成立。

### 4.3 动作声明门

普通 LLM 回复中若出现“已经帮你添加/保存/删除/完成/记录”等动作完成声明，而当前 turn 没有成功 `ToolResult`：

- 不把该文案原样展示。
- 替换为“我还没有执行这个操作，需要你确认/补充……”或重新进入动作提议。
- 记录受控日志，加入回归样本。

这道门比仅靠 system prompt 更可靠。成熟框架建立工具回环，但产品层仍要负责“完成式文案必须有执行事实”。

## 5. 中文实体抽取建议

### 5.1 计划标题

先移除动作外壳，再保留用户原词：

```text
帮我把 [机器学习特征工程练习] 加入计划
          ^^^^^^^^^^^^^^^^^^
```

不要让模型把“唱歌”自动改写为“Python 打印你好”，也不要用旧记忆替用户选主题。若剩余标题过泛，如“学习任务”“这个事情”，必须澄清。

### 5.2 日期、时间和时长

建议结构化为：

- `date`: ISO 日期或空。
- `time_slot`: 上午/下午/晚上等原始语义。
- `start_time`: 明确时间才填写。
- `duration_minutes`: 统一分钟。
- `raw_time_text`: 保留原表达，便于确认和调试。

“下午 3 个小时”不等于“下午 3 点”。解析器必须分别检测数量单位和钟点。

### 5.3 操作对象

优先级：

1. 稳定内部 ID/用户编号。
2. 本轮最近一次成功工具结果中的对象 ID。
3. 当前会话最近明确提及对象。
4. 标题包含匹配。
5. `SequenceMatcher` 等轻量相似度。
6. 多候选时让用户确认。

界面序号只是展示引用，不应替代稳定 ID。

### 5.4 指代

不要让 LLM直接把“这个”解析成最终 ID。它可以提出 `reference_kind=last_task`，本地 `ReferenceResolver` 再根据受控状态解析：

```json
{
  "last_tool_result": {"tool": "add_plan", "task_uid": "task_xxx"},
  "last_mentioned_tasks": ["task_xxx"],
  "pending_confirmation_id": null
}
```

“刚才那个”在新会话、服务重启或存在两个候选时应澄清，而不是猜。

## 6. dateparser 评估

研究版本：[dateparser `v1.4.1`](https://github.com/scrapinghub/dateparser/tree/v1.4.1)，BSD-3-Clause，Python >= 3.10。

关键源码：

- [`dateparser/date.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/date.py)：主解析入口和语言选择。
- [`dateparser/parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/parser.py)：日期 token 解析。
- [`dateparser/freshness_date_parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/freshness_date_parser.py)：相对时间。
- [`zh-Hans.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/data/date_translation_data/zh-Hans.py)：简体中文翻译和相对时间模式。
- [`tests/test_freshness_date_parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/tests/test_freshness_date_parser.py)：包含“2 小时前”“5 个月后”“7 天后”等中文样例。

优点：

- 多语言、相对时间、时区、`RELATIVE_BASE` 和未来/过去偏好成熟。
- 可作为“时间文本 -> datetime 候选”的专用组件。

限制：

- 最新版不兼容当前 Python 3.8.8。
- 它不理解计划动作、时长和标题边界。
- 对“下午抽点时间”“晚点”“下下周找时间”等模糊表达仍需业务澄清。
- 长句日期搜索可能过度匹配，必须保存原文并设置置信度。

结论：当前不直接引入。Python 升级后可在独立 `TemporalParser` 适配器中试验，并先用 RoxyPlan 中文语料做精确率测试。

## 7. Duckling 评估

研究版本：[Duckling `main`](https://github.com/facebook/duckling/tree/main)，研究时 HEAD `59a13ff`；最后 GitHub release 为 `v0.2.0.0`（2021）。仓库 LICENSE 是 BSD License。

关键源码：

- [`Duckling/Time/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Time/ZH/Rules.hs)：中文通用时间规则。
- [`Duckling/Time/ZH/CN/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Time/ZH/CN/Rules.hs)：中国区域规则。
- [`Duckling/Time/ZH/Corpus.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Time/ZH/Corpus.hs)：中文时间语料。
- [`Duckling/Duration/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Duration/ZH/Rules.hs)：中文时长。
- [`Duckling/Numeral/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Numeral/ZH/Rules.hs)：中文数字。

规则与语料按 `dimension / locale / Rules / Corpus` 组织，这个设计很值得借鉴：实体规则和验证样例放在一起，扩展中文表达时不把逻辑塞进一个路由函数。

但 Duckling 是 Haskell 服务/库，会引入 GHC/Stack 或独立服务生命周期。对 Windows 本地 Python 桌宠过重，发布节奏也不适合当前原型。结论是参考规则组织方式，不直接使用。

## 8. 置信度与确认矩阵

置信度不是模型说多少就是多少，应该由来源和业务校验共同决定：

| 来源/结果 | 只读 | 可逆写入 | 不可逆/敏感 |
| --- | --- | --- | --- |
| 固定命令 + 完整参数 | 直接执行 | 直接或简短确认 | 必须二次确认 |
| 高精度规则 + 唯一目标 | 直接执行 | 高置信度可执行 | 必须二次确认 |
| 原生 tool call + schema 通过 | 直接执行 | 需满足阈值和业务唯一性 | 必须二次确认 |
| JSON Intent + schema 通过 | 直接执行 | 阈值更高；否则确认 | 必须二次确认 |
| 缺少必填实体 | 澄清 | 澄清 | 拒绝/澄清 |
| 多个目标候选 | 展示列表 | 确认目标 | 确认目标与动作 |
| 否定/信息性问题 | 不执行 | 不执行 | 不执行 |

建议不要对 `qwen3:4b` 的自报 confidence 赋予决定权。置信度应由“来源基础分 + 实体覆盖 + 唯一目标 + 否定检查 + 业务状态”重新计算。

## 9. 本地小模型降级策略

对 qwen3:4b 这类小模型：

1. 先通过离线语料评测是否稳定返回原生 tool calls。
2. 原生工具不稳定时，使用最小 JSON schema，而不是复杂嵌套计划。
3. 每次只让模型做一种任务：动作提议或普通回答，不同时做两者。
4. 参数修复最多一次；第二次失败就澄清或回退规则。
5. 写操作默认顺序执行，不允许模型自由并行。
6. 模型离线时固定命令和高精度规则仍工作。
7. 模型升级不能绕过相同 Registry、Policy、Executor 和后置条件。

Ollama 原生 API 已支持 `tools` 和带历史的工具结果，官方格式见 [`ollama/docs/api.md`](https://github.com/ollama/ollama/blob/main/docs/api.md)。但 Ollama 官方仓库中仍能找到 Qwen 系列不同版本的 tool-call 格式和 thinking 兼容问题报告，因此“API 支持”不等于“4B 模型在本项目语料上可靠”。必须以本地评测通过率决定启用方式。

## 10. 建议的状态机

```text
chat
  -> interpreting
      -> responding_chat
      -> clarification_pending
      -> confirmation_pending
      -> executing
executing
  -> tool_retry (最多一次模型参数修复)
  -> completed
  -> failed
clarification_pending
  -> interpreting（补充信息）
  -> cancelled（换话题）
confirmation_pending
  -> executing（确认且参数/状态仍有效）
  -> cancelled（拒绝、换话题、过期、重启）
```

状态对象至少保存：

- `conversation_id`
- `request_id`
- `state`
- `proposed_action`
- `missing_fields`
- `confirmation_id`
- `created_at/expires_at`
- `target_version` 或目标摘要指纹
- `attempt_count`

重启后的默认策略可继续是“pending 失效”，但必须明确告知。若未来持久化，恢复时仍要重新解析目标、校验版本和重新过 SafetyPolicy，不能直接执行旧参数。

## 11. 规则可维护性建议

不要继续把所有规则追加到一个 `_route_rules`：

```text
intent/
  speech_act.py       # 请求、询问、愿望、否定
  patterns/
    plan.py
    memory.py
    growth.py
  entities/
    temporal.py
    duration.py
    task_reference.py
  proposal.py         # 统一 ProposedAction
  evaluator.py        # 语料回归与混淆矩阵
```

这只是候选边界，本轮不建议立即迁移代码。下一轮实施也应先补语料和契约，再拆文件，避免一边重构一边改变行为。

## 12. 中文实体、时间与指代实测

测试于 2026-07-22 直接调用当前 `IntentRouter -> AgentCore -> ToolExecutor -> GrowthManager` 链路。所有写入都位于临时目录，没有修改真实 `data/private/`。同时对孤立短语调用 `IntentRouter(enable_llm=False)`，用于确认当前是否存在独立实体层。

### 12.1 完整表达

| 输入 | 实际结果 | 结论 |
| --- | --- | --- |
| 我今天下午想学半小时机器学习 | 成功添加；标题为“学半小时机器学习”；`time_slot=""`、`duration_minutes=null` | 动作识别正确，下午和半小时丢失 |
| 今晚学习机器学习30分钟 | 成功添加；`duration_minutes=30`；`time_slot=""` | 数字时长正确，今晚丢失 |
| 明早学习英语半小时 | 回退普通聊天，没有添加计划 | “明早”和“半小时”未覆盖 |
| 把刚才那个改成50分钟 | 在存在 `last_task` 时更新成功，时长变为 50 | 当前最可靠的指代句式 |
| 推迟两个小时 | 回退普通聊天，计划未变化 | 不支持相对位移时长 |
| 改到下班以后 | 回退普通聊天，计划未变化 | 不支持事件锚点时间 |
| 完成前一个计划 | 识别为 `complete_plan`，但目标匹配失败并要求澄清 | 识别动作，不理解列表相对位置 |

### 12.2 孤立实体探针

`今天下午`、`今晚`、`明早`、`半小时`、`改成50分钟`、`推迟两个小时`、`下班以后`、`刚才那个`、`前一个计划` 在单独输入时均回退为 `chat`，`entities` 为空。

孤立短语不应自动执行动作，因此回退 `chat` 本身是安全的；问题在于当前没有一个与意图分离的实体解析器，无法把这些片段保存为澄清补充或合并到 pending action。

### 12.3 缺口定位

1. `_route_add_plan` 会消费“下午/今晚”前缀，但捕获的任务标题不再携带时间，后续 `_plan_fields_from_text` 因此无法恢复 `time_slot`。
2. 时长正则只接受阿拉伯数字，不识别“半小时”“两个小时”等中文数词。
3. 改期只支持上午、下午、晚上、今晚、明天等枚举，不支持“延后 N 小时”或“下班以后”这类相对/事件时间。
4. `ConversationStateManager` 只保存 `last_task`，没有任务列表游标，所以能解析“刚才那个”，不能解析“前一个计划”。
5. 路由结果没有独立 `TemporalEntity/DurationEntity/ReferenceEntity`，澄清轮无法可靠合并实体。

### 12.4 对下一轮的约束

- 先建立独立、纯函数式的 `TemporalParser`、`DurationParser` 和 `ReferenceResolver`，不要继续往单条 intent 正则里堆表达。
- `半小时 -> 30`、`两个小时 -> 120` 必须在本地确定性规范化。
- `今天下午/今晚/明早` 应输出结构化日期和时间段，同时保留原始文本。
- `推迟两个小时` 需要已有计划的基准时间；缺少基准时必须澄清，不能编造具体时间。
- `下班以后` 属于用户事件锚点，若没有个人日程定义，只能保留语义标签或询问具体时间。
- `刚才那个/前一个计划` 必须依赖结构化会话状态和候选列表，不应交给 LLM 从整段聊天中猜测。
