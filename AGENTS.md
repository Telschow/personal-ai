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
PHASE 18 — EXCEPTION-ONLY MEMORY REVIEW + CURATE-ALL (IMPLEMENTED)
==================================================

Added explicit human review surface for escalated memory candidates and
corpus-wide bounded curation orchestration.

- Review queue schema extended: `category` (require_approval/conflict),
  `reason`, and `candidate_json` columns with guarded ALTER TABLE migrations.
- `MemoryReviewService`: approve/reject through the SAME policy-gated
  `propose_memory` path (no second writer). Approve re-runs deterministic
  policy, then routes through `AutomaticMemoryCurator` with interactive
  approver bound to the specific review row. Reject writes nothing.
- Conflicts now queue for review (category `conflict`) in addition to being
  counted — never silently written.
- `curate-all` command: `CorpusCurationOrchestrator` sequences all 7 source
  adapters with per-source checkpoints, source isolation, adaptive/deterministic
  modes, aggregate-only reports (memory stats, evidence accumulation, review
  counts).
- CLI: `memory review --show --category --approve --reject --note` (default
  aggregate-only); `memory curate-all --mode adaptive|deterministic --sources --max-model-calls --resume --dry-run`.

PHASE 20 — MULTILINGUAL SECURITY + UNICODE RETRIEVAL FOUNDATION (IMPLEMENTED)
==================================================

Security and Unicode foundation for processing the user's English/German/Spanish
corpus. No new write paths; LLM remains proposal-only.

P0-1 Multilingual Secret/PII Detection:
- Extended `MemoryPolicy._SECRET_KEYWORDS`, `_HIGHLY_SENSITIVE_KEYWORDS`,
  `_SENSITIVE_KEYWORDS` with German and Spanish equivalents.
- Structural detectors (language-independent): IBAN, JWT, API keys, AWS keys,
  GitHub tokens, credit-card sequences, private-key headers remain unchanged.
- Keywords cover: password/Passwort/contraseña, bank account/Bankkonto/cuenta
  bancaria, credit card/Kreditkarte/tarjeta de crédito, medical/medizinische
  diagnosis/diagnóstico médico, SSN/Sozialversicherungsnummer/seguridad social,
  passport/Reisepassnummer/pasaporte, salary/Gehalt/salario, etc.
- Case/accent-insensitive matching via NFKC + casefold.

P0-2 Unicode-Safe Tokenization:
- Replaced ASCII-only `[a-z0-9]+` with Unicode-aware `[\p{L}\p{M}\p{N}]+`
  using the `regex` package (stdlib `re` lacks Unicode property escapes).
- Centralized `_normalize_for_tokenize()` (NFKC + casefold) used by
  `MemoryRetriever`, `tokenize()`, and `MemoryReconciler.normalize_text()`.
- FTS5 uses default unicode61 tokenizer (no schema migration needed;
  existing data compatible).
- Reconstruction uses original Unicode forms; no transliteration (München
  stays München).

P0-3 Review CLI Fixes:
- Fixed SQLite syntax error in `list_pending_review` / `list_review`
  (missing space before WHERE clause).
- Tests updated for aggregate-only default + `--show` flag behavior.

Tests: 2202 passing. Ruff clean. Format clean. No production DB modified.
==================================================

PHASE 21 — MULTILINGUAL DETERMINISTIC CONVERSATION EXTRACTION (IMPLEMENTED)
==================================================

Deterministic conversation-memory extraction now fully covers English, German,
and Spanish user messages. Phase 11–20 invariants (single policy-gated write
path, LLM proposal-only, content-free diagnostics) are unchanged.

- `_detect_language` (`memory/conversations.py`) scores single words AND word
  pairs against per-language trigger sets (DE/ES/EN); the English baseline
  skew is removed — good short-sentence detection without biasing toward
  English. Unmatched text returns UNKNOWN (English rule fallback).
- Spanish pro-drop support: `_spanish_is_first_person` accepts explicit
  `yo|mi|mis|me` OR a curated first-person verb form (present/preterite/
  imperfect) when not preceded by a determiner/possessive (handles noun
  homographs like "el trabajo en Google"). German/English still require an
  explicit first-person pronoun.
- `_canonical_statement` gained Catalan-free pro-drop rewriting: sentences
  beginning with a first-person verb become "El usuario {3rd-person verb}
  ...", e.g. "Trabajo en Google" → "El usuario trabaja en Google",
  "Quiero mudarme a España" → "El usuario quiere mudarse a España".
  German "Mein Name ist/heißt" → "Der nutzer heißt ...".
- `_RECURRING_PATTERNS[ES]` accepts plural forms ("todos los días",
  "cada semana", ...); DE education rule fixed to "studiere"; DE/ES trigger
  sets extended with conjugated past forms and phrase triggers.
- Weekday recurring constructs recognized per language and mapped to
  `habit`/recurring only for clearly-recurring forms: EN "every Monday"/"on
  Wednesdays"/"sundays", DE "jeden Montag"/"montags", ES "cada lunes"/"los
  domingos". One-off single-day references (EN "on Monday", DE "am Montag",
  ES "el lunes" — meetings, birthdays, appointments) never map to a recurring
  habit.
- `_candidate_for` routes ES through `_is_first_person` (pro-drop gate) via
  `_is_first_person(raw, lang)`; all other gates are unchanged.
- `_canonical_statement` gained a default `lang=EN` so the LLM-proposal layer
  (`memory/proposals.py`) keeps working unchanged.

Tests: ~66 multilingual conversation tests (German/Spanish identity, work,
location, goal, skill, habit, education; negation/question/request exclusions;
sensitive rejection; mixed-language conversations) plus weekday-recurring
coverage and a Unicode tokenization matrix (`tokenize()` keeps `München` as
`münchen`, never transliterates to ASCII). Full suite: 2475 passing. Ruff
clean. Format clean.
==================================================

PHASE 22 — BOUNDED, AGGREGATE-ONLY REAL-CORPUS AUDIT + DRY-RUN INGESTION
VALIDATION (IMPLEMENTED)
==================================================
(Note: the project chronology jumps from Phase 21 to Phase 25 in this file;
Phase 22's production-memory audit/validation tooling is documented here for
completeness. Phase 11–21 and 25–30 invariants are unchanged.)

A read-only, bounded, aggregate-only audit validates the Phase 20/21
multilingual (EN/DE/ES) deterministic memory stack against a deterministic
sample of raw conversation exports (ChatGPT + Gemini). **No new write path,
no ingestion into production, no LLM proposal call.**

- `personal-ai memory corpus-audit --source chatgpt|gemini --path <export-dir>
  [--limit N] [--max-messages N] [--scratch-db PATH] [--json]` (lib:
  `memory/corpus_audit.py`, `run_corpus_audit`) reads ONLY raw export files
  through the existing loaders/extractor/policy. It has NO `--database` flag,
  so a production database is structurally unreachable; a production-style
  `--database` argument is rejected by argparse (exit 2).
- Sample boundary: `MAX_CONVERSATIONS_PER_SOURCE = 25` conversations/source
  default, discovery is deterministic (sorted), messages capped via
  `--max-messages` (default 1000). The full corpus count is reported
  (`conversations_available`) without materializing per-message rows.
- `extract()` / `_candidate_for()` accept an optional `skip_reasons`
  content-free counter (message-level: `unsupported_source_type`,
  `inactive_branch`, `non_user_role`, `duplicate_candidate`; sentence-level:
  `too_long`, `question`, `not_first_person`, `negation`, `you_toward`,
  `quotation`, `sensitive_form`, `sensitive_keyword`, `request`, `model_direct`,
  `statement_too_long`, `no_rule_match`). Default-off, backward compatible.
- Aggregate-only report shape (§): source_type, conversations available/sampled,
  messages sampled, user messages, `languages` (en/de/es/unknown/mixed),
  `candidates` + by_kind + by_language + `recurring` + temporal, `skip_reasons`,
  `policy_decisions` + `sensitivity` + `secret_rejected` + `require_approval`
  (via `MemoryPolicy().evaluate`, pure, no writes), `evidence_valid/invalid`
  provenance counts (every evidence ref must point at a sampled user message),
  Unicode/tokenization health (non-ASCII, umlaut/accent messages, token totals,
  zero-token gaps), Phase 21A ES verb-gap occurrence counts (`hago|juego|corro|
  nado|cocino` — measured only, NEVER implemented), and a `model_calls: 0` /
  `llm_proposal_layer: not_exercised` note. `CorpusAuditReport.summary()` is
  count-only and JSON-serializable; content/identifiers/statements/evidence/
  prompts/secrets/sensitive values are never printed or returned.
- Optional scratch SQLite file (`--scratch-db`, or a disposable tempfile by
  default): seeds the sampled conversations/messages and runs the EXACT
  production write path (`ConversationMemoryIngestor` →
  `AutomaticMemoryCurator` policy gate → `MemoryStore`) twice, then compares
  `MemoryStore.statistics()` counts. Idempotency means zero memory/evidence
  growth on the second pass. A production database is never opened; with no
  scratch path the audit performs zero writes and creates zero files.
- Real-corpus pilot (bounded): ChatGPT 469 available → 25 sampled, 131 user
  messages → 1 goal candidate; Gemini 170 available → 25 sampled, 122 user
  messages → 7 candidates (6 goal, 1 relationship), 5 distinct memories. Both
  idempotent (pass1 == pass2), `secret_rejected: 0`, `require_approval: 0`,
  `evidence_invalid: 0`, model_calls 0. Phase 21A ES verbs measured at 0
  occurrences in the sample. Production DB hashes unchanged after the audit.
- Do NOT wire corpus-audit into `create_default_registry`, do not run LLM
  proposals, and do not make the audit write to any live corpus store.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

==================================================
PHASE 23 — UNICODE TOKENIZATION CLOSURE: VARIATION-SELECTOR LEAK FIX +
ZERO-TOKEN AUDIT METRIC CORRECTION (IMPLEMENTED)
==================================================

Phase 23 root-causes the Phase 22 zero-token Unicode finding and corrects the
audit metric. Phase 11–22 and 25–30 invariants (single policy-gated write
path, LLM proposal-only, security, provenance) are unchanged.

- **Tokenization is NOT the security authority.** `MemoryPolicy` still scans
  the raw statement text; `TokenPolicy` is a pure, deterministic extractor
  that *never* accepts/rejects or writes memory. All PHASE 1–22 policy tests
  unchanged and passing.

- **Tokenizer refactor (`memory/tokenizer.py`)** — single shared Unicode
  lexical primitive used by `MemoryRetriever` (`_TOKEN_RE`,
  `_normalize_for_tokenize`) and `MemoryReconciler` (`_normalize_text`, via
  `_lexical_normalize_text`): NFKC + casefold + **variation-selector strip**
  then `regex` tokenization with `[\p{L}\p{M}\p{N}]+`. Fixes the only genuine
  tokenization leakage: variation selectors (U+FE00–U+FE0F, U+E0100–U+E01EF),
  which are `Mn`-category combining marks matching `\p{M}`, no longer leak junk
  tokens (e.g. `"❤️"` -> `("️",)` became `()`; `"España ❤️"` is
  `("españa",)`). `tokenize()` remains importable from
  `personal_ai.memory.retriever` (historical callers) via `__all__`.

- **Zero-token semantics corrected (`memory/corpus_audit.py`).** Phase 22's
  `zero_token_messages` metric conflated two distinct phenomena:
  (1) messages that tokenize to **zero tokens** (lexically empty input:
  emoji/symbol/punctuation/whitespace/format — expected) and
  (2) messages whose non-ASCII letters are NFKC/casefold-**folded to ASCII**
  (fullwidth/alphanumeric-symbol letters, `ß`->`ss`, ...) which tokenize *fine*
  to ASCII tokens. The latter are now counted separately as
  `ascii_folded_messages` and are NOT an error. `zero_token_messages` now
  counts true zero-token gaps only, classified into the fixed vocabulary
  (`emoji_only`, `symbol_only`, `punctuation_only`, `symbol_punctuation`,
  `whitespace_only`, `format_or_other`, `lexical_unicode`, `unknown`);
  `lexical_unicode > 0` is the only `unicode_errors` entry (genuine defect).

- **Real-corpus result (reproduced, aggregate-only):** ChatGPT (25 sampled,
  131 user msgs) and Gemini (25 sampled, 122 user msgs) now report
  `zero_token_messages: 0`, `zero_token_by_category: {}`,
  `ascii_folded_messages: 2` each, `unicode_errors: []`. The four Phase 22
  "zero-token" messages were all ASCII-folded lexical messages (fullwidth/
  mathematical letters), not empty tokenization — the prior metric was the
  defect, not the tokenizer. The VS-leak fix is confirmed live: ChatGPT
  `tokens_total` 2919→2917 and `unicode_tokens` 28→26 (2 junk `️` tokens
  removed); Gemini unchanged at 5196/128.

- **Idempotency/security/production safety unchanged:** scratch-DB two-pass
  idempotency holds (pass1 == pass2, memories/evidence growth 0, ChatGPT
  candidates 1, policy `accept` 1). Production DB hashes unchanged after all
  audit runs. Corpus-audit is still read-only with NO `--database` flag; do
  not wire it into `create_default_registry`; do not run LLM proposals.

- **Tokenization is lexical-preservation, not segmentation.** A Han run like
  `東京に行きたい` tokenizes as one contiguous token (no Jieba/MeCab/spaCy/
  segmentation); multilingual *semantic* retrieval, embeddings, translation,
  and transliteration remain explicitly out of scope. `München` stays
  `münchen` (never transliterated); `España`/`München`/`café` stay distinct
  from `Espana`/`Munich`/`cafe`; `Straße`/`STRASSE` unify via casefold (`ß`->
  `ss`); `café` == `cafe` never (accent distinction preserved).

- Tests: `tests/test_memory_unicode_tokenization.py` (44 tests: German/Spanish
  lexemes, combining sequences, no-transliteration guarantees, CJK/Japanese/
  Korean non-zero, emoji/symbol-only zero-token, variation-selector no-leak,
  fullwidth/alphanumeric-symbol ASCII-folding, numbers fullwidth + Arabic-
  Indic, reconciliation round-trips, accent distinction, secret-rejection
  independence, classifier buckets, audit folded/zero-token semantics) plus
  `tests/test_memory_reconcile.py` and existing retrieval/audit suites. Full
  suite: 2553 passing. Ruff clean. Format clean.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff clean).

PHASE 25 — REVIEW-QUEUE DEDUPLICATION + RESUME SELECTION (IMPLEMENTED)
==================================================

Two P3 reliability fixes lay on top of Phase 24's pilot findings. Phase 11–24
invariants (single policy-gated write path, LLM proposal-only, security,
provenance) are unchanged.

- Review queue is idempotent: `CurationStore.enqueue_review_if_missing` uses
  an atomic `BEGIN IMMEDIATE` check-and-insert on the stable obligation tuple
  `(source_type, category, reason, kind, temporal_scope, statement,
  evidence_json)`. Reruns never grow the queue; a resolved
  (approved/rejected/expired) row is never silently re-opened; the same
  statement backed by different messages stays distinct obligations.
- Deduplication is mechanical, never a security classifier: secret candidates
  stay hard-rejected before the queue is consulted (zero review rows, zero
  writes); keyword-sensitive candidates park in the approval queue exactly
  once per obligation.
- `--resume` targets the newest run with `status IN ('running','failed')` per
  source/extraction (`CurationStore.resumable_run`), so an older interrupted
  run is recoverable even when a newer completed run exists; completed runs
  are terminal and never resumed. No run-id CLI flag.
- `review_deduplicated` is counted in run/per-source/`curate-all` reports
  (aggregate-only). Tests cover the dedupe matrix, resume matrix, combined
  resume+conflict, security/multilingual/provenance regressions, and CLI
  surfaces; all hermetic.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

==================================================
PHASE 26 — REVIEW-QUEUE ADJUDICATION (EXCEPTION-ONLY HUMAN APPROVAL) (IMPLEMENTED)
==================================================

The durable review queue is a validated, exception-only human adjudication
boundary. Phase 11–25 invariants (single policy-gated write path, LLM
proposal-only, security, provenance) are unchanged; **no new write route
exists**.

- Review rows transition `pending -> approved|rejected|expired`; all three are
  terminal, and a repeated decision on the same row is a `not_pending` no-op
  that never writes. A resolved obligation is never silently re-opened on rerun
  (Phase 25 dedup).
- Approval does NOT trust the stored escalation: `MemoryReviewService.approve`
  reconstructs the candidate from the row, re-evaluates `MemoryPolicy`
  deterministically, and routes through `AutomaticMemoryCurator` (row-pending
  approver bound for EVERY policy outcome, `auto_approver=approver` alongside
  `interactive_approver=approver`) → `propose_memory` gate →
  `MemoryService.apply_candidate`. A `DEFER`/`REJECT` at decision time (e.g.
  the stored `candidate_json` was tampered into secret content) expires the row
  with a count-only reason and writes ZERO memories. Reject writes nothing and
  is terminal.
- Concurrent adjudication is serialized per service instance
  (`threading.RLock`) plus a guarded pending→terminal UPDATE
  (`WHERE id=? AND status='pending'`), so two approvers on the same row yield
  exactly one write and one `not_pending`. Reconciliation remains idempotent.
- CLI wiring uses `MemoryService(MemoryStore(connection))` on the shared
  connection (`_run_memory_review`); `memory review --approve N|--reject N`
  are the only decision surfaces and operate one row at a time (no batch
  mode).
- Secret content stays hard-rejected before the queue is consulted (zero
  review rows); tampering a pending row into secret content expires it with no
  write; EN/DE/ES candidates take the identical authorization path; evidence is
  preserved id-only and merged (never duplicated/fabricated/translated).
- Tests: `tests/test_memory_review_service.py` (35 adjudication tests, the
  source of truth for the state machine/adversarial matrix) and CLI
  approve/reject end-to-end in `tests/test_cli_memory_curation.py`. Synthetic
  pilot lives at `/tmp/opencode/phase26_pilot/pilot.py` (scratch DB, deleted;
  aggregate-only `phase26_summary.json` kept).
- Known limitations (deferred, invariants unchanged): memory-write and row
  transition are not one cross-connection transaction (a crash leaves a
  recoverable memory-written/review-pending state that re-approve fixes via the
  reconciler); the per-instance lock does not serialize across processes
  (cross-process order relies on the guarded UPDATE + reconciler merge); no
  audit log of decisions beyond `reviewed_at`/`review_note`.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

==================================================
PHASE 27 — REVIEW ADJUDICATION TRANSACTION + AUDIT-LOG HARDENING (IMPLEMENTED)
==================================================

The human adjudication boundary is now crash-safe and durably auditable.
Phase 11–26 invariants (single policy-gated write path, LLM proposal-only,
security, provenance, exception-only approval) are unchanged; **no new write
route exists**.

- **Atomic adjudication (one transaction):** `CurationStore` and `MemoryStore`
  share a single SQLite connection, so the whole decision (pending-row
  validation, deterministic policy re-evaluation, the policy-gated memory
  write + reconciliation, the audit event, and the pending → terminal review
  transition) runs inside one explicit `BEGIN IMMEDIATE` ... `COMMIT`. The
  stores' internal auto-commits are deferred while the transaction is open by
  `_AdjudicationConnection` (a transparent connection proxy), so the unit is
  all-or-nothing. A crash/kill before `COMMIT` rolls everything back (no
  `approved`/no-memory, no pending/duplicate-memory states); after `COMMIT` it
  is fully durable. A failed `COMMIT` returns a conservative
  `{"outcome":"error"}` and writes nothing. `_Component_connection` is passed
  to both stores and to `MemoryReviewService`.
- **Durable adjudication audit trail:** every terminal decision (approved /
  rejected / expired) appends exactly one content-free event to
  `memory_review_audit` (review_id, action, outcome, actor, policy_category,
  memory_id, statement_hash = SHA-256 digest of the statement, created_at) —
  never statements/evidence. A repeated decision is `not_pending` and writes
  no further event; `not_found` writes none. Read by `review_audit` /
  `review_audit_counts` (aggregate-only).
- **Concurrency/crash guarantees:** per-instance RLock + guarded
  pending→terminal UPDATE + SQLite `BEGIN IMMEDIATE` make two concurrent
  approvers on the same row (same or separate connections) yield exactly one
  approval, one memory, one audit event, one `not_pending`. Because the memory
  write and row transition are one durable transaction, the Phase 26
  "memory-written/review-pending recoverable state" known limitation is
  **fully fixed**: there is no window where a committed decision splits the
  write from the transition.
- Do not regress atomicity by opening separate connections for the memory write
  and row transition; keep the single shared transaction boundary.
- Tests: `tests/test_memory_review_transaction.py` (audit exactly-one,
  crash-rollback, durable-commit-across-connection, commit-failure
conservative error, raw SQLite crash atomicity, two-connection concurrency)
   plus the Phase 26 adjudication suite with the harness moved onto the proxy
   connection (all existing decisions now exercise the atomic path).

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

PHASE 28 — DURABLE REVIEW AUDIT OBSERVABILITY (IMPLEMENTED)
===========================================================

The Phase 27 audit trail now has a read-only, privacy-safe observability surface
(`memory review --audit` / `--audit-counts`) plus aggregate adjudication counts
in `curate-all`. Phase 11–27 invariants (single policy-gated write path, LLM
proposal-only, security, provenance, exception-only approval, atomic
adjudication) are unchanged; **no new write route exists**.

- The audit CLI is **read-only and never approves**: no `--approve-all` /
  `--reject-all` / `--auto-approve` / `--approve-safe`, no automatic
  adjudication, single-row-decisions only. It only ever *reads* the
  `memory_review_audit` table.
- The audit surface emits **operational metadata only** (`review_id`, `action`,
  `outcome`, `actor`, `policy_category`, `memory_id`, `created_at`). The
  `statement_hash` digest and the internal audit row `id` are NOT exposed;
  statements, evidence, `candidate_json`, prompts, model output, secret and
  sensitive values are never printed by any audit surface (JSON or human).
  Empty DB renders `events: 0`, never fabricated rows.
- Adjudication history aggregates (`review_approved/rejected/expired`) come
  ONLY from the authoritative audit table (a global snapshot, distinct from
  pending `review_queued`/`review_deduplicated`); do not fabricate them from
  queue counts and do not add them per-source (the audit trail has no run id).
- Approve/reject results carry `audit_recorded=true|false`; repeated terminal
  decisions stay `not_pending` with `audit_recorded=false` and create no
  additional audit event (exactly-one invariant).

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

PHASE 29 — READ-ONLY MEMORY REVIEW AUDIT TOOL (IMPLEMENTED)
===========================================================

The durable Phase 27 audit trail is now observable to the agent/tool layer
through a strictly read-only tool. Phase 11–28 invariants (single policy-gated
write path, LLM proposal-only, security, provenance, exception-only approval,
atomic adjudication, Phase 28 CLI observability) are unchanged; **no new write
or adjudication route exists**.

- The tool (`memory_review_audit`, `agents/tools.py`) is a read-only
  operational view registered behind a distinct `review.audit.read` permission
  (`risk=READ`, `mutates_state=False`, `deterministic=True`), consumed by the
  researcher agent's least-privilege policy. It is registered only when a
  `MemoryReviewService` is wired into `build_default_agent_tools`.
- Two operations only: `counts` (aggregate-only `events`/`actions`/`outcomes`/
  `policy_categories`/`actors`) and `recent` (bounded, newest-first operational
  metadata: `review_id`, `action`, `outcome`, `actor`, `policy_category`,
  `memory_id`, `created_at`, default limit 20, hard max 200). Both consume the
  existing `MemoryReviewService.audit()`/`audit_counts()` → `CurationStore`
  surface; there is **no second audit implementation** and **no SQL in the
  tool**.
- The tool is mechanically read-only and cannot approve/reject/expire/reopen,
  cannot mutate memories/reviews/audit rows, cannot invoke
  `MemoryService.apply_candidate()`, cannot bypass `MemoryPolicy`, and cannot
  execute arbitrary SQL. Unknown operations are rejected; invalid/bool/float
  limits are rejected and `limit` is hard-capped; unknown or mutation-looking
  parameters (`approve`/`reject`/`review_id`/`include_*`/`debug`/`raw`/`sql`)
  are ignored, never honored.
- Privacy contract equals Phase 28: statements, evidence, `candidate_json`,
  prompts, model output, `statement_hash`, `review_note`, secrets, and
  sensitive values are never returned, logged, or raised in error text. The
  handler defensively re-projects every result to the safe field set.
- Do NOT add any agent-facing adjudication tool or parameter
  (`memory_review_approve`/`reject`/`expire`/`resolve`, `approve=`/`reject=` on
  the audit tool, auto-approval by policy_category/confidence/language/actor).
  The human review boundary stays intact; the agent layer observes, never
  decides.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

PHASE 30 — DETERMINISTIC REVIEW-AUDIT TIME-WINDOW FILTERING (IMPLEMENTED)
=========================================================================

Time-window filtering is now available over the durable review audit trail.
Phase 11–29 invariants (single policy-gated write path, LLM proposal-only,
security, provenance, exception-only approval, atomic adjudication, read-only
agent audit tool) are unchanged; **no new write, adjudication, policy,
curation, or transaction route exists**.

- The authoritative timestamp is `memory_review_audit.created_at`; `since` is
  inclusive (`created_at >= since`) and `until` is inclusive
  (`created_at <= until`); neither → all events with the existing limits;
  `since > until` is rejected deterministically before any query (no silent
  swap, no empty-result fallback). No migration/rewrite of historical rows.
- Accepted bound forms: `YYYY-MM-DD` (midnight UTC), `YYYY-MM-DDTHH:MM:SS`,
  `YYYY-MM-DDTHH:MM:SSZ`, and ISO offsets; naive timestamps are interpreted as
  UTC; every bound is normalized to a canonical UTC string
  (`format_utc_timestamp`) so equivalent instants filter identically
  (`parse_iso_timestamp`/`format_utc_timestamp` in `memory/models.py`).
- CLI: `memory review --audit` and `--audit-counts` accept `--since`/`--until`;
  invalid or reversed bounds yield a non-zero exit code. `recent` keeps its
  default limit 20 and hard max 200 clamp; counts preserve the Phase 28
  aggregate schema (events/actions/outcomes/policy_categories/actors/
  by_outcome/total) computed over the same filtered window.
- Agent tool (`memory_review_audit`, `agents/tools.py`): `counts` and `recent`
  accept `since`/`until` validated by `_parse_and_validate_timestamp`; bounds
  are normalized to UTC and passed to `MemoryReviewService.audit()`/
  `audit_counts()` (no SQL in the tool, no second audit implementation).
  Permission/risk/determinism and privacy posture are unchanged; `recent`
  still exposes only review_id/action/outcome/actor/policy_category/memory_id/
  created_at.
- Stores/services filter in SQL (`_audit_group_counts(column, where, params)`)
  with parameterized `>=`/`<=`; bounds never appear in error text; privacy
  sentinels (`candidate_json`, statements, evidence, prompts, model output,
  `statement_hash`) never leak under any filtered call; empty windows return
  `events: 0`/`[]`, never fabricated rows.
- Do NOT add a migration, a second timestamp column, or a Python-side filter;
  do NOT extend `--since`/`--until` to other flags (curation runs/reports);
  the audit trail remains content-free and time-filtering stays read-only.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

==================================================
PHASE 31 — READ-ONLY AGENT RETRIEVAL TOOLS: get_document + get_memory (IMPLEMENTED)
==================================================

The agent/tool layer gains read-only, policy-gated fetch tools for documents
and durable memories. Phase 11–30 invariants (single policy-gated write path,
LLM proposal-only, security, provenance, exception-only approval, atomic
adjudication, read-only audit) are unchanged; **no new write or adjudication
route exists**.

- `get_document` and `get_memory` are registered in `agents/tools.py` via
  `build_default_agent_tools` (`AgentToolRegistry`) and consumed by the
  researcher/corpus agent policies. Both are non-mutating, deterministic, and
  read-only; GET_DOCUMENT gates on the existing `corpus.search` permission,
  GET_MEMORY on the existing `memory.read` permission — NO new permissions
  were introduced. Policy decisions remain observable through the same shared
  audit trail; denials (`CURATOR`, permission-less agents) surface via
  `PolicyDenialError` with no store/service access.
- `get_document(document_id, chunk_limit=20, capped at 100)` returns the
  document metadata plus a bounded, deterministically ordered chunk window
  (`chunk_index, chunk_id`), or `{"status":"not_found","document_id"}` for an
  unknown id. `get_memory(memory_id)` returns the canonical memory plus
  content-free provenance aggregation (evidence count/kinds, first/last
  evidence timestamps — never evidence ids/bodies/prompts/model output/
  candidate JSON/statement hashes), or `not_found`. Tool parameters are
  validated: a missing/invalid `document_id`/`memory_id` raises
  TypeError/ValueError; unknown and mutation-looking parameters (`approve`,
  `apply_candidate`, `review_id`, `sql`, `curate`, ...) are ignored, never
  honored.
- No SQL anywhere in the tool layer: document/chunk reads go through
  `DocumentStore`/`ChunkStore`, memory reads through `MemoryService`, and the
  handlers never call `MemoryService.apply_candidate`, `create_user_memory`,
  or any curation/adjudication surface. No schema change, no new dependency,
  no network, no LLM/Ollama calls, no automatic curation or ingestion.
- Tools are privacy-preserving: aggregate/operational fields only; document
  statements, memory content, prompts, model output, secrets, and sensitive
  values never appear in results, logs, or error text.

Tests: `tests/test_agent_get_document_get_memory.py` (31 tests).

==================================================
PHASE 32 — CHAT EXPOSURE OF READ-ONLY RETRIEVAL TOOLS (IMPLEMENTED)
==================================================

The Phase 31 read-only fetch tools are now directly addressable from the
interactive chat `ToolRegistry` (`create_default_registry`). Phase 11–31
invariants unchanged; **no new write or adjudication route exists**.

- New `tools/fetch.py` provides `PolicyGatedGetter` (+
  `build_policy_gated_get_handler`): it builds ONE `PolicyEngine` over the
  Phase 31 `AgentToolRegistry` and runs `get_document`/`get_memory` via
  `PolicyEngine.execute(RESEARCHER, ...)` — the researcher is the only agent
  the chat path may impersonate (same pattern as `tools/corpus.py`,
  `tools/workouts.py`, `tools/personal_context.py`), so chat enforcement and
  audit are identical to the execution runtime and Phase 31 differentiated
  access is preserved (no permission bypass; curator/engineers still denied).
- `create_default_registry` gained a trailing `document_store` parameter
  (default None). `get_document` (requires BOTH `document_store` and
  `chunk_store`) and `get_memory` (requires `memory_service`) ToolDefinitions
  are registered only when their dependencies are wired; their handlers run
  entirely inside the policy engine — no direct store/service access from the
  registry layer, no SQL, no write surface. `propose_memory` remains
  approver-gated and is unaffected.
- `cli._connect_agent_registry` now passes `document_store` to
  `create_default_registry`.
- Tool contracts, permissions (`corpus.search` / `memory.read`, no new
  permission), risk/determinism flags, privacy posture, unknown-parameter
  rejection, and not_found semantics are all unchanged from Phase 31.

Tests: `tests/test_chat_get_document_get_memory.py` (20 tests: registration
matrices, RESEARCHER authorization + CURATOR/engineer denials that never touch
stores/services, end-to-end via `create_default_registry`, chunk-limit
passthrough/clamp, deterministic ordering, unknown params ignored,
byte-identical no-write regression, privacy key-set assertions);
`tests/test_cli_wiring.py` updated for the wired chat set (get_document
present, get_memory absent without a memory service). Full suite: 2604 passed;
ruff clean; format clean.

Existing invariants: memory is data never policy; event payloads never carry
content; count/provenance-only diagnostics; tests hermetic (pytest/ruff
clean).

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
