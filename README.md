# RoxyPlan

RoxyPlan（洛琪希计划）是一个运行在 Windows 本地的 Python / PySide6 成长陪伴桌宠原型。它把自然聊天、今日计划、行动记录、正式长期记忆、会话连续性和桌宠动作接入同一条受校验的交互链，并保留 Local Web 作为可信局域网内的适配入口。

当前产品阶段为 **V0.9 本地原型**；**V2.2** 是现有中文自然语言交互和成长闭环的稳定化工作流，不代表产品阶段升级或新增 V2.3 功能。它仍不是商业产品或公网服务；没有账号系统、云数据库、任意电脑控制、语音或向量数据库。

当前完成度见 [docs/current_status.md](docs/current_status.md)，用户推荐表达与能力边界见 [docs/capability_boundaries.md](docs/capability_boundaries.md)，中文语境评测方法见 [docs/chinese_interaction_reliability.md](docs/chinese_interaction_reliability.md)，继续开发前请阅读 [docs/v2_2_agent_handover.md](docs/v2_2_agent_handover.md)。

## 核心原则

1. 只有极少量完整匹配、长期稳定的精确命令可以绕过在线语义理解，例如精确“跳舞”、固定 UI 命令，以及当前会话确有待处理状态时的确认或取消。
2. 其他自然语言每轮只形成一个 canonical `SemanticDecision`。模型负责理解意图，程序负责参数、对象、权限、风险、确认和执行校验。
3. 模型不能直接调用 Manager 或修改 JSON。所有业务动作都必须经过白名单工具和 `ToolExecutor`。
4. `ToolResult` 是最终回复的事实骨架。没有匹配的成功写工具结果，就不能声称已经添加、保存、完成或删除。
5. 普通聊天、知识问答、情绪表达和只读查询不得误入写操作或待澄清状态。
6. 当前会话的“刚才”“上一条回答”“第二个”和确认状态不得跨窗口继承。

## 生产交互链

```text
用户消息
→ 精确命令门（仅少量完整匹配）
→ 唯一 SemanticDecision
→ BusinessResolver / SafetyPolicy
→ ToolExecutor
→ ToolResult
→ ResponseComposer / ActionClaimGuard
→ 桌面或 Local Web
```

高精度的正式记忆查询等确定性读入口可以直接生成同一份 `SemanticDecision`，但不能在主链之后再次扫描消息或产生第二次工具决策。桌宠动作由工具返回声明式 `client_actions`，桌面端再通过 `DesktopClientActionDispatcher` 在 UI 线程校验和派发；Local Web 服务端不执行 PySide6 动画。

## 当前能力

### 桌面与 Local Web

- PySide6 透明桌宠、聊天窗口、设置面板、成长面板和记忆面板。
- `jump`、`nod`、`thinking / shake`、`study / scale`、`sleep / wake` 和多帧 `dance` 动作。
- QThread 后台模型请求，避免阻塞 Qt UI。
- 桌面与 Local Web 共用 `ConversationService`、业务服务、工具注册表、本地 Repository 和人格包。
- Local Web 仅作为 `server/` 下的可信局域网适配层，不部署到公网。

### 中文自然语言与安全执行

- 模型语义输出先区分 `chat`、`read`、`write` 和 `clarify`，再归一化为 canonical `SemanticDecision` 的 `chat_only`、`tool_then_reply` 或 `clarify`；执行层只消费这一种契约。
- `BusinessResolver` 只校验、绑定真实对象和解析结构化引用，不重新猜测另一种意图。
- 写批次包含未解析项时零写入；不会把“这些”“那个”“刚才那些”等残片直接落库。
- 命令与内容载荷隔离。计划或记忆正文中的“跳舞、完成、删除”等词不会再次触发动作。
- pending 状态按 `conversation_id` 隔离，只消费语义和字段类型兼容的补充回答。
- 成功只读结果可以支撑事实回答，但不能被误判成一次写操作完成声明。

### 正式长期记忆

- 用户明确说“记住……”“保存到长期记忆”时，低风险且内容完整的请求直接调用 canonical `save_formal_memory`，不再进入普通用户可见的候选审核。
- 普通陈述默认只聊天，不自动写正式记忆，也不自动生成用户可见候选。
- 重复、近似、冲突、敏感和不完整内容继续经过现有 `MemoryService` 治理；需要时最多进行一次确认或澄清。
- 支持同一会话内引用上一条合适的用户消息或 assistant 回复；找不到内容或切换窗口后必须澄清。
- 保存成功后可在当前会话短期内撤销，底层使用归档而非永久删除。
- 候选、冲突、审计和归档底层保留给兼容、自动发现和高级诊断，但候选不再是普通聊天主流程。
- “你知道我什么”等表达通过正式记忆查询工具读取真实数据，再由人格化回复层自然归纳；不展示内部编号、分类标签或候选数量。

### 人格包与统一上下文

- 人格包位于 `data/personas/<persona_id>/`；当前只分发 `roxy`。
- `PersonaRegistry` 扫描、选择和回退人格，`PersonaPack` 负责加载、校验并分离固定人格核心与按需 lore、关系和对话示例。
- 当前人格只能通过设置面板的 `active_persona_id` 修改，聊天文字不能切换角色。
- `ContextBuilder` 是唯一上下文组装点，按预算组织安全边界、人格核心、正式目标、今日状态、当前会话、相关旧会话摘要、相关记忆、人格材料和知识。
- `ToolResult` 先形成不可改变的事实骨架；人格只能调整语气、称呼和简短鼓励，不能改变成功/失败、数量、日期、ID 或错误原因。

### 情景摘要与跨窗口连续性

- 正式长期记忆、情景会话摘要和当前工作状态保持三层分离。
- `ChatHistoryManager` 在会话结束、切换窗口或达到摘要阈值时更新摘要，不在每条消息后重写全部历史。
- 新窗口可以按当前问题检索同一 persona 的相关旧摘要，但不会加载全部旧消息，也不会继承确认、澄清、序号、“刚才”或撤销状态。
- 摘要只能支持“大概讨论过什么”的连续性，不能冒充精确原句、计划事实或正式长期记忆。

## 项目结构

```text
RoxyPlan/
├─ assets/                  # 桌宠图片和舞蹈帧
├─ data/
│  ├─ personas/roxy/       # 当前洛琪希人格包
│  ├─ knowledge/           # 本地 TXT / Markdown 知识文件
│  └─ private/             # 被 Git 忽略的聊天、计划、审计等私人数据
├─ docs/                    # 当前说明、设计、研究和历史报告
├─ frontend/                # PySide6 桌面端及动作派发
├─ modules/                 # 对话、语义、工具、记忆、上下文和 Repository
├─ server/                  # Local Web 适配层
├─ tests/                   # 隔离测试、生产型组合根测试和评测矩阵
├─ requirements.txt
└─ roxy.bat
```

## Windows 环境与启动

推荐 Python 3.10，并在项目根目录创建 `.venv`：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\roxy.bat
```

`roxy.bat` 实际启动 `frontend\pet_app.py`。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -u frontend\pet_app.py
```

DeepSeek 和 Ollama 都是可选模型来源。真实 API Key 只能来自环境变量或被 Git 忽略的本地密钥配置，不应出现在聊天、测试夹具、日志、Issue 或提交记录中。

Local Web 需要额外安装 `server\requirements.txt`，再运行 `run_roxy_web.bat`。局域网访问必须配置 `ROXY_LOCAL_TOKEN`；程序不会自动修改 Windows 防火墙。

## 本地数据与隐私

- `memory.json`：正式长期记忆，私人文件。
- `data/private/chat_history.json`、`chat_summaries.json`：聊天消息和情景摘要。
- `data/private/today_plan.json`、`action_log.json`、`growth_log.json`：计划、行动和成长数据。
- `data/private/memory_candidates.json`、`memory_conflicts.json`、`memory_audit.json`、`memory_archive.json`：兼容候选、冲突、审计和归档。
- `data/pet_config.json`：用户本地桌宠和设置配置。
- `logs/interaction_diagnostics.jsonl`：可选脱敏开发诊断；用户消息仅记录长度和 SHA-256，不记录正文、密钥或完整记忆。

测试必须使用完整临时 Repository 和项目外 `basetemp`。测试防火墙会阻止对 `memory.json` 与 `data/private/**` 的写入，并在测试前后比较真实文件清单和 SHA-256。不要用空 JSON、模板或猜测内容覆盖私人文件。

## 测试与诊断

```powershell
# 全量离线测试
.\.venv\Scripts\python.exe -m pytest -q --basetemp C:\Users\Public\RoxyPlan-pytest-temp

# Python 编译检查
.\.venv\Scripts\python.exe -m compileall -q modules frontend server tests

# 补丁空白与冲突检查
git diff --check
```

研究驱动的中文离线矩阵和在线真实模型验收方式见 [docs/chinese_interaction_reliability.md](docs/chinese_interaction_reliability.md)。离线 Stub 测试验证程序边界，不等价于真实模型中文理解质量；在线验收结果必须单独记录，未运行时不得写成“通过”。

## 文档入口

- [当前项目状态](docs/current_status.md)
- [中文交互可靠性与评测方法](docs/chinese_interaction_reliability.md)
- [V2.1 开发交接](docs/v2_1_semantic_reliability_handoff.md)
- [当前架构清单](docs/current_architecture_inventory.md)
- [运行调用链](docs/runtime_call_chains.md)
- [记忆系统](docs/memory_system.md)
- [对话历史与连续性](docs/chat_history_system.md)
- [回复事实一致性](docs/response_truthfulness.md)
- [路线图](docs/ROADMAP.md)
- [变更记录](docs/CHANGELOG.md)

`docs/analysis/`、`docs/research/` 和旧版本 completion/acceptance 文档是当时决策与验收的历史记录，不能替代当前状态说明。

## 当前边界

- 不提供任意 Shell、PowerShell、文件写入或电脑控制工具。
- 不包含公网部署、账号、支付、云数据库或跨设备同步。
- 不包含语音、向量数据库、完整历史原文 RAG 或外部人格代码加载。
- 当前只附带洛琪希人格包，不包含第二个角色、角色商城或蒸馏训练管线。
- 项目仍需通过持续离线回归和真实模型小规模验收来提高开放中文表达覆盖率，测试通过不代表任意中文输入都能被正确理解。
