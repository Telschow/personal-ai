"""Tests for Chrome history event ingestion orchestration."""

import json
from pathlib import Path

from personal_ai.event_ingestion import ingest_chrome_history
from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
)
from personal_ai.storage import EventQuery, EventStore, connect_database

BROWSER_HISTORY_KEY = "Browser History"


def _write_history(directory: Path, records: list[dict[str, object]]) -> Path:
    path = directory / "Historial.json"
    path.write_text(json.dumps({BROWSER_HISTORY_KEY: records}))
    return path


def _visit(
    url: str, time_usec: int | None = None, **extra: object
) -> dict[str, object]:
    record: dict[str, object] = {"url": url}
    if time_usec is not None:
        record["time_usec"] = time_usec
    record.update(extra)
    return record


class TestIngestChromeHistory:
    def setup_method(self) -> None:
        self.connection = connect_database(":memory:")
        self.store = EventStore(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_basic_ingestion_counts(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            [
                _visit("https://a.org", 1000000),
                _visit("https://b.org", 2000000),
                _visit(
                    "https://www.google.de/search?q=example+corp",
                    3000000,
                ),
            ],
        )
        summary = ingest_chrome_history(tmp_path, self.store)
        assert summary.files_discovered == 1
        assert summary.records_discovered == 3
        assert summary.events_stored == 3
        assert summary.search_queries == 1
        assert summary.url_visits == 2
        assert summary.skipped == 0
        assert summary.source_type == "chrome_history"

    def test_idempotent_reingestion(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            [_visit("https://a.org", 1000000), _visit("https://b.org", 2000000)],
        )
        first = ingest_chrome_history(tmp_path, self.store)
        second = ingest_chrome_history(tmp_path, self.store)
        assert first.events_stored == 2
        assert second.events_stored == 0
        assert self.store.count_events() == 2

    def test_skipped_records_reported(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            [
                _visit("https://a.org", 1000000),
                _visit("https://b.org"),  # missing timestamp
                _visit(""),  # missing url
            ],
        )
        summary = ingest_chrome_history(tmp_path, self.store)
        assert summary.records_discovered == 3
        assert summary.events_stored == 1
        assert summary.skipped == 2
        assert summary.skipped_reasons == {
            "missing_or_invalid_timestamp": 1,
            "missing_url": 1,
        }

    def test_empty_directory(self, tmp_path: Path) -> None:
        summary = ingest_chrome_history(tmp_path, self.store)
        assert summary.files_discovered == 0
        assert summary.events_stored == 0
        assert summary.search_queries == 0

    def test_stored_events_queryable(self, tmp_path: Path) -> None:
        _write_history(
            tmp_path,
            [
                _visit("https://a.org", 1000000),
                _visit("https://www.google.de/search?q=tanz", 2000000),
            ],
        )
        ingest_chrome_history(tmp_path, self.store)
        searches = self.store.search(EventQuery(event_type=EVENT_TYPE_SEARCH_QUERY))
        assert len(searches) == 1
        assert searches[0].search_query == "tanz"
        visits = self.store.search(EventQuery(event_type=EVENT_TYPE_URL_VISIT))
        assert len(visits) == 1
