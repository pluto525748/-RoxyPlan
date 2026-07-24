# RoxyPlan 开源组件复用候选

研究日期：2026-07-22

## 1. 评估基线

RoxyPlan 当前约束：

- Windows 本地运行。
- Python 3.8.8。
- PySide6 6.4.2，LLM 请求在 QThread Worker 中执行。
- Pydantic 1.10.15、FastAPI 0.103.2。
- Ollama `qwen3:4b`，当前通过 OpenAI-compatible chat 接口实验。
- Manager 和 Local JSON 是真实业务与数据边界。
- 不希望引入数据库、向量数据库、云服务或大规模重构。

因此评价标准不是“功能多”，而是：能否补一个明确缺口，同时保留当前 Registry、Manager、ConversationService、桌面线程和 JSON 数据。

## 2. 指定仓库总览

| 仓库 | 研究快照 | 许可证 | Python/运行要求 | 维护判断 | RoxyPlan 判断 |
| --- | --- | --- | --- | --- | --- |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | `v2.15.0`，HEAD `24d105d` | MIT | Python >=3.10，Pydantic >=2.12 | 非常活跃 | 设计最贴近，但当前只能借鉴；升级运行时后可做隔离 PoC |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `1.2.9`，HEAD `31f90df` | MIT | Python >=3.10，Pydantic >=2.7.4 | 非常活跃 | 可恢复复杂流程强，当前产品流程尚不足以抵消引入成本 |
| [microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel) | Python `1.44.0`，HEAD `e15ae16` | MIT | Python >=3.10，Pydantic 2 | 活跃、多语言大仓库 | plugin/filter 模式可参考，整体引入会重复现有抽象 |
| [scrapinghub/dateparser](https://github.com/scrapinghub/dateparser) | `v1.4.1`，HEAD `762dfff` | BSD-3-Clause | Python >=3.10 | 活跃 | Python 升级后可作为隔离时间解析器候选 |
| [facebook/duckling](https://github.com/facebook/duckling) | `main` HEAD `59a13ff`；release `v0.2.0.0` | BSD License | Haskell/GHC/服务 | 主分支有更新，但公开 release 较旧 | 只借鉴中文规则/语料组织，不直接引入 |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | `main` HEAD `dd5f7e3` | Apache-2.0 | Python >=3.10，Pydantic 2，通常依赖 LLM/embedding/vector store | 活跃 | 与人工确认、本地 JSON、无向量库边界冲突，不适合 |
| [letta-ai/letta](https://github.com/letta-ai/letta) | `0.16.8`，HEAD `b76da90` | Apache-2.0 | Python >=3.11，服务、ORM、持久化体系 | 活跃 | 是完整 Agent 平台，不是可嵌入小组件，当前不适合 |

“维护判断”基于研究日的 release、分支更新和测试结构，不代表长期保证。

## 3. Pydantic AI

### 关键实现

- [`tools.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/tools.py)：函数工具、schema 和重试配置。
- [`_function_schema.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_function_schema.py)：Pydantic Core 参数验证。
- [`_agent_graph.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py)：模型/工具循环。
- [`_tool_execution.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/_tool_execution.py)：并行分段、结果和 retry part。
- [`tool_manager.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/pydantic_ai_slim/pydantic_ai/tool_manager.py)：参数校验、执行、审批和重试。
- [`tests/test_tools.py`](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/tests/test_tools.py)：失败重试、延迟工具、审批恢复。

### 直接价值

- provider-neutral 工具协议。
- schema 与参数校验。
- 模型错误反馈和有界重试。
- tool-call ID 和完整消息链。
- human-in-the-loop 与 usage limits。

### 不直接引入的原因

1. Python/Pydantic 与当前环境不兼容。
2. 会迫使 FastAPI 和相关依赖一起升级。
3. qwen3:4b 是否能稳定执行 schema 工具尚未通过项目语料评测。
4. 它不能替代 RoxyPlan 的中文指代、计划状态和确认策略。

结论：**当前复制设计模式；未来运行时升级后，方案 B 可只替换模型/工具协议层。**

## 4. LangGraph

### 关键实现

- [`chat_agent_executor.py`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py)：agent/tools 循环和 step 限制。
- [`tool_node.py`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/langgraph/prebuilt/tool_node.py)：工具校验、错误 `ToolMessage` 和并行。
- [`langgraph/types.py`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/langgraph/langgraph/types.py)：`Command`、`interrupt` 与恢复。
- [`tests/test_tool_node.py`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/tests/test_tool_node.py)：工具错误、未知工具、注入和中断。
- [`tests/test_react_agent.py`](https://github.com/langchain-ai/langgraph/blob/1.2.9/libs/prebuilt/tests/test_react_agent.py)：检查点、并行和图循环。

### 直接价值

- 状态机、条件边和清晰的运行轨迹。
- checkpointer 与可恢复中断。
- 多步骤和长期运行流程比手写循环更稳。

### 不直接引入的原因

1. Python/Pydantic 不兼容。
2. LangChain Core 工具和消息类型会成为新的中心契约。
3. RoxyPlan 当前单轮最多三步，尚未达到图框架明显胜出的复杂度。
4. checkpointer 需要新的持久化边界；硬套 Local JSON 会增加状态迁移工作。
5. `interrupt()` 是暂停机制，不是安全策略，现有 SafetyPolicy 仍需保留。

结论：**当前借鉴显式状态、检查点、interrupt 语义；到跨天工作流、多 Agent 或可靠恢复成为核心需求时再评估。**

## 5. Semantic Kernel

### 关键实现

- [`kernel_function_decorator.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/functions/kernel_function_decorator.py)
- [`function_calling_utils.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/connectors/ai/function_calling_utils.py)
- [`kernel.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/kernel.py)
- [`auto_function_invocation_context.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/semantic_kernel/filters/auto_function_invocation/auto_function_invocation_context.py)
- [`test_chat_completion_with_function_calling.py`](https://github.com/microsoft/semantic-kernel/blob/python-1.44.0/python/tests/integration/completions/test_chat_completion_with_function_calling.py)

它的函数 metadata、插件过滤器、自动/手动调用模式和多 connector 测试值得参考。但 Kernel/Plugin/Filter 会与 `ConversationService/ToolRegistry/SafetyPolicy` 重叠，Agent 与进程编排范围远大于 RoxyPlan 当前需求。

结论：**参考调用前后 filter 和 connector 测试，不引入。**

## 6. dateparser

### 关键实现

- [`date.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/date.py)
- [`parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/parser.py)
- [`freshness_date_parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/freshness_date_parser.py)
- [`zh-Hans.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/dateparser/data/date_translation_data/zh-Hans.py)
- [`test_freshness_date_parser.py`](https://github.com/scrapinghub/dateparser/blob/v1.4.1/tests/test_freshness_date_parser.py)

它是本次指定项目里最有可能成为“单点直接依赖”的组件，但最新版本仍要求 Python 3.10。即使引入，也只负责时间候选，不负责意图、计划标题或执行。

结论：**当前只参考；Python 升级后，用中文领域测试决定是否引入。不要为兼容 Python 3.8 随意锁死陈旧版本。**

## 7. Duckling

Duckling 的价值在源码组织：

- [`Time/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Time/ZH/Rules.hs)
- [`Time/ZH/Corpus.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Time/ZH/Corpus.hs)
- [`Duration/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Duration/ZH/Rules.hs)
- [`Numeral/ZH/Rules.hs`](https://github.com/facebook/duckling/blob/main/Duckling/Numeral/ZH/Rules.hs)

中文数字、时间、时长按 dimension 和 corpus 分离，测试样例即规则资产。但需要 Haskell 构建或独立服务，不符合当前 Windows 轻量桌宠边界。

结论：**只借鉴“规则 + 语料”布局。**

## 8. Mem0

关键源码：

- [`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py)：`Memory.add/search/update`，初始化 embedder、vector store、LLM。
- [`mem0/memory/utils.py`](https://github.com/mem0ai/mem0/blob/main/mem0/memory/utils.py)：事实提取提示、JSON 解析和小模型输出归一化。
- [`tests/test_memory_integration.py`](https://github.com/mem0ai/mem0/blob/main/tests/test_memory_integration.py)：记忆集成测试。

`Memory.add(infer=True)` 会用 LLM 提取事实，再用 embedding/vector store 查重和写入。这与 RoxyPlan 的原则相冲突：

- 当前禁止自动把模型推断写成正式长期记忆。
- 候选必须人工审核。
- 当前明确不引入向量数据库。
- 已有兼容旧 `memory.json` 的 MemoryManager 和治理审计。

可以参考其“事实归一化”和“小模型返回不同 JSON 形状的容错测试”，不应替换现有记忆系统。

结论：**只参考容错思路，当前不适合。**

## 9. Letta

关键源码：

- [`services/agent_manager.py`](https://github.com/letta-ai/letta/blob/0.16.8/letta/services/agent_manager.py)：Agent、消息和内存初始化。
- [`services/tool_executor/tool_execution_manager.py`](https://github.com/letta-ai/letta/blob/0.16.8/letta/services/tool_executor/tool_execution_manager.py)：多类工具 executor 和持久化。
- [`schemas/memory.py`](https://github.com/letta-ai/letta/blob/0.16.8/letta/schemas/memory.py)：core memory block 与上下文渲染。
- [`orm/agent.py`](https://github.com/letta-ai/letta/blob/0.16.8/letta/orm/agent.py)：ORM 数据模型。
- [`server/rest_api/routers/v1/agents.py`](https://github.com/letta-ai/letta/blob/0.16.8/letta/server/rest_api/routers/v1/agents.py)：服务 API。

Letta 是完整的有状态 Agent 服务平台，包含 ORM、服务端、工具沙箱和多种持久化。引入它相当于迁移产品架构，不是复用一个工具闭环。

结论：**仅参考 core/recall memory 分层，不适合 RoxyPlan 当前阶段。**

## 10. 额外候选

### 10.1 ollama/ollama-python

- 仓库：[ollama/ollama-python](https://github.com/ollama/ollama-python)
- 研究版本：`v0.6.2`，HEAD `3c5a26a`
- 许可证：MIT
- 运行要求：Python >=3.8，但依赖 Pydantic >=2.9
- 核心：[`ollama/_client.py`](https://github.com/ollama/ollama-python/blob/v0.6.2/ollama/_client.py)、[`ollama/_types.py`](https://github.com/ollama/ollama-python/blob/v0.6.2/ollama/_types.py)、[`examples/tools.py`](https://github.com/ollama/ollama-python/blob/v0.6.2/examples/tools.py)

官方示例直接把 Python callable 或 schema 放入 `tools`，读取 `response.message.tool_calls`，执行本地函数后追加 assistant tool-call 消息和 `role=tool` 结果，再次请求模型。

这是唯一在 Python 版本上接近直接可用的原生 tool-call 候选，但它会引入 Pydantic 2，与当前 Pydantic 1/FastAPI 栈可能冲突；而且 qwen3:4b 的真实通过率未知。

判断：**值得做隔离兼容性 PoC，不应在未经评测时直接替换 `llm_client.py`。**

### 10.2 pytransitions/transitions

- 仓库：[pytransitions/transitions](https://github.com/pytransitions/transitions)
- 研究版本：`0.9.3`，HEAD `bd42b38`
- 许可证：MIT
- 支持 Python 3.8；基础依赖仅 `six`
- 核心：[`transitions/core.py`](https://github.com/pytransitions/transitions/blob/0.9.3/transitions/core.py)、[`tests/test_core.py`](https://github.com/pytransitions/transitions/blob/0.9.3/tests/test_core.py)

`Machine/State/Event/Transition`、conditions/unless/before/after 足以表达澄清、确认、执行和失败状态，也支持序列化机器对象。

判断：**可直接作为依赖的候选，但当前状态量仍小；先用显式枚举和转移表即可。流程继续复杂后再引入。**

### 10.3 instructor-ai/instructor

- 仓库：[instructor-ai/instructor](https://github.com/instructor-ai/instructor)
- 研究版本：`v1.15.4`，HEAD `47fdb2c`
- 许可证：MIT
- Python >=3.9，Pydantic >=2.8

擅长把 provider 输出解析成 Pydantic 模型并重试，适合作为 JSON Intent fallback 的参考。但不能直接执行工具，也不能替代安全策略和业务后置条件。

判断：**当前只参考 structured output/reask；升级 Python 后仍需比较它与 Pydantic AI 的重复度。**

### 10.4 openai/openai-agents-python

- 仓库：[openai/openai-agents-python](https://github.com/openai/openai-agents-python)
- 研究版本：`v0.18.3`，HEAD `5921667`
- 许可证：MIT
- Python >=3.10，Pydantic >=2.12.2
- 核心：[`src/agents/run.py`](https://github.com/openai/openai-agents-python/blob/v0.18.3/src/agents/run.py)、[`src/agents/run_internal/tool_execution.py`](https://github.com/openai/openai-agents-python/blob/v0.18.3/src/agents/run_internal/tool_execution.py)、[`src/agents/tool.py`](https://github.com/openai/openai-agents-python/blob/v0.18.3/src/agents/tool.py)、[`src/agents/run_state.py`](https://github.com/openai/openai-agents-python/blob/v0.18.3/src/agents/run_state.py)

工具、guardrail、approval、run-state 序列化和最大 turn 设计成熟，但当前 Python 不兼容，默认生态更贴近 OpenAI Responses。RoxyPlan 的目标是 provider-neutral 本地助手，不宜把核心绑定到单一 SDK。

判断：**参考 run-state schema、工具 guardrail 和 turn budget，不直接引入。**

## 11. 直接使用、借鉴和排除清单

### 当前可考虑直接使用

- 无。`transitions` 技术上兼容，但当前收益不足以立刻增加依赖。
- `ollama-python` 技术上接近，但 Pydantic 2 冲突和小模型可靠性必须先隔离验证。

### 值得复制设计模式

- Pydantic AI：schema、tool-call ID、retry part、审批恢复、工具预算。
- LangGraph：状态节点、条件边、interrupt、检查点、可恢复运行。
- Semantic Kernel：调用前后 filter 和 connector 测试矩阵。
- Duckling：实体规则和 corpus 共存。
- Mem0：小模型事实 JSON 形状容错，但不采用自动写记忆。
- Letta：core/recall/summary memory 边界。
- OpenAI Agents SDK：版本化 run-state 和 tool guardrail。

### 当前不建议采用

- 整体 LangGraph/Semantic Kernel/Letta/Mem0。
- 向量数据库与 embedding 模型。
- Duckling Haskell 服务。
- 为使用某个框架而在同一版本里同时升级 Python、Pydantic、FastAPI 和 Agent 架构。

## 12. 对本地小模型的实际价值

框架不能把弱模型变强，只能让失败可见、可验证、可重试。qwen3:4b 的主要限制仍是：

- 对动作与咨询边界理解不稳定。
- 复杂 schema 参数容易缺失或臆造。
- 多步骤工具调用和指代对上下文要求高。
- 工具失败后可能继续用自然语言假装完成。

因此任何候选都必须用同一份 RoxyPlan 语料比较：

1. chat/action 分类准确率。
2. 工具名准确率。
3. 参数完整率和臆造率。
4. 需要澄清时的识别率。
5. 危险操作错误执行率。
6. 工具失败后的虚假完成率。
7. 平均延迟和重试次数。

2026-07-22 的本机实测进一步确认了这一点：`qwen3:4b` 原生 tools 在充足预算下，对单一添加和带结构化 ID 的修改均为 3/3，但“完成计划”0/3、多工具仅 1/3，且平均生成 714.3 token、约 11.2 秒。项目现有 JSON fallback 的五个样本也只有咨询类 `chat` 通过本地解析。

因此当前应使用“高精度规则 + 模型动作提议 + 本地校验 + 不确定即澄清”，而不是把 JSON fallback 当作必然可靠的第二条执行通道，更不能启用无校验的原生自动工具循环。完整数据见 [`agent_tool_calling_github_review.md`](agent_tool_calling_github_review.md)。
