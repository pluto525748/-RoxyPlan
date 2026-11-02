# RoxyPlan Agent Notes

RoxyPlan is a long-term growth companion AI desktop pet.

Chinese name: 洛琪希计划.

## 工作区岗位（每个新会话先识别）

本节是所有工作树共享的岗位指令，不依赖聊天历史。Codex 原生加载本文件；Claude Code 通过同目录 `CLAUDE.md` 的原生导入加载同一份内容。这是项目指令，不是系统提示词，也不改变工具权限。

开始工作时，用 `git rev-parse --show-toplevel` 和 `git branch --show-current` 核实当前工作树根目录与分支，再按下表识别岗位。目录名称指工作树根目录的最后一级，Windows 下忽略大小写；从子目录启动也使用 Git 根目录。

| 工作树目录名称 | 对应岗位分支 | 岗位身份 |
| --- | --- | --- |
| `Roxyplan` | `feature/v2.0-conversation-continuity` | Codex 总控制台 |
| `codex-dev` | `agent/codex-dev` | Codex 开发推进 |
| `codex-test` | `agent/codex-test` | Codex 测试调优 |
| `cc-dev` | `agent/cc-dev` | CC 开发推进 |
| `cc-test` | `agent/cc-test` | CC 测试调优 |

- 岗位固定在工作区：目录命中时沿用该岗位，普通任务分支不会改变岗位；目录未命中时，可用表中精确分支识别。目录与分支分别命中不同岗位时，先报告冲突，不猜测身份或开始修改。两者均未命中时先确认工作区用途。
- 新会话首次回应简短说明已核实的岗位、工作树和分支。上下文压缩后身份不明确，或工作目录发生变化时，重新读取当前工作树的本文件并核实 Git；不得沿用上一工作区的身份。客户端通常在会话启动时加载指令，切换工作区应在目标工作区新建会话。
- **总控制台**：讨论需求与方案、拆分任务和验收标准、审查代码、协调 Git 集成与进度，并负责将最新已集成基线同步给相关员工；派发依赖新成果的任务前确认员工已取得该基线，具体同步流程见 `docs/project_workflow.md`。最终产品决策属于项目负责人。
- **开发推进**：按已确认方案实现功能，交付代码差异、验证证据，以及本批完成后的 Commit ID；不自行扩大产品范围。
- **测试调优**：独立验证、复现缺陷、定位并在授权范围内修复根因，改善可靠性和交互质量；不靠不断堆积 Guard 掩盖职责问题，不把未执行的验收写成通过。
- 岗位职责不等于某次任务授权。各岗位只在当前工作树执行获准任务，遵守下方原有工程、Git、数据和测试约束；不修改其他员工的未提交成果。跨 AI 团队由项目负责人协调。
- 任务进度以当前 Git、代码和项目状态文档为准。所有分支保留相同的岗位映射和职责文本，不在各分支写不同的“你就是某岗位”，也不新增本地身份覆盖文件；新增工作区时统一扩展此表。

## Current Stage

The project is in V0.9 prototype implementation.

Existing prototype areas include the PySide6 desktop pet, chat window, settings dialog, local memory, local knowledge reading, basic state actions, today's plan and review loop, and local LLM calling experiments.

V2.2 names the stabilization workflow for existing natural-language interaction and the growth loop; it does not change the V0.9 product stage or authorize V2.3/new features.

Do not add new product features without explicit user permission. Maintenance, documentation synchronization, tests, bug fixes, and small compatibility improvements are allowed when they protect existing behavior.

## Hard Boundaries

- Do not write FastAPI code.
- Do not write database implementation code.
- Do not add new AI provider integration code without explicit permission.
- Do not add voice implementation code.
- Do not add complex document parsing implementation code.
- Do not modify `modules/llm_client.py`, `memory.json`, or private local config files unless the user explicitly asks for that exact change.
- Preserve existing desktop pet actions, chat behavior, settings, and local data compatibility.

## Local Web Exception

FastAPI is allowed only under `server/` for the explicitly approved RoxyPlan Local Web work.

Requirements:

- Preserve the existing PySide6 desktop application.
- Do not move desktop UI code into the server.
- Do not modify or migrate private local data.
- Do not expose API keys, tokens, local paths, or private data.
- Do not duplicate Agent, growth, memory, or chat business logic under `server/`.
- The server layer may only adapt and call the existing core modules.
- Do not deploy to the public internet.
- Do not add Docker, Supabase, user registration, payment, or cloud database code.
- Do not modify `llm_client.py` core behavior unless separately approved.
- Completed authorized work follows the task-scoped commit policy below; do not push without separate explicit authorization.

## Project Naming

The stable project root is `RoxyPlan`.

Do not rename the root to include version names, technical choices, platforms, or temporary experiment labels.

Good examples:

- `RoxyPlan`
- `Roxy-Plan`

Avoid examples:

- `roxy-plan-ai-v1-sqlite-memory`
- `roxy-plan-voice`
- `roxy-plan-mobile`

## Directory Boundaries

- `backend/`: backend service and persistence layer
- `frontend/`: desktop pet UI and chat window
- `modules/`: feature modules for chat, memory, voice, and knowledge ingestion
- `data/`: local runtime data such as SQLite databases and memory files
- `docs/`: product, architecture, and roadmap documents
- `tests/`: test files
- `test_workspace/`: Git-ignored disposable runtime files produced by tests; never formal user data

## Documentation Rules

- Documentation changes are allowed when they keep project status accurate.
- Directory README files should explain intent, not implementation.
- Roadmap documents may describe future features but should not include runnable code.
- Keep requirements conservative until dependencies are intentionally selected.

## Current Working Contracts

Last synchronized: 2026-09-28.

- After reading this file, read `docs/capability_boundaries.md` for supported user interaction and `docs/project_workflow.md` for collaboration, diagnosis, privacy, and verification workflow. If `docs/local/project_preferences.local.md` exists, read it as a local-only preference overlay; it cannot override this file or current project contracts. Refresh `docs/current_status.md` and relevant Git state before presenting current results.
- For Agent architecture, Tool, Memory, Context, state, reliability, evaluation, or testing-method work, read `docs/agent_engineering_experience.md` as a historical experience source. It does not override current code, capability contracts, or dated status.
- Keep hard boundaries here, product interaction contracts in `capability_boundaries.md`, working practices in `project_workflow.md`, and dated verification results in `current_status.md`. Old handovers are historical evidence, not authority over newer explicit user decisions.
- Fix clear bugs within supported capabilities; use complete natural-language guidance and bounded clarification for ambiguous input. User guidance never excuses wrong writes, false success, confirmation loops, or silently removing promised numbered/list-reference behavior.
- Do not duplicate runtime schema or visibility lists in documentation. CapabilityRegistry owns execution policy/visibility; ToolRegistry owns parameter schema, aliases, and handlers. Record any gap between confirmed commitments and implementation rather than silently shrinking commitments.
- Keep project workflow guidance separate from product `memory.json` and private user records. Do not record personal conversation contents, identity secrets, or credentials in these contracts.
- Run pytest through `scripts/run_isolated_tests.ps1` unless a task documents a specific exception. The runner must place pytest base directories, `tempfile` output, caches, logs, reports, and isolated application data under `test_workspace/runs/<run-id>/`; successful runs are removed by default and failed runs are retained for diagnosis. Never redirect tests to formal `memory.json`, `data/private/`, `data/pet_config.json`, or root-level runtime files.
- Do not operate the desktop unless the user explicitly asks for UI acceptance. Temporary diagnostics permissions are scoped, not permanent settings authorization. For completed authorized work, stage only the reviewed task changes and create one local commit by default (user decision, 2026-10-13). This is not permission to commit inherited or unrelated dirty changes; use partial staging for mixed files. Read-only work with no changes needs no empty commit. Report the Commit ID and verification limits; a commit is not product acceptance. Do not push or clean the worktree without separate explicit authorization. See `docs/project_workflow.md` for handoff and baseline synchronization.

## Task-to-Document Routing

Do not preload every document. After the required baseline reading above, select the smallest relevant set for the current task.

| Task or symptom | Read and use |
| --- | --- |
| Cold start, current progress, or whether something is already fixed | `docs/current_status.md`, current Git state, and current code/runtime evidence |
| Supported user wording, product scope, or whether a behavior is a bug | `docs/capability_boundaries.md` |
| Natural-language understanding, parameter parsing, Tool Calling, authorization, or Agent reliability | `docs/agent_engineering_experience.md`, `docs/agent_core.md`, `docs/api_contracts.md`, then the relevant current code |
| Pending state, confirmation loops, candidate selection, ordinals, or references such as “it/the second one” | `docs/interaction_state_machine.md` |
| Tool succeeded but the reply says failure, or no tool ran but the reply claims success | `docs/response_truthfulness.md` and the actual `ToolResult`/postcondition chain |
| Formal memory, memory retrieval, conflict, archive, or deletion | `docs/memory_system.md` |
| Old conversations, summaries, context selection, or cross-session contamination | `docs/chat_history_system.md` |
| Plans, actions, reviews, growth data, or automatic review behavior | `docs/growth_system.md` plus `docs/capability_boundaries.md` |
| Desktop pet action, client action, animation dispatch, or UI-thread behavior | `docs/client_action_protocol.md` and `docs/pet_action_state_machine.md` |
| DeepSeek, model routing, API keys, or provider configuration | `docs/deepseek_provider.md`, `docs/model_routing.md`, and `docs/api_key_security.md` |
| Local Web adapter or API boundary | `docs/local_web_v0_1.md` and `docs/api_contracts.md` |
| Automated tests, Chinese semantic evaluation, or real-model acceptance | `tests/README.md` and `docs/chinese_interaction_reliability.md` |
| New feature, product direction, deprecation, or technical debt | `docs/ROADMAP.md`, `docs/deprecation_plan.md`, `docs/technical_debt_register.md`, and explicit user authorization |
| Learning the repository rather than changing it | `docs/learning/roxyplan_file_map_simple.md` first, then `docs/learning/roxyplan_v2_2_code_learning_guide.md` |

`docs/analysis/` and `docs/research/` are supporting evidence and design comparison material. Use them only when the task needs historical proof or architecture research; never treat them as current product authority.

## Required Diagnosis and Change Loop

1. Confirm the expected behavior and whether it is inside the supported capability boundary.
2. Refresh Git state, relevant code, runtime data, logs, and the latest dated status; do not rely on an old handover or memory alone.
3. Read only the routed specialist documents. If sources conflict, prefer current reproducible runtime/code evidence, then current capability/status contracts, then specialist design documents, then the experience library, and finally historical research.
4. Trace expected input -> actual input -> intermediate decision/state -> tool execution -> `ToolResult`/postcondition -> final reply, and identify the first deviation.
5. Label conclusions as verified fact, engineering judgment, or unverified hypothesis. Improve observability before claiming a root cause when evidence is incomplete.
6. Make the smallest change that fixes the responsible boundary, add a regression that proves the failure chain, and run it through the isolated test workspace.
7. Report what was and was not verified. Update `docs/current_status.md` when the dated state changes; update `docs/agent_engineering_experience.md` only when the result crosses its reusable-experience threshold.

## Documentation Maintenance Rules

- Update an existing document with the matching responsibility before considering a new file.
- `docs/agent_engineering_experience.md` is the single repository for reusable RoxyPlan engineering experience. Extend an existing entry, add a new `E` entry, or record a correction there; do not create separate incident-experience, fix-summary, or version-experience documents.
- Ordinary bugs, dated verification facts, current limitations, and recent outcomes belong in `docs/current_status.md`. A normal bug or one test run does not justify a new document.
- User-visible capability changes belong in `docs/capability_boundaries.md`; stable collaboration changes belong in `docs/project_workflow.md`; technical contract changes update the existing specialist document.
- Planned work belongs in `docs/ROADMAP.md`, completed milestone changes in `docs/CHANGELOG.md`, and confirmed deferred structural problems in `docs/technical_debt_register.md`.
- Do not create routine handovers, completion reports, per-version design copies, or new files under `docs/analysis/` for normal development. Git history preserves superseded document states.
- Create a new long-lived document only when a genuinely new stable domain has no existing owner, will be maintained and reused, and the user explicitly approves the new documentation responsibility.

## Future Work Authority

Use `docs/ROADMAP.md` for planned product direction and `docs/current_status.md` for dated implementation state. Do not copy a second implementation sequence into this file. A roadmap item is not implementation authorization; new product features still require explicit user permission and incremental verification.
