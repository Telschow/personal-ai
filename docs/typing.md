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

* `files = ["src/personal_ai"]`: the real package, not a single empty module.
* `python_version = "3.12"`: the lowest supported version, matching `requires-python`.
* `types-PyYAML` and `types-regex` are dev dependencies so `yaml` and `regex`
  imports are checked instead of reported as untyped.

## Current debt

383 errors across 47 of 127 files, collapsing to 266 distinct signatures. Reproduce with `uv run python scripts/mypy_ratchet.py --report`.
The distribution:

| Code | Count | Nature |
| --- | --- | --- |
| `attr-defined` | 211 | Mostly attribute access on a parameter annotated `object` |
| `arg-type` | 68 | Same root cause, widened argument types |
| `union-attr` | 39 | Optional/union attributes accessed without narrowing |
| `call-overload` | 20 | Overload selection on wide types |
| `assignment` | 17 | Assignment incompatible with declared type |
| `index`, `operator`, `return-value`, `misc`, other | 28 | Mixed |

Two dominant root causes:

1. **53 helper functions take an `object` parameter** and then read typed
   attributes off it, for example
   `_workout_summary_dict(workout: object)` in `src/personal_ai/server.py:800`.
   These helpers are duck-typed adapters over the ingestion and workout
   models. Annotating them properly means defining a shared structural type, which is
   a larger refactor than this CI pass should take on.
2. **Optional and enum-union attributes** are read before narrowing, mostly
   in `src/personal_ai/memory/` and `src/personal_ai/ingestion.py`.

## Known non-defects

11 `type: ignore[...]` comments name a code that mypy does not raise on that
line, reported as `not covered by "type: ignore[...]" comment`. These are
stale annotations, not runtime bugs, and are recorded in the baseline.

No test carries `@pytest.mark.integration`, and no integration marker is
registered in `pyproject.toml`. CI therefore runs the whole suite with a plain
`uv run pytest` rather than a `-m` filter that would silently exclude nothing
while looking like it scoped the run.

## Reducing the debt

Fixing an error and re-running with `--update` is the intended workflow. The
highest-value target is the `object`-parameter helper group: a single shared
`Protocol` or `TypedDict` per adapter would address a large share of the 211
`attr-defined` and 68 `arg-type` errors at once.
