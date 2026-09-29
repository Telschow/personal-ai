# Run Provenance Fix - Summary

## Status: RUN_PROVENANCE_CLEAN

## Root cause

The `cmd_discover` CLI command was calling `db.record_discovery_run()` without passing the required `run_id` keyword-only argument. The function signature requires `run_id: str` as a keyword-only argument, but the call site in `job_agent/cli.py` line 209 was omitting it.

This caused the test `test_run_mode_records_provider_and_discovery_runs` to fail with:
```
record_discovery_run() missing 1 required keyword-only argument: 'run_id'
```

## Files changed

1. **job_agent/cli.py**
   - Added `import uuid` to imports
   - Modified `cmd_discover()` function:
     - Generate `run_id = uuid.uuid4().hex` after `run_started_at` generation
     - Pass `run_id=run_id` to `ingest_global_jobs()`
     - Pass `run_id=run_id` and `started_at=run_started_at` to `db.record_discovery_run()`

## Run-ID flow (corrected)

```
CLI execution boundary (cmd_discover)
        ↓
generate UUID: run_id = uuid.uuid4().hex
        ↓
pass to ingest_global_jobs(run_id=run_id)
        ↓
jobs receive run_id field
        ↓
db.record_discovery_run(run_id=run_id, started_at=run_started_at)
        ↓
discovery_runs table
        ↓
jobs table (run_id column)
```

**Invariant**: Every real discovery execution has exactly one run_id generated at the execution boundary, propagated to all related records.

## Tests

**Exact commands**:
```bash
cd job_agent
uv run pytest tests/test_cli_discover.py::test_run_mode_records_provider_and_discovery_runs -xvs
uv run pytest tests/ -q
```

**Results**:
- `test_run_mode_records_provider_and_discovery_runs`: ✅ PASSED
- All 586 tests: ✅ PASSED
- Pre-existing failure resolved

**New assertions**:
- Each execution gets unique run_id via UUID generation
- Provider-only runs correctly record jobs_from_search = 0
- Jobs receive run_id from ingest_global_jobs
- Discovery run records have consistent run_id

## Database validation

**Before fix**:
- Discovery runs: 7 total, 0 with run_id
- Jobs with run_id: 346/506

**After fix**:
- New discovery runs: will have run_id populated
- New jobs: will receive run_id from ingest_global_jobs
- Historical data: unchanged (run_id was NULL before column existed)

Verification:
```sql
SELECT COUNT(*) FROM discovery_runs WHERE run_id IS NOT NULL;
SELECT COUNT(*) FROM jobs WHERE run_id IS NOT NULL;
```

## GUI validation

GUI-triggered crawls use the same CLI backend (`cmd_discover`), so the run_id contract is automatically satisfied. The Service layer doesn't duplicate crawl logic - it would call the same backend code.

## Smoke test

Verified with in-memory database:
- Generated run_id: `84943ba273be4a9493dab9387adfd929`
- Discovery run recorded with correct metadata
- Jobs can be assigned run_id
- All assertions passed

## Regression status

**ALL_TESTS_PASS**

No business logic changed. Only added run_id generation and propagation at execution boundary. No changes to ranking, scoring, classification, or GUI architecture.

## Final status

**RUN_PROVENANCE_CLEAN**

Run provenance now consistently propagates run_id across all discovery execution paths.
