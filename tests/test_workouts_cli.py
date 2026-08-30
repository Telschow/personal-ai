"""Tests for the ``workouts`` subcommands of the main CLI.

Runs the real argument parser, real SQLite database, and real workout
pipeline through :func:`personal_ai.cli.main`. Fully offline — sanitized
fixtures in temporary directories, no network, no Ollama.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_ai import cli
from personal_ai.workouts.models import workout_id
from tests.workouts_fixtures import FIXTURE_CSV, SECOND_FILE_CSV


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "boostcamp.csv").write_text(FIXTURE_CSV, encoding="utf-8")
    (data / "second-export.csv").write_text(SECOND_FILE_CSV, encoding="utf-8")
    (data / "notes.txt").write_text("ignored", encoding="utf-8")
    return data


def test_workouts_requires_database() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["workouts", "list"])


def test_workouts_rejects_unknown_verb() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["workouts", "frobnicate", "--database", "x.db"])


def test_agent_cli_still_parses_without_workouts(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["personal-ai", "--workspace", "/tmp", "What files are here?"]
    )
    args = cli.parse_args()
    assert args.prompt == "What files are here?"
    assert not hasattr(args, "command")


def test_workouts_import_directory(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    out = capsys.readouterr().out
    assert "files_seen: 2" in out
    assert "files_imported: 2" in out
    assert "workouts_created: 7" in out
    assert "workouts_updated: 0" in out
    assert "records_with_warnings: 3" in out
    assert "errors: 0" in out


def test_workouts_reimport_skips_files(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    out = capsys.readouterr().out
    assert "files_skipped: 2" in out
    assert "workouts_created: 0" in out


def test_workouts_import_single_file(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(
        ["workouts", "import", str(data_dir / "boostcamp.csv"), "--database", str(db)]
    )
    out = capsys.readouterr().out
    assert "files_imported: 1" in out
    assert "workouts_created: 6" in out


def test_workouts_import_missing_path(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        cli.main(
            [
                "workouts",
                "import",
                str(tmp_path / "nope"),
                "--database",
                str(tmp_path / "w.db"),
            ]
        )


def test_workouts_import_json(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["workouts_created"] == 7
    assert payload["workouts_skipped"] == 0
    assert payload["warnings"][0]["source_file"] == "boostcamp.csv"


def test_workouts_list_newest_first(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(["workouts", "list", "--database", str(db), "--limit", "2"])
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("wkt-")
    assert "2030-08-28T20:00:00+00:00" in lines[0]
    assert "Ring Dips" in lines[0] or "Afternoon Workout" in lines[0]


def test_workouts_list_json(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(["workouts", "list", "--database", str(db), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["workouts"]) == 7
    newest = payload["workouts"][0]
    assert newest["started_at"] == "2030-08-28T20:00:00+00:00"
    assert newest["exercise_count"] == 1
    assert newest["completed_set_count"] == newest["set_count"] == 1
    assert newest["total_volume_kg"] == 0


def test_workouts_list_filters(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(
        [
            "workouts",
            "list",
            "--database",
            str(db),
            "--program",
            "prog-alpha",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["workouts"]) == 1
    assert payload["workouts"][0]["name"] == "Feb 01 Workout"


def test_workouts_show_full(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    expected = workout_id("boostcamp", "boostcamp.csv", "session-001")
    cli.main(["workouts", "show", expected, "--database", str(db)])
    out = capsys.readouterr().out
    assert "Bench Press (Barbell)" in out
    assert "Felt strong" in out
    assert "80x5" in out or "82.5x3" in out
    assert "prog-alpha" in out


def test_workouts_show_json(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    expected = workout_id("boostcamp", "boostcamp.csv", "session-002")
    cli.main(["workouts", "show", expected, "--database", str(db), "--json"])
    payload = json.loads(capsys.readouterr().out)["workout"]
    assert payload["name"] == "Superset Session"
    assert [exercise["name"] for exercise in payload["exercises"]] == [
        "Barbell Row",
        "Bicep Curl (EZ Bar)",
    ]
    assert payload["exercises"][0]["order_index"] == 0
    assert payload["exercise_count"] == 2
    assert payload["set_count"] == 3
    assert payload["completed_set_count"] == 3


def test_workouts_show_unknown_id(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    with pytest.raises(SystemExit, match="No such workout"):
        cli.main(["workouts", "show", "wkt-nope", "--database", str(db)])


def test_workouts_exercises_name_filter(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(
        [
            "workouts",
            "exercises",
            "--database",
            str(db),
            "--name",
            "bench press",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["exercises"]) == 1
    first = payload["exercises"][0]
    assert first["name"] == "Bench Press (Barbell)"
    assert first["equipment_type"] == "Barbell"
    assert first["max_weight_kg"] == 82.5
    assert first["set_count"] == 3


def test_workouts_exercises_text(tmp_path: Path, data_dir: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "import", str(data_dir), "--database", str(db)])
    capsys.readouterr()
    cli.main(["workouts", "exercises", "--database", str(db), "--limit", "3"])
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 3
    assert "Bench Press (Barbell)" in out
    assert "Barbell Row" in out


def test_workouts_empty_list(tmp_path: Path, capsys) -> None:
    db = tmp_path / "w.db"
    cli.main(["workouts", "list", "--database", str(db)])
    assert "No workouts." in capsys.readouterr().out
