"""SQLite-backed storage for structured extraction results."""

import json
import sqlite3
from dataclasses import dataclass
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

# Fields searched by the extraction search, in priority order.
_SEARCHABLE_LIST_FIELDS = ("people", "organizations", "projects", "goals", "topics")


@dataclass(frozen=True, slots=True)
class ExtractionSearchResult:
    """A single hit from structured extraction search.

    ``score`` is a deterministic relevance measure in [0, 1].  Higher is
    better.  ``matched_fields`` records which extraction fields contained
    the query so callers can display provenance without re-searching.
    """

    document_id: str
    summary: str
    score: float
    matched_fields: tuple[str, ...]


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


def _field_score(field_name: str, values: tuple[str, ...], query_lower: str) -> float:
    """Return the best match score for a single collection field."""
    best = 0.0
    for value in values:
        vl = value.lower()
        if vl == query_lower:
            return 1.0
        if vl.startswith(query_lower):
            best = max(best, 0.7)
        elif query_lower in vl:
            best = max(best, 0.4)
    return best


def _score_extraction(
    query: str, extraction: StructuredExtraction
) -> tuple[float, tuple[str, ...]]:
    """Compute a relevance score and matched fields for one extraction.

    Returns (score, matched_field_names).  Score is in (0, 1].
    """
    query_lower = query.lower().strip()
    total = 0.0
    matched: list[str] = []

    # Score summary
    summary_lower = extraction.summary.lower()
    if summary_lower == query_lower:
        total += 1.0
        matched.append("summary")
    elif query_lower in summary_lower:
        total += 0.4
        matched.append("summary")

    # Score collection fields
    for field_name in _SEARCHABLE_LIST_FIELDS:
        values = getattr(extraction, field_name)
        score = _field_score(field_name, values, query_lower)
        if score > 0:
            total += score
            matched.append(field_name)

    # Normalize: max possible is 1.0 (summary) + 5 * 1.0 (fields) = 6.0
    max_score = 1.0 + len(_SEARCHABLE_LIST_FIELDS)
    normalized = total / max_score if total > 0 else 0.0
    return normalized, tuple(matched)


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

    def search(
        self, query: str, *, limit: int = 10
    ) -> tuple[ExtractionSearchResult, ...]:
        """Search structured extractions by case-insensitive substring match.

        Searches across summary and all collection fields (people,
        organizations, projects, goals, topics).  Results are scored by
        match quality and number of matched fields, then returned in
        descending score order.  Ties are broken deterministically by
        document_id.

        Scoring (per field, in [0, 1]):
            exact field value match:    1.0
            starts with query:         0.7
            contains query:            0.4

        Total score = sum of per-field scores / number of searched fields.
        This means a document matching in 2 fields scores higher than one
        matching in 1, and summary matches are weighted equally to field
        matches.
        """
        if not isinstance(query, str) or not query.strip():
            return ()

        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM structured_extractions"
        ).fetchall()

        results: list[ExtractionSearchResult] = []
        for row in rows:
            extraction = _row_to_extraction(row)
            score, matched = _score_extraction(query, extraction)
            if score > 0:
                results.append(
                    ExtractionSearchResult(
                        document_id=extraction.document_id,
                        summary=extraction.summary,
                        score=score,
                        matched_fields=matched,
                    )
                )

        results.sort(key=lambda r: (-r.score, r.document_id))
        return tuple(results[:limit])

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
