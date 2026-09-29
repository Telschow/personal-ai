# Model Architecture

## Provider
Ollama local HTTP API default http://127.0.0.1:11434
Client: personal_ai/ollama_client.py
No cloud LLM dependencies in core

## Configuration
CHAT_MODEL_ENV default qwen3.5:9b
EMBEDDING_MODEL_ENV optional
VISION_MODEL_ENV optional
OLLAMA_BASE_URL_ENV override for Docker

## Routing
ModelRouter maps capability + complexity -> model
Capabilities: SIMPLE, RESEARCH, REASONING, VISION, CODING, VERIFICATION
OllamaProvider records configured model per capability

## Safety
Think false to prevent reasoning token leakage
Strict JSON schema validation
Evidence ID allow-list
Anti-fabrication guards
Fail-closed on validation failure

## Context
System instructions 15-20%
Query 5-10%
Evidence 60-70%
Format 5-10%
Margin 0-5%

## Deterministic Core
Keyword search FTS5 works without LLM
Memory search works without LLM
Deterministic scoring works without LLM
Ingestion works without LLM

## Optional LLM Paths
Vector search requires embedding model
Vision extraction requires vision model
Structured extraction uses chat model
LLM reasoning for synthesis
CV tailoring semantic refinement optional
