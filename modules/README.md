# Modules

This directory contains reusable feature modules.

## Current Modules

- `llm_client.py`: OpenAI-compatible chat completions client.

## LLM Client

The LLM client reads connection settings from the root `config.json` file.

Supported configuration fields:

- `base_url`
- `api_key`
- `model`

The client does not hardcode a model or provider. It can be used with OpenAI-compatible services such as OpenAI, DeepSeek, or SiliconFlow when configured with the correct endpoint and model.
