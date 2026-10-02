"""Tests for Chrome history parsing primitives."""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    compute_event_id,
)
from personal_ai.sources.chrome_history import (
    SOURCE_TYPE,
    ChromeHistoryParseError,
    ChromeHistoryResult,
    chrome_timestamp_to_iso,
    event_from_record,
    extract_search_query,
    normalize_history_data,
    parse_history,
)


class TestTimestampConversion:
    def test_microseconds_since_epoch_to_utc_iso(self) -> None:
        # 1787337555640344 microseconds = 2026-08-21T18:39:15.640344 UTC
        assert (
            chrome_timestamp_to_iso(1787337555640344)
            == "2026-08-21T18:39:15.640344+00:00"
        )

    def test_zero_precision(self) -> None:
        assert chrome_timestamp_to_iso(0) == "1970-01-01T00:00:00+00:00"

    def test_converts_to_utc_not_local(self) -> None:
        # 1.0e15 microseconds = 2001-09-09T01:46:40 UTC regardless of tz
        result = chrome_timestamp_to_iso(1_000_000_000_000_000)
        assert result == "2001-09-09T01:46:40+00:00"
        assert result.endswith("+00:00")

    def test_missing_timestamp_returns_none(self) -> None:
        assert chrome_timestamp_to_iso(None) is None

    def test_boolean_timestamp_returns_none(self) -> None:
        assert chrome_timestamp_to_iso(True) is None

    def test_string_timestamp_returns_none(self) -> None:
        assert chrome_timestamp_to_iso("1787337555640344") is None

    def test_negative_timestamp(self) -> None:
        assert chrome_timestamp_to_iso(-1000000) == "1969-12-31T23:59:59+00:00"

    def test_integer_timestamp(self) -> None:
        assert chrome_timestamp_to_iso(0) is not None


class TestURLVisitParsing:
    def test_builds_url_visit_event(self) -> None:
        event = event_from_record(
            {
                "title": "Nextcloud",
                "url": "https://pc.tailnet.example/settings/apps",
                "time_usec": 1787337555640344,
                "page_transition_qualifier": "CLIENT_REDIRECT",
                "client_id": "abc==",
            }
        )
        assert event is not None
        assert event.event_type == EVENT_TYPE_URL_VISIT
        assert event.search_query is None
        assert event.url == "https://pc.tailnet.example/settings/apps"
        assert event.title == "Nextcloud"
        assert event.event_time == "2026-08-21T18:39:15.640344+00:00"
        assert event.source == SOURCE_TYPE

    def test_sets_deterministic_id(self) -> None:
        event = event_from_record(
            {
                "url": "https://example.org/page",
                "time_usec": 1787337555640344,
            }
        )
        assert event is not None
        assert event.id == compute_event_id(
            SOURCE_TYPE,
            EVENT_TYPE_URL_VISIT,
            "2026-08-21T18:39:15.640344+00:00",
            "https://example.org/page",
        )

    def test_missing_url_returns_none(self) -> None:
        assert event_from_record({"time_usec": 1787337555640344}) is None

    def test_empty_url_returns_none(self) -> None:
        assert event_from_record({"url": "", "time_usec": 1787337555640344}) is None

    def test_missing_timestamp_returns_none(self) -> None:
        assert event_from_record({"url": "https://example.org"}) is None

    def test_non_dict_record_returns_none(self) -> None:
        assert event_from_record([]) is None  # type: ignore[arg-type]

    def test_empty_title_becomes_none(self) -> None:
        event = event_from_record(
            {"url": "https://example.org", "time_usec": 1787337555640344, "title": ""}
        )
        assert event is not None
        assert event.title is None


class TestGoogleSearchQueryExtraction:
    def test_extracts_query_from_google_de(self) -> None:
        assert (
            extract_search_query(
                "https://www.google.de/search?q=example+corp+interview+pdf"
            )
            == "example corp interview pdf"
        )

    def test_extracts_query_from_google_com(self) -> None:
        assert (
            extract_search_query("https://www.google.com/search?q=people+test+systems")
            == "people test systems"
        )

    def test_extracts_decode_encoded_query(self) -> None:
        assert (
            extract_search_query(
                "https://www.google.de/search?q=mapa%20de%20Feringasee"
            )
            == "mapa de Feringasee"
        )

    def test_extracts_double_encoded_query(self) -> None:
        assert (
            extract_search_query(
                "https://www.google.com/search?q=munich%2520a%2520mexico"
            )
            == "munich a mexico"
        )

    def test_non_google_url_returns_none(self) -> None:
        assert extract_search_query("https://duckduckgo.com/?q=google+takeout") is None

    def test_google_url_that_is_not_search_returns_none(self) -> None:
        assert (
            extract_search_query(
                "https://www.google.com/travel/flights/search?q=mexico"
            )
            is None
        )

    def test_google_search_without_q_returns_none(self) -> None:
        assert extract_search_query("https://www.google.com/search?hl=de") is None

    def test_google_search_with_empty_q_returns_none(self) -> None:
        assert extract_search_query("https://www.google.com/search?q=") is None

    def test_mail_search_url_is_not_web_search(self) -> None:
        assert extract_search_query("https://mail.google.com/#search/tanz") is None

    def test_google_docs_is_not_search(self) -> None:
        assert (
            extract_search_query("https://www.google.com/document/d/abc/edit") is None
        )


class TestSearchQueryClassification:
    def _record(self, url: str) -> dict[str, object]:
        return {"url": url, "time_usec": 1787337555640344}

    def test_google_search_becomes_search_query_event(self) -> None:
        event = event_from_record(
            self._record("https://www.google.de/search?q=nifedipine+deutschland")
        )
        assert event is not None
        assert event.event_type == EVENT_TYPE_SEARCH_QUERY
        assert event.search_query == "nifedipine deutschland"

    def test_non_google_url_reverts_to_url_visit(self) -> None:
        event = event_from_record(self._record("https://www.linkedin.com/in/abc"))
        assert event is not None
        assert event.event_type == EVENT_TYPE_URL_VISIT
        assert event.search_query is None

    def test_google_non_search_reverts_to_url_visit(self) -> None:
        event = event_from_record(
            self._record("https://www.google.com/travel/flights?q=mexico")
        )
        assert event is not None
        assert event.event_type == EVENT_TYPE_URL_VISIT


class TestMetadataPreservation:
    def test_preserves_provider_fields(self) -> None:
        event = event_from_record(
            {
                "url": "https://example.org",
                "time_usec": 1787337555640344,
                "title": "T",
                "page_transition_qualifier": "TYPED",
                "favicon_url": "https://example.org/favicon.ico",
                "client_id": "abc==",
            }
        )
        assert event is not None
        assert event.metadata["page_transition_qualifier"] == "TYPED"
        assert event.metadata["favicon_url"] == "https://example.org/favicon.ico"
        assert event.metadata["client_id"] == "abc=="

    def test_empty_provider_fields_are_omitted(self) -> None:
        event = event_from_record(
            {
                "url": "https://example.org",
                "time_usec": 1787337555640344,
                "favicon_url": "",
            }
        )
        assert event is not None
        assert "favicon_url" not in event.metadata


class TestDuplicateAndMultipleEvents:
    def test_same_url_multiple_times_yields_distinct_ids(self) -> None:
        url = "https://example.org/page"
        first = event_from_record({"url": url, "time_usec": 1787337555000000})
        second = event_from_record({"url": url, "time_usec": 1787337555640344})
        assert first is not None and second is not None
        assert first.id != second.id
        assert first.url == second.url

    def test_identical_records_yield_identical_ids(self) -> None:
        record = {"url": "https://example.org", "time_usec": 1787337555640344}
        first = event_from_record(dict(record))
        second = event_from_record(dict(record))
        assert first is not None and second is not None
        assert first.id == second.id
        assert first == second


class TestParseHistory:
    def test_parses_browser_history_payload(self) -> None:
        payload = (
            b'{"Browser History": ['
            b'{"url": "https://a.org", "time_usec": 1787337555640344, "title": "A"},'
            b'{"url": "https://www.google.de/search?q=cats", "time_usec": 1000000, "title": "S"}'
            b"]}"
        )
        result = parse_history(payload)
        assert len(result.events) == 2
        types = {e.event_type for e in result.events}
        assert types == {EVENT_TYPE_URL_VISIT, EVENT_TYPE_SEARCH_QUERY}
        assert result.skipped == 0

    def test_skips_malformed_records(self) -> None:
        payload = (
            b'{"Browser History": ['
            b'{"url": "https://a.org", "time_usec": 1787337555640344},'
            b'{"url": "https://b.org"},'
            b'{"time_usec": 1000000},'
            b'"not-an-object"'
            b"]}"
        )
        result = parse_history(payload)
        assert len(result.events) == 1
        assert result.skipped == 3

    def test_events_are_deterministically_ordered(self) -> None:
        payload = (
            b'{"Browser History": ['
            b'{"url": "https://b.org", "time_usec": 2000000},'
            b'{"url": "https://a.org", "time_usec": 1000000}'
            b"]}"
        )
        result = parse_history(payload)
        times = [e.event_time for e in result.events]
        assert times == sorted(times)

    def test_non_browser_history_shape_yields_no_events(self) -> None:
        result = parse_history(b'{"Settings": []}')
        assert result.events == ()
        assert result.skipped == 0

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ChromeHistoryParseError):
            parse_history(b"not json")

    def test_top_level_non_dict_yields_no_events(self) -> None:
        result = normalize_history_data([])
        assert result.events == ()

    def test_result_is_frozen_and_counted(self) -> None:
        result = parse_history(b'{"Browser History": []}')
        assert isinstance(result, ChromeHistoryResult)
        assert result.events == ()
        assert result.skipped == 0
