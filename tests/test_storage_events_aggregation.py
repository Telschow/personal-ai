"""Tests for EventStore deterministic temporal aggregation.

All aggregation queries must be deterministic: ties are broken by an explicit
secondary sort key, buckets are computed in UTC from the canonical event time,
and the store never loads the whole table into Python for simple group-bys.
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
from personal_ai.storage.events import ActivityCount, BucketCount


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


def _empty_store() -> EventStore:
    return EventStore(connect_database(":memory:"))


def _make_watch(
    event_time: str,
    video_id: str,
    channel: str,
    url: str | None = None,
    title: str | None = "Some Video",
    duration: float | None = 360.0,
    source: str = "youtube",
) -> Event:
    return _make_event(
        EVENT_TYPE_VIDEO_WATCH,
        event_time,
        url or f"https://www.youtube.com/watch?v={video_id}",
        source=source,
        channel_name=channel,
        title=title,
        video_id=video_id,
        duration_seconds=duration,
    )


def _make_search(
    event_time: str,
    query: str,
    event_type: str = EVENT_TYPE_YOUTUBE_SEARCH,
    source: str = "youtube",
) -> Event:
    return _make_event(
        event_type,
        event_time,
        f"https://www.youtube.com/results?search_query={query}",
        source=source,
        search_query=query,
    )


class TestActivitySummary:
    def test_empty_store_returns_empty(self) -> None:
        store = _empty_store()
        assert store.activity_summary() == ()

    def test_multiple_event_types_and_sources(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
                _make_watch("2026-01-05T11:00:00+00:00", "b", "ChanA"),
                _make_search("2026-01-10T09:00:00+00:00", "career"),
                _make_search(
                    "2026-01-02T09:00:00+00:00",
                    "goals",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    source="chrome_history",
                ),
            )
        )
        results = store.activity_summary()
        # Ordered by count DESC, then event_type ASC, then source ASC.
        assert results == (
            ActivityCount(event_type="video_watch", source="youtube", count=2),
            ActivityCount(event_type="search_query", source="chrome_history", count=1),
            ActivityCount(event_type="youtube_search", source="youtube", count=1),
        )

    def test_time_filtering(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
                _make_watch("2026-03-10T10:00:00+00:00", "b", "ChanA"),
            )
        )
        results = store.activity_summary(
            start_time="2026-01-01T00:00:00+00:00",
            end_time="2026-02-01T00:00:00+00:00",
        )
        assert results == (
            ActivityCount(event_type="video_watch", source="youtube", count=1),
        )

    def test_source_filtering(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
                _make_search("2026-01-10T09:00:00+00:00", "career"),
                _make_search(
                    "2026-01-02T09:00:00+00:00",
                    "goals",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    source="chrome_history",
                ),
            )
        )
        results = store.activity_summary(source="youtube")
        assert results == (
            ActivityCount(event_type="video_watch", source="youtube", count=1),
            ActivityCount(event_type="youtube_search", source="youtube", count=1),
        )

    def test_event_type_filtering(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
                _make_search("2026-01-10T09:00:00+00:00", "career"),
            )
        )
        results = store.activity_summary(event_type=EVENT_TYPE_VIDEO_WATCH)
        assert results == (
            ActivityCount(event_type="video_watch", source="youtube", count=1),
        )


class TestTopSearchQueries:
    def _store(self) -> EventStore:
        store = _empty_store()
        store.save_events(
            (
                _make_search("2026-01-10T09:00:00+00:00", "career"),
                _make_search("2026-01-11T09:00:00+00:00", "career"),
                _make_search("2026-01-12T09:00:00+00:00", "goals"),
                _make_search("2026-01-13T09:00:00+00:00", "goals"),
                _make_search(
                    "2026-01-14T09:00:00+00:00",
                    "career",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    source="chrome_history",
                ),
            )
        )
        return store

    def test_repeated_queries_aggregated(self) -> None:
        results = self._store().top_search_queries()
        by_query = {r.query: r.count for r in results}
        assert by_query["career"] == 3
        assert by_query["goals"] == 2

    def test_deterministic_tie_breaking(self) -> None:
        # Two queries with equal counts ordered alphabetically.
        store = _empty_store()
        store.save_events(
            (
                _make_search("2026-01-10T09:00:00+00:00", "alpha"),
                _make_search("2026-01-11T09:00:00+00:00", "beta"),
            )
        )
        results = store.top_search_queries()
        assert [r.query for r in results] == ["alpha", "beta"]

    def test_source_filtering(self) -> None:
        results = self._store().top_search_queries(source="youtube")
        assert all(r.query != "career" or r.count == 2 for r in results)
        assert {r.query for r in results} == {"career", "goals"}
        career = next(r for r in results if r.query == "career")
        assert career.count == 2  # excludes the chrome search event

    def test_time_filtering(self) -> None:
        results = self._store().top_search_queries(
            start_time="2026-01-12T00:00:00+00:00",
            end_time="2026-01-14T23:59:59+00:00",
        )
        by_query = {r.query: r.count for r in results}
        # goals: Jan 12 + Jan 13 (both in range) -> 2.
        # career: only the Jan 14 (chrome) event is in range -> 1.
        assert by_query["goals"] == 2
        assert by_query["career"] == 1

    def test_limit(self) -> None:
        results = self._store().top_search_queries(limit=1)
        assert len(results) == 1
        assert results[0].count == 3  # 'career' is most frequent

    def test_non_search_events_excluded(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_search("2026-01-10T09:00:00+00:00", "career"),
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
            )
        )
        results = store.top_search_queries()
        assert [r.query for r in results] == ["career"]

    def test_event_type_restriction(self) -> None:
        results = self._store().top_search_queries(
            event_types=(EVENT_TYPE_SEARCH_QUERY,)
        )
        assert [r.query for r in results] == ["career"]
        assert results[0].count == 1


class TestTopChannels:
    def _store(self) -> EventStore:
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA", duration=300.0),
                _make_watch("2026-01-05T11:00:00+00:00", "b", "ChanA", duration=300.0),
                _make_watch("2026-02-01T12:00:00+00:00", "c", "ChanB", duration=600.0),
            )
        )
        return store

    def test_repeated_channels_aggregated(self) -> None:
        results = self._store().top_channels()
        by_channel = {r.channel_name: r for r in results}
        assert by_channel["ChanA"].count == 2
        assert by_channel["ChanA"].total_duration_seconds == 600.0
        assert by_channel["ChanB"].count == 1

    def test_deterministic_ordering(self) -> None:
        # Equal counts -> tie broken by channel_name ascending.
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "a", "Bravo"),
                _make_watch("2026-01-05T11:00:00+00:00", "b", "Alpha"),
            )
        )
        results = store.top_channels()
        assert [r.channel_name for r in results] == ["Alpha", "Bravo"]

    def test_time_filtering(self) -> None:
        results = self._store().top_channels(
            start_time="2026-01-01T00:00:00+00:00",
            end_time="2026-01-31T00:00:00+00:00",
        )
        assert [r.channel_name for r in results] == ["ChanA"]

    def test_source_filtering(self) -> None:
        store = _empty_store()
        store.save_events(
            (
                _make_watch(
                    "2026-01-05T10:00:00+00:00", "a", "ChanA", source="youtube"
                ),
                _make_watch("2026-01-05T11:00:00+00:00", "c", "ChanZ", source="other"),
            )
        )
        results = store.top_channels(source="youtube")
        assert [r.channel_name for r in results] == ["ChanA"]

    def test_durations_none_when_missing(self) -> None:
        store = _empty_store()
        store.save_events(
            (_make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA", duration=None),)
        )
        results = store.top_channels()
        assert results[0].total_duration_seconds is None


class TestTopVideos:
    def _store(self) -> EventStore:
        store = _empty_store()
        store.save_events(
            (
                _make_watch(
                    "2026-01-05T10:00:00+00:00", "AAA", "ChanA", title="Video One"
                ),
                _make_watch(
                    "2026-01-06T10:00:00+00:00", "AAA", "ChanA", title="Video One"
                ),
                _make_watch(
                    "2026-02-01T12:00:00+00:00", "BBB", "ChanB", title="Video Two"
                ),
            )
        )
        return store

    def test_repeated_video_ids_aggregated(self) -> None:
        results = self._store().top_videos()
        by_video = {r.video_id: r for r in results}
        assert by_video["AAA"].count == 2
        assert by_video["AAA"].title == "Video One"
        assert by_video["BBB"].count == 1

    def test_deterministic_ordering(self) -> None:
        # Equal counts -> tie broken by video_id ascending.
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "ZZZ", "ChanA"),
                _make_watch("2026-01-05T11:00:00+00:00", "AAA", "ChanA"),
            )
        )
        results = store.top_videos()
        assert [r.video_id for r in results] == ["AAA", "ZZZ"]

    def test_missing_title_is_none(self) -> None:
        store = _empty_store()
        store.save_events(
            (_make_watch("2026-01-05T10:00:00+00:00", "AAA", "ChanA", title=None),)
        )
        results = store.top_videos()
        assert results[0].title is None

    def test_rows_without_video_id_excluded(self) -> None:
        # A video_watch without a video_id in metadata is not grouped.
        store = _empty_store()
        store.save_events(
            (
                _make_watch("2026-01-05T10:00:00+00:00", "AAA", "ChanA"),
                Event(
                    id=compute_event_id(
                        "youtube",
                        EVENT_TYPE_VIDEO_WATCH,
                        "2026-01-05T11:00:00+00:00",
                        "https://www.youtube.com/watch?v=no-meta",
                    ),
                    event_type=EVENT_TYPE_VIDEO_WATCH,
                    event_time="2026-01-05T11:00:00+00:00",
                    source="youtube",
                    url="https://www.youtube.com/watch?v=no-meta",
                    metadata={},
                ),
            )
        )
        results = store.top_videos()
        assert [r.video_id for r in results] == ["AAA"]


class TestActivityByBucket:
    def _store(self) -> EventStore:
        store = _empty_store()
        store.save_events(
            (
                # January
                _make_watch("2026-01-05T10:00:00+00:00", "a", "ChanA"),
                _make_search("2026-01-10T09:00:00+00:00", "career"),
                _make_search(
                    "2026-01-02T09:00:00+00:00",
                    "goals",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    source="chrome_history",
                ),
                # Late January to demonstrate month boundary
                _make_watch("2026-01-31T22:00:00+00:00", "b", "ChanA"),
                # February (crossing into a new month)
                _make_watch("2026-02-01T02:00:00+00:00", "c", "ChanB"),
                # March
                _make_watch("2026-03-15T10:00:00+00:00", "d", "ChanA"),
            )
        )
        return store

    def test_monthly_buckets_chronological(self) -> None:
        results = self._store().activity_by_bucket(bucket="month")
        assert [(r.bucket, r.count) for r in results] == [
            ("2026-01", 4),
            ("2026-02", 1),
            ("2026-03", 1),
        ]

    def test_events_crossing_month_boundary(self) -> None:
        # Jan 31 22:00 UTC and Feb 1 02:00 UTC land in different buckets.
        results = self._store().activity_by_bucket(bucket="month")
        counts = {r.bucket: r.count for r in results}
        assert counts["2026-01"] == 4  # includes the Jan 31 event
        assert counts["2026-02"] == 1  # includes the Feb 1 event

    def test_daily_buckets(self) -> None:
        results = self._store().activity_by_bucket(bucket="day")
        labels = [r.bucket for r in results]
        assert labels == [
            "2026-01-02",
            "2026-01-05",
            "2026-01-10",
            "2026-01-31",
            "2026-02-01",
            "2026-03-15",
        ]

    def test_weekly_buckets_deterministic(self) -> None:
        results = self._store().activity_by_bucket(bucket="week")
        labels = [r.bucket for r in results]
        assert labels == sorted(labels)

    def test_timezone_aware_bucketing(self) -> None:
        # All events are canonical UTC (+00:00); buckets reflect UTC.
        store = _empty_store()
        store.save_events(
            (
                _make_watch(
                    "2026-01-01T23:00:00+00:00", "a", "ChanA"
                ),  # UTC Jan 1 late
            )
        )
        results = store.activity_by_bucket(bucket="month")
        assert results == (BucketCount(bucket="2026-01", count=1),)

    def test_invalid_bucket_raises(self) -> None:
        with pytest.raises(ValueError, match="bucket must be one of"):
            _empty_store().activity_by_bucket(bucket="year")

    def test_time_filtering(self) -> None:
        results = self._store().activity_by_bucket(
            bucket="month",
            start_time="2026-02-01T00:00:00+00:00",
            end_time="2026-03-31T23:59:59+00:00",
        )
        assert [(r.bucket, r.count) for r in results] == [
            ("2026-02", 1),
            ("2026-03", 1),
        ]
