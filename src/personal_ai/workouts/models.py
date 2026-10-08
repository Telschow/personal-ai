"""Domain models for the workout activity dataset.

Workouts are *domain data*, not corpus documents and not memories: they carry
the user's own exercise history in deterministic, normalized form so that
analytics and (in the future) read-only agent tools can consume them without
re-parsing the raw export.

The model intentionally keeps the original export fidelity (``value_raw``,
``amount_raw``, ``source_exercise_id``) next to normalized numeric fields
(``weight``, ``reps``). Every value that is a measurement carries its unit
side-by-side (``weight`` + ``weight_unit``), and timestamps are UTC ISO-8601
strings so ordering and timezone semantics are unambiguous.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

#: Stable identifier of the Boostcamp app export format this store understands.
SOURCE_TYPE_BOOSTCAMP = "boostcamp"

#: The whole current corpus is resistance training; this is the explicit
#: activity type assigned to every imported workout (the export itself carries
#: no overarching activity label). Kept as a constant so it is documented and
#: auditable rather than scattered through ingestion code.
DEFAULT_ACTIVITY_TYPE = "strength"


def _stable_id(prefix: str, *parts: str) -> str:
    """Deterministic, non-privacy-bearing id derived from source identity.

    The id is a hash of the given stable source parts, never of free text, so
    it is safe to use as a durable correlation key, a log field, and a
    database primary key without leaking what the record is about.
    """
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:16]}"


def workout_id(source_type: str, source_file: str, source_id: str) -> str:
    """Deterministic workout id from the source record identity."""
    return _stable_id("wkt", source_type, source_file, source_id)


def exercise_id(workout_id: str, order_index: int, source_exercise_id: str) -> str:
    """Deterministic exercise id from its position inside a workout."""
    return _stable_id("ext", workout_id, str(order_index), source_exercise_id)


def set_id(exercise_id: str, set_index: int) -> str:
    """Deterministic set id from its position inside an exercise."""
    return _stable_id("set", exercise_id, str(set_index))


@dataclass(slots=True)
class WorkoutSet:
    """One logged set of one exercise (a single completed or skipped attempt).

    ``value``/``amount`` are the export's raw cell values (typically weight in
    ``weight_unit`` and repetitions), preserved verbatim for fidelity.
    ``weight`` and ``reps`` are the deterministic numeric normalizations used
    for analytics; ``reps_open_ended`` records the ``+``-style targets such as
    ``5+`` (as many reps as possible from a minimum).
    """

    exercise_id: str
    set_index: int
    value_raw: str = ""
    amount_raw: str = ""
    weight: float | None = None
    weight_unit: str | None = None
    reps: int | None = None
    reps_open_ended: bool = False
    target_type: str | None = None
    intensity: float | None = None
    intensity_raw: str = ""
    intensity_unit: str | None = None
    skipped: bool = False
    custom: bool = False
    source: str | None = None

    @property
    def set_id(self) -> str:
        """Deterministic stable identifier of this set."""
        return set_id(self.exercise_id, self.set_index)

    @property
    def completed(self) -> bool:
        """Whether the set was performed (skipped sets are not completed)."""
        return not self.skipped


@dataclass(slots=True)
class WorkoutExercise:
    """One exercise of a workout, with its ordered sets.

    ``equipment_type`` is the export's exercise ``type`` (e.g. ``Barbell``,
    ``Machine``, ``Bodyweight``). Superset shells (type ``Superset``) are
    flattened: each child movement becomes its own exercise.
    """

    workout_id: str
    name: str
    order_index: int
    normalized_name: str = ""
    source_exercise_id: str | None = None
    equipment_type: str | None = None
    target_type: str | None = None
    notes: str | None = None
    sets: list[WorkoutSet] = field(default_factory=list)

    @property
    def exercise_id(self) -> str:
        """Deterministic stable identifier of this exercise."""
        return exercise_id(
            self.workout_id, self.order_index, self.source_exercise_id or ""
        )


@dataclass(slots=True)
class WorkoutRecord:
    """One normalized session ready to be persisted.

    This is the parsed-and-normalized form produced by the parser before it is
    handed to the store. ``source_file`` is the path of the export relative to
    the import root; ``source_checksum`` is the SHA-256 of the export's bytes.
    """

    workout_id: str
    source_type: str
    source_file: str
    source_id: str
    source_checksum: str
    content_hash: str
    started_at: str
    ended_at: str
    name: str
    activity_type: str = DEFAULT_ACTIVITY_TYPE
    week: int | None = None
    day: int | None = None
    program_id: str | None = None
    program_log_id: str | None = None
    user_id: str | None = None
    notes: str | None = None
    duration_seconds: float | None = None
    finished_v2_at: str | None = None
    exercises: list[WorkoutExercise] = field(default_factory=list)

    def exercise_count(self) -> int:
        return len(self.exercises)

    def set_count(self) -> int:
        return sum(len(exercise.sets) for exercise in self.exercises)


@dataclass(slots=True)
class Workout:
    """A decoded workout row as persisted in the store."""

    workout_id: str
    source_type: str
    source_file: str
    source_id: str
    source_checksum: str
    content_hash: str
    created_at: str
    updated_at: str
    started_at: str
    ended_at: str
    name: str
    activity_type: str
    week: int | None = None
    day: int | None = None
    program_id: str | None = None
    program_log_id: str | None = None
    user_id: str | None = None
    notes: str | None = None
    duration_seconds: float | None = None
    finished_v2_at: str | None = None
    exercises: list[WorkoutExercise] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Alias for the stable workout identifier."""
        return self.workout_id

    def exercise_count(self) -> int:
        return len(self.exercises)

    def set_count(self) -> int:
        return sum(len(exercise.sets) for exercise in self.exercises)

    def completed_set_count(self) -> int:
        return sum(
            1
            for exercise in self.exercises
            for workout_set in exercise.sets
            if not workout_set.skipped
        )

    def total_volume_kg(self) -> float:
        """Sum of ``weight * reps`` over completed sets, in kilograms.

        A conservative, source-faithful aggregate: only completed sets with a
        numeric weight and repetition count contribute.
        """
        total = 0.0
        for exercise in self.exercises:
            for workout_set in exercise.sets:
                if (
                    not workout_set.skipped
                    and workout_set.weight is not None
                    and workout_set.reps is not None
                ):
                    total += workout_set.weight * workout_set.reps
        return total


@dataclass(slots=True)
class WorkoutSummary:
    """A terse, deterministic projection of a workout for listing."""

    workout_id: str
    name: str
    started_at: str
    ended_at: str | None
    activity_type: str
    duration_seconds: float | None
    program_id: str | None
    exercise_count: int
    set_count: int
    completed_set_count: int
    total_volume_kg: float


@dataclass(slots=True)
class ExerciseSummary:
    """A terse, deterministic projection of an exercise for listing."""

    exercise_id: str
    workout_id: str
    name: str
    normalized_name: str
    order_index: int
    equipment_type: str | None
    target_type: str | None
    set_count: int
    completed_set_count: int
    max_weight_kg: float | None


@dataclass(slots=True)
class SetSummary:
    """A terse, deterministic projection of a set for listing."""

    set_id: str
    exercise_id: str
    workout_id: str
    set_index: int
    value_raw: str
    amount_raw: str
    weight: float | None
    weight_unit: str | None
    reps: int | None
    reps_open_ended: bool
    target_type: str | None
    completed: bool
    started_at: str
    exercise_name: str


@dataclass(slots=True)
class RowWarning:
    """A recoverable problem with one source row during parsing.

    The row is skipped (or its unsalvageable field is dropped) and recorded
    here so ingestion reports it without failing the whole file.
    """

    source_file: str
    index: int
    message: str


@dataclass(slots=True)
class WorkoutSearchResult:
    """One workout hit from a movement-name search.

    ``matched_exercises`` lists the exercise display names inside this workout
    that matched the query (bounded so tool output stays compact). The row is
    produced directly by the read-only query service.
    """

    workout_id: str
    name: str
    started_at: str
    activity_type: str
    duration_seconds: float | None
    program_id: str | None
    exercise_count: int
    set_count: int
    total_volume_kg: float
    matched_exercises: list[str]

    def to_dict(self) -> dict[str, object]:
        """Stable JSON-safe form used by agent tools and the HTTP API."""
        return {
            "workout_id": self.workout_id,
            "name": self.name,
            "started_at": self.started_at,
            "activity_type": self.activity_type,
            "duration_seconds": self.duration_seconds,
            "program_id": self.program_id,
            "exercise_count": self.exercise_count,
            "set_count": self.set_count,
            "total_volume_kg": self.total_volume_kg,
            "matched_exercises": list(self.matched_exercises),
        }


@dataclass(slots=True)
class WorkoutImportResult:
    """Structured outcome of one import run.

    ``files_seen``/``files_imported``/``files_skipped`` are relative paths of
    the export files considered. A file is *skipped* when it was already
    imported with the exact same checksum, meaning none of its rows changed.
    ``workouts_skipped`` counts individual unchanged workouts inside imported
    files (row-level idempotency). ``errors`` lists fatal, file-level problems;
    per-row problems are recoverable ``RowWarning``s.
    """

    files_seen: list[str] = field(default_factory=list)
    files_imported: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    workouts_created: int = 0
    workouts_updated: int = 0
    workouts_skipped: int = 0
    warnings: list[RowWarning] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def records_with_warnings(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> dict[str, object]:
        """Stable machine-consumable form used by the ``--json`` CLI output."""
        return {
            "files_seen": list(self.files_seen),
            "files_imported": list(self.files_imported),
            "files_skipped": list(self.files_skipped),
            "workouts_created": self.workouts_created,
            "workouts_updated": self.workouts_updated,
            "workouts_skipped": self.workouts_skipped,
            "records_with_warnings": self.records_with_warnings,
            "warnings": [
                {
                    "source_file": warning.source_file,
                    "index": warning.index,
                    "message": warning.message,
                }
                for warning in self.warnings
            ],
            "errors": list(self.errors),
        }


def compute_content_hash(record: WorkoutRecord) -> str:
    """Deterministic content hash over the normalized core of a workout.

    Covers everything that defines the workout's meaning: identity timestamps,
    program/week/day position, aggregated fields, and the ordered exercises
    and sets (including raw export cells and their numeric normalizations).
    Two records with identical normalized form hash identically, which is the
    idempotency anchor for ``updated`` vs ``skipped`` reporting.
    """
    lines: list[str] = [
        record.source_type,
        record.started_at,
        record.ended_at,
        record.name,
        record.activity_type,
        str(record.week),
        str(record.day),
        record.program_id or "",
        record.program_log_id or "",
        record.user_id or "",
        record.notes or "",
        f"{record.duration_seconds:.15g}"
        if record.duration_seconds is not None
        else "",
        record.finished_v2_at or "",
    ]
    for exercise in record.exercises:
        lines.append(
            "|".join(
                (
                    exercise.normalized_name,
                    exercise.equipment_type or "",
                    exercise.target_type or "",
                    exercise.source_exercise_id or "",
                    exercise.notes or "",
                )
            )
        )
        for workout_set in exercise.sets:
            lines.append(
                "|".join(
                    (
                        str(workout_set.set_index),
                        workout_set.value_raw,
                        workout_set.amount_raw,
                        _float_repr(workout_set.weight),
                        workout_set.weight_unit or "",
                        str(workout_set.reps),
                        str(workout_set.reps_open_ended),
                        workout_set.target_type or "",
                        _float_repr(workout_set.intensity),
                        workout_set.intensity_raw,
                        workout_set.intensity_unit or "",
                        str(workout_set.skipped),
                        str(workout_set.custom),
                        workout_set.source or "",
                    )
                )
            )
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _float_repr(value: float | None) -> str:
    """Stable textual form of an optional float for hashing."""
    if value is None:
        return ""
    return f"{value:.15g}"


__all__ = [
    "DEFAULT_ACTIVITY_TYPE",
    "SOURCE_TYPE_BOOSTCAMP",
    "ExerciseSummary",
    "RowWarning",
    "SetSummary",
    "Workout",
    "WorkoutExercise",
    "WorkoutImportResult",
    "WorkoutRecord",
    "WorkoutSearchResult",
    "WorkoutSet",
    "WorkoutSummary",
    "compute_content_hash",
    "exercise_id",
    "set_id",
    "workout_id",
]
