# Modules

This directory is reserved for feature modules.

## Purpose

`modules/` is the future home for feature-specific logic that should remain reusable and isolated from app entry points.

Keeping features here will make V1, V2, and V3 easier to extend without turning the backend or frontend into a mixed pile of unrelated responsibilities.

## Planned Modules

- `chat`: text conversation flow
- `memory`: long-term memory management
- `voice`: STT and TTS capabilities
- `knowledge`: document ingestion and knowledge feeding

## Current Status

No module business code has been added.

Do not add chat, memory, voice, or knowledge ingestion implementation during the documentation-only stage.
