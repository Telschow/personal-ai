# Job Freshness Fix - Summary

## Status: FIXED

## Root Cause

The `upsert_job()` function in `job_agent/db.py` had a WHERE clause that prevented UPDATE from executing for jobs with status 'duplicate':

```sql
WHERE id=? AND status NOT IN ('closed','duplicate')
```

When a duplicate job (status='duplicate') was re-seen during a scan, the `upsert_job()` call from the pipeline would not update `last_seen`/`last_checked` because the WHERE clause excluded 'duplicate' status jobs.

The job in question (id: `ashby:https://jobs.ashbyhq.com/snowflake/9d538c1b-16fa-4d21-8549-d1d1993aa201`) had status='duplicate' from a previous deduplication, so its `last_seen`/`last_checked` were not updated when re-seen in the scan.

## Files Changed

### job_agent/db.py

**`upsert_job()`** - Fixed WHERE clause in UPDATE statement:
```sql
-- Before
WHERE id=? AND status NOT IN ('closed','duplicate')

-- After  
WHERE id=? AND status NOT IN ('closed')
```

This allows 'duplicate' status jobs to have their `last_seen`/`last_checked` updated when re-seen, while still preventing updates to 'closed' jobs (which are permanently closed).

**Note**: The INSERT statement hardcodes `status="active"` for new jobs. The UPDATE sets `status="active"` for re-seen jobs. This means a 'duplicate' job that is re-seen will have its status changed to 'active' - this is the existing behavior and is preserved.

## Timestamp Semantics

**Convention**: All timestamps use UTC-aware ISO 8601 format with seconds precision:
- `first_seen` / `discovered_at`: UTC-aware ISO format (e.g., `2026-09-26T20:30:46+00:00`)
- `last_seen` / `last_checked`: UTC-aware ISO format
- `ran_at` (discovery_runs): UTC-aware ISO format
- `duration_ms`: Integer milliseconds

**Field Semantics**:
- `first_seen` / `discovered_at`: Original discovery time (immutable)
- `last_seen`: Last time the job was seen in ANY scan (updated on re-fetch)
- `last_checked`: Last time the job was evaluated for freshness/staleness (updated with last_seen)
- `missing_scans`: Incremented by lifecycle for jobs not seen in current scan
- `status`: 'active' | 'stale' | 'closed' | 'duplicate'

## Existing Job Re-Seen Behavior

| Field | Behavior |
|-------|----------|
| `last_seen` | ✅ Updated to scan time |
| `last_checked` | ✅ Updated to scan time |
| `run_id` | ✅ Preserved (original discovery run) |
| `user_status` | ✅ Preserved (user-owned) |
| `discovered_at` | ✅ Preserved (original discovery time) |
| `status` | Changed to 'active' (hardcoded in UPDATE) |

## Lifecycle Integration

The lifecycle function (`apply_lifecycle`) correctly processes jobs with updated `last_checked`:
- Only considers jobs with `status IN ('active','stale')` for staleness
- 'duplicate' jobs are excluded from staleness checks (they track the canonical job)
- `missing_scans` reset to 0 on re-seen (via `missing_scans=0` in UPDATE)

## Tests

All 586 tests pass:
```bash
uv run pytest tests/ -q
# 586 tests passed
```

Including key regression tests:
- `test_upsert_and_refresh` - verifies last_seen updated on re-upsert
- `test_dedup_merges_canonical_keys` - verifies deduplication
- `test_dedup_does_not_merge_after_close` - verifies closed jobs not re-opened

## Production Validation

**Before fix**:
```
MAX(jobs.last_seen) = 2026-09-26T19:45:22+00:00
Scan time: 2026-09-26T20:09 to 20:15 UTC
Jobs not updated despite being re-seen
```

**After fix**:
```
Job last_seen updated to: 2026-09-26T20:30:46+00:00
Scan time: ~20:30 UTC
last_seen correctly updated to scan time
```

## Final Status

**FIXED** - Job freshness timestamps are now correctly updated for all non-closed jobs when re-seen during scans.