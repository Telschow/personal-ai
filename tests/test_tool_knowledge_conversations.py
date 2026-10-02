"""Tests for search_knowledge tool with conversation results."""

import pytest

from personal_ai.retrieval import SearchResult
from personal_ai.tools.knowledge import KnowledgeSearchTool


class FakeRetrievalService:
    def __init__(self, results: tuple = ()) -> None:
        self._results = results
        self.last_query: str | None = None

    def search(self, query: str, **kwargs):
        self.last_query = query
        return self._results


class TestKnowledgeSearchToolConversations:
    def test_conversation_result_formatted(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.85,
                    title="Career Goals",
                    text="I want to work at Example Corp",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-5",
                    message_index=5,
                    role="user",
                    speaker="User",
                    timestamp="2026-01-01T12:00:00+00:00",
                    is_active_branch=True,
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "Example Corp"})["results"]
        assert len(results) == 1
        r = results[0]
        assert r["result_type"] == "conversation"
        assert r["conversation_id"] == "conv-1"
        assert r["message_id"] == "msg-5"
        assert r["message_index"] == 5
        assert r["role"] == "user"
        assert r["speaker"] == "User"
        assert r["timestamp"] == "2026-01-01T12:00:00+00:00"
        assert r["is_active_branch"] is True
        assert r["source_type"] is None
        assert "[CONVERSATION]" in r["provenance"]
        assert "Career Goals" in r["provenance"]
        assert "Message 5" in r["provenance"]
        assert "User" in r["provenance"]
        assert "Timestamp:" in r["provenance"]

    def test_document_result_not_affected(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="chunk",
                    document_id="doc-1",
                    score=0.85,
                    title="meeting.txt",
                    text="Example Corp consulting notes",
                    page_number=None,
                    matched_fields=(),
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "Example Corp"})["results"]
        r = results[0]
        assert r["result_type"] == "chunk"
        assert r["document_id"] == "doc-1"
        assert "provenance" not in r

    def test_extraction_result_not_affected(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="structured_extraction",
                    document_id="doc-2",
                    score=0.6,
                    title="notes.txt",
                    text="Summary about consulting",
                    page_number=None,
                    matched_fields=("organizations",),
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "consulting"})["results"]
        r = results[0]
        assert r["result_type"] == "structured_extraction"
        assert "provenance" not in r

    def test_mixed_results(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="chunk",
                    document_id="doc-1",
                    score=0.9,
                    title="meeting.txt",
                    text="Example Corp notes",
                    page_number=None,
                    matched_fields=(),
                ),
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.7,
                    title="Career Chat",
                    text="Example Corp career discussion",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-3",
                    message_index=3,
                    role="assistant",
                    speaker="Gemini",
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "Example Corp"})["results"]
        assert len(results) == 2
        types = {r["result_type"] for r in results}
        assert "chunk" in types
        assert "conversation" in types

    def test_conversation_provenance_format(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.8,
                    title="Gemini Career Discussion",
                    text="I am interested in consulting",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-2",
                    message_index=2,
                    role="assistant",
                    speaker="Gemini",
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "consulting"})
        provenance = results["results"][0]["provenance"]
        lines = provenance.split("\n")
        assert lines[0] == "[CONVERSATION]"
        assert lines[1] == "Gemini Career Discussion"
        assert "Message 2" in lines[2]
        assert "Gemini" in lines[2]

    def test_argument_allowlisting_unchanged(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(ValueError, match="unsupported arguments"):
            tool.search_knowledge({"query": "test", "injected": True})

    def test_inactive_branch_shows_in_provenance(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.7,
                    title="Branch Chat",
                    text="Alternative answer",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-2",
                    message_index=2,
                    role="assistant",
                    speaker="ChatGPT",
                    timestamp="2026-01-01T12:00:00+00:00",
                    is_active_branch=False,
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        r = tool.search_knowledge({"query": "answer"})["results"][0]
        assert "(inactive branch)" in r["provenance"]
        assert r["is_active_branch"] is False

    def test_no_timestamp_hides_timestamp_line(self) -> None:
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.8,
                    title="Gemini Chat",
                    text="Hello",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-1",
                    message_index=1,
                    role="user",
                    speaker="User",
                    timestamp=None,
                    is_active_branch=True,
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "Hello"})
        provenance = results["results"][0]["provenance"]
        assert "Timestamp:" not in provenance

    def test_multi_word_query_passed_through_without_schema_change(self) -> None:
        """search_knowledge must accept a natural multi-word query unchanged;
        no schema change or query rewriting is needed for multi-term retrieval."""
        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="conversation",
                    document_id=None,
                    score=0.7,
                    title="Example Corp Prep",
                    text="Example Corp case interview tips",
                    page_number=None,
                    matched_fields=("content_text",),
                    conversation_id="conv-1",
                    message_id="msg-1",
                    message_index=1,
                    role="user",
                    speaker="User",
                    timestamp="2026-01-01T12:00:00+00:00",
                    is_active_branch=True,
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        query = "Example Corp McKinsey Bain consulting business strategy case interview"
        results = tool.search_knowledge({"query": query, "limit": 12})["results"]
        assert service.last_query == query
        assert len(results) == 1
        assert results[0]["result_type"] == "conversation"
        assert results[0]["text"] == "Example Corp case interview tips"
