# Personal AI

Local-first personal knowledge and agent system.

Text knowledge is ingested through typed source adapters into a durable,
deterministic document store, chunked deterministically, and optionally
embedded by a provider-independent embedding backend for future retrieval.
The model backend is Ollama; no cloud APIs are used.

This file is the project's durable checkpoint record. It is written so a
future session can resume exactly where work stopped without relying on
chat history.

## Current status snapshot

As of checkpoint `f3d6537` ("Add batched embedding backfill"), plus a
second corpus discovery on **2026-08-23** and the ChatGPT adapter slice:

- **Phase:** knowledge ingestion covers four sources end-to-end (Keep,
  NotebookLM, Gemini, ChatGPT). The second archive's remaining families
  (PDFs, XLSX, email) are inventoried but not yet ingested. Retrieval is
  not started.
- **Tests:** 512 passing; `ruff check` clean; `ruff format --check` clean.
- **First-corpus validation:** 838 knowledge documents / 4,542 chunks from
  Google Keep and NotebookLM exports; fake-provider backfill produced
  4,542 embeddings; re-running ingestion and backfill was fully idempotent.
- **Gemini validation (2026-08-23):** all 170 conversations discovered and
  ingested through the generic orchestration into a throwaway database —
  170 documents / 1,344 chunks (167 TEXT_HEAVY + 3 MIXED), zero duplicates,
  rerun idempotent, embedding store untouched.
- **ChatGPT validation (2026-08-23):** all 469 conversations discovered
  across the five shard files and ingested into a throwaway database —
  469 documents / 3,363 chunks (451 TEXT_HEAVY + 18 MIXED), 451 structured
  extractions, zero duplicates, rerun idempotent (extractor reused stored
  extractions), embedding store untouched, no provider contacted.
- **Measured knowledge total today:** 1,477 documents / 9,249 chunks
  (838 Keep+NotebookLM, 170 Gemini, 469 ChatGPT).
- **Second corpus (inventoried):** 41 personal PDFs (28 with usable text
  layers), 6 XLSX spreadsheets, ~22.3k emails in three mbox segments
  spanning 2015–2026, and media/attachments excluded.
- **Blocked infrastructure:** local Ollama server does not serve embeddings
  yet (`/api/embed` returns HTTP 501 "server does not support embeddings";
  no embedding-capable model installed) and `PERSONAL_AI_EMBEDDING_MODEL`
  is unset, so real embeddings are pending infrastructure, not code.

## Pipeline architecture

```
SourceAdapter -> SourceRecord -> DocumentIngestor -> DocumentStore
                                     |
                                     +-> extraction/classification/chunks
                                              |
                             EmbeddingBackfiller (separate step)
                                              |
                                       ChunkEmbeddingStore
```

- Sources produce typed `SourceRecord`s with deterministic identity
  (source type, stable key, content hash).
- The ingestor persists documents, structured extractions, and chunks
  idempotently; unchanged inputs produce zero new rows.
- Embeddings are derived state: rebuildable at any time from chunks via a
  separate batched backfill (`backfill_documents`). Ingestion never calls
  a model provider.
- Embedding configuration is independent of chat configuration
  (`PERSONAL_AI_EMBEDDING_MODEL`; `create_embedder`, `probe_embedding_backend`).

## Checkpoint history

Status vocabulary:

- **implemented** — code and tests exist
- **fake-validated** — exercised end-to-end with deterministic fakes
- **corpus-validated** — run against the real local corpus
- **blocked** — waiting on external infrastructure
- **not started**

| Checkpoint | Commit | Status | What was established |
|---|---|---|---|
| Typed Ollama client | `ea0554f` | implemented | HTTP boundary, timeouts, error taxonomy; no app code talks to Ollama directly |
| Tool calls + registry | `8cdf1d6`, `c86033d` | implemented | Model tool-call parsing; explicit allow-listed tools only |
| Agent loop + sandboxed filesystem + CLI | `3d74678`..`740bfd7` | implemented | Synchronous agent over ToolRegistry; workspace-sandboxed fs tools; CLI entry point |
| Source adapter boundary | `dd19f52` | implemented | `SourceAdapter` protocol, `SourceRecord` (source_type/key/hash/payload/metadata); filesystem discovery inside sandbox only |
| Document model + SQLite store | `2736327` | implemented | Deterministic document persistence keyed by content hash |
| Text extraction + classification | `3cc6766`, `b9ce4ce` | implemented | Provider-independent text extraction boundary; measurable text-vs-image heaviness classification (threshold 200 chars/page-equivalent heuristic) |
| Structured extraction boundary | `b5c0e75` | implemented | Typed extraction schema; Ollama provider isolated behind interface |
| Extraction storage + idempotent ingestor | `969446b` | implemented | `structured_extractions` table; fail-fast ingestion; unchanged docs re-ingest as no-ops |
| Chunk store + deterministic chunks | `429d659`, `6f7477f` | implemented | Stable chunk ids derived from content; persisted during ingestion |
| Embedding contract + Ollama embedder | `cbab79b` | implemented | Provider-independent `EmbeddingProvider`; unit-tested via mock transport |
| SQLite embedding storage | `affed3f` | implemented | Idempotent `(chunk_id, model)` embedding persistence |
| Backfill decoupled from ingestion | `29abef0`, `8fae403` | implemented | Embeddings generated in a separate step; ingestion has no provider dependency |
| Raw corpus ignored by git | `cbabd1e` | implemented | `previous_project_and_raw_data/` excluded; read-only inspection only |
| Keep source adapter | `6fe028f` | implemented, corpus-validated | 731 notes on disk -> 695 records discovered (36 contentless rejected); composed-text payload hashing; trashed/empty filtering |
| NotebookLM article adapter | `cbdd97f` | implemented, corpus-validated | Structural discovery of `*/Sources/*.html`; HTML-to-text composition (stdlib parser, images counted not embedded); metadata sidecar parsing; chat history excluded structurally |
| Generic source orchestration | `df42a50` | implemented, corpus-validated | `discover_source` / `ingest_source` summaries; no per-source branching allowed (static guard test) |
| Embedding configuration | `f5bf1e7` | implemented | Env-driven model selection; explicit error when unset; readiness probe seam for `/api/embed` |
| Batched embedding backfill | `f3d6537` | implemented, corpus-validated (fake provider) | `backfill_documents`: sorted-id order, per-document atomicity, exact totals vs chunk rows |
| Real-corpus pipeline validation | this checkpoint | corpus-validated | Keep 695 docs/236 chunks; NotebookLM 143 docs/4,306 chunks; total 838 docs/4,542 chunks; fake-provider backfill produced 4,542 embeddings; second run fully idempotent |
| Structured-data investigation | this checkpoint | corpus-validated (read-only) | Maps/Chrome/YouTube/Google Pay/timeline families measured and classified; none ingested; boundaries recorded below |
| Corpus discovery 2 (`raw_data.zip`) | this checkpoint | inventoried **2026-08-23** | Second archive inspected read-only: Gemini/ChatGPT exports, personal PDFs, XLSX, mbox email, attachments/media. Nothing ingested; see "Corpus discovery 2" for the family inventory and revised plan |
| Gemini conversation adapter | this checkpoint | implemented, corpus-validated | Per-conversation JSON only (`.md` twins and `_`-prefixed aggregates excluded structurally); composed speaker-labeled text; provenance in metadata. Real corpus: 170/170 discovered, zero schema anomalies, 167 TEXT_HEAVY + 3 MIXED; throwaway ingestion: 170 docs / 1,344 chunks, rerun idempotent, embeddings untouched |
| ChatGPT conversation adapter | this checkpoint | implemented, corpus-validated | Official shard export (`conversations-*.json`) parsed structurally; active-branch linearization over parent-pointer `mapping` trees (no `children` arrays exist in the export); voice transcriptions rendered as text; media pointers and model reasoning excluded but counted; synthetic `<conversation_id>.json` source keys stable across re-sharding. Real corpus: 469/469 discovered, 451 TEXT_HEAVY + 18 MIXED, throwaway ingestion 469 docs / 3,363 chunks / 451 extractions, rerun idempotent, embeddings untouched. See "ChatGPT export schema notes" below |
| Real embeddings | — | **blocked** | Local Ollama lacks embeddings support (HTTP 501; no model). Requires enabling the server flag/installing an embedding model and setting `PERSONAL_AI_EMBEDDING_MODEL`. No code work blocked on this. |
| Retrieval (search over chunks/extractions) | — | **not started** | Design deliberately deferred until the chat-export knowledge from discovery 2 is ingested so retrieval is designed once over the full document mix |
| Structured-data pipeline | — | **not started** | Maps/Chrome/YouTube/Google Pay exports inventoried but intentionally not ingested; see boundaries below |

## Corpus inventory and boundaries

Local raw corpus: `previous_project_and_raw_data/` (~27 GB). It is
git-ignored, must never be committed, copied into fixtures/tests/logs, or
modified. All inspection is read-only. Old project directories are
reference material only — not authoritative architecture.

### Classification

**KNOWLEDGE (text/document pipeline):**
- Google Keep notes (`Conservar/`): 731 files; 695 usable records — handled
  by `KeepSourceAdapter`.
- NotebookLM articles (`NotebookLM/*/Sources/*.html`): 143 — handled by
  `NotebookLMSourceAdapter`.
- Total measured today: **838 knowledge documents, 4,542 chunks**
  (187 TEXT_HEAVY + 508 MIXED Keep; NotebookLM all TEXT_HEAVY).
- A few scattered markdown/text files exist but are low volume.
- ~30 personal PDFs referenced by the old project's index were absent from
  this copy but have since been located in the second archive — see
  "Corpus discovery 2".

**STRUCTURED DATA (never naive text-chunked):**
- Maps places/Q&A/GeoJSON JSON (6,379 files), Chrome history (~26k entries),
  YouTube CSV logs, Google Pay transaction CSVs, small timeline/location
  exports. These require a future structured-record boundary, not
  serialization into prose chunks.

**PERSONAL MEDIA (outside this pipeline entirely):**
- Photos/video/audio: ~4,335 JPG, 98+11 MP4, 305 MP3, 9 WAV, 626 `.data`
  blobs, PPTX/slide artifacts, one image-based PDF (pdftotext extracts
  ~nothing). These belong to a media organization/import workflow, not to
  text RAG.

**PROJECT/SOURCE CODE (excluded):**
- `data_handling/`, `archive/`, `chroma/`, `langchain/` — old/third-party
  code. Evidence only; may inform requirements but must be independently
  justified before any pattern is reused.

### Do-not-ingest list

Never route through the document/chunk/embedding pipeline:

1. Personal media (images/video/audio/media blobs/decks) — media workflow.
2. Derived/private databases (`chroma_db/chroma.sqlite3`,
   `private_storage/memory.db`) — derived indexes and extracted memories,
   not source-of-truth documents. Potential future *import* targets behind
   dedicated boundaries, never direct sources.
3. Project/source code trees listed above.
4. Event/tabular exports (history logs, transactions, GeoJSON dumps) — they
   must first pass through a designed structured-record boundary.

## Corpus discovery 2 — `raw_data.zip` (inventoried 2026-08-23)

A second archive, `raw_data.zip` (2.12 GB, 466 files), was uploaded to the
repository root. It is git-ignored and must never be committed or copied
into tests/docs. Inspection was read-only (extracted to a temporary
directory outside the repo). It contains the previously missing personal
data. The Gemini and ChatGPT conversation exports have since been ingested
(see the adapter checkpoints above); PDFs, spreadsheets, email, and
structured-data families remain un-ingested by design.

| Family | Measured contents | Classification / boundary |
|---|---|---|
| `Gemini/` | **170 conversations**, each as an `.md` render + `.json` twin with identical stems; clean schema `{id, title, messages[{role, content}], url, createdAt, lastMessageAt, messageCount}`; ~1.4 MB composed text; dates 2025-05 → 2026-07. Aggregates `_all_conversations.json`/`.md` duplicate all 170 ids | **KNOWLEDGE — now ingested.** `GeminiSourceAdapter` discovers the per-conversation JSON twins only; the `.md` renders and `_`-prefixed aggregates are derived duplicates and never yield records |
| `ChatGPT_export/` | **469 conversations** across `conversations-000..004.json` (~13.9 MB), uniform top-level schema; 3,721 message nodes total (1,771 user + 1,950 assistant; zero system/tool messages); content types: 3,432 `text`, 169 `multimodal_text`, 79 `thoughts`, 41 `reasoning_recap`; plus `chat.html` render (duplicate representation), user/settings/shared/manifest sidecars. Measured details in "ChatGPT export schema notes" below | **KNOWLEDGE — now ingested.** `ChatGPTSourceAdapter` discovers the shard files structurally; `chat.html` and non-conversation sidecars are excluded structurally, `*.dat` attachments stay in the media workflow |
| `ChatGPT_export/*.dat` | 26 attachment blobs: ~22 JPEG, 2 PNG, 3 HTML, 1 PDF | **PERSONAL MEDIA / mixed attachments** — excluded from text pipeline; HTML/PDF attachments revisit later via their own boundaries |
| `pdfs/` | **41 PDFs, 330 MB**: CVs ×4, tax certificates/statements, rental/utility bills, SEPA mandate, legal complaint (`Anzeige`) ×2, therapy worksheets, life-goals/goals docs, BCG case/interview prep, receipts, `Sonnenkind.pdf` (79 MB). Text census: **28 with usable text layers** (~22.5k words total), 6 fully scanned (`pdftotext` ≈ 0 words: Nebenkostenabrechnung ×2, wirtschaftplan ×2, both Vision Boards at 72/163 MB), 7 thin (<300 words) | **KNOWLEDGE (personal documents)** — small volume, high personal/legal value. Text-layer PDFs fit the planned extraction slice; scanned ones are future vision inputs |
| `Excel/` | 6 XLSX: salary history 2021–2026, finance overview, rent split among roommates, utility billing, self-reflection questions, song list | **STRUCTURED DATA (tabular personal documents)** — tiny but high value; needs spreadsheet-aware record boundary, never naive chunking |
| `Email/` | Three Gmail mbox segments, **~22.3k unique Message-IDs, Jan 2015 → Jul 2026**, nearly pairwise-disjoint (overlaps ≤149): `Bandeja de entrada.mbox` 2.15 GB (2018→2026), `Bandeja de entrada.partial.mbox` 541 MB (2015→2018), `INBOX.mbox` 154 MB (2025→2026); binary `table_of_contents` sidecars (not plain text) | **KNOWLEDGE (email)** — largest volume, highest sensitivity; requires Message-ID dedupe, quote/signature stripping, and its own adapter. Deferred until chat exports and PDFs are handled |
| `Gemini/Takeout 3/NotebookLM/Growth/` | Artifacts + Chat History byte-identical to the existing corpus's Growth notebook | **DUPLICATE** — ignore |
| `Gemini/Takeout 3/Actividad de registro de accesos/` | Device/service access-log CSVs | **STRUCTURED telemetry** — no use case; ignore for now |
| `whatsapp.txt` | 83-byte placeholder sample (synthetic names) | Ignore — not real data |

Revised knowledge totals once discovery-2 sources are ingested (projection,
not measured): roughly **1,700–2,000 documents** and **12k–18k chunks**.
Measured so far: Gemini adds 170 documents / 1,344 chunks and ChatGPT adds
469 documents / 3,363 chunks on top of the first corpus's 838 / 4,542 —
**1,477 documents / 9,249 chunks today**, leaving text-layer PDFs and email
as the remaining additions. Projected embedding workload grows accordingly
(~3–4× current backfill); still minutes-scale locally. Real embeddings
remain blocked on Ollama infrastructure; nothing was run.

### ChatGPT export schema notes (measured 2026-08-23)

Facts the adapter depends on, measured across all 469 conversations before
the parser was finalized:

- Every shard is a JSON list of conversation objects with a uniform
  top-level schema (`conversation_id`, `title`, `create_time`,
  `update_time`, `current_node`, `mapping`, `default_model_slug`,
  `is_archived`, ...). `conversation_id == id` in all 469.
- `mapping` nodes are `{id, message, parent}` only — **the export has no
  `children` arrays**, so ancestry must be reconstructed from parent
  pointers. Exactly one root node (`message == null`) per conversation;
  `current_node` always present and resolvable.
- **Branching exists:** 62 conversations contain alternative nodes (edited
  user prompts and regenerated assistant replies) that are not part of the
  displayed thread. Policy: render the active branch only (walk parents
  from `current_node` to root, reverse). Alternatives are counted per
  record in `alternative_node_count` (241 nodes corpus-wide), never
  silently discarded. Identity ignores them: pruning alternatives does not
  change payload or hash.
- **Tree order beats timestamps:** 47 messages along otherwise-valid active
  chains have `create_time` earlier than their predecessor. The adapter
  therefore never sorts by time; message timestamps stay metadata-only.
- Roles are only `user`/`assistant`. Unknown roles would pass through
  verbatim as speaker labels, but none occur in this export.
- **Voice conversations carry text in a non-obvious field:** 150
  `audio_transcription` dict parts inside `multimodal_text` messages hold
  the real transcription and are rendered as normal message text.
- Media pointer parts (19 image assets, 76 audio asset pointers, 74
  real-time voice/video containers) never enter text; they are counted in
  `media_part_count`. Message-level `attachments` metadata (23
  conversations) is preserved as `attachment_names` provenance only — the
  blobs themselves belong to the media workflow.
- Model reasoning content (`thoughts`, `reasoning_recap`; 115 on-chain
  messages) is internal scratch work: excluded from text, counted in
  `reasoning_message_count`.
- Message-level `update_time` is absent everywhere; conversation-level
  epoch-float `create_time`/`update_time` are always present and become
  the record's UTC ISO `created_at`/`modified_at`.
- One archived conversation exists; archiving is a UI state, not deletion,
  so it is ingested and flagged via `metadata.is_archived`.
- Source keys are synthetic `<conversation_id>.json` because no
  per-conversation file exists; they survive re-exports that re-shard
  conversations differently, keeping document identity stable.

Assumption changes caused by discovery 2:

1. The "missing" ~30 personal PDFs referenced by the old project's index
   now exist here (`pdfs/` matches the previously recorded filename list).
   The PDF-extraction slice is unblocked.
2. Email is confirmed present in volume (mbox, not PST/OST/MSG/EML) — the
   email adapter must target Gmail mbox format specifically.
3. No DOC/DOCX, PST, or additional messaging corpora were found.
4. Chat-export adapters (Gemini, then ChatGPT) become the highest-value
   next sources; retrieval design should wait for them so it is built once
   over the full document mix.

## Media boundary

Photos, videos, and audio are personal media. They are **not part of the
text RAG ingestion pipeline** and must never be embedded as text merely
because they exist in the corpus.

Their future workflow lives outside this pipeline:

```
raw media -> identify / deduplicate / normalize metadata
          -> organize / import -> Immich
```

No media workflow code exists yet; nothing in the current architecture
requires it.

## Architectural decisions and rationale

- **Deterministic identity over timestamps:** document and chunk IDs derive
  from content hashes so re-runs are idempotent and safe.
- **Embeddings are derived state:** rebuildable, stored separately
  (`chunk_embeddings`), generated only by an explicitly invoked backfill.
  Ingestion stays network-free and model-free.
- **Provider independence:** all model access sits behind narrow interfaces
  (`OllamaClient`, `StructuredExtractor`, `EmbeddingProvider`); application
  code never imports provider internals. Static guard tests enforce that
  orchestration/storage modules stay provider-free.
- **Structural adapters over generic parsing:** Keep and NotebookLM adapters
  encode each format's real structure (composed note text, article HTML
  rendering, sidecar metadata) rather than guessing; malformed input fails
  loudly instead of being silently dropped.
- **No speculative frameworks:** no vector DB, LangChain/LlamaIndex, or
  generic plugin system until the corpus justifies it. SQLite remains the
  single durable store.
- **Fail-fast orchestration with summaries:** `ingest_source` aborts on the
  first corrupt record and reports counts; partial silent success is avoided.
- **Privacy:** personal content is never logged; tests use synthetic
  fixtures shaped like the real formats, never real personal data.

## Validation commands

```
uv run pytest                      # full suite (no network/Ollama required)
uv run ruff check .
uv run ruff format --check src tests
```

Real-corpus validation (read-only, manual, uses throwaway DB + fake
embedding provider):

```
uv run python - <<'EOF'
from pathlib import Path
from tempfile import TemporaryDirectory
from personal_ai.sources.keep import KeepSourceAdapter
from personal_ai.sources.notebooklm import NotebookLMSourceAdapter
from personal_ai.orchestration import discover_source, ingest_source
from personal_ai.storage import connect_database
# see tests/test_embeddings_batch.py for the fake-provider backfill pattern
EOF
```

The corpus itself is validated by running the existing adapters' `discover()`
against git-ignored local paths; expected current counts: Keep 695
discovered records, NotebookLM 143 articles, Gemini 170 conversations,
ChatGPT 469 conversations. Ingestion totals through the generic
orchestration (throwaway DB): Keep + NotebookLM 838 documents / 4,542
chunks, Gemini 170 / 1,344, ChatGPT 469 / 3,363 — 1,477 documents / 9,249
chunks today, each rerun idempotent with zero embedding rows unless an
explicit fake-provider backfill is invoked.

## Structured-data inventory (investigated, not ingested)

Measured read-only during this checkpoint. None of it may be serialized
into text chunks; each family needs a purpose-built record boundary if and
when a use case exists.

| Family | Measured contents | Assessment |
|---|---|---|
| Maps `Fotos y vídeos/` | 5,059 per-media sidecar JSONs (title, taken/upload timestamps, EXIF lat/lng) | Belongs to the **media workflow** (Immich import metadata), not the structured pipeline |
| Maps automated Q&A | 1,288 records `{placeUrl, question, selectedChoice}` (+24 suggested edits, same shape) | Place-engagement signal; low text value; sensitive (reveals frequented places) |
| Maps authored Q&A (`Preguntas y respuestas.json`) | User-authored answers `{place_url, text}` | Genuine authored personal text; tiny volume |
| Labeled/saved/reviewed places | GeoJSON: labeled sites (incl. home/work coordinates), saved places, reviews with ratings/Q&A | Small, highly sensitive, high assistant value; relational candidate if ever needed |
| Commute routes (`Rutas de desplazamientos`) | Semantic trips: visits w/ lat-lng, travel modes | Location history; sensitive; no current use case |
| Chrome `Historial.json` | 26,212 browser-history entries `{title, url, time_usec}` across 942 hosts (~Nov 2025–Aug 2026); Session 15 | Behavioral telemetry; must NOT become 26k chunks; possible future indexed lookup aid |
| YouTube | CSVs are small logs (comments 70, music uploads 310, subscriptions 15, playlists ~72); real signal is two large activity HTMLs: watch history 40.4 MB, search history 13 MB | Watch/search titles would need an HTML record parser before any use; deferred |
| Google Pay (dir name contains NBSP) | 6 transaction CSVs, 41 rows total `{time, id, description, product, masked payment method, status, amount}` | Financial-sensitive; trivially small; defer |
| Timeline settings (`Cronología/Settings.json`) | 1.1 KB settings blob | Ignore |

## Next planned phase

Re-ranked after the ChatGPT adapter landed (2026-08-23), by measured value:

1. **Retrieval over all ingested knowledge** — keyword-first (SQLite FTS),
   designed once Keep + NotebookLM + Gemini + ChatGPT are in (done: 1,477
   documents / 9,249 chunks), so metadata filtering (source_type,
   timestamps, titles) is shaped by the real document mix. Text-layer PDFs
   and email can join later without redesign.
2. **Personal-PDF text extraction** — 28 of 41 PDFs have usable text
   layers; includes scanned classification (6 fully scanned stay
   vision-only for now).
3. **Email ingestion (Gmail mbox)** — largest remaining volume, highest
   sensitivity.

**Chosen next slice: 1 — retrieval design over the ingested corpus.**

Rationale: every chat-export family is now in storage with measured
classification and chunk counts, which was the explicit precondition for
designing retrieval once. PDFs remain a clean follow-on source that can be
added behind the existing adapter contract afterwards.

## Deferred work

- Personal-PDF text extraction (unblocked; includes scanned classification)
- Email ingestion (Gmail mbox): ~22.3k messages across three nearly
  disjoint segments; requires Message-ID dedupe, quote/signature stripping,
  attachment exclusion; highest sensitivity of any family
- Real embeddings (blocked on Ollama server flags/model install +
  `PERSONAL_AI_EMBEDDING_MODEL`)
- Personal-PDF text extraction (unblocked; includes scanned classification)
- Spreadsheet record boundary for the 6 personal XLSX files
- Semantic search and retrieval agent tools after keyword retrieval exists
- Durable memories concept distinct from document chunks (Phase 10);
  candidate import source later: old project's extracted-memory database,
  behind a dedicated deduplicating import boundary
- Vision extraction for image-heavy documents (two large Vision Board PDFs,
  slide decks, and scanned bills are known future inputs)
- Structured-data record boundary for Maps places/Q&A, Chrome history,
  YouTube activity, Google Pay transactions (first-corpus inventory table)
- Immich media workflow (separate track entirely)

## How to resume

1. Read this file top to bottom; trust the checkpoint table over memory.
2. Verify baseline: `uv run pytest && uv run ruff check . && uv run ruff
   format --check src tests` — expect 512 passing, all green.
3. Confirm HEAD matches or postdates `f3d6537`; the Gemini and ChatGPT
   adapter slices are uncommitted working-tree state as of 2026-08-23 —
   commit them before starting new work if not yet committed.
4. Raw data lives in two git-ignored locations: `previous_project_and_raw_data/`
   (first corpus, ~27 GB) and `raw_data.zip` at the repository root (second
   archive, 2.12 GB). Treat both strictly read-only; never commit, move,
   or copy from them into tests/docs/logs.
5. Pick the single next slice from "Next planned phase", implement it as a
   small vertical slice with tests following existing patterns (fakes over
   `connect_database(":memory:")`, mock transports for Ollama), update the
   checkpoint table, and report before committing.
