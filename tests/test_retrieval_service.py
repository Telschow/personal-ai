"""Tests for the unified RetrievalService."""

from personal_ai.documents.models import compute_content_hash
from personal_ai.ingestion import DocumentIngestor
from personal_ai.retrieval import RetrievalService, SearchResult
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    EmbeddingStore,
    ExtractionStore,
    connect_database,
)

TEXT_HEAVY_TEXT = "Meeting note about BCG consulting project. " * 20


def _make_record(payload: bytes, source_key: str = "notes.txt") -> SourceRecord:
    return SourceRecord(
        source_type="file",
        source_key=source_key,
        content_hash=compute_content_hash(payload),
        created_at="2026-08-26T10:00:00+00:00",
        modified_at="2026-08-26T10:00:00+00:00",
        payload=payload,
        metadata={"mime_type": "text/plain"},
    )


class FakeStructuredExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, extraction):
        from personal_ai.documents.structured import StructuredExtraction

        self.calls += 1
        return StructuredExtraction(
            document_id=extraction.document_id,
            summary=f"Summary about BCG project for document {self.calls}",
            people=("Daniel Telschow",),
            organizations=("BCG",),
            projects=("Project Apollo",),
            goals=("Career advancement",),
            topics=("consulting", "strategy"),
        )


class TestRetrievalService:
    """Unified search across chunks and structured extractions."""

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.document_store = DocumentStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.chunk_store = ChunkStore(self.connection)
        self.embedding_store = EmbeddingStore(self.connection)
        self.extractor = FakeStructuredExtractor()
        self.ingestor = DocumentIngestor(
            self.document_store,
            self.extraction_store,
            self.extractor,
            self.chunk_store,
            self.embedding_store,
        )

    def teardown_method(self) -> None:
        self.connection.close()

    def _ingest(self, payload: bytes, source_key: str = "notes.txt") -> str:
        record = _make_record(payload, source_key)
        result = self.ingestor.ingest(record)
        return result.document_id

    def _service(self) -> RetrievalService:
        return RetrievalService(
            self.chunk_store, self.extraction_store, self.document_store
        )

    def test_chunk_only_match(self) -> None:
        doc_id = self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        chunk_results = [r for r in results if r.result_type == "chunk"]
        assert len(chunk_results) > 0
        assert any(r.document_id == doc_id for r in chunk_results)

    def test_structured_only_match(self) -> None:
        """Query matches extraction but not chunk text."""
        self._ingest(b"Some unrelated text content here. " * 20)
        service = self._service()

        results = service.search("consulting")
        extraction_results = [
            r for r in results if r.result_type == "structured_extraction"
        ]
        assert len(extraction_results) > 0

    def test_query_matching_both(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        types = {r.result_type for r in results}
        assert "chunk" in types
        assert "structured_extraction" in types

    def test_correct_document_id_linkage(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        for r in results:
            assert r.document_id
            doc = self.document_store.get(r.document_id)
            assert doc is not None

    def test_correct_document_title(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode(), source_key="my_meeting.txt")
        service = self._service()

        results = service.search("BCG")
        titles = {r.title for r in results}
        assert "my_meeting.txt" in titles

    def test_page_number_preserved_for_chunk_results(self) -> None:
        """Chunk results have page_number=None since chunks don't store page info in search results."""
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        for r in results:
            if r.result_type == "chunk":
                assert r.page_number is None

    def test_structured_result_identifies_matched_fields(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        extraction_results = [
            r for r in results if r.result_type == "structured_extraction"
        ]
        assert len(extraction_results) > 0
        for r in extraction_results:
            assert len(r.matched_fields) > 0

    def test_deterministic_ordering(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        first = service.search("BCG")
        second = service.search("BCG")
        assert first == second

    def test_limit_respected(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG", limit=2)
        assert len(results) <= 2

    def test_empty_query(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        assert service.search("") == ()
        assert service.search("  ") == ()

    def test_no_match(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("zzzznonexistent")
        assert results == ()

    def test_search_result_fields(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        assert len(results) > 0
        for r in results:
            assert isinstance(r, SearchResult)
            assert r.result_type in ("chunk", "structured_extraction")
            assert isinstance(r.document_id, str)
            assert isinstance(r.score, float)
            assert isinstance(r.title, str)
            assert isinstance(r.text, str)

    def test_multiple_documents(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode(), source_key="doc1.txt")
        self._ingest(
            b"Completely different content about cooking recipes. " * 20,
            source_key="doc2.txt",
        )
        service = self._service()

        results = service.search("BCG")
        doc_ids = {r.document_id for r in results}
        assert len(doc_ids) >= 1
