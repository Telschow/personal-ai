# Security

## Reporting

Report security issues via GitHub Security Advisory or email maintainer.

## Measures

* Policy engine gates tool execution
* Input validation on ingestion paths
* No code execution during ingestion
* Deterministic fallback on LLM failures
* No secrets in source code
* Local-first architecture

## Threat model

Local trusted zone: your machine, local SQLite, Ollama.
External: job providers, search engines (opt-in).
LLM: local Ollama by default.

No private data leaves machine unless explicitly shared.