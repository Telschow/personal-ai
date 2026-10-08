"""Regression tests for exposing activity/event ingestion through the CLI.

Previously the CLI ``--ingest`` only accepted document sources resolved
through the source-adapter registry. The existing Chrome-history and YouTube
ingestors (``ingest_chrome_history`` / ``ingest_youtube_history``) were
programmatic-only: ``--ingest chrome_history`` and ``--ingest youtube`` were
not advertised or supported.

This slice wires those event sources into the CLI through ``--ingest`` while
keeping them on the event pipeline (EventStore) rather than the document
pipeline (DocumentStore), preserving the architectural separation between
documents and temporal events. No model and no network are involved.

All fixtures are synthetic; no real personal activity is used.
"""

import json
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.events.models import (
    EVENT_TYPE_SEARCH_QUERY,
    EVENT_TYPE_URL_VISIT,
    EVENT_TYPE_VIDEO_WATCH,
    EVENT_TYPE_YOUTUBE_SEARCH,
)
from personal_ai.storage import EventStore, connect_database
from personal_ai.storage.events import EventQuery
from personal_ai.tools.personal_context import PersonalContextService

BROWSER_HISTORY_KEY = "Browser History"

YOUTUBE_CELL = (
    '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
    "{inner}</div>"
)


def _chrome_file(directory: Path, records: list[dict[str, object]]) -> Path:
    path = directory / "Historial.json"
    path.write_text(json.dumps({BROWSER_HISTORY_KEY: records}))
    return path


def _visit(url: str, time_usec: int | None = None) -> dict[str, object]:
    record: dict[str, object] = {"url": url}
    if time_usec is not None:
        record["time_usec"] = time_usec
    return record


def _chrome_export(tmp_path: Path) -> Path:
    export = tmp_path / "chrome-export"
    export.mkdir()
    _chrome_file(
        export,
        [
            _visit("https://a.org", 1000000),
            _visit("https://b.org", 2000000),
            _visit("https://www.google.de/search?q=example+corp", 3000000),
        ],
    )
    return export


def _youtube_watch(file_name: str) -> str:
    return YOUTUBE_CELL.format(
        inner=(
            f'Has visto <a href="https://www.youtube.com/watch?v={file_name}">T</a>'
            "<br>7 mar 2016, 22:11:49 CEST<br>"
        )
    )


def _youtube_search() -> str:
    return YOUTUBE_CELL.format(
        inner=(
            'Buscaste <a href="https://www.youtube.com/results?search_query=cats">'
            "cats</a><br>13 ago 2026, 20:30:26 CEST<br>"
        )
    )


def _youtube_export(tmp_path: Path) -> Path:
    export = tmp_path / "youtube-export"
    export.mkdir()
    (export / "historial-de-reproducciones.html").write_text(
        "<html><body>"
        + _youtube_watch("AAA")
        + _youtube_watch("BBB")
        + "</body></html>"
    )
    (export / "historial-de-búsqueda.html").write_text(
        "<html><body>" + _youtube_search() + "</body></html>"
    )
    return export


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", *argv])
    cli.main()


def document_tables_exist(database: Path) -> bool:
    """Whether the document pipeline's tables were created.

    Event-only ingestion must never create the document pipeline schema, so
    this returns False after ``--ingest chrome_history`` / ``--ingest youtube``.
    """
    connection = connect_database(database)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('documents', 'document_chunks')"
        ).fetchall()
    finally:
        connection.close()
    return bool(rows)


class TestIngestableSourceAdvertisement:
    def test_event_sources_are_advertised_with_document_sources(self) -> None:
        advertised = cli.ingestable_source_types()
        assert "chrome_history" in advertised
        assert "youtube" in advertised
        for document_source in cli.known_source_types():
            assert document_source in advertised

    def test_event_sources_are_not_document_source_adapters(self) -> None:
        # Event sources are a distinct pipeline (EventStore), not document
        # source adapters, and must not be routed through resolve_source_adapter.
        assert "chrome_history" not in cli.known_source_types()
        assert "youtube" not in cli.known_source_types()

    def test_event_source_set_matches_supported_types(self) -> None:
        assert cli.EVENT_SOURCE_TYPES == ("chrome_history", "youtube")


class TestCliChromeIngest:
    def test_ingest_chrome_writes_events_not_documents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        database = tmp_path / "events.db"
        export = _chrome_export(tmp_path)

        run_cli(
            monkeypatch,
            "--ingest",
            "chrome_history",
            str(export),
            "--database",
            str(database),
        )

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "source_type: chrome_history"
        assert any(line == "events_stored: 3" for line in lines)
        assert any(line == "search_queries: 1" for line in lines)
        assert any(line == "url_visits: 2" for line in lines)

        # Events land in the event store, never the document pipeline.
        connection = connect_database(database)
        try:
            store = EventStore(connection)
            assert store.count_events() == 3
            assert store.count_events(event_type=EVENT_TYPE_SEARCH_QUERY) == 1
            assert store.count_events(event_type=EVENT_TYPE_URL_VISIT) == 2
            assert (
                len(store.search(EventQuery(event_type=EVENT_TYPE_SEARCH_QUERY))) == 1
            )
        finally:
            connection.close()
        assert not document_tables_exist(database)

    def test_ingest_chrome_rerun_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        database = tmp_path / "events.db"
        export = _chrome_export(tmp_path)
        arguments = [
            "--ingest",
            "chrome_history",
            str(export),
            "--database",
            str(database),
        ]

        run_cli(monkeypatch, *arguments)
        run_cli(monkeypatch, *arguments)

        out = capsys.readouterr().out
        connection = connect_database(database)
        try:
            assert EventStore(connection).count_events() == 3
        finally:
            connection.close()
        assert "events_stored: 0" in out  # second run inserts nothing


class TestCliYoutubeIngest:
    def test_ingest_youtube_writes_events_not_documents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        database = tmp_path / "events.db"
        export = _youtube_export(tmp_path)

        run_cli(
            monkeypatch,
            "--ingest",
            "youtube",
            str(export),
            "--database",
            str(database),
        )

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "source_type: youtube"
        assert any(line == "events_stored: 3" for line in lines)
        assert any(line == "video_watches: 2" for line in lines)
        assert any(line == "youtube_searches: 1" for line in lines)

        connection = connect_database(database)
        try:
            store = EventStore(connection)
            assert store.count_events() == 3
            assert store.count_events(source="youtube") == 3
            watches = store.search(
                EventQuery(event_type=EVENT_TYPE_VIDEO_WATCH, source="youtube")
            )
            assert len(watches) == 2
            searches = store.search(
                EventQuery(event_type=EVENT_TYPE_YOUTUBE_SEARCH, source="youtube")
            )
            assert len(searches) == 1
        finally:
            connection.close()
        assert not document_tables_exist(database)

    def test_ingest_youtube_rerun_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        database = tmp_path / "events.db"
        export = _youtube_export(tmp_path)
        arguments = [
            "--ingest",
            "youtube",
            str(export),
            "--database",
            str(database),
        ]

        run_cli(monkeypatch, *arguments)
        run_cli(monkeypatch, *arguments)

        out = capsys.readouterr().out
        connection = connect_database(database)
        try:
            assert EventStore(connection).count_events() == 3
        finally:
            connection.close()
        assert "events_stored: 0" in out


class TestEventIngestionPrivacyAndBoundedCallback:
    def test_event_ingestion_is_model_free(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        """Chrome/youtube ingestion must never construct an Ollama client.

        Event parsing and persistence are fully local and deterministic, in
        the same class as email/financial document ingestion.
        """

        def explode(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("event ingestion must not contact a model")

        monkeypatch.setattr(cli, "OllamaClient", explode)

        db = tmp_path / "events.db"
        run_cli(
            monkeypatch,
            "--ingest",
            "chrome_history",
            str(_chrome_export(tmp_path)),
            "--database",
            str(db),
        )
        connection = connect_database(db)
        try:
            assert EventStore(connection).count_events() == 3
        finally:
            connection.close()

    def test_personal_context_activity_stays_bounded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        database = tmp_path / "events.db"
        export = _chrome_export(tmp_path)
        run_cli(
            monkeypatch,
            "--ingest",
            "chrome_history",
            str(export),
            "--database",
            str(database),
        )

        connection = connect_database(database)
        try:
            service = PersonalContextService(event=EventStore(connection))
            overview = service.overview("activity")
        finally:
            connection.close()

        # Aggregate metadata only: a total count plus a tiny bounded recent
        # sample — never a dump of the whole corpus.
        assert overview["available"] is True
        assert overview["count"] == 3
        assert overview["by_event_type"][EVENT_TYPE_URL_VISIT] == 2
        assert overview["by_event_type"][EVENT_TYPE_SEARCH_QUERY] == 1
        assert isinstance(overview["recent_provenance"], list)
        assert len(overview["recent_provenance"]) <= 10

    def test_chrome_export_is_not_required_to_be_named_historial(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        # The loader discovers any JSON carrying a "Browser History" list.
        database = tmp_path / "events.db"
        export = tmp_path / "chrome-export"
        export.mkdir()
        (export / "History.json").write_text(
            json.dumps(
                {
                    BROWSER_HISTORY_KEY: [
                        _visit("https://www.google.com/search?q=goal", 1000000)
                    ]
                }
            )
        )

        run_cli(
            monkeypatch,
            "--ingest",
            "chrome_history",
            str(export),
            "--database",
            str(database),
        )
        connection = connect_database(database)
        try:
            assert EventStore(connection).count_events() == 1
        finally:
            connection.close()
