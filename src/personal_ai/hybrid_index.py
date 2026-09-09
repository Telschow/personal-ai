"""Hybrid (keyword + semantic) chunk retrieval behind the ``ChunkIndex`` boundary.

This module implements the Phase 35 hybrid fusion contract unchanged: it
composes two ``ChunkIndex`` backends — a lexical (keyword/FTS5) backend and a
semantic (embedding) backend — into a single ``ChunkIndex`` implementation and
merges their candidate sets with Reciprocal Rank Fusion (RRF, ``k = 60``, equal
weights).

The hybrid layer knows nothing about concrete backends. Both constructor slots
are typed :class:`~personal_ai.retrieval.ChunkIndex`; evidence is attributed by
constructor slot (keyword = slot 0, semantic = slot 1), never by runtime type
inspection. It performs no embedding, no query parsing, no provenance
resolution, and no persistence — the backends own those responsibilities
(:class:`~personal_ai.semantic_index.SemanticChunkIndex` embeds the query at
most once; the keyword backend sanitizes query text; provenance is forwarded
from the canonical hit, never recomputed).

Binding behavior contract (Phase 35 decisions, not suggestions):

- ``candidate_limit(final_limit) = min(4 * final_limit, 200)`` governs the
  per-backend over-fetch window; the public ``limit`` bounds only the final
  result slice.
- Duplicate identity is ``chunk_id``: a chunk present in both windows merges
  both rank contributions into exactly one candidate.
- ``RRF(c) = keyword_contribution + semantic_contribution`` with
  ``contribution = 1 / (60 + rank)`` where ``rank`` is the **0-based** tuple
  position of the chunk in that backend's returned window; a backend a chunk is
  absent from contributes 0.
- Final order: ``fusion_score`` descending, then ``chunk_id`` ascending.
- Empty or whitespace-only queries and ``limit == 0`` return ``()`` without
  touching either backend; a negative limit raises ``ValueError``.
- Both backends receive the same query, the same ``DocumentFilter``, and the
  same candidate limit, invoked in fixed order (keyword, then semantic).
- An empty backend result is a valid empty contribution — including the
  semantic backend's "no compatible embeddings" early return; a backend that
  **raises** fails the whole search closed: hybrid never returns partial
  results merged from a failed backend.

Keyword retrieval remains the default in every production wiring path;
``HybridChunkIndex`` is only ever built by explicit construction injection.
"""

from dataclasses import dataclass, replace

from personal_ai.retrieval import ChunkIndex
from personal_ai.storage.chunks import (
    DEFAULT_SEARCH_LIMIT,
    ChunkSearchResult,
    DocumentFilter,
)

# RRF inverse-ranking constant (k = 60, from Cormack et al.'s formulation).
# A documented, deliberately untuned constant — see docs/RETRIEVAL.md §35.7
# and §35.12: no weighing, no tuning, no configurability in this phase.
RRF_K = 60.0

# Per-backend over-fetch window: 4 × the final limit, hard-capped at 200 so a
# single query can never materialize unbounded per-backend work.
_CANDIDATE_MULTIPLIER = 4
_CANDIDATE_LIMIT_CEILING = 200


def _candidate_limit(final_limit: int) -> int:
    """Return the per-backend candidate window for a final result limit."""
    if final_limit < 0:
        msg = f"Search limit must be non-negative, got {final_limit}"
        raise ValueError(msg)
    return min(_CANDIDATE_MULTIPLIER * final_limit, _CANDIDATE_LIMIT_CEILING)


def _rrf_score(keyword_rank: int | None, semantic_rank: int | None) -> float:
    """Compute the RRF contribution with k = 60, equal weights, 0-based ranks.

    A ``None`` rank (chunk absent from that backend) contributes zero.
    """
    score = 0.0
    if keyword_rank is not None:
        score += 1.0 / (RRF_K + keyword_rank)
    if semantic_rank is not None:
        score += 1.0 / (RRF_K + semantic_rank)
    return score


@dataclass(frozen=True, slots=True)
class _HybridCandidate:
    """Module-private per-chunk evidence accumulator. Never public API.

    ``result`` is the canonical hit (content-identical across backends for the
    same ``chunk_id``); ``keyword_rank``/``semantic_rank`` are the 0-based
    positions in each backend's window (``None`` when absent). Raw backend
    score/rank values are deliberately not retained: RRF consumes positions
    only, so the tuple positions are the entire evidence.
    """

    result: ChunkSearchResult
    keyword_rank: int | None
    semantic_rank: int | None

    @property
    def fusion(self) -> float:
        """The fused RRF score; this becomes the public ``rank``."""
        return _rrf_score(self.keyword_rank, self.semantic_rank)


class HybridChunkIndex:
    """Concrete ``ChunkIndex`` implementation composing two backends with RRF.

    Composes the keyword (slot 0) and semantic (slot 1) backends behind the
    same ``ChunkIndex`` contract consumers already use, so the agent, tool,
    and service layers cannot tell a hybrid from any other backend. Search is
    read-only and deterministic.
    """

    def __init__(
        self,
        keyword_index: ChunkIndex,
        semantic_index: ChunkIndex,
    ) -> None:
        self._keyword_index = keyword_index
        self._semantic_index = semantic_index

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """Return hybrid hits, best-ranked first, capped at ``limit``.

        Empty/whitespace queries and ``limit == 0`` return no results without
        invoking either backend. ``limit`` must be non-negative; negative
        limits raise ``ValueError`` before any backend call. Both backends
        receive the same query, ``filters``, and candidate window
        (``min(4 * limit, 200)``), in fixed keyword-then-semantic order.
        Candidate order is by RRF score descending, then ``chunk_id``
        ascending; the fused score becomes each public result's ``rank``.
        """
        if limit < 0:
            msg = f"Search limit must be non-negative, got {limit}"
            raise ValueError(msg)
        if limit == 0 or not query.strip():
            return ()

        candidate_limit = _candidate_limit(limit)
        keyword_hits = self._keyword_index.search(
            query, limit=candidate_limit, filters=filters
        )
        semantic_hits = self._semantic_index.search(
            query, limit=candidate_limit, filters=filters
        )

        candidates: dict[str, _HybridCandidate] = {}
        for position, hit in enumerate(keyword_hits):
            candidates[hit.chunk_id] = _HybridCandidate(
                result=hit, keyword_rank=position, semantic_rank=None
            )
        for position, hit in enumerate(semantic_hits):
            existing = candidates.get(hit.chunk_id)
            if existing is None:
                candidates[hit.chunk_id] = _HybridCandidate(
                    result=hit, keyword_rank=None, semantic_rank=position
                )
            else:
                candidates[hit.chunk_id] = _HybridCandidate(
                    result=existing.result,
                    keyword_rank=existing.keyword_rank,
                    semantic_rank=position,
                )

        ordered = sorted(
            candidates.values(),
            key=lambda candidate: (-candidate.fusion, candidate.result.chunk_id),
        )
        return tuple(
            replace(candidate.result, rank=candidate.fusion)
            for candidate in ordered[:limit]
        )
