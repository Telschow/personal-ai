"""Read-only, deterministic queries over the workout dataset.

The query service is the *application boundary*: callers (the CLI today, a
read-only ``search_workouts``-style agent tool in the future) interact with
workouts only through these narrow, deterministic methods. It performs no
writes, no parsing, and exposes nothing beyond the normalized model.

Ordering is always deterministic so repeated calls return identical results:
workouts newest first (ties broken by id), exercises grouped by normalized
movement name, sets in workout order.
"""

from __future__ import annotations

import sqlite3

from personal_ai.workouts.models import (
    ExerciseSummary,
    SetSummary,
    Workout,
    WorkoutSearchResult,
    WorkoutSummary,
)
from personal_ai.workouts.store import WorkoutStore

_WORKOUT_SUMMARY_SQL = """
SELECT
    w.workout_id,
    w.name,
    w.started_at,
    w.ended_at,
    w.activity_type,
    w.duration_seconds,
    w.program_id,
    COUNT(DISTINCT ex.exercise_id) AS exercise_count,
    COUNT(s.set_id) AS set_count,
    COALESCE(SUM(CASE WHEN s.skipped = 0 THEN 1 ELSE 0 END), 0)
        AS completed_set_count,
    COALESCE(SUM(
        CASE WHEN s.skipped = 0 AND s.weight IS NOT NULL AND s.reps IS NOT NULL
        THEN s.weight * s.reps ELSE 0 END
    ), 0) AS total_volume_kg
FROM workouts w
LEFT JOIN workout_exercises ex ON ex.workout_id = w.workout_id
LEFT JOIN workout_sets s ON s.exercise_id = ex.exercise_id
{where}
GROUP BY w.workout_id
ORDER BY w.started_at DESC, w.workout_id ASC
{limit}
"""

_EXERCISE_SUMMARY_SQL = """
SELECT
    ex.exercise_id,
    ex.workout_id,
    ex.name,
    ex.normalized_name,
    ex.order_index,
    ex.equipment_type,
    ex.target_type,
    COUNT(s.set_id) AS set_count,
    COALESCE(SUM(CASE WHEN s.skipped = 0 THEN 1 ELSE 0 END), 0)
        AS completed_set_count,
    MAX(CASE WHEN s.skipped = 0 THEN s.weight END) AS max_weight_kg
FROM workout_exercises ex
LEFT JOIN workout_sets s ON s.exercise_id = ex.exercise_id
{where}
GROUP BY ex.exercise_id
ORDER BY ex.normalized_name ASC, ex.workout_id ASC, ex.order_index ASC
{limit}
"""

_SET_SUMMARY_SQL = """
SELECT
    s.set_id,
    s.exercise_id,
    ex.workout_id,
    s.set_index,
    s.value_raw,
    s.amount_raw,
    s.weight,
    s.weight_unit,
    s.reps,
    s.reps_open_ended,
    s.target_type,
    (1 - s.skipped) AS completed,
    w.started_at,
    ex.name
FROM workout_sets s
JOIN workout_exercises ex ON ex.exercise_id = s.exercise_id
JOIN workouts w ON w.workout_id = ex.workout_id
{where}
ORDER BY w.started_at DESC, w.workout_id ASC,
         ex.order_index ASC, s.set_index ASC
{limit}
"""


def _date_floor(day: str) -> str:
    return f"{day}T00:00:00+00:00"


def _date_ceil(day: str) -> str:
    return f"{day}T23:59:59+00:00"


class WorkoutQueryService:
    """Read-only deterministic query surface over a workout database.

    The service never mutates state; it shares the caller's store/connection.
    """

    def __init__(self, store: WorkoutStore) -> None:
        self._store = store
        self._connection = store._connection

    def get_workout(self, workout_id: str) -> Workout | None:
        """Return one full workout with exercises and sets, or ``None``."""
        return self._store.get_workout(workout_id)

    def list_workouts(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        program_id: str | None = None,
        source_file: str | None = None,
        limit: int | None = None,
    ) -> list[WorkoutSummary]:
        """List workouts newest-first with aggregates.

        ``date_from``/``date_to`` are inclusive ``YYYY-MM-DD`` bounds on the
        session start. All filters are optional and combinable.
        """
        clauses: list[str] = []
        params: list[object] = []
        if date_from is not None:
            clauses.append("w.started_at >= ?")
            params.append(_date_floor(date_from))
        if date_to is not None:
            clauses.append("w.started_at <= ?")
            params.append(_date_ceil(date_to))
        if program_id is not None:
            clauses.append("w.program_id = ?")
            params.append(program_id)
        if source_file is not None:
            clauses.append("w.source_file = ?")
            params.append(source_file)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = _WORKOUT_SUMMARY_SQL.format(where=where, limit=_limit_clause(limit))
        if limit is not None:
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [_row_to_workout_summary(row) for row in rows]

    def list_exercises(
        self,
        *,
        workout_id: str | None = None,
        name: str | None = None,
        limit: int | None = None,
    ) -> list[ExerciseSummary]:
        """List exercises, optionally restricted to one workout or a name.

        ``name`` compares against the case-insensitive normalized name, so
        ``bench press`` matches ``Bench Press (Barbell)``.
        """
        clauses: list[str] = []
        params: list[object] = []
        if workout_id is not None:
            clauses.append("ex.workout_id = ?")
            params.append(workout_id)
        if name is not None:
            clauses.append("ex.normalized_name LIKE ?")
            params.append(f"%{' '.join(name.lower().split())}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = _EXERCISE_SUMMARY_SQL.format(where=where, limit=_limit_clause(limit))
        if limit is not None:
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [_row_to_exercise_summary(row) for row in rows]

    def list_sets(
        self,
        *,
        workout_id: str | None = None,
        exercise_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        skipped: bool | None = None,
        limit: int | None = None,
    ) -> list[SetSummary]:
        """List sets in workout order, optionally filtered.

        ``skipped=True`` returns only skipped sets; ``skipped=False`` only
        completed sets.
        """
        clauses: list[str] = []
        params: list[object] = []
        if workout_id is not None:
            clauses.append("ex.workout_id = ?")
            params.append(workout_id)
        if exercise_id is not None:
            clauses.append("s.exercise_id = ?")
            params.append(exercise_id)
        if date_from is not None:
            clauses.append("w.started_at >= ?")
            params.append(_date_floor(date_from))
        if date_to is not None:
            clauses.append("w.started_at <= ?")
            params.append(_date_ceil(date_to))
        if skipped is not None:
            clauses.append("s.skipped = ?")
            params.append(int(skipped))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = _SET_SUMMARY_SQL.format(where=where, limit=_limit_clause(limit))
        if limit is not None:
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        return [_row_to_set_summary(row) for row in rows]

    def search(self, query: str, *, limit: int = 10) -> list[WorkoutSearchResult]:
        """Search workouts by movement name; newest first, deterministic.

        A workout matches when at least one of its exercises has a normalized
        name containing any token of ``query`` (the same case-insensitive
        comparison ``list_exercises`` uses). The comparison uses non-empty
        tokens of at least three characters so conversational filler words do
        not over-broaden the match. Hits carry the matching exercise display
        names and the usual workout aggregates. Read-only: repeated calls with
        identical data return identical rows.
        """
        if limit < 1:
            raise ValueError("limit must be >= 1")
        tokens = [token for token in query.split() if len(token) >= 3]
        if not tokens:
            raise ValueError(
                "query must contain at least one searchable movement token"
            )
        likes = " OR ".join(["ex.normalized_name LIKE ?"] * len(tokens))
        rows = self._connection.execute(
            f"""
            SELECT ex.workout_id, ex.name
            FROM workout_exercises ex
            WHERE {likes}
            ORDER BY ex.workout_id ASC, ex.order_index ASC
            """,
            [f"%{token}%" for token in tokens],
        ).fetchall()
        matched: dict[str, list[str]] = {}
        order: list[str] = []
        for workout_id, name in rows:
            sid = str(workout_id)
            if sid not in matched:
                matched[sid] = []
                order.append(sid)
            exercise_name = str(name)
            if exercise_name not in matched[sid]:
                matched[sid].append(exercise_name)
        if not order:
            return []
        placeholders = ", ".join("?" * len(order))
        params: list[object] = list(order)
        if limit is not None:
            params.append(limit)
        summaries = self._connection.execute(
            _WORKOUT_SUMMARY_SQL.format(
                where=f"WHERE w.workout_id IN ({placeholders})",
                limit=_limit_clause(limit),
            ),
            params,
        ).fetchall()
        results: list[WorkoutSearchResult] = []
        for row in summaries:
            summary = _row_to_workout_summary(row)
            results.append(
                WorkoutSearchResult(
                    workout_id=summary.workout_id,
                    name=summary.name,
                    started_at=summary.started_at,
                    activity_type=summary.activity_type,
                    duration_seconds=summary.duration_seconds,
                    program_id=summary.program_id,
                    exercise_count=summary.exercise_count,
                    set_count=summary.set_count,
                    total_volume_kg=summary.total_volume_kg,
                    matched_exercises=matched.get(summary.workout_id, [])[:5],
                )
            )
        return results

    def stats(self) -> dict[str, object]:
        """Deterministic aggregate metadata over the whole dataset.

        ``by_activity_type`` repeats the totals per activity type so the shape
        is stable for clients (an empty dataset reports a single ``None``
        bucket, keeping the key runnable everywhere).
        """
        row = self._connection.execute(
            """
            SELECT
                COUNT(DISTINCT w.workout_id) AS workout_count,
                COUNT(DISTINCT ex.exercise_id) AS exercise_count,
                COUNT(DISTINCT ex.normalized_name) AS movement_count,
                COUNT(s.set_id) AS set_count,
                COALESCE(SUM(CASE WHEN s.skipped = 0 THEN 1 ELSE 0 END), 0)
                    AS completed_set_count,
                COALESCE(SUM(
                    CASE WHEN s.skipped = 0 AND s.weight IS NOT NULL
                         AND s.reps IS NOT NULL
                    THEN s.weight * s.reps ELSE 0 END
                ), 0) AS total_volume_kg,
                COALESCE(SUM(w.duration_seconds), 0) AS total_duration_seconds
            FROM workouts w
            LEFT JOIN workout_exercises ex ON ex.workout_id = w.workout_id
            LEFT JOIN workout_sets s ON s.exercise_id = ex.exercise_id
            """
        ).fetchone()
        values = tuple(row)
        activity_rows = self._connection.execute(
            """
            SELECT
                w.activity_type,
                COUNT(DISTINCT w.workout_id),
                COUNT(DISTINCT ex.exercise_id),
                COUNT(s.set_id),
                COALESCE(SUM(CASE WHEN s.skipped = 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(
                    CASE WHEN s.skipped = 0 AND s.weight IS NOT NULL
                         AND s.reps IS NOT NULL
                    THEN s.weight * s.reps ELSE 0 END
                ), 0)
            FROM workouts w
            LEFT JOIN workout_exercises ex ON ex.workout_id = w.workout_id
            LEFT JOIN workout_sets s ON s.exercise_id = ex.exercise_id
            GROUP BY w.activity_type
            ORDER BY w.activity_type ASC
            """
        ).fetchall()
        by_activity = {}
        for activity_row in activity_rows:
            activity = str(activity_row[0] or "")
            by_activity[activity] = {
                "workout_count": int(activity_row[1]),
                "exercise_count": int(activity_row[2]),
                "set_count": int(activity_row[3]),
                "completed_set_count": int(activity_row[4]),
                "total_volume_kg": float(activity_row[5]),
            }
        return {
            "workout_count": int(values[0]),
            "exercise_count": int(values[1]),
            "movement_count": int(values[2]),
            "set_count": int(values[3]),
            "completed_set_count": int(values[4]),
            "total_volume_kg": float(values[5]),
            "total_duration_seconds": float(values[6]),
            "by_activity_type": by_activity,
        }


def _limit_clause(limit: int | None) -> str:
    return "LIMIT ?" if limit is not None else ""


def _row_to_workout_summary(row: sqlite3.Row | tuple[object, ...]) -> WorkoutSummary:
    values = tuple(row)
    return WorkoutSummary(
        workout_id=str(values[0]),
        name=str(values[1]),
        started_at=str(values[2]),
        ended_at=values[3],
        activity_type=str(values[4]),
        duration_seconds=values[5],
        program_id=values[6],
        exercise_count=int(values[7]),
        set_count=int(values[8]),
        completed_set_count=int(values[9]),
        total_volume_kg=float(values[10] or 0),
    )


def _row_to_exercise_summary(row: sqlite3.Row | tuple[object, ...]) -> ExerciseSummary:
    values = tuple(row)
    return ExerciseSummary(
        exercise_id=str(values[0]),
        workout_id=str(values[1]),
        name=str(values[2]),
        normalized_name=str(values[3]),
        order_index=int(values[4]),
        equipment_type=values[5],
        target_type=values[6],
        set_count=int(values[7]),
        completed_set_count=int(values[8]),
        max_weight_kg=values[9],
    )


def _row_to_set_summary(row: sqlite3.Row | tuple[object, ...]) -> SetSummary:
    values = tuple(row)
    return SetSummary(
        set_id=str(values[0]),
        exercise_id=str(values[1]),
        workout_id=str(values[2]),
        set_index=int(values[3]),
        value_raw=str(values[4] or ""),
        amount_raw=str(values[5] or ""),
        weight=values[6],
        weight_unit=values[7],
        reps=values[8],
        reps_open_ended=bool(values[9]),
        target_type=values[10],
        completed=bool(values[11]),
        started_at=str(values[12]),
        exercise_name=str(values[13]),
    )


__all__ = ["WorkoutQueryService"]
