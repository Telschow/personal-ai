"""Tests for YouTube history parsing primitives."""

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
    compute_event_id,
)
from personal_ai.sources.youtube_history import (
    SOURCE_TYPE,
    YouTubeHistoryParseError,
    YouTubeHistoryResult,
    event_from_cell,
    extract_search_query,
    is_history_export,
    normalize_history_html,
    youtube_timestamp_to_iso,
)

CELL_SUFFIX = "</div>"


def _cell(inner: str) -> str:
    return (
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        + inner
        + CELL_SUFFIX
    )


def _watch(
    video_id: str,
    title: str,
    timestamp: str,
    *,
    channel_url: str | None = None,
    channel_name: str | None = None,
) -> str:
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    inner = f'Has visto <a href="{video_url}">{title}</a>'
    if channel_url is not None:
        inner += f'<br><a href="{channel_url}">{channel_name}</a>'
    inner += f"<br>{timestamp}<br>"
    return _cell(inner)


def _search(query: str, timestamp: str) -> str:
    url = f"https://www.youtube.com/results?search_query={query}"
    return _cell(
        f'Buscaste <a href="{url}">{query.replace("+", " ")}</a><br>{timestamp}<br>'
    )


class TestTimestampConversion:
    def test_cest_to_utc(self) -> None:
        # 22:18 CEST = UTC+2 -> 20:18 UTC.
        assert youtube_timestamp_to_iso(11, "sept", 2023, 22, 18, 26, "CEST") == (
            "2023-09-11T20:18:26+00:00"
        )

    def test_cet_to_utc(self) -> None:
        assert youtube_timestamp_to_iso(1, "nov", 2025, 16, 31, 23, "CET") == (
            "2025-11-01T15:31:23+00:00"
        )

    def test_all_spanish_months(self) -> None:
        months = {
            "ene": 1,
            "feb": 2,
            "mar": 3,
            "abr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "ago": 8,
            "sept": 9,
            "oct": 10,
            "nov": 11,
            "dic": 12,
        }
        for name, month in months.items():
            result = youtube_timestamp_to_iso(15, name, 2023, 12, 0, 0, "CEST")
            assert result == f"2023-{month:02d}-15T10:00:00+00:00"

    def test_unknown_month_returns_none(self) -> None:
        assert youtube_timestamp_to_iso(1, "xyz", 2023, 12, 0, 0, "CEST") is None

    def test_unknown_timezone_returns_none(self) -> None:
        assert youtube_timestamp_to_iso(1, "ene", 2023, 12, 0, 0, "XYZ") is None

    def test_container_whitespace_day_is_handled(self) -> None:
        # Single-digit hour and day are handled.
        result = youtube_timestamp_to_iso(5, "may", 2015, 9, 3, 4, "CEST")
        assert result == "2015-05-05T07:03:04+00:00"

    def test_invalid_calendar_date_returns_none(self) -> None:
        assert youtube_timestamp_to_iso(31, "feb", 2023, 12, 0, 0, "CEST") is None


class TestVideoWatchParsing:
    def test_builds_video_watch_event_with_channel(self) -> None:
        event, reason = event_from_cell(
            _watch(
                "Ob5gdl9BBdM",
                '"Pedro"',
                "7 mar 2016, 22:11:49 CEST",
                channel_url="https://www.youtube.com/channel/UCtK7s89RJ9X9Nv9EQCMu9Lg",
                channel_name="Podemos",
            )
        )
        assert reason is None
        assert event is not None
        assert event.event_type == EVENT_TYPE_VIDEO_WATCH
        assert event.event_time == "2016-03-07T20:11:49+00:00"
        assert event.url == "https://www.youtube.com/watch?v=Ob5gdl9BBdM"
        assert event.title == '"Pedro"'
        assert event.channel_name == "Podemos"
        assert event.metadata["video_id"] == "Ob5gdl9BBdM"
        assert event.metadata["channel_id"] == "UCtK7s89RJ9X9Nv9EQCMu9Lg"
        assert event.source == SOURCE_TYPE

    def test_watch_without_channel(self) -> None:
        event, reason = event_from_cell(
            _watch("hrcxTDHGRPk", "Heineken DE", "12 ago 2026, 21:07:55 CEST")
        )
        assert reason is None and event is not None
        assert event.channel_name is None
        assert "channel_id" not in event.metadata
        assert event.metadata["video_id"] == "hrcxTDHGRPk"

    def test_deterministic_id(self) -> None:
        cell = _watch("ID", "Title", "7 mar 2016, 22:11:49 CEST")
        event, _ = event_from_cell(cell)
        assert event is not None
        assert event.id == compute_event_id(
            SOURCE_TYPE,
            EVENT_TYPE_VIDEO_WATCH,
            "2016-03-07T20:11:49+00:00",
            "https://www.youtube.com/watch?v=ID",
        )

    def test_url_fallback_title_becomes_none(self) -> None:
        url = "https://www.youtube.com/watch?v=qYsxZOVfYKI"
        cell = _cell(
            f'Has visto <a href="{url}">{url}</a><br>7 mar 2016, 22:18:26 CEST<br>'
        )
        event, reason = event_from_cell(cell)
        assert reason is None and event is not None
        assert event.title is None
        assert event.event_type == EVENT_TYPE_VIDEO_WATCH

    def test_html_entities_decoded_in_title(self) -> None:
        cell = _cell(
            'Has visto <a href="https://www.youtube.com/watch?v=ID">'
            "A &amp; B &#39;quotes&#39;</a><br>7 mar 2016, 22:18:26 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert reason is None and event is not None
        assert event.title == "A & B 'quotes'"

    def test_watched_non_video_post_is_skipped(self) -> None:
        cell = _cell(
            'Has visto <a href="https://www.youtube.com/post/UgkAbc">Some post</a>'
            "<br>7 mar 2016, 22:18:26 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "watched_non_video"

    def test_missing_timestamp_skipped(self) -> None:
        cell = _cell('Has visto <a href="https://www.youtube.com/watch?v=ID">T</a>')
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "missing_or_invalid_timestamp"

    def test_missing_url_skipped(self) -> None:
        cell = _cell("Has visto <br>7 mar 2016, 22:18:26 CEST<br>")
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "missing_url"

    def test_nbsp_after_verb_is_tolerated(self) -> None:
        cell = (
            '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
            'Has visto\xa0<a href="https://www.youtube.com/watch?v=ID">T</a>'
            "<br>7 mar 2016, 22:18:26 CEST<br></div>"
        )
        event, reason = event_from_cell(cell)
        assert reason is None and event is not None
        assert event.event_type == EVENT_TYPE_VIDEO_WATCH


class TestYouTubeSearchParsing:
    def test_builds_youtube_search_event(self) -> None:
        event, reason = event_from_cell(
            _search("powerlifting", "13 ago 2026, 20:30:26 CEST")
        )
        assert reason is None and event is not None
        assert event.event_type == EVENT_TYPE_YOUTUBE_SEARCH
        assert event.event_time == "2026-08-13T18:30:26+00:00"
        assert event.search_query == "powerlifting"
        assert event.url == "https://www.youtube.com/results?search_query=powerlifting"
        assert event.title is None
        assert event.channel_name is None

    def test_decoded_multiword_query(self) -> None:
        event, reason = event_from_cell(
            _search("mapa%20de%20Feringasee", "13 ago 2026, 20:30:26 CEST")
        )
        assert reason is None and event is not None
        assert event.search_query == "mapa de Feringasee"

    def test_identical_search_repeated_yields_distinct_ids_by_time(self) -> None:
        first, _ = event_from_cell(_search("cats", "1 ene 2026, 10:00:00 CEST"))
        second, _ = event_from_cell(_search("cats", "1 ene 2026, 10:05:00 CEST"))
        assert first is not None and second is not None
        assert first.search_query == second.search_query == "cats"
        assert first.id != second.id

    def test_search_url_without_query_is_skipped(self) -> None:
        cell = _cell(
            'Buscaste <a href="https://www.youtube.com/results">empty</a>'
            "<br>13 ago 2026, 20:30:26 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "missing_search_query"


class TestQueryExtraction:
    def test_extracts_query(self) -> None:
        assert (
            extract_search_query(
                "https://www.youtube.com/results?search_query=black+coffee"
            )
            == "black coffee"
        )

    def test_decodes_encoded_query(self) -> None:
        assert (
            extract_search_query(
                "https://www.youtube.com/results?search_query=mapa%20Feringasee"
            )
            == "mapa Feringasee"
        )

    def test_non_youtube_url_returns_none(self) -> None:
        assert extract_search_query("https://duckduckgo.com/?q=cats") is None

    def test_missing_query_returns_none(self) -> None:
        assert extract_search_query("https://www.youtube.com/results?hl=de") is None


class TestRepeatAndDuplicateEvents:
    def test_same_video_watched_twice_yields_distinct_ids(self) -> None:
        first, _ = event_from_cell(_watch("ID", "Title", "7 mar 2016, 22:11:49 CEST"))
        second, _ = event_from_cell(_watch("ID", "Title", "8 mar 2016, 22:11:49 CEST"))
        assert first is not None and second is not None
        assert first.url == second.url
        assert first.id != second.id

    def test_identical_watch_records_yield_identical_ids(self) -> None:
        cell = _watch("ID", "Title", "7 mar 2016, 22:11:49 CEST")
        first, _ = event_from_cell(cell)
        second, _ = event_from_cell(cell)
        assert first is not None and second is not None
        assert first.id == second.id
        assert first == second

    def test_different_videos_same_time_yield_distinct_ids(self) -> None:
        first, _ = event_from_cell(_watch("AAA", "A", "7 mar 2016, 22:11:49 CEST"))
        second, _ = event_from_cell(_watch("BBB", "B", "7 mar 2016, 22:11:49 CEST"))
        assert first is not None and second is not None
        assert first.id != second.id


class TestNonWatchSearchCells:
    def test_ad_tracking_visit_skipped(self) -> None:
        cell = _cell(
            'Has visitado <a href="https://www.google.com/url?q=...">x</a>'
            "<br>1 nov 2025, 16:31:23 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "ad_or_tracking_visit"

    def test_survey_response_skipped(self) -> None:
        cell = _cell(
            "Pregunta de encuesta respondida<br>Pregunta: q<br>Respuesta: r"
            "<br>16 may 2026, 20:22:10 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "survey_response"

    def test_ads_seen_on_page_skipped(self) -> None:
        cell = _cell(
            "Anuncios vistos en la página Inicio de YouTube"
            "<br>20 mar 2025, 20:42:52 CEST<br>"
        )
        event, reason = event_from_cell(cell)
        assert event is None
        assert reason == "ads_seen_on_page"


class TestExportDiscovery:
    def test_history_export_shape_detected(self) -> None:
        payload = (
            '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
            "x</div>"
        )
        assert is_history_export(payload)

    def test_unrelated_html_not_detected(self) -> None:
        assert not is_history_export("<html><body>hello</body></html>")


class TestParseHistoryHtml:
    WATCH = (
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        'Has visto <a href="https://www.youtube.com/watch?v=AAA">A</a>'
        "<br>7 mar 2016, 22:11:49 CEST<br></div>"
    )
    SEARCH = (
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        'Buscaste <a href="https://www.youtube.com/results?search_query=cats">cats</a>'
        "<br>13 ago 2026, 20:30:26 CEST<br></div>"
    )
    VISIT = (
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        'Has visitado <a href="https://www.google.com/url?q=...">x</a>'
        "<br>1 nov 2025, 16:31:23 CEST<br></div>"
    )

    def test_parses_mixed_history_payload(self) -> None:
        payload = (self.WATCH + self.SEARCH + self.VISIT).encode()
        result = normalize_history_html(payload)
        assert len(result.events) == 2
        types = {e.event_type for e in result.events}
        assert types == {EVENT_TYPE_VIDEO_WATCH, EVENT_TYPE_YOUTUBE_SEARCH}
        assert result.skipped == 1
        assert result.skipped_reasons == {"ad_or_tracking_visit": 1}

    def test_events_are_deterministically_ordered(self) -> None:
        # Out-of-order payload: later timestamp first in the document.
        payload = (self.WATCH + self.SEARCH).encode()
        result = normalize_history_html(payload)
        times = [e.event_time for e in result.events]
        assert times == sorted(times)

    def test_result_is_frozen_and_counted(self) -> None:
        result = normalize_history_html(self.SEARCH.encode())
        assert isinstance(result, YouTubeHistoryResult)
        assert result.skipped == 0

    def test_invalid_utf8_raises(self) -> None:
        with pytest.raises(YouTubeHistoryParseError):
            normalize_history_html(b"\xff\xfe")
