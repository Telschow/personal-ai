"""Orchestration of source records into durable documents and extractions."""

from dataclasses import dataclass

from personal_ai.documents.canonical import document_from_source_record
from personal_ai.documents.classifier import DocumentKind, classify_document
from personal_ai.documents.extractor import extract_text
from personal_ai.documents.structured import StructuredExtraction, StructuredExtractor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage.documents import DocumentStore
from personal_ai.storage.extractions import ExtractionStore


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of ingesting one source record.

    ``structured_extraction`` is ``None`` exactly when classification
    routed the document away from structured text extraction.
    """

    document_id: str
    kind: DocumentKind
    structured_extraction: StructuredExtraction | None


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
    """

    def __init__(
        self,
        document_store: DocumentStore,
        extraction_store: ExtractionStore,
        structured_extractor: StructuredExtractor,
    ) -> None:
        self._document_store = document_store
        self._extraction_store = extraction_store
        self._structured_extractor = structured_extractor

    def ingest(self, record: SourceRecord) -> IngestionResult:
        """Run one source record through the full ingestion path."""
        document = document_from_source_record(record)
        extracted = extract_text(record)
        classification = classify_document(extracted)

        self._document_store.add(document)

        structured_extraction: StructuredExtraction | None = None
        if classification.kind is DocumentKind.TEXT_HEAVY:
            existing = self._extraction_store.get(document.id)
            if existing is None:
                existing = self._structured_extractor.extract(extracted)
                self._extraction_store.save(existing)
            structured_extraction = existing

        return IngestionResult(
            document_id=document.id,
            kind=classification.kind,
            structured_extraction=structured_extraction,
        )
