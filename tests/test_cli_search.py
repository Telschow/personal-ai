"""Tests for the CLI keyword-search surface."""

from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.documents import Document, DocumentChunk
from personal_ai.storage import ChunkStore, DocumentStore, connect_database


@pytest.fixture()
def knowledge_db(tmp_path: Path) -> Path:
    database = tmp_path / "knowledge.db"
    connection = connect_database(database)
    documents = DocumentStore(connection)
    chunks = ChunkStore(connection)
    try:
        documents.add(
            Document(
                id="doc-1",
                source="notes/doc-1.json",
                source_type="keep",
                content_hash="hash-1",
                created_at="2026-01-01T00:00:00+00:00",
                modified_at="2026-01-01T00:00:00+00:00",
            )
        )
        chunks.add_many(
            (
                DocumentChunk(
                    id="chunk-z", document_id="doc-1", text="kite design notes"
                ),
                DocumentChunk(
                    id="chunk-a", document_id="doc-1", text="kite safety plans"
                ),
            )
        )
        connection.commit()
    finally:
        chunks.close()
    return database


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", *argv])
    cli.main()


def test_parse_args_search_mode_without_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--database",
            str(tmp_path / "knowledge.db"),
            "--search",
            "guitar",
        ],
    )

    args = cli.parse_args()

    assert args.search_query == "guitar"
    assert args.database == tmp_path / "knowledge.db"
    assert args.workspace is None
    assert args.limit == 10


def test_parse_args_search_respects_explicit_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--search",
            "guitar",
            "--limit",
            "3",
        ],
    )

    assert cli.parse_args().limit == 3


def test_search_requires_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", "--search", "guitar"])

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_agent_mode_still_requires_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", "--workspace", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_main_search_prints_ranked_hits(
    monkeypatch: pytest.MonkeyPatch,
    knowledge_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_cli(
        monkeypatch,
        "--database",
        str(knowledge_db),
        "--search",
        "kite",
    )

    output = capsys.readouterr().out
    lines = output.splitlines()

    assert lines[0] == "2 hits"
    # Deterministic chunk-id ordering for equal-ranked matches.
    assert "chunk=chunk-a" in lines[1]
    assert "document=doc-1" in lines[1]
    assert "   kite safety plans" == lines[2]
    assert "chunk=chunk-z" in lines[3]
    assert "   kite design notes" == lines[4]


def test_main_search_limit_truncates_results(
    monkeypatch: pytest.MonkeyPatch,
    knowledge_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_cli(
        monkeypatch,
        "--database",
        str(knowledge_db),
        "--search",
        "kite",
        "--limit",
        "1",
    )

    output = capsys.readouterr().out

    assert output.startswith("1 hits\n")
    assert "chunk=chunk-a" in output


def test_main_search_without_match_prints_friendly_message(
    monkeypatch: pytest.MonkeyPatch,
    knowledge_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_cli(
        monkeypatch,
        "--database",
        str(knowledge_db),
        "--search",
        "zeppelin",
    )

    assert capsys.readouterr().out == "No matching documents.\n"


def test_run_search_closes_the_database_connection(
    monkeypatch: pytest.MonkeyPatch,
    knowledge_db: Path,
) -> None:
    run_cli(monkeypatch, "--database", str(knowledge_db), "--search", "kite")

    # The database file must be releasable after the command finished.
    connection = connect_database(knowledge_db)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
