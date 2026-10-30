# Tests

## Windows Beta preparation

The root `roxy_main.py` is the Beta entry; `roxy.bat` remains the source
Developer entry. Beta stores records outside the program directory, by default
under `%LOCALAPPDATA%/RoxyPlan`. For an isolated Beta acceptance run, specify
`--data-root` with a new absolute directory. Beta cannot reuse simulated data
or accept Developer date controls. Never test against the default real data
root. Changing the program directory must keep the same chosen data root.

Build only in the coordinator's sole build slot, using a dedicated Windows
x64 Python 3.10.1+ venv, separate from the application's `.venv`:

```powershell
& 'C:\path\to\build-venv\Scripts\python.exe' -m pip install -r packaging\requirements-build.txt
& .\scripts\build_beta.ps1 -PythonExecutable 'C:\path\to\build-venv\Scripts\python.exe' -ProductVersion '0.9.0-beta.1' -BuildSlotApproved
```

The build retains a unique run directory with phase logs, exit code, source
hashes, resource whitelist, dependency versions, and ZIP hashes. Review the
entire onedir and external manifest; the build status only proves package
checks, not GUI, HTTPS, clean Windows, persistence, or upgrade acceptance.
Distribute the complete folder, never the EXE alone. Actual API credentials,
user stores, logs, preview artifacts, and original material packs are excluded.

Feedback export is available through settings when startup injects the instance
context. The user reviews categories and explicitly consents before generating
a ZIP. Diagnostic logs remain fingerprints and summaries; exports do not
promise full original model input/output. Daily sessions follow business date;
legacy sessions follow recorded timestamps. Development logs select whole runs
by UTC start date, so a cross-midnight run may include events outside the range.

RoxyPlan tests cover the desktop client, Local Web adapter, Agent pipeline,
memory and growth repositories, chat history, client actions, and Chinese NLU
benchmarks.

## 持续回归语料：复用历史表达与真实失败

语料优先复用现有 JSON／JSONL 文件，不新增数据库服务，也不把业务用户数据写入公开测试库。2026-10-14 只读清点：`tests/nlu_benchmark/train.jsonl`／`dev.jsonl`／`test.jsonl` 分别有 2100／700／700 条，共 3500 条单轮分区记录；`multi_turn_cases.jsonl` 有 400 组，`tests/eval/interaction_golden_cases.json` 有 30 条。seed／generated／adversarial 文件与这些分区存在来源关系，不能相加冒充独立样本总数。数量是文件记录统计，不是当前通过率或真实用户样本数。

- 原始语料、历史预期与 `final_test.sha256` 保留不变。先核对当前产品合同，使用独立的引用索引与当前预期补充历史用例；不为通过而改写历史失败。现有 benchmark 主要检查本地路由／语义解析，不能替代在线模型、多轮业务执行、持久化与桌面验收。
- 每个新增场景至少记录来源、原句／轮次、前置状态、当前预期、禁止写入、真实工具及数据结果、受测版本。只有句子列表，无法判断执行对错。真实用户原始证据保存在授权的私人证据目录；进入公开测试的表达使用经核对的非私人复现内容，索引不包含密钥与私人路径。
- 来源明确区分：历史工程测试、真实反馈、公开数据集、人工／模型合成改写。公开或生成表达不得当作真实用户实验；改写只增加风格覆盖，不增加独立用户数。同一原句的变体作为一个场景家族，避免同时进入开发和盲测而夸大泛化效果。
- 首批优先覆盖今日任务建议、长期规划与目标选择、操作方法询问、肯定／否定、确认／取消、计划完成、行动与长期记忆；保留已好用路径，专门检查误写和假成功。界面自然程度可人工评审，工具是否执行及数据是否改变由程序事实核对。
- 每次修复先跑受影响场景家族与原有成功路径；积累后的较大集合低频运行。新增或失败的模型路径选少量真实 DeepSeek 验证，提前固定请求／Token 上限，逐轮保留首次结果。大语料不等于每次全部调用在线 API，也不自动恢复用户暂缓的分层全量回归。
- 可研究 [MASSIVE 的中文分区](https://huggingface.co/datasets/AmazonScience/massive)：含意图及字段标注，数据源经过翻译／本地化，不是本产品真实对话；原数据许可及署名按 [官方 NOTICE](https://github.com/alexa/massive/blob/main/NOTICE.md) 维护。只筛选相关表达，并重标 RoxyPlan 当前预期；数据集中支持的日程操作不因此成为本产品能力。
- [CLINC150](https://github.com/clinc/oos-eval) 为英文单轮众包表达，可参考支持范围内外的判别，中文翻译应明确标注转换来源。两个公共集都不能直接覆盖本产品“建议后选择 → 工具执行”的多轮状态问题，此类场景仍需真实记录及人工审核变体。

上述是语料维护与后续采样方案，本轮没有下载公共全集、导入数据库、运行大集合或新增在线请求。

### 2026-10-22 来源及覆盖核对

现有基准的主要句面由 `scripts/generate_chinese_nlu_benchmark.py` 中的项目场景和受控变体构造，不是公开用户对话集。3500条单轮分区记录含50条基句的3200个礼貌前后缀变体及300条对抗变体；合并去重为3392个不同句面。train/dev有48句、train/test有60句完全重叠，因此不作为严格无泄漏盲测。400条多轮记录只有50种完整对话，每种重复8次。历史文件名 `train` 不代表本项目训练过模型；现有基准运行器关闭LLM，仅评估本地路由与解析。

测试方法参考记录在 `docs/chinese_interaction_reliability.md`：CheckList、Contrast Sets、中文省略重构、CREAD、DialogUSR、CrossWOZ用于组织语言现象和边界对照。当前来源记录未显示直接导入这些研究的数据原句；MASSIVE与CLINC150只作调研，未收录表达。参赛时分别披露方法参考、项目合成语句、脱敏故障复现与真实模型返回，不把它们合称开源训练数据。

129条Beta语料为107单轮、22多轮，9个业务家族加1个历史家族；登记来源为101条既有测试、27条人工变体、1条真实反馈。原状态不因其他测试通过而自动改绿。`bt_today_add_14`（方向选项短答误设新增）、`bt_cd_09`（无行动正文同时预期成功与澄清）、`bt_cd_10`（非今日新增限制混入明日只读查询）需要先核对当前预期；原句与历史证据保持原样。完整复盘、7/30日事实建议、进程重启、损坏数据、睡眠、首次提示、动作与发行等场景应结合各专项测试检查，129条本身不构成全部用户场景覆盖。

## 模拟跨天独立启动

源码根目录 `roxy_simulated_days.bat` 启动现有隔离桌宠和聊天，显示测试日期及“推进一天”。首次默认从系统当天开始，后续启动接续该实例保存的日期；不修改Windows日期。默认实例位于系统本地应用数据目录的 `RoxyPlanTestInstances/simulated-days`，与日常数据分开，不复制正式聊天、记忆、设置或密钥。模型设置在测试实例内单独管理。

需要另建测试实例或指定首日时：

```powershell
& .\scripts\run_simulated_days.ps1 -InstanceRoot 'C:\path\to\isolated-simulation' -StartDate '2026-10-01'
# 同一实例后续启动不传StartDate，接续已经推进的日期。
& .\scripts\run_simulated_days.ps1 -InstanceRoot 'C:\path\to\isolated-simulation'
```

模拟实例不得用作真实成长成效证据，也不与普通Beta数据互换。模拟日期能力复用现有业务时钟和共享服务，普通日常启动继续使用真实日期。打开该可见程序属于桌面占用，AI员工仍须先取得本次桌面使用权。

## Private-data isolation

Tests must never open the project's real `memory.json` or `data/private/`
stores. Shared support lives in:

- `tests/isolation_support.py`: creates a complete temporary memory repository;
- `tests/private_data_guard.py`: fingerprints protected project paths and rejects
  Repository constructors that resolve to real data;
- `tests/conftest.py`: installs the constructor guards and an autouse session
  snapshot assertion.

Prefer the pytest fixtures:

- `isolated_memory_paths`
- `isolated_memory_repository`
- `isolated_memory_service`

Tests that use `unittest` style or `tempfile.TemporaryDirectory` can call
`build_isolated_memory_manager`, `build_isolated_candidate_manager`, or
`build_isolated_memory_service` from `tests.isolation_support`.

Passing only a temporary `memory_file` to `MemoryManager` is forbidden: the
other defaults would still select production conflict, audit, backup, and lock
paths. Candidate, chat, growth, secret, and usage stores must likewise receive
complete temporary paths or injected Repository instances.

The session guard compares protected file/directory inventories, SHA-256, and
sizes before and after pytest. Modification times are diagnostic only. A
difference fails the run and is reported; tests never delete or roll back real
data automatically.

## Contract lanes

Every collected test receives exactly one reporting marker from
`tests/contract_taxonomy.py`:

- `production_contract`: current unified desktop/server behavior, current
  model schema, and current safety boundaries;
- `compatibility_contract`: legacy APIs, rollback surfaces, or V2.1 fixtures
  that are still deliberately supported;
- `historical_baseline`: frozen evaluation evidence that must not be presented
  as the current product contract.

Markers are for attribution, not for hiding failures. No lane is automatically
skipped or marked xfail, and an optional unfiltered run still executes all
three. Always report production, compatibility, and historical separately
whenever a historical expectation differs from current behavior; an unfiltered
aggregate is additional evidence, not a replacement or a mandatory V2.2 run.

Current model-response fixtures must pass through
`build_current_semantic_payload` and explicitly provide `subject`, `polarity`,
`modality`, `request_mode`, and `explicit_command`. V2.1 fixtures may only use
`adapt_legacy_v21_semantic_payload`; its inferred defaults are compatibility
metadata and must never be copied into a current production fixture. The
reviewed online subset keeps its current fields in
`tests/fixtures/v22_online_semantic_contract.json` without rewriting the V2.1
source matrix.

Memory-candidate manager APIs remain compatibility surfaces, but candidate
tools are retired from normal chat. Golden cases preserve their old tools as
`legacy_tools` while the current `tools` expectation remains empty.

`CapabilityRegistry` is the sole authority for model visibility and execution
policy (`side_effect`, risk, confirmation, and reversibility). `ToolRegistry`
continues to own parameter schemas, aliases, and handlers; contract tests must
fail if the projected runtime policy or visible tool set drifts from the
capability catalog.

## V2.2 release-closure safety baseline

The closure suite keeps these behaviors as production contracts:

- a negated read whose actual purpose is advice runs zero tools;
- an explicit date in the current sentence overrides stale model or prior-turn
  dates;
- a successful plan read records stable IDs and display order in a
  conversation-scoped `ReadSnapshot`; ordinal and batch follow-ups bind those
  IDs and re-read live objects before a write;
- completing the recently displayed batch shows one preview, performs no
  write before confirmation, and requires one confirmation for the batch;
- one pending operation asks at most two clarification rounds, then cancels
  without a write and returns honest executable guidance;
- an implicit ordinary stable memory is only an in-process confirmation offer
  until the user confirms; implicit sensitive information is not proactively
  offered, persisted, or left pending;
- an explicit, complete "remember this" request uses the formal-memory tool;
  plans, actions, and reviews never become long-term memory automatically;
- complex plan modification/merge and candidate-management tools remain hidden
  from the model; a model proposal for one fails honestly with zero tool
  results and unchanged data;
- fuzzy history queries return only verified old summaries, snippets, dates, or
  sources, exclude the current conversation, and report an honest no-match;
- startup review backfill checks yesterday only, is idempotent, skips empty
  days, never calls a model, and does not crash startup on failure; its setting
  defaults on independently from the evening-review reminder, and corrupt data
  warns immediately without writing a review;
- growth-log queries and statistics use exact `YYYY-MM` calendar-month bounds,
  and any natural-language summary is grounded in a successful tool result.

Primary focused files are `tests/test_v22_release_closure.py` and
`tests/test_v22_growth_closure.py`; they supplement rather than replace the
production, compatibility, and historical runs.

## Result reporting

The 2026-09-17 collection/confirmation closure adds five regression modules:
`test_v22_collection_continuation_regressions.py`,
`test_v22_completion_state_regressions.py`,
`test_v22_reply_contract_regressions.py`,
`test_v22_verified_confirmation_context.py`, and
`test_v22_confirmation_flow_regressions.py`. They cover typed collection
continuation, per-success suggestion consumption, question/count/expiry
safety, completed/deleted target no-ops, stable UID execution, truthful
ability/save assertions, actual confirmation facts, and scalar-delete scope
and current-entity priority. Current results and retained interrupted/failed
reports are maintained in `docs/current_status.md`; do not infer full-lane
success from focused assertions.

Four existing candidate fixtures (six cases) now use two genuinely partial
matching titles instead of an exact title plus its longer variant. Their
original selection, UID, tool-count, and final-state assertions remain.
The legacy `clarify_004` compatibility expectation now requires a closed
failure without pending for an unbound delete proposal, retaining its source
fixture, semantic-call checks, diagnostics, and lane classification.

The desktop assistant-reference fixture now presents an ordinary learning
suggestion rather than promising a future save without a real pending
operation. It still checks explicit later saving, extraction, UI refresh,
tool count, and cross-session isolation, with added assertions that no memory
was saved before the user's explicit request. This synchronizes the fixture
with the confirmation contract; it does not remove assistant-reference saving.

`test_qt_resource_lifecycle.py` adds four test-only lifecycle regressions.
The autouse cleanup runs before isolated desktop paths are restored, disposes
only windows created by the current test and their child resources, and keeps
the application and pre-existing windows intact. Running QThreads are reported
and their owners retained, never force-stopped; queued callback exceptions are
reported rather than swallowed. This fixes verified test resource retention
after `close()`, not a proven root cause of the full-process native crash.
The affected memory module and preceding Qt sequence remain in the complete
production run. Native crashes without complete XML are never counted as passes.

The 2026-09-16 memory/reference closure adds
`test_v22_typed_memory_and_suggestions.py` and
`test_v22_memory_reference_contract.py`. These cover typed name/preference/
habit/goal/project/current-state reads, fact existence and provenance,
retained-but-excluded temporary memories, final-answer-only usage counting,
same-timestamp identity authority, suggestion first/second selection, explicit
time overrides, duplicate confirmation provenance, quantity-vs-ordinal
contrasts, negated disclosure, and unverified action-log write claims.

The initial 16-case reference group reproduced `11 failed, 5 passed` before
the fixes. The initial full lanes exposed nine production and three
compatibility failures; the repaired incident/reference group then passed
37 cases without weakening business assertions. A later timed-choice fixture
seeds a typed pending operation directly: ordinary plan addition does not
require duration, and the two-round clarification budget is not extended.

Final 2026-09-16 production is `905 passed, 241 deselected`, exit 0;
memory/suggestion focus is `35 passed`; compatibility is
`151 passed, 995 deselected`; historical is `77 passed, 13 failed, 1056
deselected` with the same frozen failure identities as 2026-09-15. The first
frozen-code core-loop group was `134 passed, 1 deselected`; full compileall
and diff-check both exited 0. A subsequent audit
reproduced two wrong-target completions when explicit task titles contained
quantity/index wording. Those cases are now included in the reference group;
final evidence must come from the corrected run, not the prior full lane.
The corrected core-loop final-03 was `138 passed, 1 failed, 1 deselected`:
one isolated growth-log save reported `PermissionError`. The growth module
alone then passed 11 cases without code changes; the full group final-04
passed `139 passed, 1 deselected`. The failed report is retained; the exact
external permission-error cause is not confirmed. An earlier full-production
run aborted on a Qt access violation at `app.processEvents()`; it is not
counted as a pass. Final production reran the complete lane under offscreen
Qt without deleting the affected module and completed in 826.84 seconds.
See `docs/current_status.md` for the final record. XML reports live outside the
repository under `Roxyplan-test-runs/v22-*-20260916-final-*.xml`.

The preceding V2.2 closure evidence was collected on 2026-09-15 for this
working tree after replaying the latest user-reported failures:

- latest affected-component regression group: `94 passed`;
- desktop runtime plus Agent reliability group: `77 passed`;
- history/context regression group: `14 passed`;
- `production_contract`: `870 passed, 241 deselected`, exit 0;
- `compatibility_contract`: `151 passed, 960 deselected`, exit 0;
- `historical_baseline`: `77 passed, 13 failed, 1021 deselected`, exit 1;
- full-scope `compileall`: exit 0;
- `git diff --check`: exit 0 with LF/CRLF conversion warnings only.

All 2026-09-15 pytest runs used fresh external base directories and the
private-data guard remained clean. The 13 historical failures are frozen old contracts: three
model-driven duplicate/merge cases, six complex-update/previous-task-reference
cases, two old direct-memory-save cases, and two `opt_v2_006`/summary cases. They must
not be hidden with skip/xfail or mixed into the production result.

The unfiltered aggregate, agent-driven online-model semantic acceptance, and
agent-driven desktop UI acceptance were not run. The user supplied the latest
real dialogue and log evidence; the fixes were replayed with isolated
structured semantic fixtures. The existing online runner is DeepSeek-specific
and was not used as a substitute for the required existing OpenAI-provider
boundary. No provider integration was added and no network call was made.
`modules/llm_client.py`, private configuration, `memory.json`, and formal user
data were not modified.

Example lane runs (the wrapper creates a fresh isolated run directory for each command):

```powershell
.\scripts\run_isolated_tests.ps1 -m production_contract
.\scripts\run_isolated_tests.ps1 -m compatibility_contract
.\scripts\run_isolated_tests.ps1 -m historical_baseline
```

## Recommended Windows run

Use the isolated runner. It resolves the interpreter, disables the pytest cache
plugin, redirects `TEMP`/`TMP`, changes to the worktree root before pytest, and
creates a unique run under `test_workspace/runs/`:

```powershell
.\scripts\run_isolated_tests.ps1
.\scripts\run_isolated_tests.ps1 tests\test_data_isolation.py
.\scripts\run_isolated_tests.ps1 -KeepRun tests\test_data_isolation.py
```

Test paths are resolved against the worktree root, whatever directory the caller
stands in. Successful run directories are removed by default. `-KeepRun`
preserves a successful run for inspection; failed runs are always preserved, and
every run records `artifacts/python-executable.txt`, `artifacts/project-root.txt`
(the worktree the run actually tested), `artifacts/exit-code.txt`,
`artifacts/pytest-output.txt`, and `artifacts/junit.xml`. Do not pass a separate
`--basetemp`, reuse a stale run directory, or weaken assertions to work around
an isolation failure. pytest flags that collide with runner switch names must go
through `-PytestArgs @(...)`, for example `-PytestArgs @("tests/test_x.py", "-k",
"case_name")`: a bare `-k` binds to `-KeepRun`.

### Choosing the interpreter

By default the runner uses `<worktree>\.venv\Scripts\python.exe` and stops with
an actionable message when that file is missing. `-PythonExecutable` points the
same run at any existing interpreter, which is how a worktree without its own
`.venv` runs the suite:

```powershell
.\scripts\run_isolated_tests.ps1 -PythonExecutable "C:\path\to\python.exe" tests\test_data_isolation.py
.\scripts\run_isolated_tests.ps1 -PythonExecutable "C:\path\to\python.exe" -KeepRun tests\test_data_isolation.py
```

The selected interpreter supplies Python and the installed dependencies only.
The code under test, the test files, the run directory, and every isolated data
path still belong to the worktree that owns the script: the runner derives that
worktree from its own script path, changes to that root before pytest, and runs
`-m pytest`, so the usual import and collection come from the worktree under
test rather than from the caller's directory. That is the default behaviour, not
a hard boundary: changing directory cannot cancel `PYTHONPATH`, a site-wide
`sitecustomize`, an installed package with the same name, or any other
environment setting the caller already has. Keep the environment ordinary — the
runner does not attempt to sanitise it. The runner refuses an interpreter that
cannot import pytest, and records the interpreter plus the project root it
tested in `artifacts/python-executable.txt` and `artifacts/project-root.txt`.

Verified 2026-10-12 in a worktree without `.venv`, running that worktree's own
`scripts\run_isolated_tests.ps1` with `-PythonExecutable` pointing at an
existing interpreter:

- default invocation without `.venv`: stops before creating a run directory and
  names the missing path plus the `-PythonExecutable` form;
- `tests/test_data_isolation.py`: `8 passed`, exit 0, successful run cleaned;
- invocation from a different directory: same `8 passed` in the script's
  worktree, no pollution of the calling directory;
- a retained run records both halves of the pair: `artifacts/project-root.txt`
  names the worktree that was tested, `artifacts/python-executable.txt` the
  interpreter that ran it;
- failing selections retain the run with `artifacts/exit-code.txt` `4`
  (collection error) and `5` (no tests collected), and the caller observes the
  same code through `$LASTEXITCODE` in a session or as the process exit code
  when the script is started with `powershell -File`.

## Development-evidence regressions

The 2026-09-18 maintenance scope adds `test_development_log.py`,
`test_development_log_chat_integration.py`, `test_development_log_ui_sources.py`,
and `test_recent_interaction_inspector.py`. They use synthetic stores and logs:
repeated phrases, worker thread correlation, session switching, disabled and
injected loggers, reply-protection reasons, independent UI/backfill writes,
rotation, privacy filters, damaged tails, legacy evidence gaps, and read-only
inspection. A varying semantic/reply fixture demonstrates locating differing
model output without extra model calls; this is not an online model result.

These cases belong to the current production contract by the existing taxonomy;
no historical cases are moved or weakened. An actual Windows symlink-creation
test may be skipped when the account lacks permission; simulated path-rejection
checks still run. Latest lane counts, initial failed fixtures, reports and
protection checks are maintained only in `docs/current_status.md`. Runtime
behavior and diagnostic access are documented in `docs/development_logging.md`.
