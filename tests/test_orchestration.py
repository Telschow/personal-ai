"""Tests for generic source ingestion orchestration."""

import json
import sqlite3
from pathlib import Path

import pytest

from personal_ai.documents import (
    DocumentKind,
    StructuredExtraction,
    compute_content_hash,
)
from personal_ai.ingestion import DocumentIngestor
from personal_ai.orchestration import discover_source, ingest_source
from personal_ai.sources.keep import KeepSourceAdapter
from personal_ai.sources.models import SourceRecord
from personal_ai.sources.notebooklm import NotebookLMSourceAdapter
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

TEXT_HEAVY_TEXT = "Meeting note " * 30
SHORT_TEXT = "short note"


def make_record(payload: str, key: str, source_type: str = "fake") -> SourceRecord:
    materialized = payload.encode("utf-8")
    return SourceRecord(
        source_type=source_type,
        source_key=key,
        content_hash=compute_content_hash(materialized),
        created_at="2026-08-22T10:00:00+00:00",
        modified_at="2026-08-22T10:00:00+00:00",
        payload=materialized,
        metadata={"mime_type": "text/plain"},
    )


class FakeStructuredExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, extraction) -> StructuredExtraction:
        self.calls.append(extraction.document_id)
        return StructuredExtraction(document_id=extraction.document_id)


class ScriptedFailingExtractor:
    """Succeeds a fixed number of times, then always raises."""

    def __init__(self, successful_calls: int) -> None:
        self._remaining = successful_calls

    def extract(self, extraction) -> StructuredExtraction:
        if self._remaining <= 0:
            raise RuntimeError("model backend unavailable")
        self._remaining -= 1
        return StructuredExtraction(document_id=extraction.document_id)


class FakeSourceAdapter:
    """Serves a preset record list in its given order."""

    def __init__(self, records: list[SourceRecord], source_type: str = "fake") -> None:
        self._records = list(records)
        self._source_type = source_type
        self.discover_calls = 0

    @property
    def source_type(self) -> str:
        return self._source_type

    def discover(self) -> list[SourceRecord]:
        self.discover_calls += 1
        return list(self._records)


class IngestorSpy:
    """Delegates to a real ingestor while recording the processed order."""

    def __init__(self, inner: DocumentIngestor) -> None:
        self.inner = inner
        self.processed_keys: list[str] = []

    def ingest(self, record: SourceRecord):
        self.processed_keys.append(record.source_key)
        return self.inner.ingest(record)


class Harness:
    def __init__(self, extractor: object | None = None) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.extractor = (
            extractor if extractor is not None else FakeStructuredExtractor()
        )
        self.ingestor = DocumentIngestor(
            self.document_store,
            self.extraction_store,
            self.extractor,  # type: ignore[arg-type]
            self.chunk_store,
            self.embedding_store,
            chunk_size=40,
            chunk_overlap=5,
        )


def document_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


class TestGenericOrchestration:
    def test_all_records_ingested_exactly_once_in_discovery_order(self) -> None:
        harness = Harness()
        spy = IngestorSpy(harness.ingestor)
        adapter = FakeSourceAdapter(
            [
                make_record(TEXT_HEAVY_TEXT, "b_second.txt"),
                make_record(SHORT_TEXT, "a_first.txt"),
                make_record(SHORT_TEXT, "c_third.txt"),
            ]
        )

        summary = ingest_source(adapter, spy)

        assert spy.processed_keys == ["b_second.txt", "a_first.txt", "c_third.txt"]
        assert adapter.discover_calls == 1
        assert summary.source_type == "fake"
        assert summary.documents == 3
        assert summary.kind_counts == {
            DocumentKind.TEXT_HEAVY.value: 1,
            DocumentKind.MIXED.value: 2,
        }

    def test_chunk_count_aggregates_across_documents(self) -> None:
        harness = Harness()
        adapter = FakeSourceAdapter([make_record(TEXT_HEAVY_TEXT * 3, "big.txt")])

        summary = ingest_source(adapter, harness.ingestor)

        stored_chunks = sum(
            len(harness.chunk_store.list_for_document(row[0]))
            for row in harness.connection.execute("SELECT id FROM documents")
        )
        assert summary.chunk_count == stored_chunks > 1

    def test_reruns_are_idempotent_and_reuse_extractions(self) -> None:
        harness = Harness()
        adapter = FakeSourceAdapter([make_record(TEXT_HEAVY_TEXT, "note.txt")])

        first = ingest_source(adapter, harness.ingestor)
        calls_after_first = len(harness.extractor.calls)
        second = ingest_source(adapter, harness.ingestor)

        assert first == second
        assert len(harness.extractor.calls) == calls_after_first == 1
        assert document_count(harness.connection) == 1

    def test_empty_adapter_yields_zero_summary(self) -> None:
        harness = Harness()
        summary = ingest_source(FakeSourceAdapter([]), harness.ingestor)

        assert summary.documents == 0
        assert summary.kind_counts == {}
        assert summary.chunk_count == 0

    def test_failures_propagate_fail_fast_without_partial_progress_hidden(
        self,
    ) -> None:
        harness = Harness(ScriptedFailingExtractor(successful_calls=1))
        spy = IngestorSpy(harness.ingestor)
        adapter = FakeSourceAdapter(
            [
                make_record(TEXT_HEAVY_TEXT, "first.txt"),
                make_record(TEXT_HEAVY_TEXT, "second.txt"),
                make_record(SHORT_TEXT, "third.txt"),
            ]
        )

        with pytest.raises(RuntimeError, match="model backend unavailable"):
            ingest_source(adapter, spy)

        assert spy.processed_keys == ["first.txt", "second.txt"]
        assert document_count(harness.connection) == 2

    def test_discover_source_lists_keys_in_adapter_order(self) -> None:
        adapter = FakeSourceAdapter(
            [make_record(SHORT_TEXT, "z_last.txt"), make_record(SHORT_TEXT, "a.txt")]
        )

        summary = discover_source(adapter)

        assert summary.source_type == "fake"
        assert summary.source_keys == ("z_last.txt", "a.txt")


class TestRealAdaptersThroughTheSamePath:
    @pytest.fixture()
    def keep_export(self, tmp_path: Path) -> Path:
        root = tmp_path / "Keep"
        root.mkdir()

        def write_note(name: str, note: dict) -> None:
            (root / name).write_text(json.dumps(note), encoding="utf-8")

        base = {
            "color": "DEFAULT",
            "isTrashed": False,
            "isPinned": False,
            "isArchived": False,
            "createdTimestampUsec": 1699999999123456,
            "userEditedTimestampUsec": 1699999999123456,
        }
        write_note(
            "shopping.json", {**base, "title": "Shopping", "textContent": "Oat milk"}
        )
        write_note(
            "reading.json",
            {**base, "title": "", "textContent": "https://example.org/article"},
        )
        return root

    @pytest.fixture()
    def notebooklm_export(self, tmp_path: Path) -> Path:
        root = tmp_path / "NotebookLM"
        article = (
            b"<h3>Local Models</h3><p>"
            + b"Running models locally is private. " * 8
            + b"</p>"
        )
        sources = root / "Local AI/Sources"
        sources.mkdir(parents=True)
        (sources / "local_models.html").write_bytes(article)
        (sources / "local_models metadata.json").write_text(
            '{"title": "Local Models Guide", "metadata": '
            '{"originalSourceContentType": "SOURCE_CONTENT_TYPE_URL"}}',
            encoding="utf-8",
        )
        chats = root / "Local AI/Chat History"
        chats.mkdir(parents=True)
        (chats / "Chat Session - abc.html").write_bytes(b"<p>chat log</p>")
        return root

    def test_keep_adapter_flows_through_generic_orchestration(
        self, keep_export: Path
    ) -> None:
        harness = Harness()
        summary = ingest_source(KeepSourceAdapter(keep_export), harness.ingestor)

        assert summary.source_type == "google_keep"
        assert summary.documents == 2
        assert summary.kind_counts == {"mixed": 2}
        assert summary.chunk_count == 0
        stored_metadata = [
            row[0]
            for row in harness.connection.execute("SELECT metadata FROM documents")
        ]
        assert any('"Shopping"' in metadata for metadata in stored_metadata)

    def test_notebooklm_adapter_flows_through_generic_orchestration(
        self, notebooklm_export: Path
    ) -> None:
        harness = Harness()
        summary = ingest_source(
            NotebookLMSourceAdapter(notebooklm_export), harness.ingestor
        )

        assert summary.source_type == "notebooklm"
        assert summary.documents == 1
        assert summary.kind_counts == {DocumentKind.TEXT_HEAVY.value: 1}
        assert summary.chunk_count >= 1
        assert harness.extractor.calls != []


class TestArchitectureGuards:
    def test_orchestration_module_has_no_infrastructure_or_source_knowledge(
        self,
    ) -> None:
        from personal_ai import orchestration

        module_source = Path(orchestration.__file__).read_text(encoding="utf-8")
        lowered = module_source.lower()
        for forbidden in (
            "ollama",
            "httpx",
            "embed",
            "keep",
            "notebooklm",
            "filesystemadapter",
        ):
            assert forbidden not in lowered, forbidden
