# V2.2 中文交互可靠性与评测方法

更新时间：2026-09-12

## 目标

本轮不是用更多关键词覆盖更多句子，而是验证同一产品意图在中文口语、否定、反问、修正、省略、指代和多分句环境下，能否稳定进入同一份 `SemanticDecision`，并由程序边界决定是否执行工具。夹具中的 `chat/read/write/clarify` 是模型语义 schema；运行时会把它归一化为 `chat_only/tool_then_reply/clarify` 的 canonical 决策。V2.2 封版候选还要求验证列表引用、当前句日期、有限澄清、隐式记忆确认、隐藏能力降级与成长复盘的端到端事实边界。

评测同时关注三类错误：

1. 本应聊天或只读，却误触发写工具。
2. 用户明确要求执行，但实体、引用或多任务边界被错误解析。
3. 工具没有成功，最终回复却虚报已保存、已完成或已修改。

## 资料来源

案例不是从私人聊天日志直接复制，也不是凭开发者直觉随意扩写。当前矩阵先抽取公开研究中的可迁移语言现象，再结合 RoxyPlan 已确认的匿名故障类型，生成不含私人数据的最小对照句。

- [Beyond Accuracy: Behavioral Testing of NLP Models with CheckList](https://aclanthology.org/2020.acl-main.442/)：采用 Minimum Functionality、Invariance 和 Directional Expectation 思路，把能力与测试类型组成矩阵，而不是只看一组准确率。
- [Evaluating Models’ Local Decision Boundaries via Contrast Sets](https://aclanthology.org/2020.findings-emnlp.117/)：对原句做小而有意义的修改，观察正确标签或工具是否按预期改变，用于发现局部决策边界问题。
- [End-to-End Neural Context Reconstruction in Chinese Dialogue](https://aclanthology.org/W19-4108/)：用于整理中文对话中的省略和上下文重构现象。
- [CREAD: Combined Resolution of Ellipses and Anaphora in Dialogues](https://aclanthology.org/2021.naacl-main.265/)：用于设计省略与指代联合解析的正反案例。
- [DialogUSR: Complex Dialogue Utterance Splitting and Reformulation for Multiple Intent Detection](https://aclanthology.org/2022.findings-emnlp.234/)：用于多意图分句、重写和子任务完整性案例。
- [CrossWOZ: A Large-Scale Chinese Cross-Domain Task-Oriented Dialogue Dataset](https://aclanthology.org/2020.tacl-1.19/)：用于跨域、对话状态和槽位切换的测试组织方式。

这些论文提供方法和语言现象，不直接证明 RoxyPlan 的模型效果。项目中的具体中文句子是为本地产品工具边界重新编写的合成测试材料。

## 数据来源分层

### A. 公开研究现象

- 最小功能测试：一句话只检验一个核心边界。
- 不变性：称呼、标点、礼貌包装变化不应改变工具策略。
- 方向性对照：加入否定、引号、假设、反问或执行动词后，决策应按预期改变。
- 省略和指代：当前会话存在唯一结构化来源时才解析。
- 多意图：明确并列任务才允许多个工具；替代、条件和自我修正不应误当并行执行。

### B. 已确认故障类型

只保留脱敏后的故障模式，例如：

- “计划”出现在普通陈述中却触发计划工具。
- “完成”出现在查询中却被当成完成操作。
- 等待时长期间的普通聊天被 pending 消费。
- 超过旧的 5 分钟窗口后，仍有效的普通 pending 被同时命中的 assistant-reference 分支抢占，导致结构化目标丢失且零工具执行。
- 助手使用自然中文序号“第一，第二，第三，”列出明确事项，但确定性 fallback 只识别“第一步”或“一、”，导致候选列表被错误清空。
- 正式记忆正文包含“跳舞”而误派发动作。
- `show_plan` 查询成功却被写声明守卫替换为“尚未执行”。
- 模型声称保存长期记忆，但没有 `save_formal_memory` ToolResult。
- “不要展示今天计划，我该先做什么”被计划名词误触发为 `show_plan`，而不是保持建议聊天和零工具。
- 上一轮查询“昨天”后，本轮明确说“今天的呢”，却继续使用模型残留的昨日日期。
- 列表增删后按实时下标解释“第二项”，导致修改了用户没有指向的计划。
- 隐式普通稳定事实未确认就落盘，或对健康等敏感事实主动建议保存。
- 模型提议默认隐藏的修改、合并或候选维护工具后，回复仍暗示操作成功。

不把真实私人正文、记忆 ID、聊天全文或 API Key 写入夹具。

### C. 项目内合成最小对照

每个案例必须声明：

- `mode`：chat / read / write / clarify。
- `intent` 与实体。
- 允许的 candidate tools。
- 禁止出现的工具。
- 是否允许澄清。
- 对话状态或结构化引用前置条件。

合成句只能用于验证已定义的语言边界，不能作为“真实用户都这样说”的证据。

### D. 真实在线模型验收

真实 Provider 的结构化输出会受到模型版本、系统提示、延迟和服务状态影响，必须与离线 Stub 测试分开报告。在线失败应转成新的最小对照或合同测试，但不能把在线模型加入 pytest 的默认依赖。本次 Codex 封版协作使用用户指定的既有 OpenAI provider；下文 DeepSeek 数据只作为带日期的 RoxyPlan 历史验收证据，不代表本次 Codex provider，也不得据此建议切换到 `aivalux`。

## 当前离线矩阵

权威夹具：`tests/fixtures/v21_chinese_context_matrix.json`

当前共 85 条（V2.1 原始矩阵经后续匿名回归扩展后的现行夹具）：

| 模式 | 数量 | 重点 |
|---|---:|---|
| chat | 32 | 情绪、知识问答、引号、假设、否定、反问、自我修正、领域词误触 |
| read | 17 | 计划、正式记忆、行动、成长、当前会话和旧摘要查询 |
| write | 31 | 计划、行动、正式记忆、桌宠动作、命令/载荷隔离和明确多任务 |
| clarify | 5 | 只有确有执行意图且缺少必要内容/对象时澄清 |

其中 8 条 `multi_*` 多意图案例按主模式计入上表，不另行重复计数。

另有一些预期最终被 Resolver 降级澄清的 `write` 案例，例如“把这些加入计划”或缺失同会话 assistant 引用。它们保留 `write` 语义，是为了验证“理解出执行意图”和“程序拒绝不完整写入”这两个阶段没有混为一谈。

核心现象包括：

- “我怕我没有钱”是 chat，不触发计划或记忆。
- “我正在做一个 AI 陪伴计划桌宠”不因“计划”查询或写入计划。
- “我喜欢看你跳舞”是 chat；精确“跳舞”才走精确动作命令。
- “长期记忆和短期记忆有什么区别”是知识问答，不读取或保存正式记忆。
- “你知道我什么”查询正式记忆；“你知道我为什么睡不着吗”不是正式记忆查询。
- “把你刚才的回复加入长期记忆”只引用当前 conversation 最近 assistant 消息。
- “记住我喜欢看你跳舞”只保存 payload，不派发舞蹈。
- “把复习加入计划，不对，先别加”最终零写入。
- “删除第一项还是挪到明天”是方案讨论，不同时执行两个工具。
- “先查看今天计划，再把复习随机森林加入计划”才是明确多任务。
- “不要展示今天计划，我该先做什么”是 advice/chat，`show_plan` 也必须为零。
- 当前句明确说“今天/昨天/明天”时，以当前句为准，不沿用上一轮日期或模型残留实体。
- 隐式普通稳定事实只询问是否保存；隐式敏感事实既不主动建议也不创建 pending。

## 离线自动测试方法

离线测试不调用真实在线 LLM，使用 Stub SemanticDecision，但必须经过真实程序边界：

```text
IntentRouter
→ SemanticActionParser / SemanticDecision
→ ConversationService
→ BusinessResolver
→ CapabilityRegistry（模型可见性与执行策略唯一权威）
→ ToolRegistry（参数 schema / handler）/ ToolExecutor
→ ResponseComposer / ActionClaimGuard
→ interaction diagnostics
```

断言不只看 intent，还包括：

- 一条非精确消息最多一次正式语义调用。
- candidate tools 与禁止工具集合。
- 真实执行工具名称和次数。
- chat/read 不创建 write pending。
- 模糊写批次零写入。
- ToolResult success/failure 与最终状态一致。
- diagnostics 中只出现一个 `SemanticDecision`，FinalResponse 后没有第二个工具来源。
- 兼容 pending continuation 优先于 fresh assistant reference；成功后 pending 被消费，且不会调用 assistant 方案提取模型重新猜目标。
- assistant plan 模型候选不足时，只能从上一条助手原文的明确有序结构恢复候选；自然中文序号与数字序号都要覆盖，恢复结果仍须经过候选选择后才能写入。
- 成功 `show_plan` 必须保存会话内 `ReadSnapshot` 的稳定 ID 与 `display_order`；后续序号按展示顺序绑定，执行前再按稳定 ID 读取实时对象，不能用变化后的列表下标替代。
- 否定读取、实际只求建议的当前句必须 `tool_results=[]`；当前句明确日期必须覆盖旧轮日期和模型残留日期。
- 批量完成确认前数据不变，目标只展示一次并只等待一次整体确认；批量新增计划的三项上限不能误套到最近展示计划的批量完成。
- 同一 pending 最多两轮澄清；仍缺字段时清除 pending、零写入，并使用统一的诚实降级与可执行示例。
- 隐式普通稳定记忆确认前，正式记忆与磁盘候选均为零；隐式敏感信息不得主动建议保存或建立 pending。
- `CapabilityRegistry` 的模型可见性及副作用、风险、确认策略、可逆性必须与运行时一致；`ToolRegistry` 只提供 schema/handler。隐藏复杂工具即使被模型提议，也必须零工具、零写入并诚实说明未完成。
- 历史会话模糊查询只返回已验证的旧摘要、片段、日期或来源并排除当前会话；没有匹配时明确说没有找到，不能用当前对话编造旧内容。
- 昨日复盘启动补齐的设置默认开启且与晚间提醒独立；运行时只检查昨日、幂等且不调用模型，已有日志和空白日均不写入，数据损坏立即提醒。自然月成长查询只包含目标 `YYYY-MM`，自然总结只能基于成功 ToolResult 的已核验月度数据。
- 临时 Repository 中的数据变化符合预期，正式私人数据不变。

主要测试入口：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chinese_context_matrix.py -q --basetemp C:\Users\Public\RoxyPlan-chinese-matrix
```

V2.2 封版收口的专项入口还包括 `tests/test_v22_release_closure.py`、`tests/test_v22_growth_closure.py`、`tests/test_contract_taxonomy.py` 与既有安全边界测试。完整回归按 `tests/contract_taxonomy.py` 分开运行 production、compatibility 和 historical；unfiltered 只是可选汇总，不能替代分轨报告。矩阵或专项通过也不能替代既有记忆、计划、状态、人格、ContextBuilder 和 Local Web 自动测试；桌面 UI 属于用户按需触发的独立验收，未实际执行时不得写成通过。

### 2026-08-20 定向真实桌面回归

本轮增加了两个不能由默认离线矩阵替代的真实桌面场景：

1. 普通计划选择静置超过旧 300 秒后，使用同时命中 pending continuation 与 assistant reference 的自然跟进。通过标准是继续使用 pending 的结构化标题，只调用一次 `add_plan`，终态 `completed`。
2. 助手先用“第一，第二，第三，”给出三项纯中文建议，用户再引用整段建议并选择其中一项。通过标准是引用轮 0 工具并进入 `assistant_plan_selection`，选择轮只写入被选中的一项。

真实复验中，第一条链在等待 7 分 24 秒后通过。第二条链首次失败但保持 0 工具；修复自然序号 fallback 并彻底重启后，诊断显示模型候选 0、原文 fallback 候选 3，选择第三项后出现唯一一次成功 `add_plan`。这证明的是两条已知故障链，不代表所有开放中文或完整桌面冒烟清单都已通过。

### 2026-09-11 当前 OpenAI 在线验收状态

本轮没有运行当前 OpenAI 在线语义验收。现有 `scripts/run_online_chinese_semantic_acceptance.py` 固定使用 DeepSeek provider；RoxyPlan 正式运行 factory 在当前工作树仅提供 DeepSeek/Ollama。本轮只读核验了本地配置的顶层 provider 字段为 Ollama，环境中也没有可用的 OpenAI 凭据；核验未输出密钥、未改写配置。为遵守“不切换、不新增 provider”的边界，本轮没有联网，也没有用 DeepSeek 历史脚本冒充 OpenAI 证据。这是明确的未运行项，不是通过或失败结果。

## 历史在线真实模型验收（DeepSeek）

脚本：`scripts/run_online_chinese_semantic_acceptance.py`。本节记录 2026-08-04 的可复查历史方法，不是本次 Codex provider 的运行建议。本次协作继续使用用户指定的现有 OpenAI provider；除非用户另行授权，不切换或新增 provider，并且不提议 `aivalux`。

安全约束：

- 只从环境变量读取 API Key，默认变量名为 `DEEPSEEK_API_KEY`。
- 不读取 `data/pet_config.json` 或私人密钥文件。
- 使用 `TemporaryDirectory` 和隔离虚构数据。
- 默认报告写到项目外 `Roxyplan-test-runs/`。
- 报告可能包含合成测试句和模型原始结构化输出，不应放入公开提交。

历史复现实例（只在用户明确要求复现该历史 provider 时使用）：

```powershell
$env:DEEPSEEK_API_KEY = "仅在当前终端设置"
.\.venv\Scripts\python.exe scripts\run_online_chinese_semantic_acceptance.py
```

可以用 `--limit` 先做小规模冒烟，并通过 `--model` 明确模型。验收报告至少记录：

- UTC 时间、Provider、请求模型和实际模型。
- 案例数、通过/失败数。
- 每条的解析 intent、候选工具、执行工具、最终状态和 request_id。
- 失败原因、延迟与 token 用量。
- `isolated_virtual_data=true` 和 `project_private_data_read=false`。

### 历史结果

**已记录**（2026-08-04，DeepSeek 双模型各 3 轮 × 20 案例）。这些数字不得描述为 2026-09-11 当前模型或本轮新运行结果。

| 模型 | 通过率 | 一致通过 | 一致失败 | 不稳定 |
|---|---:|---:|---:|---:|
| deepseek-v4-flash | 73.3% (44/60) | 14 | 5 | 1 |
| deepseek-v4-pro | 76.7% (46/60) | 14 | 4 | 2 |

Pro 改进：write_004 中文编码问题从 Flash 0/3 修复到 Pro 2/3。
共同持续失败（4 cases，均为模型端）：multi_001（多意图分解）、read_010（意图粒度）、read_015（方言实体推断）、write_019（category 缺失）。

零误触发：所有 chat 案例均未触发工具。
报告位置：`C:\Users\16127\Projects\Roxyplan-test-runs\online-chinese-semantic-acceptance-20260804-12*.json`（Flash）和 `…-13*.json`（Pro）。

## 诊断日志

持久化开发日志默认位置：`logs/interaction_diagnostics.jsonl`。

每条记录应可追踪：

```text
SemanticDecision
→ Validation / ReferenceResolution
→ ToolExecution:<tool>
→ ToolResult:<tool>:success|failed
→ MemoryAudit（如适用）
→ FinalResponse
```

重点字段：

- `request_id`：同一轮的脱敏关联标识。
- `route_source` / `semantic_parse_source`：精确命令、LLM、确定性读合同或 fallback 来源。
- `action_candidates` / `resolver_result`：模型提案与程序校验结果。
- `tool_calls` / `tool_results`：真实调用工具、成功状态和错误码。
- `interaction_state_before/after`：pending 是否被正确消费或取消。
- `last_read_snapshot` 的脱敏结构：只核对 snapshot/tool 标识、对象数量、稳定 ID 与展示顺序是否进入会话状态；写入前还要从业务仓储复核实时对象，日志中的旧快照不能单独证明执行有效。
- `coordinator_decision`：`fresh_turn`、`pending_continuation` 或 `control:<action>`，用于区分新请求、结构化续接和确认/取消等控制输入。
- assistant plan 候选的 `semantic_parse_source` 与 `validation_notes`：区分模型候选、确定性 fallback 或空候选，并只记录候选数量，不持久化候选正文。
- `response_source` / `status` / `pipeline`：最终分支和完整阶段。
- `persona_context` / `context_sections`：人格版本、检索条数、字符数和裁剪状态。
- `normalized_text`：只包含长度和 SHA-256，不包含用户正文。

日志适合验证路由和工具真实性，但不保存最终自然回复全文，因此不能单独评价文风。文风测试应使用隔离输入和结构/禁用模式断言，不要求模型输出固定中文句子。

当前日志缺少明确时间戳和运行来源标签时，不能可靠区分历史桌面记录与测试记录；分析前应结合文件修改时间、request_id 和运行命令，不能猜测。

## 通过标准

一轮中文交互只有同时满足以下条件才算通过：

1. 意图和工具边界正确。
2. 工具执行次数正确，禁止工具为零。
3. 写操作具有匹配的成功 ToolResult；失败时不虚报。
4. 当前会话状态不泄漏到新 conversation。
5. 诊断链完整且没有第二次语义决策或 FinalResponse 后工具。
6. 自动测试使用隔离数据，正式私人文件清单和 SHA-256 不变。
7. 在线模型结果与离线程序结果分别报告。
8. 仍在有效期内的兼容 pending 必须使用已保存的结构化字段继续，不能改从聊天历史猜测目标；真实桌面长等待回归还必须看到匹配的 ToolExecution、成功 ToolResult 和终态 `completed`。
9. 否定建议与隐式敏感记忆必须零工具、零 pending/持久化；普通稳定记忆只有确认后才允许出现成功 `save_formal_memory`。
10. 列表序号与批量完成必须从 `ReadSnapshot` 的展示顺序绑定稳定 ID，并在写入前复核实时对象；确认前零写入，批量只确认一次。
11. 当前句明确日期覆盖旧日期；同一操作最多澄清两轮；隐藏复杂工具只能诚实降级，不能产生 ToolResult 或数据变化。
12. 昨日启动补齐不覆盖已有复盘、不为空日造记录且不调用模型；自然月日志不得混入相邻月份，模型总结只能来自已核验工具结果。
13. 普通聊天的隐式历史连续性最多注入两条高相关真实旧片段，当前表达优先且无证据时静默跳过；显式历史查询才展示真实摘要、片段、日期或来源，无匹配就明确说明，且不把当前会话当成旧证据。
14. diagnostics 必须严格服从显式开关；关闭时即使提供持久化路径也不得写文件，开启时 pipeline outcome 与诊断字段必须实际落盘。

2026-09-11 本轮证据如下：故障聚焦 `28 passed`；核心学习闭环 `129 passed, 11 deselected`；production `813 passed, 241 deselected`（exit 0）；compatibility `151 passed, 903 deselected`（exit 0）；historical `78 passed, 12 failed, 964 deselected`（exit 1）；全范围 `compileall` exit 0；`git diff --check` exit 0，仅有 LF/CRLF 警告。私有数据防火墙保持干净。12 项 historical 失败均为冻结旧契约：3 项复杂合并、5 项复杂改期/上一任务引用、2 项旧记忆直存、2 项 `opt_v2_006`/汇总，不得混为 production 失败。unfiltered 与桌面 UI 本轮未执行；前者不在用户列出的本轮必跑项中，后者由用户按需自行验收。

2026-09-11 至 2026-09-12 又按用户提供的真实会话逐项复现并修复能力询问被记忆查询抢占、否定纠正继续打开记忆范围、候选查询给出错误替代说法、成功添加后被后续聊天虚假撤回、删除确认状态串入添加流程、内部 `task_ref` 泄露，以及长期记忆回复版式重复的问题。最终审查还加入现实世界结果、同名新目标、外部系统、否定/疑问、UI 状态不一致和回复中多写入声明等对抗；本地规则只保护 RoxyPlan 写入真实性，UI 与上一成功结果冲突时要求重新读取，不用正则替代现实语义判断。受影响组件与事故回归组合 `136 passed`，release closure 与 reliability 组合 `88 passed`，核心学习闭环 `127 passed, 1 deselected`；production `849 passed, 241 deselected`（exit 0），compatibility `151 passed, 939 deselected`（exit 0），historical `78 passed, 12 failed, 1000 deselected`（exit 1），失败集合仍是同一批冻结旧契约。全范围 `compileall` 与 `git diff --check` 均 exit 0，后者只有 LF/CRLF 警告。测试继续由仓库外临时目录和私有数据防火墙隔离；未执行 unfiltered、在线模型或真实桌面 UI，未修改 provider、`modules/llm_client.py`、正式用户数据或私人配置。

2026-09-14 至 2026-09-15 根据用户提供的新真实对话和日志继续修复：展示序号的新同义完成、批量目标中的“未完成”状态修饰、重复成功回复、后续明确计划承诺、空标题命令壳、隐藏复杂能力提前降级、删除高风险本地桥接、行动正文污染、显式生成今日复盘的快照修订，以及旧摘要过长和普通聊天机械复述。历史上下文现在只选最多两条查询中心片段，泛化词单独重合不召回；当前句与旧片段冲突时当前句优先，来源元数据默认不展示，也绝不转写长期记忆。diagnostics 的路径不再越权开启记录。

最新隔离证据：受影响组件组合 `94 passed`，桌面运行链与 Agent reliability `77 passed`，历史/上下文 `14 passed`；production `870 passed, 241 deselected`（exit 0），compatibility `151 passed, 960 deselected`（exit 0），historical `77 passed, 13 failed, 1021 deselected`（exit 1）；全范围 `compileall` 与 `git diff --check` 均 exit 0。13 项 historical 失败严格限定为 3 项模型驱动重复/合并、6 项复杂修改/上一任务引用、2 项旧直接记忆保存和 2 项 `opt_v2_006`/汇总。未运行 unfiltered；agent 未操控桌面，也未用固定 DeepSeek 的旧在线 runner 冒充当前 OpenAI provider 验收。用户提供的真实体验是故障输入，自动化结果来自仓库外隔离数据根。未修改 provider、`modules/llm_client.py`、`memory.json`、私人配置或正式用户数据。

## 如何扩展矩阵

新增案例前先回答：

1. 它来自哪种公开语言现象或已确认故障类型？
2. 与现有案例相比，最小变化是什么？
3. 预期不变的是意图、工具还是数据副作用？
4. 是否需要 conversation state、上一轮只读结果或结构化引用？
5. 它是离线合同测试，还是必须留到真实模型手动/脚本验收？

不要为单句新增生产关键词特判；先检查统一语义合同、schema 归一化、Resolver、状态兼容和回复真实性边界。
