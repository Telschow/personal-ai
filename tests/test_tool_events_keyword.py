"""Tests for the ``keyword`` filter through the ``query_events`` agent tool.

``keyword`` is an optional literal (ASCII case-insensitive) substring filter
over title, url, search_query and channel_name. This file proves it routes
through every operation, composes with the structural filters, rejects
invalid types consistently, and leaves the no-keyword behaviour unchanged.
"""

import pytest

from personal_ai.events.models import (
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


def _yt_search(event_time, query, **kwargs):
    return _make_event(
        EVENT_TYPE_YOUTUBE_SEARCH,
        event_time,
        f"https://www.youtube.com/results?search_query={query}",
        search_query=query,
        **kwargs,
    )


class ToolSetup:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.store.save_events(
            (
                _watch(
                    "2026-01-05T10:00:00+00:00", "AAA", "FinanceTV", "Finance Deep Dive"
                ),
                _watch(
                    "2026-02-01T12:00:00+00:00", "BBB", "GardenTV", "Gardening Basics"
                ),
                _yt_search("2026-01-10T09:00:00+00:00", "career pivot"),
                _yt_search("2026-02-02T09:00:00+00:00", "goals"),
            )
        )
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()


class TestEventsOperationKeyword(ToolSetup):
    def test_keyword_narrows_events(self) -> None:
        results = self.tool.query_events({"operation": "events", "keyword": "career"})
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["search_query"] == "career pivot"

    def test_keyword_with_source(self) -> None:
        # All events are youtube here, so filtering still returns the match.
        results = self.tool.query_events(
            {"operation": "events", "keyword": "finance", "source": "youtube"}
        )
        assert isinstance(results, list)
        assert len(results) == 1  # the single FinanceTV watch
        assert results[0]["title"] == "Finance Deep Dive"

    def test_operation_events_without_keyword_unchanged(self) -> None:
        results = self.tool.query_events({})
        assert isinstance(results, list)
        assert len(results) == 4


class TestActivitySummaryKeyword(ToolSetup):
    def test_narrows_output(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_summary", "keyword": "career"}
        )
        assert result["operation"] == "activity_summary"
        by_key = {(r["event_type"], r["source"]): r["count"] for r in result["results"]}
        assert by_key == {("youtube_search", "youtube"): 1}


class TestTopSearchesKeyword(ToolSetup):
    def test_restricts_to_matching_searches(self) -> None:
        result = self.tool.query_events(
            {"operation": "top_searches", "keyword": "career"}
        )
        assert [r["query"] for r in result["results"]] == ["career pivot"]
        assert result["results"][0]["count"] == 1

    def test_combines_with_time_range(self) -> None:
        result = self.tool.query_events(
            {
                "operation": "top_searches",
                "keyword": "e",
                "start_time": "2026-02-01T00:00:00+00:00",
                "end_time": "2026-02-28T23:59:59+00:00",
            }
        )
        # 'e' matches the search_query 'goals' (Feb), not 'career pivot' (Jan).
        assert [r["query"] for r in result["results"]] == ["goals"]


class TestTopChannelsKeyword(ToolSetup):
    def test_restricts_to_matching_channel(self) -> None:
        result = self.tool.query_events(
            {"operation": "top_channels", "keyword": "finance"}
        )
        assert [r["channel_name"] for r in result["results"]] == ["FinanceTV"]


class TestTopVideosKeyword(ToolSetup):
    def test_restricts_to_matching_title(self) -> None:
        result = self.tool.query_events(
            {"operation": "top_videos", "keyword": "finance"}
        )
        assert [r["video_id"] for r in result["results"]] == ["AAA"]


class TestActivityByBucketKeyword(ToolSetup):
    def test_narrows_bucket_counts(self) -> None:
        result = self.tool.query_events(
            {"operation": "activity_by_bucket", "bucket": "month", "keyword": "finance"}
        )
        counts = {r["bucket"]: r["count"] for r in result["results"]}
        assert counts == {"2026-01": 1}


class TestKeywordValidation:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.tool = EventQueryTool(self.store)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_non_string_keyword_rejected(self) -> None:
        for args in (
            {"keyword": 5},
            {"operation": "events", "keyword": 5},
            {"operation": "activity_summary", "keyword": ["career"]},
        ):
            with pytest.raises(TypeError, match="keyword must be a string"):
                self.tool.query_events(args)

    def test_empty_and_whitespace_keyword_treated_as_omitted(self) -> None:
        # A whitespace-only keyword behaves like no keyword: all events return.
        result = self.tool.query_events({"operation": "events", "keyword": "   "})
        assert isinstance(result, list)

    def test_unknown_argument_still_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported arguments"):
            self.tool.query_events({"operation": "events", "keyword": "x", "bogus": 1})
