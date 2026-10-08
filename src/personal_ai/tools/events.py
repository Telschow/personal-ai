"""Agent tool exposing structural temporal-event queries.

Temporal events (browser visits, searches, watched videos) are retrieved
deterministically through time-range and type/source filters — never through
semantic or vector search. This tool gives the agent a narrowly scoped way to
answer temporal questions such as "what was I searching for in January 2026?"
or aggregate questions such as "which channels did I watch most often this
year?".

The tool dispatches on a single ``operation`` argument:

- ``events`` — detailed event rows (the original behavior).
- ``activity_summary`` — counts grouped by event_type and source.
- ``top_searches`` — most frequent search queries.
- ``top_channels`` — most watched channels.
- ``top_videos`` — most watched videos.
- ``activity_by_bucket`` — activity counts per UTC time bucket.

Aggregation operations return a concise structured result
(``{"operation": ..., "results": [...]}``) rather than raw rows.

Every operation accepts an optional ``keyword`` — a literal, case-insensitive
substring filter applied to each event's title, url, search_query, and
channel_name. ``%`` and ``_`` in the keyword are matched literally. It is a
deterministic SQL filter, never semantic or embedding search.
"""

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
)
from personal_ai.retrieval import (
    ActivityBucketsRequest,
    ActivitySummaryRequest,
    ChannelTrendsRequest,
    EventQueryRequest,
    SearchTrendsRequest,
    VideoTrendsRequest,
    activity_by_bucket,
    activity_summary,
    query_events,
    top_channels,
    top_searches,
    top_videos,
)
from personal_ai.storage.events import EventStore

_COMMON_ARGUMENT_KEYS = frozenset({"operation"})

_BASE_ARGUMENT_KEYS = _COMMON_ARGUMENT_KEYS | frozenset(
    {"start_time", "end_time", "event_type", "source", "keyword", "limit"}
)

_OPERATION_ARGUMENT_KEYS: dict[str, frozenset[str]] = {
    "events": _BASE_ARGUMENT_KEYS,
    "activity_summary": _BASE_ARGUMENT_KEYS,
    "top_searches": _BASE_ARGUMENT_KEYS,
    "top_channels": _BASE_ARGUMENT_KEYS,
    "top_videos": _BASE_ARGUMENT_KEYS,
    "activity_by_bucket": _BASE_ARGUMENT_KEYS | frozenset({"bucket"}),
}

_VALID_OPERATIONS = frozenset(_OPERATION_ARGUMENT_KEYS)

_VALID_EVENT_TYPES = frozenset(
    {
        EVENT_TYPE_SEARCH_QUERY,
        EVENT_TYPE_URL_VISIT,
        EVENT_TYPE_VIDEO_WATCH,
        EVENT_TYPE_YOUTUBE_SEARCH,
    }
)

_SEARCH_EVENT_TYPES = frozenset({EVENT_TYPE_SEARCH_QUERY, EVENT_TYPE_YOUTUBE_SEARCH})

_VIDEO_EVENT_TYPES = frozenset({EVENT_TYPE_VIDEO_WATCH})

_VALID_BUCKETS = frozenset({"day", "week", "month"})


def _parse_optional_str(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value.strip() or None


def _parse_limit(arguments: dict[str, object], default: int = 100) -> int:
    limit = arguments.get("limit", default)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    return limit


def _parse_keyword(arguments: dict[str, object]) -> str | None:
    """Parse an optional literal keyword substring filter.

    ``keyword`` must be a string when supplied; whitespace-only or empty
    values are treated as absent (no filtering), matching ``_parse_optional_str``
    semantics.
    """
    keyword = arguments.get("keyword")
    if keyword is None:
        return None
    if not isinstance(keyword, str):
        raise TypeError("keyword must be a string")
    return keyword.strip() or None


def _validate_event_type(
    arguments: dict[str, object],
    allowed: frozenset[str] = _VALID_EVENT_TYPES,
) -> str | None:
    event_type = _parse_optional_str(arguments, "event_type")
    if event_type is not None and event_type not in allowed:
        allowed_str = ", ".join(sorted(allowed))
        msg = f"event_type must be one of: {allowed_str}"
        raise ValueError(msg)
    return event_type


def _format_event(event: Event) -> dict[str, object]:
    """Format one event for the model with concise provenance."""
    formatted: dict[str, object] = {
        "event_type": event.event_type,
        "event_time": event.event_time,
        "title": event.title,
        "url": event.url,
        "search_query": event.search_query,
        "channel_name": event.channel_name,
        "duration_seconds": event.duration_seconds,
        "source": event.source,
    }
    video_id = event.metadata.get("video_id")
    if isinstance(video_id, str):
        formatted["video_id"] = video_id
    return formatted


class EventQueryTool:
    """Structural temporal-event query over the persisted event store.

    Accepts only explicitly allow-listed JSON arguments and converts them
    into typed requests; the model can never influence SQL beyond validated
    time, type, source, operation, and bucket filters.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    def query_events(self, arguments: dict[str, object]) -> object:
        """Run one structural or aggregation event query."""
        operation = arguments.get("operation", "events")
        if not isinstance(operation, str):
            raise TypeError("operation must be a string")
        operation = operation.strip() or "events"
        if operation not in _VALID_OPERATIONS:
            allowed = ", ".join(sorted(_VALID_OPERATIONS))
            msg = f"operation must be one of: {allowed}"
            raise ValueError(msg)

        allowed_keys = _OPERATION_ARGUMENT_KEYS[operation]
        unknown_keys = sorted(set(arguments) - set(allowed_keys))
        if unknown_keys:
            msg = f"unsupported arguments: {', '.join(unknown_keys)}"
            raise ValueError(msg)

        handler = _OPERATION_HANDLERS[operation]
        return handler(self, arguments)

    def _operation_events(self, arguments: dict[str, object]) -> object:
        event_type = _validate_event_type(arguments)
        request = EventQueryRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=event_type,
            keyword=_parse_keyword(arguments),
            limit=_parse_limit(arguments),
        )
        events = query_events(self._event_store, request)
        return [_format_event(event) for event in events]

    def _operation_activity_summary(self, arguments: dict[str, object]) -> object:
        request = ActivitySummaryRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=_validate_event_type(arguments),
            keyword=_parse_keyword(arguments),
        )
        results = activity_summary(self._event_store, request)
        return {
            "operation": "activity_summary",
            "results": [
                {
                    "event_type": r.event_type,
                    "source": r.source,
                    "count": r.count,
                }
                for r in results
            ],
        }

    def _operation_top_searches(self, arguments: dict[str, object]) -> object:
        event_type = _validate_event_type(arguments, _SEARCH_EVENT_TYPES)
        request = SearchTrendsRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=event_type,
            keyword=_parse_keyword(arguments),
            limit=_parse_limit(arguments, default=10),
        )
        results = top_searches(self._event_store, request)
        return {
            "operation": "top_searches",
            "results": [{"query": r.query, "count": r.count} for r in results],
        }

    def _operation_top_channels(self, arguments: dict[str, object]) -> object:
        event_type = _validate_event_type(arguments, _VIDEO_EVENT_TYPES)
        request = ChannelTrendsRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=event_type,
            keyword=_parse_keyword(arguments),
            limit=_parse_limit(arguments, default=10),
        )
        results = top_channels(self._event_store, request)
        return {
            "operation": "top_channels",
            "results": [
                {
                    "channel_name": r.channel_name,
                    "count": r.count,
                    "total_duration_seconds": r.total_duration_seconds,
                }
                for r in results
            ],
        }

    def _operation_top_videos(self, arguments: dict[str, object]) -> object:
        event_type = _validate_event_type(arguments, _VIDEO_EVENT_TYPES)
        request = VideoTrendsRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=event_type,
            keyword=_parse_keyword(arguments),
            limit=_parse_limit(arguments, default=10),
        )
        results = top_videos(self._event_store, request)
        return {
            "operation": "top_videos",
            "results": [
                {
                    "video_id": r.video_id,
                    "title": r.title,
                    "url": r.url,
                    "count": r.count,
                }
                for r in results
            ],
        }

    def _operation_activity_by_bucket(self, arguments: dict[str, object]) -> object:
        bucket = _parse_optional_str(arguments, "bucket") or "month"
        if bucket not in _VALID_BUCKETS:
            allowed = ", ".join(sorted(_VALID_BUCKETS))
            msg = f"bucket must be one of: {allowed}"
            raise ValueError(msg)
        request = ActivityBucketsRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=_validate_event_type(arguments),
            keyword=_parse_keyword(arguments),
            bucket=bucket,
            limit=_parse_limit(arguments, default=100),
        )
        results = activity_by_bucket(self._event_store, request)
        return {
            "operation": "activity_by_bucket",
            "bucket": bucket,
            "results": [{"bucket": r.bucket, "count": r.count} for r in results],
        }


_OPERATION_HANDLERS = {
    "events": EventQueryTool._operation_events,
    "activity_summary": EventQueryTool._operation_activity_summary,
    "top_searches": EventQueryTool._operation_top_searches,
    "top_channels": EventQueryTool._operation_top_channels,
    "top_videos": EventQueryTool._operation_top_videos,
    "activity_by_bucket": EventQueryTool._operation_activity_by_bucket,
}
