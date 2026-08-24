"""Tests for the application-facing search service."""

from personal_ai.documents import Document, DocumentChunk
from personal_ai.retrieval import (
    DEFAULT_SEARCH_LIMIT,
    SearchDocumentsRequest,
    search_documents,
)
from personal_ai.storage import (
    DocumentFilter,
    DocumentStore,
    connect_database,
)
from personal_ai.storage.chunks import ChunkStore


class RecordingChunkStore(ChunkStore):
    """Stub that records delegated calls instead of touching SQLite."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, DocumentFilter | None]] = []
        self._result: tuple[object, ...] = ()

    def queue_result(self, result: tuple[object, ...]) -> None:
        self._result = result

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[object, ...]:
        self.calls.append((query, limit, filters))
        return self._result


def make_request(**overrides: object) -> SearchDocumentsRequest:
    values: dict[str, object] = {"query": "guitar"}
    values.update(overrides)
    return SearchDocumentsRequest(**values)  # type: ignore[arg-type]


def test_service_delegates_to_the_chunk_store_unchanged() -> None:
    store = RecordingChunkStore()
    sentinel: tuple[object, ...] = ("hit",)

    store.queue_result(sentinel)
    result = search_documents(store, make_request())

    assert result is sentinel
    assert store.calls == [("guitar", DEFAULT_SEARCH_LIMIT, None)]


def test_service_forwards_query_limit_and_filter_verbatim() -> None:
    store = RecordingChunkStore()
    document_filter = DocumentFilter(
        source_types=("keep",), created_after="2026-01-01T00:00:00+00:00"
    )

    search_documents(
        store, make_request(query="kite", limit=3, document_filter=document_filter)
    )

    assert store.calls == [("kite", 3, document_filter)]


def test_default_limit_matches_the_storage_constant() -> None:
    from personal_ai.storage.chunks import DEFAULT_SEARCH_LIMIT as storage_limit

    assert DEFAULT_SEARCH_LIMIT == storage_limit == 10


def test_search_returns_typed_hits_with_full_provenance(tmp_path) -> None:
    connection = connect_database(tmp_path / "knowledge.db")
    documents = DocumentStore(connection)
    chunks = ChunkStore(connection)
    try:
        document = Document(
            id="doc-1",
            source="notes/doc-1.json",
            source_type="keep",
            content_hash="hash-1",
            created_at="2026-01-01T00:00:00+00:00",
            modified_at="2026-01-01T00:00:00+00:00",
        )
        chunk = DocumentChunk(
            id="chunk-1", document_id="doc-1", text="guitar practice plan"
        )
        documents.add(document)
        chunks.add(chunk)

        results = search_documents(chunks, make_request(query="guitar", limit=5))

    finally:
        chunks.close()

    assert len(results) == 1
    hit = results[0]
    assert hit.chunk_id == "chunk-1"
    assert hit.document_id == "doc-1"
    assert hit.chunk_index is None
    assert hit.text == "guitar practice plan"
    assert isinstance(hit.rank, float)


def test_empty_query_returns_no_results(tmp_path) -> None:
    connection = connect_database(tmp_path / "knowledge.db")
    chunks = ChunkStore(connection)
    try:
        results = search_documents(chunks, make_request(query="   "))
    finally:
        chunks.close()

    assert results == ()


def test_non_string_query_is_rejected() -> None:
    try:
        make_request(query=123)
    except TypeError as exc:
        assert "query must be a string" in str(exc)
    else:
        msg = "Non-string query must be rejected"
        raise AssertionError(msg)


def test_non_integer_and_boolean_limits_are_rejected() -> None:
    for bad_limit in ("5", 2.5, True):
        try:
            make_request(limit=bad_limit)
        except TypeError as exc:
            assert "limit must be an integer" in str(exc)
        else:
            msg = f"Limit {bad_limit!r} must be rejected"
            raise AssertionError(msg)


def test_negative_limit_is_rejected() -> None:
    try:
        make_request(limit=-1)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        msg = "Negative limit must be rejected"
        raise AssertionError(msg)


def test_zero_limit_is_accepted() -> None:
    assert make_request(limit=0).limit == 0
