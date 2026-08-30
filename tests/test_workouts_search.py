"""Tests for workout movement-name search and aggregate metadata.

Offline: the sanitized fixture is imported into a temp SQLite file and the
deterministic search surface is exercised. Every assertion is repeatable.
"""

from __future__ import annotations

import json
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
    more, _ = parse_workout_file(
        SECOND_FILE_CSV.encode("utf-8"), source_file="second-export.csv"
    )
    store.import_records(more, source_file="second-export.csv")
    yield WorkoutQueryService(store)
    connection.close()


def test_search_matches_normalized_movement_name(service: WorkoutQueryService) -> None:
    hits = service.search("bench press")
    assert hits
    assert any("Bench Press (Barbell)" in h.matched_exercises for h in hits)


def test_search_matches_each_token(service: WorkoutQueryService) -> None:
    hits = service.search("bench press")
    assert all(
        any(
            "bench" in name.lower() or "press" in name.lower()
            for name in h.matched_exercises
        )
        for h in hits
    )


def test_search_is_case_insensitive(service: WorkoutQueryService) -> None:
    upper = service.search("BENCH PRESS")
    lower = service.search("bench press")
    assert [h.workout_id for h in upper] == [h.workout_id for h in lower]


def test_search_multi_token_matches_any_token(service: WorkoutQueryService) -> None:
    hits = service.search("barbell row")
    assert hits
    assert all(
        any("Row" in name or "Barbell" in name for name in h.matched_exercises)
        for h in hits
    )


def test_search_orders_newest_first_like_listing(service: WorkoutQueryService) -> None:
    listed = [w.workout_id for w in service.list_workouts()]
    hits = service.search("barbell")
    order = [h.workout_id for h in hits]
    assert order == [wid for wid in listed if wid in order]


def test_search_results_match_listing_aggregates(service: WorkoutQueryService) -> None:
    by_id = {w.workout_id: w for w in service.list_workouts()}
    for hit in service.search("deadlift"):
        summary = by_id[hit.workout_id]
        assert hit.name == summary.name
        assert hit.started_at == summary.started_at
        assert hit.exercise_count == summary.exercise_count
        assert hit.set_count == summary.set_count
        assert hit.total_volume_kg == summary.total_volume_kg


def test_search_respects_limit(service: WorkoutQueryService) -> None:
    total = len(service.search("barbell"))
    assert len(service.search("barbell", limit=1)) == 1
    assert len(service.search("barbell", limit=max(total, 99))) == total


def test_search_unknown_returned_empty(service: WorkoutQueryService) -> None:
    assert service.search("zzz-unknown-movement") == []


def test_search_rejects_empty_query(service: WorkoutQueryService) -> None:
    with pytest.raises(ValueError):
        service.search("   ")


def test_search_rejects_bad_limit(service: WorkoutQueryService) -> None:
    with pytest.raises(ValueError):
        service.search("bench", limit=0)


def test_search_is_deterministic_and_json_safe(service: WorkoutQueryService) -> None:
    first = [hit.to_dict() for hit in service.search("barbell")]
    second = [hit.to_dict() for hit in service.search("barbell")]
    assert first == second
    json.dumps(first)


def test_search_does_not_mutate_store(service: WorkoutQueryService) -> None:
    before = service.stats()["workout_count"]
    service.search("barbell")
    service.search("unknown")
    assert service.stats()["workout_count"] == before


def test_stats_shapes_are_consistent(service: WorkoutQueryService) -> None:
    stats = service.stats()
    assert stats["workout_count"] == len(service.list_workouts())
    assert stats["set_count"] == len(service.list_sets())
    activity = stats["by_activity_type"]
    assert isinstance(activity, dict)
    for bucket in activity.values():
        assert set(bucket) >= {
            "workout_count",
            "exercise_count",
            "set_count",
            "completed_set_count",
            "total_volume_kg",
        }
    assert sum(b["workout_count"] for b in activity.values()) == stats["workout_count"]
    json.dumps(stats)


def test_stats_on_empty_store(tmp_path: Path) -> None:
    connection, store = open_workout_store(tmp_path / "empty.db")
    try:
        stats = WorkoutQueryService(store).stats()
        assert stats["workout_count"] == 0
        assert stats["set_count"] == 0
        assert stats["total_duration_seconds"] == 0.0
    finally:
        connection.close()
