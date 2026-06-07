# RoxyPlan Architecture

## Overview

RoxyPlan is split into a frontend desktop pet interface, a backend service layer, reusable feature modules, and local data storage.

## Boundaries

- `frontend/`: visual desktop pet, chat window, and user interactions
- `backend/`: APIs, persistence coordination, and service entry points
- `modules/`: isolated feature areas such as chat, memory, voice, and knowledge ingestion
- `data/`: local runtime data and user-owned storage
- `docs/`: product and architecture documentation
- `tests/`: validation for each feature area

## Data Plan

- SQLite stores chat history and structured conversation records.
- `memory.json` stores long-term companion memory.
- Future knowledge files can be staged under `data/` before parsing.

## Implementation Rule

This document describes structure only. No business logic has been implemented yet.
