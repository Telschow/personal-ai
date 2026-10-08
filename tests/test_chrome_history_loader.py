"""Tests for the Chrome history loader."""

import json
from pathlib import Path

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
)
from personal_ai.sources.chrome_history_loader import (
    ChromeHistoryLoader,
    ChromeHistoryLoadError,
)

BROWSER_HISTORY_KEY = "Browser History"


def _history_payload(records: list[dict[str, object]]) -> bytes:
    return json.dumps({BROWSER_HISTORY_KEY: records}).encode("utf-8")


def _visit(
    url: str, time_usec: int | None = None, **extra: object
) -> dict[str, object]:
    record: dict[str, object] = {"url": url}
    if time_usec is not None:
        record["time_usec"] = time_usec
    record.update(extra)
    return record


class TestChromeHistoryLoaderDiscovery:
    def test_discovers_history_json(self, tmp_path: Path) -> None:
        (tmp_path / "Historial.json").write_bytes(
            _history_payload(
                [_visit("https://a.org", 1000000), _visit("https://b.org", 2000000)]
            )
        )
        loader = ChromeHistoryLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 1
        assert files[0].name == "Historial.json"

    def test_ignores_non_history_json_sidecars(self, tmp_path: Path) -> None:
        (tmp_path / "Historial.json").write_bytes(
            _history_payload([_visit("https://a.org", 1000000)])
        )
        (tmp_path / "Configuracion.json").write_text('{"Settings": {}}')
        (tmp_path / "Marcadores.html").write_text("<html></html>")
        loader = ChromeHistoryLoader(tmp_path)
        assert len(loader.discover_files()) == 1

    def test_empty_directory_finds_nothing(self, tmp_path: Path) -> None:
        loader = ChromeHistoryLoader(tmp_path)
        assert loader.discover_files() == []

    def test_load_all_returns_events(self, tmp_path: Path) -> None:
        (tmp_path / "Historial.json").write_bytes(
            _history_payload(
                [
                    _visit("https://a.org", 1000000),
                    _visit("https://www.google.de/search?q=test", 2000000),
                ]
            )
        )
        loader = ChromeHistoryLoader(tmp_path)
        events = loader.load_all()
        assert len(events) == 2
        assert {e.event_type for e in events} == {
            EVENT_TYPE_URL_VISIT,
            EVENT_TYPE_SEARCH_QUERY,
        }

    def test_load_file_result_counts_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "H.json").write_bytes(
            _history_payload(
                [
                    _visit("https://a.org", 1000000),
                    _visit("https://b.org"),  # missing timestamp
                ]
            )
        )
        loader = ChromeHistoryLoader(tmp_path)
        result = loader.load_file_result(tmp_path / "H.json")
        assert len(result.events) == 1
        assert result.skipped == 1

    def test_load_non_history_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "Config.json").write_text('{"Settings": []}')
        loader = ChromeHistoryLoader(tmp_path)
        with pytest.raises(ChromeHistoryLoadError):
            loader.load_file(tmp_path / "Config.json")

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "H.json").write_text("not json")
        loader = ChromeHistoryLoader(tmp_path)
        with pytest.raises(ChromeHistoryLoadError):
            loader.load_file(tmp_path / "H.json")
