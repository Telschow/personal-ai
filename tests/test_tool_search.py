"""Tests for the search_documents agent tool and its registration."""

from pathlib import Path

import pytest

from personal_ai.documents import Document, DocumentChunk
from personal_ai.storage import ChunkStore, DocumentStore, connect_database
from personal_ai.tools import create_default_registry
from personal_ai.tools.registry import ToolExecutionError


def make_document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "id": "doc-1",
        "source": "notes/doc-1.json",
        "source_type": "keep",
        "content_hash": "hash-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "modified_at": "2026-01-01T00:00:00+00:00",
        "metadata": {},
    }
    values.update(overrides)
    return Document(**values)  # type: ignore[arg-type]


class KnowledgeFixture:
    """Chunk store seeded with two documents over one shared connection."""

    def __init__(self, database: Path | str = ":memory:") -> None:
        self._connection = connect_database(database)
        self.documents = DocumentStore(self._connection)
        self.chunks = ChunkStore(self._connection)
        self.documents.add(make_document(id="doc-keep"))
        self.documents.add(
            make_document(
                id="doc-nlm",
                source_type="notebooklm",
                created_at="2026-05-01T00:00:00+00:00",
                modified_at="2026-05-01T00:00:00+00:00",
                content_hash="hash-nlm",
            )
        )
        self.chunks.add_many(
            (
                DocumentChunk(
                    id="chunk-a", document_id="doc-keep", text="guitar practice log"
                ),
                DocumentChunk(
                    id="chunk-b",
                    document_id="doc-nlm",
                    text="guitar chord theory",
                    metadata={"chunk_index": 1},
                ),
            )
        )

    def close(self) -> None:
        self.chunks.close()


@pytest.fixture()
def knowledge(tmp_path: Path) -> KnowledgeFixture:
    fixture = KnowledgeFixture(tmp_path / "knowledge.db")
    yield fixture
    fixture.close()


@pytest.fixture()
def registry(knowledge: KnowledgeFixture, tmp_path: Path):
    return create_default_registry(tmp_path, chunk_store=knowledge.chunks)


def test_registry_without_store_hides_the_search_tool(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    names = [
        schema["function"]["name"]
        for schema in registry.schemas()  # type: ignore[index]
    ]
    assert names == ["list_directory"]


def test_registry_with_store_exposes_search_schema(registry) -> None:
    schemas = registry.schemas()

    assert [schema["function"]["name"] for schema in schemas] == [  # type: ignore[index]
        "list_directory",
        "search_documents",
    ]
    search_schema = schemas[-1]["function"]
    assert search_schema["parameters"]["required"] == ["query"]


def test_successful_search_returns_serialized_hits(registry) -> None:
    results = registry.execute("search_documents", {"query": "guitar"})

    assert results == [
        {
            "chunk_id": "chunk-a",
            "document_id": "doc-keep",
            "chunk_index": None,
            "text": "guitar practice log",
            "rank": results[0]["rank"],  # type: ignore[index]
        },
        {
            "chunk_id": "chunk-b",
            "document_id": "doc-nlm",
            "chunk_index": 1,
            "text": "guitar chord theory",
            "rank": results[1]["rank"],  # type: ignore[index]
        },
    ]
    assert isinstance(results[0]["rank"], float)  # type: ignore[index]


def test_source_type_filter_narrows_results(registry) -> None:
    results = registry.execute(
        "search_documents",
        {"query": "guitar", "filter": {"source_types": ["notebooklm"]}},
    )

    assert [hit["chunk_id"] for hit in results] == ["chunk-b"]  # type: ignore[index]


def test_date_filter_excludes_documents_outside_window(registry) -> None:
    results = registry.execute(
        "search_documents",
        {
            "query": "guitar",
            "filter": {"created_after": "2026-02-01T00:00:00+00:00"},
        },
    )

    assert [hit["chunk_id"] for hit in results] == ["chunk-b"]  # type: ignore[index]


def test_invalid_filter_date_surfaces_validation_error(registry) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        registry.execute(
            "search_documents",
            {"query": "guitar", "filter": {"created_after": "yesterday"}},
        )

    assert isinstance(exc_info.value.__cause__, ValueError)


def test_missing_query_is_rejected(registry) -> None:
    with pytest.raises(ToolExecutionError):
        registry.execute("search_documents", {})


def test_non_string_query_is_rejected(registry) -> None:
    with pytest.raises(ToolExecutionError):
        registry.execute("search_documents", {"query": 42})


def test_boolean_and_negative_limits_are_rejected(registry) -> None:
    for bad_limit in (True, -1):
        with pytest.raises(ToolExecutionError):
            registry.execute(
                "search_documents", {"query": "guitar", "limit": bad_limit}
            )


def test_unknown_arguments_are_rejected(registry) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        registry.execute(
            "search_documents", {"query": "guitar", "sql": "DROP TABLE users"}
        )

    assert "unsupported arguments" in str(exc_info.value.__cause__)


def test_unknown_filter_fields_are_rejected(registry) -> None:
    with pytest.raises(ToolExecutionError) as exc_info:
        registry.execute(
            "search_documents",
            {"query": "guitar", "filter": {"match_all": True}},
        )

    assert "unsupported fields" in str(exc_info.value.__cause__)


def test_empty_query_returns_no_results(registry) -> None:
    assert registry.execute("search_documents", {"query": ""}) == []
    assert registry.execute("search_documents", {"query": "   "}) == []


def test_no_match_returns_empty_list(registry) -> None:
    assert registry.execute("search_documents", {"query": "zeppelin"}) == []


def test_operator_words_stay_literal_through_the_tool(registry) -> None:
    # Boolean OR syntax would match both chunks; literal terms match none.
    assert registry.execute("search_documents", {"query": "practice OR theory"}) == []


def test_equal_ranks_order_deterministically(registry) -> None:
    first = registry.execute("search_documents", {"query": "guitar"})
    second = registry.execute("search_documents", {"query": "guitar"})

    ids = ["chunk-a", "chunk-b"]
    assert [hit["chunk_id"] for hit in first] == ids  # type: ignore[index]
    assert first == second


def test_limit_bounds_result_count(knowledge: KnowledgeFixture, tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path, chunk_store=knowledge.chunks)

    results = registry.execute("search_documents", {"query": "guitar", "limit": 1})

    assert len(results) == 1
