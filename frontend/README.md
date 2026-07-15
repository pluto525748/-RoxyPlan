# Frontend

This directory contains the RoxyPlan desktop pet frontend prototype.

## Current Version

V0.9 implements a lightweight desktop pet chat prototype with local memory, today's plan and review commands, configurable rule personality, local `.txt` / `.md` knowledge reading, settings access, state actions, and configurable LLM API access:

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
- Rule-based local Roxy replies for configured trigger phrases
- Reads `memory.json` when the app starts
- Creates local `memory.json` from `memory.example.json` when missing
- Saves the user's first normal input as nickname
- Supports `记住：xxxx` to save long-term memory
- Supports `我的记忆` and `忘记：xxxx` to inspect or delete saved memory
- Reads `data/roxy_personality.json` when the app starts
- Supports rule-based personality replies without AI
- Supports `查看人格` to display the current personality configuration
- Reads `data/knowledge/` `.txt` and `.md` files when the app starts
- Uses light keyword matching to add relevant knowledge snippets to the LLM prompt
- Supports `查看知识` to display loaded local knowledge file names
- Reads root `config.json` for OpenAI-compatible LLM API access
- Sends ordinary chat messages to the configured LLM with personality and memory context
- Keeps LLM calls on a worker thread so thinking animation can continue
- Provides a settings dialog entry from the chat window
- Supports plan, action record, daily review, and growth log chat commands
- Provides a growth panel for plans, actions, reviews, and recent growth logs
- Provides a desktop pet right-click menu for chat, growth, settings, dance, encouragement, sleep, wake, and quit
- Includes eight generated transparent dance frames and a multi-frame playback path for `assets/pet/dance/dance_*.png`

## Not Included

- No database integration
- No voice integration
- No vector database or complex document parser
