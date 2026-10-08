"""Tests for the read-only workout query service.

Offline: every test stores the sanitized fixture into a temp SQLite file and
exercises the deterministic query surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_ai.workouts.parser import parse_workout_file
from personal_ai.workouts.query import WorkoutQueryService
from personal_ai.workouts.store import open_workout_store
from tests.workouts_fixtures import FIXTURE_CSV, SECOND_FILE_CSV


@pytest.fixture()
def service(tmp_path: Path) -> WorkoutQueryService:
    connection, store = open_workout_store(tmp_path / "w.db")
    records, _ = parse_workout_file(
        FIXTURE_CSV.encode("utf-8"), source_file="boostcamp.csv"
    )
    store.import_records(records, source_file="boostcamp.csv")
    yield WorkoutQueryService(store)
    connection.close()


@pytest.fixture()
def two_file_service(tmp_path: Path) -> WorkoutQueryService:
    connection, store = open_workout_store(tmp_path / "w.db")
    records, _ = parse_workout_file(
        FIXTURE_CSV.encode("utf-8"), source_file="boostcamp.csv"
    )
    store.import_records(records, source_file="boostcamp.csv")
    more, _ = parse_workout_file(
        SECOND_FILE_CSV.encode("utf-8"), source_file="second-export.csv"
    )
    store.import_records(more, source_file="second-export.csv")
    yield WorkoutQueryService(store)
    connection.close()


def test_get_workout_returns_full_tree(service: WorkoutQueryService) -> None:
    workouts = service.list_workouts(limit=1)
    assert len(workouts) == 1
    workout = service.get_workout(workouts[0].workout_id)
    assert workout is not None
    assert workout.exercises
    assert all(exercise.sets for exercise in workout.exercises)


def test_get_workout_unknown_returns_none(service: WorkoutQueryService) -> None:
    assert service.get_workout("wkt-does-not-exist") is None


def test_list_workouts_orders_newest_first(service: WorkoutQueryService) -> None:
    workouts = service.list_workouts()
    started = [workout.started_at for workout in workouts]
    assert started == sorted(started, reverse=True)
    assert started[0] == "2030-08-28T20:00:00+00:00"


def test_list_workouts_limit(service: WorkoutQueryService) -> None:
    assert len(service.list_workouts(limit=2)) == 2


def test_list_workouts_date_range(service: WorkoutQueryService) -> None:
    workouts = service.list_workouts(date_from="2024-06-01", date_to="2024-12-31")
    assert [workout.name for workout in workouts] == [
        "Sep 04 Workout",
        "Empty Session",
        "Superset Session",
    ]


def test_list_workouts_date_range_is_inclusive(service: WorkoutQueryService) -> None:
    by_day = {workout.name: workout for workout in service.list_workouts()}
    assert any(
        workout.started_at.startswith("2024-02-01") for workout in by_day.values()
    )
    single = service.list_workouts(date_from="2024-02-01", date_to="2024-02-01")
    assert len(single) == 1
    assert single[0].started_at.startswith("2024-02-01")


def test_list_workouts_program_filter(service: WorkoutQueryService) -> None:
    workouts = service.list_workouts(program_id="prog-alpha")
    assert len(workouts) == 1
    assert workouts[0].name == "Feb 01 Workout"
    assert service.list_workouts(program_id="prog-missing") == []


def test_list_workouts_source_file_filter(
    two_file_service: WorkoutQueryService,
) -> None:
    assert len(two_file_service.list_workouts(source_file="boostcamp.csv")) == 6
    assert len(two_file_service.list_workouts(source_file="second-export.csv")) == 1
    assert len(two_file_service.list_workouts()) == 7


def test_summary_aggregates(service: WorkoutQueryService) -> None:
    by_name = {workout.name: workout for workout in service.list_workouts()}
    bench = by_name["Feb 01 Workout"]
    assert bench.exercise_count == 1
    assert bench.set_count == 3
    assert bench.completed_set_count == 3
    assert bench.total_volume_kg == pytest.approx(80 * 5 + 82.5 * 3 + 82.5 * 3)

    superset = by_name["Superset Session"]
    assert superset.exercise_count == 2
    assert superset.set_count == 3

    deadlift = by_name["Sep 04 Workout"]
    # One of two sets is skipped: volume and completed counts reflect it.
    assert deadlift.set_count == 2
    assert deadlift.completed_set_count == 1
    assert deadlift.total_volume_kg == pytest.approx(100 * 5)


def test_list_exercises_name_filter(service: WorkoutQueryService) -> None:
    exercises = service.list_exercises(name="bench press")
    assert len(exercises) == 1
    first = exercises[0]
    assert first.name == "Bench Press (Barbell)"
    assert first.equipment_type == "Barbell"
    assert first.set_count == 3
    assert first.completed_set_count == 3
    assert first.max_weight_kg == 82.5

    dips = service.list_exercises(name="dips")
    assert len(dips) == 1
    assert dips[0].name == "Ring Dips"
    assert dips[0].max_weight_kg is None  # bodyweight: no tracked load


def test_list_exercises_workout_filter(service: WorkoutQueryService) -> None:
    superset = next(
        workout
        for workout in service.list_workouts()
        if workout.name == "Superset Session"
    )
    exercises = service.list_exercises(workout_id=superset.workout_id)
    assert [exercise.name for exercise in exercises] == [
        "Barbell Row",
        "Bicep Curl (EZ Bar)",
    ]
    assert [exercise.order_index for exercise in exercises] == [0, 1]


def test_list_exercises_limit(service: WorkoutQueryService) -> None:
    assert len(service.list_exercises(limit=2)) == 2


def test_exercises_grouped_deterministically(service: WorkoutQueryService) -> None:
    names = [exercise.name for exercise in service.list_exercises()]
    assert names == sorted(names, key=str.lower)


def test_list_sets_all_and_order(service: WorkoutQueryService) -> None:
    sets = service.list_sets()
    assert len(sets) == 9
    first = sets[0]
    assert first.started_at == "2030-08-28T20:00:00+00:00"
    assert first.exercise_name == "Ring Dips"
    assert first.set_index == 1


def test_list_sets_skipped_filter(service: WorkoutQueryService) -> None:
    assert len(service.list_sets(skipped=True)) == 1
    assert len(service.list_sets(skipped=False)) == 8
    skipped = service.list_sets(skipped=True)[0]
    assert skipped.exercise_name == "Deadlift (Barbell)"
    assert skipped.completed is False


def test_list_sets_exercise_filter(service: WorkoutQueryService) -> None:
    exercises = service.list_exercises(name="bench press")
    sets = service.list_sets(exercise_id=exercises[0].exercise_id)
    assert len(sets) == 3
    assert [s.value_raw for s in sets] == ["80", "82.5", "82.5"]


def test_list_sets_date_range_and_workout_filters(service: WorkoutQueryService) -> None:
    in_2024 = service.list_sets(date_from="2024-01-01", date_to="2024-12-31")
    assert len(in_2024) == 8  # everything except the 2030 bodyweight session
    workouts = service.list_workouts(limit=1)
    contained = service.list_sets(workout_id=workouts[0].workout_id)
    assert len(contained) == 1
    assert contained[0].exercise_name == "Ring Dips"


def test_set_summary_exposes_open_ended_reps(service: WorkoutQueryService) -> None:
    sets = service.list_sets()
    open_sets = [s for s in sets if s.reps_open_ended]
    assert len(open_sets) == 2  # bench set 3+ and deadlift set 5+
    assert all(s.reps is not None for s in open_sets)


def test_query_does_not_mutate(service: WorkoutQueryService) -> None:
    before = service.list_workouts()
    service.list_workouts(limit=1)
    service.list_exercises(name="bench press")
    service.list_sets(skipped=True)
    after = service.list_workouts()
    assert [w.workout_id for w in before] == [w.workout_id for w in after]


def test_service_reads_others_updates(
    service: WorkoutQueryService, tmp_path: Path
) -> None:
    """A later import through the store is visible to an existing service."""
    records, _ = parse_workout_file(
        SECOND_FILE_CSV.encode("utf-8"), source_file="second-export.csv"
    )
    service._store.import_records(records, source_file="second-export.csv")
    assert len(service.list_workouts(source_file="second-export.csv")) == 1
