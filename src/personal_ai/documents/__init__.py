"""Document ingestion domain models."""

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_document,
    compute_chunk_id,
)
from personal_ai.documents.classifier import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentCharacteristics,
    DocumentClassification,
    DocumentKind,
    classify,
    classify_document,
    measure_text,
)
from personal_ai.documents.embedding import (
    Embedding,
    EmbeddingProvider,
    MalformedEmbeddingError,
    parse_embedding,
)
from personal_ai.documents.extractor import (
    PageExtraction,
    PDFExtractionError,
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
from personal_ai.documents.structured import (
    MalformedStructuredOutputError,
    StructuredExtraction,
    StructuredExtractor,
    parse_structured_extraction,
)

__all__ = [
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_CHUNK_SIZE",
    "TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS",
    "Document",
    "DocumentCharacteristics",
    "DocumentChunk",
    "DocumentClassification",
    "DocumentKind",
    "Embedding",
    "EmbeddingProvider",
    "MalformedEmbeddingError",
    "MalformedStructuredOutputError",
    "PDFExtractionError",
    "PageExtraction",
    "StructuredExtraction",
    "StructuredExtractor",
    "TextExtractionError",
    "TextExtractionResult",
    "chunk_document",
    "classify",
    "classify_document",
    "compute_chunk_id",
    "compute_content_hash",
    "compute_document_id",
    "document_from_source_record",
    "extract_text",
    "measure_text",
    "parse_embedding",
    "parse_structured_extraction",
]
