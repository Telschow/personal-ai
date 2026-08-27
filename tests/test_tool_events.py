"""Tests for the query_events agent tool."""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
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


class TestEventQueryToolYouTube:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()

    def _save_watch(self) -> None:
        self.store.save_event(
            Event(
                id=compute_event_id(
                    "youtube",
                    EVENT_TYPE_VIDEO_WATCH,
                    "2026-01-20T12:00:00+00:00",
                    "https://www.youtube.com/watch?v=AAA",
                ),
                event_type=EVENT_TYPE_VIDEO_WATCH,
                event_time="2026-01-20T12:00:00+00:00",
                source="youtube",
                title="Some Video",
                url="https://www.youtube.com/watch?v=AAA",
                channel_name="Podemos",
                metadata={"video_id": "AAA", "channel_id": "UCtK"},
            )
        )

    def _save_search(self) -> None:
        self.store.save_event(
            Event(
                id=compute_event_id(
                    "youtube",
                    EVENT_TYPE_YOUTUBE_SEARCH,
                    "2026-01-21T12:00:00+00:00",
                    "https://www.youtube.com/results?search_query=career",
                ),
                event_type=EVENT_TYPE_YOUTUBE_SEARCH,
                event_time="2026-01-21T12:00:00+00:00",
                source="youtube",
                url="https://www.youtube.com/results?search_query=career",
                search_query="career",
            )
        )

    def test_event_type_video_watch_accepted_and_formatted(self) -> None:
        self._save_watch()
        results = self.tool.query_events({"event_type": "video_watch"})
        assert len(results) == 1
        row = results[0]
        assert row["event_type"] == "video_watch"
        assert row["title"] == "Some Video"
        assert row["channel_name"] == "Podemos"
        assert row["video_id"] == "AAA"
        assert row["source"] == "youtube"

    def test_event_type_youtube_search_accepted(self) -> None:
        self._save_search()
        results = self.tool.query_events({"event_type": "youtube_search"})
        assert len(results) == 1
        assert results[0]["search_query"] == "career"

    def test_mixes_youtube_and_chrome_without_conflict(self) -> None:
        self._save_watch()
        self.store.save_event(
            Event(
                id=compute_event_id(
                    "chrome_history",
                    EVENT_TYPE_SEARCH_QUERY,
                    "2026-01-22T12:00:00+00:00",
                    "https://www.google.de/search?q=goals",
                ),
                event_type=EVENT_TYPE_SEARCH_QUERY,
                event_time="2026-01-22T12:00:00+00:00",
                source="chrome_history",
                url="https://www.google.de/search?q=goals",
                search_query="goals",
            )
        )
        assert len(self.tool.query_events({"event_type": "video_watch"})) == 1
        assert len(self.tool.query_events({"source": "youtube"})) == 1
        assert len(self.tool.query_events({"source": "chrome_history"})) == 1

    def test_rejects_non_youtube_event_type(self) -> None:
        with pytest.raises(ValueError, match="event_type must be one of"):
            self.tool.query_events({"event_type": "email"})
