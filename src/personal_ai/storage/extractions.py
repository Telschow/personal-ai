"""SQLite-backed storage for structured extraction results."""

import json
import sqlite3
from types import TracebackType
from typing import Self

from personal_ai.documents.structured import StructuredExtraction

_EXTRACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS structured_extractions (
    document_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    people TEXT NOT NULL,
    organizations TEXT NOT NULL,
    projects TEXT NOT NULL,
    goals TEXT NOT NULL,
    topics TEXT NOT NULL,
    metadata TEXT NOT NULL
)
"""

_COLUMNS = (
    "document_id",
    "summary",
    "people",
    "organizations",
    "projects",
    "goals",
    "topics",
    "metadata",
)

_LIST_FIELDS = ("people", "organizations", "projects", "goals", "topics")

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO structured_extractions ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)


def _extraction_to_row(extraction: StructuredExtraction) -> tuple[object, ...]:
    return (
        extraction.document_id,
        extraction.summary,
        json.dumps(extraction.people),
        json.dumps(extraction.organizations),
        json.dumps(extraction.projects),
        json.dumps(extraction.goals),
        json.dumps(extraction.topics),
        json.dumps(extraction.metadata),
    )


def _row_to_extraction(row: tuple[object, ...]) -> StructuredExtraction:
    values = iter(row)
    document_id = str(next(values))
    summary = str(next(values))
    collections = {
        field_name: tuple(json.loads(str(next(values)))) for field_name in _LIST_FIELDS
    }
    metadata = json.loads(str(next(values)))
    return StructuredExtraction(
        document_id=document_id,
        summary=summary,
        metadata=metadata,
        **collections,
    )


class ExtractionStore:
    """Durable structured extractions keyed by their document id.

    One extraction per document: ``document_id`` is the primary key, so
    repeated ingestion of unchanged sources replaces the stored entry
    instead of duplicating it, following the same convention as
    :class:`~personal_ai.storage.documents.DocumentStore`.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_EXTRACTIONS_SCHEMA)
        self._connection.commit()

    def save(self, extraction: StructuredExtraction) -> bool:
        """Store an extraction; returns True if newly inserted, False if updated."""
        cursor = self._connection.execute(_INSERT_SQL, _extraction_to_row(extraction))
        inserted = cursor.rowcount == 1
        if not inserted:
            assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
            self._connection.execute(
                f"UPDATE structured_extractions SET {assignments} "
                f"WHERE document_id = ?",
                (*_extraction_to_row(extraction)[1:], extraction.document_id),
            )
        self._connection.commit()
        return inserted

    def get(self, document_id: str) -> StructuredExtraction | None:
        """Return the extraction for this document id, or None when absent."""
        row = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM structured_extractions "
            "WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return _row_to_extraction(row) if row is not None else None

    def close(self) -> None:
        """Release the underlying database connection."""
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
