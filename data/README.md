# Data

This directory is reserved for local runtime data.

## Purpose

`data/` contains lightweight local runtime data and configuration used by the current prototype.

This directory should be treated carefully because it may eventually contain private conversation history, memory files, uploaded documents, and generated caches.

## Current Contents

- `pet_config.json`：desktop pet settings
- `pet_tips.json`：local bubble tips
- `roxy_personality.json`：local personality rules
- `knowledge/`：local `.txt` / `.md` knowledge files
- `private/`：private daily plans, action records, and saved reviews

## Privacy Notes

Do not commit private user data.

Actual files placed under `data/knowledge/` are ignored by default, except `.gitkeep` and `README.md`.

`private/` and legacy growth JSON files are ignored and should remain local because they may contain private plans, actions, and reviews.

Do not add database files, generated caches, API keys, private notes, or chat exports.
