"""SQLite-backed storage for page-level vision extraction results.

``vision_pages`` is a derived table keyed by ``(document_id, page_number)``
exactly like the ``documents`` columns it annotates, and it is always
re-derivable: page text depends only on the page image, the vision model,
and the prompt version recorded alongside it. Storing the model and prompt
version lets cache lookups reject rows produced by a different derivation
instead of reusing text the current configuration would not produce.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

_VISION_PAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_pages (
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    vision_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, page_number)
)
"""

_COLUMNS = (
    "document_id",
    "page_number",
    "text",
    "vision_model",
    "prompt_version",
    "created_at",
)

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO vision_pages ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)


@dataclass(frozen=True, slots=True)
class StoredVisionPage:
    """A cached page-level vision extraction and its derivation identity."""

    text: str
    vision_model: str
    prompt_version: str
    created_at: str


def _row_to_page(row: tuple[object, ...]) -> StoredVisionPage:
    return StoredVisionPage(
        text=str(row[2]),
        vision_model=str(row[3]),
        prompt_version=str(row[4]),
        created_at=str(row[5]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class VisionStore:
    """Durable per-page vision text keyed by document id and page number.

    Saving under an existing ``(document_id, page_number)`` replaces the
    prior row following the insert-or-update convention used across this
    project's stores, so changed vision derivations overwrite stale text
    instead of accumulating duplicates. Decision-making about whether a
    stored page matches the current vision model and prompt version is the
    caller's (that is the cache-key contract); this store only persists.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.execute(_VISION_PAGES_SCHEMA)
        self._connection.commit()

    def get(self, document_id: str, page_number: int) -> StoredVisionPage | None:
        """Return the cached vision text for this page, or None when absent."""
        row = self._connection.execute(
            "SELECT document_id, page_number, text, vision_model, "
            "prompt_version, created_at FROM vision_pages "
            "WHERE document_id = ? AND page_number = ?",
            (document_id, page_number),
        ).fetchone()
        return _row_to_page(row) if row is not None else None

    def save(
        self,
        document_id: str,
        page_number: int,
        text: str,
        vision_model: str,
        prompt_version: str,
    ) -> bool:
        """Persist one page's vision text; True if newly inserted."""
        created_at = _utc_now()
        row = (
            document_id,
            page_number,
            text,
            vision_model,
            prompt_version,
            created_at,
        )
        cursor = self._connection.execute(_INSERT_SQL, row)
        inserted = cursor.rowcount == 1
        if not inserted:
            self._connection.execute(
                "UPDATE vision_pages SET text = ?, vision_model = ?, "
                "prompt_version = ?, created_at = ? "
                "WHERE document_id = ? AND page_number = ?",
                (
                    text,
                    vision_model,
                    prompt_version,
                    created_at,
                    document_id,
                    page_number,
                ),
            )
        self._connection.commit()
        return inserted

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
