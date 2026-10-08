# Architecture

Personal AI is a single-user, local-first platform. Everything runs on one machine against SQLite and a local Ollama server. External model providers are not part of the core.

## Layers

```
sources/ (explicit ingestion)  ->  documents/ (canonicalise, chunk, classify)
        ->  storage/ (SQLite, FTS5, optional embeddings)
        ->  retrieval (BM25, semantic, hybrid RRF) + memory/ + people/
        ->  agents/ PolicyEngine  ->  tools/ registry
        ->  agent loop (agent.py)  ->  CLI, OpenAI-compatible API (server.py), MCP bridge (mcp_server.py)
```

| Layer | Package | Responsibility |
| --- | --- | --- |
| Ingestion | `sources/`, `documents/`, `ingestion.py` | Explicit, deterministic ingestion. Content-hash identities make re-import idempotent. |
| Storage | `storage/` | SQLite in WAL mode with foreign keys. FTS5 virtual tables for keyword search. Embeddings are optional. |
| Retrieval | `retrieval.py`, `hybrid_index.py`, `semantic_index.py` | Keyword (BM25), semantic and hybrid retrieval with Reciprocal Rank Fusion and deterministic tie-breaking. See `docs/retrieval.md`. |
| Memory | `memory/` | Scoped, confidence-weighted memories with provenance. Lifecycle is candidate, active, archive, with deterministic reconciliation. See `docs/memory.md`. |
| Governance | `agents/policy.py` | `PolicyEngine` returns ALLOWED, DENIED or APPROVAL_REQUIRED for every tool call. Enforced in code, not by model obedience. |
| Execution | `execution/` | Planner, executor and verifier with an event-sourced audit trail and a control plane. See `docs/CONTROL_PLANE.md`. |
| Interfaces | `cli.py`, `server.py`, `mcp_server.py` | CLI, OpenAI-compatible HTTP API, and a read-only MCP bridge (stdio) exposing six read tools. |

## Deterministic and probabilistic parts

Deterministic: ingestion, keyword search, memory search and reconciliation, policy decisions, orchestration, retrieval planning. These work without an LLM.

Probabilistic and optional: LLM answer synthesis, structured extraction, vision extraction, and semantic search (needs an embedding model).

## Model provider

Ollama over HTTP (default `http://127.0.0.1:11434`), isolated in `ollama_client.py`. The core has no cloud LLM dependency. Model names come from configuration (see `docs/configuration.md`). Generation runs with `think` disabled so reasoning tokens do not leak, outputs are validated against a strict schema, and only evidence IDs present in the context may be cited. On validation failure the system falls back to deterministic output.

## Trust boundaries

| Zone | Trust |
| --- | --- |
| Local machine, SQLite, Ollama | Trusted |
| Filesystem adapter | Sandboxed to the configured workspace |
| Retrieved content, memories, tool arguments | Untrusted data, never instructions |
| MCP bridge | Read-only, same authorization path as chat |

Controls: explicit ingestion paths, scope-aware retrieval, identifiers-only event logs, content-hash idempotency, bounded agent loop (`max_tool_rounds = 8`, tool results truncated at 12,000 characters), and human approval for permissions such as `network`. The threat analysis is in `docs/threat-model.md`.

## Known risks

* No encryption at rest. Use filesystem permissions and disk encryption.
* Ingested browser history can contain tokens and keys.
* Memories can hold sensitive profile data. There is no automatic expiry.
* Tool-argument prompt injection is mitigated by the policy layer but not eliminated.
