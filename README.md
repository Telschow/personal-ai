# Personal AI

A local-first personal knowledge and agent platform. It ingests your own documents into SQLite, retrieves evidence with provenance, and lets a local LLM use tools only inside a policy boundary that requires human approval for risky actions.

[![CI](https://github.com/Telschow/personal-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Telschow/personal-ai/actions/workflows/ci.yml)

## In 60 seconds

* **Ingest:** explicit, deterministic ingestion of documents, ChatGPT and Gemini exports, email, browser history and more into a local SQLite database. Content hashes make re-imports idempotent.
* **Retrieve:** FTS5 keyword search (BM25), optional embeddings through Ollama, and hybrid ranking with Reciprocal Rank Fusion. Every hit carries chunk id, document id and content hash.
* **Remember:** governed memory with scope, confidence and provenance. Memories move from candidate to active to archive, and a deterministic reconciler decides between adding evidence, superseding and flagging a conflict.
* **Act, with limits:** a tool-calling agent loop capped at 8 rounds with 12,000-character tool results. A `PolicyEngine` answers ALLOWED, DENIED or APPROVAL_REQUIRED for every tool call. Enforcement is in code, not in the prompt.
* **Serve:** CLI, an OpenAI-compatible HTTP API, and a read-only MCP bridge.

No cloud service is needed for core operation. Ollama is the only model provider and sits behind one client module.

## See it in action

![The eight stages of the personal-ai pipeline: synthetic private input flows through ingestion, BM25 retrieval, evidence and provenance, local reasoning, and then meets a policy boundary that stops at human approval, leaving zero actions performed.](docs/architecture/demo-snapshot.png)

A deterministic, offline demo runs this repository's own ingestion, retrieval, provenance and policy code against a fixed synthetic corpus, then stops at the human approval boundary.

> Governance demo GIF (30 s): placeholder. Planned content: the agent proposes a network action, the policy blocks it, a human approves, the audit log records the decision.

### The eight stages

| # | Stage | What actually happens |
|---|-------|----------------------|
| 1 | Private input | Two fixed synthetic documents enter the process. Nothing is read from disk and no external service is contacted. |
| 2 | Ingest | `DocumentIngestor` canonicalises, classifies both documents as `text_heavy`, and produces 2 deterministic chunks. |
| 3 | Retrieve | The keyword backend (`SQLiteChunkIndex`, FTS5 native BM25) returns 2 ranked hits. No embedding model is involved. |
| 4 | Evidence | Each hit resolves to a chunk id, document id, source key, and content hash. Provenance is complete for every hit. |
| 5 | Local reasoning | A structured summary is assembled deterministically from the retrieval outcome. No language model is called, so there is no chain-of-thought to show. |
| 6 | Policy | `PolicyEngine` evaluates the proposed outbound action and returns `approval_required` for `Permission.network`. |
| 7 | Human approval | `PolicyEngine.execute` raises `ApprovalRequiredError`; the handler never runs. The plan state machine also refuses `needs_approval` to `completed`. |
| 8 | Bounded action | Zero actions are performed. The run ends here on purpose. |

Run it and inspect the output:

```bash
uv run python -m personal_ai.demo              # writes artifacts/demo/results.json and metadata.json
uv run python scripts/render_demo_snapshot.py  # optional: artifacts/demo/snapshot.png
```

[`docs/architecture/demo.html`](docs/architecture/demo.html) is a single self-contained file with no external fonts, scripts or CDN requests, so it opens straight from disk. The run writes `artifacts/demo/results.json` (what happened) and `artifacts/demo/metadata.json` (provenance of the run). `artifacts/` is gitignored.

### What this demo is not

* **It is not a benchmark.** It shows architecture. No accuracy, latency, recall or throughput number is measured, claimed or implied. The BM25 ranks in `results.json` are raw ranking values from this run, not a quality score.
* **It says nothing about model quality.** No language model is called. The reasoning stage is a deterministic summary of retrieved evidence.
* **It is not autonomous.** The system performs no action without explicit human approval. Steps 7 and 8 exist to prove that, and `tests/test_demo.py` fails if the boundary ever stops holding.
* **It contains no real data.** No personal documents, email, finances or profiles. Addresses use the reserved `example.invalid` domain.
* **It is not a production-readiness claim.** It is a narrow, synthetic illustration of one path through the code.

### Determinism and privacy

* **Synthetic only.** Every corpus string carries a `SYNTHETIC-DEMO-DATA` marker, asserted by the test suite.
* **Offline.** No network call, no LLM and no local model. Tests run the demo with `socket` and the Ollama client disabled, and fail if the demo package gains a network or model-client import.
* **Deterministic.** Fixed corpus and fixed timestamps, no wall-clock reads, BM25 ties broken on chunk id. Both JSON artifacts are byte-identical across runs and machines.
* **No hidden reasoning.** Only user-facing structured metadata and the chunk identifiers it cited are recorded.

## Features

| Area | What it does | Where |
|------|--------------|-------|
| Ingestion | Sources: files, ChatGPT, Gemini, email, Chrome history, YouTube history, finance, events, vision | `src/personal_ai/sources/`, `docs/ingestion.md` |
| Retrieval | BM25, semantic and hybrid RRF, plus an evaluation harness (recall@k, precision@k, hit@k, MRR) | `docs/retrieval.md`, `src/personal_ai/retrieval_evaluation.py` |
| Memory | Scoped, confidence-weighted, reconciled, with a human review queue | `docs/memory.md` |
| Governance | Permission-gated tools, approvals, event-sourced execution | `docs/agents.md`, `docs/CONTROL_PLANE.md` |
| Interfaces | CLI, OpenAI-compatible API, read-only MCP bridge | `src/personal_ai/cli.py`, `server.py`, `mcp_server.py` |

Architecture overview: `docs/architecture.md`. Threat model: `docs/threat-model.md`.

## Setup

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Telschow/personal-ai
cd personal-ai
uv sync --locked --dev
uv run pytest -q
```

Quality gates, as run in CI:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/mypy_ratchet.py
```

On Python 3.12 the suite reports `3094 passed, 4 skipped`. It is hermetic: temporary SQLite databases, fakes, no network.

To run against a local model, install [Ollama](https://ollama.com), copy `.env.example` to `.env` and set `OLLAMA_BASE_URL` and the model names. Never commit `.env`. See `docs/configuration.md`.

```bash
uv run python -m personal_ai.cli --help      # ingest, search, agent
uv run python -m personal_ai.server --help   # OpenAI-compatible API
```

A Docker image and compose file are in `docker/`.

## Limits

* There are no published retrieval or answer-quality numbers yet. The retrieval evaluation harness exists; a measured benchmark over a synthetic question set is the next milestone in `docs/roadmap.md`.
* Answer quality depends on the local model and hardware.
* Vector search is optional and needs an embedding model.
* Ingestion is explicit. Nothing watches your directories.
* Memory does not expire automatically. Curation is manual.
* There is no encryption at rest. Use disk encryption and file permissions.
* Single user, single machine. There is no multi-tenant isolation.

## How this was built

This project was built with AI coding assistants (OpenCode with the skills in `.opencode/`, and Claude Code). The assistants wrote much of the code and tests. The constraints they worked under are the author's and are written down in `AGENTS.md`: local-first, Ollama only at the infrastructure boundary, explicit tools with no `eval`, shell or dynamic imports, a filesystem sandbox as a hard boundary, synthetic data only in the repository, and no action without policy approval. The test suite, the privacy regression test and CI are the checks that enforce those constraints.

## Roadmap, security, contributing

* Roadmap with exit tests: `docs/roadmap.md`
* Reporting a vulnerability: `SECURITY.md`
* Data rules and contribution guide: `PUBLIC_DATA_POLICY.md`, `CONTRIBUTING.md`

Released under the license in `LICENSE`.
