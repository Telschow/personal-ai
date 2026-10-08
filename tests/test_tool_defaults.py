"""Tests for the default tool registry."""

from pathlib import Path

from personal_ai.retrieval import RetrievalService
from personal_ai.storage import (
    ChunkStore,
    ConversationStore,
    DocumentStore,
    EventStore,
    ExtractionStore,
    connect_database,
)
from personal_ai.tools import create_default_registry


def _knowledge_and_event_registry(tmp_path: Path) -> object:
    """Build a registry exposing both search_knowledge and query_events.

    The chunk store is seeded with one indexed chunk so the narrow
    ``search_documents`` tool is also registered; without indexed document
    content that tool is intentionally hidden (Phase 23).
    """
    connection = connect_database(":memory:")
    chunk_store = ChunkStore(connection)
    extraction_store = ExtractionStore(connection)
    document_store = DocumentStore(connection)
    conversation_store = ConversationStore(connection)
    event_store = EventStore(connection)
    from personal_ai.documents import Document, DocumentChunk

    document_store.add(
        Document(
            id="doc-seed",
            source="notes/seed.json",
            source_type="keep",
            content_hash="hash-seed",
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
            metadata={},
        )
    )
    chunk_store.add(
        DocumentChunk(
            id="chunk-seed",
            document_id="doc-seed",
            text="career transition for consulting",
        )
    )
    service = RetrievalService(
        chunk_store,
        extraction_store,
        document_store,
        conversation_store,
    )
    registry = create_default_registry(
        tmp_path,
        chunk_store=chunk_store,
        retrieval_service=service,
        event_store=event_store,
    )
    connection.close()
    return registry


def _description(registry: object, name: str) -> str:
    for schema in registry.schemas():
        if schema["function"]["name"] == name:
            return schema["function"]["description"]
    raise AssertionError(f"{name} not in registry")


def test_default_registry_contains_list_directory(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)

    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files and directories in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Relative directory path. Defaults to the workspace root."
                            ),
                        }
                    },
                    "required": [],
                },
            },
        }
    ]


def test_default_registry_executes_list_directory(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello")
    (tmp_path / "documents").mkdir()

    registry = create_default_registry(tmp_path)

    assert registry.execute("list_directory", {}) == [
        {"name": "documents", "type": "directory"},
        {"name": "hello.txt", "type": "file"},
    ]


def test_registry_no_event_store_without_event_tool(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    names = {schema["function"]["name"] for schema in registry.schemas()}
    assert "query_events" not in names


def test_registry_registers_query_events_with_event_store(tmp_path: Path) -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        registry = create_default_registry(tmp_path, event_store=event_store)
        names = {schema["function"]["name"] for schema in registry.schemas()}
        assert "query_events" in names
        results = registry.execute("query_events", {"event_type": "search_query"})
        assert results == []
    finally:
        connection.close()


def test_registry_query_events_accepts_youtube_event_types(tmp_path: Path) -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        registry = create_default_registry(tmp_path, event_store=event_store)
        for event_type in ("video_watch", "youtube_search"):
            results = registry.execute("query_events", {"event_type": event_type})
            assert results == []
    finally:
        connection.close()


def test_registry_query_events_schema_exposes_keyword(tmp_path: Path) -> None:
    connection = connect_database(":memory:")
    try:
        event_store = EventStore(connection)
        registry = create_default_registry(tmp_path, event_store=event_store)
        schema = next(
            s for s in registry.schemas() if s["function"]["name"] == "query_events"
        )
        props = schema["function"]["parameters"]["properties"]
        assert "keyword" in props
        assert props["keyword"]["type"] == "string"
    finally:
        connection.close()


class TestCrossDomainCompositionGuidance:
    """Phase 17: tool descriptions carry the guidance needed to compose the
    knowledge and event domains with the existing two-tool architecture.

    These assertions are deterministic (they inspect the schemas the model
    sees), not model-behavior tests; they guard the reliability guidance that
    makes sequential composition dependable in the absence of a system prompt.
    """

    def test_search_knowledge_guides_domain_and_composition(
        self, tmp_path: Path
    ) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_knowledge")
        assert "durable personal knowledge" in description
        assert "query_events" in description
        assert "may require both tools" in description

    def test_query_events_guides_aggregate_preference(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "query_events")
        assert "prefer the aggregate operations" in description
        assert "top_searches" in description
        assert "raw 'events' operation" in description
        assert "search_knowledge" in description


class TestKnowledgeToolSelectionGuidance:
    """Phase 20: semantic descriptions make the narrow document/chunk search
    distinguishable from the broad knowledge search, so the model can reliably
    choose the appropriate tool. These are deterministic schema assertions, not
    model-behavior tests."""

    def test_search_documents_is_narrow_chunks_only(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_documents")
        description_lower = description.lower()
        assert "chunk" in description_lower
        assert "search_knowledge" in description
        assert "not the general-purpose" in description_lower
        assert "narrow" in description_lower

    def test_search_documents_excludes_broad_sources(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_documents")
        description_lower = description.lower()
        assert "does not search conversations" in description_lower
        assert "structured extractions" in description_lower

    def test_search_knowledge_covers_broad_sources(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_knowledge")
        description_lower = description.lower()
        assert "document/chunk" in description_lower
        assert "structured extractions" in description_lower
        assert "conversation messages" in description_lower

    def test_search_knowledge_is_broader_and_preferred(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_knowledge")
        description_lower = description.lower()
        assert "general-purpose" in description_lower
        assert "preferred knowledge tool" in description_lower
        assert "broader than search_documents" in description_lower
        assert "knows, wrote, discussed, researched, or recorded" in description_lower

    def test_search_knowledge_complements_query_events(self, tmp_path: Path) -> None:
        registry = _knowledge_and_event_registry(tmp_path)
        description = _description(registry, "search_knowledge")
        description_lower = description.lower()
        assert "complements query_events" in description_lower
        assert "may require both tools" in description_lower
