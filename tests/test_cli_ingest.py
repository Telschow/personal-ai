"""Tests for the CLI ingestion mode."""

import json
import sqlite3
from pathlib import Path
from typing import Self

import pytest

from personal_ai import cli
from personal_ai.documents import Document, StructuredExtraction
from personal_ai.retrieval import SearchDocumentsRequest, search_documents
from personal_ai.storage import ChunkStore, DocumentStore, connect_database


def make_document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "id": "doc-seed",
        "source": "seed",
        "source_type": "keep",
        "content_hash": "hash-seed",
        "created_at": "2025-01-01T00:00:00+00:00",
        "modified_at": "2025-01-01T00:00:00+00:00",
        "metadata": {},
    }
    values.update(overrides)
    return Document(**values)  # type: ignore[arg-type]


LONG_ANSWER = (
    "Practice guitar in short daily sessions. Warm up with scales, then "
    "work on chord transitions slowly with a metronome. Record yourself "
    "weekly and compare against earlier recordings to hear progress. "
    "Focus on clean fretting before speed, and always end with something "
    "fun to keep motivation high."
)


@pytest.fixture()
def gemini_export(tmp_path: Path) -> Path:
    export = tmp_path / "gemini-export"
    export.mkdir()
    conversation = {
        "id": "conv-1",
        "title": "Guitar learning",
        "messages": [
            {"role": "user", "content": "How do I practice guitar?"},
            {"role": "assistant", "content": LONG_ANSWER},
        ],
        "createdAt": "2026-01-15T10:00:00Z",
        "lastMessageAt": "2026-01-15T10:05:00Z",
        "messageCount": 2,
    }
    (export / "conversation.json").write_text(json.dumps(conversation))
    return export


class FakeOllamaClient:
    """Context-manager double that can only serve chat-shaped use.

    It deliberately exposes no embedding method: if ingestion ever tried
    to contact an embedding provider, the test would fail loudly.
    """

    def __init__(self, model: str) -> None:
        self.model = model

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class FakeStructuredExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, extraction) -> StructuredExtraction:  # type: ignore[no-untyped-def]
        self.calls.append(extraction.document_id)
        return StructuredExtraction(document_id=extraction.document_id)


@pytest.fixture()
def faked_model(monkeypatch: pytest.MonkeyPatch) -> FakeStructuredExtractor:
    extractor = FakeStructuredExtractor()
    monkeypatch.setattr(cli, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(cli, "OllamaStructuredExtractor", lambda client: extractor)
    return extractor


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", *argv])
    cli.main()


def table_counts(database: Path) -> tuple[int, int, int]:
    connection = connect_database(database)
    try:
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[
            0
        ]
        embeddings = connection.execute(
            "SELECT COUNT(*) FROM chunk_embeddings"
        ).fetchone()[0]
    finally:
        connection.close()
    return int(documents), int(chunks), int(embeddings)


def ingest_argv(gemini_export: Path, database: Path) -> list[str]:
    return [
        "--ingest",
        "gemini",
        str(gemini_export),
        "--database",
        str(database),
    ]


def test_ingest_creates_database_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    assert database.is_file()
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "source_type: gemini",
        "documents: 1",
        "kind_counts:",
        "  text_heavy: 1",
        "chunks: 1",
    ]
    documents, chunks, embeddings = table_counts(database)
    assert (documents, chunks, embeddings) == (1, 1, 0)
    assert len(faked_model.calls) == 1


def test_second_invocation_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    arguments = ingest_argv(gemini_export, database)

    run_cli(monkeypatch, *arguments)
    first_output = capsys.readouterr().out
    first_calls = len(faked_model.calls)

    run_cli(monkeypatch, *arguments)

    assert capsys.readouterr().out == first_output
    assert len(faked_model.calls) == first_calls
    assert table_counts(database) == (1, 1, 0)


def test_ingested_database_is_immediately_searchable(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    connection = connect_database(database)
    try:
        hits = search_documents(
            ChunkStore(connection),
            SearchDocumentsRequest(query="guitar metronome"),
        )
    finally:
        connection.close()

    assert len(hits) == 1
    assert hits[0].document_id
    assert "metronome" in hits[0].text


def test_unknown_source_exits_without_creating_database(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
) -> None:
    database = tmp_path / "should-not-exist.db"

    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--ingest",
            "gmail",
            str(gemini_export),
            "--database",
            str(database),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code != 0
    assert "Unknown source type" in str(exc_info.value)
    assert not database.exists()


def test_missing_source_path_exits_without_creating_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-export"
    database = tmp_path / "should-not-exist.db"

    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--ingest",
            "gemini",
            str(missing),
            "--database",
            str(database),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code != 0
    assert "not a directory" in str(exc_info.value)
    assert not database.exists()


def test_existing_database_is_reused_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    seed_connection = connect_database(database)
    documents_store = DocumentStore(seed_connection)
    documents_store.add(make_document(id="doc-existing"))
    seed_connection.close()

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    connection = connect_database(database)
    try:
        ids = {
            str(row[0])
            for row in connection.execute("SELECT id FROM documents").fetchall()
        }
    finally:
        connection.close()

    # The seeded document survived and exactly one gemini document joined.
    assert "doc-existing" in ids
    assert len(ids) == 2


def test_connection_is_released_after_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    faked_model: FakeStructuredExtractor,
) -> None:
    database = tmp_path / "knowledge.db"
    real_connect = cli.connect_database
    opened: list[sqlite3.Connection] = []

    def tracking_connect(path: object) -> sqlite3.Connection:
        connection = real_connect(path)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    monkeypatch.setattr(cli, "connect_database", tracking_connect)

    run_cli(monkeypatch, *ingest_argv(gemini_export, database))

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_search_and_ingest_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--search",
            "guitar",
            "--ingest",
            "gemini",
            str(gemini_export),
            "--database",
            str(tmp_path / "k.db"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_ingest_requires_database(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["personal-ai", "--ingest", "gemini", str(gemini_export)]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_agent_mode_unchanged_by_ingest_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", "--workspace", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    # Missing prompt in agent mode keeps its original required behavior.
    assert exc_info.value.code == 2
