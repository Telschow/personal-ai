"""Integration tests for the SQLite-backed document store."""

import sqlite3
from pathlib import Path

import pytest

from personal_ai.documents import Document
from personal_ai.storage import DocumentStore, connect_database

CREATED_AT = "2026-08-22T10:00:00+00:00"


def make_document(
    document_id: str = "doc-1",
    *,
    created_at: str = CREATED_AT,
    modified_at: str = CREATED_AT,
    metadata: dict[str, object] | None = None,
) -> Document:
    return Document(
        id=document_id,
        source="notes/ideas.txt",
        source_type="file",
        content_hash=f"hash-for-{document_id}",
        created_at=created_at,
        modified_at=modified_at,
        path="/workspace/notes/ideas.txt",
        filename="ideas.txt",
        mime_type="text/plain",
        metadata=metadata if metadata is not None else {},
    )


def make_store(path: Path | str = ":memory:") -> DocumentStore:
    return DocumentStore(connect_database(path))


def test_add_then_get_round_trips_full_document() -> None:
    document = make_document(metadata={"topics": ["goals", "fitness"]})

    with make_store() as store:
        assert store.add(document) is True
        assert store.get("doc-1") == document


def test_readding_same_document_is_not_a_duplicate(tmp_path: Path) -> None:
    with make_store(tmp_path / "personal.db") as store:
        assert store.add(make_document()) is True
        assert store.add(make_document()) is False

        assert len(store.list_documents()) == 1


def test_readd_updates_entry_but_preserves_created_at() -> None:
    original = make_document(metadata={"topics": ["goals"]})
    reingested = make_document(
        modified_at="2026-08-23T09:00:00+00:00",
        metadata={"topics": ["career"]},
    )
    reingested.metadata["pages"] = 2

    with make_store() as store:
        store.add(original)
        store.add(reingested)

        stored = store.get("doc-1")

    assert stored is not None
    assert stored.created_at == CREATED_AT
    assert stored.modified_at == "2026-08-23T09:00:00+00:00"
    assert stored.metadata == {"topics": ["career"], "pages": 2}


def test_get_unknown_id_returns_none() -> None:
    with make_store() as store:
        assert store.get("missing") is None


def test_list_documents_orders_deterministically() -> None:
    later = make_document("later-doc", created_at="2026-08-23T10:00:00+00:00")
    earlier = make_document("earlier-doc", created_at="2026-08-21T10:00:00+00:00")
    same_day = make_document("a-doc", created_at="2026-08-22T10:00:00+00:00")

    with make_store() as store:
        store.add(later)
        store.add(earlier)
        store.add(same_day)

        ids = [document.id for document in store.list_documents()]

    assert ids == ["earlier-doc", "a-doc", "later-doc"]


def test_metadata_is_snapshotted_at_write_time() -> None:
    document = make_document()
    with make_store() as store:
        store.add(document)
        document.metadata["mutated"] = True

        stored = store.get("doc-1")

    assert stored is not None
    assert "mutated" not in stored.metadata


def test_optional_fields_survive_round_trip() -> None:
    minimal = Document(
        id="minimal",
        source="export/chat.json",
        source_type="export",
        content_hash="hash-minimal",
        created_at=CREATED_AT,
        modified_at=CREATED_AT,
    )

    with make_store() as store:
        store.add(minimal)

        assert store.get("minimal") == minimal


def test_documents_persist_across_connections(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"

    with make_store(database) as first_store:
        first_store.add(make_document())

    with make_store(database) as second_store:
        assert second_store.get("doc-1") == make_document()


def test_context_manager_closes_connection() -> None:
    connection = connect_database(":memory:")

    with DocumentStore(connection) as store:
        store.add(make_document())

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("doc-1")
