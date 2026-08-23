"""Orchestration of source records into durable documents and extractions."""

from dataclasses import dataclass

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_document,
)
from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import TextExtractionResult, extract_text
from personal_ai.documents.models import DocumentChunk
from personal_ai.documents.structured import StructuredExtraction, StructuredExtractor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.extractions import ExtractionStore


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of ingesting one source record.

    ``structured_extraction`` is ``None`` exactly when classification
    routed the document away from structured text extraction. ``chunks``
    mirrors the chunk set ensured in storage: empty for documents that
    classification did not route through text chunking.
    """

    document_id: str
    kind: DocumentKind
    structured_extraction: StructuredExtraction | None
    chunks: tuple[DocumentChunk, ...]


class DocumentIngestor:
    """Connects extraction, classification, and storage for one record.

    The canonical document is persisted before structured extraction runs,
    so a stored extraction always references a stored document and a failed
    model call never leaves a fake extraction behind. An extraction already
    persisted for a document id is reused verbatim, so re-ingesting an
    unchanged source never reruns the model or yields a different result;
    a missing extraction (for example after a previous failure) is retried.
    Provider errors propagate unchanged; nothing here logs or swallows
    document content.

    Text-heavy documents are additionally chunked deterministically and the
    resulting chunk set is aligned with storage: an identical stored set is
    left untouched, while missing or drifted sets (changed chunk
    configuration, partial deletion) are replaced in full, so re-ingestion
    never duplicates or leaves stale chunks behind.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        extraction_store: ExtractionStore,
        structured_extractor: StructuredExtractor,
        chunk_store: ChunkStore,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._document_store = document_store
        self._extraction_store = extraction_store
        self._structured_extractor = structured_extractor
        self._chunk_store = chunk_store
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def ingest(self, record: SourceRecord) -> IngestionResult:
        """Run one source record through the full ingestion path."""
        document = document_from_source_record(record)
        extracted = extract_text(record)
        classification = classify_document(extracted)

        self._document_store.add(document)

        structured_extraction: StructuredExtraction | None = None
        chunks: tuple[DocumentChunk, ...] = ()
        if classification.kind is DocumentKind.TEXT_HEAVY:
            existing = self._extraction_store.get(document.id)
            if existing is None:
                existing = self._structured_extractor.extract(extracted)
                self._extraction_store.save(existing)
            structured_extraction = existing

            chunks = self._ensure_chunks(document.id, extracted)

        return IngestionResult(
            document_id=document.id,
            kind=classification.kind,
            structured_extraction=structured_extraction,
            chunks=chunks,
        )

    def _ensure_chunks(
        self, document_id: str, extracted: TextExtractionResult
    ) -> tuple[DocumentChunk, ...]:
        """Align one document's stored chunks with deterministic re-chunking.

        Chunking is a cheap pure function, so it runs unconditionally and
        the result is compared against stored rows: equal sets leave storage
        untouched, while drifted sets are replaced wholesale via
        ``delete_for_document`` followed by ``add_many``.
        """
        chunks = chunk_document(
            extracted, chunk_size=self._chunk_size, overlap=self._chunk_overlap
        )
        stored = self._chunk_store.list_for_document(document_id)
        if stored == chunks:
            return chunks
        if stored:
            self._chunk_store.delete_for_document(document_id)
        self._chunk_store.add_many(chunks)
        return chunks
