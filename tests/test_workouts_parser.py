"""Tests for the workout export parser (pure, offline, deterministic)."""

from __future__ import annotations

import pytest

from personal_ai.workouts.models import SOURCE_TYPE_BOOSTCAMP, workout_id
from personal_ai.workouts.parser import (
    BOOSTCAMP_HEADER,
    UnsupportedWorkoutFormatError,
    WorkoutParseError,
    _normalize_timestamp,
    detect_source_type,
    parse_boostcamp_csv,
    parse_workout_file,
)
from tests.workouts_fixtures import (
    FIXTURE_CSV,
    SECOND_FILE_CSV,
)


def test_detect_source_type_boostcamp() -> None:
    assert detect_source_type(FIXTURE_CSV) == SOURCE_TYPE_BOOSTCAMP
    assert detect_source_type(SECOND_FILE_CSV) == SOURCE_TYPE_BOOSTCAMP


def test_detect_source_type_unknown() -> None:
    assert detect_source_type("date,amount,note\n2024-01-01,10,hello") is None
    assert detect_source_type("") is None


def test_header_fingerprint_matches_expected_column_order() -> None:
    assert BOOSTCAMP_HEADER[0] == "id"
    assert BOOSTCAMP_HEADER[10] == "records"
    assert len(BOOSTCAMP_HEADER) == 19


def test_parse_workout_file_empty_raises() -> None:
    with pytest.raises(WorkoutParseError):
        parse_workout_file(b"", source_file="empty.csv")


def test_parse_workout_file_unknown_format_raises() -> None:
    with pytest.raises(UnsupportedWorkoutFormatError):
        parse_workout_file(b"a,b,c\n1,2,3", source_file="other.csv")


def test_parse_workout_file_non_utf8_raises() -> None:
    with pytest.raises(WorkoutParseError):
        parse_workout_file(b"\xff\xfe not text", source_file="binary.csv")


def test_parse_fixture_valid_rows_and_warnings() -> None:
    records, warnings = parse_workout_file(
        FIXTURE_CSV.encode("utf-8"),
        source_file="boostcamp.csv",
    )
    # 8 rows: 6 valid, 2 skipped (no-id row, bad timestamp row).
    assert len(records) == 6
    assert len(warnings) == 3  # bad JSON, no id, bad timestamp
    assert records[0].source_type == SOURCE_TYPE_BOOSTCAMP
    assert records[0].source_file == "boostcamp.csv"


def test_timestamps_are_day_first() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    by_id = {record.source_id: record for record in records}
    # ``1/2/2024`` is 1 February, not 2 January (day-first D/M/Y).
    assert by_id["session-001"].started_at == "2024-02-01T10:00:00+00:00"
    assert by_id["session-001"].ended_at == "2024-02-01T10:32:05+00:00"
    # First component > 12 can only be a day.
    assert by_id["session-002"].started_at == "2024-06-16T08:00:00+00:00"


def test_offset_is_normalized_to_utc() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    by_id = {record.source_id: record for record in records}
    assert by_id["session-004"].started_at == "2024-09-04T20:00:00+00:00"
    assert by_id["session-004"].ended_at == "2024-09-04T20:45:00+00:00"


def test_title_month_name_matches_day_first_date() -> None:
    # Mirrors the real export's ``Aug 09 Workout`` title proving D/M/Y.
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    by_id = {record.source_id: record for record in records}
    assert by_id["session-001"].name == "Feb 01 Workout"
    assert by_id["session-001"].started_at.startswith("2024-02-01")
    assert by_id["session-004"].name == "Sep 04 Workout"
    assert by_id["session-004"].started_at.startswith("2024-09-04")


def test_normalize_timestamp_days_first_explicit() -> None:
    # ``9/8/2024`` is 9 August; sub-second precision is dropped.
    assert (
        _normalize_timestamp("9/8/2024 14:35:44.588+00") == "2024-08-09T14:35:44+00:00"
    )


def test_normalize_timestamp_invalid() -> None:
    with pytest.raises(WorkoutParseError):
        _normalize_timestamp("2024-08-09T14:35:44")


def test_bench_press_sets_normalized() -> None:
    records, warnings = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    assert all(warning.index != 2 for warning in warnings)  # bench row is clean
    bench = next(record for record in records if record.source_id == "session-001")
    assert bench.name == "Feb 01 Workout"
    assert bench.week == 1
    assert bench.day == 2
    assert bench.program_id == "prog-alpha"
    assert bench.program_log_id == "log-1"
    assert bench.notes == "Felt strong"
    assert bench.duration_seconds == 1925
    assert bench.finished_v2_at == "2024-02-01T10:32:06+00:00"
    assert bench.exercise_count() == 1

    exercise = bench.exercises[0]
    assert exercise.name == "Bench Press (Barbell)"
    assert exercise.normalized_name == "bench press (barbell)"
    assert exercise.equipment_type == "Barbell"
    assert exercise.source_exercise_id == "ex-bench"
    assert exercise.notes == "Paused reps"
    assert len(exercise.sets) == 3

    third = exercise.sets[2]
    assert third.weight == 82.5
    assert third.reps == 3
    assert third.reps_open_ended is True  # ``3+``
    assert third.intensity is None  # RPE range, not a scalar
    assert third.intensity_raw == "[8,9]"
    assert third.intensity_unit == "RPE"
    assert third.skipped is False
    assert third.completed is True


def test_skipped_set_and_open_ended_load() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    deadlift = next(record for record in records if record.source_id == "session-004")
    sets = deadlift.exercises[0].sets
    assert sets[0].skipped is True
    assert sets[0].completed is False
    assert sets[0].weight == 100
    assert sets[1].skipped is False
    assert sets[1].reps == 5
    assert sets[1].reps_open_ended is True


def test_bodyweight_set_takes_no_weight() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    dips = next(record for record in records if record.source_id == "session-003")
    workout_set = dips.exercises[0].sets[0]
    assert workout_set.weight is None
    assert workout_set.weight_unit is None
    assert workout_set.reps == 15
    assert workout_set.custom is True
    assert workout_set.source == "coach"
    assert workout_set.intensity_raw == "[7,8]"


def test_superset_shells_are_flattened() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    superset = next(record for record in records if record.source_id == "session-002")
    assert superset.exercise_count() == 2
    assert [exercise.name for exercise in superset.exercises] == [
        "Barbell Row",
        "Bicep Curl (EZ Bar)",
    ]
    assert superset.exercises[0].order_index == 0
    assert superset.exercises[1].order_index == 1
    assert superset.exercises[0].workout_id == superset.workout_id
    assert [len(exercise.sets) for exercise in superset.exercises] == [2, 1]
    # The shell itself contributes no exercise.
    assert all(exercise.source_exercise_id != "ss-1" for exercise in superset.exercises)


def test_bad_records_json_produces_warning_but_keeps_workout() -> None:
    records, warnings = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    broken = next(record for record in records if record.source_id == "session-005")
    assert broken.exercise_count() == 0
    assert any(
        "Invalid exercise records JSON" in warning.message for warning in warnings
    )


def test_empty_records_is_valid() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    assert [record.source_id for record in records] == [
        "session-001",
        "session-002",
        "session-003",
        "session-004",
        "session-005",
        "session-008",
    ]


def test_missing_id_row_skipped_with_warning() -> None:
    _, warnings = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    assert any("no id" in warning.message for warning in warnings)


def test_bad_timestamp_row_skipped_with_warning() -> None:
    _, warnings = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    assert any("Row skipped" in warning.message for warning in warnings)


def test_parse_boostcamp_csv_rejects_unrelated_text() -> None:
    with pytest.raises(UnsupportedWorkoutFormatError):
        parse_boostcamp_csv(
            SECOND_FILE_CSV.replace("id,created_at", "x,y"), source_file="bad.csv"
        )


def test_deterministic_workout_ids_from_source_identity() -> None:
    records, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    first = records[0]
    expected = workout_id(SOURCE_TYPE_BOOSTCAMP, "boostcamp.csv", first.source_id)
    assert first.workout_id == expected

    again, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    assert [record.workout_id for record in again] == [
        record.workout_id for record in records
    ]

    other_file, _ = parse_boostcamp_csv(FIXTURE_CSV, source_file="renamed.csv")
    assert other_file[0].workout_id != first.workout_id


def test_warning_indices_are_1_based_row_numbers() -> None:
    _, warnings = parse_boostcamp_csv(FIXTURE_CSV, source_file="boostcamp.csv")
    by_message = {warning.message: warning.index for warning in warnings}
    # session-005, the no-id row, and session-007 fail on rows 6, 7 and 8
    # (1 header row, first data row is 2).
    assert sorted(by_message.values()) == [6, 7, 8]
    json_index = next(
        index
        for message, index in by_message.items()
        if "Invalid exercise records JSON" in message
    )
    assert json_index == 6
    assert all(warning.source_file == "boostcamp.csv" for warning in warnings)
