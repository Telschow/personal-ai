"""SQLite-backed storage for ingested documents."""

import json
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from personal_ai.documents.models import Document

_DOCUMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    path TEXT,
    filename TEXT,
    mime_type TEXT,
    metadata TEXT NOT NULL
)
"""

_COLUMNS = (
    "id",
    "source",
    "source_type",
    "content_hash",
    "created_at",
    "modified_at",
    "path",
    "filename",
    "mime_type",
    "metadata",
)

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO documents ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)


def connect_database(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for personal-AI storage."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _document_to_row(document: Document) -> tuple[object, ...]:
    return (
        document.id,
        document.source,
        document.source_type,
        document.content_hash,
        document.created_at,
        document.modified_at,
        document.path,
        document.filename,
        document.mime_type,
        json.dumps(document.metadata),
    )


def _row_to_document(row: tuple[object, ...]) -> Document:
    return Document(
        id=str(row[0]),
        source=str(row[1]),
        source_type=str(row[2]),
        content_hash=str(row[3]),
        created_at=str(row[4]),
        modified_at=str(row[5]),
        path=row[6] if row[6] is None else str(row[6]),
        filename=row[7] if row[7] is None else str(row[7]),
        mime_type=row[8] if row[8] is None else str(row[8]),
        metadata=json.loads(str(row[9])),
    )


def _select_sql(where: str) -> str:
    return f"SELECT {', '.join(_COLUMNS)} FROM documents{where}"


class DocumentStore:
    """Durable document storage keyed by stable document id.

    Re-adding a document whose id already exists updates the stored entry
    but keeps the original ``created_at`` timestamp, so repeated ingestion
    of unchanged sources never duplicates or loses first-seen information.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_DOCUMENTS_SCHEMA)
        self._connection.commit()

    def add(self, document: Document) -> bool:
        """Store a document; returns True if newly inserted, False if updated."""
        cursor = self._connection.execute(_INSERT_SQL, _document_to_row(document))
        inserted = cursor.rowcount == 1
        if not inserted:
            self._connection.execute(
                """
                UPDATE documents SET
                    source = ?,
                    source_type = ?,
                    content_hash = ?,
                    modified_at = ?,
                    path = ?,
                    filename = ?,
                    mime_type = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (
                    document.source,
                    document.source_type,
                    document.content_hash,
                    document.modified_at,
                    document.path,
                    document.filename,
                    document.mime_type,
                    json.dumps(document.metadata),
                    document.id,
                ),
            )
        self._connection.commit()
        return inserted

    def get(self, document_id: str) -> Document | None:
        """Return the document with this id, or None when absent."""
        row = self._connection.execute(
            _select_sql(" WHERE id = ?"), (document_id,)
        ).fetchone()
        return _row_to_document(row) if row is not None else None

    def list_documents(self) -> list[Document]:
        """Return all documents in deterministic created-at order."""
        rows = self._connection.execute(
            _select_sql(" ORDER BY created_at, id")
        ).fetchall()
        return [_row_to_document(row) for row in rows]

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
