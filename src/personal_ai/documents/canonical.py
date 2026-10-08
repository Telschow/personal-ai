"""Canonicalization of source records into documents."""

from personal_ai.documents.models import Document, compute_document_id
from personal_ai.sources.models import SourceRecord


def document_from_source_record(record: SourceRecord) -> Document:
    """Build the canonical document for a source record.

    Identity comes exclusively from the stable identity helpers, so repeated
    ingestion of an unchanged source always yields the same document id.
    Source-specific facts are preserved verbatim in ``metadata``.
    """
    return Document(
        id=compute_document_id(
            record.source_type, record.source_key, record.content_hash
        ),
        source=record.source_key,
        source_type=record.source_type,
        content_hash=record.content_hash,
        created_at=record.created_at,
        modified_at=record.modified_at,
        metadata=dict(record.metadata),
    )
