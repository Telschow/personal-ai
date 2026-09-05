# Personal AI — Roadmap and implementation status

The project is developed in small, tested slices. This page records what is
**implemented and shipped**, what is **being worked on now**, and what remains
**planned**. Only shipped items are claimed to work.

Status legend: **done** · **current** · **planned**.

---

## Phase 1 — Core agent (done)

- Typed Ollama HTTP client, local-first (`qwen3.5:9b`).
- Native Ollama tool-call parsing.
- Synchronous Agent loop (`max_tool_rounds=8`).
- Typed `ToolRegistry`; sandboxed filesystem tools (workspace boundary,
  no shell/exec/subprocess).
- Default tool registry; CLI agent entrypoint.
- Requirements: no cloud, no eval/exec, no dynamic imports.

## Phases 2–9 — Document foundation, ingestion, retrieval (done)

- `Document` / `DocumentChunk` models, stable ids, content hashes.
- SQLite document/chunk/extraction stores; idempotent re-ingestion.
- Filesystem loader (`.txt` `.md` `.pdf` `.png` `.jpg` `.jpeg`), sandboxed.
- Provider-independent text extraction; PDF loading separated from page text.
- Document classifier: `text-heavy / image-heavy / mixed` (measurable,
  encapsulated heuristics).
- Structured extraction through an abstraction (summary, people, projects,
  goals, tags); text model for text-heavy docs.
- Chunking; provider-independent embeddings with an Ollama implementation
  (optional backend).
- Retrieval: SQLite FTS5 keyword search + metadata-filtered chunk search;
  `search_documents` tool gated on indexed content.

## Phases 11–23 — Sources and retrieval hardening (done)

- Chrome history and YouTube history event sources.
- Conversational export adapters: ChatGPT, Gemini, Google Keep, NotebookLM.
- Google Takeout email (mbox) + Thunderbird mailbox support — model-free.
- Generic source ingestion orchestration; idempotent embedding backfill.
- Multi-term conversation ranking and tool-description guidance (Phase 23),
  temporal bounds on conversation search.

## Phase 30 — OpenAI-compatible HTTP API (done)

- `python -m personal_ai.server` gateway: `/v1/chat/completions`,
  `/v1/models`, SSE streaming; `PERSONAL_AI_CHAT_MODEL`,
  `PERSONAL_AI_API_HOST/PORT/TOKEN`, `PERSONAL_AI_WORKSPACE/DATABASE`.

## Phases 31–37 — Broader ingestion and search (done)

- Phase 31: file ingestion (`.txt/.md/.pdf/images`) with classifier-driven
  vision routing.
- Phase 32: email ingestion (Google Takeout / Thunderbird), fully local,
  model-free.
- Phase 33: unified corpus search with provenance and metadata filtering.
- Phase 34a: vision extraction for image-heavy/scanned documents (optional
  `PERSONAL_AI_VISION_MODEL`).
- Phase 37: financial exports — deterministic, structured, model-free.

## Phases 39–39B — Execution control plane & orchestration (done)

- Durable plans/tasks/events/evidence/approvals in SQLite; cursor-based event
  streams; Kanban projection.
- Agents/Skills/Tools/Policy model; `ModelRouter` + `OllamaProvider`
  (capability routing, model-agnostic).
- `PolicyEngine` software-policy gate: no tool runs un-checked; approval
  gates; least privilege; unverified corpus/memory treated as data.
- Researcher → verifier → synthesis workflow; deterministic verifier;
  bounded retries, no unrestricted autonomous loop.
- HTTP gateway surface `/api/executions/*` (Phase 44 brings it over HTTP).

## Phase 40 — Memory layer (done)

- Typed memory domain: kinds, scopes, confidence/importance, lifecycle;
  `MemoryDraft` explicit creation.
- SQLite persistence in the same DB as orchestration; idempotent upserts;
  audit-safe events; physical purge.
- Deterministic weighted lexical retrieval (relevance/importance/confidence/
  recency), scope-enforced; search never mutates state.

## Phase 41 — Agent memory retrieval (done)

- `search_memory` tool for agents (allowlisted, scope-safe, no-mutation),
  wired through registry + policy; injected as UNTRUSTED reference data.

## Phase 42 — Automatic chat recall (done)

- Application-level `ChatMemory` bounded query + scope derivation for chat
  contexts; missing trusted context never means "search everything"; distinct
  from explicit `search_memory`.

## Phase 43 — Workouts (done)

- Boostcamp CSV export parser → normalized `Workout`/exercises/sets with
  deterministic ids and content hashes; idempotent transactional import;
  read-only query service; CLI (`import/list/show/exercises`).

## Phase 44 — Control-plane HTTP gateway (done)

- OpenAI-compatible `/v1` (model id `personal-ai`, SSE streaming) plus
  read-only `/api/memory/*`, `/api/workouts/*`, `/api/executions/*`
  (list/show/events/board/approve/reject/resume/pause/cancel/retry).
- Approvals over HTTP are real durable, scope-exact decisions; tightened body
  validation; unified JSON error mapping; 503 when services are unconfigured.

## Phase 45 — Dockerized gateway on the Open WebUI network (current / shipped)

- `docker/Dockerfile` (Python ≥ 3.14 via uv, `uv lock`-pinned, non-editable
  install, existing entrypoint) + repo-root `.dockerignore` (private data and
  dev artifacts excluded) + `docker/docker-compose.yml`.
- The `personal-ai` service joins external `open-webui_default` and
  `ollama_default` networks; **no host ports**; `OLLAMA_BASE_URL` env support
  (`config.load_ollama_settings`, `build_agent(base_url=...)`).
- Open WebUI reaches the gateway at `http://personal-ai:8000/v1`
  (model id `personal-ai`); healthcheck via `/v1/models`.
- Docs: `docs/DOCKER.md`; offline config tests (`test_docker_config.py`,
  `test_ollama_configuration.py`) keep the suite hermetic.
- **Status:** implemented, validated (`docker compose config/build/up`,
  `docker exec open-webui curl http://personal-ai:8000/v1/models`, real chat
  from the Open WebUI container) and manually tested through Open WebUI.

## Phase 46 — Conversation exports + conversation-layer memory (done)

- `--ingest chatgpt|gemini <dir> --database <db>` routes to the typed
  `ConversationStore` (idempotent, model-free, aggregate-only reporting); the
  document-pipeline adapters become library API only.
- Optional `--memory` runs bounded deterministic conversation extraction
  (user-authored, active-branch, first-person self-assertions only) through
  the same policy-gated `propose_memory` write path as corpus extraction;
  provenance-only evidence, aggregate-only reports, idempotent reruns.
- `ConversationStore.list_conversations` gained `limit`/`offset` + source
  filters for bounded reads.

## Phase 47 — LLM-assisted memory candidate proposals (done)

- Bounded LLM candidate proposals (`memory/proposals.py`) on top of Slice 4:
  one bounded conversation window per model call, strict JSON output contract,
  validated/never-repaired, deterministic conversion guards (provenance,
  user-assertion, sensitive-form/negation/question skips), application-set
  scores and deterministic temporal/kind overrides.
- The LLM is a proposal generator only: every proposal still flows through
  `MemoryPolicy` → `AutomaticMemoryCurator` → `propose_memory` gate →
  `MemoryService.apply_candidate`. Policy remains authoritative; secrets are
  hard-rejected, salary-style content stays `require_approval`, and a denied
  `memory.write` gate raises and writes nothing.
- Bounded I/O (messages/prompt chars/proposals/evidence/retries), count-only
  failures, provenance-only evidence, idempotent reruns, aggregate-only
  reporting. Library API only — not wired into the CLI or
  `create_default_registry`.

## Phase 48 — Durable, resumable full-corpus memory curation (done)

- `memory/curation.py` turns already-ingested conversations into memories
  through the exact same policy-gated write path (`MemoryPolicy` →
  `AutomaticMemoryCurator` → `propose_memory` gate →
  `MemoryService.apply_candidate`); no second write route, and a denied
  `memory.write` gate raises `ApprovalRequiredError` and writes nothing.
- Durable `CurationStore` checkpoints (runs/units/review, content-free except
  the explicit review queue), idempotent reruns, resumable runs (`--resume`
  completes exactly the unfinished units; stale `running` units recovered,
  `failed` units retried), and dry runs that create no rows and no writes.
  A run with failed units is recorded `failed` (`unit_failures`) and remains
  resumable; a successful run is `completed` (terminal).
- Bounded I/O everywhere (`limit`/`offset` paging on
  `ConversationStore.list_conversations`/`list_messages`, `max_messages`,
  `max_prompt_chars`, `max_candidates_per_unit`, `max_retries`,
  `max_model_calls` (0 = unlimited), per-unit `unit_timeout_seconds`).
- CLI surface: `personal-ai memory curate|runs|review` (separate from the
  `--ingest --memory` flag and from `personal_ai.execution.cli`); `review` is
  the only statement-printing surface. Not wired into
  `create_default_registry`.

## Phase 49 — Unified full-corpus memory curation (done)

Curation is source-independent; no new write route exists.

- Source adapters (`memory/adapters.py`) discover bounded `CurationUnit`
  objects and derive `MemoryCandidate` instances routed through the exact same
  policy-gated write path as conversations (`MemoryPolicy` →
  `AutomaticMemoryCurator` → `propose_memory` gate → `MemoryService`).
  Adapters never write SQLite or touch memory tables.
- Sources: **email** (one unit per recurring non-webmail sender domain;
  deterministic mode is metadata-only with id-only monthly evidence; LLM mode
  adds bounded representative-email windows), **financial** (counted only, zero
  candidates and zero model calls in both modes), **document** (deterministic
  proposes nothing; LLM mode shows bounded chunk windows gated on
  `allowed_document_ids`), **workout/activity** (deterministic-only, reusing
  the conserved corpus extractors). chatgpt/gemini unchanged.
- `CurationAdapterRegistry` resolves the single adapter per source; units carry
  `source_id`/`source_version`/`signal`; checkpoints record `source_version`.
- Adaptive LLM gating: `--min-signal` and `--sample` downgrade low-signal /
  over-budget LLM units to deterministic processing, never calling the model
  for a downgraded unit.
- All step/run/report checkpoints stay aggregate-only; review-queue rows are
  the only statement-bearing table. Not part of `create_default_registry`.

## Planned (not yet implemented)

These are explicitly **not** shipped — do not claim them as working:

- **Semantic / hybrid retrieval first-class:** on by default with a pluggable
  vector backend; keyword + semantic + metadata filtering with ranking
  fusion.
- **Vision-first source pipelines** and denser multimodal extraction
  (whiteboards, mind maps, vision boards) as a first-class source class
  rather than an opt-in model.
- **Agent-facing retrieval tools** beyond the current allowlist (e.g.,
  `get_document`, `get_memory`), through `ToolRegistry` only.
- **Durable memories as first-class execution outputs** (memory producer
  surfaces) — currently memories are explicit-only.
- **Multi-agent chat** above the approval plane; richer Open WebUI chat
  surfaces reusing the approval UI.
- **Sync/import tooling** for additional workout/health export formats
  beyond Boostcamp CSV.
- Any scaling, multi-user, or cloud-level orchestration.

## Working principles

- Every slice lands with tests; the full suite stays hermetic (no Ollama, no
  network, no real personal documents).
- Local-first, provider-independent model boundaries, small composable
  components, no premature framework adoption (no LangChain/LlamaIndex/
  Chroma in the default path).
- Private personal data, exports, and live databases never enter the repo or
  published images.