# RoxyPlan

RoxyPlan is a long-term growth companion AI desktop pet.

Chinese name: 洛琪希计划.

## Positioning

RoxyPlan is designed to be a companion that stays with the user for a long time, remembers useful context, and grows through conversation, voice interaction, and user-fed knowledge.

The project root should remain stable as `RoxyPlan`. Product versions, feature stages, and platform variants should be represented inside documents, modules, branches, or release tags instead of being added to the root directory name.

## Current Status

This repository is currently a documentation-only project skeleton.

No business code has been added.

Do not add framework implementations, desktop pet behavior, database logic, or AI integration until the implementation phase starts.

## Roadmap

### V1: Desktop Pet and Text Chat

- Desktop pet avatar
- Click to open chat window
- SQLite chat history
- `memory.json` long-term memory

### V2: Voice Interaction

- Voice chat
- TTS voice playback
- STT voice recognition

### V3: Knowledge Feeding

- Knowledge feeding system
- PDF support
- Word support
- PPT support
- TXT support

## Project Structure

- `backend/`: backend boundary for future services, APIs, persistence coordination, and memory coordination
- `frontend/`: frontend boundary for the future desktop pet avatar, chat window, and interaction UI
- `modules/`: reusable feature module boundary for chat, memory, voice, and knowledge ingestion
- `data/`: local runtime data boundary for memory files, SQLite files, uploads, and parsed knowledge cache
- `docs/`: planning, architecture, roadmap, memory, and product documents
- `tests/`: future validation boundary for backend, frontend, modules, memory, and knowledge ingestion

## Key Files

- `memory.json`: initial long-term memory skeleton
- `requirements.txt`: dependency placeholder for future Python work
- `AGENTS.md`: collaboration rules and project boundaries for coding agents
- `.gitignore`: ignores common local, runtime, and generated files

## Documentation

- `docs/ROADMAP.md`: staged product plan
- `docs/ARCHITECTURE.md`: current architecture boundaries
- `docs/MEMORY.md`: long-term memory planning notes

## Implementation Guardrails

- Keep the root directory name as `RoxyPlan`.
- Keep version and platform names out of the root directory name.
- Do not add business code during documentation-only planning.
- Do not introduce backend frameworks until backend implementation begins.
- Do not implement desktop pet behavior until frontend implementation begins.
- Do not implement database logic until persistence design is approved.
