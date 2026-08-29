# Personal AI

Local-first personal knowledge and agent system.

This project turns your personal history — conversation notes, chat logs,
search history, and watched/browsed activity — into a personal history
**retrieval** system you can ask questions of in natural language. It runs
entirely on your own hardware with a local Ollama model. No cloud APIs are
used and your personal data never leaves the machine.

> **Status: ACCEPTED / DEMO-READY proof of concept.**
> This is a POC, not yet a fully autonomous personal assistant. It reliably
> answers grounded, single-domain questions about your stored personal
> history, and is honest about what it does not know. It is **not** a
> general-purpose autonomous agent, and its known limitations are documented
> below.

---

## Table of contents

- [What this is](#a-what-this-is)
- [Current status](#b-current-status)
- [What currently works](#c-what-currently-works)
- [Known limitations](#d-known-limitations)
- [Hardware/model decision](#e-hardwaremodel-decision)
- [Evaluation history](#f-evaluation-history)
- [Current architecture](#g-current-architecture)
- [Current data sources](#h-current-data-sources)
- [Roadmap](#i-roadmap)
- [Missing implementation steps](#j-missing-implementation-steps)
- [Open WebUI integration](#k-open-webui-integration)
- [Local deployment example](#l-local-deployment-example)
- [Usage](#m-usage)
- [Testing](#n-testing)
- [Repository state / git](#o-repository-state--git)

---

## A. What this is

- **Local-first:** everything runs on your machine. The model backend is a
  local Ollama instance.
- **Privacy-oriented:** personal content is never sent to a cloud service,
  never committed, and never dumped into logs. Tests use synthetic fixtures,
  never real personal data. The filesystem workspace is a hard sandbox.
- **Personal-history retrieval:** the core value is *grounded recall* — you
  ask a question and the Agent retrieves your actual records (conversations,
  search/watch/browse events) and answers from them.
- **Agent/tool-based retrieval:** a synchronous Agent loop lets the model
  choose from an explicit, allow-listed set of tools (`search_knowledge`,
  `query_events`, etc.) rather than having retrieval hard-wired to a query.
- **Local Ollama model:** `qwen3.5:9b`.
- **Current POC scope:** grounded personal-history lookup over a
  conversation + temporal-event corpus, with document/PDF ingestion built in
  code but not yet populated.

### What it is NOT

- **Not** a self-hosted ChatGPT/assistant clone with general world knowledge
  as its core. It is a *personal* retrieval system grounded in *your* data.
- **Not** a fully autonomous agent that can be left to run arbitrary tasks.
- **Not** a multi-modal vision system yet (image-heavy PDFs are a planned
  future source slice).
- **Not** a client of any cloud AI API.
- **Not** a vector-database / RAG retrieval product (semantic embeddings are
  infrastructure-blocked; current retrieval is keyword/FTS over determinants).

---

## B. Current status

### ACCEPTED / DEMO-READY POC

Validated baseline:

- Model: `qwen3.5:9b` (fixed deployment choice, see [Hardware/model decision](#e-hardwaremodel-decision))
- Backend: **local Ollama** (GTX 1070, 8 GB VRAM, 15 GB RAM hardware)
- Agent: existing synchronous `Agent` loop (`max_tool_rounds=8`)
- Tool registry: existing `ToolRegistry` with the Phase 23 changes retained
  (see [Evaluation history](#f-evaluation-history))
- Retrieval: existing architecture — keyword/FTS over chunks + conversation
  search + structural event queries. No retrieval heuristics added.
- Prompt: **baseline only** — no extra system prompt is used.
- Access: **CLI** (`python -m personal_ai.cli`) and, as of **Phase 30**, an
  **OpenAI-compatible HTTP API** (`python -m personal_ai.server`) that reuses
  the exact same Agent + ToolRegistry path — suitable as an Open WebUI backend.
- Tests: 1337 passing (no network, no Ollama, no real personal data);
  `ruff check` and `ruff format --check` clean.
- No reproducible Category A (deterministic code-level) retrieval/tooling
  defect has been found.

The POC is **ACCEPTED / DEMO-READY**. `qwen3.5:9b` is the current deployable
local model. The accepted limitation is **Category C model synthesis**: compound
multi-domain questions can drop a domain or drift, which is not considered a
deterministic retrieval/tooling defect. Users should split complex compound
questions when necessary.

The POC passed the acceptance suite (Phase 28) and a user-facing demo suite
(Phase 29) for its intended core scope: grounded lookups, honesty about
missing evidence, and correct temporal summaries.

---

## C. What currently works

Demonstrated capabilities (via the production CLI path against the real
conversation + event corpus):

1. **Grounded knowledge lookup** — "What do I know about BCG?" returns real
   stored knowledge, not a generic essay.
2. **Personal search-history retrieval** — "What did I search for recently?"
   returns actual recent search queries.
3. **Watched-video retrieval** — "What videos did I watch about X?" returns
   real records from the YouTube watch store.
4. **Temporal activity lookup** — "What was my most recent career-related
   activity?" returns a real recent record.
5. **Aggregate temporal summaries** — "Summarize my activity from January
   2026." returns correct, corpus-verified aggregate counts (e.g. total
   events, watched videos, searches).
6. **Negative evidence / "no records" answers** — "Do I have anything about
   fly fishing?" plainly says there is no record, checking multiple sources,
   and does not fabricate.
7. **Grounded factual recall** — cited videos, searches, dates, and counts
   are traceable to real stored records (verified against the corpus).
8. **Local model execution** — the whole loop runs on a local GPU/VRAM
   (warm queries ≈ 13–65 s; typical lookups ≈ 25–31 s).
9. **Evidence-based answers** — the Agent selects sources and the final
   answer is grounded in retrieved evidence.
10. **Avoiding systematic fabrication** — across the acceptance and demo
    suites, no fabricated watched video, search, date, or cross-source
    connection was observed. Where an item was uncertain it was either
    omitted or explicitly hedged.

Example successful questions (see [Usage](#m-usage)):

```
What do I know about BCG?
What did I search for recently?
What videos did I watch about job interviews?
Do I have anything about fly fishing?
Summarize my activity from January 2026.
```

---

## D. Known limitations

### Category C model synthesis — the main limitation

The single most significant known limitation is **model synthesis** on the
current 9B model. `qwen3.5:9b` can reliably:

- retrieve the correct evidence,
- ground individual facts in real records,
- report the absence of evidence honestly,
- answer single-domain, well-scoped questions.

But it can sometimes fail when the question asks it to:

- combine **multiple evidence domains** in one answer,
- synthesize watched-videos **and** notes together,
- **compare** multiple people/topics across sources ("compare what I know
  about BCG and McKinsey"),
- perform broad **open-ended thematic** synthesis.

Observed failure modes (stochastic — they vary run to run):

- drops one requested domain and answers only the other half,
- answers only one half of a compound question,
- drifts into generic advice (losing the personal evidence),
- includes real-but-noisy records (matched on a substring) — though these
  are usually hedged as uncertain, not asserted as fact,
- occasionally produces an **empty final answer** (a silent blank after tool
  calls), and
- shows **stochastic variation** between repeated runs of the same question.

Mixture across repeated runs of a compound question:
clean dual-domain answer / noisy-but-hedged / generic-drift / empty. This is
the accepted, characterized behavior of the 9B model.

> **THIS IS NOT CLASSIFIED AS A RETRIEVAL BUG.** The tools *can* retrieve
> the evidence — the weakness is the final model synthesis step.
> Consequently this limitation is **not** "fixed" by prompt engineering,
> retrieval heuristics, or swapping in another ≤9B model (see
> [Hardware/model decision](#e-hardwaremodel-decision)).

### Temporal limitation

- Simple recent/temporal questions usually work correctly.
- "most recent" / "recent" is **approximate**: the model returns a real,
  recent record, but on different runs it may surface a different one — it
  does not always return the single newest record. Treat such answers as "a
  real, recent record," not a guarantee of recency ordering.
- This is a model/tool-selection behavior, not a demonstrated deterministic
  retrieval defect.

### Empty-answer guidance

Because an occasional empty/generic answer is stochastic, the practical
guidance to users is: **retry the same question** — the next attempt is
usually fine, and **split compound questions** into single-domain
sub-questions. See [Usage](#m-usage) and [`docs/USAGE.md`](docs/USAGE.md).

---

## E. Hardware/model decision

The model was investigated across Phases 25–27. The conclusion is that the
project is **hardware-bounded** and `qwen3.5:9b` is the fixed deployment
choice for the current scope.

- **`qwen3.5:9b` is the preferred model.** It is the most trustworthy of the
  readily-hostable options on grounding and factual recall.
- **`llama3.1:8b` was tested** (Phase 26) and was lighter/faster but
  **materially worse on grounding** — it fabricated or departed from
  evidence more often. It was rejected.
- **A genuinely stronger model was investigated** (Phase 27):
  `qwen3:14b` (Q4_K_M ≈ 9.3 GB) could **not be reliably hosted** on the
  current GTX 1070 (8 GB VRAM) / 15 GB RAM configuration, and download was
  impractically slow (~4 MB/s). The hardware stop-condition triggered.
- Therefore **do not substitute another ≤9B model merely to claim a model
  comparison** — that would not improve capability and would regress the
  measured grounding quality.
- `qwen3.5:9b` remains the deployment choice because it is the strongest
  model that runs reliably on this hardware while meeting the grounding
  requirement for the POC scope.

If multi-domain compound synthesis ever becomes a hard *product* requirement
(rather than a documented POC limitation), that requires a materially
stronger model (≥14B, including a vision model) on adequate hardware, or a
different deployment architecture — out of scope for this hardware.

---

## F. Evaluation history

A concise record of why the architecture is the way it is. (This is a
summary of an intentional evaluation process, not an experiment diary.)

### Phase 23 — Retrieval fixes (RETAINED)

Production changes that remain in the working tree and are part of the
accepted baseline:

- **`ChunkStore.count()`** (`src/personal_ai/storage/chunks.py`): a count of
  indexed chunks.
- **`search_documents` conditional registration**
  (`src/personal_ai/tools/defaults.py`): the narrow chunk-only tool is
  registered only when `chunk_store.count() > 0`, i.e. when there is actually
  indexed document content. On a conversation/event-only corpus the model is
  not shown a tool that can't return anything.
- **Conversation `_match_score` / `_longest_content_run`**
  (`src/personal_ai/storage/conversations.py`): a deterministic scorer that
  rewards a specific multi-word phrase over scattered generic term matches,
  and demotes title/speaker-only matches.

These are retained because they improve retrieval/tool-availability behavior
and are covered by regression tests in the normal (no-Ollama) suite.

### Phase 24 — Controlled baseline evaluation

Established a controlled baseline of the unmodified Agent + tools on
`qwen3.5:9b` to measure grounding, temporal behavior, and synthesis.

### Phase 25 — Prompt grounding experiment — **REJECTED**

A system-prompt attempt to force better grounding. It improved a few narrow
cases but **regressed synthesis and caused instability**, so it was rejected.
The project uses the baseline prompt only.

### Phase 26 — Model comparison: `qwen3.5:9b` preferred

Compared `qwen3.5:9b` against `llama3.1:8b`. Result: **`qwen3.5:9b`
preferred** because it is more trustworthy on grounding.

### Phase 27 — Attempted stronger model — hardware stop

Attempted a genuinely stronger model (`qwen3:14b`). The hardware
stop-condition triggered (insufficient VRAM/RAM headroom; impractically slow
download). No comparison run was possible.

### Phase 28 — Acceptance suite — **ACCEPT**

Ran the full 8-category acceptance suite. Single-domain grounded lookups,
negative evidence, and temporal summaries were reliable; the dominant
failure class was stochastic Category C compound synthesis. **Verdict:
ACCEPT** for the POC scope.

### Phase 29 — POC demonstration & user-facing hardening — **DEMO-READY**

Ran a user-facing demo suite (knowledge recall, activity recall, temporal,
negative-evidence, summary, watch/search history, and a compound-limitation
demonstration), repeated key demos to quantify stochastic variation, and
verified the safety boundary (no fabricated personal records across the
suite). Produced [`docs/USAGE.md`](docs/USAGE.md). **Verdict: DEMO-READY.**

---

## G. Current architecture

Based on the actual source (see `src/personal_ai/`). Mermaid diagram of the
parts that exist today and where the future API/UI layer plugs in:

```mermaid
flowchart TD
    CLI[CLI<br/>--workspace --database --verbose] --> AGENT
    OW[Open WebUI<br/>PLANNED] -.-> API[Personal AI HTTP API<br/>PLANNED, Phase 30]
    API -.-> AGENT

    subgraph AGENT[Agent loop]
        AG[Agent.run(messages)] --> REG[ToolRegistry]
    end

    REG --> T1[search_knowledge<br/>RetrievalService: chunks+extractions+conversations]
    REG --> T2[query_events<br/>EventStore: aggregate/structural events]
    REG --> T3[search_documents<br/>chunk-only, registered only when chunks>0]
    REG --> T4[list_directory<br/>sandboxed filesystem]

    T1 --> KB[(Knowledge: chunks,<br/>extractions, conversations)]
    T2 --> EV[(Events: search/url/video)]
    T3 --> DOC[(Documents/chunks)]
    T4 --> FS[workspace sandbox]

    AG --> OLL[OllamaClient<br/>qwen3.5:9b, local]

    KB --> ING[Ingestion pipeline]
    ING --> SRC[SourceAdapters: keep, notebooklm, gemini, chatgpt, email, filesystem]
```

Parts (existing vs. planned):

- **Data ingestion** (existing): structured `SourceAdapter`s (Keep, NotebookLM,
  Gemini, ChatGPT, email, filesystem) → typed `SourceRecord`s → a single
  generic orchestration (`ingest_source`) → `DocumentIngestor`
  (extract text → classify → persist → chunk → extract structured knowledge
  for text-heavy docs). Idempotent: unchanged inputs produce no new rows.
  Real embeddings are a separate, decoupled backfill step.
- **Corpus/database** (existing): a single SQLite database with tables for
  documents, document chunks (+ FTS5 index), structured extractions,
  conversations (+ messages, attachments), events, and chunk embeddings. All
  deterministic.
- **Retrieval** (existing): `RetrievalService` unifies keyword/FTS over
  chunks, structured extractions, and conversation messages; `EventStore`
  provides structural temporal queries (events, activity summary, top
  searches/channels/videos, per-bucket activity). Semantic/vector retrieval
  is **not** active (embeddings infrastructure-blocked).
- **Agent** (existing): synchronous `Agent.run(messages)` loop over
  `OllamaClient`, calling `ToolRegistry`, bounded by `max_tool_rounds=8`.
- **ToolRegistry** (existing): explicit, allow-listed tools only. The model
  can never run arbitrary code, import modules, or escape the filesystem
  sandbox.
- **Ollama** (existing): provider-independent via `OllamaClient`
  (HTTP boundary, structured tool-call parsing). Chat, structured
  extraction, and (future) embeddings are separate concerns.
- **CLI** (existing): `python -m personal_ai.cli --workspace ... --database
  ... [--verbose] "question"`. Also supports `--search` and `--ingest`.
- **API boundary** (PLANNED, Phase 30): there is currently **no HTTP/server
  layer** anywhere in the repo. The smallest clean boundary is an HTTP API
  that reuses the existing Agent + ToolRegistry wiring (see
  [Open WebUI integration](#k-open-webui-integration) and the phase plan).
- **Open WebUI frontend** (PLANNED, later): a UI that talks to the Personal
  AI API — **not** directly to Ollama.

---

## H. Current data sources

Exactly what is implemented vs. planned. Do not assume sources are live
unless stated.

**Implemented (code exists, exercised by tests):**

| Source | Adapter / path | State |
|---|---|---|
| Google Keep notes | `sources/keep.py` | adapter + corpus-validated (695 records in the original corpus) |
| NotebookLM articles | `sources/notebooklm.py` | adapter + corpus-validated |
| Gemini conversations | `sources/gemini.py` | adapter + corpus-validated (170) |
| ChatGPT conversations | `sources/chatgpt.py` | adapter + corpus-validated (469) |
| Email (Gmail mbox) | `sources/email.py` + `sources/email_normalization.py` | adapter + deterministic Message-ID identity + quote/signature normalization; model-free ingestion (Phase 32) |
| Generic files (txt/md/pdf/png/jpg/jpeg) | `sources/filesystem.py` | adapter + CLI `--ingest file`; corpus-validated (41 PDFs ingested, Phase 31) |
| Chrome history (events) | `sources/chrome_history*.py` → `event_ingestion` | implemented; live in the event corpus |
| YouTube watch/search (events) | `sources/youtube_history*.py` → `event_ingestion` | implemented; live in the event corpus |

**PDF ingestion** (implemented, Phase 31): `documents.extractor.extract_text`
supports PDFs via PyMuPDF with page-aware chunking (`chunk_document`) and
classification (`TEXT_HEAVY` / `MIXED` / `IMAGE_HEAVY` / `EMPTY`). The 41
personal PDFs are ingested via the CLI into a local SQLite database; see
[Phase 31](#phase-31--pdf-ingestion-done).

### The real, live corpus today

The corpus used by the accepted demo suite (`/tmp/ph18_corpus.db`) is
**conversation + event only**:

- **conversations** ~639 / **conversation_messages** ~4,531
- **events** ~72,902 (search_query, url_visit, video_watch, youtube_search)
- **documents** 0 / **document_chunks** 0 / **structured_extractions** 0

This is the concrete, verified "indexed document count is 0" limitation from
earlier evaluations: with no indexed document chunks, the model's durable
knowledge comes from **conversations and events** (the narrow
`search_documents` tool is therefore hidden).

Phase 31 adds the first **document corpus**: the 41 personal PDFs are ingested
into a local SQLite knowledge base (`--ingest file`, see
[docs/USAGE.md](docs/USAGE.md)), keyed by content hash and idempotent to
re-run. Extractions and chunks live in that database; the demo corpus above and
the filesystem scratch `knowledge.db` are separate. Merging the document corpus
into a single always-on knowledge base used by agent mode is the next step.

(Note: the `knowledge.db` at the repo root is a small, git-ignored local
test/scratch database — 1 document, 0 chunks — and is not the demo corpus.)

---

## I. Roadmap

Concrete, with statuses.

### Completed

- Source/ingestion foundation and document model
- FTS / keyword retrieval
- Conversation & knowledge retrieval
- Activity / event retrieval and temporal aggregation
- Temporal activity support
- Phase 23 retrieval correctness fixes (retained)
- `qwen3.5:9b` model evaluation
- Prompt experiment and rejection, model comparison, hardware-limitation assessment
- Acceptance suite — ACCEPT (Phase 28)
- POC demonstration + user-facing hardening — DEMO-READY (Phase 29)
- User documentation — `docs/USAGE.md`
- **Phase 30 — OpenAI-compatible HTTP API** (server module, `/v1/chat/completions`,
  `/v1/models`, localhost binding, optional token, tests)
- **Phase 34a — vision extraction for image-heavy PDFs** (optional local-only
  page-level vision, per-page cache, additive chunk augmentation)
- **Open WebUI integration path** (see [Open WebUI integration](#k-open-webui-integration))

### Current state

The project is a **local-first personal-history POC / early daily-driver
backend** — it is **not** a general autonomous assistant. `qwen3.5:9b` remains
the deployable local model because it fits the hardware, it is more trustworthy
than the tested `llama3.1:8b` on grounding, ≥14B testing was hardware-infeasible,
and synthesis/compound-query limitations remain accepted POC constraints.

Current interface: Open WebUI (or any OpenAI-compatible client) → Personal AI
API → existing Agent + ToolRegistry → personal corpus + local Ollama.

### Phase 30 — API boundary (DONE)

Implemented. Reuses the existing Agent + ToolRegistry; the API is a thin
OpenAI-compatible `/v1/chat/completions` boundary only. Non-streaming.
See [Open WebUI integration](#k-open-webui-integration) and
[`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md).

### Phase 31 — PDF ingestion (DONE)

The machine-found `raw_data_extracted/raw_data/pdfs/` — the inventoried 41
personal PDFs (19 text-heavy, 16 mixed, 6 image-heavy) — is wired into the CLI
as the `file` source and ingested into a local SQLite knowledge base via
`--ingest file <dir> --database <db>`:

- text extraction (exists: `extract_text`, PyMuPDF)
- document classification (exists: `classify_document`)
- metadata (filename, pages/images, source type)
- chunking: every document with ≥200 extracted non-whitespace characters is
  indexed into `document_chunks`, so `search_documents` appears and FTS hits
  return personal PDF content; mixed documents are chunked from extracted text
  alone while the vision path is being built
- structured extraction stays text-heavy-only: the model is called only for
  text-heavy documents (never for mixed/image-only content)
- image-heavy and near-empty documents are stored as documents but not chunked
  (vision-capable pipeline lands in Phase 34a)
- tests + regression validation (rerun idempotent; validated by
  `scripts/validate_pdf_ingestion.py` and the unit/integration suite)

The 41 PDFs ingest deterministically (41 documents, 246 chunks, 35 chunked
documents, idempotent re-run). The result lives in a dedicated local database,
separate from the conversation/event demo corpus.

### Phase 32 — Email ingestion (DONE)

Gmail/mbox ~22.3k messages (three near-disjoint segments 2015–2026). Email now
ingests at scale through the existing generic pipeline — **no separate email
pipeline**:

- **Identity**: each message is keyed by its normalized `Message-ID`
  (delimiters stripped, whitespace folded, domain lowercased), feeding the
  existing content-hash-based document id. The same message present in several
  mailboxes maps to one document; a message whose body changed gets a new
  document, the old id stays. Messages without a `Message-ID` (2 in the real
  corpus) fall back to a deterministic `noid/<sha1>` digest of their payload.
- **Deterministic normalization** (`sources/email_normalization.py`, stdlib-only
  pure functions): trailing `>`-quoted replies, `On ... wrote:`, original/
  forwarded separators, and conservative trailing signatures are stripped;
  HTML keeps paragraph/line structure as separate lines; blank runs collapse.
  Where rules are uncertain the text is preserved, never silently deleted.
- **Model-free ingestion**: email runs with structured extraction disabled
  (`structured_extractor=None`), so email ingestion makes **zero Ollama calls**.
  `DocumentIngestor` gained an optional extractor; text-heavy records are still
  chunked and searchable.
- **CLI**: `--ingest email <Takeout/Email dir>`; deterministic, idempotent
  re-runs; metadata (from/to/subject/date/message-id/mailbox) only, never
  message content in logs.
- Mailbox-attachment content and conversation *threading* are deferred
  (attachment metadata only), per the Phase 33 notes below.

**Real-corpus validation** (local SQLite, `/tmp/phase32.db`, aggregate counts
only): the full ~2.7 GB export (three mbox segments) discovers deterministically
in ~94 s — 22,403 records from 22,401 Message-ID keys + 2 `noid/` fallbacks
(152 duplicate copies, 6 of which carry distinct bodies). Ingestion is
model-free: **22,257 documents, 80,892 searchable chunks, 0 structured
extractions** (no Ollama calls). Body normalization shifts 22 short messages
from text-heavy to mixed (quoted-only tails no longer count as content). A full
re-run is byte-identical: same summary, same DB counts, ~102 s with zero writes.

### Phase 33 — Unified corpus search provenance, email titles, MIME filtering (DONE)

The PDF corpus (Phase 31) and email corpus (Phase 32) now search as **one
unified corpus** with source identity surfaced to the model:

- **Result provenance** — every `search_documents` and `search_knowledge`
  result carries the owning document's `source_type` (`file`, `email`, ...)
  and `source` identifier (workspace-relative path for files; normalized
  `Message-ID` for email). `SearchResult` and `ChunkSearchResult` grew a
  `source_type` field; the chunk-store provenance is resolved in one batched
  lookup (no N+1).
- **Email titles** — email search results now display the email **subject**
  when present; emails without one fall back to the source `Message-ID`.
  This is presentation-only — email identity/normalization is untouched.
- **MIME-type filtering** — `DocumentFilter` gained an optional `mime_types`
  inclusion list (also exposed in the `filter` argument of both search tools),
  reading the authoritative `metadata["mime_type"]` recorded at ingestion.
  PDFs are `file` sources with MIME type `application/pdf` — filtering for
  PDFs uses `mime_types: ["application/pdf"]`, never a `"pdf"` source type.
- `--search` CLI output is unchanged; no schema migration, no new tables, no
  new dependencies, no Agent/prompt/model changes.

**Tests**: 24 new/updated — `tests/test_corpus_unified_e2e.py` (PDF + txt +
mbox in one corpus, provenance, MIME filters, tool output contract), plus MIME
filter tests in `test_storage_chunk_search_filters.py`, tool provenance tests
in `test_tool_search.py`/`test_tool_knowledge.py`, and RetrievalService
source-type/email-title tests in `test_retrieval_service.py`.

**Real-corpus validation** (`/tmp/phase33.db`, merged private build of the real
41-PDF + 22,257-email corpora, aggregate counts only): 22,298 documents /
81,138 chunks; `mime_types=["application/pdf"]` isolates the 41 PDFs; email
titles resolve to subjects where one exists, with the `Message-ID` fallback for
the rest.

### Phase 34a — Vision extraction for image-heavy PDFs (LOCAL, OPT-IN)

Page-level vision extraction makes image-only and scanned PDFs searchable
without changing the chat or text pipelines. Everything is local and opt-in
via environment configuration:

- `PERSONAL_AI_VISION_MODEL` (default: unset = disabled) names a local
  vision-capable Ollama model; `PERSONAL_AI_VISION_PROMPT_VERSION` (default
  `v1`) identifies the extraction prompt.
- Only `image_heavy` PDFs route to the vision model. Text-heavy, mixed, and
  email sources never do. Routing and classification are untouched.
- Each page is rendered locally to a PNG capped at 1568px on the long side;
  the model output is appended to that page's original extracted text and
  chunked through the existing deterministic chunker (no pages merged, page
  numbers preserved, original text verbatim).
- Results are cached per `(document_id, page_number, vision_model,
  prompt_version)` in a new derived `vision_pages` table (the only schema
  addition). Re-ingesting unchanged scans makes zero vision calls; changing
  the model or prompt version re-extracts only those pages.
- Failure semantics: a page that cannot be rendered is skipped and retried on
  the next run; empty model output is not cached; provider (Ollama) failures
  propagate and never silently disable the pipeline; a stored document is
  never modified or lost.
- No new dependencies, no Tesseract/Poppler, no cloud APIs, no vision batch
  or parallelism.

**Tests**: `tests/test_vision_extractor.py`, `tests/test_vision_storage.py`,
`tests/test_vision_configuration.py`, `tests/test_vision_ingestion.py`, plus
vision cases in `tests/test_ollama_client.py` and `tests/test_cli_ingest.py`.

**Real-corpus validation** (local SQLite, `/tmp/phase34a.db`, aggregate counts
only): the six image-heavy PDFs in `raw_data_extracted/raw_data/pdfs` become
searchable via `PERSONAL_AI_VISION_MODEL=qwen3.5:9b` (the only currently
installed vision-capable model), with a deterministic idempotent re-run.

### Phase 33 — Open WebUI hardening

UI-layer polish once the API exists:

- authentication / local-only access
- source/evidence citations surfaced in responses
- error handling and retry guidance for known synthesis/empty-answer behavior
- streaming UX
- conversation handling (multi-turn stays within the Agent loop)

### Phase 34+ — additional personal sources

Only after each source has a clear adapter/normalization contract (the
existing `SourceAdapter` boundary):

- spreadsheet record boundary for the 6 personal XLSX files
- structured-data record boundary for Maps places/Q&A, Chrome history links
- durable "memories" concept distinct from document chunks (Phase 10)

Each new source is a small vertical slice with tests behind the existing
adapter contract — **not** a separate per-source pipeline.

---

## J. Missing implementation steps

Checklist of what is still required for the POC to become a useful daily
personal assistant. **REQUIRED** items block the daily-driver goal; OPTIONAL
items are quality/robustness improvements.

### REQUIRED

- [x] **Stable HTTP API** (Phase 30) — `python -m personal_ai.server` exposes
      the programmatic entry point alongside the CLI.
- [x] **OpenAI-compatible endpoint** (Phase 30) — `POST /v1/chat/completions`
      with `GET /v1/models`; usable as an Open WebUI backend.
- [ ] **Streaming responses** — tool-call latency (13–65 s) is otherwise a
      silent wait (Phase 33).
- [ ] **Source/evidence metadata in responses** — so the user can see what a
      claim is grounded in (Phase 33).
- [ ] **PDF ingestion** into the live corpus (Phase 31).
- [x] **Email ingestion** at scale (Phase 32) — deterministic Message-ID
      identity + model-free normalization; validated against the real corpus
      (see Phase 32 section).
- [ ] **Ingestion scheduling / re-ingestion** — a way to re-run `--ingest`
      against the corpus without manual CLI invocations.
- [ ] **Incremental updates** — add only what changed (idempotency already
      exists; scheduling + invocation does not).
- [ ] **Duplicate detection** — already deterministic via content hashes
      (implemented); verify end-to-end for new sources.
- [ ] **Stable source IDs + timestamps** — already in `SourceRecord` /
      document identity (implemented); surface them through the API.
- [ ] **Observability** (structured, non-content logging) — the CLI `--verbose`
      trace exists; the API needs equivalent structured metadata.
- [x] **Configuration** — `PERSONAL_AI_CHAT_MODEL` and API host/port/token are
      environment-configurable (Phase 30); `cli.py` still hard-codes
      `MODEL = "qwen3.5:9b"` via `config.DEFAULT_CHAT_MODEL`.
- [ ] **Backup/recovery** — the SQLite database is the durable store; a
      documented backup/restore path (and location) is needed.
- [x] **Authentication/access control** — optional `PERSONAL_AI_API_TOKEN`
      bearer token for the API (Phase 30), required for any non-localhost
      exposure.
- [ ] **Tests for all of the above** (following existing no-Ollama patterns).
- [x] **Documentation** — README (this file), `docs/USAGE.md`,
      `docs/OPEN_WEBUI.md`, and this Phase 30 report.

### OPTIONAL

- [ ] Real semantic/vector retrieval (blocked on Ollama embedding support +
      `PERSONAL_AI_EMBEDDING_MODEL`) — hybrid search beyond keyword/FTS.
- [ ] Vision extraction for image-heavy documents (needs a vision model +
      hardware).
- [ ] Durable "memories" as a distinct concept from document chunks.
- [ ] Spreadsheet / structured-record boundaries for XLSX and Maps data.
- [ ] Open WebUI UX polish, conversation persistence in the UI, citations UI.
- [ ] Containerization (Dockerfile/compose) — none exists today.

---

## K. Open WebUI integration

### Intended end state

```
Open WebUI  (UI layer)
    |
    v
Personal AI HTTP API   (OpenAI-compatible /v1/chat/completions)
    |
    v
existing Agent  (+ ToolRegistry)
    |
    v
personal corpus  +  local Ollama
```

- **Ollama remains the local model runtime** (qwen3.5:9b).
- **Open WebUI is the UI layer only.** It is **not** responsible for personal
  retrieval, and it should **not** talk directly to Ollama for personal
  questions, because a direct Ollama → Open WebUI path would **bypass the
  personal-data Agent and its ToolRegistry** — the very layer that grounds
  answers in your history.

### Current repository state

Phase 30 is **implemented and smoke-tested** against the real corpus. The API
lives in `src/personal_ai/server.py` and reuses the exact CLI wiring through
`cli.build_agent()` — the same `_connect_agent_registry` + `Agent.run(messages)`
path — so it does **not** reimplement retrieval. Both the CLI and the server go
through one canonical construction path (`build_agent` in `cli.py`).

### Alternative architecture (rejected as primary)

```
Open WebUI -> Ollama directly
```

This is **not sufficient for this project**: it bypasses the personal
retrieval/tool layer, so answers would not be grounded in the user's stored
personal history. It is at most a fallback for generic non-personal chat. The
implemented integration keeps the Personal AI API in the middle.

### Implemented API surface (Phase 30)

- `POST /v1/chat/completions` — OpenAI-compatible request body (`model`,
  `messages`) returning the Agent's answer. `stream` is rejected with `400`
  (non-streaming in Phase 30; streaming is Phase 33).
- `GET /v1/models` — lists `qwen3.5:9b` for Open WebUI model discovery.
- The full `system` / `user` / `assistant` history is mapped to `ChatMessage`s
  and passed straight to `Agent.run()`; multi-turn grounding is preserved and
  **no extra grounding system prompt is injected** (production baseline stays
  prompt-free).
- Internal tool calls are **not** returned as assistant text — only the
  Agent's final answer is.
- Errors are clean HTTP codes (`400`/`401`/`500`/`502`) with an OpenAI-style
  `{"error": {...}}` shape; details are logged without dumping corpus content.
- Default bind is **localhost-only** (`127.0.0.1`); optional
  `PERSONAL_AI_API_TOKEN` bearer token.

Full setup, env vars, and the Docker networking note are in
[`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md).

See the planning notes in the next section for the precise Phase 30 plan and
its constraints (do not rewrite the Agent/retrieval/ToolRegistry).

---

## L. Local deployment example

Two entry points exist: the **CLI** and the **HTTP API** (Phase 30). Both share
the same Agent/ToolRegistry wiring. There is no containerization yet.

Hardware: GTX 1070 / 8 GB VRAM / 15 GB RAM.

### 1. Ollama (local model runtime)

Ollama runs locally and serves `qwen3.5:9b`. Cold model load can exceed the
client's 180 s timeout, so **pre-warm the model** before latency-sensitive
interactive use (e.g. send a trivial prompt first, or keep a session open).

### 2. qwen3.5:9b

The fixed chat model (see [Hardware/model decision](#e-hardwaremodel-decision)).
Install/pull it via Ollama.

### 3. Personal AI

CLI:

```
uv run python -m personal_ai.cli \
  --workspace /path/to/workspace \
  --database /path/to/corpus.db \
  --verbose "What do I know about BCG?"
```

- `--workspace` is the sandboxed directory the filesystem tool may inspect.
- `--database` points at the SQLite personal corpus.
- `--verbose` prints the agent trace (rounds, tool calls, timing) to stderr.
- `--search QUERY` and `--ingest SOURCE PATH` exist (see `--help`).

HTTP API (Phase 30) — localhost only by default (port 8000):

```
uv run python -m personal_ai.server \
  --workspace /path/to/workspace \
  --database /path/to/corpus.db
```

Then e.g.:

```
curl http://127.0.0.1:8000/v1/models
curl -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"What do I know about BCG?"}]}' \
     http://127.0.0.1:8000/v1/chat/completions
```

Config (env or flags): `PERSONAL_AI_CHAT_MODEL`, `PERSONAL_AI_API_HOST`,
`PERSONAL_AI_API_PORT`, `PERSONAL_AI_API_TOKEN`. See
[`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md).

### 4. Open WebUI

Configure Open WebUI to connect to the Personal AI API as an OpenAI-compatible
backend (`http://127.0.0.1:8000/v1`, model `qwen3.5:9b`). The Personal AI API
stays in the middle so answers remain grounded in your history — Open WebUI is
the UI layer only. Full setup (including the Docker networking note) is in
[`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md).

---

## M. Usage

See [`docs/USAGE.md`](docs/USAGE.md) for the full user guide. The question-style
guidance below applies to both the CLI and the HTTP API (which simply exposes
the same Agent through `POST /v1/chat/completions`). For API endpoint details,
env vars, and Open WebUI setup see [`docs/OPEN_WEBUI.md`](docs/OPEN_WEBUI.md).

**Recommended question style — GOOD (single-domain, well-scoped):**

- "What do I know about BCG?"
- "What did I search for recently?"
- "What videos did I watch about X?"
- "Summarize my January 2026 activity."
- "Do I have anything about fly fishing?" (returns an honest no-records answer)

**LESS RELIABLE (multi-domain / open synthesis):**

- "Compare everything I watched with everything in my notes and tell me what
  career strategy I should pursue."

**Guidance:** compound questions can be split into multiple grounded
single-domain questions:

```
What did I watch about BCG?
What do my notes say about BCG?
```

And because an occasional empty/generic answer is stochastic model behavior,
**retry the same question** if you get nothing back. The next attempt is
usually fine.

---

## N. Testing

- **`uv run pytest`** — full suite: **1337 tests**, no network, no Ollama, no real
  personal data. Uses `tmp_path`, `httpx.MockTransport`, in-memory/temp SQLite,
  and deterministic fakes. This includes the API tests in `tests/test_server.py`
  (request parsing, error mapping, `/v1/chat/completions`, `/v1/models`,
  authentication, config precedence) which use a fake Agent, not a live server.
- **`uv run ruff check .`** — clean.
- **`uv run ruff format --check src tests`** — clean (127 files).
- **Evaluation harnesses** — `scripts/evaluate_agent.py` (and the phase
  drivers) are *manual/private* harnesses that require a live Ollama + real
  corpus. They are **not** part of the automated suite and are not run by
  `pytest`. `scripts/evaluate_agent.py` is currently an untracked private
  harness and is **not** automatically added to commits.
- **What is a regression:** any change that (a) breaks an existing test, (b)
  fails `ruff`/format checks, (c) alters deterministic retrieval behavior
  without a test update, or (d) introduces a network/Ollama dependency into
  the normal suite.

---

## O. Repository state / git

- HEAD: `1f2fb4a` (`docs: document the personal AI HTTP API and Open WebUI
  integration`). Local `main` is ahead of `origin/main` by 6 commits (not
  pushed).
- Phase 23 retrieval correctness fixes and Phase 30 (API) are **committed**
  in the three most recent commits:
  1. `1a9c130 fix(retrieval): retain phase 23 multi-term ...`
  2. `0133892 feat(api): add OpenAI-compatible personal AI HTTP API`
  3. `1f2fb4a docs: document the personal AI HTTP API and Open WebUI integration`
- Untracked:
  - `scripts/evaluate_agent.py` (private/manual eval harness — not intended
    for automatic commit)
- `knowledge.db` at the repo root, `previous_project_and_raw_data/`, and
  `raw_data.zip` are git-ignored local data and must never be committed.
- `/tmp` corpus artifacts (e.g. `/tmp/ph18_corpus.db`) live outside the repo
  and are never committed.

---

## Validation commands

```
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
```

Real-corpus evaluation is manual and read-only (requires a live local Ollama
and the real corpus database, which are never part of the test suite).
