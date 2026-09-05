"""Tests for EventStore SQLite persistence."""

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    Event,
    compute_event_id,
)
from personal_ai.storage import EventQuery, EventStore, connect_database


def _make_event(
    event_type: str = EVENT_TYPE_URL_VISIT,
    event_time: str = "2026-01-15T10:00:00+00:00",
    source: str = "chrome_history",
    url: str = "https://example.org",
    **overrides: object,
) -> Event:
    values: dict[str, object] = {
        "id": compute_event_id(source, event_type, event_time, url),
        "event_type": event_type,
        "event_time": event_time,
        "source": source,
        "url": url,
        "metadata": {},
    }
    values.update(overrides)
    return Event(**values)  # type: ignore[arg-type]


class TestEventStore:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_save_and_get_event(self) -> None:
        event = _make_event()
        assert self.store.save_event(event) is True
        assert self.store.get_event(event.id) == event

    def test_save_is_idempotent(self) -> None:
        event = _make_event()
        assert self.store.save_event(event) is True
        assert self.store.save_event(event) is False
        assert self.store.count_events() == 1

    def test_batch_save_counts_new_rows(self) -> None:
        events = (
            _make_event(event_time="2026-01-15T10:00:00+00:00", url="https://a.org"),
            _make_event(event_time="2026-01-16T10:00:00+00:00", url="https://b.org"),
        )
        count = self.store.save_events(events)
        assert count == 2
        # Re-saving reports zero new
        assert self.store.save_events(events) == 0

    def test_multiple_events_same_url_distinct_ids(self) -> None:
        url = "https://example.org/page"
        first = _make_event(event_time="2026-01-15T10:00:00+00:00", url=url)
        second = _make_event(event_time="2026-01-16T10:00:00+00:00", url=url)
        self.store.save_events((first, second))
        assert self.store.count_events() == 2

    def test_metadata_round_trip(self) -> None:
        event = _make_event(
            metadata={"page_transition_qualifier": "TYPED", "favicon_url": "x"}
        )
        self.store.save_event(event)
        assert self.store.get_event(event.id).metadata == {
            "page_transition_qualifier": "TYPED",
            "favicon_url": "x",
        }

    def test_nullable_fields_round_trip(self) -> None:
        event = _make_event(
            title="T", search_query=None, channel_name=None, duration_seconds=None
        )
        self.store.save_event(event)
        stored = self.store.get_event(event.id)
        assert stored.title == "T"
        assert stored.search_query is None
        assert stored.channel_name is None
        assert stored.duration_seconds is None

    def test_update_preserves_id_on_conflict(self) -> None:
        event = _make_event(title="old")
        self.store.save_event(event)
        updated = _make_event(title="new")  # same id
        assert self.store.save_event(updated) is False
        assert self.store.get_event(event.id).title == "new"


class TestEventStoreQueries:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)
        # Three visits on different dates + a search event
        self.store.save_events(
            (
                _make_event(
                    event_time="2026-01-10T08:00:00+00:00", url="https://a.org"
                ),
                _make_event(
                    event_time="2026-01-20T08:00:00+00:00", url="https://b.org"
                ),
                _make_event(
                    event_time="2026-02-10T08:00:00+00:00", url="https://c.org"
                ),
                _make_event(
                    event_type=EVENT_TYPE_SEARCH_QUERY,
                    event_time="2026-01-25T12:00:00+00:00",
                    url="https://www.google.de/search?q=bcg",
                    search_query="bcg",
                ),
            )
        )

    def teardown_method(self) -> None:
        self.connection.close()

    def test_time_range_inclusive(self) -> None:
        results = self.store.search(
            EventQuery(
                start_time="2026-01-15T00:00:00+00:00",
                end_time="2026-01-31T00:00:00+00:00",
            )
        )
        times = {e.event_time for e in results}
        assert times == {
            "2026-01-20T08:00:00+00:00",
            "2026-01-25T12:00:00+00:00",
        }

    def test_source_filter(self) -> None:
        self.store.save_event(
            _make_event(
                event_time="2026-01-30T00:00:00+00:00",
                url="https://other.org",
                source="youtube",
                event_type=EVENT_TYPE_URL_VISIT,
            )
        )
        results = self.store.search(EventQuery(source="chrome_history"))
        assert len(results) == 4
        assert all(e.source == "chrome_history" for e in results)

    def test_event_type_filter(self) -> None:
        results = self.store.search(EventQuery(event_type=EVENT_TYPE_SEARCH_QUERY))
        assert len(results) == 1
        assert results[0].search_query == "bcg"

    def test_combined_filters(self) -> None:
        results = self.store.search(
            EventQuery(
                source="chrome_history",
                event_type=EVENT_TYPE_URL_VISIT,
                start_time="2026-01-15T00:00:00+00:00",
                end_time="2026-02-01T00:00:00+00:00",
            )
        )
        assert [e.url for e in results] == ["https://b.org"]

    def test_empty_query_returns_all_ordered(self) -> None:
        results = self.store.search(EventQuery(limit=100))
        assert len(results) == 4
        times = [e.event_time for e in results]
        assert times == sorted(times)

    def test_limit_respected(self) -> None:
        results = self.store.search(EventQuery(limit=2))
        assert len(results) == 2

    def test_list_events_filtered(self) -> None:
        results = self.store.list_events(source="chrome_history", limit=100)
        assert len(results) == 4

    def test_list_events_pages_with_offset(self) -> None:
        page_one = self.store.list_events(
            source="chrome_history", event_type=EVENT_TYPE_URL_VISIT, limit=2
        )
        page_two = self.store.list_events(
            source="chrome_history",
            event_type=EVENT_TYPE_URL_VISIT,
            limit=2,
            offset=2,
        )
        all_events = self.store.list_events(
            source="chrome_history", event_type=EVENT_TYPE_URL_VISIT, limit=100
        )

        assert len(page_one) == 2
        assert len(page_two) == 1
        assert page_one + page_two == all_events
        assert (
            self.store.list_events(
                source="chrome_history",
                event_type=EVENT_TYPE_URL_VISIT,
                limit=2,
                offset=4,
            )
            == ()
        )

    def test_count_by_type(self) -> None:
        assert self.store.count_events(event_type=EVENT_TYPE_SEARCH_QUERY) == 1
        assert self.store.count_events(event_type=EVENT_TYPE_URL_VISIT) == 3
        assert self.store.count_events(source="chrome_history") == 4
