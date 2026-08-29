"""Orchestration of source records into durable documents and extractions."""

from dataclasses import dataclass

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_document,
)
from personal_ai.documents.classifier import (
    TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS,
    DocumentKind,
    classify_document,
    measure_text,
)
from personal_ai.documents.extractor import (
    PageExtraction,
    TextExtractionResult,
    extract_text,
)
from personal_ai.documents.models import DocumentChunk
from personal_ai.documents.structured import StructuredExtraction, StructuredExtractor
from personal_ai.documents.vision import (
    VisionExtractionError,
    VisionExtractor,
    render_page,
)
from personal_ai.sources.models import SourceRecord
from personal_ai.storage.chunks import ChunkStore
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.embeddings import EmbeddingStore
from personal_ai.storage.extractions import ExtractionStore
from personal_ai.storage.vision import VisionStore


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of ingesting one source record.

    ``structured_extraction`` is ``None`` exactly when classification
    routed the document away from structured text extraction. ``chunks``
    mirrors the chunk set ensured in storage: empty for documents whose
    usable extracted text fell below the chunking threshold.

    Ingestion is durable on its own and never requires an embedding
    provider. Embeddings are derived data populated separately by
    :class:`~personal_ai.embeddings.EmbeddingBackfiller`.
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
    never duplicates or leaves stale chunks behind. When a replacement
    removes chunk ids, their embeddings are deleted before the chunks
    themselves are replaced, so no interruption can strand vectors whose
    chunks no longer exist. Embedding generation is not part of ingestion;
    see :class:`~personal_ai.embeddings.EmbeddingBackfiller`.

    Every document whose usable extracted text reaches
    :data:`~personal_ai.documents.classifier.TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS`
    is chunked regardless of kind, so a mixed document is chunked from its
    extracted text alone. Documents whose extracted text falls below the
    threshold (image-heavy scans, near-empty documents) are stored
    unchunked. Only ``TEXT_HEAVY`` documents ever reach the structured
    extractor; other kinds never trigger a model call. When
    ``structured_extractor`` is ``None``, no document triggers structured
    extraction at all — text-heavy sources are still chunked and searchable
    (used for sources whose extraction is fully local and deterministic,
    such as email).

    When a vision extractor and vision store are both configured, every
    ``IMAGE_HEAVY`` document is additionally routed through page-level
    vision extraction: each page's rendered image produces vision text that
    is appended to that page's verbatim extracted text, and the measured
    augmented text decides chunking, so an otherwise blank scanned page can
    become searchable. Vision output is cached per page keyed by model and
    prompt version; a complete cache avoids all rendering and model calls.
    Rendering failures skip only the affected page, while provider errors
    propagate. Classification always measures the original extracted text,
    so vision never changes a document's kind.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        extraction_store: ExtractionStore,
        structured_extractor: StructuredExtractor | None,
        chunk_store: ChunkStore,
        embedding_store: EmbeddingStore,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        vision_extractor: VisionExtractor | None = None,
        vision_store: VisionStore | None = None,
    ) -> None:
        self._document_store = document_store
        self._extraction_store = extraction_store
        self._structured_extractor = structured_extractor
        self._chunk_store = chunk_store
        self._embedding_store = embedding_store
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._vision_extractor = vision_extractor
        self._vision_store = vision_store

    def ingest(self, record: SourceRecord) -> IngestionResult:
        """Run one source record through the full ingestion path."""
        document = document_from_source_record(record)
        extracted = extract_text(record)
        classification = classify_document(extracted)

        self._document_store.add(document)

        structured_extraction: StructuredExtraction | None = None
        chunks: tuple[DocumentChunk, ...] = ()
        if (
            classification.kind is DocumentKind.TEXT_HEAVY
            and self._structured_extractor is not None
        ):
            existing = self._extraction_store.get(document.id)
            if existing is None:
                existing = self._structured_extractor.extract(extracted)
                self._extraction_store.save(existing)
            structured_extraction = existing

        effective = self._augment_with_vision(
            classification.kind, document.id, extracted, record
        )
        total_characters = measure_text(effective.text).non_whitespace_character_count
        if total_characters >= TEXT_HEAVY_MIN_NON_WHITESPACE_CHARACTERS:
            chunks = self._ensure_chunks(document.id, effective)

        return IngestionResult(
            document_id=document.id,
            kind=classification.kind,
            structured_extraction=structured_extraction,
            chunks=chunks,
        )

    def _augment_with_vision(
        self,
        kind: DocumentKind,
        document_id: str,
        extracted: TextExtractionResult,
        record: SourceRecord,
    ) -> TextExtractionResult:
        """Attach page-level vision text to an image-heavy extracted PDF.

        Only ``IMAGE_HEAVY`` documents are routed to the vision extractor,
        and only when one is configured; every other kind passes through
        untouched. Augmentation is additive: each page keeps its original
        extracted text verbatim, vision text is appended, and pages are
        never merged. Cached pages (matching model and prompt version) make
        no vision call at all.
        """
        if (
            kind is not DocumentKind.IMAGE_HEAVY
            or self._vision_extractor is None
            or self._vision_store is None
            or extracted.pages is None
            or record.payload is None
        ):
            return extracted

        import pymupdf

        try:
            source_doc = pymupdf.open(stream=record.payload, filetype="pdf")
        except Exception as exc:
            msg = "Could not open PDF payload for vision extraction"
            raise VisionExtractionError(msg) from exc

        pages: list[PageExtraction] = []
        try:
            for page in extracted.pages:
                try:
                    augmented = self._vision_page(document_id, page, source_doc)
                except VisionExtractionError:
                    continue
                pages.append(augmented if augmented is not None else page)
        finally:
            source_doc.close()

        return TextExtractionResult(
            document_id=extracted.document_id,
            source_type=extracted.source_type,
            source_key=extracted.source_key,
            content_hash=extracted.content_hash,
            text="\n\n".join(p.text for p in pages),
            metadata=extracted.metadata,
            pages=tuple(pages),
        )

    def _vision_page(
        self,
        document_id: str,
        page: PageExtraction,
        source_doc: object,
    ) -> PageExtraction | None:
        """Return one page with vision text appended, or None when none.

        A matched cache row short-circuits before any rendering or model
        call. Rendering failures raise :class:`VisionExtractionError` (the
        page is skipped by the caller) while provider errors propagate
        unchanged. Empty model output is not cached, so it is retried on
        the next ingest.
        """
        cached = self._vision_store.get(document_id, page.page_number)
        if (
            cached is not None
            and cached.vision_model == self._vision_extractor.model
            and cached.prompt_version == self._vision_extractor.prompt_version
        ):
            return PageExtraction(
                page_number=page.page_number,
                text=self._augmented_text(page, cached.text),
            )

        try:
            pdf_page = source_doc.load_page(page.page_number - 1)
            rendered = render_page(pdf_page)
        except Exception as exc:
            msg = f"Could not render page {page.page_number} for vision extraction"
            raise VisionExtractionError(msg) from exc

        vision_text = self._vision_extractor.extract(rendered)
        if vision_text is None or not vision_text.strip():
            return None
        self._vision_store.save(
            document_id,
            page.page_number,
            vision_text,
            self._vision_extractor.model,
            self._vision_extractor.prompt_version,
        )
        return PageExtraction(
            page_number=page.page_number,
            text=self._augmented_text(page, vision_text),
        )

    @staticmethod
    def _augmented_text(page: PageExtraction, vision_text: str) -> str:
        """Append vision text after the page's verbatim extracted text."""
        if page.text.strip():
            return page.text.rstrip() + "\n\n" + vision_text
        return vision_text

    def _ensure_chunks(
        self, document_id: str, extracted: TextExtractionResult
    ) -> tuple[DocumentChunk, ...]:
        """Align one document's stored chunks with deterministic re-chunking.

        Chunking is a cheap pure function, so it runs unconditionally and
        the result is compared against stored rows: equal sets leave storage
        untouched, while drifted sets are replaced wholesale via
        ``delete_for_document`` followed by ``add_many``. Embeddings of
        removed chunk ids are deleted *before* the chunks themselves are
        replaced: every step is idempotent and re-derivable from the stored
        chunk set, so an interruption at any point leaves a state that a
        plain retry fully reconciles — stale vectors can never outlive
        their chunks indefinitely.
        """
        chunks = chunk_document(
            extracted, chunk_size=self._chunk_size, overlap=self._chunk_overlap
        )
        stored = self._chunk_store.list_for_document(document_id)
        if stored == chunks:
            return chunks

        stale_ids = {chunk.id for chunk in stored} - {chunk.id for chunk in chunks}
        for stale_id in sorted(stale_ids):
            self._embedding_store.delete(stale_id)
        if stored:
            self._chunk_store.delete_for_document(document_id)
        self._chunk_store.add_many(chunks)
        return chunks
