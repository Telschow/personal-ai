"""Tests for YouTube history event ingestion orchestration."""

from pathlib import Path

from personal_ai.event_ingestion import ingest_youtube_history
from personal_ai.events.models import (
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
)
from personal_ai.storage import EventStore, connect_database
from personal_ai.storage.events import EventQuery

CELL = (
    '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
    "{inner}</div>"
)


def _write_history(directory: Path, name: str, cells: list[str]) -> Path:
    doc = "<html><body>" + "".join(cells) + "</body></html>"
    path = directory / name
    path.write_text(doc, encoding="utf-8")
    return path


def _watch(video_id: str, ts: str) -> str:
    return CELL.format(
        inner=f'Has visto <a href="https://www.youtube.com/watch?v={video_id}">T</a>'
        f"<br>{ts}<br>"
    )


def _search(query: str, ts: str) -> str:
    return CELL.format(
        inner=(
            f'Buscaste <a href="https://www.youtube.com/results?search_query={query}">'
            f"{query}</a><br>{ts}<br>"
        )
    )


def _visit(ts: str) -> str:
    return CELL.format(
        inner=f'Has visitado <a href="https://www.google.com/url?q=x">x</a><br>{ts}<br>'
    )


class TestIngestYouTubeHistory:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_basic_ingestion_counts(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            "historial-de-reproducciones.html",
            [
                _watch("AAA", "7 mar 2016, 22:11:49 CEST"),
                _watch("BBB", "8 mar 2016, 10:00:00 CEST"),
            ],
        )
        _write_history(
            tmp_path,
            "historial-de-búsqueda.html",
            [_search("cats", "13 ago 2026, 20:30:26 CEST")],
        )
        summary = ingest_youtube_history(tmp_path, self.store)
        assert summary.files_discovered == 2
        assert summary.records_discovered == 3
        assert summary.events_stored == 3
        assert summary.video_watches == 2
        assert summary.youtube_searches == 1
        assert summary.skipped == 0
        assert summary.source_type == "youtube"

    def test_idempotent_reingestion(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            "h.html",
            [_watch("AAA", "7 mar 2016, 22:11:49 CEST")],
        )
        first = ingest_youtube_history(tmp_path, self.store)
        second = ingest_youtube_history(tmp_path, self.store)
        assert first.events_stored == 1
        assert second.events_stored == 0
        assert self.store.count_events() == 1
        assert self.store.count_events(source="youtube") == 1

    def test_repeated_watch_is_distinct_and_idempotent(self, tmp_path: Path) -> None:
        # The same video watched at two different times produces two events.
        _write_history(
            tmp_path,
            "h.html",
            [
                _watch("AAA", "7 mar 2016, 22:11:49 CEST"),
                _watch("AAA", "8 mar 2016, 10:00:00 CEST"),
            ],
        )
        summary = ingest_youtube_history(tmp_path, self.store)
        assert summary.events_stored == 2
        assert self.store.count_events() == 2
        # Re-ingesting is a no-op.
        assert ingest_youtube_history(tmp_path, self.store).events_stored == 0
        assert self.store.count_events() == 2

    def test_cross_file_duplicate_watch_deduplicated(self, tmp_path: Path) -> None:
        # The same watch (same URL + timestamp) in both files collapses to one.
        _write_history(
            tmp_path,
            "a.html",
            [_watch("AAA", "7 mar 2016, 22:11:49 CEST")],
        )
        _write_history(
            tmp_path,
            "b.html",
            [_watch("AAA", "7 mar 2016, 22:11:49 CEST")],
        )
        summary = ingest_youtube_history(tmp_path, self.store)
        assert summary.events_stored == 1
        assert self.store.count_events() == 1

    def test_skipped_records_reported_with_reasons(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            "h.html",
            [
                _watch("AAA", "7 mar 2016, 22:11:49 CEST"),
                _visit("1 nov 2025, 16:31:23 CEST"),
            ],
        )
        summary = ingest_youtube_history(tmp_path, self.store)
        assert summary.records_discovered == 2
        assert summary.events_stored == 1
        assert summary.skipped == 1
        assert summary.skipped_reasons == {"ad_or_tracking_visit": 1}

    def test_empty_directory(self, tmp_path: Path) -> None:
        summary = ingest_youtube_history(tmp_path, self.store)
        assert summary.files_discovered == 0
        assert summary.events_stored == 0
        assert summary.video_watches == 0
        assert summary.youtube_searches == 0

    def test_stored_events_queryable_by_type_and_source(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            "h.html",
            [
                _watch("AAA", "7 mar 2016, 22:11:49 CEST"),
                _search("cats", "13 ago 2026, 20:30:26 CEST"),
            ],
        )
        ingest_youtube_history(tmp_path, self.store)
        watches = self.store.search(
            EventQuery(event_type=EVENT_TYPE_VIDEO_WATCH, source="youtube")
        )
        assert len(watches) == 1
        assert watches[0].metadata["video_id"] == "AAA"
        searches = self.store.search(
            EventQuery(event_type=EVENT_TYPE_YOUTUBE_SEARCH, source="youtube")
        )
        assert len(searches) == 1
        assert searches[0].search_query == "cats"

    def test_stored_events_preserve_channel_and_metadata(self, tmp_path: Path) -> None:
        cell = CELL.format(
            inner=(
                'Has visto <a href="https://www.youtube.com/watch?v=AAA">T</a>'
                '<br><a href="https://www.youtube.com/channel/UCAbc">Podemos</a>'
                "<br>7 mar 2016, 22:11:49 CEST<br>"
            )
        )
        _write_history(tmp_path, "h.html", [cell])
        ingest_youtube_history(tmp_path, self.store)
        events = self.store.search(EventQuery(event_type=EVENT_TYPE_VIDEO_WATCH))
        assert len(events) == 1
        assert events[0].channel_name == "Podemos"
        assert events[0].metadata["channel_id"] == "UCAbc"
