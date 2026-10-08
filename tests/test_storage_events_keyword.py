"""Tests for literal keyword filtering in EventStore.

The ``keyword`` filter is a deterministic, case-insensitive (for ASCII)
substring match over an event's title, url, search_query, and channel_name.
``%`` and ``_`` supplied by the caller are treated literally (escaped for
SQLite ``LIKE``), and the filter composes with every structural filter and
aggregation. Results stay deterministically ordered and an omitted keyword
must be behaviourally identical to no filter.
"""

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    Event,
    compute_event_id,
)
from personal_ai.storage import EventStore, connect_database
from personal_ai.storage.events import EventQuery


def _kw(store: EventStore, keyword: str, **filters: object) -> tuple:
    """Run a keyword search via ``EventQuery``, forwarding extra filters."""
    return store.search(EventQuery(keyword=keyword, **filters))  # type: ignore[arg-type]


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


def _watch(
    event_time: str,
    video_id: str,
    channel: str,
    title: str = "Some Video",
    url: str | None = None,
    duration: float | None = 360.0,
) -> Event:
    return _make_event(
        EVENT_TYPE_VIDEO_WATCH,
        event_time,
        url or f"https://www.youtube.com/watch?v={video_id}",
        channel_name=channel,
        title=title,
        video_id=video_id,
        duration_seconds=duration,
    )


def _search(
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


def _store() -> EventStore:
    store = EventStore(connect_database(":memory:"))
    store.save_events(
        (
            # Title match candidate.
            _watch(
                "2026-01-05T10:00:00+00:00", "aaa", "CNBC", title="Career Interview"
            ),
            # URL match candidate.
            _watch(
                "2026-01-06T10:00:00+00:00",
                "bbb",
                "Bloomberg",
                title="Finance 101",
                url="https://www.youtube.com/watch?v=finance-report",
            ),
            # search_query / channel_name match candidates.
            _search("2026-01-07T10:00:00+00:00", "career tips"),
            _watch("2026-01-08T10:00:00+00:00", "ccc", "CareersDaily"),
            # Unrelated row.
            _watch("2026-02-01T10:00:00+00:00", "ddd", "GardenTV", title="Gardening"),
            _search("2026-02-02T10:00:00+00:00", "planting"),
        )
    )
    return store


class TestSearchKeyword:
    def test_matches_title(self) -> None:
        results = _kw(_store(), "Career Interview")
        assert [e.title for e in results] == ["Career Interview"]

    def test_matches_url(self) -> None:
        results = _kw(_store(), "finance-report")
        assert [e.title for e in results] == ["Finance 101"]

    def test_matches_search_query(self) -> None:
        results = _kw(_store(), "career tips")
        assert [e.search_query for e in results] == ["career tips"]

    def test_matches_channel_name(self) -> None:
        results = _kw(_store(), "CareersDaily")
        assert [e.channel_name for e in results] == ["CareersDaily"]

    def test_case_insensitive_ascii(self) -> None:
        results = _kw(_store(), "CAREER INTERVIEW")
        assert [e.title for e in results] == ["Career Interview"]

    def test_partial_substring(self) -> None:
        # "career" appears in title, search_query, and channel_name.
        results = _kw(_store(), "career")
        assert len(results) == 3

    def test_no_match_returns_empty(self) -> None:
        assert _kw(_store(), "nonexistent-term") == ()

    def test_keyword_with_source(self) -> None:
        store = _store()
        store.save_events(
            (
                _search(
                    "2026-03-01T10:00:00+00:00",
                    "career goals",
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    source="chrome_history",
                ),
            )
        )
        # The chrome search also matches 'career', but restricting source to
        # youtube must exclude it.
        results = _kw(store, "career", source="youtube")
        assert all(e.source == "youtube" for e in results)
        assert len(results) == 3  # the original youtube career matches only

    def test_keyword_with_event_type(self) -> None:
        results = _kw(_store(), "career", event_type=EVENT_TYPE_VIDEO_WATCH)
        # "career" appears in the title of the video_watch and channel of another.
        types = {e.event_type for e in results}
        assert types == {EVENT_TYPE_VIDEO_WATCH}

    def test_keyword_with_time_range(self) -> None:
        results = _kw(
            _store(),
            "career",
            start_time="2026-01-01T00:00:00+00:00",
            end_time="2026-01-31T23:59:59+00:00",
        )
        # CareersDaily watch is Jan 8; Career Interview Jan 5; search Jan 7.
        assert len(results) == 3

    def test_limit_still_works(self) -> None:
        results = _kw(_store(), "career", limit=2)
        assert len(results) == 2

    def test_literal_percent(self) -> None:
        store = EventStore(connect_database(":memory:"))
        store.save_events(
            (
                _watch(
                    "2026-01-05T10:00:00+00:00", "a", "ChanA", title="100% Progress"
                ),
                _watch("2026-01-06T10:00:00+00:00", "b", "ChanB", title="Plain"),
            )
        )
        # '%' must be treated literally, not as a wildcard.
        assert [e.title for e in _kw(store, "100% Progress")] == ["100% Progress"]
        # A bare '%' must not match everything; it matches only rows that
        # literally contain '%'.
        assert [e.title for e in _kw(store, "%")] == ["100% Progress"]

    def test_literal_underscore(self) -> None:
        store = EventStore(connect_database(":memory:"))
        store.save_events(
            (
                _watch("2026-01-05T10:00:00+00:00", "a", "ChanA", title="part_a"),
                _watch("2026-01-06T10:00:00+00:00", "b", "ChanB", title="partXb"),
                _watch("2026-01-07T10:00:00+00:00", "c", "ChanC", title="part_b"),
            )
        )
        # '_' is matched literally: 'part_a' must not match 'partXb' (where the
        # '_' would be a single-char wildcard) nor 'part_b'.
        results = _kw(store, "part_a")
        assert [e.title for e in results] == ["part_a"]

    def test_literal_backslash(self) -> None:
        store = EventStore(connect_database(":memory:"))
        store.save_events(
            (
                _watch("2026-01-05T10:00:00+00:00", "a", "ChanA", title=r"win\path"),
                _watch("2026-01-06T10:00:00+00:00", "b", "ChanB", title="winXpath"),
            )
        )
        results = _kw(store, r"win\path")
        assert [e.title for e in results] == [r"win\path"]

    def test_deterministic_ordering(self) -> None:
        # Ordering is by event_time then id, exactly as without keyword.
        store = _store()
        with_kw = [(e.event_time, e.id) for e in _kw(store, "career")]
        assert with_kw == sorted(with_kw)

    def test_omitted_keyword_preserves_behavior(self) -> None:
        store = _store()
        all_events = store.search(EventQuery())
        kw_events = store.search(EventQuery(keyword=None))
        assert [e.id for e in all_events] == [e.id for e in kw_events]
        # And matches unfiltered list order.
        assert [e.id for e in all_events] == [
            e.id for e in store.list_events(limit=1000)
        ]


class TestKeywordAggregations:
    def test_top_search_queries_respects_keyword(self) -> None:
        results = _store().top_search_queries(keyword="career")
        # career tips in youtube_search; also chrome 'career' if present — none here.
        assert {r.query for r in results} == {"career tips"}

    def test_top_channels_respects_keyword(self) -> None:
        store = _store()
        store.save_events(
            (_watch("2026-01-09T10:00:00+00:00", "x", "CareersDaily", title="Show 2"),)
        )
        results = store.top_channels(keyword="CareersDaily")
        assert [r.channel_name for r in results] == ["CareersDaily"]
        assert results[0].count == 2  # Jan 8 + Jan 9 watches

    def test_top_videos_respects_keyword(self) -> None:
        store = _store()
        store.save_events(
            (
                _watch(
                    "2026-01-09T10:00:00+00:00",
                    "aaa",
                    "CNBC",
                    title="Career Interview",
                ),
            )
        )
        results = store.top_videos(keyword="Career Interview")
        assert results and results[0].video_id == "aaa"
        assert results[0].count == 2

    def test_activity_summary_respects_keyword(self) -> None:
        results = _store().activity_summary(keyword="career")
        by = {(r.event_type, r.source): r.count for r in results}
        # video_watch rows matching 'career' (title + channel) == 2.
        assert by[(EVENT_TYPE_VIDEO_WATCH, "youtube")] == 2
        assert by[(EVENT_TYPE_YOUTUBE_SEARCH, "youtube")] == 1

    def test_activity_by_bucket_respects_keyword(self) -> None:
        results = _store().activity_by_bucket(keyword="career", bucket="month")
        counts = {r.bucket: r.count for r in results}
        assert counts == {"2026-01": 3}  # 3 career-matching events in Jan

    def test_aggregation_deterministic_ordering_with_keyword(self) -> None:
        store = _store()
        first = store.top_videos(keyword="career")
        second = store.top_videos(keyword="career")
        assert first == second
        first_summary = store.activity_summary(keyword="career")
        second_summary = store.activity_summary(keyword="career")
        assert first_summary == second_summary

    def test_omitted_keyword_preserves_behavior(self) -> None:
        store = _store()
        assert store.top_videos() == store.top_videos(keyword=None)
        assert store.activity_summary() == store.activity_summary(keyword=None)
        assert store.top_search_queries() == store.top_search_queries(keyword=None)
        assert store.activity_by_bucket(bucket="month") == store.activity_by_bucket(
            bucket="month", keyword=None
        )
