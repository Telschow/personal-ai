"""SQLite-backed storage for temporal events.

Temporal events are stored separate from documents, chunks, and embeds:
they are retrieved structurally through time-range and event-type
filtering, not through vector or full-text semantic search.
"""

import json
import sqlite3
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from personal_ai.events.models import Event

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    url TEXT,
    search_query TEXT,
    channel_name TEXT,
    duration_seconds REAL,
    metadata TEXT NOT NULL
)
"""

_EVENTS_INDEXES = (
    ("CREATE INDEX IF NOT EXISTS idx_events_time ON events (event_time)"),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_type_time "
        "ON events (event_type, event_time)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_source_time "
        "ON events (source, event_time)"
    ),
)

_COLUMNS = (
    "id",
    "event_type",
    "event_time",
    "source",
    "title",
    "url",
    "search_query",
    "channel_name",
    "duration_seconds",
    "metadata",
)


def _optional_str(value: object) -> str | None:
    """Convert a nullable column value to str or None."""
    return str(value) if value is not None else None


def _event_to_row(event: Event) -> tuple[object, ...]:
    return (
        event.id,
        event.event_type,
        event.event_time,
        event.source,
        event.title,
        event.url,
        event.search_query,
        event.channel_name,
        event.duration_seconds,
        json.dumps(event.metadata),
    )


def _row_to_event(row: tuple[object, ...]) -> Event:
    return Event(
        id=str(row[0]),
        event_type=str(row[1]),
        event_time=str(row[2]),
        source=str(row[3]),
        title=_optional_str(row[4]),
        url=_optional_str(row[5]),
        search_query=_optional_str(row[6]),
        channel_name=_optional_str(row[7]),
        duration_seconds=float(row[8]) if row[8] is not None else None,
        metadata=json.loads(str(row[9])),
    )


@dataclass(frozen=True, slots=True)
class EventQuery:
    """A deterministic, structurally filtered event query.

    All bounds are inclusive ISO-8601 strings. ``source`` and ``event_type``
    filters are optional and must match exactly. Ordering is by event_time
    then id (stable for equal timestamps).
    """

    start_time: str | None = None
    end_time: str | None = None
    source: str | None = None
    event_type: str | None = None
    limit: int = 100


class EventStore:
    """Manages temporal events in SQLite.

    Uses the same idempotent upsert pattern as other stores in this
    project: INSERT OR IGNORE for new rows, conditional UPDATE for changed
    rows, always returning a boolean indicating whether the row was new.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(_EVENTS_SCHEMA)
        for index_sql in _EVENTS_INDEXES:
            self._connection.execute(index_sql)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def save_event(self, event: Event) -> bool:
        """Persist an event, returning True if newly inserted.

        On conflict (same id), updates mutable fields while preserving the
        original id.
        """
        row = _event_to_row(event)
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO events "
            f"({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            row,
        )
        if cursor.rowcount == 1:
            self._connection.commit()
            return True

        self._connection.execute(
            "UPDATE events SET "
            "event_type = ?, event_time = ?, source = ?, title = ?, url = ?, "
            "search_query = ?, channel_name = ?, duration_seconds = ?, metadata = ? "
            "WHERE id = ?",
            (
                event.event_type,
                event.event_time,
                event.source,
                event.title,
                event.url,
                event.search_query,
                event.channel_name,
                event.duration_seconds,
                json.dumps(event.metadata),
                event.id,
            ),
        )
        self._connection.commit()
        return False

    def save_events(self, events: tuple[Event, ...]) -> int:
        """Persist a batch of events, returning the count newly inserted."""
        count = 0
        for event in events:
            if self.save_event(event):
                count += 1
        return count

    def get_event(self, event_id: str) -> Event | None:
        row = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return _row_to_event(row) if row is not None else None

    def count_events(
        self,
        *,
        source: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """Count stored events, optionally filtered by source and/or type."""
        clauses: list[str] = []
        params: list[object] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._connection.execute(
            f"SELECT COUNT(*) FROM events{where}", params
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def list_events(
        self,
        *,
        source: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> tuple[Event, ...]:
        """Return stored events in deterministic (time, id) order.

        Optionally filtered by exact ``source`` and/or ``event_type``.
        """
        clauses: list[str] = []
        params: list[object] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM events{where} "
            "ORDER BY event_time, id "
            "LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(_row_to_event(r) for r in rows)

    def search(self, query: EventQuery) -> tuple[Event, ...]:
        """Run a deterministic structurally-filtered event query.

        Supports inclusive time-range bounds, an optional exact source
        filter, and an optional exact event_type filter. Ordering is by
        event_time then id. Only deterministic SQL with bound parameters is
        used, so callers can never influence the query beyond the typed
        filters.
        """
        clauses: list[str] = []
        params: list[object] = []
        if query.start_time is not None:
            clauses.append("event_time >= ?")
            params.append(query.start_time)
        if query.end_time is not None:
            clauses.append("event_time <= ?")
            params.append(query.end_time)
        if query.source is not None:
            clauses.append("source = ?")
            params.append(query.source)
        if query.event_type is not None:
            clauses.append("event_type = ?")
            params.append(query.event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM events{where} "
            "ORDER BY event_time, id "
            "LIMIT ?",
            (*params, query.limit),
        ).fetchall()
        return tuple(_row_to_event(r) for r in rows)
