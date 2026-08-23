"""Tests for batched embedding backfill over many documents."""

from collections.abc import Iterable

import pytest

from personal_ai.documents import (
    Embedding,
    StructuredExtraction,
    compute_content_hash,
)
from personal_ai.embeddings import EmbeddingBackfiller, backfill_documents
from personal_ai.ingestion import DocumentIngestor
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

MULTI_CHUNK_TEXT = "Meeting note " * 30
SINGLE_TEXT = "short note"


def make_record(payload: str, key: str) -> SourceRecord:
    materialized = payload.encode("utf-8")
    return SourceRecord(
        source_type="file",
        source_key=key,
        content_hash=compute_content_hash(materialized),
        created_at="2026-08-22T10:00:00+00:00",
        modified_at="2026-08-22T10:00:00+00:00",
        payload=materialized,
        metadata={"mime_type": "text/plain"},
    )


class FakeStructuredExtractor:
    def extract(self, extraction) -> StructuredExtraction:
        return StructuredExtraction(document_id=extraction.document_id)


class FakeProvider:
    def __init__(self, model: str = "fake-embed-model") -> None:
        self.model = model
        self.calls: list[str] = []

    def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        return Embedding(model=self.model, vector=(0.5, -0.25))


class FlakyProvider(FakeProvider):
    """Raises once a configured number of calls have been served."""

    def __init__(self, successful_calls: int, model: str = "fake-embed-model") -> None:
        super().__init__(model=model)
        self._remaining = successful_calls

    def embed(self, text: str) -> Embedding:
        if self._remaining <= 0:
            raise RuntimeError("embedding backend unavailable")
        self._remaining -= 1
        return super().embed(text)


class Harness:
    def __init__(self) -> None:
        self.connection = connect_database(":memory:")
        stores = (
            DocumentStore(self.connection),
            ExtractionStore(self.connection),
            ChunkStore(self.connection),
            EmbeddingStore(self.connection),
        )
        self.document_store, self.extraction_store = stores[0], stores[1]
        self.chunk_store, self.embedding_store = stores[2], stores[3]
        self.ingestor = DocumentIngestor(
            *stores[:2],
            FakeStructuredExtractor(),
            *stores[2:],
            chunk_size=40,
            chunk_overlap=5,
        )

    def ingest(self, payload: str, key: str) -> str:
        result = self.ingestor.ingest(make_record(payload, key))
        return result.document_id

    def backfiller(self, provider: FakeProvider) -> EmbeddingBackfiller:
        return EmbeddingBackfiller(
            self.chunk_store,
            self.embedding_store,
            provider,  # type: ignore[arg-type]
        )

    def chunk_texts(self, document_ids: Iterable[str]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for document_id in document_ids:
            for chunk in self.chunk_store.list_for_document(document_id):
                pairs.append((document_id, chunk.text))
        return pairs

    def stored_models(self) -> set[str]:
        rows = self.connection.execute("SELECT DISTINCT model FROM chunk_embeddings")
        return {row[0] for row in rows}

    def embedding_rows(self) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM chunk_embeddings"
        ).fetchone()[0]


def total_chunks(harness: Harness) -> int:
    return harness.connection.execute(
        "SELECT COUNT(*) FROM document_chunks"
    ).fetchone()[0]


class TestBackfillDocuments:
    def test_documents_are_processed_in_sorted_id_order(self) -> None:
        harness = Harness()
        ids = [harness.ingest(MULTI_CHUNK_TEXT, f"note{i}.txt") for i in range(3)]
        provider = FakeProvider()

        summary = backfill_documents(harness.backfiller(provider), list(reversed(ids)))

        expected = [text for _, text in harness.chunk_texts(sorted(ids))]
        assert provider.calls == expected
        assert summary.documents == 3
        assert summary.embeddings_created == len(expected)

    def test_second_complete_run_performs_zero_calls_and_writes(self) -> None:
        harness = Harness()
        ids = [harness.ingest(MULTI_CHUNK_TEXT, "a.txt")]
        provider = FakeProvider()
        backfiller = harness.backfiller(provider)

        first = backfill_documents(backfiller, ids)
        rows_after_first = harness.embedding_rows()
        second = backfill_documents(backfiller, ids)

        assert first.embeddings_created == rows_after_first == total_chunks(harness)
        assert second.embeddings_created == 0
        assert harness.embedding_rows() == rows_after_first
        assert len(provider.calls) == total_chunks(harness)

    def test_chunkless_documents_need_no_provider_calls(self) -> None:
        harness = Harness()
        chunked = harness.ingest(MULTI_CHUNK_TEXT, "big.txt")
        empty = harness.ingest("", "empty.txt")
        mixed = harness.ingest(SINGLE_TEXT, "tiny.txt")
        provider = FakeProvider()

        summary = backfill_documents(
            harness.backfiller(provider), [chunked, empty, mixed]
        )

        assert summary.documents == 3
        assert summary.embeddings_created == total_chunks(harness)
        assert total_chunks(harness) == len(provider.calls) > 0

    def test_partial_failure_keeps_prefix_and_resume_completes(self) -> None:
        harness = Harness()
        ids = sorted([harness.ingest(MULTI_CHUNK_TEXT, f"n{i}.txt") for i in range(3)])
        chunk_total = sum(
            len(harness.chunk_store.list_for_document(doc)) for doc in ids
        )
        flaky = FlakyProvider(successful_calls=chunk_total // 2)

        with pytest.raises(RuntimeError, match="embedding backend unavailable"):
            backfill_documents(harness.backfiller(flaky), ids)

        partial_rows = harness.embedding_rows()
        assert 0 < partial_rows < chunk_total

        resumed = backfill_documents(harness.backfiller(FakeProvider()), ids)

        assert resumed.embeddings_created == chunk_total - partial_rows
        assert harness.embedding_rows() == chunk_total

    def test_model_change_regenerates_every_vector(self) -> None:
        harness = Harness()
        ids = [harness.ingest(MULTI_CHUNK_TEXT, "a.txt")]
        old_provider = FakeProvider(model="model-a")

        backfill_documents(harness.backfiller(old_provider), ids)
        new_provider = FakeProvider(model="model-b")
        summary = backfill_documents(harness.backfiller(new_provider), ids)

        assert summary.embeddings_created == total_chunks(harness)
        assert harness.stored_models() == {"model-b"}

    def test_matching_preexisting_embeddings_are_reused_within_batch(self) -> None:
        harness = Harness()
        doc_a = harness.ingest(MULTI_CHUNK_TEXT, "a.txt")
        doc_b = harness.ingest(MULTI_CHUNK_TEXT, "b.txt")
        provider = FakeProvider()
        first_chunks = harness.chunk_store.list_for_document(doc_a)
        for chunk in first_chunks[:1]:
            harness.embedding_store.add(
                Embedding(model=provider.model, vector=(0.1,)), chunk.id
            )

        summary = backfill_documents(harness.backfiller(provider), [doc_a, doc_b])

        assert summary.embeddings_created == total_chunks(harness) - 1
        assert len(provider.calls) == summary.embeddings_created
