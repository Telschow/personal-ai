"""Integration tests for the ingestion pipeline, using fakes and SQLite."""

from typing import NamedTuple

import pytest

from personal_ai.documents import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DocumentChunk,
    DocumentClassification,
    DocumentKind,
    Embedding,
    StructuredExtraction,
    TextExtractionError,
    TextExtractionResult,
    chunk_document,
    compute_content_hash,
    document_from_source_record,
    extract_text,
    measure_text,
)
from personal_ai.embeddings import EmbeddingBackfiller
from personal_ai.ingestion import DocumentIngestor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

TEXT_HEAVY_TEXT = "Meeting note " * 30
FAKE_EMBED_MODEL = "fake-embed-model"


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


class _Unspecified:
    pass


_UNSPECIFIED_EXTRACTOR = _Unspecified()


class FakeEmbeddingProvider:
    """Deterministic vector source recording calls."""

    def __init__(self, model: str = FAKE_EMBED_MODEL) -> None:
        self.model = model
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        return Embedding(model=self.model, vector=(0.5, -0.25))


class ChunkStoreSpy:
    """Delegates to a real store while recording mutating calls."""

    def __init__(
        self, inner: ChunkStore, events: list[tuple[str, object]] | None = None
    ) -> None:
        self._inner = inner
        self._events = events if events is not None else []
        self.add_many_calls: list[int] = []
        self.delete_calls: list[str] = []

    def list_for_document(self, document_id: str) -> tuple[DocumentChunk, ...]:
        return self._inner.list_for_document(document_id)

    def add_many(self, chunks: tuple[DocumentChunk, ...]) -> int:
        self.add_many_calls.append(len(chunks))
        self._events.append(("chunks_add", len(chunks)))
        return self._inner.add_many(chunks)

    def delete_for_document(self, document_id: str) -> int:
        self.delete_calls.append(document_id)
        self._events.append(("chunks_delete", document_id))
        return self._inner.delete_for_document(document_id)


class EmbeddingStoreSpy:
    """Delegates to a real store while recording mutating calls."""

    def __init__(
        self, inner: EmbeddingStore, events: list[tuple[str, object]] | None = None
    ) -> None:
        self._inner = inner
        self._events = events if events is not None else []
        self.add_calls: list[str] = []
        self.delete_calls: list[str] = []

    def get(self, chunk_id: str) -> Embedding | None:
        return self._inner.get(chunk_id)

    def add(self, embedding: Embedding, chunk_id: str) -> bool:
        self.add_calls.append(chunk_id)
        self._events.append(("emb_add", chunk_id))
        return self._inner.add(embedding, chunk_id)

    def delete(self, chunk_id: str) -> bool:
        self.delete_calls.append(chunk_id)
        self._events.append(("emb_delete", chunk_id))
        return self._inner.delete(chunk_id)


class IngestionHarness(NamedTuple):
    ingestor: DocumentIngestor
    document_store: DocumentStore
    extraction_store: ExtractionStore
    chunk_store: ChunkStore
    embedding_store: EmbeddingStore
    extractor: FakeStructuredExtractor


def make_ingestor(
    extractor: object | None = _UNSPECIFIED_EXTRACTOR,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IngestionHarness:
    connection = connect_database(":memory:")
    document_store = DocumentStore(connection)
    extraction_store = ExtractionStore(connection)
    chunk_store = ChunkStore(connection)
    embedding_store = EmbeddingStore(connection)
    fake_extractor = (
        FakeStructuredExtractor() if extractor is _UNSPECIFIED_EXTRACTOR else extractor
    )
    ingestor = DocumentIngestor(
        document_store,
        extraction_store,
        fake_extractor,
        chunk_store,
        embedding_store,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return IngestionHarness(
        ingestor,
        document_store,
        extraction_store,
        chunk_store,
        embedding_store,
        fake_extractor,
    )


def test_text_heavy_record_flows_through_full_pipeline() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert result.kind is DocumentKind.TEXT_HEAVY
    assert result.document_id == document_from_source_record(record).id
    assert result.structured_extraction is not None
    assert result.structured_extraction.summary == "summary 1"

    stored_document = harness.document_store.get(result.document_id)
    assert stored_document == document_from_source_record(record)

    stored_extraction = harness.extraction_store.get(result.document_id)
    assert stored_extraction == result.structured_extraction


def test_structured_extractor_receives_existing_extraction_result() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert len(harness.extractor.calls) == 1
    received = harness.extractor.calls[0]
    assert isinstance(received, TextExtractionResult)
    assert received.text == TEXT_HEAVY_TEXT
    assert received.document_id == result.document_id
    assert received.source_key == "notes/ideas.txt"


def test_reingesting_unchanged_content_is_idempotent() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    first = harness.ingestor.ingest(record)
    second = harness.ingestor.ingest(record)

    assert second == first
    assert len(harness.extractor.calls) == 1
    assert len(harness.document_store.list_documents()) == 1
    stored = harness.extraction_store.get(first.document_id)
    assert stored == first.structured_extraction


def test_changed_content_creates_new_identity_and_keeps_old_extraction() -> None:
    original_record = make_record(TEXT_HEAVY_TEXT.encode())
    changed_record = make_record(b"Revised meeting note " * 30)
    harness = make_ingestor()

    first = harness.ingestor.ingest(original_record)
    second = harness.ingestor.ingest(changed_record)

    assert second.document_id != first.document_id
    assert second.kind is DocumentKind.TEXT_HEAVY
    assert len(harness.document_store.list_documents()) == 2

    old_extraction = harness.extraction_store.get(first.document_id)
    new_extraction = harness.extraction_store.get(second.document_id)
    assert old_extraction == first.structured_extraction
    assert new_extraction == second.structured_extraction


def test_empty_document_skips_structured_extraction() -> None:
    record = make_record(b"")
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert result.kind is DocumentKind.EMPTY
    assert result.structured_extraction is None
    assert harness.extractor.calls == []
    assert harness.embedding_store.get("anything") is None
    assert harness.document_store.get(result.document_id) is not None
    assert harness.extraction_store.get(result.document_id) is None


def test_mixed_document_skips_structured_extraction() -> None:
    record = make_record(b"hi")
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert result.kind is DocumentKind.MIXED
    assert result.structured_extraction is None
    assert harness.extractor.calls == []
    assert harness.embedding_store.get("anything") is None
    assert harness.extraction_store.get(result.document_id) is None


def test_mixed_document_with_usable_text_is_chunked_without_extraction(
    monkeypatch,
) -> None:
    """A MIXED document with usable text reaches the chunker, never the model.

    Image evidence (future vision analysis / PDF image counts) paired with
    at least the text threshold is MIXED by the classifier. Its extracted
    text is still chunked and searchable while the vision path is being
    built, but structured extraction stays TEXT_HEAVY-only.
    """

    def mixed_classification(extraction):
        return DocumentClassification(
            document_id=extraction.document_id,
            kind=DocumentKind.MIXED,
            characteristics=measure_text(extraction.text),
        )

    monkeypatch.setattr("personal_ai.ingestion.classify_document", mixed_classification)
    harness = make_ingestor()

    result = harness.ingestor.ingest(make_record(TEXT_HEAVY_TEXT.encode()))

    assert result.kind is DocumentKind.MIXED
    assert result.structured_extraction is None
    assert harness.extractor.calls == []
    assert harness.extraction_store.get(result.document_id) is None

    expected = chunk_document(extract_text(make_record(TEXT_HEAVY_TEXT.encode())))
    assert result.chunks == expected
    assert harness.chunk_store.list_for_document(result.document_id) == expected


def test_reingesting_unchanged_mixed_document_does_not_duplicate_chunks(
    monkeypatch,
) -> None:
    """MIXED chunking is idempotent exactly like the TEXT_HEAVY path."""

    def mixed_classification(extraction):
        return DocumentClassification(
            document_id=extraction.document_id,
            kind=DocumentKind.MIXED,
            characteristics=measure_text(extraction.text),
        )

    monkeypatch.setattr("personal_ai.ingestion.classify_document", mixed_classification)
    harness = make_ingestor()

    first = harness.ingestor.ingest(make_record(TEXT_HEAVY_TEXT.encode()))
    second = harness.ingestor.ingest(make_record(TEXT_HEAVY_TEXT.encode()))

    assert second == first
    assert len(harness.extractor.calls) == 0
    assert harness.chunk_store.list_for_document(first.document_id) == first.chunks


def test_image_heavy_documents_never_touch_the_embedding_store(monkeypatch) -> None:
    """Image evidence cannot arise from text measurement yet; force it here.

    The fixture keeps the IMAGE_HEAVY state classifier-consistent: short
    extracted text below the chunking threshold, so the ingestor stores the
    document unchunked and never touches the embedding store.
    """

    def image_heavy_classification(extraction):
        return DocumentClassification(
            document_id=extraction.document_id,
            kind=DocumentKind.IMAGE_HEAVY,
            characteristics=measure_text(extraction.text),
        )

    monkeypatch.setattr(
        "personal_ai.ingestion.classify_document", image_heavy_classification
    )
    harness = make_ingestor()

    result = harness.ingestor.ingest(make_record(b"Vision board scan, handwriting"))

    assert result.kind is DocumentKind.IMAGE_HEAVY
    assert result.chunks == ()
    assert result.structured_extraction is None
    assert harness.extractor.calls == []
    assert harness.document_store.get(result.document_id) is not None


def test_text_extraction_failure_propagates_without_persistence() -> None:
    record = make_record(b"\xff\xfe invalid utf-8 payload")
    harness = make_ingestor()

    with pytest.raises(TextExtractionError):
        harness.ingestor.ingest(record)

    assert harness.document_store.list_documents() == []
    document_id = document_from_source_record(record).id
    assert harness.extraction_store.get(document_id) is None


def test_structured_failure_propagates_and_leaves_no_extraction_record() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(FailingStructuredExtractor())

    with pytest.raises(RuntimeError):
        harness.ingestor.ingest(record)

    document_id = document_from_source_record(record).id
    assert harness.document_store.get(document_id) is not None
    assert harness.extraction_store.get(document_id) is None
    assert harness.chunk_store.list_for_document(document_id) == ()


def test_identity_and_metadata_survive_the_pipeline() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode(), source_key="docs/goals.md")
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    stored = harness.document_store.get(result.document_id)
    assert stored is not None
    assert stored.source == "docs/goals.md"
    assert stored.source_type == "file"
    assert stored.content_hash == compute_content_hash(TEXT_HEAVY_TEXT.encode())
    assert stored.metadata["mime_type"] == "text/markdown"


def test_text_heavy_document_persists_deterministic_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    expected = chunk_document(extract_text(record))
    assert result.chunks == expected
    assert harness.chunk_store.list_for_document(result.document_id) == expected


def test_reingesting_unchanged_content_does_not_duplicate_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    first = harness.ingestor.ingest(record)
    second = harness.ingestor.ingest(record)

    stored = harness.chunk_store.list_for_document(first.document_id)
    assert second.chunks == first.chunks
    assert stored == first.chunks


def test_ingestion_succeeds_without_any_embedding_provider() -> None:
    """Bulk durability must not depend on model availability.

    The ingestor has no embedding provider dependency at all; the full
    durable path (document, extraction, chunks) completes while the
    embedding store records no operations whatsoever.
    """
    record = make_record(TEXT_HEAVY_TEXT.encode())
    connection = connect_database(":memory:")
    embedding_spy = EmbeddingStoreSpy(EmbeddingStore(connection))
    extractor = FakeStructuredExtractor()
    ingestor = DocumentIngestor(
        DocumentStore(connection),
        ExtractionStore(connection),
        extractor,
        ChunkStoreSpy(ChunkStore(connection)),
        embedding_spy,
    )

    result = ingestor.ingest(record)

    assert result.kind is DocumentKind.TEXT_HEAVY
    assert result.structured_extraction is not None
    assert result.chunks
    assert len(extractor.calls) == 1
    assert embedding_spy.add_calls == []
    assert embedding_spy.delete_calls == []


def test_missing_chunks_are_repaired_without_rerunning_the_model() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()

    first = harness.ingestor.ingest(record)
    harness.chunk_store.delete_for_document(first.document_id)

    second = harness.ingestor.ingest(record)

    assert len(harness.extractor.calls) == 1
    restored = harness.chunk_store.list_for_document(first.document_id)
    assert restored == first.chunks
    assert second.chunks == first.chunks


def test_reingestion_does_not_rewrite_identical_embeddings_or_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    connection = connect_database(":memory:")
    chunk_spy = ChunkStoreSpy(ChunkStore(connection))
    embedding_spy = EmbeddingStoreSpy(EmbeddingStore(connection))
    ingestor = DocumentIngestor(
        DocumentStore(connection),
        ExtractionStore(connection),
        FakeStructuredExtractor(),
        chunk_spy,
        embedding_spy,
    )

    first = ingestor.ingest(record)
    assert first.chunks

    chunk_spy.add_many_calls.clear()
    chunk_spy.delete_calls.clear()
    second = ingestor.ingest(record)

    assert chunk_spy.add_many_calls == []
    assert chunk_spy.delete_calls == []
    assert embedding_spy.add_calls == []
    assert embedding_spy.delete_calls == []
    assert second.chunks == first.chunks


def test_rechunking_prunes_stale_embeddings_immediately() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)

    first = harness.ingestor.ingest(record)
    backfiller = EmbeddingBackfiller(
        harness.chunk_store, harness.embedding_store, FakeEmbeddingProvider()
    )
    backfiller.ensure_document(first.document_id)
    old_ids = {chunk.id for chunk in first.chunks}

    rechunked = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        chunk_size=80,
        chunk_overlap=10,
    ).ingest(record)
    new_ids = {chunk.id for chunk in rechunked.chunks}
    removed = old_ids - new_ids

    assert removed
    # Pruning happened during ingestion itself, with no further backfill.
    for stale_id in removed:
        assert harness.embedding_store.get(stale_id) is None
    for chunk in rechunked.chunks:
        stored = harness.embedding_store.get(chunk.id)
        if chunk.id in old_ids:
            assert stored is not None and stored.model == FAKE_EMBED_MODEL
        else:
            assert stored is None


def test_stale_embedding_deletion_precedes_chunk_replacement() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    connection = connect_database(":memory:")
    events: list[tuple[str, object]] = []
    chunk_spy = ChunkStoreSpy(ChunkStore(connection), events)
    embedding_spy = EmbeddingStoreSpy(EmbeddingStore(connection), events)
    stores = (DocumentStore(connection), ExtractionStore(connection))

    DocumentIngestor(
        *stores,
        FakeStructuredExtractor(),
        chunk_spy,
        embedding_spy,
        chunk_size=40,
        chunk_overlap=5,
    ).ingest(record)
    events.clear()

    DocumentIngestor(
        *stores,
        FakeStructuredExtractor(),
        chunk_spy,
        embedding_spy,
        chunk_size=80,
        chunk_overlap=10,
    ).ingest(record)

    kinds = [kind for kind, _payload in events]
    assert kinds[0] == "emb_delete"
    assert "emb_delete" in kinds
    first_chunk_op = min(
        index for index, kind in enumerate(kinds) if kind.startswith("chunks_")
    )
    last_emb_delete = max(
        index for index, kind in enumerate(kinds) if kind == "emb_delete"
    )
    assert last_emb_delete < first_chunk_op


def test_interrupted_stale_deletion_state_is_reconciled_by_retry() -> None:
    """A crash between pruning and replacement leaves a recoverable state.

    Simulated interruption: stale embeddings are already deleted while the
    old chunk rows are still in place. A plain retry of the same operation
    must complete the transition without errors or duplicates.
    """
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)
    first = harness.ingestor.ingest(record)
    EmbeddingBackfiller(
        harness.chunk_store, harness.embedding_store, FakeEmbeddingProvider()
    ).ensure_document(first.document_id)
    old_ids = {chunk.id for chunk in first.chunks}

    expected_new = chunk_document(extract_text(record), chunk_size=80, overlap=10)
    # Delete embeddings for ids that the new configuration removes; old
    # chunk rows are intentionally left in place, as an interruption would.
    for stale_id in old_ids - {chunk.id for chunk in expected_new}:
        assert harness.embedding_store.delete(stale_id) is True

    second = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        chunk_size=80,
        chunk_overlap=10,
    ).ingest(record)

    assert second.chunks == expected_new
    assert harness.chunk_store.list_for_document(second.document_id) == expected_new
    final_ids = {chunk.id for chunk in expected_new}
    for stale_id in old_ids - final_ids:
        assert harness.embedding_store.get(stale_id) is None


def test_missing_embeddings_never_cause_model_or_extraction_reruns() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)
    result = harness.ingestor.ingest(record)
    assert len(harness.extractor.calls) == 1

    backfiller = EmbeddingBackfiller(
        harness.chunk_store, harness.embedding_store, FakeEmbeddingProvider()
    )
    assert backfiller.ensure_document(result.document_id) == len(result.chunks)
    for chunk in result.chunks:
        assert harness.embedding_store.delete(chunk.id) is True

    again = harness.ingestor.ingest(record)
    provider = FakeEmbeddingProvider()
    repaired = EmbeddingBackfiller(
        harness.chunk_store, harness.embedding_store, provider
    ).ensure_document(result.document_id)

    assert again.chunks == result.chunks
    assert len(harness.extractor.calls) == 1
    assert repaired == len(result.chunks)
    assert provider.calls == [chunk.text for chunk in result.chunks]


def test_empty_document_produces_no_chunks() -> None:
    record = make_record(b"")
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert result.kind is DocumentKind.EMPTY
    assert result.chunks == ()
    assert harness.chunk_store.list_for_document(result.document_id) == ()


def test_mixed_document_produces_no_text_chunks() -> None:
    record = make_record(b"hi")
    harness = make_ingestor()

    result = harness.ingestor.ingest(record)

    assert result.kind is DocumentKind.MIXED
    assert result.chunks == ()
    assert harness.chunk_store.list_for_document(result.document_id) == ()


def test_structured_failure_persists_no_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(FailingStructuredExtractor())

    with pytest.raises(RuntimeError):
        harness.ingestor.ingest(record)

    document_id = document_from_source_record(record).id
    assert harness.chunk_store.list_for_document(document_id) == ()


def test_chunks_remain_isolated_between_documents() -> None:
    original_record = make_record(TEXT_HEAVY_TEXT.encode())
    changed_record = make_record(b"Revised meeting note " * 30)
    harness = make_ingestor()

    first = harness.ingestor.ingest(original_record)
    second = harness.ingestor.ingest(changed_record)

    assert harness.chunk_store.list_for_document(first.document_id) == first.chunks
    assert harness.chunk_store.list_for_document(second.document_id) == second.chunks


def test_changed_chunk_configuration_replaces_stale_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()
    rechunked = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        chunk_size=40,
        chunk_overlap=5,
    )

    first = harness.ingestor.ingest(record)
    second = rechunked.ingest(record)

    expected = chunk_document(extract_text(record), chunk_size=40, overlap=5)
    stored = harness.chunk_store.list_for_document(first.document_id)
    assert len(expected) > 1
    assert stored == expected
    assert second.chunks == expected
    assert {c.id for c in first.chunks}.isdisjoint({c.id for c in expected})


def test_repeated_identical_rechunking_performs_zero_writes() -> None:
    """The transition to a new configuration is a fixed point once done."""
    connection = connect_database(":memory:")
    events: list[tuple[str, object]] = []
    chunk_spy = ChunkStoreSpy(ChunkStore(connection), events)
    embedding_spy = EmbeddingStoreSpy(EmbeddingStore(connection), events)
    stores = (DocumentStore(connection), ExtractionStore(connection))
    rechunked = DocumentIngestor(
        *stores,
        FakeStructuredExtractor(),
        chunk_spy,
        embedding_spy,
        chunk_size=80,
        chunk_overlap=10,
    )

    first = rechunked.ingest(make_record(TEXT_HEAVY_TEXT.encode()))
    assert events
    events.clear()
    chunk_spy.add_many_calls.clear()
    chunk_spy.delete_calls.clear()

    second = rechunked.ingest(make_record(TEXT_HEAVY_TEXT.encode()))

    assert second == first
    assert events == []
    assert chunk_spy.add_many_calls == []
    assert chunk_spy.delete_calls == []
    assert embedding_spy.add_calls == []
    assert embedding_spy.delete_calls == []
