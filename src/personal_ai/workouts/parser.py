"""Deterministic parsing of workout exports into normalized records.

The parser is the *file boundary*: it turns raw export text into
:class:`~personal_ai.workouts.models.WorkoutRecord` objects without touching
SQLite, the filesystem, or any model. It is pure local deterministic parsing
— exactly like the ``financial`` source adapter — so no network or LLM is
involved.

Only the Boostcamp export format is currently recognized. Discovery is header
based: :func:`detect_source_type` fingerprints the first record so a
mislabelled export is surfaced instead of being silently mis-parsed.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime

from personal_ai.workouts.models import (
    DEFAULT_ACTIVITY_TYPE,
    SOURCE_TYPE_BOOSTCAMP,
    RowWarning,
    WorkoutExercise,
    WorkoutRecord,
    WorkoutSet,
    compute_content_hash,
    workout_id,
)

#: Normalized header of the Boostcamp app workout-log export.
BOOSTCAMP_HEADER = (
    "id",
    "created_at",
    "updated_at",
    "program_id",
    "user_id",
    "status",
    "finished_at",
    "week",
    "day",
    "workout",
    "records",
    "program_log_id",
    "user_notes",
    "title",
    "duration",
    "legacy",
    "program_variation_index",
    "share_image",
    "finished_v2_at",
)


class WorkoutParseError(Exception):
    """Raised when an export cannot be parsed into known workout records."""


class UnsupportedWorkoutFormatError(WorkoutParseError):
    """Raised when a file does not match any known workout export format."""


# ``%d/%m/%Y %H:%M:%S[.ffffff]`` with an hours-only UTC offset like ``+00``.
# The export's timestamps are day-first (verified against the ``Aug 09
# Workout``-style titles and the many day values > 12), so the first number is
# the day of month.
_TIMESTAMP_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4}) "
    r"(?P<hour>\d{1,2}):(?P<minute>\d{1,2}):(?P<second>\d{1,2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>[+-]\d{1,2})(?::(?P<offset_min>\d{2}))?"
)

# The first digit run of an ``amount`` cell (repetitions, possibly ``5+``).
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def detect_source_type(text: str) -> str | None:
    """Fingerprint the export format from its first non-empty record."""
    records = _candidate_records(text)
    if not records:
        return None
    header = _normalize_header(records[0])
    if header == BOOSTCAMP_HEADER:
        return SOURCE_TYPE_BOOSTCAMP
    return None


def parse_workout_file(
    raw: bytes,
    *,
    source_file: str,
    source_checksum: str = "",
) -> tuple[list[WorkoutRecord], list[RowWarning]]:
    """Parse a workout export into ``(records, warnings)``.

    Raises :class:`WorkoutParseError` for fatal, file-level problems (empty
    payload, unknown format, undecodable text). Recoverable per-row problems
    never abort parsing; they are returned as :class:`RowWarning` entries.
    """
    if not raw:
        raise WorkoutParseError(f"Empty workout export payload: {source_file}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WorkoutParseError(
            f"Workout export is not valid UTF-8: {source_file}"
        ) from exc
    source_type = detect_source_type(text)
    if source_type is None:
        raise UnsupportedWorkoutFormatError(
            f"Could not identify a known workout export format: {source_file}"
        )
    return parse_boostcamp_csv(
        text,
        source_file=source_file,
        source_checksum=source_checksum,
    )


def parse_boostcamp_csv(
    text: str,
    *,
    source_file: str,
    source_checksum: str = "",
) -> tuple[list[WorkoutRecord], list[RowWarning]]:
    """Parse Boostcamp CSV text into ``(records, warnings)``.

    Per-row problems (undecodable timestamps, unparseable ``records`` JSON,
    malformed set entries) are recoverable: the offending row or field is
    skipped and a :class:`RowWarning` is returned instead of the whole file
    failing.
    """
    if detect_source_type(text) != SOURCE_TYPE_BOOSTCAMP:
        raise UnsupportedWorkoutFormatError(
            f"CSV does not match the Boostcamp export format: {source_file}"
        )
    warnings: list[RowWarning] = []
    parsed = list(csv.DictReader(io.StringIO(text)))
    workouts: list[WorkoutRecord] = []
    for index, row in enumerate(parsed, start=2):
        workout = _parse_row(row, source_file, source_checksum, index, warnings)
        if workout is not None:
            workouts.append(workout)
    return workouts, warnings


def _candidate_records(text: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return []


def _normalize_header(fields: list[str]) -> tuple[str, ...]:
    """Normalize a header row into a deterministic lowercase field tuple."""
    return tuple(" ".join(field.strip().lower().split()) for field in fields)


def _normalize_timestamp(value: str) -> str:
    """Normalize a Boostcamp timestamp to a UTC ISO-8601 string.

    The export's timestamps look like ``9/8/2024 14:35:44.588+00`` in
    *day-first* form (9 August 2024), with an hours-only UTC offset and an
    arbitrary fractional-second width. The offset is applied and the result
    is a ``YYYY-MM-DDTHH:MM:SS+00:00`` string with sub-second precision
    dropped for deterministic, comparable timestamps.
    """
    match = _TIMESTAMP_RE.fullmatch(value.strip())
    if match is None:
        raise WorkoutParseError(
            f"Invalid workout timestamp {value!r}: not an export timestamp"
        )
    parts = match.groupdict()
    fraction = (parts["fraction"] or "")[:6]
    year = f"{int(parts['year']):04d}"
    month = f"{int(parts['month']):02d}"
    day = f"{int(parts['day']):02d}"
    hour = f"{int(parts['hour']):02d}"
    minute = f"{int(parts['minute']):02d}"
    second = f"{int(parts['second']):02d}"
    base = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
    if fraction:
        base += f".{fraction}"
    offset = parts["offset"] if parts["offset_min"] else f"{parts['offset']}:00"
    if parts["offset_min"]:
        offset = f"{parts['offset']}:{parts['offset_min']}"
    return (
        datetime.fromisoformat(base + offset)
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )


def _parse_int(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_optional(value: str) -> int | None:
    return _parse_int(value) if value else None


def _parse_row(
    row: dict[str, str],
    source_file: str,
    source_checksum: str,
    index: int,
    warnings: list[RowWarning],
) -> WorkoutRecord | None:
    """Parse one CSV data row into a workout, or ``None`` when skipped."""
    source_id = (row.get("id") or "").strip()
    if not source_id:
        warnings.append(RowWarning(source_file, index, "Row has no id; row skipped"))
        return None

    try:
        started = (
            _normalize_timestamp(row.get("created_at") or "")
            if row.get("created_at")
            else ""
        )
        ended = (
            _normalize_timestamp(row.get("finished_at") or "")
            if row.get("finished_at")
            else ""
        )
        finished_v2 = (
            _normalize_timestamp(row.get("finished_v2_at") or "")
            if row.get("finished_v2_at")
            else ""
        )
    except WorkoutParseError as exc:
        warnings.append(RowWarning(source_file, index, f"Row skipped: {exc}"))
        return None
    if not started and not ended:
        warnings.append(
            RowWarning(
                source_file,
                index,
                "Row has neither created_at nor finished_at; row skipped",
            )
        )
        return None

    workout = WorkoutRecord(
        workout_id=workout_id(SOURCE_TYPE_BOOSTCAMP, source_file, source_id),
        source_type=SOURCE_TYPE_BOOSTCAMP,
        source_file=source_file,
        source_id=source_id,
        source_checksum=source_checksum,
        content_hash="",
        started_at=started,
        ended_at=ended or started,
        name=(row.get("title") or "").strip(),
        activity_type=DEFAULT_ACTIVITY_TYPE,
        week=_parse_optional(row.get("week", "")),
        day=_parse_optional(row.get("day", "")),
        program_id=row.get("program_id") or None,
        program_log_id=row.get("program_log_id") or None,
        user_id=row.get("user_id") or None,
        notes=row.get("user_notes") or None,
        duration_seconds=_parse_float(row.get("duration", "")),
        finished_v2_at=finished_v2 or None,
    )
    try:
        records_json = json.loads(row.get("records") or "[]")
    except ValueError as exc:
        warnings.append(
            RowWarning(
                source_file,
                index,
                f"Invalid exercise records JSON; exercises skipped: {exc}",
            )
        )
        records_json = None
    _parse_exercises(records_json, workout, warnings, index, source_file)
    workout.content_hash = compute_content_hash(workout)
    return workout


def _parse_exercises(
    records_json: object,
    workout: WorkoutRecord,
    warnings: list[RowWarning],
    row_index: int,
    source_file: str,
) -> None:
    if not isinstance(records_json, list):
        if records_json is None:
            return
        warnings.append(
            RowWarning(
                source_file,
                row_index,
                "Exercise records are not a list; none imported",
            )
        )
        return
    order_index = 0
    for exercise in records_json:
        if not isinstance(exercise, dict):
            warnings.append(
                RowWarning(
                    source_file,
                    row_index,
                    f"Exercise entry at position {order_index} is not an object; skipped",
                )
            )
            order_index += 1
            continue

        supersets = exercise.get("supersets")
        if isinstance(supersets, list) and supersets:
            # Superset shells carry no name or sets of their own; flatten each
            # child movement into its own exercise in list order.
            for child in supersets:
                if not isinstance(child, dict):
                    warnings.append(
                        RowWarning(
                            source_file,
                            row_index,
                            "Superset child at position "
                            f"{order_index} is not an object; skipped",
                        )
                    )
                    order_index += 1
                    continue
                child_name = child.get("name", "")
                child_name = child_name if isinstance(child_name, str) else ""
                child_id = child.get("id")
                parsed = WorkoutExercise(
                    workout_id=workout.workout_id,
                    name=" ".join(child_name.split()),
                    order_index=order_index,
                    normalized_name=" ".join(child_name.split()).lower(),
                    source_exercise_id=child_id if isinstance(child_id, str) else None,
                )
                raw_sets = child.get("sets")
                for set_index, raw_set in enumerate(
                    raw_sets if isinstance(raw_sets, list) else [],
                    start=1,
                ):
                    _parse_set(
                        parsed, set_index, raw_set, warnings, source_file, row_index
                    )
                workout.exercises.append(parsed)
                order_index += 1
            continue

        name = exercise.get("name", "")
        name = name if isinstance(name, str) else ""
        equipment_type = exercise.get("type")
        equipment_type = equipment_type if isinstance(equipment_type, str) else None
        target_type = exercise.get("target_type")
        target_type = target_type if isinstance(target_type, str) else None
        notes = exercise.get("notes")
        notes = notes if isinstance(notes, str) else None
        source_exercise_id = exercise.get("id")
        source_exercise_id = (
            source_exercise_id if isinstance(source_exercise_id, str) else None
        )
        parsed = WorkoutExercise(
            workout_id=workout.workout_id,
            name=" ".join(name.split()),
            order_index=order_index,
            normalized_name=" ".join(name.split()).lower(),
            source_exercise_id=source_exercise_id,
            equipment_type=equipment_type,
            target_type=target_type,
            notes=notes,
        )
        sets = exercise.get("sets")
        if isinstance(sets, list):
            raw_sets = sets
        elif sets is None:
            raw_sets = []
        else:
            warnings.append(
                RowWarning(
                    source_file,
                    row_index,
                    f"Sets of exercise {name!r} are not a list; none imported",
                )
            )
            raw_sets = []
        for set_index, raw_set in enumerate(raw_sets, start=1):
            _parse_set(parsed, set_index, raw_set, warnings, source_file, row_index)
        workout.exercises.append(parsed)
        order_index += 1


def _parse_set(
    exercise: WorkoutExercise,
    set_index: int,
    raw_set: object,
    warnings: list[RowWarning],
    source_file: str,
    row_index: int,
) -> None:
    if not isinstance(raw_set, dict):
        warnings.append(
            RowWarning(
                source_file,
                row_index,
                f"Set {set_index} of exercise {exercise.source_exercise_id!r} "
                "is not an object; set skipped",
            )
        )
        return
    value_raw = raw_set.get("value")
    amount_raw = raw_set.get("amount")
    value_raw = value_raw if isinstance(value_raw, str) else ""
    amount_raw = amount_raw if isinstance(amount_raw, str) else ""

    weight = _parse_float(value_raw)
    reps: int | None = None
    reps_open_ended = False
    amount = amount_raw.strip()
    if amount:
        number = _NUMBER_RE.search(amount)
        if number is not None:
            parsed = int(float(number.group()))
            if parsed >= 0:
                reps = parsed
                reps_open_ended = amount.endswith("+")

    weight_unit = raw_set.get("weight_unit")
    weight_unit = weight_unit if isinstance(weight_unit, str) and weight_unit else None
    intensity: float | None = None
    intensity_raw = ""
    raw_intensity = raw_set.get("intensity")
    if raw_intensity is not None:
        intensity_raw = json.dumps(raw_intensity, separators=(",", ":"))
        if isinstance(raw_intensity, (int, float)) and not isinstance(
            raw_intensity, bool
        ):
            intensity = float(raw_intensity)
        elif isinstance(raw_intensity, str):
            intensity = _parse_float(raw_intensity)
    intensity_unit = raw_set.get("intensity_unit")
    intensity_unit = (
        intensity_unit if isinstance(intensity_unit, str) and intensity_unit else None
    )
    target_type = raw_set.get("target_type")
    target_type = target_type if isinstance(target_type, str) and target_type else None
    source = raw_set.get("source")
    source = source if isinstance(source, str) and source else None

    exercise.sets.append(
        WorkoutSet(
            exercise_id=exercise.exercise_id,
            set_index=set_index,
            value_raw=value_raw,
            amount_raw=amount_raw,
            weight=weight,
            weight_unit=weight_unit,
            reps=reps,
            reps_open_ended=reps_open_ended,
            target_type=target_type,
            intensity=intensity,
            intensity_raw=intensity_raw,
            intensity_unit=intensity_unit,
            skipped=bool(raw_set.get("skipped")),
            custom=bool(raw_set.get("custom")),
            source=source,
        )
    )


__all__ = [
    "BOOSTCAMP_HEADER",
    "UnsupportedWorkoutFormatError",
    "WorkoutParseError",
    "detect_source_type",
    "parse_boostcamp_csv",
    "parse_workout_file",
]
