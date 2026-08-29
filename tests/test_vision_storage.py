"""Tests for the SQLite-backed vision page cache."""

import sqlite3

import pytest

from personal_ai.storage.vision import StoredVisionPage, VisionStore


def make_store() -> tuple[VisionStore, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    return VisionStore(connection), connection


class TestVisionStore:
    def test_save_then_get_round_trips_row(self) -> None:
        store, connection = make_store()
        try:
            inserted = store.save("doc-1", 3, "visible page text", "vision-model", "v1")

            stored = store.get("doc-1", 3)

            assert inserted is True
            assert isinstance(stored, StoredVisionPage)
            assert stored.text == "visible page text"
            assert stored.vision_model == "vision-model"
            assert stored.prompt_version == "v1"
            assert stored.created_at  # non-empty timestamp
        finally:
            connection.close()

    def test_resave_updates_in_place_without_duplicates(self) -> None:
        store, connection = make_store()
        try:
            first_new = store.save("doc-1", 1, "old text", "vision-model", "v1")
            second_new = store.save("doc-1", 1, "new text", "vision-model", "v1")

            assert first_new is True
            assert second_new is False
            assert store.get("doc-1", 1).text == "new text"
            count = connection.execute(
                "SELECT COUNT(*) FROM vision_pages WHERE document_id = ? AND page_number = ?",
                ("doc-1", 1),
            ).fetchone()[0]
            assert int(count) == 1
        finally:
            connection.close()

    def test_missing_page_returns_none(self) -> None:
        store, connection = make_store()
        try:
            assert store.get("doc-1", 1) is None
        finally:
            connection.close()

    def test_pages_are_keyed_by_document_and_page_number(self) -> None:
        store, connection = make_store()
        try:
            store.save("doc-a", 1, "a1", "m", "v1")
            store.save("doc-a", 2, "a2", "m", "v1")
            store.save("doc-b", 1, "b1", "m", "v1")

            assert store.get("doc-a", 1).text == "a1"
            assert store.get("doc-a", 2).text == "a2"
            assert store.get("doc-b", 1).text == "b1"
            assert store.get("doc-a", 3) is None
            assert store.get("doc-b", 2) is None
        finally:
            connection.close()

    def test_table_uses_compound_primary_key(self) -> None:
        store, connection = make_store()
        try:
            store.save("doc-1", 1, "text", "m", "v1")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO vision_pages VALUES (?, ?, ?, ?, ?, ?)",
                    ("doc-1", 1, "dup", "m", "v1", "2026-01-01T00:00:00+00:00"),
                )
        finally:
            connection.close()
