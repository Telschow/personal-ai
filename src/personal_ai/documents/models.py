"""Typed models for documents and their searchable chunks."""

import hashlib
from dataclasses import dataclass, field


def compute_content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest fingerprinting document content."""
    return hashlib.sha256(data).hexdigest()


def compute_document_id(source_type: str, source: str, content_hash: str) -> str:
    """Derive a stable document identity from origin and content.

    Re-ingesting unchanged content from the same source yields the same id,
    which keeps ingestion idempotent. Components are joined with a NUL
    separator so field boundaries can never merge ambiguously.
    """
    identity_material = f"{source_type}\x00{source}\x00{content_hash}"
    return hashlib.sha256(identity_material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Document:
    """A single ingested personal document.

    ``id`` must come from :func:`compute_document_id` so that repeated
    ingestion of the same source cannot create duplicates.
    """

    id: str
    source: str
    source_type: str
    content_hash: str
    created_at: str
    modified_at: str
    path: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """A searchable portion of a document."""

    id: str
    document_id: str
    text: str
    page_number: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
