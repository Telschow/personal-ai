"""Deterministic synthetic-fixture evaluation harness for chunk retrieval.

Measurement-only harness for the three ``ChunkIndex`` backends — keyword
(FTS5), semantic (embedding), and their RRF hybrid — against hand-authored
relevance judgments. Nothing here changes retrieval behaviour, and nothing
in this module is wired into any production default: it exists so future
retrieval changes can be judged by evidence instead of intuition.

The harness is deliberately small and explicit:

- Metric helpers are pure functions over chunk identifiers (never text,
  scores, or internal ranks) so measurements are independent of any one
  backend's ranking scale.
- ``evaluate_case`` only ever drives backends through the public
  ``search(query, limit, filters)`` surface. It NEVER swallows a backend
  failure: a raising backend propagates (fail closed), so the harness can
  never record a crash as a legitimate zero-result evaluation.

Metric conventions (documented, deterministic, unit-tested):

- ``recall@k = |relevant ∩ top-k| / |relevant|``; an empty relevant set is
  vacuously ``1.0`` (there is nothing that failed to be retrieved).
- ``precision@k = |relevant ∩ top-k| / k`` with a **fixed** denominator k
  (trec_eval convention: shorter result lists are penalised, an empty
  retrieval scores ``0.0``).
- ``hit@k`` is ``1`` iff at least one relevant chunk is within the top-k;
  an empty relevant set is ``0`` (nothing relevant exists to be hit).
- ``reciprocal_rank`` is ``1 / (position + 1)`` with **0-based** positions
  on the retrieved list (independent of the hybrid layer's internal RRF
  ``k = 60`` positions), and ``0.0`` when nothing relevant is retrieved.

Diagnostics stay aggregate-only: ``summarize`` prints per-backend numeric
means over case names and counts — never query text, chunk content, or
document provenance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from personal_ai.storage.chunks import (
    DEFAULT_SEARCH_LIMIT,
    ChunkSearchResult,
    DocumentFilter,
)


@runtime_checkable
class ChunkSearcher(Protocol):
    """The minimal retrieval surface the harness drives.

    Structurally identical to :class:`~personal_ai.retrieval.ChunkIndex` but
    kept local so the harness measures any duck-typed backend (real indexes,
    recorded fakes, unions) without importing into the retrieval boundary.
    """

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """Return hits for this query, best-ranked first."""
        ...


# --- Metric conventions ------------------------------------------------------


def recall_at_k(relevant: AbstractSet[str], returned: Sequence[str], k: int) -> float:
    """Return the fraction of relevant chunks that appear in the top-k.

    An empty relevant set is vacuously perfect (``1.0``).
    """
    if k < 1:
        msg = f"k must be >= 1, got {k}"
        raise ValueError(msg)
    if not relevant:
        return 1.0
    relevant_set = frozenset(relevant)
    hits = sum(1 for chunk_id in returned[:k] if chunk_id in relevant_set)
    return hits / len(relevant_set)


def precision_at_k(
    relevant: AbstractSet[str], returned: Sequence[str], k: int
) -> float:
    """Return the fraction of top-k slots that hold a relevant chunk.

    The denominator is always exactly ``k`` (trec_eval convention), so
    shorter lists are penalised: an empty retrieval scores ``0.0``.
    """
    if k < 1:
        msg = f"k must be >= 1, got {k}"
        raise ValueError(msg)
    relevant_set = frozenset(relevant)
    hits = sum(1 for chunk_id in returned[:k] if chunk_id in relevant_set)
    return hits / k


def hit_at_k(relevant: AbstractSet[str], returned: Sequence[str], k: int) -> int:
    """Return ``1`` iff at least one relevant chunk is within the top-k.

    An empty relevant set has nothing to hit and scores ``0``.
    """
    if k < 1:
        msg = f"k must be >= 1, got {k}"
        raise ValueError(msg)
    if not relevant:
        return 0
    relevant_set = frozenset(relevant)
    return 1 if any(chunk_id in relevant_set for chunk_id in returned[:k]) else 0


def reciprocal_rank(relevant: AbstractSet[str], returned: Sequence[str]) -> float:
    """Return ``1 / (position + 1)`` of the first relevant hit (0-based).

    Independent of the hybrid layer's RRF ``k = 60`` constant: this is the
    standard first-relevant-position metric, ``0.0`` when nothing matches.
    """
    if not relevant:
        return 0.0
    relevant_set = frozenset(relevant)
    for position, chunk_id in enumerate(returned):
        if chunk_id in relevant_set:
            return 1.0 / (position + 1)
    return 0.0


def mean_reciprocal_rank(ranks: Sequence[float]) -> float:
    """Return the arithmetic mean of reciprocal ranks (``0.0`` when empty)."""
    if not ranks:
        return 0.0
    return sum(ranks) / len(ranks)


# --- Evaluation types --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One query with its hand-authored relevance judgment.

    ``relevant_chunk_ids`` is the ground truth this fixture asserts; empty
    means "nothing in the indexed corpus is relevant to this query".
    ``filters``, when set, is the document-metadata constraint applied to
    every backend in the case.
    """

    name: str
    query: str
    relevant_chunk_ids: frozenset[str] = field(default_factory=frozenset)
    filters: DocumentFilter | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """The measured outcome of one backend for one case at one ``k``.

    ``returned_chunk_ids`` is the deterministic, order-preserving projection
    of the backend's results. Metrics are computed on demand from the stored
    identifiers, never from scores or ranks.
    """

    case: EvaluationCase
    backend: str
    k: int
    returned_chunk_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        """Number of chunks the backend returned for this query."""
        return len(self.returned_chunk_ids)

    @property
    def recall(self) -> float:
        """``recall@k`` against the case's relevance judgment."""
        return recall_at_k(
            self.case.relevant_chunk_ids, self.returned_chunk_ids, self.k
        )

    @property
    def precision(self) -> float:
        """``precision@k`` against the case's relevance judgment."""
        return precision_at_k(
            self.case.relevant_chunk_ids, self.returned_chunk_ids, self.k
        )

    @property
    def hit(self) -> int:
        """``hit@k`` against the case's relevance judgment."""
        return hit_at_k(self.case.relevant_chunk_ids, self.returned_chunk_ids, self.k)

    @property
    def reciprocal_rank(self) -> float:
        """First-relevant-position reciprocal rank (0-based positions)."""
        return reciprocal_rank(self.case.relevant_chunk_ids, self.returned_chunk_ids)

    def format_row(self) -> str:
        """One deterministic, aggregate-only report line."""
        return (
            f"{self.backend:<10} {self.case.name:<22} k={self.k} "
            f"count={self.count:>3} recall={self.recall:.3f} "
            f"precision={self.precision:.3f} hit={self.hit} "
            f"rr={self.reciprocal_rank:.3f}"
        )


# --- Driver ------------------------------------------------------------------


def evaluate_case(
    case: EvaluationCase,
    backends: Mapping[str, ChunkSearcher],
    *,
    k: int,
    limit: int | None = None,
) -> tuple[EvaluationResult, ...]:
    """Evaluate ``case`` against every backend and return one result each.

    Backends are driven only through their public ``search`` surface with
    ``limit`` (defaulting to ``k``) and the case's filters. Backend failures
    propagate unchanged — the harness never turns a crash into a measured
    zero-result. ``k`` and ``limit`` must be at least 1.
    """
    if k < 1:
        msg = f"k must be >= 1, got {k}"
        raise ValueError(msg)
    effective_limit = k if limit is None else limit
    if effective_limit < 1:
        msg = f"limit must be >= 1, got {limit}"
        raise ValueError(msg)

    results = []
    for backend, index in backends.items():
        hits = index.search(case.query, limit=effective_limit, filters=case.filters)
        results.append(
            EvaluationResult(
                case=case,
                backend=backend,
                k=k,
                returned_chunk_ids=tuple(hit.chunk_id for hit in hits),
            )
        )
    return tuple(results)


def summarize(results: Sequence[EvaluationResult]) -> str:
    """Return a deterministic, aggregate-only per-backend summary table.

    Numbers only — per-backend case count and mean recall / precision /
    hit-rate / mean reciprocal rank. Never query text, chunk content, or
    document provenance.
    """
    grouped: list[tuple[str, list[EvaluationResult]]] = []
    for result in results:
        for name, entries in grouped:
            if name == result.backend:
                entries.append(result)
                break
        else:
            grouped.append((result.backend, [result]))

    lines = ["backend        cases  recall  precision  hit   mrr"]
    for backend, entries in grouped:
        count = len(entries)
        lines.append(
            f"{backend:<14} {count:>5}  "
            f"{sum(e.recall for e in entries) / count:>6.3f}  "
            f"{sum(e.precision for e in entries) / count:>9.3f}  "
            f"{sum(e.hit for e in entries) / count:>5.3f}  "
            f"{sum(e.reciprocal_rank for e in entries) / count:>6.3f}"
        )
    return "\n".join(lines)
