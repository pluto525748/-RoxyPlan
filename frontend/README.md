# Frontend

This directory contains the RoxyPlan desktop pet frontend prototype.

## Current Version

V0.6 implements the smallest interactive desktop pet chat prototype with local memory, configurable rule personality, txt knowledge scanning, and configurable LLM API access:

- Desktop pet window
- Default avatar drawing
- Drag support
- Always-on-top window
- Double-click to open a chat window
- Chat record display area
- Text input box
- Send button
- Enter key sending
- Automatic scroll to bottom
- Timestamp display
- Fixed local Roxy reply
- Reads `memory.json` when the app starts
- Saves the user's first normal input as nickname
- Supports `记住：xxxx` to save long-term memory
- Reads `data/roxy_personality.json` when the app starts
- Supports rule-based personality replies without AI
- Supports `查看人格` to display the current personality configuration
- Scans `data/knowledge/` for `.txt` files when the app starts
- Supports `查看知识` to display loaded txt knowledge file names
- Reads root `config.json` for OpenAI-compatible LLM API access
- Sends ordinary chat messages to the configured LLM with personality and memory context

## Not Included

- No database integration
- No voice integration
- No knowledge base integration
