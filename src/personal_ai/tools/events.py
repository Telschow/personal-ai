"""Agent tool exposing structural temporal-event queries.

Temporal events (browser visits, searches) are retrieved deterministically
through time-range and type/source filters — never through semantic or
vector search. This tool gives the agent a narrowly scoped way to answer
temporal questions such as "what was I searching for in January 2026?".
"""

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    Event,
)
from personal_ai.retrieval import EventQueryRequest, query_events
from personal_ai.storage.events import EventStore

_ALLOWED_ARGUMENT_KEYS = frozenset(
    {"start_time", "end_time", "event_type", "source", "limit"}
)

_VALID_EVENT_TYPES = frozenset({EVENT_TYPE_SEARCH_QUERY, EVENT_TYPE_URL_VISIT})


def _parse_optional_str(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value.strip() or None


def _parse_limit(arguments: dict[str, object]) -> int:
    limit = arguments.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    return limit


def _format_event(event: Event) -> dict[str, object]:
    """Format one event for the model with concise provenance."""
    return {
        "event_type": event.event_type,
        "event_time": event.event_time,
        "title": event.title,
        "url": event.url,
        "search_query": event.search_query,
        "source": event.source,
    }


class EventQueryTool:
    """Structural temporal-event query over the persisted event store.

    Accepts only explicitly allow-listed JSON arguments and converts them
    into a typed :class:`~personal_ai.retrieval.EventQueryRequest`; the model
    can never influence SQL beyond validated time, type, and source filters.
    """

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    def query_events(self, arguments: dict[str, object]) -> list[dict[str, object]]:
        """Run one structural event query from model-supplied arguments."""
        unknown_keys = sorted(set(arguments) - _ALLOWED_ARGUMENT_KEYS)
        if unknown_keys:
            msg = f"unsupported arguments: {', '.join(unknown_keys)}"
            raise ValueError(msg)

        event_type = _parse_optional_str(arguments, "event_type")
        if event_type is not None and event_type not in _VALID_EVENT_TYPES:
            allowed = ", ".join(sorted(_VALID_EVENT_TYPES))
            msg = f"event_type must be one of: {allowed}"
            raise ValueError(msg)

        request = EventQueryRequest(
            start_time=_parse_optional_str(arguments, "start_time"),
            end_time=_parse_optional_str(arguments, "end_time"),
            source=_parse_optional_str(arguments, "source"),
            event_type=event_type,
            limit=_parse_limit(arguments),
        )
        events = query_events(self._event_store, request)
        return [_format_event(event) for event in events]
