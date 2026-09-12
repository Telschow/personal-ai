"""CLI tests for the ``personal-ai embedding`` verb (backfill).

Hermetic only: seeded temporary SQLite databases and a fake embedding
provider injected through ``cli.create_embedder``. No Ollama, no network,
no production data.
"""

import argparse
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.config import EMBEDDING_MODEL_ENV
from personal_ai.documents import DocumentChunk, Embedding
from personal_ai.storage import ChunkStore, EmbeddingStore, connect_database

FAKE_MODEL = "fake-embed"


class FakeEmbeddingProvider:
    """Deterministic provider standing in for the Ollama embedder."""

    def __init__(self, model: str = FAKE_MODEL) -> None:
        self.model = model
        self.calls = 0

    def embed(self, text: str) -> Embedding:
        self.calls += 1
        return Embedding(model=self.model, vector=(0.5, 0.25))


def seed_chunks(database: Path, count: int = 3) -> None:
    connection = connect_database(database)
    try:
        store = ChunkStore(connection)
        for index in range(count):
            store.add(
                DocumentChunk(
                    id=f"chunk-{index}",
                    document_id="doc-1",
                    text=f"note {index} about planning",
                )
            )
    finally:
        connection.close()


def embedding_count(database: Path, model: str) -> int:
    connection = connect_database(database)
    try:
        return EmbeddingStore(connection).count(model)
    finally:
        connection.close()


def test_embedding_backfill_parser_defaults() -> None:
    args = cli.parse_args(["embedding", "backfill", "--database", "db.sqlite"])

    assert args.command == "embedding"
    assert args.verb == "backfill"
    assert args.database == Path("db.sqlite")
    assert args.model is None
    assert args.batch_size == 32
    assert args.resume is True


def test_embedding_backfill_no_resume_flag() -> None:
    args = cli.parse_args(
        ["embedding", "backfill", "--database", "db.sqlite", "--no-resume"]
    )

    assert args.resume is False


def test_embedding_missing_database_exits(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(SystemExit, match="Database not found"):
        cli.run_embedding(
            cli.parse_args(
                [
                    "embedding",
                    "backfill",
                    "--database",
                    str(missing),
                    "--model",
                    FAKE_MODEL,
                ]
            )
        )


def test_embedding_requires_model_or_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "empty.db"
    database.touch()
    monkeypatch.delenv(EMBEDDING_MODEL_ENV, raising=False)

    with pytest.raises(SystemExit, match=EMBEDDING_MODEL_ENV):
        cli.run_embedding(
            cli.parse_args(["embedding", "backfill", "--database", str(database)])
        )


def test_embedding_backfill_end_to_end_hermetic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "personal.db"
    seed_chunks(database)
    monkeypatch.setattr(
        cli, "create_embedder", lambda settings, **kwargs: FakeEmbeddingProvider()
    )

    code = cli.run_embedding(
        cli.parse_args(
            [
                "embedding",
                "backfill",
                "--database",
                str(database),
                "--model",
                FAKE_MODEL,
            ]
        )
    )

    assert code == 0
    out = capsys.readouterr()
    assert "Embedding backfill" in out.out
    assert "embeddings created: 3" in out.out
    assert f"stored for model {FAKE_MODEL}: 3 / 3" in out.out
    assert "[embedding]" in out.err
    assert embedding_count(database, FAKE_MODEL) == 3
    connection = connect_database(database)
    try:
        store = EmbeddingStore(connection)
        assert store.get("chunk-0") == Embedding(model=FAKE_MODEL, vector=(0.5, 0.25))
    finally:
        connection.close()


def test_embedding_backfill_is_idempotent_across_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "personal.db"
    seed_chunks(database)
    monkeypatch.setattr(
        cli, "create_embedder", lambda settings, **kwargs: FakeEmbeddingProvider()
    )
    args = ["embedding", "backfill", "--database", str(database), "--model", FAKE_MODEL]

    assert cli.run_embedding(cli.parse_args(args)) == 0
    first = capsys.readouterr().out
    assert cli.run_embedding(cli.parse_args(args)) == 0
    second = capsys.readouterr().out

    assert "embeddings created: 3" in first
    assert "embeddings created: 0" in second
    assert embedding_count(database, FAKE_MODEL) == 3


def test_embedding_backfill_default_model_from_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "personal.db"
    seed_chunks(database)
    monkeypatch.setenv(EMBEDDING_MODEL_ENV, FAKE_MODEL)
    monkeypatch.setattr(
        cli, "create_embedder", lambda settings, **kwargs: FakeEmbeddingProvider()
    )

    code = cli.run_embedding(
        cli.parse_args(["embedding", "backfill", "--database", str(database)])
    )

    assert code == 0
    out = capsys.readouterr().out
    assert f"model: {FAKE_MODEL}" in out
    assert "embeddings created: 3" in out
    assert embedding_count(database, FAKE_MODEL) == 3


def test_embedding_backfill_invalid_batch_size(tmp_path: Path) -> None:
    database = tmp_path / "personal.db"
    seed_chunks(database)

    with pytest.raises(SystemExit, match="batch-size"):
        cli.run_embedding(
            cli.parse_args(
                [
                    "embedding",
                    "backfill",
                    "--database",
                    str(database),
                    "--model",
                    FAKE_MODEL,
                    "--batch-size",
                    "0",
                ]
            )
        )


def test_embedding_unknown_verb_exits() -> None:
    with pytest.raises(SystemExit, match="Unknown embedding verb"):
        cli.run_embedding(argparse.Namespace(verb="frobnicate"))


def test_main_dispatches_embedding_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[argparse.Namespace] = []

    def fake_run_embedding(args: argparse.Namespace) -> int:
        recorded.append(args)
        return 7

    monkeypatch.setattr(cli, "run_embedding", fake_run_embedding)
    cli.main(
        ["embedding", "backfill", "--database", "db.sqlite", "--model", FAKE_MODEL]
    )

    assert len(recorded) == 1
    assert recorded[0].command == "embedding"
    assert recorded[0].verb == "backfill"
