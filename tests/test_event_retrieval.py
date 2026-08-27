"""Tests for the retrieval-layer temporal event query."""

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    Event,
    compute_event_id,
)
from personal_ai.retrieval import EventQueryRequest, query_events
from personal_ai.storage import EventStore, connect_database


def _make_event(
    event_type: str,
    event_time: str,
    url: str,
    source: str = "chrome_history",
    search_query: str | None = None,
) -> Event:
    return Event(
        id=compute_event_id(source, event_type, event_time, url),
        event_type=event_type,
        event_time=event_time,
        source=source,
        url=url,
        search_query=search_query,
        metadata={},
    )


class TestQueryEvents:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.store.save_events(
            (
                _make_event(
                    EVENT_TYPE_SEARCH_QUERY,
                    "2026-01-20T12:00:00+00:00",
                    "https://www.google.de/search?q=career",
                    search_query="career",
                ),
                _make_event(
                    EVENT_TYPE_SEARCH_QUERY,
                    "2026-03-05T09:00:00+00:00",
                    "https://www.google.com/search?q=goals",
                    search_query="goals",
                ),
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()

    def test_time_range_query(self) -> None:
        results = query_events(
            self.store,
            EventQueryRequest(
                start_time="2026-01-01T00:00:00+00:00",
                end_time="2026-02-01T00:00:00+00:00",
                event_type=EVENT_TYPE_SEARCH_QUERY,
            ),
        )
        assert len(results) == 1
        assert results[0].search_query == "career"

    def test_event_type_filter(self) -> None:
        results = query_events(
            self.store, EventQueryRequest(event_type=EVENT_TYPE_URL_VISIT)
        )
        assert results == ()
        results = query_events(
            self.store, EventQueryRequest(event_type=EVENT_TYPE_SEARCH_QUERY)
        )
        assert len(results) == 2

    def test_source_filter(self) -> None:
        results = query_events(self.store, EventQueryRequest(source="chrome_history"))
        assert len(results) == 2

    def test_no_filters_returns_all(self) -> None:
        results = query_events(self.store, EventQueryRequest(limit=10))
        assert len(results) == 2
