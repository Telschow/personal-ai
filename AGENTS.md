You are the primary implementation engineer for this repository.

Repository:
    /mnt/immich/projects/personal-ai

Project:
    Personal AI — a local-first personal knowledge and agent system.

Your job is to evolve the existing codebase into a robust personal AI system while preserving the architecture and quality of the existing implementation.

IMPORTANT:
Do NOT rewrite the project from scratch.
Do NOT replace working abstractions just because you would design them differently.
Do NOT implement the entire roadmap in one giant change.
Work incrementally in small vertical slices.
Every slice must have tests.
Keep the repository runnable after every change.
Do not commit unless explicitly asked.

==================================================
CURRENT STATE
==================================================

The repository already contains:

- typed Ollama HTTP client
- Ollama native tool-call parsing
- synchronous Agent loop
- typed ToolRegistry
- sandboxed filesystem tools
- default tool registry
- CLI
- extensive unit/integration tests

Current architecture:

    CLI
     │
     ▼
    Agent
     │
     ▼
    ToolRegistry
     │
     ├── list_directory
     │
     ▼
    FilesystemTool
     │
     ▼
    sandboxed workspace

Ollama is running locally.

The project currently uses Python 3.14+.

Existing quality requirements:

    uv run pytest
    uv run ruff check .
    uv run ruff format --check src tests

must remain clean.

Never introduce tests that require the real Ollama server unless explicitly marked as integration tests and intentionally separated from the normal test suite.

==================================================
VISION
==================================================

Build a local-first personal AI that can:

1. converse with the user
2. use safe tools
3. ingest personal documents
4. understand both text and images
5. extract structured knowledge
6. store durable personal knowledge
7. retrieve relevant knowledge later
8. eventually combine retrieval with agent/tool execution

The system should eventually handle sources such as:

    PDFs
    images
    scanned documents
    notes
    emails
    WhatsApp exports
    ChatGPT exports
    Gemini exports
    text files
    screenshots
    vision boards
    handwritten notes
    other personal data sources

The system should be designed around the idea that a "personal AI" is more than a PDF search engine.

Visual information is first-class information.

Examples:

    vision boards
    goal collages
    handwritten notes
    whiteboards
    diagrams
    mind maps
    financial charts
    photographs
    screenshots
    scanned reports

OCR alone is insufficient for image-heavy documents.

A vision-capable model should be able to interpret semantic information that OCR cannot capture.

Example:

A vision board containing:

    BMW logo
    BCG logo
    family photograph
    €500k
    beach
    dumbbell
    house

should be interpretable as something resembling:

    long-term aspirations involving career progression,
    financial independence, family, fitness, travel, and
    home ownership.

==================================================
TARGET ARCHITECTURE
==================================================

The eventual architecture should resemble:

                        ┌──────────────┐
                        │     CLI      │
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │    Agent     │
                        └──────┬───────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
             Tools         Retrieval       Memory
                │              │              │
                │              │              │
                ▼              ▼              ▼
           filesystem      search       structured data
                │              │              │
                └──────────────┼──────────────┘
                               │
                               ▼
                       Personal Knowledge
                               │
                               ▼
                         Document Store
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
              Text-heavy             Image-heavy
                    │                     │
                    ▼                     ▼
                Text model           Vision model
                    │                     │
                    └──────────┬──────────┘
                               ▼
                       Structured extraction
                               │
                               ▼
                        Persistent storage
                               │
                               ▼
                           Embeddings
                               │
                               ▼
                           Retrieval


==================================================
IMPORTANT ARCHITECTURAL PRINCIPLES
==================================================

1. LOCAL FIRST

Prefer local services/models.

Ollama is the current model backend.

Do not introduce cloud APIs unless explicitly requested.

2. PROVIDER INDEPENDENCE

Do not scatter Ollama-specific assumptions throughout the application.

The Ollama client should remain the infrastructure boundary.

Future model providers should be replaceable.

3. TESTABILITY

Business logic must be testable without:

    network
    Ollama
    filesystem outside temporary directories
    external databases

Use dependency injection and fakes/mocks.

4. SECURITY

The filesystem workspace sandbox is a hard security boundary.

Never allow the model to arbitrarily:

    read outside the workspace
    write outside the workspace
    execute shell commands
    execute arbitrary Python
    dynamically import modules
    spawn subprocesses

Do not introduce eval(), exec(), shell execution, or dynamic imports as tool mechanisms.

5. EXPLICIT TOOLS

The model may only execute registered tools.

Never allow model-generated code to become executable code.

6. DATA PRIVACY

Personal data is expected to be sensitive.

Avoid unnecessary logging of:

    document contents
    conversations
    extracted memories
    tool arguments

7. IDEMPOTENT INGESTION

Re-running ingestion on the same unchanged document should not create duplicate documents or memories.

Use stable document identifiers/content hashes where appropriate.

8. OBSERVABILITY

Prefer structured metadata and explicit status information over dumping personal content into logs.

9. SMALL COMPONENTS

Prefer narrow interfaces and composable components.

10. AVOID PREMATURE COMPLEXITY

Do not introduce a large framework merely because it exists.

In particular:

DO NOT immediately introduce Chroma, LangChain, LlamaIndex, or another large orchestration framework.

First establish clean internal interfaces.

==================================================
DOCUMENT MODEL
==================================================

Introduce a clear distinction between:

DOCUMENTS

What was ingested.

Example conceptual model:

    Document
        id
        source
        source_type
        path
        filename
        mime_type
        created_at
        modified_at
        content_hash
        metadata

EXTRACTIONS

What the model understood from a document.

Example:

    Extraction
        document_id
        summary
        people
        projects
        goals
        preferences
        entities
        tags

CHUNKS

Searchable portions of a document.

Example:

    DocumentChunk
        id
        document_id
        text
        page_number
        metadata

EMBEDDINGS

Vector representations used for retrieval.

Keep embeddings conceptually separate from documents.

==================================================
STORAGE
==================================================

Initially prefer SQLite for durable metadata and structured knowledge.

Do not prematurely commit the entire architecture to a specific vector database.

Create abstractions/interfaces where appropriate, for example:

    DocumentStore
    ExtractionStore
    ChunkStore
    EmbeddingStore

The initial implementation can be SQLite-backed.

A future vector implementation can be added later.

The application should not need to know which vector database is being used.

==================================================
INGESTION PIPELINE
==================================================

Build toward a unified ingestion pipeline.

Conceptually:

    Source
      │
      ▼
    Document Loader
      │
      ▼
    Document Analyzer
      │
      ├───────────────┐
      │               │
      ▼               ▼
    Text-heavy     Image-heavy
      │               │
      ▼               ▼
    Text model      Vision model
      │               │
      └───────┬───────┘
              ▼
      Structured extraction
              │
              ▼
        Document storage
              │
              ▼
           Chunking
              │
              ▼
         Embeddings
              │
              ▼
        Retrieval index


Do NOT create separate application-level pipelines for:

    PDF
    image
    email
    WhatsApp

Instead create a common document abstraction and source-specific loaders.

==================================================
TEXT QUALITY
==================================================

Do not classify documents merely as:

    has text
    no text

Measure usable extracted text.

For example, a PDF with:

    mostly empty pages
    image-only pages
    tiny OCR fragments

should be considered image-heavy.

The exact heuristic should be encapsulated in a classifier rather than scattered through ingestion code.

==================================================
VISION
==================================================

Vision processing should only happen when useful.

Potential triggers:

    insufficient extracted text
    image-heavy page
    scanned document
    poor OCR quality
    explicit image source

Do not send every normal text document through a vision model.

Vision processing should be represented by an abstraction.

For example:

    VisionExtractor

Do not hard-code the entire application to a particular vision model.

The exact Ollama model can initially be configured through settings.

==================================================
MODELS
==================================================

Different tasks may use different models.

Conceptually:

    Chat model
        agent conversations

    Text extraction model
        structured document extraction

    Vision model
        image understanding

    Embedding model
        embeddings

Do not assume one model must perform every task.

Models should be configurable.

The current working chat model is:

    qwen3.5:9b

Do not change it unnecessarily.

==================================================
STRUCTURED EXTRACTION
==================================================

Extraction should produce structured data rather than only a giant summary string.

Potential fields:

    summary
    people
    organizations
    projects
    goals
    preferences
    locations
    dates
    topics
    tags

Keep the schema intentionally conservative initially.

Do not create a gigantic ontology before real data exists.

Schemas should be typed.

Prefer Pydantic/dataclasses where appropriate.

==================================================
RETRIEVAL
==================================================

Eventually support:

    semantic search
    keyword search
    metadata filtering

Do not assume semantic vector search is always best.

A personal AI should be able to answer:

    "What did I write about X?"

    "Where did I mention Y?"

    "What documents relate to project Z?"

    "What goals did I have around career?"

Hybrid retrieval can be introduced later.

==================================================
AGENT TOOLS
==================================================

Eventually expose retrieval as an agent tool.

For example:

    search_memory
    search_documents
    get_document
    get_memory

These tools must go through ToolRegistry.

The agent must never directly access storage internals.

==================================================
CLI
==================================================

The CLI currently supports:

    --workspace PATH
    PROMPT

Keep the CLI simple.

Eventually consider:

    --workspace
    --model
    --verbose
    --ingest
    --search

But don't add these until their underlying functionality exists.

==================================================
PROJECT STRUCTURE
==================================================

Move toward a structure similar to:

    src/personal_ai/
        __init__.py

        agent.py
        cli.py
        ollama_client.py

        tools/
            __init__.py
            registry.py
            filesystem.py
            defaults.py

        documents/
            __init__.py
            models.py
            loader.py
            classifier.py
            extractor.py
            pipeline.py

        storage/
            __init__.py
            database.py
            documents.py
            memories.py
            chunks.py

        embeddings/
            __init__.py
            models.py
            ollama.py

        retrieval/
            __init__.py
            search.py

        config.py

Do NOT create empty speculative modules merely to match this tree.

Create modules when their functionality is actually implemented.

==================================================
CONFIGURATION
==================================================

Use environment/configuration rather than hard-coding everything.

Potential settings:

    OLLAMA_BASE_URL
    PERSONAL_AI_CHAT_MODEL
    PERSONAL_AI_TEXT_MODEL
    PERSONAL_AI_VISION_MODEL
    PERSONAL_AI_EMBEDDING_MODEL
    PERSONAL_AI_DATABASE
    PERSONAL_AI_WORKSPACE

But introduce configuration incrementally.

Do not over-engineer configuration before it is needed.

==================================================
TESTING REQUIREMENTS
==================================================

Every new component requires tests.

Normal test suite must not require:

    running Ollama
    internet
    real personal documents

Use:

    pytest
    tmp_path
    httpx.MockTransport
    fake clients
    deterministic fixtures

Integration tests should test boundaries between components.

Examples:

    Agent + ToolRegistry
    Agent + filesystem
    ingestion + temporary files
    storage + temporary SQLite database

==================================================
CURRENT ROADMAP
==================================================

Implement in approximately these phases.

PHASE 1 — CURRENT

DONE:

    Ollama client
    tool calls
    ToolRegistry
    Agent
    filesystem sandbox
    default registry
    CLI
    tests

Do not rewrite these.

PHASE 2 — DOCUMENT FOUNDATION

Implement:

    Document model
    DocumentChunk model
    source metadata
    stable IDs/content hashes
    document store abstraction
    SQLite implementation

Tests first.

PHASE 3 — FILE DISCOVERY

Implement a filesystem document loader capable of discovering supported files inside the workspace.

Initially support a small set, for example:

    .txt
    .md
    .pdf
    .png
    .jpg
    .jpeg

Do not implement every format immediately.

Security:

    discovery must remain inside workspace.

PHASE 4 — TEXT EXTRACTION

Implement extraction for text-based documents.

For PDFs, separate:

    file loading
    page extraction
    text quality measurement

Do not mix model calls into basic file parsing.

PHASE 5 — DOCUMENT CLASSIFICATION

Determine whether a document is:

    text-heavy
    image-heavy
    mixed

Use explicit measurable heuristics.

Test this thoroughly.

PHASE 6 — MODEL EXTRACTION

Implement structured extraction through an abstraction.

For text-heavy documents:

    text model

For image-heavy documents:

    vision model

Do not send unnecessary data to the vision model.

PHASE 7 — CHUNKING + EMBEDDINGS

Introduce chunks.

Then embedding generation.

Keep embedding provider abstract.

Initial provider can be Ollama.

PHASE 8 — RETRIEVAL

Implement:

    semantic search
    metadata filtering
    document retrieval

Initially a simple implementation is preferable.

PHASE 9 — RETRIEVAL TOOLS

Expose retrieval through ToolRegistry.

For example:

    search_documents

Then the existing Agent can reason over the user's personal knowledge.

PHASE 10 — MEMORY

Only after documents/retrieval work well, introduce durable "memories" as a distinct concept.

Memories should not simply be copies of document chunks.

A memory represents extracted durable knowledge about the user.

PHASE 11 — MEMORY WRITES (IMPLEMENTED)

Memory is now read-ignorable, write-gated:

- Read retrieval is application-controlled: the read-only `search_memory`
  agent tool (permission `memory.read`) and automatic `ChatMemory.recall`
  (bounded lexical query + deterministic salient-context fallback). Both emit
  UNTRUSTED-labeled context, never instructions.
- The ONLY agent write route is the approval-gated `propose_memory` chat tool:
  `PolicyEngine.execute(curator, "propose_memory", {...})`, where
  `memory.write` lives in the curator policy's `approval_required`. No approver
  wired ⇒ `ApprovalRequiredError` (default-deny). The handler exposes a bounded
  draft surface (content/kind/summary/confidence/importance) and always writes
  global, user-sourced memory via `MemoryService.create_user_memory`.
- Registration is default-deny: `create_default_registry` registers
  `propose_memory` only when BOTH a `MemoryService` and a
  `memory_proposal_approver` are supplied; the server/Open WebUI path registers
  no write tool.
- Never add another memory-write path (no background memorization, no inferred
  writes, no write via `search_memory`).

PHASE 12 — AUTOMATIC MEMORY POLICY (IMPLEMENTED)

Deterministic automatic acceptance coexists with the manual-gate rules above —
governing invariants are unchanged:

- `MemoryPolicy.evaluate(candidate)` is a pure, DETERMINISTIC policy (accept /
  reject / defer / require_approval). It is never an LLM judge: model-generated
  candidate metadata is untrusted input and every numeric/enum/evidence field
  is validated at candidate construction (`tests/test_memory_candidate.py`).
- SECRET content (keys, tokens, JWTs, credential keywords) is ALWAYS rejected
  before any write path — never deferred, never escalated, never stored.
- Every write — automatic acceptance incl. — still goes through
  `PolicyEngine.execute(CURATOR, "propose_memory", ...)`;
  `AutomaticMemoryCurator` grants the `memory.write` approval gate only by
  re-verifying the deterministic policy for exactly that candidate, so a
  denied/absent gate still raises `ApprovalRequiredError` and writes nothing
  (`tests/test_automatic_memory_curator.py`). The curator is a builder in
  `tools/memory.py`; it is NOT wired into `create_default_registry` (the
  production chat path remains interactive-only).
- Multi-evidence provenance (`memory_evidence`, idempotent) and the lifecycle
  `candidate -> active -> superseded(-> archived/deleted)` are enforced in the
  storage/service layer; reconciliation is lexical (no vector search, no LLM),
  conflicts surface via `MemoryConflictError`, and superseded/candidate/
  archived/deleted memories never surface in search or recall.
- `MemoryService.apply_candidate(candidate)` is the ONLY write route that
  bypasses human interaction, and it is reachable ONLY via the policy engine
  `propose_memory` gate. Do not add any other automatic write path.

PHASE 13 — CORPUS-LAYER MEMORY INGESTION (IMPLEMENTED)

Deterministic, bounded corpus extraction feeds the exact same policy-gated
write path as above — all Phase 11/12 invariants are unchanged:

- `CorpusMemoryIngestor` (`memory/corpus.py`) derives `MemoryCandidate`
  instances from already-ingested structured material and routes every one
  through `AutomaticMemoryCurator` (→ `propose_memory` gate →
  `MemoryService.apply_candidate`). It performs no raw SQL and no direct writes.
- Extraction is deterministic and conservative — no LLM, no body parsing, no
  invented facts: workouts propose a generic recurring `habit` only above
  session/month thresholds; chrome URL visits aggregate per normalized domain
  into recurring `interest` candidates (never raw URLs/titles/query strings in
  memory content or identifiers); email/financial documents are scanned for
  counts only (their metadata has no safely-deterministic facts this slice).
- All real corpus signals are behavioral provenance ⇒ `MemoryPolicy` defers
  them (nothing auto-writes on a live run). The auto-accept path is still
  honored and tested with personal-document/conversation provenance.
- Every step is bounded: batch reads (`DocumentStore.list_documents` /
  `EventStore.list_events` gained `limit`/`offset` + source filters), a
  `max_records_per_source` cap, and a `max_candidates_per_source` cap.
  `CorpusIngestionReport` is aggregate-only; rerunning is idempotent.
- Do NOT wire corpus ingestion into `create_default_registry`, and do not add
  a corpus CLI/checkpoint yet — those belong to later curation slices.

PHASE 14 — CONVERSATION-LAYER MEMORY INGESTION (IMPLEMENTED)

Conversation exports (ChatGPT/Gemini) are now first-class sources beside
documents. Phase 11/12/13 invariants are unchanged:

- `--ingest chatgpt|gemini <dir> --database <db>` routes to the typed
  `ConversationStore` (idempotent, no model call). This is the ONLY route for
  conversation exports — the document pipeline never sees them. The chatgpt
  document adapters (`sources/chatgpt.py`, `sources/gemini.py`) remain library
  API and are NOT wired into the CLI.
- The optional `--memory` flag runs bounded deterministic extraction over the
  stored conversations of the requested source and prints an aggregate-only
  report under `memory:`. Without the flag, memory is never touched.
- `ConversationMemoryExtractor` (`memory/conversations.py`) derives
  `MemoryCandidate` instances from user-authored, active-branch, first-person
  self-assertions only (assistant/system/tool claims are never user facts);
  negation, questions, model-directed requests, quoted content, and sensitive
  material are skipped. Temporal scope is derived from the trigger
  (current/historical/recurring); "I want to do X" is a `goal`, never a fact.
- Every candidate is routed through the same gate as corpus extraction:
  `AutomaticMemoryCurator` (→ `propose_memory` gate →
  `MemoryService.apply_candidate`). No raw SQL, no direct writes.
- Evidence is provenance-only: stable identifiers and ISO timestamps, never
  message content. Repeated facts across conversations accumulate evidence on
  a single memory via the reconciler; reruns are idempotent.
- Every step is bounded (batch reads with `limit`/`offset`, caps on
  conversations/messages/candidates) and reports are aggregate-only.
- Memory ingestion is never wired into `create_default_registry`; the
  production chat path remains interactive-only.
- Add no other conversation-exports ingestion route, and do not auto-run
  `--memory` without the flag.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

PHASE 15 — LLM-ASSISTED MEMORY CANDIDATE PROPOSALS (IMPLEMENTED)

A bounded LLM *proposal* layer (`memory/proposals.py`) now sits on top of the
deterministic Slice 4 extractor. Phase 11–14 invariants are unchanged; the
LLM is a **proposal generator only**, never a decision-maker or a writer:

- The model proposes ONLY `statement`, `kind`, `temporal_scope`,
  `confidence`, and the `evidence_message_ids` it saw in ONE bounded
  conversation window. It does NOT decide accept/reject/defer/approval,
  conflicts, supersession, sensitivity, or permission, and it never writes.
- Every proposal still flows through the exact same single policy-gated write
  path: validated `MemoryProposal` → `to_memory_candidate` → `MemoryPolicy`
  → `AutomaticMemoryCurator` → `propose_memory` gate →
  `MemoryService.apply_candidate`. Do NOT add any second write path; do not
  bypass `PolicyEngine`; do not touch SQLite from the proposal layer; do not
  weaken `MemoryPolicy` (secrets stay hard-rejected; keyword-sensitive content
  stays `require_approval`).
- Durability/relevance/specificity/utility come from a fixed application
  table; `assertion_status`/`recurrence`/evidence refs are set by the
  application. Deterministic trigger phrases override the model's labels
  ("wants to …" → goal/current; recurring timeframes → recurring; past tense
  → historical). `LLM confidence` is a policy input, not authorization.
- Strict output contract: JSON object with a `proposals` array
  (`MEMORY_PROPOSAL_SCHEMA`, validated via the existing `OllamaClient.chat`
  `format=` surface). Malformed structure raises
  `MalformedMemoryProposalError` (whole batch → zero candidates); invalid
  items are dropped and counted. Output is never repaired or guessed.
- Deterministic conversion guards in `to_memory_candidate`: referenced
  evidence must exist in the bounded window and include at least one
  user-authored self-assertion (questions/requests are not user facts);
  statements must canonicalize to a "The user …" form; sensitive forms,
  questions, and negations are dropped.
- Bounded I/O: one model call per conversation window (`max_messages`,
  `max_prompt_chars`), `MAX_PROPOSALS_PER_BATCH`/`max_candidates_per_unit`/
  `MAX_EVIDENCE_PER_PROPOSAL` caps, `CONVERSATION_SOURCE_TYPES`-scoped reads
  with limits/offsets. Never send the corpus to the model in one call.
- Failure behavior: model errors/malformed output retry at most
  `max_retries` times; persistent failure yields zero candidates for that
  batch with count-only reasons (`ollama_error`/`malformed_output`/
  `model_error`). Empty `proposals: []` is a successful no-op. A denied
  `memory.write` gate still raises `ApprovalRequiredError` and writes nothing
  (mandatory regression test).
- Provenance stays id-only and idempotent (reconciliation merges, reruns do
  not duplicate). Diagnostics aggregate-only: prompts, responses, and
  candidate statements are never logged or printed.
- `LLMConversationMemoryIngestor` / `LLMMemoryProposalExtractor` are library
  API only — NOT wired into the CLI, NOT registered in
  `create_default_registry`; the production chat path remains interactive-only.
- Do not add other LLM-proposal routes, do not auto-run LLM proposals without
  explicit invocation, and keep the deterministic Slice 4 extractor in place
  (the LLM layer is additive, not a replacement).

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

PHASE 16 — DURABLE, RESUMABLE FULL-CORPUS MEMORY CURATION (IMPLEMENTED)

A durable, resumable curation layer (`memory/curation.py`) now turns
already-ingested conversations into memories through the exact same
policy-gated write path as every other memory feature. Phase 11–15 invariants
are unchanged; curation adds **no new write route**:

- Every candidate flows deterministic `MemoryPolicy` → `AutomaticMemoryCurator`
  → `propose_memory` gate → `MemoryService.apply_candidate`; a denied
  `memory.write` gate still raises `ApprovalRequiredError` and writes nothing
  (mandatory regression test). No raw SQL against memory tables; no second
  write path.
- Runs are **idempotent** (reruns reconcile evidence onto the same memories),
  **resumable** (`memory curate --resume` completes exactly the unfinished
  units of the latest run for a source/extraction pair: completed units are
  skipped, stale `running` units recovered, `failed` units retried with
  incremented attempts), and **durable** (unit progress is checkpointed before
  any model call; a failed/timeout unit never aborts a run). A run that ends
  with failed units is recorded `failed` (failure_reason `unit_failures`) so it
  remains resumable; a wholly successful run is `completed` (terminal).
- `dry_run` analyzes the window (policy + reconciliation only) and creates NO
  rows and NO writes; `dry_run` and `resume` are mutually exclusive.
- The only content-bearing table is the explicit review queue
  (`memory_curation_review`): candidates the policy escalates
  (`require_approval`, e.g. keyword-sensitive salary material) are parked there
  with id-only evidence and never auto-written. Run/unit checkpoints store
  identifiers, counts, hashes, timestamps, and statuses only.
- Bounded I/O everywhere: `ConversationStore.list_conversations` /
  `list_messages` paging with `limit`/`offset`, `max_messages`,
  `max_prompt_chars`, `max_candidates_per_unit`, `max_retries`,
  `max_model_calls` (0 = unlimited), and a per-unit wall-clock
  `unit_timeout_seconds` (one worker thread per unit).
- Reports are aggregate-only (`CurationReport.summary()`): totals, decision
  counts, error reasons, versions — never statements, unit ids, or
  prompt/response text. Versioning: extractor/policy/prompt hashes recorded on
  every run row.
- CLI surface is `personal-ai memory curate|runs|review` (kept separate from
  the `--ingest --memory` flag and from `personal_ai.execution.cli`). `review`
  is the only command that prints statement text (the minimal surface for
  human adjudication). No file, document, or corpus source is wired yet, and
  curation is NOT part of `create_default_registry` — the production chat path
  remains interactive-only.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

PHASE 17 — UNIFIED FULL-CORPUS MEMORY CURATION (IMPLEMENTED)

Slice 6's durable, resumable curation is now source-independent. Phase 11–16
invariants are unchanged; **no new write route exists**.

- Source adapters (`memory/adapters.py`) discover bounded `CurationUnit`
  objects and derive `MemoryCandidate` instances that flow through the exact
  same single write path as conversations: deterministic `MemoryPolicy` →
  `AutomaticMemoryCurator` → `propose_memory` gate →
  `MemoryService.apply_candidate`. Adapters never write SQLite; adapters never
  touch memory tables. A denied `memory.write` gate still raises
  `ApprovalRequiredError` and writes nothing.
- `CurationAdapterRegistry` maps `CurationConfig.source_type` to a single
  adapter; the runner resolves it in `run()`. `CurationUnit` carries
  `source_id`/`source_version`/`signal`, and checkpoints record
  `source_version` (guarded ALTER TABLE). Versioning is adapter-aware:
  `CurationAdapter.version(extraction)` yields per-source extractor versions
  and, for LLM document proposals, the prompt hash.
- Supported sources and behavior:
  - **chatgpt/gemini** — existing conversation adapter (unchanged).
  - **email** — one unit per recurring non-webmail sender domain (≥5 emails,
    ≥2 distinct months); deterministic mode is metadata-only (never reads
    subject/body/chunks) and proposes a recurring `interest` candidate with
    id-only, one-ref-per-month evidence (≤5 refs); LLM mode adds a bounded
    representative-email window through `DocumentProposalExtractor`.
  - **financial** — counted only. Zero candidates and ZERO model calls in
    both modes; financial content is never sent to the model.
  - **document** (generic docs) — deterministic mode proposes nothing and
    never loads chunks; LLM mode shows the model bounded document windows and
    gates every proposal on `allowed_document_ids` (strict JSON
    `DOCUMENT_PROPOSAL_SCHEMA`, malformed output retried ≤ `max_retries`, then
    the unit fails).
  - **workout / activity** — deterministic-only adapters (single bounded
    aggregate unit each) reusing the conserved corpus extractors
    (`extract_workout_routine` / `extract_activity_patterns`); behavioral
    provenance means the policy defers them; LLM mode raises
    `CurationConfigError`.
- Adaptive LLM gating: `--min-signal` and `--sample` downgrade low-signal /
  over-budget LLM units to deterministic processing (counted
  `units_signal_skipped`); `_apply_gate` never calls the model for a
  downgraded unit.
- Bounds hold everywhere: `max_records_per_source`-equivalent caps per
  adapter (`_MAX_*`), `max_messages`/`max_prompt_chars`/
  `max_candidates_per_unit`/`max_retries`, run-wide `max_model_calls`, per-unit
  `unit_timeout_seconds`. Diagnostics and run/report checkpoints stay
  aggregate-only; review-queue rows remain the only statement-bearing table.
- CLI (`personal-ai memory curate`): `--source` (chatgpt/gemini/email/
  financial/document/workout/activity, default chatgpt), `--mode` (alias of
  `--extraction`), `--limit`/`--max-units` alias and `--offset`,
  `--max-messages`, `--max-model-calls`, `--max-retries`, `--min-signal`,
  `--sample`, `--unit-timeout`. The registry is built conditionally so only the
  request's source store tables are created. Curation is NOT part of
  `create_default_registry`; the production chat path remains interactive-only.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

==================================================
IMPORTANT IMPLEMENTATION WORKFLOW
==================================================

Before changing code:

1. Inspect the existing implementation.
2. Inspect relevant tests.
3. Identify the smallest coherent slice.
4. Implement it.
5. Add/update tests.
6. Run:

       uv run pytest
       uv run ruff check .
       uv run ruff format --check src tests

7. Review the diff.

Do not proceed to an unrelated phase if the current phase is broken.

Keep each change small enough to review.

Do not silently change APIs without updating tests.

Do not delete existing tests merely because an implementation changes.

==================================================
GIT
==================================================

Do not commit changes automatically.

At the end of each coherent implementation slice, report:

    What changed
    Why
    Tests added
    Test results
    Ruff results
    Files changed
    Suggested commit message
    Next recommended slice

The human will decide when to commit.

==================================================
FIRST TASK
==================================================

Do NOT start implementing the entire architecture.

Start with PHASE 2:

    Document model
    DocumentChunk model
    stable document identity/content hash
    minimal SQLite-backed document storage

Before writing code:

    inspect the existing repository
    inspect current tests
    propose the minimal design

Then implement only that slice.

Requirements:

    typed
    testable
    local-first
    no network
    no Ollama dependency
    no vector database yet
    no LangChain/LlamaIndex
    no speculative abstractions

When complete, run the full test/lint/format suite and report the result.

STOP after completing this slice and wait for further instructions.
