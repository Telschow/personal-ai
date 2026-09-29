# Models

## Overview

Personal AI uses local models via Ollama for optional LLM features.

## Provider

* Primary provider: Ollama HTTP API (default: http://127.0.0.1:11434)
* Client: src/personal_ai/ollama_client.py
* No cloud LLM dependencies in core code

## Configuration

Environment variables in .env:
* OLLAMA_BASE_URL
* PERSONAL_AI_CHAT_MODEL (default: qwen3.5:9b)
* PERSONAL_AI_EMBEDDING_MODEL (optional)
* PERSONAL_AI_VISION_MODEL (optional)

## Capabilities

ModelRouter maps capability + complexity to model:
* SIMPLE: classification, small decisions
* RESEARCH: general-purpose research
* REASONING: complex reasoning
* VISION: image understanding
* CODING: software engineering
* VERIFICATION: independent review

## Safety

* Think disabled to prevent reasoning token leakage
* Strict JSON schema validation for structured outputs
* Evidence ID allow-list
* Anti-fabrication guards
* Fail-closed on validation failure

## Deterministic Core

* Keyword search FTS5 works without LLM
* Memory search works without LLM
* Deterministic scoring works without LLM
* Ingestion works without LLM

## Optional LLM Paths

* Vector search requires embedding model
* Vision extraction uses vision model
* Structured extraction uses chat model
* LLM reasoning for synthesis
* CV tailoring semantic refinement optional

## Local-First

All model inference runs on your machine via local Ollama instance. No personal data leaves your hardware unless you explicitly configure external model providers.