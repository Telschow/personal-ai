"""Document ingestion domain models."""

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.extractor import (
    TextExtractionError,
    TextExtractionResult,
    extract_text,
)
from personal_ai.documents.models import (
    Document,
    DocumentChunk,
    compute_content_hash,
    compute_document_id,
)

__all__ = [
    "Document",
    "DocumentChunk",
    "TextExtractionError",
    "TextExtractionResult",
    "compute_content_hash",
    "compute_document_id",
    "document_from_source_record",
    "extract_text",
]
