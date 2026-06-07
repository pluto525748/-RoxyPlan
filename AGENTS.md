# RoxyPlan Agent Notes

RoxyPlan is a long-term growth companion AI desktop pet.

Chinese name: 洛琪希计划.

## Current Stage

The project is in skeleton and documentation planning.

Do not add business logic until the implementation phase starts.

## Hard Boundaries

- Do not write FastAPI code.
- Do not write desktop pet implementation code.
- Do not write database implementation code.
- Do not add AI provider integration code.
- Do not add voice implementation code.
- Do not add document parsing implementation code.

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

- Documentation changes are allowed in the current stage.
- Directory README files should explain intent, not implementation.
- Roadmap documents may describe future features but should not include runnable code.
- Keep requirements as placeholders until dependencies are intentionally selected.

## Future Implementation Order

1. Confirm product and architecture documents.
2. Design V1 data model and interaction flow.
3. Choose backend and frontend technology.
4. Implement V1 in small, testable steps.
5. Add V2 voice capabilities after V1 is stable.
6. Add V3 knowledge feeding after storage and memory rules are clear.
