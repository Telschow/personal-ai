"""Generic orchestration driving source adapters through ingestion.

This layer contains no knowledge of individual sources: any adapter
satisfying :class:`~personal_ai.sources.base.SourceAdapter` flows through
the identical code path. Records are processed exactly once, in the
deterministic order produced by ``discover()``, and failures are fail-fast
— the first failing record propagates its exception unchanged, leaving
already-ingested documents durable and a plain retry to reconcile the rest
through canonical document identity.
"""

from dataclasses import dataclass, field

from personal_ai.ingestion import DocumentIngestor
from personal_ai.sources.base import SourceAdapter


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """Deterministic listing of what an adapter currently offers."""

    source_type: str
    source_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceIngestionSummary:
    """Outcome of running one adapter fully through ingestion."""

    source_type: str
    documents: int
    kind_counts: dict[str, int] = field(default_factory=dict)
    chunk_count: int = 0


def discover_source(adapter: SourceAdapter) -> DiscoverySummary:
    """Run discovery without touching extraction, storage, or providers."""
    records = adapter.discover()
    return DiscoverySummary(
        source_type=adapter.source_type,
        source_keys=tuple(record.source_key for record in records),
    )


def ingest_source(
    adapter: SourceAdapter, ingestor: DocumentIngestor
) -> SourceIngestionSummary:
    """Ingest every discovered record of one adapter exactly once."""
    kind_counts: dict[str, int] = {}
    chunk_count = 0
    documents = 0

    for record in adapter.discover():
        result = ingestor.ingest(record)
        documents += 1
        kind = result.kind
        kind_counts[kind.value] = kind_counts.get(kind.value, 0) + 1
        chunk_count += len(result.chunks)

    return SourceIngestionSummary(
        source_type=adapter.source_type,
        documents=documents,
        kind_counts=kind_counts,
        chunk_count=chunk_count,
    )
