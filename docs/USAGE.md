# Personal AI — user guide and known limitations

This is a local-first personal assistant backed by a single local chat model
(`qwen3.5:9b`). It answers questions from your stored personal records
(conversation notes, watched-video and search history, URL visits).

## Ingesting your files (Phase 31)

Bring supported files (`.txt`, `.md`, `.pdf`, `.png`, `.jpg`, `.jpeg`) into a
knowledge database before (or after) asking questions. Discovery stays inside
the directory you pass and walks it recursively:

```
uv run python -m personal_ai.cli \
  --ingest file /path/to/your/files \
  --database /path/to/knowledge.db
```

Behavior is deterministic and idempotent: re-running on unchanged files never
duplicates documents or chunks. Document content is keyed by content hash.

- Text-heavy documents get structured extraction plus chunked text.
- Mixed documents (text plus images, e.g. scanned pages) get chunked text but
  no model extraction; image-only and near-empty documents are stored but not
  chunked until the vision-capable pipeline lands.
- Ingestion never uploads anything: only your local Ollama model is called, and
  only for text-heavy documents.

Knowledge-database-backed agent queries and `--search` then include the ingested
documents automatically.

## Ingesting your email (Phase 32)

Ingest a Google Takeout email export (the `<Folder>.mbox/mbox` structure) into
the same knowledge database:

```
uv run python -m personal_ai.cli \
  --ingest email /path/to/Takeout/Email \
  --database /path/to/knowledge.db
```

The same command also works on Thunderbird's local mailboxes: pointing the email
source at a Thunderbird account directory (containing extensionless flat mbox
files like `INBOX` and `Sent`) discovers those mailboxes directly, with the same
`Message-ID` identity contract. Metadata-only companion files (`.msf`, `.dat`,
`.json`) are never treated as mailboxes.

Email ingestion is different from the other sources in one important way: it is
**fully local and model-free**. No Ollama call is made during email ingestion, so
it needs no model running and never sends mail content to a model.

Behavior:

- Each message is keyed by its normalized `Message-ID` (delimiters stripped,
  domain lowercased), so the same message discovered in several mailboxes maps
  to one document. Messages without a `Message-ID` fall back to a deterministic
  digest of their content.
- Bodies are normalized deterministically: trailing quoted replies
  (`On ... wrote:`, original/forwarded separators, `>`-prefixed blocks) and
  conservative trailing signatures are removed; HTML messages keep their
  paragraph structure as separate lines. When the rules are uncertain the text
  is kept, never silently discarded.
- Headers and body are composed into payload text and content-hashed; documents
  and chunks carry metadata (from/to/subject/date/message-id/mailbox), never
  raw stack traces or content in logs.
- Re-running on the same export is idempotent: unchanged messages produce no
  new documents or chunks. Messages only usable via empty or image-only bodies
  are skipped.

## Financial exports (Phase 37)

Financial CSV exports are canonicalized into a deterministic, privacy-reduced
searchable form and flow through the same corpus pipeline as any other source.
Financial ingests are fully local and model-free (no Ollama call):

```
uv run python -m personal_ai.cli \
  --ingest financial /path/to/Financial_data \
  --database /path/to/knowledge.db
```

Behavior:

- **Deterministic**: canonicalization is pure local parsing (no LLM). UTF-8 and
  UTF-8-BOM, comma or semicolon delimiters, and German decimal-comma amounts
  are normalized; dates become `YYYY-MM-DD`.
- **Sensitive identifiers are excluded**: IBAN, counterparty IBAN,
  Gläubiger-ID, Mandatsreferenz, Kundenreferenz/payment references, account and
  card identifiers are dropped from searchable text outright (never masked or
  logged). A banking identifier embedded inside a free-text field is stripped.
- **Source identity is content-derived**: the stable source key
  (`financial/<kind>/<bucket>/<content-hash-prefix>`) is independent of the
  filename, column order, delimiter, BOM, and locale formatting, so renaming or
  re-exporting an unchanged file never duplicates documents.
- **Exact duplicate rows** inside one export are collapsed deterministically;
  brokerage transactions deduplicate on their unique `transaction_id`.
- **Portfolio snapshots** (undated holdings exports) are stored with an explicit
  `snapshot_date: unknown` marker and never receive a fabricated date.
- The four recognized schemas (`bank`, `card`, `investment_transaction`,
  `portfolio`) are distinguished in metadata (`financial_kind`); provider
  provenance stays in metadata, not as separate source types.
- Brokerage PDF statements need no special handling: they flow through the
  existing `file` / PDF ingestion path.

## Unified corpus search, provenance, and filtering (Phase 33)

Ingested files and email share one searchable corpus, regardless of which
`--ingest` command added them. Search results expose their **source identity**:

- `search_documents` (`--search`) and `search_knowledge` results carry the
  owning `source_type` (`file`, `email`, ...) and the `source` identifier
  (the file's workspace-relative path, or the email's normalized `Message-ID`).
- Email results use the **email subject** as their display title when one
  exists; emails without a subject fall back to the `Message-ID`.
- `--search` output is unchanged; provenance is exposed through the agent
  tools and the retrieval layer.

The `filter` both agent tools accept supports an optional `mime_types` list
(in addition to `source_types` and date bounds). A PDF is a `file` source
whose **MIME type** is `application/pdf` — to search only PDFs, pass
`"mime_types": ["application/pdf"]` in the tool's `filter` (never a `"pdf"`
source type). The `mime_types` filter reads the document metadata recorded at
ingestion (`text/plain`, `application/pdf`, `message/rfc822`, ...).
Documents with no MIME metadata never match a MIME filter.

## Vision extraction for image-heavy PDFs (Phase 34a)

Image-only and scanned PDFs are stored at ingestion but contain no readable
text, so they stay unsearchable unless a vision model is configured. Page-level
vision extraction turns those blank pages into searchable chunks:

- Set `PERSONAL_AI_VISION_MODEL` to a local vision-capable Ollama model (the
  current chat model `qwen3.5:9b` supports vision). Leave it unset to keep the
  plain store-without-chunks behavior.
- `PERSONAL_AI_VISION_PROMPT_VERSION` (default `v1`) selects the extraction
  prompt; changing it re-extracts pages produced with an older version.

How it works:

- Only `image_heavy` PDFs are routed to the vision model. Text-heavy and mixed
  documents never send anything to a vision model, and email ingestion stays
  fully model-free regardless of the settings above.
- Each page is rendered locally to a capped PNG and sent only to your local
  Ollama server, which transcribes visible text and describes visible content.
  That vision text is appended to the page's original extracted text and then
  chunked exactly like any other document, so `--search` and the knowledge
  tools find it.
- Results are cached per document, page, vision model, and prompt version.
  Re-running ingestion on unchanged scans makes **zero** vision calls. Empty or
  failed page output is skipped and retried on the next run.
- Nothing is uploaded, and neither extracted text nor vision output is logged.

## Two ways to use it

**CLI** (single question, prints the answer to stdout):

```
uv run python -m personal_ai.cli \
  --workspace /tmp/ws \
  --database /path/to/knowledge.db \
  "Your question here"
```

**HTTP API (Phase 30)** — an OpenAI-compatible service that uses the exact same
Agent, for Open WebUI or any OpenAI-compatible client:

```
uv run python -m personal_ai.server \
  --workspace /tmp/ws \
  --database /path/to/knowledge.db
```

Then `POST http://127.0.0.1:8000/v1/chat/completions` with an OpenAI-style
`messages` array. See [`OPEN_WEBUI.md`](OPEN_WEBUI.md) for endpoints, env vars,
and Open WebUI setup.

Add `--verbose` to the CLI to watch the agent pick evidence sources (tool calls,
rounds, timing). The final answer goes to stdout; progress goes to stderr. The
question-style guidance below applies to both the CLI and the API.

## How to ask good questions

The assistant is most reliable when a question targets a single, well-scoped
topic. Prefer these patterns:

- **Personal knowledge recall** — "What do I know about BCG?"
- **Activity recall** — "What did I search for recently?"
- **Temporal activity** — "What was my most recent career-related activity?"
- **Negative evidence** — "Do I have anything about fly fishing?" (It will
  say plainly when there is no record — it does not invent content.)
- **Activity summary** — "Summarize my activity from January 2026."
- **Watching/search history** — "What videos did I watch about job interviews?"

The assistant retrieves your actual records and answers from them. It does not
fabricate personal data (watched videos, searches, dates, or cross-source
connections). If an item is uncertain it will usually flag it as such.

## Known limitations

These come from the local 9B model, not from the retrieval system, and are
intentionally not papered over.

1. **Compound questions are unreliable.** A question spanning two evidence
   domains in one sentence — for example "What did I watch about BCG, and what
   do my notes say about it?" or "Compare what I know about BCG and McKinsey"
   — may retrieve both domains correctly but then answer only one, drift into
   a generic how-to guide, or include noise. **Split such questions** into one
   single-domain sub-question each:

   > What did I watch about BCG?
   >
   > What do my notes say about BCG?

2. **"Most recent / recent" is approximate.** The assistant returns a real
   recent record, but on different runs it may surface a different one — it
   does not always return the single newest record. Treat "most recent" answers
   as "a real, recent record," not a guarantee of recency ordering.

3. **Occasional blank or generic answers.** On some runs the model ends a
   turn without producing text (a silent blank after tool calls), or produces
   only a fragment. This is stochastic. **Retry the same question** — the next
   attempt is usually fine.

## What you can reliably rely on

- Grounded factual recall of your stored notes and history — no fabrication.
- Trustworthy "there is no record of X" answers across multiple sources.
- Temporal summaries with correct date bounds and real aggregate counts.
- Convergent answers with consistent latency for single-domain questions.

## What you should not rely on

- Unprompted open-ended synthesis across many unrelated documents.
- Compound multi-domain questions without splitting them.
- Deterministic answers: any given question may vary across runs.

Everything asserted about your records is grounded in what is stored; the
weakness is **omission or noise in synthesis, not invented facts**.
