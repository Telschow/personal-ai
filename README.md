# Personal AI

A **local-first personal knowledge and agent system**. It ingests your own
files, conversations, exports and activity into a single SQLite knowledge base
on your machine, and answers questions about them through a local `qwen3.5:9b`
model run by Ollama. No cloud APIs, no data leaving your hardware.

The project is a running, single-user system today: an agent CLI, an
OpenAI-compatible HTTP gateway ([Open WebUI](https://openwebui.ai) can use it),
durable memory, a permission-gated execution control plane, and a workout
dataset — all backed by local SQLite and a local vision/chat model.

> This repository contains **no personal data**. Private exports, databases and
> workspaces are staged outside the repo (see [Privacy](#-privacy-and-local-first-guarantees)).

---

## Table of contents

- [What it is](#what-it-is)
- [Current architecture](#current-architecture)
- [Deployment topology (Open WebUI + Tailscale + Docker + Ollama)](#deployment-topology)
- [Getting started](#getting-started)
- [Configure Open WebUI](#configure-open-webui)
- [Current model and API endpoint](#current-model-and-api-endpoint)
- [Personal-data capabilities](#personal-data-capabilities)
- [Memory security model](#memory-security-model)
- [Workout support](#workout-support)
- [Privacy and local-first guarantees](#privacy-and-local-first-guarantees)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Development and tests](#development-and-tests)
- [Documentation](#documentation)

---

## What it is

- **Local-first.** Everything runs on your machine: the model backend is a
  local Ollama instance (`qwen3.5:9b`), and all extraction/retrieval is
  deterministic Python + that local model.
- **Privacy-oriented.** Personal content is never sent to a cloud service,
  never committed, and never dumped into logs. Tests use synthetic fixtures.
- **An agent, not just a search engine.** A synchronous agent loop lets the
  model choose from an explicit allow-listed set of tools
  (`search_knowledge`, `search_documents`, `query_events`,
  `search_workouts`, ...) to answer from your stored records.
- **Structured by design.** Documents, chunks, extractions, embeddings,
  memories, workouts and executions are distinct typed models with stable
  (content-hash / deterministic) identities — re-ingestion never duplicates.
- **Extensible by source.** A common document abstraction with
  source-specific adapters instead of separate per-format pipelines.

### What it is NOT

- **Not** a general-purpose autonomous agent that can be pointed at arbitrary
  tasks. Only registered, permission-checked tools execute; no model-generated
  code ever runs.
- **Not** a cloud AI client. All model calls go to local Ollama.
- **Not** a vector-database-first RAG product. Retrieval today is SQLite FTS5
  keyword search plus deterministic lexical ranking; embeddings/vision are
  optional configured backends (see [Known limitations](#known-limitations)).
- **Not** a multi-user or distributed service. Single user, single machine.

---

## Current architecture

```
                    ┌───────────────┐        ┌────────────────────────┐
                    │  CLI agent    │        │  HTTP gateway (server) │
                    │ python -m     │        │  python -m             │
                    │ personal_ai   │        │  personal_ai.server     │
                    │               │        │  /v1 + /api/*          │
                    └──────┬────────┘        └───────────┬────────────┘
                           │                            │ OpenAI-compatible
                           └──────────── May run inside ┤  (Open WebUI / curl)
                                        a Docker (Phase 45) │
                                                            ▼
                                                    Agent + ToolRegistry
                                                            │
                    ┌───────────────┬──────────────────────┼─────────────────┐
                    ▼               ▼                      ▼                 ▼
              Filesystem      Retrieval                Memory           Control plane
              (sandboxed       services                (Phase 40-42)   (Phase 39)
               workspace)      └── search_documents     └── explicit     └── executions,
                               └── search_knowledge         memories,        approvals,
                               └── query_events             chat recall      events, board
                    │               │                      │                 │
                    └───────────────┴──────────────────────┴─────────────────┘
                                                    │
                                                    ▼
                                       Single SQLite database
                                       (documents, chunks, embeddings,
                                        memories, workouts, executions)
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                              Text model (Ollama)            Vision model (Ollama,
                              + local parser/classifier       optional, Phase 34a)
```

Three ways to drive the same Agent + ToolRegistry:

- **Agent REPL / one-shot prompt** — `python -m personal_ai.cli [--workspace
  DIR] [--database FILE] "your question"`, with `--search QUERY` for a text
  search and `--ingest SOURCE PATH` for ingestion.
- **OpenAI-compatible gateway** — `python -m personal_ai.server
  --workspace DIR --database FILE`, served as model id `personal-ai`.
- **Dockerized gateway** (Phase 45) — the same server, containerized, on the
  Open WebUI Docker network (no host port).

### Data model

Distinct typed concepts, backed by one SQLite file (plus the sandboxed
workspace on disk):

| Concept | Meaning |
| --- | --- |
| **Document** | What was ingested — source, path, mime type, times, **content hash** (stable id). |
| **Chunk** | A searchable portion of a document (text, page number, metadata). |
| **Extraction** | What the model understood: summary, people, projects, goals, tags. |
| **Embedding** | Optional vector per chunk (Ollama embedding model). |
| **Memory** | Durable knowledge about the user/system: `fact`, `preference`, `decision`, `project_context`, `entity`, `summary`, `instruction`, with scope + lifecycle. |
| **Workout** | Normalized exercise session (`wkt-` ids, idempotent re-import). |
| **Execution / Plan / Task** | Resumable units of work with event streams and approvals. |

---

## Deployment topology

The reference deployment uses Open WebUI (Docker) + Tailscale + Ollama
(Docker) + this repo's gateway:

```
your browser / phone
      │  https://<your-openwebui>.ts.net:8443   (Open WebUI via Tailscale)
      ▼
open-webui  (Docker, network: open-webui_default)
      │  OpenAI-compatible connection  →  http://personal-ai:8000/v1
      ▼
personal-ai  (this repo, Docker Compose, network: open-webui_default
              + ollama_default; NO host ports exposed)
      │  http://ollama:11434
      ▼
ollama  (Docker, network: ollama_default, model qwen3.5:9b)
```

The gateway joins **two external Docker networks** — Open WebUI's network so
Open WebUI can reach it, and Ollama's network so it can reach Ollama. Every hop
is on container bridge networks; the host firewall (`FORWARD` policy DROP)
is never opened, and **port 8000 is never published to the host or LAN**.

Open WebUI is reached over Tailscale; the gateway itself is only ever at
`personal-ai` inside Docker, never at a host IP. See
[`docs/DOCKER.md`](docs/DOCKER.md) for the full rationale.

---

## Getting started

Prerequisites: Python ≥ 3.14, [`uv`](https://docs.astral.sh/uv/), a local
[Ollama](https://ollama.com) instance with the chat model pulled.

```bash
uv sync

# 1) ingest your own supported files / exports into a knowledge DB
uv run python -m personal_ai.cli \
  --ingest file /path/to/files \
  --database /path/to/knowledge.db

# 2) ask questions through the agent
uv run python -m personal_ai.cli \
  --workspace /path/to/workspace \
  --database /path/to/knowledge.db \
  "What do I know about project X?"

# 3) or serve the OpenAI-compatible gateway (binds 127.0.0.1:8000 by default)
uv run python -m personal_ai.server \
  --workspace /path/to/workspace \
  --database /path/to/knowledge.db
```

### Dockerized stack

```bash
docker compose -f docker/docker-compose.yml up -d --build

# verify the exact Open WebUI hop
docker exec open-webui curl -sS http://personal-ai:8000/v1/models
# → {"object":"list","data":[{"id":"personal-ai", ...}]}
```

The compose file mounts `./data` into the container as `/data` (SQLite
database + workspace). Set `OLLAMA_BASE_URL`, `PERSONAL_AI_CHAT_MODEL`,
`PERSONAL_AI_DATABASE`, `PERSONAL_AI_WORKSPACE` as needed. The compose file
references the external `open-webui_default` / `ollama_default` networks;
rename them at the bottom of the file for a different setup.
See [`docs/DOCKER.md`](docs/DOCKER.md).

---

## Configure Open WebUI

1. Open WebUI → **Settings → Connections → OpenAI**.
2. **API base URL**: `http://personal-ai:8000/v1` when the gateway is
   containerized on Open WebUI's network; `http://127.0.0.1:8000/v1` when the
   gateway runs on the host.
3. **API key**: blank, unless `PERSONAL_AI_API_TOKEN` is set (then use that).
4. **Model id**: `personal-ai` (the gateway ignores the requested model name
   and always serves this one id).

See [`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md) for background and the streaming
(SSE) details.

---

## Current model and API endpoint

- **Chat model:** `qwen3.5:9b` (fixed deployment choice; configurable via
  `PERSONAL_AI_CHAT_MODEL`).
- **Model endpoint:** `OLLAMA_BASE_URL`, default `http://localhost:11434`
  (set to `http://ollama:11434` in Docker).
- **Gateway:** `http://<host>:8000/v1` with served model id `personal-ai`;
  non-streaming and SSE streaming chat, plus `/v1/models`.
- **Vision model:** optional, disabled unless `PERSONAL_AI_VISION_MODEL` is
  set (image-heavy ingestion only, Phase 34a).
- **Embedding model:** optional, configured via
  `personal_ai.config.embedding` env (used when embeddings are enabled);
  retrieval does not require it.

---

## Personal-data capabilities

### Sources (auto-detected adapters)

| Source | CLI type | Notes |
| --- | --- | --- |
| Local files | `file` | `.txt`, `.md`, `.pdf`, `.png`, `.jpg`, `.jpeg` (recursive, sandboxed) |
| Google Takeout email | `email` | mbox structure; also Thunderbird mailboxes; fully local, model-free |
| Financial exports | `financial` | deterministic, structured, model-free (Phase 37) |
| ChatGPT exports | `chatgpt` | conversation export adapter |
| Gemini exports | `gemini` | conversation export adapter |
| Google Keep | `google_keep` | notes |
| NotebookLM | `notebooklm` | article/source exports |
| Chrome history | `chrome_history` | browsing activity |
| YouTube history | `youtube` | watched-video activity |
| Workouts | `workouts` | Boostcamp CSV export → normalized dataset (Phase 43) |

### Ingestion pipeline

```
Source → adapter → Document (content hash) → classifier (text-heavy /
image-heavy / mixed) → structured extraction | vision extraction
→ chunks → (optional embeddings) → SQLite
```

- **Deterministic and idempotent:** re-ingesting unchanged files/messages
  produces no new documents, chunks or memories.
- **Text-heavy** documents get structured extraction (summary, people,
  projects, goals, tags) and chunked text.
- **Image-heavy** documents (scanned PDFs, image-only pages, low OCR yield)
  are routed to the vision model — when configured — instead of being blindly
  pushed through OCR-like text extraction.
- **Email and financial** sources are model-free: pure deterministic parsing.
- **Unified corpus search** (Phase 33) with metadata filtering and provenance.

### Retrieval

- SQLite FTS5 keyword search over chunks, plus deterministic lexical memory
  retrieval and structured temporal event queries.
- Exposed to the agent as explicit tools (`search_documents`,
  `search_knowledge`, `query_events`, ...) through `ToolRegistry` — the agent
  never touches storage internals.

---

## Memory security model

- **Memory is data, never policy.** Stored memories can inform context, but
  can never grant permissions, relax approvals, or alter the `PolicyEngine`.
- **Explicit creation only.** Memories are written via an explicit
  `MemoryDraft`; there is **no autonomous/unrestricted memory writing**. The
  HTTP gateway exposes memory as **read-only** (`/api/memory/*`).
- **Scoped retrieval.** `global | agent | project | execution` scopes; a
  missing scope never means "search everything".
- **Safe audit events.** Event payloads carry ids, kinds, scopes and
  statuses — never private memory text. Physical `purge` is available for
  privacy-sensitive records and survives as an event only.
- **Automatic chat recall** (Phase 42) labels injected context as UNTRUSTED
  reference data: hostile passages are data, never instructions.
- See [`docs/MEMORY.md`](docs/MEMORY.md) and
  [`docs/AGENT_ORCHESTRATION.md`](docs/AGENT_ORCHESTRATION.md).

---

## Workout support

A local-first, normalized activity dataset parsed from a Boostcamp export
(`Workout/Boostcamp.csv`):

- Deterministic ids (`wkt-`/`ext-`/`set-`) derived from source identity, not
  free text; idempotent, transactional re-import.
- Read-only deterministic queries (workouts, exercises, stats, history).
- Policy-gated `search_workouts` chat tool, so Open WebUI chat can answer
  movement-based questions through the agent.
- CLI: `uv run python -m personal_ai.cli workouts ...` (`import`, `list`,
  `show`, `exercises`).
- HTTP: read-only `/api/workouts/*` on the gateway.
- See [`docs/WORKOUTS.md`](docs/WORKOUTS.md).

---

## Privacy and local-first guarantees

- **No cloud:** every model call goes to local Ollama; extraction, parsing and
  retrieval are local Python.
- **No uploads:** ingestion never sends content to any external service.
- **No secrets or data in the repo:** databases, workspaces, and private
  exports are gitignored (`.gitignore`), excluded from Docker builds
  (`.dockerignore`), and never committed. Tests use synthetic fixtures.
- **Hard sandbox:** the model may only use registered tools; filesystem tools
  resolve paths inside the workspace and reject escapes; no shell, no
  `eval`/`exec`, no arbitrary Python, no subprocess tooling.
- **Least privilege:** every agent tool runs only through the `PolicyEngine`;
  denied permissions always win, and approval-gated tools never execute until
  a human approves (durable, scope-exact `(execution, task, permission)`).
- **No model-generated code** is ever executed (Phase 39/39A/39B invariants).
- **Safe logs:** event payloads carry ids/counts/summaries — never full
  document content, conversations, tool arguments, or secrets.

---

## Known limitations

- **Retrieval is keyword-first.** Default retrieval is SQLite FTS5 + lexical
  ranking; semantic/vector search is an optional (embedding) backend and not
  the default path.
- **Vision is opt-in.** Image-heavy understanding requires setting
  `PERSONAL_AI_VISION_MODEL`; without it, image-heavy documents are stored but
  not vision-extracted.
- **Single-user, single-machine.** No multi-user auth surfaces, no
  distributed/networked orchestration.
- **Model synthesis.** `qwen3.5:9b` can occasionally drop a domain in broad
  compound questions or drift from evidence. Split compound questions; a retry
  usually resolves the stochastic case. This is treated as a model-quality
  characteristic, not a retrieval defect.
- **Memory is deterministic, not semantic.** Memory retrieval uses a weighted
  lexical score (relevance/importance/confidence/recency); no model-based
  similarity search.
- **No autonomous memory writes.** Memories are created explicitly (CLI/API/
  executions), never implicitly by chat.
- **Workouts.** Currently one supported export format (Boostcamp CSV); no
  writing/mutation surface by design.

---

## Roadmap

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased implementation status.

- **Completed:** core agent + tools (Phase 1) · document model, loading,
  classification, extraction, chunking, embeddings (2–7) · retrieval + tools
  (8–9) · 8+ source adapters · unified corpus search (33) · vision extraction
  (34a) · financial exports (37) · control plane + orchestration (39–39B) ·
  memory layer + chat recall (40–42) · workouts (43) · HTTP gateway (44) ·
  Dockerized deploy on the Open WebUI network (45).
- **Next (not yet implemented):** richer embedding-backed semantic retrieval
  as a configurable provider, multi-agent chat surfaces above the approval
  plane, and additional workout/memory consumer surfaces. Nothing beyond the
  implemented state is claimed to work.

---

## Development and tests

```bash
uv run pytest -q              # full suite (hermetic: no Ollama, no network)
uv run ruff check .
uv run ruff format --check src tests
uv run ruff format           # apply formatting if needed
```

Tests never require a running Ollama, an internet connection, or real personal
documents. Integration tests cover boundaries like `Agent + ToolRegistry`,
`Agent + filesystem`, ingestion + temporary files, storage + temporary SQLite,
and the HTTP gateway (ASGI, in-process).

## Documentation

- [`docs/USAGE.md`](docs/USAGE.md) — user guide: ingestion, email, financial,
  search, limitations.
- [`docs/AGENT_ORCHESTRATION.md`](docs/AGENT_ORCHESTRATION.md) — agents,
  skills, tools, policy, orchestration.
- [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md) — executions, events,
  approvals, Kanban.
- [`docs/MEMORY.md`](docs/MEMORY.md) — memory domain, storage, retrieval,
  chat recall.
- [`docs/WORKOUTS.md`](docs/WORKOUTS.md) — workout dataset.
- [`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md) — Open WebUI + HTTP API details.
- [`docs/DOCKER.md`](docs/DOCKER.md) — Dockerized gateway deployment.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phased status and what remains.