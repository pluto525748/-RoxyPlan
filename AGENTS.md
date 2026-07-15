# RoxyPlan Agent Notes

RoxyPlan is a long-term growth companion AI desktop pet.

Chinese name: 洛琪希计划.

## Current Stage

The project is in V0.9 prototype implementation.

Existing prototype areas include the PySide6 desktop pet, chat window, settings dialog, local memory, local knowledge reading, basic state actions, today's plan and review loop, and local LLM calling experiments.

Do not add new product features without explicit user permission. Maintenance, documentation synchronization, tests, bug fixes, and small compatibility improvements are allowed when they protect existing behavior.

## Hard Boundaries

- Do not write FastAPI code.
- Do not write database implementation code.
- Do not add new AI provider integration code without explicit permission.
- Do not add voice implementation code.
- Do not add complex document parsing implementation code.
- Do not modify `modules/llm_client.py`, `memory.json`, or private local config files unless the user explicitly asks for that exact change.
- Preserve existing desktop pet actions, chat behavior, settings, and local data compatibility.

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

## Future Implementation Order

1. Confirm product and architecture documents.
2. Design V1 data model and interaction flow.
3. Choose backend and frontend technology.
4. Implement V1 in small, testable steps.
5. Add V2 voice capabilities after V1 is stable.
6. Add V3 knowledge feeding after storage and memory rules are clear.
