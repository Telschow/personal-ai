"""Typed models describing items yielded by source adapters."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A single raw item ingested from a data source.

    A record carries stable identity material and provenance, not extracted
    knowledge. ``source_key`` must be stable within ``source_type`` across
    re-imports (a relative POSIX path for files, a message-id for mail, ...).
    ``payload`` holds the exact bytes that were hashed whenever they were
    materialized during ingestion; analyzers must rely on the payload rather
    than re-reading a possibly changed origin.
    """

    source_type: str
    source_key: str
    content_hash: str
    created_at: str
    modified_at: str
    payload: bytes | None = None
    metadata: dict[str, object] = field(default_factory=dict)
