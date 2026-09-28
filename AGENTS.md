# RoxyPlan Agent Notes

RoxyPlan is a long-term growth companion AI desktop pet.

Chinese name: 洛琪希计划.

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
- Do not commit or push.

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

## Documentation Rules

- Documentation changes are allowed when they keep project status accurate.
- Directory README files should explain intent, not implementation.
- Roadmap documents may describe future features but should not include runnable code.
- Keep requirements conservative until dependencies are intentionally selected.

## Current Working Contracts

Last synchronized: 2026-09-27.

- After reading this file, read `docs/capability_boundaries.md` for supported user interaction and `docs/project_workflow.md` for collaboration, diagnosis, privacy, and verification workflow. Refresh `docs/current_status.md` and relevant Git state before presenting current results.
- For Agent architecture, Tool, Memory, Context, state, reliability, evaluation, or testing-method work, read `docs/agent_engineering_experience.md` as a historical experience source. It does not override current code, capability contracts, or dated status.
- Keep hard boundaries here, product interaction contracts in `capability_boundaries.md`, working practices in `project_workflow.md`, and dated verification results in `current_status.md`. Old handovers are historical evidence, not authority over newer explicit user decisions.
- Fix clear bugs within supported capabilities; use complete natural-language guidance and bounded clarification for ambiguous input. User guidance never excuses wrong writes, false success, confirmation loops, or silently removing promised numbered/list-reference behavior.
- Do not duplicate runtime schema or visibility lists in documentation. CapabilityRegistry owns execution policy/visibility; ToolRegistry owns parameter schema, aliases, and handlers. Record any gap between confirmed commitments and implementation rather than silently shrinking commitments.
- Keep project workflow guidance separate from product `memory.json` and private user records. Do not record personal conversation contents, identity secrets, or credentials in these contracts.
- Do not operate the desktop unless the user explicitly asks for UI acceptance. Temporary diagnostics permissions are scoped, not permanent settings authorization. Do not stage, commit, push, or clean the worktree without explicit authorization.

## Future Work Authority

Use `docs/ROADMAP.md` for planned product direction and `docs/current_status.md` for dated implementation state. Do not copy a second implementation sequence into this file. A roadmap item is not implementation authorization; new product features still require explicit user permission and incremental verification.
