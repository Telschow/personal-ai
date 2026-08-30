"""Tests for the memory subcommands of the execution CLI.

Runs the real argument parser, real SQLite database, and real memory service
through :func:`main`, asserting on stdout and exit codes. Fully offline — no
network, no Ollama, no corpus required for memory commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai.execution.cli import main, parse_args


def test_cli_requires_database(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["memory", "add", "fragile"])


def test_cli_memory_add_and_show_roundtrip(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(["--database", str(db), "memory", "add", "Prefers espresso over tea."])
    memory_id = capsys.readouterr().out.split()[0]

    main(["--database", str(db), "memory", "show", memory_id, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["memory"]["content"] == "Prefers espresso over tea."
    assert data["memory"]["kind"] == "preference"
    assert data["memory"]["status"] == "active"
    assert data["events"]


def test_cli_memory_add_with_options(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(
        [
            "--database",
            str(db),
            "memory",
            "add",
            "--kind",
            "fact",
            "--scope",
            "project",
            "--scope-id",
            "proj-3",
            "--importance",
            "0.9",
            "Project uses a local-first posture.",
        ]
    )
    out = capsys.readouterr().out
    assert "kind=fact" in out
    assert "scope=project/proj-3" in out


def test_cli_memory_search_and_json_scores(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(["--database", str(db), "memory", "add", "Leadership goal: reach CTO."])
    capsys.readouterr()
    main(
        [
            "--database",
            str(db),
            "memory",
            "search",
            "leadership cto",
            "--json",
        ]
    )
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) == 1
    assert hits[0]["rank"] == 1
    assert "score" in hits[0]
    assert "leadership" in hits[0]["memory"]["content"].lower()


def test_cli_memory_scoped_search(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(
        [
            "--database",
            str(db),
            "memory",
            "add",
            "--scope",
            "project",
            "--scope-id",
            "proj-9",
            "Roadmap for the analytics project.",
        ]
    )
    capsys.readouterr()
    main(["--database", str(db), "memory", "search", "roadmap"])
    assert "No" not in capsys.readouterr().out
    main(
        [
            "--database",
            str(db),
            "memory",
            "search",
            "roadmap",
            "--scope",
            "project",
            "--scope-id",
            "other",
        ]
    )
    assert capsys.readouterr().out == ""


def test_cli_memory_list_status_filter(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(["--database", str(db), "memory", "add", "First"])
    memory_id = capsys.readouterr().out.split()[0]
    main(["--database", str(db), "memory", "archive", memory_id])
    capsys.readouterr()
    main(["--database", str(db), "memory", "list"])
    assert capsys.readouterr().out == ""
    main(["--database", str(db), "memory", "list", "--status", "archived", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["memory_id"] == memory_id
    assert data[0]["status"] == "archived"


def test_cli_memory_delete_and_purge(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(["--database", str(db), "memory", "add", "Temporary."])
    memory_id = capsys.readouterr().out.split()[0]
    main(["--database", str(db), "memory", "delete", memory_id])
    assert "deleted" in capsys.readouterr().out
    main(["--database", str(db), "memory", "list"])
    assert capsys.readouterr().out == ""  # deleted no longer listed as active

    main(["--database", str(db), "memory", "add", "Purge me."])
    purge_id = capsys.readouterr().out.split()[0]
    main(["--database", str(db), "memory", "purge", purge_id])
    assert "purged" in capsys.readouterr().out


def test_cli_memory_unknown_memory_clean_exit(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    with pytest.raises(SystemExit) as exc:
        main(["--database", str(db), "memory", "show", "mem-doesnotexist"])
    assert "Unknown memory" in str(exc.value)


def test_cli_memory_and_execution_share_database(tmp_path: Path, capsys) -> None:
    db = tmp_path / "orch.db"
    main(["--database", str(db), "memory", "add", "Shared DB memory."])
    capsys.readouterr()

    corpus = tmp_path / "corpus.db"
    main(
        [
            "--database",
            str(db),
            "research",
            "career goals",
            "--corpus",
            str(corpus),
        ]
    )
    assert "status=" in capsys.readouterr().out

    main(["--database", str(db), "execution", "list", "--json"])
    executions = json.loads(capsys.readouterr().out)
    assert executions and executions[0]["status"] in {"completed", "failed"}

    main(["--database", str(db), "memory", "search", "Shared"])
    assert "Shared DB memory." in capsys.readouterr().out


def test_cli_memory_add_requires_single_content_arg(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--database", "x.db", "memory", "add"])
    parsed = parse_args(["--database", "x.db", "memory", "add", "hello world"])
    assert parsed.content == "hello world"
