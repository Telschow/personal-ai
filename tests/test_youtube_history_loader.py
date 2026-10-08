"""Tests for the YouTube history loader."""

from pathlib import Path

import pytest

from personal_ai.events.models import (
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
)
from personal_ai.sources.youtube_history import YouTubeHistoryParseError
from personal_ai.sources.youtube_history_loader import (
    YouTubeHistoryLoader,
    YouTubeHistoryLoadError,
)

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
UNRELATED = "<html><body>not history</body></html>"


def _doc(*cells: str) -> bytes:
    return ("<html><body>" + "".join(cells) + "</body></html>").encode("utf-8")


class TestYouTubeHistoryLoaderDiscovery:
    def test_discovers_history_html(self, tmp_path: Path) -> None:
        (tmp_path / "historial-de-reproducciones.html").write_bytes(_doc(WATCH))
        loader = YouTubeHistoryLoader(tmp_path)
        files = loader.discover_files()
        assert len(files) == 1
        assert files[0].name == "historial-de-reproducciones.html"

    def test_ignores_unrelated_html(self, tmp_path: Path) -> None:
        (tmp_path / "historial-de-reproducciones.html").write_bytes(_doc(WATCH))
        (tmp_path / "info.html").write_text(UNRELATED)
        loader = YouTubeHistoryLoader(tmp_path)
        assert len(loader.discover_files()) == 1

    def test_ignores_non_html_sidecars(self, tmp_path: Path) -> None:
        (tmp_path / "historial-de-reproducciones.html").write_bytes(_doc(WATCH))
        (tmp_path / "videos.mp4").write_bytes(b"\x00\x01")
        (tmp_path / "canales.csv").write_text("a,b\n")
        loader = YouTubeHistoryLoader(tmp_path)
        assert len(loader.discover_files()) == 1

    def test_empty_directory_finds_nothing(self, tmp_path: Path) -> None:
        loader = YouTubeHistoryLoader(tmp_path)
        assert loader.discover_files() == []

    def test_media_directory_is_ignored(self, tmp_path: Path) -> None:
        # A media subdirectory mirroring the real "vídeos"/"music" folders.
        media = tmp_path / "vídeos"
        media.mkdir()
        (media / "somevideo.mp4").write_bytes(b"\x00\x01")
        (tmp_path / "historial-de-reproducciones.html").write_bytes(_doc(WATCH))
        loader = YouTubeHistoryLoader(tmp_path)
        assert len(loader.discover_files()) == 1


class TestYouTubeHistoryLoaderLoading:
    def test_load_all_returns_events(self, tmp_path: Path) -> None:
        (tmp_path / "h.html").write_bytes(_doc(WATCH, SEARCH))
        loader = YouTubeHistoryLoader(tmp_path)
        events = loader.load_all()
        assert len(events) == 2
        assert {e.event_type for e in events} == {
            EVENT_TYPE_VIDEO_WATCH,
            EVENT_TYPE_YOUTUBE_SEARCH,
        }

    def test_load_file_result_counts_skipped(self, tmp_path: Path) -> None:
        visit = (
            '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
            'Has visitado <a href="https://www.google.com/url?q=x">x</a>'
            "<br>1 nov 2025, 16:31:23 CEST<br></div>"
        )
        (tmp_path / "h.html").write_bytes(_doc(WATCH, visit))
        loader = YouTubeHistoryLoader(tmp_path)
        result = loader.load_file_result(tmp_path / "h.html")
        assert len(result.events) == 1
        assert result.skipped == 1
        assert result.skipped_reasons == {"ad_or_tracking_visit": 1}

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        loader = YouTubeHistoryLoader(tmp_path)
        with pytest.raises(YouTubeHistoryLoadError):
            loader.load_file(tmp_path / "nope.html")

    def test_load_invalid_utf8_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "h.html"
        path.write_bytes(b"\xff\xfe")
        loader = YouTubeHistoryLoader(tmp_path)
        with pytest.raises(YouTubeHistoryParseError):
            loader.load_file(path)
