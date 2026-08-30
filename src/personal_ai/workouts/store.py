"""SQLite-backed durable storage for the workout dataset.

Follows the project's storage conventions: the store class creates its tables
via ``CREATE TABLE IF NOT EXISTS`` on the shared
:func:`personal_ai.storage.documents.connect_database` connection, exactly like
:class:`personal_ai.execution.storage.OrchestrationStore` and
:class:`personal_ai.memory.store.MemoryStore`. Workout tables therefore live in
the same SQLite database as the rest of the personal AI state and survive
process restarts.

Idempotency is anchored on ``UNIQUE(source_file, source_id)``: every normalized
workout carries a deterministic content hash, and re-importing identical data
never duplicates or rewrites rows — it reports ``skipped``. Changed data
(``updated``) replaces the workout's exercises and sets inside one
transaction; ``created_at`` is kept from first import. The ``workout_sources``
manifest tracks each export file's checksum, size, and import timestamps for
provenance.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_ai.storage.documents import connect_database
from personal_ai.workouts.models import (
    Workout,
    WorkoutExercise,
    WorkoutImportResult,
    WorkoutRecord,
    WorkoutSet,
)

_WORKOUTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    workout_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_checksum TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    name TEXT NOT NULL DEFAULT '',
    activity_type TEXT NOT NULL,
    week INTEGER,
    day INTEGER,
    program_id TEXT,
    program_log_id TEXT,
    user_id TEXT,
    notes TEXT,
    duration_seconds REAL,
    finished_v2_at TEXT,
    UNIQUE (source_file, source_id)
)
"""

_EXERCISES_SCHEMA = """
CREATE TABLE IF NOT EXISTS workout_exercises (
    exercise_id TEXT PRIMARY KEY,
    workout_id TEXT NOT NULL REFERENCES workouts(workout_id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    source_exercise_id TEXT,
    equipment_type TEXT,
    target_type TEXT,
    notes TEXT,
    UNIQUE (workout_id, order_index)
)
"""

_SETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS workout_sets (
    set_id TEXT PRIMARY KEY,
    exercise_id TEXT NOT NULL REFERENCES workout_exercises(exercise_id)
        ON DELETE CASCADE,
    set_index INTEGER NOT NULL,
    value_raw TEXT,
    amount_raw TEXT,
    weight REAL,
    weight_unit TEXT,
    reps INTEGER,
    reps_open_ended INTEGER NOT NULL DEFAULT 0,
    target_type TEXT,
    intensity REAL,
    intensity_raw TEXT,
    intensity_unit TEXT,
    skipped INTEGER NOT NULL DEFAULT 0,
    custom INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    CHECK (set_index >= 1),
    UNIQUE (exercise_id, set_index)
)
"""

_SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS workout_sources (
    source_file TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_workouts_started_at ON workouts(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_workouts_program ON workouts(program_id)",
    "CREATE INDEX IF NOT EXISTS idx_workouts_source ON workouts(source_file)",
    "CREATE INDEX IF NOT EXISTS idx_we_workout ON workout_exercises(workout_id)",
    "CREATE INDEX IF NOT EXISTS idx_we_name ON workout_exercises(normalized_name)",
    "CREATE INDEX IF NOT EXISTS idx_ws_exercise ON workout_sets(exercise_id)",
)

_WORKOUT_COLUMNS = (
    "workout_id",
    "source_type",
    "source_file",
    "source_id",
    "source_checksum",
    "content_hash",
    "created_at",
    "updated_at",
    "started_at",
    "ended_at",
    "name",
    "activity_type",
    "week",
    "day",
    "program_id",
    "program_log_id",
    "user_id",
    "notes",
    "duration_seconds",
    "finished_v2_at",
)

_EXERCISE_COLUMNS = (
    "exercise_id",
    "workout_id",
    "order_index",
    "name",
    "normalized_name",
    "source_exercise_id",
    "equipment_type",
    "target_type",
    "notes",
)

_SET_COLUMNS = (
    "set_id",
    "exercise_id",
    "set_index",
    "value_raw",
    "amount_raw",
    "weight",
    "weight_unit",
    "reps",
    "reps_open_ended",
    "target_type",
    "intensity",
    "intensity_raw",
    "intensity_unit",
    "skipped",
    "custom",
    "source",
)


def _checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class WorkoutStore:
    """SQLite-backed CRUD store for normalized workouts.

    ``now`` is injectable so tests can pin the import/update timestamps.
    """

    def __init__(
        self, connection: sqlite3.Connection, *, now: str | None = None
    ) -> None:
        self._connection = connection
        self._now = now or now_iso
        for schema in (
            _WORKOUTS_SCHEMA,
            _EXERCISES_SCHEMA,
            _SETS_SCHEMA,
            _SOURCES_SCHEMA,
            *_INDEXES,
        ):
            self._connection.execute(schema)
        self._connection.commit()

    # ---- import ----
    def import_records(
        self,
        records: list[WorkoutRecord],
        *,
        source_file: str | None = None,
        checksum: str | None = None,
        size_bytes: int | None = None,
    ) -> WorkoutImportResult:
        """Persist normalized records idempotently and report the outcome.

        All changes for a file run in a single transaction, so a crash mid-way
        never leaves a half-imported file. Unchanged workouts are left alone
        and counted as ``skipped``.
        """
        if not records:
            return WorkoutImportResult()
        source_type = records[0].source_type
        source_file = source_file or records[0].source_file
        checksum = checksum or records[0].source_checksum or ""
        result = WorkoutImportResult()
        with self._transaction():
            for record in records:
                self._import_one(record, result)
            self._connection.execute(
                """
                INSERT INTO workout_sources (
                    source_file, source_type, checksum, size_bytes,
                    imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_file) DO UPDATE SET
                    source_type=excluded.source_type,
                    checksum=excluded.checksum,
                    size_bytes=excluded.size_bytes,
                    updated_at=excluded.updated_at
                """,
                (
                    source_file,
                    source_type,
                    checksum,
                    size_bytes if size_bytes is not None else 0,
                    self._now(),
                    self._now(),
                ),
            )
        return result

    def get_workout(self, workout_id: str) -> Workout | None:
        """Return the full workout with exercises and sets, or ``None``."""
        row = self._connection.execute(
            "SELECT * FROM workouts WHERE workout_id = ?", (workout_id,)
        ).fetchone()
        if row is None:
            return None
        workout = _row_to_workout(row)
        workout.exercises = self._load_exercises(workout_id)
        return workout

    # ---- internal ----
    def _import_one(self, record: WorkoutRecord, result: WorkoutImportResult) -> None:
        existing = self._connection.execute(
            "SELECT content_hash, created_at FROM workouts "
            "WHERE source_file = ? AND source_id = ?",
            (record.source_file, record.source_id),
        ).fetchone()
        now = self._now()
        if existing is None:
            workout_id = record.workout_id
            self._insert_workout(record, created_at=now, updated_at=now)
            self._insert_children(workout_id, record.exercises)
            result.workouts_created += 1
            return
        if existing[0] == record.content_hash:
            result.workouts_skipped += 1
            return
        self._connection.execute(
            "UPDATE workouts SET "
            "source_checksum=?, content_hash=?, updated_at=?, started_at=?, "
            "ended_at=?, name=?, activity_type=?, week=?, day=?, "
            "program_id=?, program_log_id=?, user_id=?, notes=?, "
            "duration_seconds=?, finished_v2_at=? "
            "WHERE workout_id=?",
            (
                record.source_checksum,
                record.content_hash,
                now,
                record.started_at,
                record.ended_at,
                record.name,
                record.activity_type,
                record.week,
                record.day,
                record.program_id,
                record.program_log_id,
                record.user_id,
                record.notes,
                record.duration_seconds,
                record.finished_v2_at,
                record.workout_id,
            ),
        )
        self._connection.execute(
            "DELETE FROM workout_exercises WHERE workout_id = ?",
            (record.workout_id,),
        )
        self._insert_children(record.workout_id, record.exercises)
        result.workouts_updated += 1

    def _insert_workout(
        self, record: WorkoutRecord, *, created_at: str, updated_at: str
    ) -> None:
        self._connection.execute(
            "INSERT INTO workouts ("
            + ", ".join(_WORKOUT_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.workout_id,
                record.source_type,
                record.source_file,
                record.source_id,
                record.source_checksum,
                record.content_hash,
                created_at,
                updated_at,
                record.started_at,
                record.ended_at,
                record.name,
                record.activity_type,
                record.week,
                record.day,
                record.program_id,
                record.program_log_id,
                record.user_id,
                record.notes,
                record.duration_seconds,
                record.finished_v2_at,
            ),
        )

    def _insert_children(
        self, workout_id: str, exercises: list[WorkoutExercise]
    ) -> None:
        for exercise in exercises:
            self._connection.execute(
                "INSERT INTO workout_exercises ("
                + ", ".join(_EXERCISE_COLUMNS)
                + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    exercise.exercise_id,
                    workout_id,
                    exercise.order_index,
                    exercise.name,
                    exercise.normalized_name,
                    exercise.source_exercise_id,
                    exercise.equipment_type,
                    exercise.target_type,
                    exercise.notes,
                ),
            )
            for workout_set in exercise.sets:
                self._connection.execute(
                    "INSERT INTO workout_sets ("
                    + ", ".join(_SET_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        workout_set.set_id,
                        exercise.exercise_id,
                        workout_set.set_index,
                        workout_set.value_raw,
                        workout_set.amount_raw,
                        workout_set.weight,
                        workout_set.weight_unit,
                        workout_set.reps,
                        int(workout_set.reps_open_ended),
                        workout_set.target_type,
                        workout_set.intensity,
                        workout_set.intensity_raw,
                        workout_set.intensity_unit,
                        int(workout_set.skipped),
                        int(workout_set.custom),
                        workout_set.source,
                    ),
                )

    def _load_exercises(self, workout_id: str) -> list[WorkoutExercise]:
        exercises: list[WorkoutExercise] = []
        exercise_rows = self._connection.execute(
            "SELECT * FROM workout_exercises WHERE workout_id = ? "
            "ORDER BY order_index ASC, exercise_id ASC",
            (workout_id,),
        ).fetchall()
        for row in exercise_rows:
            exercise = _row_to_exercise(row)
            set_rows = self._connection.execute(
                "SELECT * FROM workout_sets WHERE exercise_id = ? "
                "ORDER BY set_index ASC, set_id ASC",
                (exercise.exercise_id,),
            ).fetchall()
            exercise.sets = [_row_to_set(row) for row in set_rows]
            exercises.append(exercise)
        return exercises

    def close(self) -> None:
        self._connection.close()

    def _transaction(self) -> Any:
        """Context manager performing an atomic import transaction."""

        class _Transaction:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def __enter__(self) -> None:
                self._connection.execute("BEGIN")

            def __exit__(self, exc_type: object, *exc: object) -> None:
                if exc_type is None:
                    self._connection.commit()
                else:
                    self._connection.rollback()

        return _Transaction(self._connection)


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def open_workout_store(
    path: str | Path,
) -> tuple[sqlite3.Connection, WorkoutStore]:
    """Open a SQLite database configured for workout storage.

    Reuses the shared personal-AI connection, so workout tables co-locate in
    one file by default (exactly like the memory and orchestration stores).
    """
    connection = connect_database(path)
    return connection, WorkoutStore(connection)


def import_workout_directory(
    directory: Path,
    store: WorkoutStore,
) -> WorkoutImportResult:
    """Discover and import every workout export inside a directory.

    Parses ``.csv`` files recursively inside ``directory`` (never outside it),
    fingerprints each one to its known format, and imports normalized records
    idempotently. Fatal file-level problems are collected in ``errors``;
    recoverable per-row problems in ``warnings``.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"Workout directory does not exist: {directory}")
    from personal_ai.workouts.parser import (
        WorkoutParseError,
        parse_workout_file,
    )

    result = WorkoutImportResult()
    files = sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() == ".csv"
        ),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    for path in files:
        relative = path.relative_to(directory).as_posix()
        result.files_seen.append(relative)
        raw = path.read_bytes()
        checksum = _checksum(raw)
        existing = store._connection.execute(
            "SELECT checksum FROM workout_sources WHERE source_file = ?",
            (relative,),
        ).fetchone()
        if existing is not None and existing[0] == checksum:
            result.files_skipped.append(relative)
            continue
        try:
            records, warnings = parse_workout_file(
                raw,
                source_file=relative,
                source_checksum=checksum,
            )
        except WorkoutParseError as exc:
            result.errors.append(str(exc))
            continue
        result.files_imported.append(relative)
        result.warnings.extend(warnings)
        outcome = store.import_records(
            records,
            source_file=relative,
            checksum=checksum,
            size_bytes=len(raw),
        )
        result.workouts_created += outcome.workouts_created
        result.workouts_updated += outcome.workouts_updated
        result.workouts_skipped += outcome.workouts_skipped
        result.warnings.extend(outcome.warnings)
    return result


def _row_to_workout(row: sqlite3.Row | tuple[object, ...]) -> Workout:
    values = tuple(row)
    return Workout(
        workout_id=str(values[0]),
        source_type=str(values[1]),
        source_file=str(values[2]),
        source_id=str(values[3]),
        source_checksum=str(values[4]),
        content_hash=str(values[5]),
        created_at=str(values[6]),
        updated_at=str(values[7]),
        started_at=str(values[8]),
        ended_at=str(values[9]),
        name=str(values[10]),
        activity_type=str(values[11]),
        week=values[12],
        day=values[13],
        program_id=values[14],
        program_log_id=values[15],
        user_id=values[16],
        notes=values[17],
        duration_seconds=values[18],
        finished_v2_at=values[19],
    )


def _row_to_exercise(row: sqlite3.Row | tuple[object, ...]) -> WorkoutExercise:
    values = tuple(row)
    return WorkoutExercise(
        workout_id=str(values[1]),
        name=str(values[3]),
        order_index=int(values[2]),
        normalized_name=str(values[4]),
        source_exercise_id=values[5],
        equipment_type=values[6],
        target_type=values[7],
        notes=values[8],
    )


def _row_to_set(row: sqlite3.Row | tuple[object, ...]) -> WorkoutSet:
    values = tuple(row)
    return WorkoutSet(
        exercise_id=str(values[1]),
        set_index=int(values[2]),
        value_raw=str(values[3] or ""),
        amount_raw=str(values[4] or ""),
        weight=values[5],
        weight_unit=values[6],
        reps=values[7],
        reps_open_ended=bool(values[8]),
        target_type=values[9],
        intensity=values[10],
        intensity_raw=str(values[11] or ""),
        intensity_unit=values[12],
        skipped=bool(values[13]),
        custom=bool(values[14]),
        source=values[15],
    )


__all__ = [
    "WorkoutStore",
    "import_workout_directory",
    "now_iso",
    "open_workout_store",
]
