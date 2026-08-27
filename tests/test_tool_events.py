"""Tests for the query_events agent tool."""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    Event,
    compute_event_id,
)
from personal_ai.storage import EventStore, connect_database
from personal_ai.tools.events import EventQueryTool


def _make_event(
    event_type: str,
    event_time: str,
    url: str,
    search_query: str | None = None,
    source: str = "chrome_history",
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


class TestEventQueryTool:
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
                    EVENT_TYPE_URL_VISIT,
                    "2026-01-21T10:00:00+00:00",
                    "https://www.linkedin.com/in/abc",
                ),
            )
        )
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_returns_search_queries_in_range(self) -> None:
        results = self.tool.query_events(
            {
                "event_type": "search_query",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-31T00:00:00+00:00",
            }
        )
        assert len(results) == 1
        assert results[0]["search_query"] == "career"
        assert results[0]["event_type"] == "search_query"
        assert results[0]["url"] == "https://www.google.de/search?q=career"

    def test_returns_url_visits(self) -> None:
        results = self.tool.query_events({"event_type": "url_visit"})
        assert len(results) == 1
        assert results[0]["url"] == "https://www.linkedin.com/in/abc"

    def test_no_filters_returns_all(self) -> None:
        results = self.tool.query_events({})
        assert len(results) == 2

    def test_limit(self) -> None:
        results = self.tool.query_events({"limit": 1})
        assert len(results) == 1

    def test_rejects_unknown_argument(self) -> None:
        with pytest.raises(ValueError, match="unsupported arguments"):
            self.tool.query_events({"query": "career"})

    def test_rejects_invalid_event_type(self) -> None:
        with pytest.raises(ValueError, match="event_type must be one of"):
            self.tool.query_events({"event_type": "email"})

    def test_rejects_non_string_time(self) -> None:
        with pytest.raises(TypeError, match="start_time must be a string"):
            self.tool.query_events({"start_time": 123})

    def test_rejects_negative_limit(self) -> None:
        with pytest.raises(ValueError, match="limit must be non-negative"):
            self.tool.query_events({"limit": -1})
