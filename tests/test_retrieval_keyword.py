"""Tests for wire-through of the literal ``keyword`` filter at the retrieval layer.

Every retrieval entry point that targets temporal events accepts ``keyword``
and forwards it to the underlying ``EventStore`` so that results are narrowed
to events matching the literal (ASCII case-insensitive) substring. These tests
prove keyword composition with each aggregation and the structural query,
without needing the tools layer or the storage internals.
"""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
    compute_event_id,
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
from personal_ai.storage import EventStore, connect_database


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


def _search(event_time, query, event_type=EVENT_TYPE_YOUTUBE_SEARCH):
    return _make_event(
        event_type,
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


class RetrievalSetup:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.store.save_events(
            (
                # Title/channel keyword dimension.
                _watch(
                    "2026-01-05T10:00:00+00:00", "AAA", "FinanceTV", "Finance Deep Dive"
                ),
                _watch(
                    "2026-01-05T11:00:00+00:00", "AAA", "FinanceTV", "Finance Deep Dive"
                ),
                _watch(
                    "2026-02-01T12:00:00+00:00", "BBB", "GardenTV", "Gardening Basics"
                ),
                # search_query keyword dimension.
                _search("2026-01-10T09:00:00+00:00", "career pivot"),
                _search("2026-01-11T09:00:00+00:00", "career pivot"),
                _search("2026-01-12T09:00:00+00:00", "finance goals"),
                _chrome_search("2026-01-02T09:00:00+00:00", "career pivot"),
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()


class TestQueryEventsKeyword(RetrievalSetup):
    def test_keyword_matches_search_query(self) -> None:
        results = query_events(self.store, EventQueryRequest(keyword="career pivot"))
        assert len(results) == 3  # 2 youtube_search + 1 chrome search
        assert all(e.search_query == "career pivot" for e in results)

    def test_keyword_matches_title(self) -> None:
        results = query_events(self.store, EventQueryRequest(keyword="finance"))
        # 2 video watches with the title + 1 youtube_search 'finance goals'.
        assert len(results) == 3
        assert all(
            "finance" in str(e.title or e.search_query or "").lower() for e in results
        )

    def test_keyword_no_match(self) -> None:
        assert query_events(self.store, EventQueryRequest(keyword="nonexistent")) == ()


class TestActivitySummaryKeyword(RetrievalSetup):
    def test_narrows_counts(self) -> None:
        results = activity_summary(
            self.store, ActivitySummaryRequest(keyword="career pivot")
        )
        counts = {(r.event_type, r.source): r.count for r in results}
        # Only the search events match; no video_watch rows appear.
        assert counts == {
            (EVENT_TYPE_YOUTUBE_SEARCH, "youtube"): 2,
            (EVENT_TYPE_SEARCH_QUERY, "chrome_history"): 1,
        }


class TestTopSearchesKeyword(RetrievalSetup):
    def test_restricts_to_matching_searches(self) -> None:
        results = top_searches(self.store, SearchTrendsRequest(keyword="career"))
        by_query = {r.query: r.count for r in results}
        assert by_query == {"career pivot": 3}

    def test_combines_with_source(self) -> None:
        results = top_searches(
            self.store, SearchTrendsRequest(keyword="career", source="youtube")
        )
        by_query = {r.query: r.count for r in results}
        assert by_query == {"career pivot": 2}


class TestTopChannelsKeyword(RetrievalSetup):
    def test_restricts_to_matching_channels(self) -> None:
        results = top_channels(self.store, ChannelTrendsRequest(keyword="finance"))
        assert [r.channel_name for r in results] == ["FinanceTV"]
        assert results[0].count == 2


class TestTopVideosKeyword(RetrievalSetup):
    def test_restricts_to_matching_titles(self) -> None:
        results = top_videos(self.store, VideoTrendsRequest(keyword="finance"))
        assert [r.video_id for r in results] == ["AAA"]
        assert results[0].count == 2


class TestActivityByBucketKeyword(RetrievalSetup):
    def test_narrows_bucket_counts(self) -> None:
        results = activity_by_bucket(
            self.store, ActivityBucketsRequest(bucket="month", keyword="career")
        )
        counts = {r.bucket: r.count for r in results}
        assert counts == {"2026-01": 3}


class TestValidation:
    def test_invalid_bucket_still_rejects(self) -> None:
        conn = connect_database(":memory:")
        try:
            store = EventStore(conn)
            with pytest.raises(ValueError, match="bucket must be one of"):
                activity_by_bucket(
                    store, ActivityBucketsRequest(bucket="year", keyword="career")
                )
        finally:
            conn.close()
