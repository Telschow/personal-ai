"""Integration tests for the SQLite-backed chunk store."""

import sqlite3
from pathlib import Path

import pytest

from personal_ai.documents import (
    DocumentChunk,
    TextExtractionResult,
    chunk_document,
)
from personal_ai.storage import ChunkStore, connect_database


def make_chunk(**overrides: object) -> DocumentChunk:
    values: dict[str, object] = {
        "id": "chunk-1",
        "document_id": "doc-1",
        "text": "first searchable passage",
        "page_number": 3,
        "metadata": {
            "chunk_index": 0,
            "source_type": "file",
            "source_key": "notes/ideas.txt",
            "content_hash": "hash-1",
        },
    }
    values.update(overrides)
    return DocumentChunk(**values)  # type: ignore[arg-type]


def make_store(path: Path | str = ":memory:") -> ChunkStore:
    return ChunkStore(connect_database(path))


def test_add_then_get_round_trips_full_chunk() -> None:
    stored_chunk = make_chunk()

    with make_store() as store:
        assert store.add(stored_chunk) is True
        assert store.get("chunk-1") == stored_chunk


def test_minimal_chunk_round_trips_with_defaults() -> None:
    minimal = DocumentChunk(id="chunk-min", document_id="doc-1", text="body")

    with make_store() as store:
        assert store.add(minimal) is True

        assert store.get("chunk-min") == minimal


def test_metadata_survives_round_trip() -> None:
    metadata = {
        "chunk_index": 2,
        "source_key": "mail/msg-7",
        "tags": ["goals", "career"],
        "confidence": 0.8,
    }

    with make_store() as store:
        store.add(make_chunk(metadata=metadata))

        assert store.get("chunk-1") is not None
        assert store.get("chunk-1").metadata == metadata  # type: ignore[union-attr]


def test_get_unknown_id_returns_none() -> None:
    with make_store() as store:
        assert store.get("missing") is None


def test_resaving_same_chunk_id_is_not_a_duplicate() -> None:
    updated = make_chunk(text="revised passage", metadata={"chunk_index": 0})

    with make_store() as store:
        assert store.add(make_chunk()) is True
        assert store.add(updated) is False

        assert len(store.list_for_document("doc-1")) == 1
        assert store.get("chunk-1") == updated


def test_none_and_zero_page_number_stay_distinct() -> None:
    unnumbered = make_chunk(id="chunk-none", page_number=None)
    numbered_zero = make_chunk(id="chunk-zero", page_number=0)

    with make_store() as store:
        store.add(unnumbered)
        store.add(numbered_zero)

        assert store.get("chunk-none") == unnumbered
        assert store.get("chunk-zero") == numbered_zero
        assert store.get("chunk-zero").page_number == 0  # type: ignore[union-attr]


def test_add_many_stores_multiple_chunks() -> None:
    chunks = (
        make_chunk(id="chunk-a", metadata={"chunk_index": 0}),
        make_chunk(id="chunk-b", text="second passage", metadata={"chunk_index": 1}),
    )

    with make_store() as store:
        assert store.add_many(chunks) == 2

        assert store.get("chunk-a") == chunks[0]
        assert store.get("chunk-b") == chunks[1]


def test_replaying_add_many_inserts_nothing_new() -> None:
    chunks = (make_chunk(), make_chunk(id="chunk-b"))

    with make_store() as store:
        assert store.add_many(chunks) == 2
        assert store.add_many(chunks) == 0
        assert len(store.list_for_document("doc-1")) == 2


def test_add_many_empty_collection_is_a_harmless_no_op() -> None:
    with make_store() as store:
        assert store.add_many(()) == 0

        assert store.get("chunk-1") is None


def test_list_for_document_orders_by_chunk_index_regardless_of_insert_order() -> None:
    later = make_chunk(id="chunk-later", text="second", metadata={"chunk_index": 1})
    earlier = make_chunk(id="chunk-earlier", text="first", metadata={"chunk_index": 0})

    with make_store() as store:
        store.add_many((later, earlier))

        ordered = store.list_for_document("doc-1")

    assert [c.id for c in ordered] == ["chunk-earlier", "chunk-later"]
    assert [c.text for c in ordered] == ["first", "second"]


def test_chunks_without_index_metadata_order_deterministically_by_id() -> None:
    unindexed = (
        make_chunk(id="chunk-zz", metadata={}),
        make_chunk(id="chunk-aa", metadata={}),
    )

    with make_store() as store:
        store.add_many(unindexed)

        first = store.list_for_document("doc-1")
        second = store.list_for_document("doc-1")

    assert [c.id for c in first] == ["chunk-aa", "chunk-zz"]
    assert first == second


def test_chunks_from_different_documents_remain_isolated() -> None:
    other_document = make_chunk(
        id="chunk-other", document_id="doc-2", metadata={"chunk_index": 0}
    )
    own = make_chunk()

    with make_store() as store:
        store.add_many((own, other_document))

        assert store.list_for_document("doc-1") == (own,)
        assert store.list_for_document("doc-2") == (other_document,)


def test_delete_for_document_removes_only_that_documents_chunks() -> None:
    survivor = make_chunk(id="chunk-survivor", document_id="doc-2")
    removed_first = make_chunk(id="chunk-gone-a")
    removed_second = make_chunk(id="chunk-gone-b")

    with make_store() as store:
        store.add_many((survivor, removed_first, removed_second))

        assert store.delete_for_document("doc-1") == 2

        assert store.get("chunk-gone-a") is None
        assert store.get("chunk-gone-b") is None
        assert store.get("chunk-survivor") == survivor


def test_deleting_missing_document_is_harmless() -> None:
    with make_store() as store:
        assert store.delete_for_document("no-such-document") == 0


def test_chunks_persist_across_connections(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"

    with make_store(database) as store:
        store.add(make_chunk())

    with make_store(database) as store:
        assert store.get("chunk-1") == make_chunk()


def test_context_manager_closes_connection() -> None:
    connection = connect_database(":memory:")

    with ChunkStore(connection) as store:
        store.add(make_chunk())

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("chunk-1")


def test_chunker_output_round_trips_through_the_store() -> None:
    extraction = TextExtractionResult(
        document_id="doc-42",
        source_type="file",
        source_key="docs/goals.md",
        content_hash="hash-42",
        text="goal one. goal two. " * 60,
    )
    chunks = chunk_document(extraction, chunk_size=120, overlap=20)

    with make_store() as store:
        assert store.add_many(chunks) == len(chunks)

        restored = store.list_for_document("doc-42")

    assert restored == chunks


def test_list_chunks_returns_all_in_chunk_id_order() -> None:
    ids = ["chunk-z", "chunk-a", "chunk-m"]
    chunks = [make_chunk(id=chunk_id, text=f"text {chunk_id}") for chunk_id in ids]

    with make_store() as store:
        store.add_many(chunks)

        listed = store.list_chunks()

    assert [chunk.id for chunk in listed] == ["chunk-a", "chunk-m", "chunk-z"]
    assert listed == tuple(sorted(chunks, key=lambda chunk: chunk.id))


def test_list_chunks_bounded_window_with_offset() -> None:
    with make_store() as store:
        for index in range(5):
            store.add(make_chunk(id=f"chunk-{index:02d}", text=f"text {index}"))

        with pytest.raises(ValueError):
            store.list_chunks(limit=-1)
        first = store.list_chunks(limit=2)
        second = store.list_chunks(limit=2, offset=2)
        tail = store.list_chunks(limit=2, offset=4)
        beyond = store.list_chunks(limit=2, offset=10)
        none = store.list_chunks(limit=0)

    assert [chunk.id for chunk in first] == ["chunk-00", "chunk-01"]
    assert [chunk.id for chunk in second] == ["chunk-02", "chunk-03"]
    assert [chunk.id for chunk in tail] == ["chunk-04"]
    assert beyond == ()
    assert none == ()


def test_list_chunks_offset_without_limit_returns_remainder() -> None:
    with make_store() as store:
        for index in range(5):
            store.add(make_chunk(id=f"chunk-{index:02d}", text=f"text {index}"))

        listed = store.list_chunks(offset=2)

    assert [chunk.id for chunk in listed] == ["chunk-02", "chunk-03", "chunk-04"]


def test_list_chunks_empty_store_returns_nothing() -> None:
    with make_store() as store:
        assert store.list_chunks() == ()
