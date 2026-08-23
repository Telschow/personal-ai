"""Document ingestion domain models."""

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.classifier import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentCharacteristics,
    DocumentClassification,
    DocumentKind,
    classify,
    classify_document,
    measure_text,
)
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
    "TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS",
    "Document",
    "DocumentCharacteristics",
    "DocumentChunk",
    "DocumentClassification",
    "DocumentKind",
    "TextExtractionError",
    "TextExtractionResult",
    "classify",
    "classify_document",
    "compute_content_hash",
    "compute_document_id",
    "document_from_source_record",
    "extract_text",
    "measure_text",
]
