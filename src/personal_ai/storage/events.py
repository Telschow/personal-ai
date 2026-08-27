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

# Bucket strategies for time aggregation, mapped to SQLite strftime formats.
# Event timestamps are canonical UTC, so buckets are computed in UTC.
_BUCKET_FORMATS = {
    "day": "%Y-%m-%d",
    "week": "%Y-%W",  # Monday-first week number of the UTC year (00-53).
    "month": "%Y-%m",
}

# Cap for negative/absent limits so a bare "LIMIT -1" (SQLite = unlimited) is
# never produced from an unvalidated caller.
_MAX_RESULTS = 1 << 30

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


@dataclass(frozen=True, slots=True)
class ActivityCount:
    """One (event_type, source) grouping with its event count."""

    event_type: str
    source: str
    count: int


@dataclass(frozen=True, slots=True)
class CountedQuery:
    """A search query value together with how often it was run."""

    query: str
    count: int


@dataclass(frozen=True, slots=True)
class ChannelCount:
    """Watch counts for one channel.

    ``total_duration_seconds`` is the sum of ``duration_seconds`` across the
    group's events, or ``None`` when none of the events carry durations.
    """

    channel_name: str
    count: int
    total_duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class VideoCount:
    """Watch counts for one video, keyed by its stable video id.

    ``video_id`` is read from each event's ``metadata["video_id"]``.
    ``title`` and ``url`` are representative values from the watched video.
    """

    video_id: str | None
    title: str | None
    url: str | None
    count: int


@dataclass(frozen=True, slots=True)
class BucketCount:
    """Event count for one time bucket.

    ``bucket`` is a UTC bucket label such as ``2026-01`` (month) or
    ``2026-01-20`` (day); the exact shape depends on the bucket strategy.
    """

    bucket: str
    count: int


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

    @staticmethod
    def _filter_predicate(
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_type: str | None = None,
    ) -> tuple[str, list[object]]:
        """Build an ``events`` WHERE clause (and params) from standard filters.

        Time bounds are inclusive. Results in ``(where_str, params)`` where
        ``where_str`` is empty when there are no filters.
        """
        clauses: list[str] = []
        params: list[object] = []
        if start_time is not None:
            clauses.append("event_time >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("event_time <= ?")
            params.append(end_time)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    @staticmethod
    def _group_where(
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_types: tuple[str, ...] | None = None,
        not_null: str | None = None,
    ) -> tuple[str, list[object]]:
        """Extend :meth:`_filter_predicate` for grouping queries.

        Applies the standard filters plus an optional ``event_types`` IN set
        and an optional ``not_null`` column requirement. Returns ``(where,
        params)``.
        """
        where, params = EventStore._filter_predicate(
            start_time=start_time, end_time=end_time, source=source
        )
        conditions: list[str] = []
        if not_null is not None:
            conditions.append(f"{not_null} IS NOT NULL")
        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            conditions.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if conditions:
            clause = " AND ".join(conditions)
            where = f" WHERE {clause}" if not where else f"{where} AND {clause}"
        return where, params

    def activity_summary(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_type: str | None = None,
    ) -> tuple[ActivityCount, ...]:
        """Return event counts grouped by event_type and source.

        Ordered by count descending, then event_type and source ascending for
        deterministic tie-breaking. Optional inclusive time bounds and exact
        source/event_type filters restrict the rows counted.
        """
        where, params = self._filter_predicate(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_type=event_type,
        )
        rows = self._connection.execute(
            "SELECT event_type, source, COUNT(*) AS c FROM events"
            f"{where} GROUP BY event_type, source "
            "ORDER BY c DESC, event_type ASC, source ASC",
            params,
        ).fetchall()
        return tuple(
            ActivityCount(event_type=str(r[0]), source=str(r[1]), count=int(r[2]))
            for r in rows
        )

    def top_search_queries(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int = 10,
    ) -> tuple[CountedQuery, ...]:
        """Return the most frequent distinct ``search_query`` values.

        Restricted to rows that carry a search query, optionally limited to a
        set of ``event_types``. Ordered by count descending then query
        ascending. ``limit`` applies to the number of returned queries.
        """
        where, params = self._group_where(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_types=event_types,
            not_null="search_query",
        )
        limit = _MAX_RESULTS if limit < 0 else limit
        rows = self._connection.execute(
            "SELECT search_query AS q, COUNT(*) AS c FROM events"
            f"{where} GROUP BY q ORDER BY c DESC, q ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(CountedQuery(query=str(r[0]), count=int(r[1])) for r in rows)

    def top_channels(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int = 10,
    ) -> tuple[ChannelCount, ...]:
        """Return the most frequently watched channels.

        Restricted to rows that carry a channel name. ``count`` is the number
        of watches; ``total_duration_seconds`` sums only the durations the
        source actually recorded (``None`` when none exist). Ordered by count
        descending then channel name ascending.
        """
        where, params = self._group_where(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_types=event_types,
            not_null="channel_name",
        )
        limit = _MAX_RESULTS if limit < 0 else limit
        rows = self._connection.execute(
            "SELECT channel_name AS ch, COUNT(*) AS c, "
            "SUM(duration_seconds) AS d FROM events"
            f"{where} GROUP BY ch ORDER BY c DESC, ch ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(
            ChannelCount(
                channel_name=str(r[0]),
                count=int(r[1]),
                total_duration_seconds=float(r[2]) if r[2] is not None else None,
            )
            for r in rows
        )

    def top_videos(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int = 10,
    ) -> tuple[VideoCount, ...]:
        """Return the most frequently watched videos.

        Grouped by the stable ``metadata["video_id"]``; events without a video
        id are excluded. ``title`` and ``url`` are representative values from
        the group. Ordered by count descending then video id ascending.
        """
        where, params = self._group_where(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_types=event_types,
            not_null="json_extract(metadata, '$.video_id')",
        )
        limit = _MAX_RESULTS if limit < 0 else limit
        rows = self._connection.execute(
            "SELECT json_extract(metadata, '$.video_id') AS vid, "
            "MAX(title) AS t, MIN(url) AS u, COUNT(*) AS c FROM events"
            f"{where} GROUP BY vid ORDER BY c DESC, vid ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(
            VideoCount(
                video_id=str(r[0]) if r[0] is not None else None,
                title=str(r[1]) if r[1] is not None else None,
                url=str(r[2]) if r[2] is not None else None,
                count=int(r[3]),
            )
            for r in rows
        )

    def activity_by_bucket(
        self,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        source: str | None = None,
        event_type: str | None = None,
        bucket: str = "month",
        limit: int = 100,
    ) -> tuple[BucketCount, ...]:
        """Return event counts grouped by a UTC time bucket.

        ``bucket`` is one of ``day``, ``week``, or ``month``. Bucket labels
        are derived from the canonical UTC event time, so aggregation is
        consistently in UTC. Ordered chronologically by bucket label.
        """
        fmt = _BUCKET_FORMATS.get(bucket)
        if fmt is None:
            msg = f"bucket must be one of: {', '.join(sorted(_BUCKET_FORMATS))}"
            raise ValueError(msg)
        where, params = self._filter_predicate(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_type=event_type,
        )
        limit = _MAX_RESULTS if limit < 0 else limit
        # strftime(?, ...) appears textually before the WHERE placeholders, so
        # the format parameter must be bound first, then the filter params,
        # then the LIMIT.
        rows = self._connection.execute(
            "SELECT strftime(?, event_time) AS label, COUNT(*) AS c "
            "FROM events"
            f"{where} GROUP BY label ORDER BY label ASC LIMIT ?",
            (fmt, *params, limit),
        ).fetchall()
        return tuple(BucketCount(bucket=str(r[0]), count=int(r[1])) for r in rows)
