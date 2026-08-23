"""Integration tests for the SQLite-backed extraction store."""

import json
import sqlite3
from pathlib import Path

import pytest

from personal_ai.documents import StructuredExtraction
from personal_ai.storage import ExtractionStore, connect_database


def make_extraction(**overrides: object) -> StructuredExtraction:
    values: dict[str, object] = {
        "document_id": "doc-1",
        "summary": "Career planning notes",
        "people": ("Alice",),
        "organizations": ("ACME",),
        "projects": ("kitchen renovation",),
        "goals": ("financial independence",),
        "topics": ("career", "finance"),
        "metadata": {"model": "test-model"},
    }
    values.update(overrides)
    return StructuredExtraction(**values)  # type: ignore[arg-type]


def make_store(path: Path | str = ":memory:") -> ExtractionStore:
    return ExtractionStore(connect_database(path))


def test_save_then_get_round_trips_full_extraction() -> None:
    extraction = make_extraction()

    with make_store() as store:
        assert store.save(extraction) is True
        assert store.get("doc-1") == extraction


def test_minimal_extraction_round_trips_with_defaults() -> None:
    minimal = StructuredExtraction(document_id="doc-2")

    with make_store() as store:
        assert store.save(minimal) is True

        stored = store.get("doc-2")

    assert stored == minimal


def test_resaving_same_document_id_is_not_a_duplicate() -> None:
    updated = make_extraction(summary="Updated summary")

    with make_store() as store:
        assert store.save(make_extraction()) is True
        assert store.save(updated) is False

        assert store.get("doc-1") == updated


def test_get_unknown_id_returns_none() -> None:
    with make_store() as store:
        assert store.get("missing") is None


def test_collections_are_stored_as_json(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"

    with make_store(database) as store:
        store.save(make_extraction())

    connection = connect_database(database)
    try:
        row = connection.execute(
            "SELECT people FROM structured_extractions WHERE document_id = 'doc-1'"
        ).fetchone()
    finally:
        connection.close()

    assert json.loads(str(row[0])) == ["Alice"]


def test_metadata_is_snapshotted_at_write_time() -> None:
    extraction = make_extraction()

    with make_store() as store:
        store.save(extraction)
        extraction.metadata["mutated"] = True

        stored = store.get("doc-1")

    assert stored is not None
    assert "mutated" not in stored.metadata


def test_distinct_documents_keep_distinct_extractions() -> None:
    first = make_extraction(document_id="doc-1")
    second = make_extraction(document_id="doc-2")

    with make_store() as store:
        assert store.save(first) is True
        assert store.save(second) is True

        assert store.get("doc-1") == first
        assert store.get("doc-2") == second


def test_extractions_persist_across_connections(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"

    with make_store(database) as store:
        store.save(make_extraction())

    with make_store(database) as store:
        assert store.get("doc-1") == make_extraction()


def test_context_manager_closes_connection() -> None:
    connection = connect_database(":memory:")

    with ExtractionStore(connection) as store:
        store.save(make_extraction())

    with pytest.raises(sqlite3.ProgrammingError):
        store.get("doc-1")
