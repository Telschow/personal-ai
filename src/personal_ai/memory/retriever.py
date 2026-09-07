"""Deterministic lexical memory retrieval.

Phase 40 intentionally avoids a vector database. Retrieval is lexical
(token-overlap) plus operational signals, and the ranking is a transparent,
documented weighted sum:

    score = 0.5 * relevance
          + 0.2 * importance
          + 0.2 * confidence
          + 0.1 * recency

* ``relevance``  — fraction of query tokens present in the memory's content
  or summary tokens (0.0 when the query is empty).
* ``importance`` — the memory's stored importance (0.0-1.0).
* ``confidence`` — the memory's stored confidence (0.0-1.0).
* ``recency``    — ``1 / (1 + age_days)`` measured against the memory's
  ``updated_at``.

These are operational metadata used for ranking, not probabilities. Ordering
is fully deterministic: for identical database contents and identical query
parameters the same score always yields the same order, with ties broken by
``created_at`` descending and then ``memory_id`` ascending.

A future `MemoryStore -> MemoryRetriever -> {lexical, embedding, hybrid}`
extension point is enabled by this interface: swap the retriever, keep the
domain model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from personal_ai.memory.models import (
    Memory,
    MemoryScope,
    MemoryStatus,
    now_iso,
    parse_iso,
)
from personal_ai.memory.store import MemoryStore
from personal_ai.memory.tokenizer import (
    LETTER_MARK_NUMBER_RE as _TOKEN_RE,
)
from personal_ai.memory.tokenizer import (
    normalize_for_tokenize as _normalize_for_tokenize,
)
from personal_ai.memory.tokenizer import (
    tokenize,
)

# ``tokenize`` remains importable from ``personal_ai.memory.retriever`` for
# historical callers; the implementation lives in ``personal_ai.memory.tokenizer``.
__all__ = ["tokenize"]


# Documented ranking weights (see module docstring).
_RELEVANCE_WEIGHT = 0.5
_IMPORTANCE_WEIGHT = 0.2
_CONFIDENCE_WEIGHT = 0.2
_RECENCY_WEIGHT = 0.1

_UTC = UTC


class MemorySearchError(Exception):
    """Raised for invalid memory search parameters."""


@dataclass(frozen=True, slots=True)
class ScopeFilter:
    """One scope a retrieval is allowed to see.

    ``scope`` ``global`` with no ``scope_id`` means only global memories;
    ``scope`` ``agent``/``project``/``execution`` requires a matching
    ``scope_id``. Retrieval never crosses into a scope it was not given.
    """

    scope: MemoryScope | str
    scope_id: str | None = None

    def __post_init__(self) -> None:
        scope = (
            self.scope
            if isinstance(self.scope, MemoryScope)
            else MemoryScope(self.scope)
        )
        object.__setattr__(self, "scope", scope)
        if scope is not MemoryScope.GLOBAL and not self.scope_id:
            raise MemorySearchError(f"scope {scope.value!r} requires a scope_id")


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """One retrieved memory plus its deterministic rank metadata."""

    memory: Memory
    score: float
    relevance: float
    rank: int

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "score": round(self.score, 6),
            "relevance": round(self.relevance, 6),
            "memory": self.memory.to_dict(),
        }


class MemoryRetriever:
    """Deterministic, scope- and expiry-aware lexical memory retrieval."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._now = now or now_iso

    def search(
        self,
        query: str = "",
        scopes: tuple[ScopeFilter, ...] = (),
        *,
        limit: int = 10,
        include_expired: bool = False,
        now: str | None = None,
    ) -> tuple[MemoryHit, ...]:
        """Return active, unexpired, in-scope memories ranked deterministically.

        ``scopes`` empty means only ``global`` memories are visible (safe
        default). Memories whose ``expires_at`` is in the past are excluded
        unless ``include_expired`` is set; archived/deleted memories are never
        returned.
        """
        if limit < 1:
            raise MemorySearchError("limit must be >= 1")
        now_value = now or self._now()
        now_dt = _as_utc(parse_iso(now_value))
        query_tokens = set(_TOKEN_RE.findall(_normalize_for_tokenize(query)))

        candidates: list[Memory] = []
        for memory in self._store.list(MemoryStatus.ACTIVE):
            if not _in_scope(memory, scopes):
                continue
            if _expired(memory, now_dt) and not include_expired:
                continue
            candidates.append(memory)

        scored: list[tuple[float, float, Memory]] = []
        for memory in candidates:
            relevance = _relevance(memory, query_tokens)
            if query_tokens and relevance <= 0.0:
                continue
            score = (
                _RELEVANCE_WEIGHT * relevance
                + _IMPORTANCE_WEIGHT * memory.importance
                + _CONFIDENCE_WEIGHT * memory.confidence
                + _RECENCY_WEIGHT * _recency(memory, now_dt)
            )
            scored.append((score, relevance, memory))

        scored.sort(
            key=lambda item: (
                -item[0],
                -_as_utc(parse_iso(item[2].created_at)).timestamp()
                if item[2].created_at
                else 0.0,
                item[2].memory_id,
            )
        )

        return tuple(
            MemoryHit(memory=memory, score=score, relevance=relevance, rank=rank)
            for rank, (score, relevance, memory) in enumerate(scored[:limit], start=1)
        )


def _in_scope(memory: Memory, scopes: tuple[ScopeFilter, ...]) -> bool:
    if memory.scope is MemoryScope.GLOBAL:
        return True
    return any(
        item.scope is memory.scope and item.scope_id == memory.scope_id
        for item in scopes
    )


def _expired(memory: Memory, now_dt: datetime) -> bool:
    if not memory.expires_at:
        return False
    return _as_utc(parse_iso(memory.expires_at)) <= now_dt


def _relevance(memory: Memory, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    content_tokens = _TOKEN_RE.findall(_normalize_for_tokenize(memory.content))
    summary_tokens = _TOKEN_RE.findall(_normalize_for_tokenize(memory.summary))
    haystack = set(content_tokens) | set(summary_tokens)
    overlap = len(query_tokens & haystack)
    return overlap / len(query_tokens)


def _recency(memory: Memory, now_dt: datetime) -> float:
    if not memory.updated_at:
        return 0.0
    try:
        updated = _as_utc(parse_iso(memory.updated_at))
    except ValueError:
        return 0.0
    age_days = max(0.0, (now_dt - updated).total_seconds() / 86400.0)
    return 1.0 / (1.0 + age_days)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)
