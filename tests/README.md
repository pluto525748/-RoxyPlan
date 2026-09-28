# Tests

RoxyPlan tests cover the desktop client, Local Web adapter, Agent pipeline,
memory and growth repositories, chat history, client actions, and Chinese NLU
benchmarks.

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

Example lane runs (use a fresh external base directory for each command):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  -m production_contract --basetemp 'C:\Temp\roxyplan-production'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  -m compatibility_contract --basetemp 'C:\Temp\roxyplan-compatibility'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  -m historical_baseline --basetemp 'C:\Temp\roxyplan-historical'
```

## Recommended Windows run

Use the project virtual environment, disable the cache plugin, and place each
pytest base directory outside the repository:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider `
  --basetemp 'C:\path\outside\Roxyplan\pytest-run'
```

Do not reuse a stale system pytest temporary directory, and do not weaken
assertions to work around an isolation failure.

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
