"""Workout activity dataset: parsing, storage, and read-only queries.

Workouts are a self-contained domain data source (like memories) that lives
beside — never inside — the control plane. They are normalized from raw
exports (currently the Boostcamp app CSV) into a SQLite store with
deterministic identities, idempotent re-import, and a read-only query
service for analytics and future agent tools.

Pipeline::

    Export file → WorkoutParser → WorkoutNormalizer → WorkoutStore → WorkoutQueryService

Workout data never becomes documents, memories, or embeddings, and parsing is
pure local deterministic code — no LLM, no network.
"""

from personal_ai.workouts.models import (
    DEFAULT_ACTIVITY_TYPE,
    SOURCE_TYPE_BOOSTCAMP,
    ExerciseSummary,
    RowWarning,
    SetSummary,
    Workout,
    WorkoutExercise,
    WorkoutImportResult,
    WorkoutRecord,
    WorkoutSet,
    WorkoutSummary,
    compute_content_hash,
    exercise_id,
    set_id,
    workout_id,
)
from personal_ai.workouts.parser import (
    UnsupportedWorkoutFormatError,
    WorkoutParseError,
    detect_source_type,
    parse_boostcamp_csv,
    parse_workout_file,
)
from personal_ai.workouts.query import WorkoutQueryService
from personal_ai.workouts.store import (
    WorkoutStore,
    import_workout_directory,
    open_workout_store,
)

__all__ = [
    "DEFAULT_ACTIVITY_TYPE",
    "SOURCE_TYPE_BOOSTCAMP",
    "ExerciseSummary",
    "RowWarning",
    "SetSummary",
    "UnsupportedWorkoutFormatError",
    "Workout",
    "WorkoutExercise",
    "WorkoutImportResult",
    "WorkoutParseError",
    "WorkoutQueryService",
    "WorkoutRecord",
    "WorkoutSet",
    "WorkoutStore",
    "WorkoutSummary",
    "compute_content_hash",
    "detect_source_type",
    "exercise_id",
    "import_workout_directory",
    "open_workout_store",
    "parse_boostcamp_csv",
    "parse_workout_file",
    "set_id",
    "workout_id",
]
