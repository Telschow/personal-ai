"""SQLite-backed storage for chunk embeddings."""

import json
import sqlite3
from collections.abc import Iterable
from types import TracebackType
from typing import Self

from personal_ai.documents.embedding import (
    Embedding,
    MalformedEmbeddingError,
    parse_embedding,
)

_EMBEDDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    vector TEXT NOT NULL,
    dimensions INTEGER NOT NULL
)
"""

_COLUMNS = ("chunk_id", "model", "vector", "dimensions")

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO chunk_embeddings ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)


def _validated_embedding(embedding: Embedding) -> Embedding:
    """Revalidate through the domain contract before anything is stored.

    ``Embedding`` instances can be constructed directly with values that
    the contract forbids (empty, non-numeric, or non-finite components);
    storage must never persist them.
    """
    return parse_embedding(embedding.model, [embedding.vector])


def _embedding_to_row(embedding: Embedding, chunk_id: str) -> tuple[object, ...]:
    return (
        chunk_id,
        embedding.model,
        json.dumps(list(embedding.vector)),
        embedding.dimensions,
    )


def _row_to_embedding(chunk_id: str, row: tuple[object, ...]) -> Embedding:
    """Decode one stored row, refusing to repair or fabricate values."""
    model = str(row[1])
    try:
        components = json.loads(str(row[2]))
    except ValueError as exc:
        msg = f"Stored embedding for chunk {chunk_id!r} contains invalid JSON"
        raise ValueError(msg) from exc

    try:
        embedding = parse_embedding(model, [components])
    except MalformedEmbeddingError as exc:
        msg = f"Stored embedding for chunk {chunk_id!r} is malformed"
        raise ValueError(msg) from exc

    declared_dimensions = row[3]
    if embedding.dimensions != declared_dimensions:
        msg = (
            f"Stored embedding for chunk {chunk_id!r} declares "
            f"{declared_dimensions} dimensions but decodes to "
            f"{embedding.dimensions}"
        )
        raise ValueError(msg)
    return embedding


class EmbeddingStore:
    """Durable chunk embeddings keyed by their existing chunk id.

    Identity comes exclusively from the caller; this store never derives,
    replaces, or generates ids. The model name always travels with the
    vector, and saving under an existing chunk id replaces the prior row
    (including across models), following the insert-or-update convention
    of :class:`~personal_ai.storage.chunks.ChunkStore`.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_EMBEDDINGS_SCHEMA)
        self._connection.commit()

    def add(self, embedding: Embedding, chunk_id: str) -> bool:
        """Store an embedding; returns True if newly inserted, False if updated."""
        validated = _validated_embedding(embedding)
        cursor = self._connection.execute(
            _INSERT_SQL, _embedding_to_row(validated, chunk_id)
        )
        inserted = cursor.rowcount == 1
        if not inserted:
            assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
            self._connection.execute(
                f"UPDATE chunk_embeddings SET {assignments} WHERE chunk_id = ?",
                (*_embedding_to_row(validated, chunk_id)[1:], chunk_id),
            )
        self._connection.commit()
        return inserted

    def add_many(self, embeddings: Iterable[tuple[Embedding, str]]) -> int:
        """Store ``(embedding, chunk_id)`` pairs in one transaction.

        Returns the number newly inserted; an existing chunk id is updated
        with the new vector, exactly as in :meth:`add`. Either the whole
        batch persists or, on error, the transaction rolls back and the
        exception propagates, leaving the store unchanged.
        """
        inserted = 0
        try:
            for embedding, chunk_id in embeddings:
                validated = _validated_embedding(embedding)
                cursor = self._connection.execute(
                    _INSERT_SQL, _embedding_to_row(validated, chunk_id)
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
                    self._connection.execute(
                        f"UPDATE chunk_embeddings SET {assignments} WHERE chunk_id = ?",
                        (*_embedding_to_row(validated, chunk_id)[1:], chunk_id),
                    )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return inserted

    def count(self, model: str) -> int:
        """Return the number of stored embeddings produced by this model."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE model = ?", (model,)
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def get(self, chunk_id: str) -> Embedding | None:
        """Return the embedding for this chunk id, or None when absent."""
        row = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM chunk_embeddings WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        return _row_to_embedding(chunk_id, row) if row is not None else None

    def list_for_chunks(self, chunk_ids: Iterable[str]) -> dict[str, Embedding]:
        """Return stored embeddings for the given chunk ids in one batched read.

        Chunk ids without a stored embedding are simply absent from the
        result; the returned mapping preserves input order. Each row passes
        the same strict validation as :meth:`get`, so corrupt or
        self-inconsistent vectors raise instead of being silently reused.
        """
        ids = tuple(dict.fromkeys(chunk_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" * len(ids))
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM chunk_embeddings "
            f"WHERE chunk_id IN ({placeholders})",
            ids,
        ).fetchall()
        found = {str(row[0]): _row_to_embedding(str(row[0]), row) for row in rows}
        return {chunk_id: found[chunk_id] for chunk_id in ids if chunk_id in found}

    def delete(self, chunk_id: str) -> bool:
        """Remove one embedding; returns True if a row was deleted."""
        cursor = self._connection.execute(
            "DELETE FROM chunk_embeddings WHERE chunk_id = ?", (chunk_id,)
        )
        self._connection.commit()
        return cursor.rowcount == 1

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
