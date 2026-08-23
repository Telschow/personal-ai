"""SQLite-backed storage for document chunks."""

import json
import sqlite3
from collections.abc import Iterable
from types import TracebackType
from typing import Self

from personal_ai.documents.models import DocumentChunk

_CHUNKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER,
    page_number INTEGER,
    text TEXT NOT NULL,
    metadata TEXT NOT NULL
)
"""

_CHUNKS_DOCUMENT_ORDER_INDEX = """
CREATE INDEX IF NOT EXISTS document_chunks_document_order
ON document_chunks (document_id, chunk_index, chunk_id)
"""

_COLUMNS = (
    "chunk_id",
    "document_id",
    "chunk_index",
    "page_number",
    "text",
    "metadata",
)

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO document_chunks ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)

_SELECT_SQL = f"SELECT {', '.join(_COLUMNS)} FROM document_chunks"


def _order_index(chunk: DocumentChunk) -> int | None:
    """Project the ordering position from metadata for indexed retrieval.

    Only genuine integers qualify (``bool`` is excluded); anything else
    leaves the column ``NULL`` and ordering falls back to chunk id. The
    metadata itself always round-trips verbatim regardless.
    """
    value = chunk.metadata.get("chunk_index")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _chunk_to_row(chunk: DocumentChunk) -> tuple[object, ...]:
    return (
        chunk.id,
        chunk.document_id,
        _order_index(chunk),
        chunk.page_number,
        chunk.text,
        json.dumps(chunk.metadata),
    )


def _row_to_chunk(row: tuple[object, ...]) -> DocumentChunk:
    return DocumentChunk(
        id=str(row[0]),
        document_id=str(row[1]),
        page_number=row[3] if row[3] is None else int(row[3]),
        text=str(row[4]),
        metadata=json.loads(str(row[5])),
    )


class ChunkStore:
    """Durable searchable chunks keyed by their content-addressed chunk id.

    Identity comes exclusively from ``compute_chunk_id`` and is never
    generated here, so re-saving unchanged chunks neither duplicates rows
    nor changes observable state, following the insert-or-update convention
    of :class:`~personal_ai.storage.documents.DocumentStore`. Chunks are
    listed per document in ``chunk_index`` order with chunk id as the
    deterministic tie-break, independent of incidental row order.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_CHUNKS_SCHEMA)
        self._connection.execute(_CHUNKS_DOCUMENT_ORDER_INDEX)
        self._connection.commit()

    def _insert_or_update(self, chunk: DocumentChunk) -> bool:
        """Insert the chunk, or overwrite it when its id already exists."""
        cursor = self._connection.execute(_INSERT_SQL, _chunk_to_row(chunk))
        if cursor.rowcount == 1:
            return True
        assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
        self._connection.execute(
            f"UPDATE document_chunks SET {assignments} WHERE chunk_id = ?",
            (*_chunk_to_row(chunk)[1:], chunk.id),
        )
        return False

    def add(self, chunk: DocumentChunk) -> bool:
        """Store a chunk; returns True if newly inserted, False if updated."""
        inserted = self._insert_or_update(chunk)
        self._connection.commit()
        return inserted

    def add_many(self, chunks: Iterable[DocumentChunk]) -> int:
        """Store chunks in one transaction; returns the number newly inserted.

        Either every chunk is persisted or, on error, the transaction is
        rolled back and the exception propagates, leaving the store
        unchanged.
        """
        inserted = 0
        try:
            for chunk in chunks:
                if self._insert_or_update(chunk):
                    inserted += 1
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return inserted

    def get(self, chunk_id: str) -> DocumentChunk | None:
        """Return the chunk with this id, or None when absent."""
        row = self._connection.execute(
            f"{_SELECT_SQL} WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        return _row_to_chunk(row) if row is not None else None

    def list_for_document(self, document_id: str) -> tuple[DocumentChunk, ...]:
        """Return one document's chunks in deterministic chunk_index order."""
        rows = self._connection.execute(
            f"{_SELECT_SQL} WHERE document_id = ? ORDER BY chunk_index, chunk_id",
            (document_id,),
        ).fetchall()
        return tuple(_row_to_chunk(row) for row in rows)

    def delete_for_document(self, document_id: str) -> int:
        """Remove all chunks of one document; returns the number deleted."""
        cursor = self._connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
        )
        self._connection.commit()
        return cursor.rowcount

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
