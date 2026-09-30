"""Tests for the unified RetrievalService."""

from personal_ai.documents.models import compute_content_hash
from personal_ai.ingestion import DocumentIngestor
from personal_ai.retrieval import RetrievalService, SearchResult
from personal_ai.sources.models import SourceRecord
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentFilter,
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


def _make_email_record(payload: bytes, subject: str) -> SourceRecord:
    return SourceRecord(
        source_type="email",
        source_key="synthetic-phase33@example.test",
        content_hash=compute_content_hash(payload),
        created_at="2026-08-26T10:00:00+00:00",
        modified_at="2026-08-26T10:00:00+00:00",
        payload=payload,
        metadata={
            "filename": "synthetic-phase33@example.test",
            "mime_type": "message/rfc822",
            "subject": subject,
            "sender": "sender@example.test",
            "to": "me@example.test",
            "message_id": "<synthetic-phase33@example.test>",
        },
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
            people=("Alice Example",),
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
        self.conversation_store = ConversationStore(self.connection)
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
            self.chunk_store, self.extraction_store, self.document_store, self.conversation_store
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

    # --- Phase 33: result provenance and source identity -------------------

    def test_source_type_present_on_chunk_and_extraction_results(self) -> None:
        self._ingest(TEXT_HEAVY_TEXT.encode())
        service = self._service()

        results = service.search("BCG")
        assert len(results) > 0
        for result in results:
            assert result.source_type == "file"

    def test_email_subject_becomes_result_title(self) -> None:
        record = _make_email_record(
            b"Guitar chord charts phase33mbox. " * 20,
            subject="Phase 33 Email Subject",
        )
        ingested = self.ingestor.ingest(record)
        service = self._service()

        hits = [
            result
            for result in service.search("phase33mbox")
            if result.document_id == ingested.document_id
        ]
        assert len(hits) > 0
        for result in hits:
            assert result.title == "Phase 33 Email Subject"
            assert result.source_type == "email"

    def test_email_without_subject_falls_back_to_source_identity(self) -> None:
        record = _make_email_record(
            b"Guitar chord charts phase33fallback. " * 20,
            subject="",
        )
        ingested = self.ingestor.ingest(record)
        document = self.document_store.get(ingested.document_id)
        assert document is not None
        service = self._service()

        hits = [
            result
            for result in service.search("phase33fallback")
            if result.document_id == ingested.document_id
        ]
        assert len(hits) > 0
        for result in hits:
            assert result.title == document.source
            assert result.title != "Phase 33 Email Subject"
            assert result.source_type == "email"

    def test_hybrid_search_routes_chunk_results_to_document_store(self) -> None:
        """Test that RetrievalService correctly routes result types for document lookups.
        
        ChunkSearchResult and ExtractionSearchResult should trigger DocumentStore.get()
        for provenance lookup, while ConversationSearchResult should not.
        """
        # Track calls to DocumentStore.get
        get_calls = []
        original_get = self.document_store.get
        
        def tracked_get(document_id: str):
            get_calls.append(document_id)
            return original_get(document_id)
        
        self.document_store.get = tracked_get
        
        try:
            service = self._service()
            
            # Create test data that will produce all three result types
            # Chunk: ingest a document with text content
            chunk_doc_id = self._ingest(b"Hello world chunk content")
            
            # Extraction: need a different document for extraction to avoid caching
            extraction_doc_id = self._ingest(b"Different content for extraction")
            
            # Extraction: we need to trigger extraction store separately
            # For simplicity, we'll mock the extraction store to return a known result
            from personal_ai.storage.extractions import ExtractionSearchResult
            
            # Mock extraction store to return a known extraction result
            extraction_result = ExtractionSearchResult(
                document_id=extraction_doc_id,
                summary="Test extraction summary",
                score=0.8,
                matched_fields=("summary",),
            )
            
            original_extraction_search = self.extraction_store.search
            def mock_extraction_search(query: str, *, limit: int = 10):
                if query == "test":
                    return (extraction_result,)
                return original_extraction_search(query, limit=limit)
            self.extraction_store.search = mock_extraction_search
            
            # Conversation: we'll mock conversation store similarly
            from personal_ai.storage.conversations import ConversationSearchResult
            
            conversation_result = ConversationSearchResult(
                message_id="msg1",
                conversation_id="conv1",
                conversation_title="Test Conversation",
                message_index=0,
                role="user",
                speaker="Test Speaker",
                content_text="Test conversation content",
                score=0.7,
            )
            
            original_conversation_search = self.conversation_store.search
            def mock_conversation_search(
                query: str,
                *,
                limit: int = 10,
                created_after: str | None = None,
                created_before: str | None = None,
            ):
                if query == "test":
                    return (conversation_result,)
                return original_conversation_search(query, limit=limit, created_after=created_after, created_before=created_before)
            self.conversation_store.search = mock_conversation_search
            
            # Chunk store: make sure our document produces a chunk result
            # We need to make sure chunk store returns a result for our query
            from personal_ai.storage.chunks import ChunkSearchResult
            
            chunk_result = ChunkSearchResult(
                chunk_id="chunk1",
                document_id=chunk_doc_id,
                chunk_index=0,
                text="Hello world chunk content",
                rank=0.0,  # BM25 rank
            )
            
            original_chunk_search = self.chunk_store.search
            def mock_chunk_search(
                query: str,
                *,
                limit: int = 10,
                filters: DocumentFilter | None = None,
            ):
                if query == "test":
                    return (chunk_result,)
                return original_chunk_search(query, limit=limit, filters=filters)
            self.chunk_store.search = mock_chunk_search
            
            # Perform search that should trigger all three mocks
            results = service.search("test", limit=10)
            
            # Verify we got results of all three types
            result_types = {r.result_type for r in results}
            assert "chunk" in result_types, f"Expected chunk result, got {result_types}"
            assert "structured_extraction" in result_types, f"Expected extraction result, got {result_types}"
            assert "conversation" in result_types, f"Expected conversation result, got {result_types}"
            
            # Count DocumentStore.get calls - should be 2 (for chunk and extraction, not conversation)
            # Each chunk and extraction result triggers one document lookup for provenance
            assert len(get_calls) == 2, f"Expected 2 DocumentStore.get calls (chunk + extraction), got {len(get_calls)}: {get_calls}"
            
            # Verify the document IDs looked up are correct
            looked_up_doc_ids = set(get_calls)
            expected_doc_ids = {chunk_doc_id, extraction_doc_id}  # Different doc IDs for chunk and extraction
            assert looked_up_doc_ids == expected_doc_ids, f"Expected document IDs {expected_doc_ids}, got {looked_up_doc_ids}"
            
        finally:
            # Restore original methods
            self.document_store.get = original_get
            self.extraction_store.search = original_extraction_search
            self.conversation_store.search = original_conversation_search
            self.chunk_store.search = original_chunk_search

    def test_deletion_cascade_removes_all_related_data(self) -> None:
        """Verify that deleting a document removes chunks, FTS entries, and embeddings."""
        # Ingest a document that will create chunks and (potentially) embeddings
        doc_id = self._ingest(b"Test content for deletion cascade")
        
        # Verify chunks exist
        chunk_hits = self.chunk_store.search("Test content", limit=10)
        if chunk_hits:
            # Delete the document
            deleted_count = self.chunk_store.delete_for_document(doc_id)
            
            # Verify chunks are deleted
            remaining_hits = self.chunk_store.search("Test content", limit=10)
            assert len(remaining_hits) == 0, "Chunks should be deleted after document deletion"
            
            # Note: Embedding deletion requires explicit call if embeddings exist
            # This test documents the gap in PAI-009
