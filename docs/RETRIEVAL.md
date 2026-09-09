# Retrieval Contract

This document describes the canonical contract between the retrieval layer and
the agent. It is deliberately small and stable: the goal is an
explicit, measurable, bounded, and explainable retrieval surface that can later
integrate hybrid search **without** changing the agent-facing shape.

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
return identical ordering. Per-backend ranking differs by design — keyword
retrieval ranks by FTS5 BM25 over the persisted chunk store (smaller = better),
semantic retrieval by cosine similarity (higher = better), and the two scales are
never mixed. Extractions and conversations are merged and re-sorted by the
`RetrievalService`.

## Retrieval boundary (`ChunkIndex`)

The search surface depends on a typed retrieval contract instead of a concrete
backend. Two implementations currently satisfy it. **Keyword (FTS5) retrieval is
the default in every production wiring path** — semantic retrieval is an
explicit, construction-injected choice, never a silent switch.

```
Agent / Chat / Tool
        │
        ▼
  Document-search service   (retrieval.py: search_documents, RetrievalService)
        │
        ▼
      ChunkIndex            (runtime-checkable Protocol; one `search` surface)
        │
        ├──────────────┬─────────────────────────────┐
        ▼              ▼                             ▼
  SQLiteChunkIndex  SemanticChunkIndex      (future) HybridChunkIndex
  (FTS5 keyword,    (stored-vector cosine,            (fusion —
   default)          explicit provider)               not implemented)
```

- `ChunkIndex.search(query, limit, filters)` returns typed `ChunkSearchResult`
  hits, best-ranked first, with a deterministic chunk-id tie-break. Rank
  semantics are backend-specific: the keyword index ranks lexically (FTS5
  BM25, smaller rank = better), the semantic index ranks by cosine similarity
  in `[0, 1]` (higher = better). The two scales are **not directly
  comparable**; only an implementation interprets its own rank. No
  FTS5/SQLite/embedding vocabulary leaks into the contract: queries are free
  text with explicit, deterministic semantics per backend.
- `SQLiteChunkIndex` (in `storage/chunks.py`) owns the FTS5 MATCH query, BM25
  ranking, query sanitization, and document-metadata filtering. It shares the
  caller's connection and requires the chunk/FTS tables created by `ChunkStore`
  (the store remains the persistence authority and satisfies the same contract).
- `SemanticChunkIndex` (in `semantic_index.py`) is the embedding-backed
  sibling: it loads stored vectors from the existing `chunk_embeddings` table
  (`EmbeddingStore`) via a single batched read, embeds the query once through an
  `EmbeddingProvider`, and ranks candidates by cosine similarity — nearest
  neighbours first, brute-force by design (no vector database, no ANN).
  Vectors are only comparable under the provider's current model, so stored
  vectors from another model are excluded (run the existing `EmbeddingBackfiller`
  to renew them); a same-model dimension mismatch fails deterministically
  instead of producing a meaningless score. Search is observationally read-only
  — it never backfills, writes, or re-embeds the corpus — and a provider/store
  failure is an `error`, never a `no_matches`. Embeddings for a corpus remain
  optional: without a configured embedding model, the default keyword path is
  fully functional and never initializes an embedding provider.
- Consumers (`search_documents`, `RetrievalService`, `SearchTool`) depend only
  on the `ChunkIndex` surface, so a future `HybridChunkIndex` over these two
  backends (score fusion + renormalization) can be introduced without touching
  the agent, tools, or this contract.

---

## Hybrid retrieval design (Phase 35 — design only, not implemented)

This section is the design contract for `HybridChunkIndex`. **Nothing here is
implemented and no fusion code exists.** It exists so that Phase 36 can
implement the hybrid layer without reopening the fundamental design questions.

### 35.0 Scope and non-goals

`HybridChunkIndex` is strictly a **retrieval composition layer**. It must
not own policy, memory, filesystem access, document ingestion, corpus
embedding generation, or agent authorization. It reads nothing but the two
backends it is constructed with; it writes nothing; it never resolves
provenance itself and never calls any memory API. Search results remain
untrusted data that is never converted into memories or anything else.

This phase explicitly does **not** implement:

- weighted score fusion,
- reciprocal rank fusion,
- normalized score fusion,
- learned ranking / cross-encoder reranking,
- heuristic score blending,
- any `HybridChunkIndex.search()` that combines results.

### 35.1 Roles of the three indexes

| Index | Responsibility | Ranking |
| --- | --- | --- |
| `SQLiteChunkIndex` | lexical retrieval: FTS5 MATCH, query sanitization, BM25 ranking, document-metadata filtering | BM25, smaller `rank` = better; deterministic `(rank, chunk_id)` |
| `SemanticChunkIndex` | semantic retrieval: query embedding, cosine similarity, embedding/model validation, document-metadata filtering | cosine in `[0, 1]`, higher = better; negative-similarity candidates excluded; deterministic `(-score, chunk_id)` |
| `HybridChunkIndex` (future) | invoke both backends, reconcile candidate sets by `chunk_id`, combine ranking evidence, apply one deterministic final ordering, preserve the `ChunkSearchResult` contract | fused RRF score, higher = better; deterministic `(-score, chunk_id)` |

### 35.2 The existing `ChunkIndex` contract is sufficient — preserve it

`ChunkIndex.search(query, limit=10, filters=None) -> tuple[ChunkSearchResult, ...]`
needs **no change** for hybrid retrieval:

- The hybrid layer can call each backend independently — both implement the
  same signature and semantics.
- `limit` keeps public meaning = **final result limit**. The larger internal
  per-backend *candidate* limit (35.3) is a parameter of the hybrid
  implementation only and never enters the protocol.
- Backend-specific scores are preserved **internally** in per-candidate
  evidence (35.6); `ChunkSearchResult.rank` remains a single opaque float and
  the type is unchanged.
- `filters` pass identically: both backends apply the same `DocumentFilter`
  **before** ranking via the shared `_document_constraints` helper, so a
  hybrid passes the same filter object to each and neither runs after the
  other.
- Duplicate reconciliation keys on `chunk_id` (35.5).
- Final ordering is deterministic by construction (35.10).

`ChunkSearchResult` does **not** change. Backend-specific values
(`bm25_score`, `cosine_score`) are never exposed on the public result.

### 35.3 Candidate-limit semantics (over-fetching)

Hybrid distinguishes:

```
final result limit       = the public `limit` (0..MAX_SEARCH_LIMIT)
backend candidate limit  = how many candidates each backend is asked for
```

Definition (defaults, not tuned — see 35.12):

```
candidate_limit(final_limit) = min(4 * final_limit, 200)
```

Examples: `candidate_limit(10) = 40`, `candidate_limit(50) = 200`.

Rationale. The union of both backends' candidate windows is the real
candidate set, so a final hit that ranks well in one backend but poorly in
the other still has to be *inside the other backend's window* to contribute
evidence. With RRF at `k = 60` (35.7), rank positions beyond roughly `4 ×
the list length` contribute numerically negligible mass, so a 4× window is
the smallest window that captures realistic cross-backend overlap without
chasing arbitrarily long tails. The 200 ceiling is a hard bound on per-query
work (and on the semantic backend's O(V) scan cost) for the local
single-user deployment; `MAX_SEARCH_LIMIT = 50` is the largest `final_limit`
the tool layer will ever pass, so `4 × 50 = 200` is the worst case.

A `final_limit` of `0` returns `()` immediately, without invoking either
backend (mirroring both backends' existing behavior). A negative limit is a
`ValueError` before any backend call.

### 35.4 Filters

Every backend receives the **same** `DocumentFilter`, and filters are applied
by the backends **before** ranking — never "retrieve everything, rank, filter
after" — because ranking-first-then-filtering can drop the correct top-N when
a strongly-ranked candidate fails the filter.

Both current backends support all six `DocumentFilter` fields identically
(`source_types`, `mime_types`, `created_after/before`, `modified_after/
before`) because both delegate to the same `_document_constraints` /
`json_extract(documents.metadata, '$.mime_type')` SQL fragments over the
authoritative `documents` table. There is therefore **no filter semantics gap
between backends today**; if a future backend (for example a real vector DB)
cannot express a filter, hybrid must fail deterministically rather than
silently drop the filter.

### 35.5 Duplicate handling

Identity is **`chunk_id`** — the surrogate primary key of `document_chunks`
and the key both backends emit in `ChunkSearchResult.chunk_id`. Text is never
the identity: two chunks can legitimately carry identical text with different
provenance.

On a duplicate, evidence from **both** backends is retained in the internal
candidate record (35.6) and both rank contributions feed the fused score. The
surviving public `ChunkSearchResult` is content-identical whichever backend
supplied it (same `chunk_id`, `document_id`, `chunk_index`, `text`,
`source_type`, `source`), because provenance is a pure function of
`document_id`. Champion selection for identical content therefore cannot
change observable state.

### 35.6 Internal (private) representation

An internal-only frozen record is required so the final ordering has both
backends' evidence:

```python
@dataclass(frozen=True, slots=True)
class HybridCandidate:          # module-private, never public
    result: ChunkSearchResult   # the canonical hit (identical on duplicate)
    keyword_rank: int | None    # 0-based position in keyword window
    semantic_rank: int | None   # 0-based position in semantic window
    keyword_score: float | None # raw backend score (opaque, diagnostic only)
    semantic_score: float | None
    fusion_score: float         # RRF result; this becomes result.rank
```

Raw backend scores are stored for diagnostics/evaluation only (35.11). They
are never compared across backends and never surface on
`ChunkSearchResult`.

### 35.7 Score semantics and the recommended fusion algorithm

**Naïve additive blending is rejected.** `keyword_score + semantic_score` has
no meaning: the FTS5/BM25 value is a corpus-dependent negative magnitude
with no fixed range, while cosine similarity is a bounded `[0, 1]` pairwise
angle. The two have no common unit; a high-magnitude BM25 value could
arbitrarily dominate. Any normalization-before-weighting scheme (Option A)
would need to calibrate two distributions without an evaluation set and would
still only be meaningful under evaluation (35.12).

Recommended algorithm: **Reciprocal Rank Fusion (RRF)**, rank-based, equal
weights, `k = 60`:

```
RRF(c) = Σ_i  1 / (k + rank_i(c))

where rank_i(c) is the 0-based position of chunk c in backend i's window,
        and a chunk absent from a backend contributes 0.
```

Choice rationale:

- **Scale-free** — consumes only rank *positions*, which both backends
  already emit deterministically; no calibration of incomparable scores.
- **Deterministic and monotone** — with one backend (or one backend empty),
  RRF reduces exactly to that backend's own ordering; with both, it produces a
  single stable ordering.
- **Explainable and simple** — no weights, no learned parameters, no training
  data, trivially unit-testable.
- **Local-first fit** — bounded corpora, one or two queries per tool call.
- **Extensible** — per-backend weights and `k` are single points of change,
  deliberately gated behind the Phase 37 evaluation (35.12).

`k` meaning: RRF dampens each backend's top-ranked contribution;
`k = 60` (the value used in Cormack et al.'s original formulation) keeps the
maximum single-backend contribution near `1/61` so no single backend can
dominate by absolute magnitude, while rank-position differences stay
meaningful within a window. This is a documented constant, **not a tuned
weight** — see 35.12.

### 35.8 Empty-backend behavior

These are distinct states and must be distinguished by construction:

- **Both backends return `()`** → hybrid returns `()` (a real `no_matches`).
- **Keyword empty, semantic non-empty** → semantic results, in semantic
  order (RRF is monotone over one backend).
- **Semantic empty, keyword non-empty** → keyword results, in keyword order.
- **Semantic "no compatible embeddings"** (its Phase 34 early-return `()` with
  no provider call) is an *empty backend*, exactly like no semantic match —
  never an infrastructure error.

"Empty" is defined as a backend *succeeding* and returning an empty tuple. A
backend that **raises** is a *failure* (35.9). Because both backends are
exception-based on real failures, hybrid distinguishes the two statically.

### 35.9 Backend failure policy — fail closed

**Recommendation: fail closed.** If any requested backend raises (provider or
store failure), hybrid raises, and the existing tool envelope converts that
into the canonical `error` / `retrieval_unavailable` outcome. Hybrid never
returns a partial result set on failure.

Rationale, grounded in this repository:

- The [status contract](#status-separation) in this document already treats
  `results` / `no_matches` / `error` as strictly disjoint; a partial hybrid
  result would smuggle an incomplete candidate set into `results`, and the
  model could wrongly reason "there is nothing relevant" over a
  missing-backend corpus.
- Silent degradation is the failure mode explicitly to be avoided: *"explicit
  hybrid mode should have deterministic failure semantics rather than silently
  changing retrieval mode."* Returning keyword-only results on semantic outage
  is indistinguishable from keyword mode to the agent.
- Locally, FTS5 is nearly always healthy; hybrid failing closed on an
  embedding outage is a visible, debuggable signal, and the operator can
  select a non-hybrid mode at the wiring layer (35.11).

A future *graceful-degradation* variant is deliberately **not** designed now:
it would require a new `RetrievalOutcome` state (e.g. a `partial` status that
reports which backend failed) and a product decision about how the agent
interprets it. That is a change to the outcome contract, not to
`ChunkIndex`, and is out of scope until there is a reason for it.

### 35.10 Query embedding and determinism

Hybrid never embeds anything. It invokes `SemanticChunkIndex.search(...)`,
which owns the embedding-provider interaction and generates **at most one**
query embedding per query. Hybrid similarly never parses or normalizes query
text — that is the keyword backend's job (`_match_expression`).

Deterministic ordering — every point fixed:

1. **Candidate identity:** `chunk_id`.
2. **Invocation order:** keyword backend, then semantic backend (fixed, so
   evidence processing order is stable).
3. **Ranking:** RRF, `k = 60`, equal weights; missing backend → 0.
4. **Duplicate handling:** per-candidate evidence merge (35.5, 35.6).
5. **Missing-backend behavior:** empty contribution; empty tuple + failure
   distinction per 35.8–35.9.
6. **Final order:** `fusion_score` descending, then `chunk_id` ascending
   (same tie-break convention as both existing backends).

Two candidates with identical fused score, a candidate present in only one
backend, and two candidates with equal per-backend contributions all resolve
through the same rule: equal fused scores → `chunk_id` ascending. The result
of repeated identical searches is identical.

### 35.11 Configuration recommendation

Selection belongs at the **dependency-construction layer**, never as a
silent switch, with an optional operator override exposed later:

- **Default:** `keyword` — the existing wiring passes `ChunkStore` (which
  satisfies `ChunkIndex`) untouched. Existing deployments are unaffected.
- Configuring an embedding model **must not change retrieval behavior**; a
  semantic or hybrid index is only ever built by explicit wiring.
- **Semantic mode** = construct `SemanticChunkIndex(connection, provider)`
  (already possible today by injection).
- **Hybrid mode** = construct `HybridChunkIndex(keyword_index, semantic_index)`
  with the semantic slot built only when an `EmbeddingProvider` is available.
- If and when an operator-visible knob is added, the recommended name is
  `PERSONAL_AI_RETRIEVAL_MODE=keyword|semantic|hybrid` (default `keyword`),
  read at wiring time and mapping onto the same constructor choices — **not**
  a per-query parameter. That env knob is **not implemented in this phase**.

### 35.12 Evaluation and tuning (what Phase 37 validates, not Phase 36)

No fusion parameter is tuned in Phase 36: `k = 60`, the `4×/200` candidate
window, and equal weights are constants with documented rationale, not
weights. Without an evaluation set, `KEYWORD_WEIGHT=0.6`-style settings would
be arbitrary.

Phase 37 builds a small synthetic-fixture evaluation suite measuring:
exact-keyword recall (hybrid must not bury a precise keyword match), semantic
paraphrase recall (the semantic leg's contribution), precision@K, ranking
stability across `k` and `candidate_limit`, filter correctness, and
no-result/failure behavior. Numeric tuning of `k`, the candidate window, or
per-backend weights happens only against that suite.

Diagnostics for tests/debug/evaluation stay internal (35.6) — an optional
debug view exposing `keyword_rank`, `semantic_rank`, `keyword_score`,
`semantic_score`, `fusion_score` per `chunk_id` is useful for that purpose but
is **not** part of the public `ChunkSearchResult` and does not affect the
`ChunkIndex` contract.

### 35.13 Cost model

Hybrid roughly doubles per-query retrieval work: one FTS query plus one
semantic scan (O(vocab) FTS work, O(stored vectors) for the current
brute-force semantic backend) plus an in-memory O(U log U) sort over the
union `U ≤ 2 × candidate_limit`. That is acceptable for the local, single-user,
bounded-corpus deployment — one or two hybrid queries per tool call, each
bounded and deterministic. A future ANN/vector backend would replace the
semantic O(V) scan when corpora grow; it is explicitly out of scope now.

### 35.14 Agent, tool, HTTP, and policy boundaries

- Agent tools keep calling `search_documents` / `SearchTool` unchanged;
  `search_documents` needs no new public parameter.
- `RetrievalService` keeps receiving a `ChunkIndex`; a hybrid instance is
  injected below it exactly like the keyword index today.
- OpenAI-compatible endpoints and policy enforcement are unchanged: policy
  sits above `RetrievalService` and never sees a backend.
- `HybridChunkIndex` is added *below* the existing boundary only:

```
RetrievalService
        ↓
    ChunkIndex
        ↓
HybridChunkIndex
     ↙        ↘
ChunkIndex  ChunkIndex
```

No backend depends on `RetrievalService`, agents, or tools; no circular
dependency exists.

### 35.15 Phase 36 implementation shape (for reference)

```python
class HybridChunkIndex:
    def __init__(
        self,
        keyword_index: ChunkIndex,   # slot 0 — lexical evidence
        semantic_index: ChunkIndex,  # slot 1 — semantic evidence
        *,
        candidate_limit: ... = default(35.3),
        k: float = 60.0,
    ) -> None: ...
    def search(self, query, limit=10, filters=None) -> tuple[ChunkSearchResult, ...]:
        # validate → call keyword → call semantic → union/dedupe →
        # per-backend evidence → RRF → sort (fusion desc, chunk_id asc) →
        # slice to public limit → forward provenance from the canonical hit
```

The hybrid depends on the **abstraction**, not the concrete backends: both
arguments are `ChunkIndex`, and lexical-vs-semantic evidence is attributed by
constructor slot (position), never by type. This keeps the public protocol
backend-free — the hybrid layer has no idea a `ChunkIndex` is "really"
`SQLiteChunkIndex`, `SemanticChunkIndex`, or another composition.

Because `ChunkSearchResult` is frozen, the fused score is applied with
`dataclasses.replace(candidate.result, rank=fusion_score)` before returning —
the `ChunkSearchResult` type and its field set are unchanged.

### 35.16 Phase 36 test matrix (to be implemented with the code)

- **Candidate union:** keyword-only result; semantic-only result; overlapping
  result (both orderings interleaved).
- **Dedup:** same `chunk_id` in both windows → one result with both
  contributions; identical text in two different `chunk_id`s stays two.
- **Ranking:** lexical-only winner; semantic-only winner; balanced winner;
  fused-score tie → `chunk_id` ascending; a single-backend candidate loses to
  a both-backend candidate at comparable top positions.
- **Filters:** identical filter reaches both; filtered-out candidates never
  leak; all six `DocumentFilter` fields exercised through hybrid.
- **Limits:** `candidate_limit` governs the windows; `final_limit` governs the
  slice; fewer candidates than requested; `limit=0` → `()` without backend
  calls; negative limit → `ValueError`.
- **Empty:** both empty → `()`; keyword empty → semantic results; semantic
  empty → keyword results; "no compatible embeddings" behaves as semantic
  empty, not error.
- **Failure:** keyword raises, semantic fails → hybrid raises → tool
  `error`/`retrieval_unavailable`; provider failure propagates; failure is
  never an empty `results`.
- **Determinism:** repeated identical searches produce identical ordering.
- **Security:** search is read-only (tables unchanged); no policy bypass; no
  memory writes; provenance forwarded, never recomputed.
- **Contract:** `isinstance(HybridChunkIndex, ChunkIndex)`; frozen
  `ChunkSearchResult` values; no backend-specific fields on public results.

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
