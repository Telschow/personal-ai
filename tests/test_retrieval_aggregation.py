"""Tests for the retrieval-layer aggregation requests and functions."""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
    compute_event_id,
)
from personal_ai.retrieval import (
    DEFAULT_SEARCH_EVENT_TYPES,
    DEFAULT_VIDEO_EVENT_TYPES,
    ActivityBucketsRequest,
    ActivitySummaryRequest,
    ChannelTrendsRequest,
    SearchTrendsRequest,
    VideoTrendsRequest,
    activity_by_bucket,
    activity_summary,
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
        metadata=metadata,
    )


def _watch(event_time, video_id, channel, title="Some Video"):
    return _make_event(
        EVENT_TYPE_VIDEO_WATCH,
        event_time,
        f"https://www.youtube.com/watch?v={video_id}",
        channel_name=channel,
        title=title,
        video_id=video_id,
    )


def _search(event_time, query, event_type=EVENT_TYPE_YOUTUBE_SEARCH):
    return _make_event(
        event_type,
        event_time,
        f"https://www.youtube.com/results?search_query={query}",
        search_query=query,
    )


class RetrievalSetup:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        self.store.save_events(
            (
                # January
                _search("2026-01-10T09:00:00+00:00", "career"),
                _search("2026-01-11T09:00:00+00:00", "career"),
                _search(
                    "2026-01-02T09:00:00+00:00",
                    "goals",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                ),
                # February
                _watch("2026-02-01T12:00:00+00:00", "AAA", "ChanA", "Video One"),
                _watch("2026-02-02T12:00:00+00:00", "AAA", "ChanA", "Video One"),
                _watch("2026-02-03T12:00:00+00:00", "BBB", "ChanB", "Video Two"),
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()


class TestDefaultEventTypes:
    def test_default_search_types(self) -> None:
        assert set(DEFAULT_SEARCH_EVENT_TYPES) == {
            EVENT_TYPE_SEARCH_QUERY,
            EVENT_TYPE_YOUTUBE_SEARCH,
        }

    def test_default_video_types(self) -> None:
        assert set(DEFAULT_VIDEO_EVENT_TYPES) == {EVENT_TYPE_VIDEO_WATCH}


class TestActivitySummaryRequest(RetrievalSetup):
    def test_groups_by_type_and_source(self) -> None:
        results = activity_summary(self.store, ActivitySummaryRequest())
        counts = {(r.event_type, r.source): r.count for r in results}
        assert counts[("search_query", "youtube")] == 1
        assert counts[("youtube_search", "youtube")] == 2
        assert counts[("video_watch", "youtube")] == 3

    def test_event_type_filter(self) -> None:
        results = activity_summary(
            self.store,
            ActivitySummaryRequest(event_type=EVENT_TYPE_VIDEO_WATCH),
        )
        assert len(results) == 1
        assert results[0].count == 3

    def test_time_filter(self) -> None:
        results = activity_summary(
            self.store,
            ActivitySummaryRequest(
                start_time="2026-02-01T00:00:00+00:00",
                end_time="2026-02-28T23:59:59+00:00",
            ),
        )
        assert sum(r.count for r in results) == 3


class TestTopSearchesRequest(RetrievalSetup):
    def test_default_includes_both_search_types(self) -> None:
        results = top_searches(self.store, SearchTrendsRequest())
        by_query = {r.query: r.count for r in results}
        assert by_query["career"] == 2
        assert by_query["goals"] == 1

    def test_source_filter(self) -> None:
        # All searches here are 'youtube'; restricting to a foreign source
        # yields nothing.
        results = top_searches(self.store, SearchTrendsRequest(source="chrome_history"))
        assert results == ()

    def test_explicit_event_type(self) -> None:
        results = top_searches(
            self.store,
            SearchTrendsRequest(event_type=EVENT_TYPE_SEARCH_QUERY),
        )
        assert [r.query for r in results] == ["goals"]

    def test_limit(self) -> None:
        results = top_searches(self.store, SearchTrendsRequest(limit=1))
        assert len(results) == 1
        assert results[0].query == "career"


class TestTopChannelsRequest(RetrievalSetup):
    def test_groups_by_channel(self) -> None:
        results = top_channels(self.store, ChannelTrendsRequest())
        by_channel = {r.channel_name: r for r in results}
        assert by_channel["ChanA"].count == 2
        assert by_channel["ChanB"].count == 1

    def test_source_filter(self) -> None:
        results = top_channels(
            self.store, ChannelTrendsRequest(source="chrome_history")
        )
        assert results == ()


class TestTopVideosRequest(RetrievalSetup):
    def test_groups_by_video_id(self) -> None:
        results = top_videos(self.store, VideoTrendsRequest())
        by_video = {r.video_id: r for r in results}
        assert by_video["AAA"].count == 2
        assert by_video["BBB"].count == 1
        assert by_video["AAA"].title == "Video One"

    def test_limit(self) -> None:
        results = top_videos(self.store, VideoTrendsRequest(limit=1))
        assert len(results) == 1
        assert results[0].video_id == "AAA"


class TestActivityByBucketRequest(RetrievalSetup):
    def test_month_buckets(self) -> None:
        results = activity_by_bucket(self.store, ActivityBucketsRequest(bucket="month"))
        counts = {r.bucket: r.count for r in results}
        assert counts["2026-01"] == 3
        assert counts["2026-02"] == 3

    def test_source_filter(self) -> None:
        results = activity_by_bucket(
            self.store, ActivityBucketsRequest(bucket="month", source="youtube")
        )
        counts = {r.bucket: r.count for r in results}
        assert counts["2026-01"] == 3  # all youtube here


class TestValidationRequest:
    def test_invalid_bucket_raises(self) -> None:
        conn = connect_database(":memory:")
        try:
            store = EventStore(conn)
            with pytest.raises(ValueError, match="bucket must be one of"):
                activity_by_bucket(store, ActivityBucketsRequest(bucket="year"))
        finally:
            conn.close()
