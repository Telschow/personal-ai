"""Tests for the query_events agent tool aggregation operations.

Covers routing, validation, filtering, empty results, and output formatting
for every supported ``operation`` exposed through :class:`EventQueryTool`.
"""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
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
    source: str = "youtube",
    search_query: str | None = None,
    channel_name: str | None = None,
    title: str | None = None,
    video_id: str | None = None,
    duration_seconds: float | None = None,
) -> Event:
    metadata: dict[str, object] = {}
    if video_id is not None:
        metadata["video_id"] = video_id
    return Event(
        id=compute_event_id(source, event_type, event_time, url),
        event_type=event_type,
        event_time=event_time,
        source=source,
        url=url,
        search_query=search_query,
        channel_name=channel_name,
        title=title,
        duration_seconds=duration_seconds,
        metadata=metadata,
    )


def _watch(event_time, video_id, channel, title="Some Video", duration=300.0):
    return _make_event(
        EVENT_TYPE_VIDEO_WATCH,
        event_time,
        f"https://www.youtube.com/watch?v={video_id}",
        channel_name=channel,
        title=title,
        video_id=video_id,
        duration_seconds=duration,
    )


def _yt_search(event_time, query):
    return _make_event(
        EVENT_TYPE_YOUTUBE_SEARCH,
        event_time,
        f"https://www.youtube.com/results?search_query={query}",
        search_query=query,
    )


def _chrome_search(event_time, query):
    return _make_event(
        EVENT_TYPE_SEARCH_QUERY,
        event_time,
        f"https://www.google.de/search?q={query}",
        source="chrome_history",
        search_query=query,
    )


class ToolSetup:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.store.save_events(
            (
                _watch("2026-01-05T10:00:00+00:00", "AAA", "ChanA", "Video One"),
                _watch("2026-01-05T11:00:00+00:00", "AAA", "ChanA", "Video One"),
                _watch("2026-02-01T12:00:00+00:00", "BBB", "ChanB", "Video Two"),
                _yt_search("2026-01-10T09:00:00+00:00", "career"),
                _yt_search("2026-01-11T09:00:00+00:00", "career"),
                _yt_search("2026-01-12T09:00:00+00:00", "goals"),
                _chrome_search("2026-01-02T09:00:00+00:00", "goals"),
            )
        )
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()


class TestActivitySummaryOperation(ToolSetup):
    def test_output_shape(self) -> None:
        result = self.tool.query_events({"operation": "activity_summary"})
        assert result["operation"] == "activity_summary"
        rows = result["results"]
        assert {"event_type": "video_watch", "source": "youtube", "count": 3} in rows
        types = {(r["event_type"], r["source"]): r["count"] for r in rows}
        assert types[("youtube_search", "youtube")] == 3
        assert types[("search_query", "chrome_history")] == 1

    def test_source_filter(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_summary", "source": "youtube"}
        )
        rows = result["results"]
        assert all(r["source"] == "youtube" for r in rows)

    def test_event_type_filter(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_summary", "event_type": "video_watch"}
        )
        rows = result["results"]
        assert len(rows) == 1
        assert rows[0]["event_type"] == "video_watch"


class TestTopSearchesOperation(ToolSetup):
    def test_aggregates_across_search_sources(self) -> None:
        result = self.tool.query_events({"operation": "top_searches"})
        by_query = {r["query"]: r["count"] for r in result["results"]}
        assert by_query["goals"] == 2  # youtube + chrome
        assert by_query["career"] == 2

    def test_event_type_restricted_to_search(self) -> None:
        self.tool.query_events(
            {"operation": "top_searches", "event_type": "youtube_search"}
        )
        with pytest.raises(ValueError, match="event_type must be one of"):
            self.tool.query_events(
                {"operation": "top_searches", "event_type": "video_watch"}
            )

    def test_source_filter(self) -> None:
        result = self.tool.query_events(
            {"operation": "top_searches", "source": "youtube"}
        )
        by_query = {r["query"]: r["count"] for r in result["results"]}
        assert by_query["goals"] == 1  # the chrome goals search is excluded
        assert by_query["career"] == 2

    def test_time_filter(self) -> None:
        result = self.tool.query_events(
            {
                "operation": "top_searches",
                "start_time": "2026-01-10T00:00:00+00:00",
                "end_time": "2026-01-12T00:00:00+00:00",
            }
        )
        by_query = {r["query"]: r["count"] for r in result["results"]}
        assert by_query["career"] == 2
        assert "goals" not in by_query  # both goals searches outside range

    def test_limit(self) -> None:
        result = self.tool.query_events({"operation": "top_searches", "limit": 1})
        assert len(result["results"]) == 1

    def test_empty_store(self) -> None:
        conn = connect_database(":memory:")
        try:
            tool = EventQueryTool(EventStore(conn))
            result = tool.query_events({"operation": "top_searches"})
            assert result["results"] == []
        finally:
            conn.close()


class TestTopChannelsOperation(ToolSetup):
    def test_output_shape(self) -> None:
        result = self.tool.query_events({"operation": "top_channels"})
        by_channel = {r["channel_name"]: r for r in result["results"]}
        assert by_channel["ChanA"]["count"] == 2
        assert by_channel["ChanA"]["total_duration_seconds"] == 600.0

    def test_event_type_restricted_to_video(self) -> None:
        with pytest.raises(ValueError, match="event_type must be one of"):
            self.tool.query_events(
                {"operation": "top_channels", "event_type": "youtube_search"}
            )

    def test_time_filter(self) -> None:
        result = self.tool.query_events(
            {
                "operation": "top_channels",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-31T23:59:59+00:00",
            }
        )
        channels = [r["channel_name"] for r in result["results"]]
        assert "ChanA" in channels
        assert "ChanB" not in channels


class TestTopVideosOperation(ToolSetup):
    def test_output_shape(self) -> None:
        result = self.tool.query_events({"operation": "top_videos"})
        by_video = {r["video_id"]: r for r in result["results"]}
        assert by_video["AAA"]["count"] == 2
        assert by_video["AAA"]["title"] == "Video One"

    def test_event_type_restricted_to_video(self) -> None:
        with pytest.raises(ValueError, match="event_type must be one of"):
            self.tool.query_events(
                {"operation": "top_videos", "event_type": "search_query"}
            )


class TestActivityByBucketOperation(ToolSetup):
    def test_monthly_output_shape(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_by_bucket", "bucket": "month"}
        )
        assert result["bucket"] == "month"
        counts = {r["bucket"]: r["count"] for r in result["results"]}
        assert counts["2026-01"] == 6
        assert counts["2026-02"] == 1

    def test_default_bucket_is_month(self) -> None:
        result = self.tool.query_events({"operation": "activity_by_bucket"})
        assert result["bucket"] == "month"

    def test_invalid_bucket_raises(self) -> None:
        with pytest.raises(ValueError, match="bucket must be one of"):
            self.tool.query_events(
                {"operation": "activity_by_bucket", "bucket": "year"}
            )

    def test_day_bucket(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_by_bucket", "bucket": "day"}
        )
        labels = [r["bucket"] for r in result["results"]]
        assert labels == sorted(labels)

    def test_source_filter(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_by_bucket", "bucket": "month", "source": "youtube"}
        )
        counts = {r["bucket"]: r["count"] for r in result["results"]}
        assert counts["2026-01"] == 5  # excludes the chrome goals search


class TestOperationValidation:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_invalid_operation_raises(self) -> None:
        with pytest.raises(ValueError, match="operation must be one of"):
            self.tool.query_events({"operation": "bogus"})

    def test_non_string_operation_raises(self) -> None:
        with pytest.raises(TypeError, match="operation must be a string"):
            self.tool.query_events({"operation": 1})

    def test_invalid_limit(self) -> None:
        for op in (
            "top_searches",
            "top_channels",
            "top_videos",
            "activity_by_bucket",
        ):
            with pytest.raises(ValueError, match="limit must be non-negative"):
                self.tool.query_events({"operation": op, "limit": -1})

    def test_unsupported_bucket_argument_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported arguments"):
            self.tool.query_events({"operation": "events", "bucket": "month"})

    def test_events_operation_unchanged(self) -> None:
        # The default (no operation) still returns a bare list.
        result = self.tool.query_events({})
        assert isinstance(result, list)
