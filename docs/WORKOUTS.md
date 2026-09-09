# Workout dataset (legacy Phase 43)

A local-first, normalized activity dataset that lives **beside** the control
plane (like memories): parsed from raw exports into a SQLite store with
deterministic identities, idempotent re-import, and a read-only query surface.
No network, no Ollama, no vector database, no LLM — parsing is pure
deterministic Python.

## What this is and is not

| Concept | Definition |
| --- | --- |
| **Workout** | One completed (or logged) exercise session from the user's own history: exercises, sets, loads, notes, program position. |
| **Export source** | A file from the Boostcamp app that this phase knows how to parse (`Workout/Boostcamp.csv`). |
| **Memory / corpus** | Unchanged — workouts are **neither** documents nor memories; no embedding, no memory creation, no agent mutation this phase. |

## Pipeline

```
Boostcamp export (.csv)
      │
      ▼
WorkoutParser        detect_source_type → parse_workout_file → WorkoutRecord[]
      │
      ▼
WorkoutStore         idempotent import_records (transactional, per-file manifest)
      │
      ▼
WorkoutQueryService  read-only deterministic queries (CLI today, tools later)
```

## Domain model (`personal_ai/workouts/models.py`)

- `WorkoutSet` — one logged attempt: raw cells (`value_raw`, `amount_raw`)
  next to deterministic normalizations (`weight`, `reps`,
  `reps_open_ended` for `5+`-style targets), explicit units (`weight_unit`,
  `intensity_unit`), `intensity` (scalar, e.g. 75%) vs `intensity_raw` (JSON,
  e.g. RPE ranges `[7,8]`), `skipped`, `custom`, `source`.
- `WorkoutExercise` — a movement with ordered sets. Superset shells (type
  `Superset`) are **flattened**: each child becomes its own exercise.
- `WorkoutRecord` — a normalized session ready for storage; the parser output.
- `Workout` — a persisted, decoded session (with exercises/sets loaded).
- `WorkoutSummary`, `ExerciseSummary`, `SetSummary` — deterministic list
  projections with aggregates (counts, completed counts, max load,
  `total_volume_kg` = Σ weight × reps over completed numeric sets).
- `WorkoutImportResult` — structured outcome: seen/imported/skipped files,
  created/updated/skipped workouts, per-row `RowWarning`s, fatal `errors`.
  `to_dict()` is the `--json` CLI contract. `records_with_warnings` is a
  derived property.
- `RowWarning` — a recoverable per-row problem (row is skipped or its
  unsalvageable field is dropped); fatal file-level problems raise
  `WorkoutParseError` / `UnsupportedWorkoutFormatError`.

### Deterministic identity

Ids are hashes of **source identity, never free text** (safe as log fields and
keys without leaking content):

| Record | Identity inputs | Prefix |
| --- | --- | --- |
| Workout | `(source_type, source_file, source_id)` | `wkt-` |
| Exercise | `(workout_id, order_index, source_exercise_id)` | `ext-` |
| Set | `(exercise_id, set_index)` | `set-` |

`compute_content_hash` covers the normalized core of a workout (timestamps,
program/week/day, aggregates, ordered exercises/sets incl. raw cells and
normalizations). Equal hash ⇒ unchanged ⇒ reported `skipped` on re-import.

## Timestamps

Boostcamp export timestamps are **day-first** (`D/M/YYYY`, e.g.
`9/8/2024 14:35:44.588+00` = 9 August 2024), with an hours-only UTC offset and
arbitrary fractional-second width. This was verified against the export's own
`Aug 09 Workout`-style titles (76/79 titled rows agree with D/M/Y; the 2
mismatches are sessions that ran across midnight and were titled by their
finish date) and the many day values > 12. `_normalize_timestamp` applies the
offset and emits zero-padded UTC ISO-8601 with sub-second precision dropped
for deterministic, comparable timestamps.

## Storage (`personal_ai/workouts/store.py`)

- `WorkoutStore(connection, *, now=None)` reuses the shared
  `connect_database` connection and creates its tables with
  `CREATE TABLE IF NOT EXISTS` + indexes, exactly like the memory/orchestration
  stores. Workouts co-locate in one SQLite file by default.
- Tables: `workouts` (`UNIQUE(source_file, source_id)`), `workout_exercises`
  (FK cascade, `UNIQUE(workout_id, order_index)`), `workout_sets` (FK cascade,
  `CHECK(set_index >= 1)`, `UNIQUE(exercise_id, set_index)`), and
  `workout_sources` — a per-file manifest (checksum, size, imported/updated).
- `import_records` is transactional and idempotent: unchanged workouts are
  `skipped`, changed ones are `updated` (children replaced — an exercise that
  disappears from the source disappears from storage), new ones `created`.
- `open_workout_store(path)` is the standalone convenience entry (tests, CLI).
- `import_workout_directory(directory, store)` discovers `.csv` files
  recursively (never outside the directory), fingerprints format by header,
  and **skips whole files whose checksum already matches the manifest** —
  re-running ingestion never re-parses unchanged files.

## Query surface (`personal_ai/workouts/query.py`)

`WorkoutQueryService` is read-only (no writes, no parse, no storage internals
exposed) and deterministic (workouts newest-first; ties by id; exercises by
normalized name; sets in workout order). Methods: `get_workout`,
`list_workouts(date_from / date_to / program_id / source_file / limit)`,
`list_exercises(workout_id / name / limit)` (name matches the normalized name,
so `bench press` finds `Bench Press (Barbell)`), `list_sets(workout_id /
exercise_id / date_from / date_to / skipped / limit)`, `search(query, limit)`
(movement-name keyword search over workouts), and `stats()` (workout/exercise/
set counts by activity type).

## CLI

A dedicated verb on the main CLI, dispatched before agent parsing:

```
personal-ai workouts import PATH [--database DB] [--json]
personal-ai workouts list [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD]
                [--program ID] [--limit N] [--database DB] [--json]
personal-ai workouts show WORKOUT_ID [--database DB] [--json]
personal-ai workouts exercises [--name NAME] [--workout ID] [--limit N]
                [--database DB] [--json]
```

`--database` is required (data is co-located in the same SQLite file as memory
and orchestration state). `import` accepts a directory (recursive discovery)
or a single `.csv`. Plain output is human-readable summaries; `--json` emits
the structured contract (`WorkoutImportResult.to_dict()`, workout/exercise/
set dicts).

## Agent tool and HTTP gateway (legacy Phase 44)

The `search_workouts` chat tool is registered only when the default tool
registry is built with a `WorkoutQueryService`:
`create_default_registry(workout_service=...)`. It is a read-only movement-name
query tool (`{query: str required, limit?: int}`); its handler goes through the
same policy engine as every other tool (permission `researcher.search_workouts`),
so chat can ask "how often do I bench press?" and the answer is grounded in the
same normalized store the CLI queries. Without a wired service the tool is
absent and the agent behaves exactly as before.

Read-only workout endpoints are also served by the HTTP gateway
(`personal_ai.server`): `/api/workouts` (list), `/api/workouts/stats`,
`/api/workouts/history`, `/api/workouts/exercises`, `/api/workouts/{id}` — all
1:1 onto `WorkoutQueryService`, never onto the store.

## Testing

New suites: `test_workouts_parser.py` (day-first timestamps, month-name title
cross-check, UTC offset normalization, superset flattening, skipped / open
-ended sets, bodyweight and RPE sets, malformed rows → warnings, deterministic
ids), `test_workouts_store.py` (schema, transactional idempotent import,
created/updated/skipped, child replacement on update, directory discovery +
checksum skip, manifest, injectable `now`, file identity scoping),
`test_workouts_query.py` (ordering, date range inclusive bounds, program /
source-file / name filters, aggregate correctness, skipped-set filters,
no-mutation), `test_workouts_search.py` (movement-name search + stats),
`test_agent_tool_workouts.py` (policy-gated `search_workouts` agent tool,
optional registration), `test_workouts_cli.py` (offline end-to-end: import/
list/show/exercises, `--json` shapes, error exits). Shared sanitized fixtures
live in
`tests/workouts_fixtures.py` — invented values only, never real personal data.
All offline: `tmp_path` SQLite, no network, no Ollama.

The full real export was also smoke-tested against a throwaway `/tmp` database
(412/412 workouts parsed with zero warnings or errors; re-import skipped the
unchanged file).

## Deliberately out of scope (legacy Phase 43)

- **Workouts are not memories or documents** — no automatic memory creation,
  no embeddings, no retrieval index. The read-only `search_workouts` agent tool
  exists (legacy Phase 44), enters through `ToolRegistry`/policy like every other
  tool, and is fully optional: no data becomes model-visible unless the agent
  is explicitly given the tool.
- Semantic/trend analytics (PR curves, program comparisons) — the normalized
  model supports them, but they are future work.
- Additional export formats (Hevy, Strong, Apple Health, …) — a new `detect`
  branch and parser slots into the same store without schema change.