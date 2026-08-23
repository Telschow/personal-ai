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


class FakeEmbeddingProvider:
    """Deterministic vector source recording calls."""

    def __init__(
        self,
        model: str = FAKE_EMBED_MODEL,
        vector: tuple[float, ...] = (0.5, -0.25),
    ) -> None:
        self.model = model
        self.vector = vector
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        return Embedding(model=self.model, vector=self.vector)


class FlakyEmbeddingProvider:
    """Embeds deterministically but raises on one chosen call."""

    def __init__(self, fail_on_call: int, model: str = FAKE_EMBED_MODEL) -> None:
        self.model = model
        self.fail_on_call = fail_on_call
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        if len(self.calls) == self.fail_on_call:
            raise RuntimeError("embedding backend unavailable")
        return Embedding(model=self.model, vector=(0.5,))


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


class EmbeddingStoreSpy:
    """Delegates to a real store while recording mutating calls."""

    def __init__(self, inner: EmbeddingStore) -> None:
        self._inner = inner
        self.add_calls: list[str] = []
        self.delete_calls: list[str] = []

    def get(self, chunk_id: str) -> Embedding | None:
        return self._inner.get(chunk_id)

    def add(self, embedding: Embedding, chunk_id: str) -> bool:
        self.add_calls.append(chunk_id)
        return self._inner.add(embedding, chunk_id)

    def delete(self, chunk_id: str) -> bool:
        self.delete_calls.append(chunk_id)
        return self._inner.delete(chunk_id)


class IngestionHarness(NamedTuple):
    ingestor: DocumentIngestor
    document_store: DocumentStore
    extraction_store: ExtractionStore
    chunk_store: ChunkStore
    embedding_store: EmbeddingStore
    extractor: FakeStructuredExtractor
    provider: FakeEmbeddingProvider


def make_ingestor(
    extractor: object | None = None,
    provider: object | None = None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IngestionHarness:
    connection = connect_database(":memory:")
    document_store = DocumentStore(connection)
    extraction_store = ExtractionStore(connection)
    chunk_store = ChunkStore(connection)
    embedding_store = EmbeddingStore(connection)
    fake_extractor = extractor if extractor is not None else FakeStructuredExtractor()
    fake_provider = provider if provider is not None else FakeEmbeddingProvider()
    ingestor = DocumentIngestor(
        document_store,
        extraction_store,
        fake_extractor,
        chunk_store,
        embedding_store,
        fake_provider,
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
        fake_provider,
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
    provider_calls_after_first = len(harness.provider.calls)
    second = harness.ingestor.ingest(record)

    assert second == first
    assert len(harness.extractor.calls) == 1
    assert len(harness.provider.calls) == provider_calls_after_first
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
    assert harness.provider.calls == []
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
    assert harness.provider.calls == []
    assert harness.embedding_store.get("anything") is None
    assert harness.extraction_store.get(result.document_id) is None


def test_image_heavy_documents_never_reach_the_embedding_provider(monkeypatch) -> None:
    """Image evidence cannot arise from text measurement yet; force it here."""

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

    result = harness.ingestor.ingest(make_record(TEXT_HEAVY_TEXT.encode()))

    assert result.kind is DocumentKind.IMAGE_HEAVY
    assert result.chunks == ()
    assert result.structured_extraction is None
    assert harness.extractor.calls == []
    assert harness.provider.calls == []


def test_text_extraction_failure_propagates_without_persistence() -> None:
    record = make_record(b"\xff\xfe invalid utf-8 payload")
    harness = make_ingestor()

    with pytest.raises(TextExtractionError):
        harness.ingestor.ingest(record)

    assert harness.document_store.list_documents() == []
    document_id = document_from_source_record(record).id
    assert harness.extraction_store.get(document_id) is None
    assert harness.provider.calls == []


def test_structured_failure_propagates_and_leaves_no_extraction_record() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(FailingStructuredExtractor())

    with pytest.raises(RuntimeError):
        harness.ingestor.ingest(record)

    document_id = document_from_source_record(record).id
    assert harness.document_store.get(document_id) is not None
    assert harness.extraction_store.get(document_id) is None
    assert harness.chunk_store.list_for_document(document_id) == ()
    assert harness.provider.calls == []


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


def test_reingestion_does_not_rewrite_identical_embeddings_or_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    connection = connect_database(":memory:")
    chunk_spy = ChunkStoreSpy(ChunkStore(connection))
    embedding_spy = EmbeddingStoreSpy(EmbeddingStore(connection))
    provider = FakeEmbeddingProvider()
    ingestor = DocumentIngestor(
        DocumentStore(connection),
        ExtractionStore(connection),
        FakeStructuredExtractor(),
        chunk_spy,
        embedding_spy,
        provider,
    )

    ingestor.ingest(record)
    assert provider.calls
    assert embedding_spy.add_calls

    embedding_spy.add_calls.clear()
    embedding_spy.delete_calls.clear()
    chunk_spy.add_many_calls.clear()
    chunk_spy.delete_calls.clear()
    provider_calls_before = len(provider.calls)
    second = ingestor.ingest(record)

    assert chunk_spy.add_many_calls == []
    assert chunk_spy.delete_calls == []
    assert embedding_spy.add_calls == []
    assert embedding_spy.delete_calls == []
    assert len(provider.calls) == provider_calls_before
    assert second.chunks


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


def test_missing_embedding_is_repaired_without_rerunning_extraction() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)

    first = harness.ingestor.ingest(record)
    assert len(first.chunks) > 1
    victim_text = first.chunks[0].text
    assert harness.embedding_store.delete(first.chunks[0].id) is True
    calls_before = len(harness.provider.calls)

    second = harness.ingestor.ingest(record)

    assert len(harness.extractor.calls) == 1
    assert len(harness.provider.calls) == calls_before + 1
    assert harness.provider.calls[-1] == victim_text
    restored = harness.embedding_store.get(second.chunks[0].id)
    assert restored == Embedding(model=FAKE_EMBED_MODEL, vector=(0.5, -0.25))


def test_changed_chunk_configuration_replaces_stale_chunks() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor()
    rechunked = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        FakeEmbeddingProvider(),
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


def test_chunks_remain_isolated_between_documents() -> None:
    original_record = make_record(TEXT_HEAVY_TEXT.encode())
    changed_record = make_record(b"Revised meeting note " * 30)
    harness = make_ingestor()

    first = harness.ingestor.ingest(original_record)
    second = harness.ingestor.ingest(changed_record)

    assert harness.chunk_store.list_for_document(first.document_id) == first.chunks
    assert harness.chunk_store.list_for_document(second.document_id) == second.chunks


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
    assert harness.provider.calls == []


def test_changed_chunk_configuration_creates_fresh_embeddings_for_new_ids() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)
    fresh_provider = FakeEmbeddingProvider()
    rechunked = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        fresh_provider,
        chunk_size=80,
        chunk_overlap=10,
    )

    _first = harness.ingestor.ingest(record)
    second = rechunked.ingest(record)

    assert fresh_provider.calls == [chunk.text for chunk in second.chunks]
    for chunk in second.chunks:
        stored = harness.embedding_store.get(chunk.id)
        assert stored == Embedding(model=FAKE_EMBED_MODEL, vector=(0.5, -0.25))


def test_stale_embeddings_for_removed_chunk_ids_are_cleaned_up() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)
    rechunked = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        FakeStructuredExtractor(),
        harness.chunk_store,
        harness.embedding_store,
        FakeEmbeddingProvider(),
        chunk_size=80,
        chunk_overlap=10,
    )

    first = harness.ingestor.ingest(record)
    old_ids = {chunk.id for chunk in first.chunks}
    second = rechunked.ingest(record)
    new_ids = {chunk.id for chunk in second.chunks}
    removed = old_ids - new_ids

    assert removed
    for stale_id in removed:
        assert harness.embedding_store.get(stale_id) is None
    for chunk in second.chunks:
        assert harness.embedding_store.get(chunk.id) is not None


def test_model_change_causes_reembedding_instead_of_reuse() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(provider=FakeEmbeddingProvider(model="old-model"))
    replacement = FakeEmbeddingProvider(model="new-model", vector=(0.9, 0.8))
    reembedded = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        harness.extractor,
        harness.chunk_store,
        harness.embedding_store,
        replacement,
    )

    first = harness.ingestor.ingest(record)
    second = reembedded.ingest(record)

    assert second.chunks == first.chunks
    assert len(replacement.calls) == len(second.chunks)
    for chunk in second.chunks:
        stored = harness.embedding_store.get(chunk.id)
        assert stored == Embedding(model="new-model", vector=(0.9, 0.8))
    assert len(harness.extractor.calls) == 1


def test_embedding_failure_mid_document_keeps_repairable_partial_state() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = make_ingestor(chunk_size=40, chunk_overlap=5)
    expected = chunk_document(extract_text(record), chunk_size=40, overlap=5)
    assert len(expected) >= 3

    flaky = FlakyEmbeddingProvider(fail_on_call=2)
    failing = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        harness.extractor,
        harness.chunk_store,
        harness.embedding_store,
        flaky,
        chunk_size=40,
        chunk_overlap=5,
    )

    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        failing.ingest(record)

    assert flaky.calls == [chunk.text for chunk in expected[:2]]
    assert harness.embedding_store.get(expected[0].id) == Embedding(
        model=FAKE_EMBED_MODEL, vector=(0.5,)
    )
    for chunk in expected[1:]:
        assert harness.embedding_store.get(chunk.id) is None
    assert len(harness.extractor.calls) == 1

    healthy = DocumentIngestor(
        harness.document_store,
        harness.extraction_store,
        harness.extractor,
        harness.chunk_store,
        harness.embedding_store,
        FakeEmbeddingProvider(),
        chunk_size=40,
        chunk_overlap=5,
    )
    result = healthy.ingest(record)

    assert result.chunks == expected
    assert len(harness.extractor.calls) == 1
    for chunk in expected:
        stored = harness.embedding_store.get(chunk.id)
        assert stored is not None and stored.model == FAKE_EMBED_MODEL
