"""Orchestration of source records into durable documents and extractions."""

from dataclasses import dataclass

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_document,
)
from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.embedding import EmbeddingProvider
from personal_ai.documents.extractor import TextExtractionResult, extract_text
from personal_ai.documents.models import DocumentChunk
from personal_ai.documents.structured import StructuredExtraction, StructuredExtractor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.embeddings import EmbeddingStore
from personal_ai.storage.extractions import ExtractionStore


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of ingesting one source record.

    ``structured_extraction`` is ``None`` exactly when classification
    routed the document away from structured text extraction. ``chunks``
    mirrors the chunk set ensured in storage: empty for documents that
    classification did not route through text chunking.

    Text-heavy ingestion additionally ensures one embedding per persisted
    chunk in the embedding store; that store remains authoritative and its
    contents are intentionally not mirrored here, keeping the result lean
    and free of provider internals.
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
    never duplicates or leaves stale chunks behind. Embeddings for chunks
    whose stored vector is missing or was produced by another model are
    regenerated; everything else is left untouched.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        extraction_store: ExtractionStore,
        structured_extractor: StructuredExtractor,
        chunk_store: ChunkStore,
        embedding_store: EmbeddingStore,
        embedding_provider: EmbeddingProvider,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._document_store = document_store
        self._extraction_store = extraction_store
        self._structured_extractor = structured_extractor
        self._chunk_store = chunk_store
        self._embedding_store = embedding_store
        self._embedding_provider = embedding_provider
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
            self._ensure_embeddings(chunks)

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
        ``delete_for_document`` followed by ``add_many``. Chunk ids removed
        by the replacement have their embeddings deleted as well, so stale
        vectors cannot outlive their chunks.
        """
        chunks = chunk_document(
            extracted, chunk_size=self._chunk_size, overlap=self._chunk_overlap
        )
        stored = self._chunk_store.list_for_document(document_id)
        if stored == chunks:
            return chunks

        stale_ids = {chunk.id for chunk in stored} - {chunk.id for chunk in chunks}
        if stored:
            self._chunk_store.delete_for_document(document_id)
        self._chunk_store.add_many(chunks)
        for stale_id in sorted(stale_ids):
            self._embedding_store.delete(stale_id)
        return chunks

    def _ensure_embeddings(self, chunks: tuple[DocumentChunk, ...]) -> None:
        """Ensure every chunk carries an embedding from the current model.

        A stored embedding is reused only when its recorded model matches
        the provider's current model identity; anything missing or produced
        by another model is re-embedded and overwritten under the same
        chunk id, so a future model change invalidates old vectors instead
        of silently treating them as equivalent. Chunks are processed in
        order and each embedding persists immediately, so a provider
        failure mid-document leaves a partial but consistent state: earlier
        chunks keep valid embeddings, the failing chunk propagates its
        error, and later chunks stay absent. A retry re-embeds exactly the
        remainder without rerunning structured extraction. Provider errors
        propagate unchanged; nothing here logs or swallows chunk text.
        """
        for chunk in chunks:
            stored = self._embedding_store.get(chunk.id)
            if stored is not None and stored.model == self._embedding_provider.model:
                continue
            embedding = self._embedding_provider.embed(chunk.text)
            self._embedding_store.add(embedding, chunk.id)
