"""SQLite-backed storage for document chunks."""

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
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

# Derived, rebuildable full-text index over the authoritative chunks. Only
# the searchable text and the join key back to ``document_chunks`` live here;
# every other field of a search result is read from the authoritative table.
_CHUNKS_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
    text,
    chunk_id UNINDEXED
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

_FTS_INSERT_SQL = "INSERT INTO document_chunks_fts (chunk_id, text) VALUES (?, ?)"

_SEARCH_DEFAULT_LIMIT = 10
DEFAULT_SEARCH_LIMIT = _SEARCH_DEFAULT_LIMIT

_SEARCH_SQL = """
SELECT
    document_chunks.chunk_id,
    document_chunks.document_id,
    document_chunks.chunk_index,
    document_chunks.text,
    bm25(document_chunks_fts) AS rank
FROM document_chunks_fts
JOIN document_chunks ON document_chunks.chunk_id = document_chunks_fts.chunk_id
WHERE document_chunks_fts MATCH ?
ORDER BY rank, document_chunks.chunk_id
LIMIT ?
"""

_FILTERED_SEARCH_SQL_TEMPLATE = """
SELECT
    document_chunks.chunk_id,
    document_chunks.document_id,
    document_chunks.chunk_index,
    document_chunks.text,
    bm25(document_chunks_fts) AS rank
FROM document_chunks_fts
JOIN document_chunks ON document_chunks.chunk_id = document_chunks_fts.chunk_id
JOIN documents ON documents.id = document_chunks.document_id
WHERE document_chunks_fts MATCH ?{constraints}
ORDER BY rank, document_chunks.chunk_id
LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class ChunkSearchResult:
    """One keyword-search hit, projected from the authoritative chunk row.

    ``source_type`` and ``source`` carry the owning document's provenance,
    resolved in one batched lookup after the rank query (see
    :func:`_load_document_provenance`). Orphaned chunks and standalone
    chunk stores without a ``documents`` table report ``None`` provenance.
    """

    chunk_id: str
    document_id: str
    chunk_index: int | None
    text: str
    rank: float
    source_type: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentFilter:
    """Explicit constraints on authoritative document metadata.

    Every field is optional; a filter with no fields set matches every
    document exactly like no filter at all. ``source_types`` and
    ``mime_types`` are explicit inclusion lists. The MIME filter reads the
    authoritative ``documents.metadata`` record (where source adapters
    store ``mime_type``); documents without a MIME type never match. Date
    boundaries are inclusive and compared lexically
    against the stored ISO-8601 timestamps, so boundaries written in the
    same format as the documents compare naturally; date-only strings act
    as inclusive whole-day bounds. Documents whose timestamp is unknown
    (stored empty) never match a date-bounded window.

    Invalid values fail loudly at construction instead of silently
    narrowing results: unparsable boundaries, inverted ranges, mixed
    offset-naive/aware boundary pairs, and empty or blank source or MIME
    types are all rejected.
    """

    source_types: tuple[str, ...] | None = None
    mime_types: tuple[str, ...] | None = None
    created_after: str | None = None
    created_before: str | None = None
    modified_after: str | None = None
    modified_before: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_types",
            _validate_type_list("source_types", self.source_types),
        )
        object.__setattr__(
            self,
            "mime_types",
            _validate_type_list("mime_types", self.mime_types),
        )
        _validate_boundary("created_after", self.created_after)
        _validate_boundary("created_before", self.created_before)
        _validate_boundary("modified_after", self.modified_after)
        _validate_boundary("modified_before", self.modified_before)
        _validate_range("created", self.created_after, self.created_before)
        _validate_range("modified", self.modified_after, self.modified_before)

    @property
    def is_empty(self) -> bool:
        """True when no field constrains the search."""
        return (
            self.source_types is None
            and self.mime_types is None
            and self.created_after is None
            and self.created_before is None
            and self.modified_after is None
            and self.modified_before is None
        )


def _validate_type_list(
    field: str, value: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Validate an inclusion-list filter field (source/MIME types)."""
    if value is None:
        return None
    entries = tuple(value)
    if not entries:
        msg = f"{field} must be None or non-empty"
        raise ValueError(msg)
    if any(not isinstance(entry, str) or not entry for entry in entries):
        msg = f"{field} entries must be non-empty strings"
        raise ValueError(msg)
    return entries


def _parse_boundary(field: str, value: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{field} must be an ISO-8601 date or timestamp, got {value!r}"
        raise ValueError(msg) from exc


def _validate_boundary(field: str, value: str | None) -> None:
    if value is not None:
        _parse_boundary(field, value)


def _validate_range(name: str, after: str | None, before: str | None) -> None:
    if after is None or before is None:
        return
    try:
        inverted = _parse_boundary(f"{name}_after", after) > _parse_boundary(
            f"{name}_before", before
        )
    except TypeError as exc:
        msg = f"{name} boundaries mix offset-naive and offset-aware timestamps"
        raise ValueError(msg) from exc
    if inverted:
        msg = f"{name}_after must not be later than {name}_before"
        raise ValueError(msg)


def _document_constraints(
    filters: DocumentFilter,
) -> tuple[str, list[object]]:
    """Translate a non-empty filter into a WHERE fragment plus parameters."""
    clauses: list[str] = []
    parameters: list[object] = []
    if filters.source_types is not None:
        placeholders = ", ".join("?" * len(filters.source_types))
        clauses.append(f"documents.source_type IN ({placeholders})")
        parameters.extend(filters.source_types)
    if filters.mime_types is not None:
        placeholders = ", ".join("?" * len(filters.mime_types))
        clauses.append(
            "json_extract(documents.metadata, '$.mime_type') IN (" + placeholders + ")"
        )
        parameters.extend(filters.mime_types)
    for column, after_field, before_field in (
        ("created_at", "created_after", "created_before"),
        ("modified_at", "modified_after", "modified_before"),
    ):
        after = getattr(filters, after_field)
        before = getattr(filters, before_field)
        if after is not None or before is not None:
            clauses.append(f"documents.{column} <> ''")
        if after is not None:
            clauses.append(f"documents.{column} >= ?")
            parameters.append(after)
        if before is not None:
            clauses.append(f"documents.{column} <= ?")
            parameters.append(before)
    fragment = f" AND {' AND '.join(clauses)}" if clauses else ""
    return fragment, parameters


def _match_expression(query: str) -> str | None:
    """Turn free user text into a safe FTS5 MATCH expression.

    Each whitespace-separated term is quoted as a literal phrase and the
    terms are AND-ed (FTS5's implicit default), so punctuation, operators,
    and column filters in personal content can never alter query syntax.
    Returns None for queries without any terms.
    """
    terms = query.split()
    if not terms:
        return None
    return " ".join(f'"{term.replace('"', '""')}"' for term in terms)


def _document_provenance(
    connection: sqlite3.Connection, rows: list[tuple[object, ...]]
) -> tuple[tuple[str | None, str | None], ...]:
    """Resolve owning-document provenance for search hit rows in one query.

    Returns one ``(source_type, source)`` pair per input row, aligned with
    ``rows`` by order. The lookup is batched by document id (never one query
    per hit). Documents without a row in the ``documents`` table (orphaned
    chunks) and standalone chunk stores that never created that table both
    yield ``None`` provenance.
    """
    if not rows:
        return ()
    has_documents_table = (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
        is not None
    )
    if not has_documents_table:
        return ((None, None),) * len(rows)
    document_ids = {row[1] for row in rows}
    placeholders = ", ".join("?" * len(document_ids))
    fetched = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT id, source_type, source FROM documents "
            f"WHERE id IN ({placeholders})",
            tuple(document_ids),
        ).fetchall()
    }
    return tuple(fetched.get(row[1], (None, None)) for row in rows)


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

    Keyword search runs over a derived SQLite FTS5 index that this store
    keeps consistent with ``document_chunks`` inside the same transactions
    as every mutation. The index is rebuildable at any time via
    :meth:`rebuild_search_index`.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_CHUNKS_SCHEMA)
        self._connection.execute(_CHUNKS_DOCUMENT_ORDER_INDEX)
        self._connection.execute(_CHUNKS_FTS_SCHEMA)
        self._connection.commit()

    def _insert_or_update(self, chunk: DocumentChunk) -> bool:
        """Insert the chunk, or overwrite it when its id already exists."""
        cursor = self._connection.execute(_INSERT_SQL, _chunk_to_row(chunk))
        if cursor.rowcount == 1:
            self._connection.execute(_FTS_INSERT_SQL, (chunk.id, chunk.text))
            return True
        assignments = ", ".join(f"{column} = ?" for column in _COLUMNS[1:])
        self._connection.execute(
            f"UPDATE document_chunks SET {assignments} WHERE chunk_id = ?",
            (*_chunk_to_row(chunk)[1:], chunk.id),
        )
        # Refresh the index entry even when only non-text columns changed,
        # so no update path can leave stale terms behind.
        self._connection.execute(
            "DELETE FROM document_chunks_fts WHERE chunk_id = ?", (chunk.id,)
        )
        self._connection.execute(_FTS_INSERT_SQL, (chunk.id, chunk.text))
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
        self._connection.execute(
            """
            DELETE FROM document_chunks_fts WHERE chunk_id IN (
                SELECT chunk_id FROM document_chunks WHERE document_id = ?
            )
            """,
            (document_id,),
        )
        cursor = self._connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
        )
        self._connection.commit()
        return cursor.rowcount

    def search(
        self,
        query: str,
        limit: int = _SEARCH_DEFAULT_LIMIT,
        filters: DocumentFilter | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """Return keyword-search hits over stored chunks, best-ranked first.

        Ranking uses FTS5's native BM25; equal ranks are ordered by chunk id,
        keeping results deterministic. Queries are treated as literal
        keyword terms, never as FTS5 query syntax. Empty or whitespace-only
        queries return no results.

        ``filters`` optionally constrains hits by authoritative document
        metadata (see :class:`DocumentFilter`); the limit applies after
        filtering. Passing None or an empty filter is exactly equivalent to
        unfiltered search.
        """
        if limit < 0:
            msg = f"Search limit must be non-negative, got {limit}"
            raise ValueError(msg)
        expression = _match_expression(query)
        if expression is None:
            return ()
        if filters is None or filters.is_empty:
            rows = self._connection.execute(_SEARCH_SQL, (expression, limit)).fetchall()
        else:
            constraints, parameters = _document_constraints(filters)
            sql = _FILTERED_SEARCH_SQL_TEMPLATE.format(constraints=constraints)
            rows = self._connection.execute(
                sql, (expression, *parameters, limit)
            ).fetchall()
        provenance = _document_provenance(self._connection, rows)
        return tuple(
            ChunkSearchResult(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                chunk_index=row[2] if row[2] is None else int(row[2]),
                text=str(row[3]),
                rank=float(row[4]),
                source_type=provenance[index][0],
                source=provenance[index][1],
            )
            for index, row in enumerate(rows)
        )

    def rebuild_search_index(self) -> int:
        """Rebuild the derived full-text index from authoritative chunks.

        Drops and re-populates the entire FTS index from the current rows in
        ``document_chunks``, returning the number of indexed chunks. This is
        the recovery path for databases written before the index existed.
        """
        self._connection.execute("DELETE FROM document_chunks_fts")
        rows = self._connection.execute(
            "SELECT chunk_id, text FROM document_chunks ORDER BY chunk_id"
        ).fetchall()
        self._connection.executemany(
            _FTS_INSERT_SQL, ((str(row[0]), str(row[1])) for row in rows)
        )
        self._connection.commit()
        return len(rows)

    def count(self) -> int:
        """Return the number of indexed chunks currently stored."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM document_chunks"
        ).fetchone()
        return int(row[0]) if row is not None else 0

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
