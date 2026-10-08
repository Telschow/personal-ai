"""Tests for CLI conversation ingestion (chatgpt/gemini) and --memory."""

import json
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.storage import ConversationStore, connect_database


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["personal-ai", *argv])
    cli.main()


@pytest.fixture()
def gemini_export(tmp_path: Path) -> Path:
    export = tmp_path / "gemini-export"
    export.mkdir()
    conversation = {
        "id": "conv-1",
        "title": "Career",
        "messages": [
            {"role": "user", "content": "I work at Example Corp"},
            {"role": "assistant", "content": "That sounds great!"},
        ],
        "createdAt": "2026-01-15T10:00:00Z",
        "lastMessageAt": "2026-01-15T10:05:00Z",
        "messageCount": 2,
    }
    (export / "conversation.json").write_text(json.dumps(conversation))
    return export


@pytest.fixture()
def chatgpt_export(tmp_path: Path) -> Path:
    export = tmp_path / "chatgpt-export"
    export.mkdir()
    shard = [
        {
            "id": "conv-1",
            "title": "Career chat",
            "create_time": 1767236400.0,
            "update_time": 1767237000.0,
            "current_node": "node-assistant",
            "mapping": {
                "node-root": {"message": None, "parent": None},
                "node-user": {
                    "message": {
                        "id": "message-1",
                        "author": {"role": "user"},
                        "create_time": 1767236400.0,
                        "content": {
                            "content_type": "text",
                            "parts": ["I work at Example Corp"],
                        },
                    },
                    "parent": "node-root",
                },
                "node-assistant": {
                    "message": {
                        "id": "message-2",
                        "author": {"role": "assistant"},
                        "create_time": 1767237000.0,
                        "content": {
                            "content_type": "text",
                            "parts": ["Excellent!"],
                        },
                    },
                    "parent": "node-user",
                },
            },
        }
    ]
    (export / "conversations-shard.json").write_text(json.dumps(shard))
    return export


class ExplodingOllamaClient:
    """Raises if constructed: conversation ingestion is fully local."""

    def __init__(self, model: str) -> None:
        raise AssertionError(
            "conversation ingestion must never construct a model client"
        )


def _counts(database: Path, table: str) -> int:
    connection = connect_database(database)
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    finally:
        connection.close()
    return int(row[0]) if row else 0


def test_ingest_chatgpt_export(
    monkeypatch: pytest.MonkeyPatch,
    chatgpt_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    database = tmp_path / "knowledge.db"

    _run_cli(
        monkeypatch,
        "--ingest",
        "chatgpt",
        str(chatgpt_export),
        "--database",
        str(database),
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "source_type: chatgpt",
        "conversations: 1",
        "messages: 2",
        "shards: 1",
        "attachments: 0",
    ]
    assert _counts(database, "conversations") == 1
    assert _counts(database, "conversation_messages") == 2


def test_ingest_chatgpt_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    chatgpt_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    database = tmp_path / "knowledge.db"
    argv = (
        "--ingest",
        "chatgpt",
        str(chatgpt_export),
        "--database",
        str(database),
    )

    _run_cli(monkeypatch, *argv)
    first_output = capsys.readouterr().out

    _run_cli(monkeypatch, *argv)
    second_output = capsys.readouterr().out

    assert first_output.splitlines() == [
        "source_type: chatgpt",
        "conversations: 1",
        "messages: 2",
        "shards: 1",
        "attachments: 0",
    ]
    assert second_output.splitlines() == [
        "source_type: chatgpt",
        "conversations: 0",
        "messages: 0",
        "shards: 1",
        "attachments: 0",
    ]
    assert _counts(database, "conversations") == 1
    assert _counts(database, "conversation_messages") == 2


def test_ingest_gemini_with_memory_writes_durable_memory(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    database = tmp_path / "knowledge.db"

    _run_cli(
        monkeypatch,
        "--ingest",
        "gemini",
        str(gemini_export),
        "--database",
        str(database),
        "--memory",
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines[:5] == [
        "source_type: gemini",
        "conversations: 1",
        "messages: 2",
        "md_files_skipped: 0",
        "aggregate_files_skipped: 0",
    ]
    assert "memory:" in lines
    assert "  candidates: 1" in lines
    assert "  accepted: 1" in lines
    assert "  writes: 1" in lines
    assert "  created: 1" in lines
    assert _counts(database, "memories") == 1
    assert _counts(database, "memory_evidence") == 1

    connection = connect_database(database)
    try:
        statement = connection.execute("SELECT content FROM memories").fetchone()[0]
        source_type = connection.execute(
            "SELECT source_type FROM memory_evidence"
        ).fetchone()[0]
    finally:
        connection.close()
    assert statement == "The user works at Example Corp"
    assert source_type == "gemini"


def test_ingest_gemini_without_memory_never_touches_memory_store(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    database = tmp_path / "knowledge.db"

    _run_cli(
        monkeypatch,
        "--ingest",
        "gemini",
        str(gemini_export),
        "--database",
        str(database),
    )

    out = capsys.readouterr().out
    assert "memory:" not in out
    assert _counts(database, "conversations") == 1
    assert _counts(database, "conversation_messages") == 2


def test_ingested_chatgpt_conversation_is_searchable(
    monkeypatch: pytest.MonkeyPatch,
    chatgpt_export: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "OllamaClient", ExplodingOllamaClient)
    database = tmp_path / "knowledge.db"

    _run_cli(
        monkeypatch,
        "--ingest",
        "chatgpt",
        str(chatgpt_export),
        "--database",
        str(database),
    )

    connection = connect_database(database)
    try:
        hits = ConversationStore(connection).search("Example Corp")
    finally:
        connection.close()

    assert len(hits) == 1
    assert "Example Corp" in hits[0].content_text


def test_memory_flag_requires_conversation_source(
    monkeypatch: pytest.MonkeyPatch,
    gemini_export: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "personal-ai",
            "--ingest",
            "file",
            str(gemini_export),
            "--database",
            str(tmp_path / "k.db"),
            "--memory",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2


def test_memory_flag_requires_ingest_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["personal-ai", "--workspace", str(tmp_path), "--memory", "hello"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.parse_args()

    assert exc_info.value.code == 2
