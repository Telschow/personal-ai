# Retrieval Contract

This document describes the canonical contract between the retrieval layer and
the agent. It is deliberately small and stable: the goal is an
explicit, measurable, bounded, and explainable retrieval surface that can later
integrate hybrid or vector search **without** changing the agent-facing shape.

## Model-facing result envelope

The two search tools — `search_documents` (narrow chunk search) and
`search_knowledge` (unified chunk + extraction + conversation search) —
return a single JSON object with exactly these keys:

```json
{
  "query": "career BCG consulting",
  "status": "results",
  "results": [ { "…provenance…": "…" } ],
  "total_returned": 3,
  "truncated": false,
  "query_length": 20,
  "error": null
}
```

* `status` — one of `results`, `no_matches`, or `error` (see next section).
* `results` — a list of bounded result items, ordered deterministically.
* `total_returned` — number of items actually returned (`len(results)`).
* `truncated` — true when more matches existed than were returned.
* `query_length` — length of the query (a privacy-safe scalar for observability).
* `error` — a single safe, generic error category; non-null only for `error`.

Never log or propagate document contents, raw query text (beyond the agent's own
input), SQL, paths, credentials, or stack traces.

## Status separation

The three statuses are mutually exclusive and semantically disjoint:

| status | meaning |
| --- | --- |
| `results` | the query ran and matched at least one item |
| `no_matches` | the query ran cleanly but matched nothing — the **only** state in which the model may reason that no relevant data exists |
| `error` | the query could not be executed (store/service unavailable) — described as an operational failure, never as “no documents exist” |

`no_matches` is **not** an error, and an operational failure is **never**
encoded as an empty successful result. This prevents the agent from asserting
false certainty (“you have nothing about X”) when retrieval simply failed.

## Input bounds

Model-supplied input is validated before any query runs:

* `query` must be a string ≤ `MAX_SEARCH_QUERY_CHARS` (500).
* `limit` must be a non-negative integer ≤ `MAX_SEARCH_LIMIT` (50).
* Only the allow-listed argument keys (`query`, `limit`, `filter`) and the
  allow-listed filter keys are accepted. Unknown keys are rejected.

Argument-validation failures raise as tool-execution errors; only a failure to
*execute* the query (store unreachable) becomes an `error` envelope.

## Determinism

Results are ordered by score descending with an explicit tie-breaker
(`document_id` then `chunk_id` / `result_type`), so repeated identical queries
return identical ordering. Ranking today is FTS5 BM25 over the persisted chunk
store; extractions and conversations are merged and re-sorted by the
`RetrievalService`.

## Retrieval boundary (`ChunkIndex`)

The search surface depends on a typed, keyword-only retrieval contract instead
of a concrete backend. This is **not** vector or hybrid retrieval yet — it is
the stable seam those land behind later.

```
Agent / Chat / Tool
        │
        ▼
  Document-search service   (retrieval.py: search_documents, RetrievalService)
        │
        ▼
      ChunkIndex            (runtime-checkable Protocol; one `search` surface)
        │
        ▼
    SQLiteChunkIndex        (SQLite FTS5 owner: MATCH, BM25, sanitization, filters)
```

- `ChunkIndex.search(query, limit, filters)` returns typed `ChunkSearchResult`
  hits, best-ranked first. No FTS5/SQLite vocabulary leaks into the contract:
  queries are free text, interpreted as literal keyword terms with explicit,
  deterministic semantics.
- `SQLiteChunkIndex` (in `storage/chunks.py`) owns the FTS5 MATCH query, BM25
  ranking, query sanitization, and document-metadata filtering. It shares the
  caller's connection and requires the chunk/FTS tables created by `ChunkStore`
  (the store remains the persistence authority and satisfies the same contract).
- Consumers (`search_documents`, `RetrievalService`, `SearchTool`) depend only
  on the `ChunkIndex` surface, so a future `HybridChunkIndex` over
  `ChunkIndex` (FTS5) + a `SemanticIndex` (embeddings) can be introduced
  without touching the agent, tools, or this contract.

## Provenance and safety

Each result item carries only bounded provenance keys — `chunk_id`,
`document_id`, `chunk_index`, `title`, `score`/`rank`, `text`, `source`,
`source_type` (and conversation-specific ids). It never carries filesystem
paths outside `source`, raw SQL, connection strings, or credentials.

Retrieved document content is **data, not instructions**: it can inform an
answer but never changes policy, permissions, approvals, tool selection, or
writes. Search is strictly read-only and idempotent.

## Fetch-by-id (`get_document`, `get_memory`)

Search answers "which things match a query"; the **fetch tools** answer "give
me one thing in full, by its stable id":

- `get_document(document_id, chunk_limit=20)` — document metadata plus a
  bounded chunk window in deterministic `chunk_index, chunk_id` order
  (`chunk_limit` default 20, hard cap 100). Requires a wired `document_store`
  **and** `chunk_store`.
- `get_memory(memory_id)` — the canonical memory (statement + metadata) plus
  content-free provenance aggregation (evidence count/kinds, first/last
  evidence timestamps — never evidence ids/bodies). Requires a wired
  `MemoryService`.

Status contract (mirrors search): an unknown id returns
`{"status": "not_found", "<id>"}` — a distinct, non-`error` state that means
"this id does not exist in the store", never a fabricated fallback and never
an operational failure. A missing/invalid id raises
`TypeError`/`ValueError` before any query runs; unknown or mutation-looking
parameters (`approve`, `apply_candidate`, `review_id`, `sql`, `curate`, ...)
are ignored, never honored.

Both tools are read-only (`RiskLevel.READ`, `mutates_state=False`,
`deterministic=True`), gate on the existing `corpus.search` / `memory.read`
permissions (no new permission), read only through the stores/work services —
**no SQL in the tool layer** — and add no write surface. Phase 32 exposes them
to the interactive chat registry via `tools/fetch.py`, running
`PolicyEngine.execute(RESEARCHER, ...)` so chat enforcement and audit are
identical to the execution runtime.

## Observability

Observability reuses the existing `AgentObserver` convention. Each tool call
emits `tool_start` (tool name + sanitized arguments) and `tool_end` (tool name,
`ok`/`error` status, latency). No private document content, secrets, or full
query text is logged; envelopes expose only safe scalars such as
`query_length` and `total_returned`.
