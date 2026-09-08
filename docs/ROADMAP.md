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

## Phase 20 — Multilingual Security + Unicode Retrieval Foundation (done)

Security and Unicode foundation for processing the user's English/German/Spanish
corpus. No new write paths; LLM remains proposal-only.

P0-1 Multilingual Secret/PII Detection:
- Extended `MemoryPolicy` keywords with German and Spanish equivalents.
- Structural detectors (language-independent) unchanged.
- Keywords cover: password/Passwort/contraseña, bank account/Bankkonto/cuenta
  bancaria, credit card/Kreditkarte/tarjeta de crédito, medical/medizinische
  diagnosis/diagnóstico médico, SSN/Sozialversicherungsnummer/seguridad social,
  passport/Reisepassnummer/pasaporte, salary/Gehalt/salario, etc.
- Case/accent-insensitive matching via NFKC + casefold.

P0-2 Unicode-Safe Tokenization:
- Replaced ASCII-only `[a-z0-9]+` with Unicode-aware `[\p{L}\p{M}\p{N}]+`
  using the `regex` package.
- Centralized `_normalize_for_tokenize()` (NFKC + casefold) used by
  `MemoryRetriever`, `tokenize()`, and `MemoryReconciler.normalize_text()`.
- FTS5 uses default unicode61 tokenizer (no schema migration needed).
- Reconstruction uses original Unicode forms; no transliteration.

P0-3 Review CLI Fixes:
- Fixed SQLite syntax error in `list_pending_review` / `list_review`.
- Tests updated for aggregate-only default + `--show` flag behavior.

## Phase 21 — Multilingual Deterministic Conversation Extraction (done)

Deterministic conversation-memory extraction now fully covers English, German,
and Spanish user messages. Phase 11–20 invariants unchanged.

- `_detect_language` scores single words AND word pairs against per-language
  trigger sets; the English baseline skew is removed. Unmatched → UNKNOWN
  (English rule fallback).
- Spanish pro-drop support: `_spanish_is_first_person` accepts explicit
  `yo|mi|mis|me` OR a curated first-person verb form when not preceded by a
  determiner/possessive (handles noun homographs).
- `_canonical_statement` pro-drop rewriting ("Trabajo en Google" → "El usuario
  trabaja en Google"); German "Mein Name ist/heißt" → "Der nutzer heißt ...".
- `_RECURRING_PATTERNS[ES]` accepts plural forms; DE education rule fixed to
  "studiere"; DE/ES trigger sets extended with conjugated past forms.
- Weekday recurring constructs mapped to `habit`/recurring only for
  clearly-recurring forms (EN "every Monday"/"on Wednesdays", DE "jeden
  Montag"/"montags", ES "cada lunes"/"los domingos"); one-off single-day
  references (EN "on Monday", DE "am Montag", ES "el lunes") never become a
  recurring habit.
- `_is_first_person(raw, lang)` routes ES through the pro-drop gate.
- `_canonical_statement` gained default `lang=EN` (LLM-proposal layer intact).

Tests: 2475 passing (incl. Unicode tokenization matrix, no ASCII
transliteration). Ruff clean. Format clean.

## Phase 23 — Multilingual LLM Proposal Compatibility (done)

The bounded LLM *proposal* layer now speaks the source language instead of
dropping every non-English candidate. Phase 11–21 invariants unchanged; the
LLM remains a proposal generator only, and there is still exactly one
policy-gated write path.

- `_statement_language` (`memory/proposals.py`): marker-first ("der nutzer",
  "el usuario", "the user") with the deterministic `_detect_language` fallback
  (EN fallback on UNKNOWN).
- `_canonicalize_statement` canonicalizes first-person DE/ES proposals through
  the shared Slice 4 canonicalizer and verifies the third-person marker of the
  detected language — original-language content is kept, never translated.
- Negation, request/model-directed evidence gates, and the goal/recurring/past
  deterministic overrides are per-language; English tables ARE the original
  regexes (byte-identical behavior). The evidence assertion gate is a
  conservative union across EN/DE/ES.
- Quoted statements of every form („…“, «…», “…”, ‘…’, ASCII) are dropped as
  `quoted` in the conversation proposal path; `to_document_candidate`
  (`memory/adapters.py`) uses the same canonicalization/negation gates.
- One ES trigger addition (`estudié`) fixes language detection for "Estudié
  informática.".

Bounded real-Ollama pilot (synthetic only, 15 calls): EN 7 / DE 5 / ES 4
candidates, zero failed windows, no production database touched.

Tests: 2299 passing. Ruff clean. Format clean. `git diff --check` clean.

## Phase 24 — Bounded production-corpus curation readiness pilot (done)

Measurement-only pilot over a bounded real-corpus sample in a scratch SQLite
database; **zero repository code changed**. Existing `memory curate` runner
used as-is behind an external `/tmp` harness (deleted afterwards).

- **Scope:** 50 conversations per source (chatgpt, gemini), deterministic
  order; 172 + 376 messages, 93 + 187 user messages, 219 + 610 user sentences.
- **Stage A (deterministic):** 12 candidates (chatgpt 1; gemini 11 — goal 9,
  relationship 2; en 7 / de 3 / unknown 1); all policy-accepted, sensitivity
  ordinary; secrets/sensitive 0; candidate provenance valid 12/12.
- **Stage B (LLM, local Ollama qwen3.5):** 137 model calls, 0 failed units;
  chatgpt 3 written / 0 conflicts; gemini 15 written + 6 conflicts (review
  queue, category conflict); 0 rejected, 0 require_approval.
- **Outcome:** 30 active memories (goal 10, preference 5, work 3, relationship
  3, identity 2, habit 2, location_context 2, biography 1, routine 1, skill 1),
  41 provenance-valid evidence refs; language en 19 / de 7 / unknown 4.
- **Idempotency:** deterministic rerun → `created=0` both sources.
- **Checkpoint/resume:** gemini LLM interrupted at 4 units, `--resume` recovered
  and completed all 50 windows; duplicate writes after resume = 0.
- **Economics:** LLM unit wall-clock p50 2.26 s / p95 32.9 s; deterministic
  ~0 s.
- **Safety:** production DBs byte-identical (`production_db_modified=false`);
  scratch DB with personal content deleted, only count-only JSONs kept.

Tests: suite re-run green post-pilot (zero code change). Ruff clean. Format
clean.

## Phase 25 — Review-queue deduplication + resume selection fixes (done)

Fixes the two P3 reliability defects Phase 24 observed without any memory
architecture, policy, candidate, or write-path changes.

- **P3-1 review-queue deduplication:** a review obligation is the stable
  tuple `(source_type, category, reason, kind, temporal_scope, statement,
  evidence_json)`; `CurationStore.enqueue_review_if_missing` performs an
  atomic `BEGIN IMMEDIATE` check-and-insert. Reruns no longer grow the queue;
  resolved rows are not resurrected; identical statements with distinct
  evidence stay distinct.
- **P3-2 resume selection:** new `resumable_run` query picks the newest run in
  `running`/`failed` for a source/extraction pair; `--resume` now recovers an
  older interrupted run even when a newer completed run exists, and never
  resumes a completed (terminal) run. No run-id flag added.
- **Counting:** `review_deduplicated` added to run counters, per-source
  outcomes, and the `curate-all` report (aggregate-only).
- **Security/multilingual/provenance:** deduping is mechanical and never a
  classifier — secret candidates stay hard-rejected (zero review rows,
  zero writes); EN/DE/ES obligations dedupe identically; per-message evidence
  keeps distinct obligations distinct.
- **Synthetic pilot:** planted interrupted run + newer completed run,
  repeated conflict, salary escalations, and a secret candidate all behaved
  as specified in a scratch DB (deleted after aggregation); production DBs
  verified byte-identical.

Tests: 2318 passing (19 new). Ruff clean. Format clean.

## Phase 26 — Review-queue adjudication (exception-only human approval) (done)

Proves the durable review queue is safe and correct from candidate → policy →
curator → pending obligation → human adjudication → canonical write, across the
full state machine, adversarially (tampering, secrets, concurrency, reruns,
multilingual), over synthetic fixtures and scratch DBs only.

- **State machine:** `pending -> approved|rejected|expired`; all three terminal;
  a repeated decision observes `not_pending` and never writes; resolved rows are
  never silently re-opened.
- **Single write route holds:** approval re-runs `MemoryPolicy` on the
  reconstructed candidate, then routes through `AutomaticMemoryCurator` →
  `propose_memory` gate → `MemoryService.apply_candidate`. Rejection writes
  nothing. No second writer added.
- **Defects fixed (P0):** (1) CLI review wiring passed `CurationStore` where a
  `MemoryStore` was required, crashing approve/reject — now
  `MemoryService(MemoryStore(connection))`; (2) conflict-row approval used a
  policy-only auto approver that bypassed the row-pending gate — now the
  row-pending approver is bound for every policy outcome; (3) concurrent
  adjudication was unserialized — now a per-service `threading.RLock` plus a
  guarded `WHERE id=? AND status='pending'` transition, so two approvers yield
  exactly one write.
- **Security/provenance:** secrets hard-rejected before the queue (zero review
  rows); tampering a stored candidate into secret content expires the row with
  zero writes; evidence stays id-only, merged via the reconciler, never
  duplicated or fabricated; EN/DE/ES take the identical authorization path.
- **Synthetic pilot (scratch DB, deleted):** deterministic pass + LLM
  escalation → 4 approval rows / 1 secret rejected; adjudication produced 2
  approved + 1 rejected + 1 expired; round 2 all no-op; rerun dedupes 4/4;
  concurrency race → exactly one write. All litmus checks true.
- **CLI:** `memory review --approve N|--reject N` verified end-to-end.

Tests: 2356 passing (37 new: 35 adjudication + 2 CLI approve/reject end-to-end).
Ruff clean. Format clean. `git diff --check` clean. No production DB touched.

## Phase 27 — Review adjudication transaction + audit-log hardening (done)

Makes the human adjudication boundary crash-safe and durably auditable without
adding any write route or changing the security model.

- **Atomic adjudication (one transaction):** because `CurationStore` and
  `MemoryStore` share one SQLite connection, the entire decision (pending-row
  validation, deterministic policy re-evaluation, the policy-gated memory write
  + reconciliation, the audit event, and the pending → terminal transition)
  runs inside one explicit `BEGIN IMMEDIATE` ... `COMMIT`
  (`_AdjudicationConnection` defers the store-internal auto-commits while the
  transaction is open). A crash before `COMMIT` rolls everything back; after
  `COMMIT` it is fully durable. A failed `COMMIT` returns a conservative
  `{"outcome":"error"}` and writes nothing. This fully fixes Phase 26's
  "memory-written/review-pending" window.
- **Durable adjudication audit trail:** every terminal decision appends exactly
  one content-free event to `memory_review_audit` (review_id, action, outcome,
  actor, policy_category, memory_id, statement_hash = SHA-256 digest of the
  statement, created_at) — never statements/evidence. Repeated decisions are
  `not_pending` and add no event; reads are aggregate-only
  (`review_audit` / `review_audit_counts`).
- **Concurrency:** per-instance RLock + guarded pending→terminal UPDATE +
  `BEGIN IMMEDIATE` yield exactly one approval / memory / audit event and one
  `not_pending` across two separate connections/instances on the same DB.
- **CLI:** `_run_memory_review` builds both stores and the review service on one
  `_AdjudicationConnection`; approve/reject outcomes still reflect the
  committed durable state.
- **Synthetic fixtures only** (temp SQLite files + `:memory:`); no production
  DB opened writable.

Tests: 2367 passing (11 new in `tests/test_memory_review_transaction.py` plus the
Phase 26 adjudication suite now exercising the atomic path). Ruff clean. Format
clean. `git diff --check` clean. No production DB touched.

## Phase 28 — Durable review audit observability & aggregate adjudication reporting (done)

Read-only, privacy-safe observability over the Phase 27 audit trail plus
aggregate adjudication in `curate-all`. **No new write route**; atomic
adjudication, policy, and approval semantics unchanged.

- **CLI audit surface (`memory review --audit` / `--audit-counts`):** prints a
  bounded recent view (metadata only) and aggregate-only totals; both support
  `--json`. Emitted fields are operational only — `review_id`, `action`,
  `outcome`, `actor`, `policy_category`, `memory_id`, `created_at`. The
  `statement_hash` digest and internal audit row `id` are never exposed;
  statements/evidence/sensitive content are never printed. Empty DB →
  `events: 0`.
- **Store/service:** `CurationStore.review_audit()` gained deterministic
  `recent` ordering + bound; `review_audit_counts()` returns a stable aggregate
  (`events`/`actions`/`outcomes`/`policy_categories`/`actors`). The service
  exposes `audit()`/`audit_counts()` that strip content.
- **Approve/reject metadata:** results now carry `audit_recorded=true|false`;
  repeated decisions stay `not_pending` with `audit_recorded=false` and create
  no additional event (exactly-one invariant preserved).
- **`curate-all` adjudication snapshot:** `CorpusCurationReport.summary()` adds
  `review_approved`/`review_rejected`/`review_expired` derived from the audit
  table (global; audit has no run id), kept distinct from pending
  `review_queued`/`review_deduplicated`.
- **Latent `curate-all` CLI fixes** (first exercised by these tests): removed
  the call to a non-existent `CorpusCurationConfig.validate()` and corrected the
  `WorkoutStore`/`EventStore` imports in `_build_curation_registry_generic`. No
  change to adjudication semantics.

Tests: 2377 passing (12 new in `tests/test_memory_review_audit_cli.py`). Ruff
clean. Format clean. `git diff --check` clean. No production DB touched
(production hashes unchanged).

## Phase 29 — Read-only memory review audit agent tool (done)

A strictly read-only agent-facing surface for the Phase 27/28 audit trail.
**No new write or adjudication route**; observability is separate from human
adjudication.

- **Tool (`memory_review_audit`, `agents/tools.py`):** registered behind a
  distinct read-only `review.audit.read` permission (`risk=READ`,
  `mutates_state=False`, `deterministic=True`), only when a
  `MemoryReviewService` is wired into `build_default_agent_tools`. Consumes the
  existing `MemoryReviewService.audit()`/`audit_counts()` → `CurationStore`
  surface — no second audit implementation, no SQL in the tool.
- **Operations:** `counts` (aggregate-only events/actions/outcomes/
  policy_categories/actors) and `recent` (bounded newest-first metadata:
  review_id, action, outcome, actor, policy_category, memory_id, created_at;
  default limit 20, hard max 200, deterministic `created_at DESC, id DESC`).
- **Privacy:** identical to Phase 28 — statements, evidence, `candidate_json`,
  prompts, model output, `statement_hash`, `review_note`, secrets, and
  sensitive values are never returned/logged/in error text; the handler
  defensively re-projects every result.
- **No autonomy:** cannot approve/reject/expire/reopen, mutate memories/reviews/
  audit rows, invoke `apply_candidate()`, bypass `MemoryPolicy`, or run SQL.
  Unknown operations/limits rejected; mutation-looking params ignored. The
  human review boundary is intact — the agent observes, never decides.
- **Permissions:** researcher holds the least-privilege read permission; the
  reviewer agent (no `review.audit.read`) is denied at the policy boundary.

Tests: 2399 passing (20 new in `tests/test_memory_review_audit_tool.py`). Ruff
clean. Format clean. `git diff --check` clean. No production DB touched
(production hashes unchanged).

## Phase 30 — Deterministic review-audit time-window filtering (done)

Deterministic, inclusive `--since`/`--until` filtering over the durable review
audit trail. **No new write, adjudication, policy, curation, or transaction
route** — observability stays read-only and content-free.

- **Filter contract:** authoritative timestamp is `memory_review_audit.
  created_at`; `since`/`until` are inclusive (`>=`/`<=`); neither → all events
  with existing limits; `since > until` rejected deterministically before any
  query (no silent swap, no empty fallback). No migration.
- **Timestamp normalization:** `YYYY-MM-DD` (midnight UTC),
  `YYYY-MM-DDTHH:MM:SS`, `YYYY-MM-DDTHH:MM:SSZ`, ISO offsets; naive → UTC;
  every bound canonicalized via `format_utc_timestamp` so equivalent instants
  filter identically (`parse_iso_timestamp`/`format_utc_timestamp` in
  `memory/models.py`).
- **CLI:** `memory review --audit`/`--audit-counts` take `--since`/`--until`;
  invalid/reversed bounds exit non-zero with a clean message (no traceback).
  `recent` keeps default limit 20 / hard max 200; counts preserve the Phase 28
  aggregate schema over the same window; empty windows render `events: 0`/`[]`.
- **Agent tool (`memory_review_audit`):** `counts`/`recent` validate `since`/
  `until` (`_parse_and_validate_timestamp`: TypeError for non-strings, ValueError
  for bad formats/reversed range), normalize to UTC, and pass through to the
  existing `MemoryReviewService` surface — no SQL in the tool, no second audit
  implementation. Permission/risk/determinism and privacy unchanged.
- **Store/service:** SQL-side parameterized `>=`/`<=` filtering
  (`review_audit`, `review_audit_counts`,
  `_audit_group_counts(column, where, params)`); ordering deterministic
  (`created_at DESC, id DESC`).

Tests: 2467 passing (68 new in `tests/test_memory_audit_time_filtering.py`).
Ruff clean. Format clean. `git diff --check` clean. No production DB touched
(production hashes unchanged).

## Phase 31 — Read-only agent retrieval tools: get_document + get_memory (done)

The agent/tool layer gains read-only, policy-gated fetch tools for documents
and durable memories. Phase 11–30 invariants unchanged; **no new write or
adjudication route exists**.

- `get_document` and `get_memory` are registered in `agents/tools.py` via
  `build_default_agent_tools` (`AgentToolRegistry`) and consumed by the
  researcher/corpus agent policies. Both are non-mutating, deterministic, and
  read-only; GET_DOCUMENT gates on the existing `corpus.search` permission,
  GET_MEMORY on the existing `memory.read` permission — **no new permissions**.
  Denials (`CURATOR`, permission-less agents) surface via `PolicyDenialError`
  with no store/service access, and decisions stay observable through the
  shared audit trail.
- `get_document(document_id, chunk_limit=20, capped at 100)` returns the
  document metadata plus a bounded, deterministically ordered chunk window
  (`chunk_index, chunk_id`), or `{"status":"not_found","document_id"}` for an
  unknown id. `get_memory(memory_id)` returns the canonical memory plus
  content-free provenance aggregation (evidence count/kinds, first/last
  evidence timestamps — never evidence ids/bodies), or `not_found`.
- Malformed/missing ids raise `TypeError`/`ValueError`; unknown and
  mutation-looking parameters (`approve`, `apply_candidate`, `review_id`,
  `sql`, `curate`, ...) are ignored, never honored. No SQL in the tool layer
  (reads go through `DocumentStore`/`ChunkStore`/`MemoryService`); no schema
  change, no new dependency, no network, no LLM calls, no automatic curation.
- Privacy: aggregate/operational fields only; document statements, memory
  content, prompts, model output, secrets, and sensitive values never appear in
  results, logs, or error text.

Tests: `tests/test_agent_get_document_get_memory.py` (31 tests).

## Phase 32 — Chat exposure of read-only retrieval tools (done)

The Phase 31 fetch tools are now directly addressable from the interactive chat
`ToolRegistry` (`create_default_registry`). Phase 11–31 invariants unchanged;
**no new write or adjudication route exists**.

- New `tools/fetch.py` provides `PolicyGatedGetter` (+
  `build_policy_gated_get_handler`): one `PolicyEngine` over the Phase 31
  `AgentToolRegistry` runs `get_document`/`get_memory` via
  `PolicyEngine.execute(RESEARCHER, ...)` — the researcher is the only agent
  the chat path may impersonate (same pattern as `tools/corpus.py` /
  `workouts.py` / `personal_context.py`), so chat enforcement and audit are
  identical to the execution runtime and Phase 31 differentiated access is
  preserved (no permission bypass).
- `create_default_registry` gained a trailing `document_store` parameter
  (default None). `get_document` registers only when BOTH `document_store` and
  `chunk_store` are wired; `get_memory` registers only when a `memory_service`
  is wired. Handlers run entirely inside the policy engine — no direct
  store/service access from the registry layer, no SQL, no write surface.
  `propose_memory` remains approver-gated and is unaffected.
- `cli._connect_agent_registry` passes `document_store` through.
- Tool contracts, permissions, risk/determinism flags, privacy posture,
  unknown-parameter rejection, and `not_found` semantics are all unchanged
  from Phase 31.

Tests: 2604 passing (20 new in `tests/test_chat_get_document_get_memory.py`;
`tests/test_cli_wiring.py` updated for the wired chat set). Ruff clean. Format
clean.

## Planned (not yet implemented)

These are explicitly **not** shipped — do not claim them as working:

- **Semantic / hybrid retrieval first-class:** on by default with a pluggable
  vector backend; keyword + semantic + metadata filtering with ranking
  fusion.
- **Vision-first source pipelines** and denser multimodal extraction
  (whiteboards, mind maps, vision boards) as a first-class source class
  rather than an opt-in model.
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
## Phase 22 — Real-corpus audit (implemented)

- **`memory corpus-audit`**: bounded, aggregate-only, read-only audit of raw
  ChatGPT/Gemini exports through the existing loaders/extractor/policy. No
  `--database` flag; production DB structurally unreachable.
- **Sampling**: 25 conversations/source deterministic sorted sample;
  `--max-messages` per-conversation cap.
- **Metrics**: language distribution (en/de/es/unknown/mixed), candidates by
  kind/language/temporal, skip-reason taxonomy, policy decisions/sensitivity,
  evidence provenance validity, Unicode/tokenization health, Phase 21A ES
  trigger-gap occurrence counts (measured only — never implemented),
  `model_calls: 0`.
- **Dry-run ingestion validation**: optional disposable scratch SQLite runs
  the exact policy-gated production write path twice; zero memory/evidence
  growth on pass 2 proves idempotency.

Real-corpus pilot (bounded): ChatGPT 469→25 sampled (131 user messages, 1 goal
candidate); Gemini 170→25 sampled (122 user messages, 7 candidates → 5 distinct
memories). Both idempotent, `secret_rejected: 0`, `require_approval: 0`,
`evidence_invalid: 0`, model calls 0, production DB hashes unchanged.

Tests: 2507 passing (32 new: `tests/test_memory_corpus_audit.py` +
`tests/test_cli_memory_corpus_audit.py`). Ruff clean. Format clean.

## Phase 23 — Unicode tokenization closure (implemented)

- **Shared Unicode primitive** (`memory/tokenizer.py`): NFKC + casefold +
  variation-selector strip + `regex` tokenization (`[\p{L}\p{M}\p{N}]+`),
  consumed by retriever and reconciler. `tokenize()` re-exported from retriever.
- **VS-leak fix (real defect):** variation selectors (U+FE00–U+FE0F,
  U+E0100–U+E01EF) no longer leak junk `Mn`-tokens (`❤️` -> no tokens; live:
  ChatGPT `tokens_total` 2919→2917, `unicode_tokens` 28→26).
- **Audit metric correction:** `zero_token_messages` now counts only true
  zero-token gaps (bucketed via `zero_token_by_category`); messages whose
  non-ASCII letters NFKC/casefold to ASCII (fullwidth/alphanumeric-symbol)
  tokenize normally and are counted as `ascii_folded_messages` (expected, not
  an error). `unicode_errors` non-empty only for `lexical_unicode > 0`.
- **Real-corpus outcome:** ChatGPT + Gemini `zero_token_messages: 0`,
  `ascii_folded_messages: 2` each, `unicode_errors: []`. The Phase 22
  "4 zero-token messages" were ASCII-folded lexical messages — a metric
  artifact, not a tokenization failure. Scratch-DB idempotency + prod DB
  hashes unchanged.
- **Explicitly out of scope:** segmentation, semantic retrieval, embeddings,
  translation, transliteration. `München` stays `münchen`; `café` != `cafe`.

Tests: 2553 passing (44 in `tests/test_memory_unicode_tokenization.py` plus
reconcile/audit additions). Ruff clean. Format clean.
