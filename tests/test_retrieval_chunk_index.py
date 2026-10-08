"""Tests for the typed ChunkIndex retrieval boundary and its SQLite FTS5 backend."""

import pytest

from personal_ai.documents import Document, DocumentChunk
from personal_ai.documents.models import compute_content_hash, compute_document_id
from personal_ai.retrieval import (
    ChunkIndex,
    RetrievalService,
    SearchDocumentsRequest,
    search_documents,
)
from personal_ai.storage import (
    ChunkStore,
    DocumentStore,
    ExtractionStore,
    SQLiteChunkIndex,
    connect_database,
)

SEARCHABLE = "quarterly budget review notes"


def make_chunk(**overrides: object) -> DocumentChunk:
    values: dict[str, object] = {
        "id": "chunk-1",
        "document_id": "doc-1",
        "text": SEARCHABLE,
        "metadata": {"chunk_index": 0},
    }
    values.update(overrides)
    return DocumentChunk(**values)  # type: ignore[arg-type]


def make_document(
    document_id: str, source_type: str = "file", source: str = "notes.txt"
) -> Document:
    return Document(
        id=document_id,
        source=source,
        source_type=source_type,
        content_hash=compute_content_hash(b"x"),
        created_at="2026-08-26T10:00:00+00:00",
        modified_at="2026-08-26T10:00:00+00:00",
    )


def build_index(*chunks: DocumentChunk) -> SQLiteChunkIndex:
    connection = connect_database(":memory:")
    store = ChunkStore(connection)
    store.add_many(chunks)
    return SQLiteChunkIndex(connection)


class _RecordingIndex:
    """A fake ChunkIndex that records calls; never touches SQLite."""

    def __init__(self, hits: tuple = ()) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int, object]] = []

    def search(self, query, limit=10, filters=None):
        self.calls.append((query, limit, filters))
        return self.hits


def test_chunk_store_satisfies_chunk_index_protocol() -> None:
    connection = connect_database(":memory:")
    assert isinstance(ChunkStore(connection), ChunkIndex)
    assert isinstance(SQLiteChunkIndex(connection), ChunkIndex)


def test_index_returns_typed_result_for_matching_chunk() -> None:
    index = build_index(make_chunk())

    hits = index.search("budget")

    assert len(hits) == 1
    assert hits[0].chunk_id == "chunk-1"
    assert hits[0].document_id == "doc-1"
    assert hits[0].chunk_index == 0
    assert hits[0].text == SEARCHABLE
    assert isinstance(hits[0].rank, float)


def test_no_match_returns_empty_tuple() -> None:
    hits = build_index(make_chunk()).search("nonexistent term")
    assert hits == ()


def test_results_mirror_chunk_store_semantics() -> None:
    chunks = (
        make_chunk(id="a", text="budget review"),
        make_chunk(id="b", text="finance plan"),
    )
    connection = connect_database(":memory:")
    store = ChunkStore(connection)
    store.add_many(chunks)
    index = SQLiteChunkIndex(connection)

    from_index = index.search("budget")
    from_store = store.search("budget")

    assert from_index == from_store
    assert from_index[0].chunk_id == "a"


def test_ranking_orders_best_match_first_with_deterministic_tie_break() -> None:
    index = build_index(
        make_chunk(id="c", text="budget"),
        make_chunk(id="a", text="review"),
        make_chunk(id="b", text="budget"),
        make_chunk(id="e", text="budget"),
    )

    hits = index.search("budget")

    assert [h.chunk_id for h in hits] == ["b", "c", "e"]
    assert all(hits[i].rank <= hits[i + 1].rank for i in range(len(hits) - 1))


def test_empty_and_whitespace_queries_return_no_results() -> None:
    index = build_index(make_chunk())
    assert index.search("") == ()
    assert index.search("   ") == ()


def test_limit_bounds_results() -> None:
    index = build_index(make_chunk(id="a"), make_chunk(id="b"))
    assert len(index.search("budget", limit=1)) == 1
    assert index.search("budget", limit=0) == ()


def test_negative_limit_rejected() -> None:
    with pytest.raises(ValueError):
        build_index(make_chunk()).search("budget", limit=-1)


def test_large_limit_returns_all_matches() -> None:
    chunks = tuple(make_chunk(id=f"c{i}", text="budget") for i in range(40))
    hits = build_index(*chunks).search("budget", limit=10_000)
    assert len(hits) == 40


def test_punctuation_and_fts_operators_are_literal() -> None:
    index = build_index(make_chunk(text="budget OR finance notes"))

    hits = index.search("budget OR finance")

    assert [h.chunk_id for h in hits] == ["chunk-1"]


def test_unicode_keywords_match_and_stay_unicode() -> None:
    index = build_index(make_chunk(text="München Reiseplanung für 2026"))

    hits = index.search("München")

    assert len(hits) == 1
    assert hits[0].text == "München Reiseplanung für 2026"


def test_multi_document_identities_and_isolation() -> None:
    doc_a = compute_document_id("file", "a.txt", "hash-a")
    doc_b = compute_document_id("file", "b.txt", "hash-b")
    index = build_index(
        make_chunk(id="a1", document_id=doc_a, text="fitness plan"),
        make_chunk(id="b1", document_id=doc_b, text="fitness plan"),
    )

    hits = index.search("fitness")

    assert {h.document_id for h in hits} == {doc_a, doc_b}
    assert {h.chunk_id for h in hits} == {"a1", "b1"}


def test_provenance_resolves_from_documents_table() -> None:
    doc_id = compute_document_id("file", "notes.txt", "hash-mine")
    connection = connect_database(":memory:")
    DocumentStore(connection).add(make_document(doc_id))
    ChunkStore(connection).add(make_chunk(document_id=doc_id))

    hits = SQLiteChunkIndex(connection).search("budget")

    assert len(hits) == 1
    assert hits[0].source_type == "file"
    assert hits[0].source == "notes.txt"


def test_document_filter_restricts_to_owning_documents() -> None:
    from personal_ai.storage import DocumentFilter

    email_doc = compute_document_id("email", "sender@example.test", "hash-email")
    note_doc = compute_document_id("file", "notes.txt", "hash-note")
    connection = connect_database(":memory:")
    DocumentStore(connection).add(make_document(email_doc, "email", "me@example.test"))
    DocumentStore(connection).add(make_document(note_doc))
    ChunkStore(connection).add(
        make_chunk(id="mail", document_id=email_doc, text="quarterly budget")
    )
    ChunkStore(connection).add(
        make_chunk(id="note", document_id=note_doc, text="quarterly budget")
    )

    hits = SQLiteChunkIndex(connection).search(
        "budget", filters=DocumentFilter(source_types=("email",))
    )

    assert [h.chunk_id for h in hits] == ["mail"]


def test_search_documents_accepts_any_chunk_index_implementation() -> None:
    recording = _RecordingIndex(hits=build_index(make_chunk()).search("budget"))

    result = search_documents(
        recording,
        SearchDocumentsRequest(query="budget", limit=5),
    )

    assert isinstance(recording, ChunkIndex)
    assert recording.calls == [("budget", 5, None)]
    assert len(result) == 1
    assert result[0].chunk_id == "chunk-1"


def test_retrieval_service_consumes_the_abstraction_not_the_backend() -> None:
    connection = connect_database(":memory:")
    recording = _RecordingIndex()
    service = RetrievalService(
        recording,
        ExtractionStore(connection),
        DocumentStore(connection),
    )

    results = service.search("taxes")

    assert results == ()
    assert recording.calls == [("taxes", 10, None)]


def test_prebuilt_index_sees_late_additions_on_same_connection() -> None:
    connection = connect_database(":memory:")
    store = ChunkStore(connection)
    store.add(make_chunk(text="budget"))
    index = SQLiteChunkIndex(connection)

    assert {h.chunk_id for h in index.search("budget")} == {"chunk-1"}

    store.add(make_chunk(id="late", text="budget received"))

    assert {h.chunk_id for h in index.search("budget")} == {"chunk-1", "late"}
    assert index.search("received")[0].chunk_id == "late"
