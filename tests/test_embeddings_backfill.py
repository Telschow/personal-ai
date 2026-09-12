"""Integration tests for the embedding backfill boundary."""

from pathlib import Path

import pytest

import personal_ai.embeddings as embeddings_module
from personal_ai.documents import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    Embedding,
    StructuredExtraction,
    TextExtractionResult,
    compute_content_hash,
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
    """Records calls and returns a deterministic extraction."""

    def __init__(self) -> None:
        self.calls: list[TextExtractionResult] = []

    def extract(self, extraction: TextExtractionResult) -> StructuredExtraction:
        self.calls.append(extraction)
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"summary {len(self.calls)}",
        )


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


class BackfillHarness:
    """Shared stores on one connection plus convenience constructors."""

    def __init__(self) -> None:
        connection = connect_database(":memory:")
        self.document_store = DocumentStore(connection)
        self.extraction_store = ExtractionStore(connection)
        self.chunk_store = ChunkStore(connection)
        self.embedding_store = EmbeddingStore(connection)

    def ingestor(
        self,
        extractor: object | None = None,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> DocumentIngestor:
        return DocumentIngestor(
            self.document_store,
            self.extraction_store,
            extractor if extractor is not None else FakeStructuredExtractor(),
            self.chunk_store,
            self.embedding_store,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def backfiller(
        self,
        provider: object | None = None,
        embedding_store: object | None = None,
    ) -> EmbeddingBackfiller:
        return EmbeddingBackfiller(
            self.chunk_store,
            embedding_store if embedding_store is not None else self.embedding_store,
            provider if provider is not None else FakeEmbeddingProvider(),
        )


def test_backfiller_embeds_persisted_chunks_under_exact_ids() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor(chunk_size=40, chunk_overlap=5).ingest(record)
    expected_chunks = result.chunks
    assert len(expected_chunks) > 1
    provider = FakeEmbeddingProvider()

    embedded = harness.backfiller(provider).ensure_document(result.document_id)

    assert embedded == len(expected_chunks)
    assert provider.calls == [chunk.text for chunk in expected_chunks]
    for chunk in expected_chunks:
        stored = harness.embedding_store.get(chunk.id)
        assert stored == Embedding(model=FAKE_EMBED_MODEL, vector=(0.5, -0.25))


def test_matching_model_reuse_skips_provider_calls_and_writes() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor().ingest(record)
    spy = EmbeddingStoreSpy(harness.embedding_store)
    provider = FakeEmbeddingProvider()
    backfiller = harness.backfiller(provider, spy)

    first = backfiller.ensure_document(result.document_id)
    adds_after_first = list(spy.add_calls)
    calls_after_first = list(provider.calls)
    second = backfiller.ensure_document(result.document_id)

    assert first > 0
    assert second == 0
    assert spy.add_calls == adds_after_first
    assert provider.calls == calls_after_first


def test_missing_embedding_is_repaired_and_other_rows_untouched() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor().ingest(record)
    backfiller = harness.backfiller()
    backfiller.ensure_document(result.document_id)
    victim = result.chunks[0]
    original_others = {
        chunk.id: harness.embedding_store.get(chunk.id) for chunk in result.chunks[1:]
    }
    assert harness.embedding_store.delete(victim.id) is True
    spy = EmbeddingStoreSpy(harness.embedding_store)

    repaired = harness.backfiller(embedding_store=spy).ensure_document(
        result.document_id
    )

    assert repaired == 1
    assert spy.add_calls == [victim.id]
    assert spy.delete_calls == []
    restored = harness.embedding_store.get(victim.id)
    assert restored == Embedding(model=FAKE_EMBED_MODEL, vector=(0.5, -0.25))
    for chunk_id, embedding in original_others.items():
        assert harness.embedding_store.get(chunk_id) == embedding


def test_model_change_causes_reembedding_instead_of_reuse() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor().ingest(record)
    first = harness.backfiller(FakeEmbeddingProvider(model="old-model"))
    first.ensure_document(result.document_id)

    replacement = FakeEmbeddingProvider(model="new-model", vector=(0.9, 0.8))
    reembedded = harness.backfiller(replacement).ensure_document(result.document_id)

    assert reembedded == len(result.chunks)
    assert len(replacement.calls) == len(result.chunks)
    for chunk in result.chunks:
        stored = harness.embedding_store.get(chunk.id)
        assert stored == Embedding(model="new-model", vector=(0.9, 0.8))


def test_partial_failure_keeps_durable_repairable_prefix() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor(chunk_size=40, chunk_overlap=5).ingest(record)
    chunks = result.chunks
    assert len(chunks) >= 3
    flaky = FlakyEmbeddingProvider(fail_on_call=2)

    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        harness.backfiller(flaky).ensure_document(result.document_id)

    assert flaky.calls == [chunk.text for chunk in chunks[:2]]
    assert harness.embedding_store.get(chunks[0].id) == Embedding(
        model=FAKE_EMBED_MODEL, vector=(0.5,)
    )
    for chunk in chunks[1:]:
        assert harness.embedding_store.get(chunk.id) is None


def test_retry_after_failure_reuses_completed_embeddings() -> None:
    record = make_record(TEXT_HEAVY_TEXT.encode())
    harness = BackfillHarness()
    result = harness.ingestor(chunk_size=40, chunk_overlap=5).ingest(record)
    chunks = result.chunks
    assert len(chunks) >= 3
    flaky = FlakyEmbeddingProvider(fail_on_call=2)
    with pytest.raises(RuntimeError):
        harness.backfiller(flaky).ensure_document(result.document_id)

    healthy = FakeEmbeddingProvider()
    resumed = harness.backfiller(healthy).ensure_document(result.document_id)

    assert healthy.calls == [chunk.text for chunk in chunks[1:]]
    assert resumed == len(chunks) - 1
    again = harness.backfiller(healthy).ensure_document(result.document_id)
    assert again == 0
    assert len(healthy.calls) == len(chunks) - 1


@pytest.mark.parametrize("document_id", ["missing-document", ""])
def test_unknown_or_chunkless_documents_embed_nothing(document_id: str) -> None:
    harness = BackfillHarness()
    provider = FakeEmbeddingProvider()

    embedded = harness.backfiller(provider).ensure_document(document_id)

    assert embedded == 0
    assert provider.calls == []


def test_empty_document_ingested_then_backfilled_yields_zero() -> None:
    harness = BackfillHarness()
    result = harness.ingestor().ingest(make_record(b""))
    provider = FakeEmbeddingProvider()

    assert result.kind.value == "empty"
    assert harness.backfiller(provider).ensure_document(result.document_id) == 0
    assert provider.calls == []


def test_backfill_module_has_no_infrastructure_imports() -> None:
    source = Path(embeddings_module.__file__).read_text(encoding="utf-8").lower()

    assert "ollama" not in source
    assert "httpx" not in source


def seed_corpus(harness: BackfillHarness, source_keys: tuple[str, ...]) -> int:
    """Ingest a few text-heavy records; returns the resulting chunk count."""
    for source_key in source_keys:
        harness.ingestor(chunk_size=40, chunk_overlap=5).ingest(
            make_record(TEXT_HEAVY_TEXT.encode(), source_key=source_key)
        )
    return harness.chunk_store.count()


def test_backfill_corpus_embeds_all_chunks_in_chunk_id_order() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt", "notes/two.txt"))
    assert total > 3
    expected_order = [chunk.text for chunk in harness.chunk_store.list_chunks()]
    provider = FakeEmbeddingProvider()

    summary = harness.backfiller(provider).backfill_corpus(batch_size=3)

    assert summary.total_chunks == total
    assert summary.chunks_scanned == total
    assert summary.embeddings_created == total
    assert provider.calls == expected_order
    assert harness.embedding_store.count(FAKE_EMBED_MODEL) == total
    for chunk in harness.chunk_store.list_chunks():
        assert harness.embedding_store.get(chunk.id) == Embedding(
            model=FAKE_EMBED_MODEL, vector=(0.5, -0.25)
        )


def test_backfill_corpus_is_idempotent() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt",))
    provider = FakeEmbeddingProvider()
    backfiller = harness.backfiller(provider)
    assert backfiller.backfill_corpus().embeddings_created == total
    calls_after_first = list(provider.calls)

    second = backfiller.backfill_corpus()

    assert second.embeddings_created == 0
    assert second.chunks_scanned == total
    assert provider.calls == calls_after_first


def test_backfill_corpus_resumes_after_partial_failure() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt", "notes/two.txt"))
    batch_size = 3
    fail_on_call = 5
    flaky = FlakyEmbeddingProvider(fail_on_call=fail_on_call)
    with pytest.raises(RuntimeError, match="embedding backend unavailable"):
        harness.backfiller(flaky).backfill_corpus(batch_size=batch_size)

    persisted = harness.embedding_store.count(FAKE_EMBED_MODEL)
    expected_persisted = ((fail_on_call - 1) // batch_size) * batch_size
    assert 0 < persisted == expected_persisted < total

    healthy = FakeEmbeddingProvider()
    resumed = harness.backfiller(healthy).backfill_corpus(batch_size=batch_size)

    assert resumed.embeddings_created == total - persisted
    assert resumed.chunks_scanned == total
    assert harness.embedding_store.count(FAKE_EMBED_MODEL) == total
    again = harness.backfiller(healthy).backfill_corpus(batch_size=batch_size)
    assert again.embeddings_created == 0


def test_backfill_corpus_no_resume_overwrites_current_model_vectors() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt",))
    provider = FakeEmbeddingProvider()
    backfiller = harness.backfiller(provider)
    assert backfiller.backfill_corpus().embeddings_created == total
    calls_after_first = list(provider.calls)

    overwritten = backfiller.backfill_corpus(resume=False)

    assert overwritten.embeddings_created == total
    assert len(provider.calls) == len(calls_after_first) + total
    assert harness.embedding_store.count(FAKE_EMBED_MODEL) == total


def test_backfill_corpus_model_change_reembeds_everything() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt",))
    assert (
        harness.backfiller(FakeEmbeddingProvider(model="old-model"))
        .backfill_corpus()
        .embeddings_created
        == total
    )

    replacement = FakeEmbeddingProvider(model="new-model", vector=(0.9, 0.8))
    summary = harness.backfiller(replacement).backfill_corpus()

    assert summary.embeddings_created == total
    assert harness.embedding_store.count("old-model") == 0
    assert harness.embedding_store.count("new-model") == total
    for chunk in harness.chunk_store.list_chunks():
        assert harness.embedding_store.get(chunk.id) == Embedding(
            model="new-model", vector=(0.9, 0.8)
        )


def test_backfill_corpus_progress_callback_reports_cumulative_counts() -> None:
    harness = BackfillHarness()
    total = seed_corpus(harness, ("notes/one.txt",))
    progress: list[tuple[int, int]] = []

    summary = harness.backfiller().backfill_corpus(
        batch_size=4, progress_callback=lambda s, c: progress.append((s, c))
    )

    assert summary.embeddings_created == total
    assert progress and progress[-1] == (total, total)
    scanned = [entry[0] for entry in progress]
    created = [entry[1] for entry in progress]
    assert scanned == sorted(scanned) and created == sorted(created)
    assert all(step % 4 == 0 for step in scanned[:-1]) or total < 4


def test_backfill_corpus_empty_corpus_is_a_noop() -> None:
    harness = BackfillHarness()
    provider = FakeEmbeddingProvider()

    summary = harness.backfiller(provider).backfill_corpus()

    assert summary.total_chunks == 0
    assert summary.chunks_scanned == 0
    assert summary.embeddings_created == 0
    assert provider.calls == []


@pytest.mark.parametrize("batch_size", [0, -3])
def test_backfill_corpus_rejects_nonpositive_batch_size(batch_size: int) -> None:
    harness = BackfillHarness()
    harness.ingestor().ingest(make_record(TEXT_HEAVY_TEXT.encode()))

    with pytest.raises(ValueError, match="batch_size"):
        harness.backfiller().backfill_corpus(batch_size=batch_size)


def test_backfill_corpus_empty_chunk_text_is_never_embedded() -> None:
    harness = BackfillHarness()
    harness.ingestor().ingest(make_record(b""))
    provider = FakeEmbeddingProvider()

    summary = harness.backfiller(provider).backfill_corpus()

    assert summary.total_chunks == 0
    assert provider.calls == []
