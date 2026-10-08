"""Tests for the SQLite workout store: schema, idempotency, and imports.

Fully offline: temporary SQLite files and sanitized in-memory fixtures. No
network, no Ollama, no real personal data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from personal_ai.workouts.models import (
    SOURCE_TYPE_BOOSTCAMP,
    workout_id,
)
from personal_ai.workouts.parser import parse_workout_file
from personal_ai.workouts.store import (
    WorkoutStore,
    import_workout_directory,
    now_iso,
    open_workout_store,
)
from tests.workouts_fixtures import (
    FIXTURE_CSV,
    FIXTURE_ROWS,
    MODIFIED_CSV,
    SECOND_FILE_CSV,
    _exercise,
    _set,
    to_csv,
)


def _parse(csv_text: str, source_file: str = "boostcamp.csv"):
    return parse_workout_file(
        csv_text.encode("utf-8"),
        source_file=source_file,
        source_checksum="cafe1234",
    )


def test_open_workout_store_creates_tables(tmp_path: Path) -> None:
    path = tmp_path / "workouts.db"
    connection, _store = open_workout_store(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "workouts",
            "workout_exercises",
            "workout_sets",
            "workout_sources",
        } <= tables
    finally:
        connection.close()
    assert path.exists()


def test_import_records_created_and_get_workout(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        records, _ = _parse(FIXTURE_CSV)
        result = store.import_records(records, source_file="boostcamp.csv")
        assert result.workouts_created == 6
        assert result.workouts_updated == 0
        assert result.workouts_skipped == 0

        first = next(record for record in records if record.source_id == "session-001")
        workout = store.get_workout(first.workout_id)
        assert workout is not None
        assert workout.source_type == SOURCE_TYPE_BOOSTCAMP
        assert workout.source_file == "boostcamp.csv"
        assert workout.workout_id == first.workout_id
        assert workout.exercise_count() == 1
        exercise = workout.exercises[0]
        assert exercise.name == "Bench Press (Barbell)"
        assert exercise.sets[2].intensity_raw == "[8,9]"
        assert exercise.sets[2].reps == 3
        assert exercise.sets[2].reps_open_ended is True
        assert workout.created_at == workout.updated_at
        assert workout.duration_seconds == 1925
    finally:
        connection.close()


def test_reimport_is_idempotent_and_reports_skipped(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        records, _ = _parse(FIXTURE_CSV)
        store.import_records(records, source_file="boostcamp.csv")
        first = store.get_workout(records[0].workout_id)
        assert first is not None

        result = store.import_records(records, source_file="boostcamp.csv")
        assert result.workouts_created == 0
        assert result.workouts_updated == 0
        assert result.workouts_skipped == 6

        after = store.get_workout(records[0].workout_id)
        assert after is not None
        assert after.created_at == first.created_at
        assert after.updated_at == first.updated_at
    finally:
        connection.close()


def test_changed_workout_is_updated_not_duplicated(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        records, _ = _parse(FIXTURE_CSV)
        store.import_records(records, source_file="boostcamp.csv")
        original = store.get_workout(records[0].workout_id)
        assert original is not None

        modified, _ = _parse(MODIFIED_CSV)
        result = store.import_records(modified, source_file="boostcamp.csv")
        assert result.workouts_created == 0
        assert result.workouts_updated == 1
        assert result.workouts_skipped == 5

        bench = next(record for record in modified if record.source_id == "session-001")
        workout = store.get_workout(bench.workout_id)
        assert workout is not None
        assert workout.exercises[0].sets[0].weight == 85
        assert workout.source_id == "session-001"
        assert workout.created_at == original.created_at
    finally:
        connection.close()


def test_update_replaces_children_children(tmp_path: Path) -> None:
    """Exercises that disappear from the source must disappear from storage."""
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        records, _ = _parse(FIXTURE_CSV)
        store.import_records(records, source_file="boostcamp.csv")

        superset = next(
            record for record in records if record.source_id == "session-002"
        )
        before = store.get_workout(superset.workout_id)
        assert before is not None and before.exercise_count() == 2

        # Re-import the superset with only one child movement retained.
        slim_rows = [list(row) for row in FIXTURE_ROWS]
        shell = {
            "id": "ss-1",
            "name": "",
            "type": "Superset",
            "custom": False,
            "source": "user",
            "sets": [],
            "supersets": [
                _exercise(
                    "ch-row",
                    "Barbell Row",
                    [_set("60", "8", weight_unit="kg")],
                    equipment_type="Barbell",
                )
            ],
        }
        slim_row = next(row for row in slim_rows if row[0] == "session-002")
        slim_row[10] = json.dumps([shell])
        slim_records, _ = _parse(to_csv(slim_rows))
        second = next(
            record for record in slim_records if record.source_id == "session-002"
        )
        store.import_records(slim_records, source_file="boostcamp.csv")

        after = store.get_workout(second.workout_id)
        assert after is not None
        assert after.exercise_count() == 1
        assert after.exercises[0].name == "Barbell Row"
        assert len(after.exercises[0].sets) == 1
    finally:
        connection.close()


def test_import_workout_directory_discovers_and_skips(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "boostcamp.csv").write_text(FIXTURE_CSV, encoding="utf-8")
    (data / "subdir").mkdir()
    (data / "subdir" / "second-export.csv").write_text(
        SECOND_FILE_CSV, encoding="utf-8"
    )
    (data / "notes.txt").write_text("ignored", encoding="utf-8")

    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        result = import_workout_directory(data, store)
        assert result.files_seen == ["boostcamp.csv", "subdir/second-export.csv"]
        assert result.files_imported == ["boostcamp.csv", "subdir/second-export.csv"]
        assert result.files_skipped == []
        assert result.workouts_created == 7  # 6 + 1
        assert result.errors == []

        again = import_workout_directory(data, store)
        assert again.files_skipped == ["boostcamp.csv", "subdir/second-export.csv"]
        assert again.files_imported == []
        assert again.workouts_created == 0
        assert again.workouts_skipped == 0
    finally:
        connection.close()


def test_import_workout_directory_collects_file_errors(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "boostcamp.csv").write_text(FIXTURE_CSV, encoding="utf-8")
    (data / "broken.csv").write_text("not,a,boostcamp\n1,2,3", encoding="utf-8")

    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        result = import_workout_directory(data, store)
        assert result.workouts_created == 6
        assert len(result.errors) == 1
        assert "Could not identify a known workout export format" in result.errors[0]
        assert result.files_imported == ["boostcamp.csv"]
        assert result.files_seen == ["boostcamp.csv", "broken.csv"]
    finally:
        connection.close()


def test_import_workout_directory_missing_dir_raises(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        with pytest.raises(FileNotFoundError):
            import_workout_directory(tmp_path / "nope", store)
    finally:
        connection.close()


def test_injected_now_pins_timestamps(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "w.db")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        store = WorkoutStore(connection, now=lambda: "2030-01-01T00:00:00+00:00")
        records, _ = _parse(FIXTURE_CSV)
        store.import_records(records, source_file="boostcamp.csv")
        row = connection.execute(
            "SELECT created_at, updated_at FROM workouts LIMIT 1"
        ).fetchone()
        assert row == ("2030-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
        sources = connection.execute(
            "SELECT imported_at, updated_at FROM workout_sources LIMIT 1"
        ).fetchone()
        assert sources == ("2030-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
    finally:
        connection.close()


def test_manifest_records_checksum_and_size(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        raw = FIXTURE_CSV.encode("utf-8")
        records, _ = _parse(FIXTURE_CSV)
        store.import_records(
            records,
            source_file="boostcamp.csv",
            checksum="summing-cafe",
            size_bytes=len(raw),
        )
        row = connection.execute(
            "SELECT source_file, source_type, checksum, size_bytes FROM workout_sources"
        ).fetchone()
        assert row == ("boostcamp.csv", SOURCE_TYPE_BOOSTCAMP, "summing-cafe", len(raw))
    finally:
        connection.close()


def test_same_source_id_between_files_is_distinct(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "w.db")
    try:
        records, _ = _parse(FIXTURE_CSV, source_file="one.csv")
        other, _ = _parse(FIXTURE_CSV, source_file="two.csv")
        store.import_records(records, source_file="one.csv")
        store.import_records(other, source_file="two.csv")
        assert store.get_workout(records[0].workout_id) is not None
        assert store.get_workout(other[0].workout_id) is not None
        assert records[0].workout_id != other[0].workout_id
        count = connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
        assert count == 12
    finally:
        connection.close()


def test_now_iso_is_utc_iso_8601() -> None:
    value = now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None


def test_workout_id_is_source_scoped() -> None:
    assert workout_id("boostcamp", "a.csv", "id-1") == workout_id(
        "boostcamp", "a.csv", "id-1"
    )
    assert workout_id("boostcamp", "a.csv", "id-1") != workout_id(
        "boostcamp", "b.csv", "id-1"
    )
