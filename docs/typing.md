# Type checking status

`mypy` runs over the full `src/personal_ai` package in CI. It is a real gate,
not a placeholder, but the package currently carries pre-existing type debt and
the gate is enforced through a ratchet rather than a zero-error requirement.

## How the gate works

`scripts/mypy_ratchet.py` runs mypy, collects every error as a
`(file, error code, message)` signature, and compares that set against
`config/mypy-baseline.json`.

* No error code is disabled, so nothing is masked.
* A new error, or an extra occurrence of an already-recorded error, fails CI.
* Line numbers are excluded from signatures, so unrelated edits do not churn
  the baseline.
* Fixing errors only shrinks the baseline.
* Re-run `uv run python scripts/mypy_ratchet.py --update` to accept new debt
  deliberately, or `--report` for a non-failing summary.

The script exits non-zero if mypy itself cannot be executed, so a broken
environment can never be mistaken for a clean run.

## Configuration

Defined in `pyproject.toml` under `[tool.mypy]`:

* `files = ["src/personal_ai"]` — the real package, not a single empty module.
* `python_version = "3.14"` — matches `.python-version` and `requires-python`.
* `mypy_path = ["job_agent"]` plus a `job_agent.*` override with
  `follow_imports = "skip"`. `personal_ai/server.py` imports the companion
  `job_agent` distribution, which is a separate package
  (`job_agent/pyproject.toml`) and is not installed into the root environment.
  The override lets those imports resolve without expanding type checking to
  a second distribution.
* `types-PyYAML` and `types-regex` are dev dependencies so `yaml` and `regex`
  imports are checked instead of reported as untyped.

## Current debt

384 errors across 47 of 122 files, collapsing to 267 distinct signatures.
The distribution:

| Code | Count | Nature |
| --- | --- | --- |
| `attr-defined` | 211 | Mostly attribute access on a parameter annotated `object` |
| `arg-type` | 69 | Same root cause, widened argument types |
| `union-attr` | 39 | Optional/union attributes accessed without narrowing |
| `call-overload` | 20 | Overload selection on wide types |
| `assignment` | 17 | Assignment incompatible with declared type |
| `index`, `operator`, `return-value`, `misc`, other | 28 | Mixed |

Two dominant root causes:

1. **53 helper functions take an `object` parameter** and then read typed
   attributes off it, for example
   `_workout_summary_dict(workout: object)` in `src/personal_ai/server.py:849`.
   These helpers are duck-typed adapters over the `job_agent` and
   ingestion models, which are not importable from the root environment.
   Annotating them properly means defining a shared structural type, which is
   a larger refactor than this CI pass should take on.
2. **Optional and enum-union attributes** are read before narrowing, mostly
   in `src/personal_ai/memory/` and `src/personal_ai/ingestion.py`.

## Genuine defects found and fixed

`src/personal_ai/server.py` — the artifact upload endpoint read `.read()`,
`.filename`, and `.content_type` off `form["file"]` after only a
`getattr(file, "filename", "")` check. `starlette.datastructures.FormData` is
typed as holding `UploadFile | str`, so a text field submitted under the name
`file` would reach `await file.read()` and raise `AttributeError` instead of
returning a 400. The handler now narrows with an explicit
`isinstance(uploaded, UploadFile)` check and reports a proper 400. This removed
4 errors and one genuine crash path.

`src/personal_ai/server.py` — the `/api/jobs/discover` handler called
`job_db.record_discovery_run` without the `run_id` and `started_at` arguments
the Job-Agent DB API requires, so every discovery request that reached that
line raised `TypeError` and returned HTTP 500. The handler now generates a
`run_id` per request and passes the run start timestamp.

## Known non-defects

11 `type: ignore[...]` comments name a code that mypy does not raise on that
line, reported as `not covered by "type: ignore[...]" comment`. These are
stale annotations, not runtime bugs, and are recorded in the baseline.

No test carries `@pytest.mark.integration`, and no integration marker is
registered in `pyproject.toml`. CI therefore runs the whole suite with a plain
`uv run pytest` rather than a `-m` filter that would silently exclude nothing
while looking like it scoped the run.

`JOB_AGENT_AVAILABLE` in `src/personal_ai/server.py` is a deliberately shallow
probe. It imports `job_agent.db`, which needs none of the discovery stack's
third-party dependencies, so it reports True when only the database layer is
present. The discovery endpoints need more than that, which is why the tests
in `tests/test_server_job_agent.py` probe the discovery modules directly. The
quality job installs the `job_agent` package (`uv pip install -e ./job_agent`)
so those tests execute rather than skip. It is installed as a separate
distribution rather than a workspace member: the root suite requires
`pytest>=9` while `job_agent` pins `pytest<9`, so a shared workspace cannot
resolve both.

## Reducing the debt

Fixing an error and re-running with `--update` is the intended workflow. The
highest-value target is the `object`-parameter helper group: a single shared
`Protocol` or `TypedDict` per adapter would address a large share of the 211
`attr-defined` and 69 `arg-type` errors at once.
