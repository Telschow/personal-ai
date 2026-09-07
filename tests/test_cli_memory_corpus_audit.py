"""CLI tests for ``personal-ai memory corpus-audit``.

The corpus-audit verb is deliberately distinct from the other memory verbs: it
requires NO ``--database`` flag (it structurally cannot target a production
database), it reads only raw conversation exports, and its output is strictly
aggregate-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai import cli


def _write_gemini_export(directory: Path) -> None:
    conversation = {
        "id": "conv-1",
        "title": "Career",
        "messages": [
            {"role": "user", "content": "I work at BCG and I want to move to Spain."},
            {"role": "assistant", "content": "That sounds great!"},
        ],
        "createdAt": "2026-01-15T10:00:00Z",
        "lastMessageAt": "2026-01-15T10:05:00Z",
        "messageCount": 2,
    }
    (directory / "conversation.json").write_text(json.dumps(conversation))


def _run_memory_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr("sys.argv", ["personal-ai", "memory", *argv])
    return cli.run_memory(cli.parse_args(["memory", *argv]))


def test_corpus_audit_requires_no_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = tmp_path / "gemini"
    export.mkdir()
    _write_gemini_export(export)

    code = _run_memory_cli(
        monkeypatch,
        "corpus-audit",
        "--source",
        "gemini",
        "--path",
        str(export),
        "--limit",
        "1",
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "source_type: gemini" in out
    assert "conversations_sampled: 1" in out
    assert "conversations_available: 1" in out
    assert "candidates:" in out
    assert "model_calls: 0" in out
    assert "llm_proposal_layer: not_exercised" in out
    assert "idempotent: True" in out  # default scratch DB is a disposable tempfile
    # No content may ever be printed.
    assert "BCG" not in out
    assert "Spain" not in out
    assert "conv-1" not in out


def test_corpus_audit_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = tmp_path / "gemini"
    export.mkdir()
    _write_gemini_export(export)

    code = _run_memory_cli(
        monkeypatch,
        "corpus-audit",
        "--source",
        "gemini",
        "--path",
        str(export),
        "--limit",
        "1",
        "--json",
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["source_type"] == "gemini"
    assert data["conversations_sampled"] == 1
    assert data["model_calls"] == 0
    assert data["idempotency"]["idempotent"] is True
    dumped = capsys.readouterr().out  # nothing left in stdout after JSON
    assert dumped == ""


def test_corpus_audit_rejects_production_like_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    export = tmp_path / "gemini"
    export.mkdir()
    _write_gemini_export(export)
    with pytest.raises(SystemExit) as exc_info:
        _run_memory_cli(
            monkeypatch,
            "corpus-audit",
            "--source",
            "gemini",
            "--path",
            str(export),
            "--database",
            str(tmp_path / "prod.db"),
        )
    # argparse exit code 2: --database is unknown to corpus-audit.
    assert exc_info.value.code == 2


def test_corpus_audit_does_not_touch_other_databases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    export = tmp_path / "gemini"
    export.mkdir()
    _write_gemini_export(export)
    database = tmp_path / "unrelated.db"
    database.write_text("sentinel")

    _run_memory_cli(
        monkeypatch,
        "corpus-audit",
        "--source",
        "gemini",
        "--path",
        str(export),
        "--limit",
        "1",
    )
    assert database.read_text() == "sentinel"


def test_corpus_audit_scratch_db_flag_backed_by_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    export = tmp_path / "gemini"
    export.mkdir()
    _write_gemini_export(export)
    scratch = tmp_path / "scratch.db"

    code = _run_memory_cli(
        monkeypatch,
        "corpus-audit",
        "--source",
        "gemini",
        "--path",
        str(export),
        "--limit",
        "1",
        "--scratch-db",
        str(scratch),
    )
    assert code == 0
    assert scratch.exists()
    assert scratch.read_bytes().startswith(b"SQLite format 3\x00")


def test_corpus_audit_requires_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_memory_cli(monkeypatch, "corpus-audit", "--source", "gemini")
    assert exc_info.value.code == 2
