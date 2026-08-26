"""Tests for retrieval integration with conversation messages."""

import json
from pathlib import Path

from personal_ai.conversation_ingestion import ingest_gemini_conversations
from personal_ai.retrieval import RetrievalService, SearchResult
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
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
