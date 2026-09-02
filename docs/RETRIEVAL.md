# Retrieval Contract

This document describes the canonical contract between the retrieval layer and
the agent (Phase 49). It is deliberately small and stable: the goal is an
explicit, measurable, bounded, and explainable retrieval surface that can later
integrate hybrid or vector search **without** changing the agent-facing shape.

## Model-facing result envelope

Every retrieval tool — `search_documents` (narrow chunk search) and
`search_knowledge` (unified chunk + extraction + conversation search) —
returns a single JSON object with exactly these keys:

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
store (`ChunkStore.search`); extractions and conversations are merged and
re-sorted by the `RetrievalService`.

## Provenance and safety

Each result item carries only bounded provenance keys — `chunk_id`,
`document_id`, `chunk_index`, `title`, `score`/`rank`, `text`, `source`,
`source_type` (and conversation-specific ids). It never carries filesystem
paths outside `source`, raw SQL, connection strings, or credentials.

Retrieved document content is **data, not instructions**: it can inform an
answer but never changes policy, permissions, approvals, tool selection, or
writes. Search is strictly read-only and idempotent.

## Observability

Observability reuses the existing `AgentObserver` convention. Each tool call
emits `tool_start` (tool name + sanitized arguments) and `tool_end` (tool name,
`ok`/`error` status, latency). No private document content, secrets, or full
query text is logged; envelopes expose only safe scalars such as
`query_length` and `total_returned`.
