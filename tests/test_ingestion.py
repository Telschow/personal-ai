"""Integration tests for the ingestion pipeline, using fakes and SQLite."""

import pytest

from personal_ai.documents import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DocumentChunk,
    DocumentKind,
    StructuredExtraction,
    TextExtractionError,
    TextExtractionResult,
    chunk_document,
    compute_content_hash,
    document_from_source_record,
    extract_text,
)
from personal_ai.ingestion import DocumentIngestor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    ExtractionStore,
    connect_database,
)

TEXT_HEAVY_TEXT = "Meeting note " * 30


def make_record(payload: bytes, source_key: str = "notes/ideas.txt") -> SourceRecord:
    return SourceRecord(
        source_type="file",
        source_key=source_key,
        content_hash=compute_content_hash(payload),
        created_at="2026-08-22T10:00:00+00:00",
        modified_at="2026-08-22T10:00:00+00:00",
        payload=payload,
        metadata={"mime_type": "text/markdown"},
    )


class FakeStructuredExtractor:
    """Records calls; optionally replays scripted results."""

    def __init__(self) -> None:
        self.calls: list[TextExtractionResult] = []

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        self.calls.append(extraction)
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"summary {len(self.calls)}",
        )


class FailingStructuredExtractor:
    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        raise RuntimeError("model backend unavailable")


class ChunkStoreSpy:
    """Delegates to a real store while recording mutating calls."""

    def __init__(self, inner: ChunkStore) -> None:
        self._inner = inner
        self.add_many_calls: list[int] = []
        self.delete_calls: list[str] = []

    def list_for_document(self, document_id: str) -> tuple[DocumentChunk, ...]:
        return self._inner.list_for_document(document_id)

    def add_many(self, chunks: tuple[DocumentChunk, ...]) -> int:
        self.add_many_calls.append(len(chunks))
        return self._inner.add_many(chunks)

    def delete_for_document(self, document_id: str) -> int:
        self.delete_calls.append(document_id)
        return self._inner.delete_for_document(document_id)


def make_ingestor(
    extractor: object | None = None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[
    DocumentIngestor,
    DocumentStore,
    ExtractionStore,
    ChunkStore,
    FakeStructuredExtractor,
]:
    connection = connect_database(":memory:")
    document_store = DocumentStore(connection)
    extraction_store = ExtractionStore(connection)
    chunk_store = ChunkStore(connection)
    fake = extractor if extractor is not None else FakeStructuredExtractor()
    ingestor = DocumentIngestor(
        document_store,
        extraction_store,
        fake,  # type: ignore[arg-type]
        chunk_store,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return ingestor, document_store, extraction_store, chunk_store, fake  # type: ignore[return-value]


def test_text_heavy_record_flows_through_full_pipeline() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, document_store, extraction_store, _chunk_store, _fake = make_ingestor()

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.TEXT_HEAVY
    assert result.document_id == document_from_source_record(record).id
    assert result.structured_extraction is not None
    assert result.structured_extraction.summary == "summary 1"

    stored_document = document_store.get(result.document_id)
    assert stored_document == document_from_source_record(record)

    stored_extraction = extraction_store.get(result.document_id)
    assert stored_extraction == result.structured_extraction


def test_structured_extractor_receives_existing_extraction_result() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, _document_store, _extraction_store, _chunk_store, fake = make_ingestor()

    result = ingestor.ingest(record)

    assert len(fake.calls) == 1
    received = fake.calls[0]
    assert isinstance(received, TextExtractionResult)
    assert received.text == TEXT_HEAVY_TEXT
    assert received.document_id == result.document_id
    assert received.source_key == "notes/ideas.txt"


def test_reingesting_unchanged_content_is_idempotent() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, document_store, extraction_store, _chunk_store, fake = make_ingestor()

    first = ingestor.ingest(record)
    second = ingestor.ingest(record)

    assert second == first
    assert len(fake.calls) == 1
    assert len(document_store.list_documents()) == 1
    stored = extraction_store.get(first.document_id)
    assert stored == first.structured_extraction


def test_changed_content_creates_new_identity_and_keeps_old_extraction() -> None:
    original_record = make_record(TEXT_HEAVY_TEXT.encode())
    changed_record = make_record(b"Revised meeting note " * 30)
    ingestor, document_store, extraction_store, _chunk_store, _fake = make_ingestor()

    first = ingestor.ingest(original_record)
    second = ingestor.ingest(changed_record)

    assert second.document_id != first.document_id
    assert second.kind is DocumentKind.TEXT_HEAVY
    assert len(document_store.list_documents()) == 2

    old_extraction = extraction_store.get(first.document_id)
    new_extraction = extraction_store.get(second.document_id)
    assert old_extraction == first.structured_extraction
    assert new_extraction == second.structured_extraction


def test_empty_document_skips_structured_extraction() -> None:
    record = make_record(b"")
    ingestor, document_store, extraction_store, _chunk_store, fake = make_ingestor()

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.EMPTY
    assert result.structured_extraction is None
    assert fake.calls == []
    assert document_store.get(result.document_id) is not None
    assert extraction_store.get(result.document_id) is None


def test_mixed_document_skips_structured_extraction() -> None:
    record = make_record(b"hi")
    ingestor, _document_store, extraction_store, _chunk_store, fake = make_ingestor()

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.MIXED
    assert result.structured_extraction is None
    assert fake.calls == []
    assert extraction_store.get(result.document_id) is None


def test_text_extraction_failure_propagates_without_persistence() -> None:
    record = make_record(b"\xff\xfe invalid utf-8 payload")
    ingestor, document_store, extraction_store, _chunk_store, fake = make_ingestor()

    with pytest.raises(TextExtractionError):
        ingestor.ingest(record)

    assert document_store.list_documents() == []
    assert extraction_store.get(document_from_source_record(record).id) is None
    assert fake.calls == []


def test_structured_failure_propagates_and_leaves_no_extraction_record() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, document_store, extraction_store, _chunk_store, _fake = make_ingestor(
        FailingStructuredExtractor()
    )

    with pytest.raises(RuntimeError):
        ingestor.ingest(record)

    document_id = document_from_source_record(record).id
    assert document_store.get(document_id) is not None
    assert extraction_store.get(document_id) is None


def test_identity_and_metadata_survive_the_pipeline() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode(), source_key="docs/goals.md")
    ingestor, document_store, _extraction_store, _chunk_store, _fake = make_ingestor()

    result = ingestor.ingest(record)

    stored = document_store.get(result.document_id)
    assert stored is not None
    assert stored.source == "docs/goals.md"
    assert stored.source_type == "file"
    assert stored.content_hash == compute_content_hash(TEXT_HEAVY_TEXT.encode())
    assert stored.metadata["mime_type"] == "text/markdown"


def test_text_heavy_document_persists_deterministic_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor()

    result = ingestor.ingest(record)

    expected = chunk_document(extract_text(record))
    assert result.chunks == expected
    assert chunk_store.list_for_document(result.document_id) == expected


def test_reingesting_unchanged_content_does_not_duplicate_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor()

    first = ingestor.ingest(record)
    second = ingestor.ingest(record)

    stored = chunk_store.list_for_document(first.document_id)
    assert second.chunks == first.chunks
    assert stored == first.chunks


def test_reingestion_does_not_rewrite_identical_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    connection = connect_database(":memory:")
    spy = ChunkStoreSpy(ChunkStore(connection))
    ingestor = DocumentIngestor(
        DocumentStore(connection),
        ExtractionStore(connection),
        FakeStructuredExtractor(),
        spy,  # type: ignore[arg-type]
    )

    ingestor.ingest(record)
    assert spy.add_many_calls

    spy.add_many_calls.clear()
    spy.delete_calls.clear()
    second = ingestor.ingest(record)

    assert spy.add_many_calls == []
    assert spy.delete_calls == []
    assert second.chunks


def test_missing_chunks_are_repaired_without_rerunning_the_model() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, _document_store, _extraction_store, chunk_store, fake = make_ingestor()

    first = ingestor.ingest(record)
    chunk_store.delete_for_document(first.document_id)

    second = ingestor.ingest(record)

    assert len(fake.calls) == 1
    restored = chunk_store.list_for_document(first.document_id)
    assert restored == first.chunks
    assert second.chunks == first.chunks


def test_changed_chunk_configuration_replaces_stale_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, document_store, extraction_store, chunk_store, _fake = make_ingestor()
    rechunked = DocumentIngestor(
        document_store,
        extraction_store,
        FakeStructuredExtractor(),
        chunk_store,
        chunk_size=40,
        chunk_overlap=5,
    )

    first = ingestor.ingest(record)
    second = rechunked.ingest(record)

    expected = chunk_document(extract_text(record), chunk_size=40, overlap=5)
    stored = chunk_store.list_for_document(first.document_id)
    assert len(expected) > 1
    assert stored == expected
    assert second.chunks == expected
    assert {c.id for c in first.chunks}.isdisjoint({c.id for c in expected})


def test_chunks_remain_isolated_between_documents() -> None:
    original_record = make_record(TEXT_HEAVY_TEXT.encode())
    changed_record = make_record(b"Revised meeting note " * 30)
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor()

    first = ingestor.ingest(original_record)
    second = ingestor.ingest(changed_record)

    assert chunk_store.list_for_document(first.document_id) == first.chunks
    assert chunk_store.list_for_document(second.document_id) == second.chunks


def test_empty_document_produces_no_chunks() -> None:
    record = make_record(b"")
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor()

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.EMPTY
    assert result.chunks == ()
    assert chunk_store.list_for_document(result.document_id) == ()


def test_mixed_document_produces_no_text_chunks() -> None:
    record = make_record(b"hi")
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor()

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.MIXED
    assert result.chunks == ()
    assert chunk_store.list_for_document(result.document_id) == ()


def test_structured_failure_persists_no_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    ingestor, _document_store, _extraction_store, chunk_store, _fake = make_ingestor(
        FailingStructuredExtractor()
    )

    with pytest.raises(RuntimeError):
        ingestor.ingest(record)

    document_id = document_from_source_record(record).id
    assert chunk_store.list_for_document(document_id) == ()
