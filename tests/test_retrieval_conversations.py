"""Tests for retrieval integration with conversation messages."""

import json
from pathlib import Path

from personal_ai.conversation_ingestion import ingest_gemini_conversations
from personal_ai.documents.conversations import Conversation, ConversationMessage
from personal_ai.retrieval import RetrievalService, SearchResult
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentFilter,
    DocumentStore,
    ExtractionStore,
    connect_database,
)


def _write_conversation(
    directory: Path,
    filename: str,
    *,
    conv_id: str = "abc123",
    title: str = "Test Conversation",
    messages: list[dict[str, str]] | None = None,
) -> Path:
    if messages is None:
        messages = [
            {"role": "user", "content": "Tell me about BCG consulting"},
            {"role": "assistant", "content": "BCG is Boston Consulting Group."},
        ]
    data = {
        "id": conv_id,
        "title": title,
        "messages": messages,
        "messageCount": len(messages),
        "createdAt": "2026-01-01T00:00:00.000Z",
        "lastMessageAt": "2026-01-01T00:00:00.000Z",
    }
    path = directory / filename
    path.write_text(json.dumps(data))
    return path


class TestConversationRetrieval:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.chunk_store = ChunkStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.document_store = DocumentStore(self.connection)
        self.conversation_store = ConversationStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def _service(self) -> RetrievalService:
        return RetrievalService(
            self.chunk_store,
            self.extraction_store,
            self.document_store,
            self.conversation_store,
        )

    def test_conversation_searchable(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
            title="BCG Career Discussion",
            messages=[
                {"role": "user", "content": "What do you know about BCG?"},
                {
                    "role": "assistant",
                    "content": "BCG stands for Boston Consulting Group.",
                },
            ],
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        assert len(conv_results) > 0

    def test_role_preserved(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
            messages=[
                {"role": "user", "content": "Hello BCG"},
                {"role": "assistant", "content": "Hi there"},
            ],
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        for r in conv_results:
            assert r.role in ("user", "assistant")
            assert r.speaker in ("User", "Gemini")

    def test_conversation_title_preserved(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
            title="Career Goals at BCG",
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        assert any(r.title == "Career Goals at BCG" for r in conv_results)

    def test_message_index_preserved(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
            messages=[
                {"role": "user", "content": "First question about BCG"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Second question about BCG"},
            ],
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        assert any(r.message_index == 0 for r in conv_results)
        assert any(r.message_index == 2 for r in conv_results)

    def test_conversation_id_preserved(self, tmp_path: Path) -> None:
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
            conv_id="test123",
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        for r in conv_results:
            assert r.conversation_id is not None

    def test_document_search_still_works(self, tmp_path: Path) -> None:
        # Ensure document search isn't broken by adding conversation search
        _write_conversation(
            tmp_path,
            "20260101_BCG_aaa.json",
        )
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        # Searching should return at least conversation results
        results = service.search("BCG")
        assert len(results) > 0

    def test_empty_search_returns_nothing(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        assert service.search("") == ()
        assert service.search("  ") == ()

    def test_no_match(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("zzzznonexistent")
        assert results == ()

    def test_deterministic_ordering(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        first = service.search("BCG")
        second = service.search("BCG")
        assert first == second

    def test_result_type_field(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        for r in results:
            assert r.result_type == "conversation"

    def test_conversation_result_fields(self, tmp_path: Path) -> None:
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        for r in results:
            assert isinstance(r, SearchResult)
            assert r.document_id is None
            assert isinstance(r.score, float)
            assert isinstance(r.title, str)
            assert isinstance(r.text, str)

    def test_without_conversation_store(self, tmp_path: Path) -> None:
        """RetrievalService works without conversation_store (backward compat)."""
        service = RetrievalService(
            self.chunk_store,
            self.extraction_store,
            self.document_store,
        )
        # Should not crash, just no conversation results
        results = service.search("anything")
        assert results == ()

    def test_timestamp_in_result(self, tmp_path: Path) -> None:
        """Search results should include timestamp from conversation messages."""
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        for r in conv_results:
            # Gemini messages have None timestamps
            assert r.timestamp is None

    def test_is_active_branch_in_result(self, tmp_path: Path) -> None:
        """Search results should include is_active_branch."""
        _write_conversation(tmp_path, "20260101_BCG_aaa.json")
        ingest_gemini_conversations(tmp_path, self.conversation_store)
        service = self._service()
        results = service.search("BCG")
        conv_results = [r for r in results if r.result_type == "conversation"]
        for r in conv_results:
            # Gemini messages are always active
            assert r.is_active_branch is True


class TestConversationTemporalRegression:
    """Phase 16: search_knowledge time bounds must apply to conversation results."""

    JUST_AFTER = "2026-01-15T00:00:00+00:00"
    JUST_BEFORE = "2026-02-15T00:00:00+00:00"

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.chunk_store = ChunkStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.document_store = DocumentStore(self.connection)
        self.conversation_store = ConversationStore(self.connection)
        self.conversation_store.save_conversation(
            Conversation(
                id="conv-1",
                title="BCG Career Discussion",
                source_type="gemini",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-02-16T00:00:00+00:00",
                metadata={},
            )
        )
        self.conversation_store.save_message(
            ConversationMessage(
                id="m-jan",
                conversation_id="conv-1",
                message_index=0,
                role="user",
                speaker="User",
                content_text="BCG early January",
                content_type="text",
                timestamp=self.JUST_AFTER,
                parent_message_id=None,
                is_active_branch=True,
                metadata={},
            )
        )
        self.conversation_store.save_message(
            ConversationMessage(
                id="m-feb",
                conversation_id="conv-1",
                message_index=1,
                role="user",
                speaker="User",
                content_text="BCG mid February",
                content_type="text",
                timestamp=self.JUST_BEFORE,
                parent_message_id=None,
                is_active_branch=True,
                metadata={},
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()

    def _service(self) -> RetrievalService:
        return RetrievalService(
            self.chunk_store,
            self.extraction_store,
            self.document_store,
            self.conversation_store,
        )

    def test_created_before_filters_conversation_results(self) -> None:
        service = self._service()
        results = service.search(
            "BCG", filters=DocumentFilter(created_before="2026-02-01T00:00:00+00:00")
        )
        conv_texts = [r.text for r in results if r.result_type == "conversation"]
        assert "BCG early January" in conv_texts
        assert "BCG mid February" not in conv_texts

    def test_window_both_ends_applies_to_conversations(self) -> None:
        service = self._service()
        results = service.search(
            "BCG",
            filters=DocumentFilter(
                created_after=self.JUST_AFTER,
                created_before=self.JUST_BEFORE,
            ),
        )
        conv_texts = [r.text for r in results if r.result_type == "conversation"]
        # Both boundaries inclusive: both messages match.
        assert set(conv_texts) == {"BCG early January", "BCG mid February"}

    def test_multi_term_query_with_bounds_through_service(self) -> None:
        """A natural multi-word query combined with temporal bounds must flow
        all the way through RetrievalService into ConversationStore."""
        service = self._service()
        results = service.search(
            "BCG McKinsey consulting strategy",
            filters=DocumentFilter(created_before="2026-02-01T00:00:00+00:00"),
        )
        conv_texts = [r.text for r in results if r.result_type == "conversation"]
        assert "BCG early January" in conv_texts
        assert "BCG mid February" not in conv_texts

    def test_without_conversation_store_still_works(self) -> None:
        service = RetrievalService(
            self.chunk_store,
            self.extraction_store,
            self.document_store,
        )
        results = service.search(
            "BCG", filters=DocumentFilter(created_before="2026-02-01T00:00:00+00:00")
        )
        assert results == ()


class TestConversationMultiTermRetrieval:
    """Multi-term conversation retrieval through the full RetrievalService path."""

    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.chunk_store = ChunkStore(self.connection)
        self.extraction_store = ExtractionStore(self.connection)
        self.document_store = DocumentStore(self.connection)
        self.conversation_store = ConversationStore(self.connection)
        self._add("conv-bcg", "BCG case prep notes")
        self._add("conv-career", "Career planning discussion")
        self._add("conv-other", "Cooking recipes and food")

    def teardown_method(self) -> None:
        self.connection.close()

    def _add(self, conv_id: str, content: str) -> None:
        self.conversation_store.save_conversation(
            Conversation(
                id=conv_id,
                title="Neutral",
                source_type="gemini",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-01-02T00:00:00+00:00",
                metadata={},
            )
        )
        self.conversation_store.save_message(
            ConversationMessage(
                id=f"m-{conv_id}",
                conversation_id=conv_id,
                message_index=0,
                role="user",
                speaker="User",
                content_text=content,
                content_type="text",
                timestamp="2026-01-10T00:00:00+00:00",
                parent_message_id=None,
                is_active_branch=True,
                metadata={},
            )
        )

    def _service(self) -> RetrievalService:
        return RetrievalService(
            self.chunk_store,
            self.extraction_store,
            self.document_store,
            self.conversation_store,
        )

    def test_multi_word_query_finds_any_term_match(self) -> None:
        service = self._service()
        results = service.search("BCG McKinsey Bain consulting")
        conv_texts = [r.text for r in results if r.result_type == "conversation"]
        assert "BCG case prep notes" in conv_texts
        assert "Cooking recipes and food" not in conv_texts

    def test_multi_word_query_ranks_more_terms_higher(self) -> None:
        self._add("conv-case-only", "A case study example document")
        service = self._service()
        results = service.search("BCG case interview")
        by_id = {r.message_id: r.score for r in results}
        # m-conv-bcg matches "BCG"+"case"; m-conv-case-only matches "case" only.
        assert by_id["m-conv-bcg"] > by_id["m-conv-case-only"]

    def test_multi_word_query_respects_limit(self) -> None:
        service = self._service()
        results = service.search("BCG career food", limit=1)
        assert len(results) <= 1
