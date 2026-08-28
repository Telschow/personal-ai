"""Tests for the search_knowledge agent tool."""

import pytest

from personal_ai.tools.knowledge import KnowledgeSearchTool


class FakeRetrievalService:
    def __init__(self, results: tuple = ()) -> None:
        self._results = results
        self.last_query: str | None = None
        self.last_limit: int | None = None
        self.last_filters: object | None = None

    def search(self, query: str, *, limit: int = 10, filters=None):
        self.last_query = query
        self.last_limit = limit
        self.last_filters = filters
        return self._results


class TestKnowledgeSearchTool:
    def test_calls_service_with_query(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        tool.search_knowledge({"query": "BCG"})
        assert service.last_query == "BCG"

    def test_calls_service_with_limit(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        tool.search_knowledge({"query": "BCG", "limit": 3})
        assert service.last_limit == 3

    def test_default_limit(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        tool.search_knowledge({"query": "BCG"})
        assert service.last_limit == 10

    def test_rejects_unknown_arguments(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(ValueError, match="unsupported arguments"):
            tool.search_knowledge({"query": "BCG", "injected": True})

    def test_rejects_missing_query(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(TypeError, match="query must be a string"):
            tool.search_knowledge({})

    def test_rejects_non_string_query(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(TypeError, match="query must be a string"):
            tool.search_knowledge({"query": 123})

    def test_rejects_non_integer_limit(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(TypeError, match="limit must be an integer"):
            tool.search_knowledge({"query": "BCG", "limit": "five"})

    def test_returns_formatted_results(self) -> None:
        from personal_ai.retrieval import SearchResult

        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="chunk",
                    document_id="doc-1",
                    score=0.85,
                    title="meeting.txt",
                    text="BCG consulting project notes",
                    page_number=None,
                    matched_fields=(),
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "BCG"})
        assert len(results) == 1
        assert results[0]["result_type"] == "chunk"
        assert results[0]["document_id"] == "doc-1"
        assert results[0]["score"] == 0.85
        assert results[0]["title"] == "meeting.txt"
        assert results[0]["matched_fields"] == []

    def test_extraction_result_includes_matched_fields(self) -> None:
        from personal_ai.retrieval import SearchResult

        service = FakeRetrievalService(
            results=(
                SearchResult(
                    result_type="structured_extraction",
                    document_id="doc-2",
                    score=0.6,
                    title="notes.txt",
                    text="Summary about consulting",
                    page_number=None,
                    matched_fields=("organizations", "topics"),
                ),
            )
        )
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "consulting"})
        assert results[0]["matched_fields"] == ["organizations", "topics"]

    def test_passes_filter_to_service(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        tool.search_knowledge(
            {
                "query": "BCG",
                "filter": {
                    "source_types": ["pdf"],
                    "created_after": "2026-01-01T00:00:00+00:00",
                },
            }
        )
        assert service.last_filters is not None
        assert service.last_filters.source_types == ("pdf",)
        assert service.last_filters.created_after == "2026-01-01T00:00:00+00:00"

    def test_created_before_filter_reaches_retrieval_service(self) -> None:
        """Phase 16: the tool passes created_before through to conversation
        retrieval. The actual conversation-time filtering lives in
        RetrievalService; here we prove the tool forwards the bound."""
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        tool.search_knowledge(
            {
                "query": "BCG",
                "filter": {"created_before": "2026-02-01T00:00:00+00:00"},
            }
        )
        assert service.last_filters is not None
        assert service.last_filters.created_before == "2026-02-01T00:00:00+00:00"

    def test_rejects_invalid_filter_key(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(ValueError, match="unsupported fields"):
            tool.search_knowledge({"query": "BCG", "filter": {"sneaky_field": "value"}})

    def test_rejects_non_string_source_types(self) -> None:
        service = FakeRetrievalService()
        tool = KnowledgeSearchTool(service)
        with pytest.raises(ValueError, match="list of strings"):
            tool.search_knowledge(
                {"query": "BCG", "filter": {"source_types": [1, 2, 3]}}
            )

    def test_empty_results(self) -> None:
        service = FakeRetrievalService(results=())
        tool = KnowledgeSearchTool(service)
        results = tool.search_knowledge({"query": "nonexistent"})
        assert results == []
