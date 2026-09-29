# Provider Hardening Summary

## Status: PROVIDER_HARDENING_COMPLETE

## Changes Made

### 1. SmartRecruiters Source (sources.py)
- **Fixed**: `'str' object has no attribute 'get'` error by adding defensive parsing
- **Added**: Response shape validation (top-level must be dict, `content` must be list)
- **Added**: Per-posting validation (skip non-dict entries, handle non-dict `ref`)
- **Added**: Structured logging for malformed postings

### 2. Greenhouse Source (sources.py)
- **Fixed**: Unbounded pagination and timeout issues
- **Added**: Per-request timeout (15s connect/read, 5s connect)
- **Added**: Hard page limit (50 pages max)
- **Added**: Response size limit (5MB)
- **Added**: Structured logging for timeouts and HTTP errors

### 3. Ashby Source (sources.py)
- **Added**: Response shape validation
- **Added**: Per-posting validation (skip non-dict entries)
- **Added**: Missing job URL handling (falls back to title)
- **Added**: Max page limit (50 pages)
- **Added**: Structured logging for malformed responses/postings

### 4. Lever Source (sources.py)
- **Added**: Response validation (must be list)
- **Added**: Per-posting validation (skip non-dict entries)
- **Added**: Structured logging for malformed postings

### 5. Workable Source (sources.py)
- **Added**: Items validation (must be list)
- **Added**: Structured logging for malformed items

### 6. DirectPageSource (sources.py)
- **Fixed**: Timeout handling (wraps in SourceError after 3 attempts)
- **Added**: Zero-candidate diagnostics (logs `jsonld_blocks`, `jobposting_found`, `page_size`)
- **Added**: JSON-LD parse error logging
- **Improved**: Retry logic for transient HTTP errors

### 7. Logging Infrastructure (sources.py)
- **Added**: `log` and `log_event` imports
- **Added**: Structured logging for all providers

## Tests Added

### test_provider_regressions.py (18 tests)
- SmartRecruiters: valid response, malformed top-level, missing content, string in content, non-dict ref
- Greenhouse: pagination bounded, timeout, HTTP error
- DirectPageSource: diagnostics, timeout wrapped in SourceError
- Ashby: malformed response, malformed posting, invalid jobs field, missing job ID
- Lever: invalid response, malformed posting
- Workable: malformed items
- Source isolation verification

## Test Results

```
586 tests passed (including 18 new regression tests)
```

## Production Scan Results (dry-run, no-global-search)

### Sources Tested

| Source | Status | Candidates Fetched | Jobs Accepted | Failures |
|--------|--------|-------------------|---------------|----------|
| **Greenhouse** | ✅ Working | 155 | 155 | 3 (1 response too large, 2 HTTP 404) |
| **Ashby** | ✅ Working | 349 | 337 | 2 (errors logged, scan continues) |
| **SmartRecruiters** | ✅ Fixed | 217 (100+100+17) | 211 | ~40 (0-result companies, scan continues) |
| **Direct** | ✅ Diagnostics | 0 | 0 | 2 timeouts, 50+ zero-result (no JSON-LD JobPosting) |

### Key Observations

1. **SmartRecruiters**: Now parses correctly! Previously crashed with `'str' object has no attribute 'get'`. Now handles malformed responses gracefully and continues scanning other companies.

2. **Greenhouse**: Pagination bounded, timeouts handled. One company (databricks) returns 9.7MB response - caught by size limit. Two companies return 404 - isolated failures.

3. **Ashby**: Working well (349 candidates). Two failures logged but scan continues.

4. **Direct Sources**: Most return 0 candidates (no JSON-LD JobPosting schema on career pages). Diagnostics now show `jsonld_blocks`, `jobposting_found`, `page_size` for each.

5. **Source Isolation**: All providers fail independently - one failure never stops the scan.

## Final Report

### Provider Hardening: PASS

### Tests
- **586 tests passed** (568 existing + 18 new)
- No regressions

### Ashby
- 349 candidates, 337 accepted, 2 failures logged
- Scan continues after failures

### SmartRecruiters
- 217 candidates fetched, 211 accepted
- ~40 companies return 0 results (valid zero-yield)
- No more `'str' object has no attribute 'get'` crashes

### Greenhouse
- 155 candidates, 155 accepted
- 3 failures (1 response too large, 2 HTTP 404) - isolated
- Pagination bounded (50 pages max), 15s timeout

### Direct
- 0 candidates (expected - most career pages lack JSON-LD JobPosting)
- Diagnostics show `jsonld_blocks`, `jobposting_found=0`, `page_size`
- 2 timeouts properly wrapped in SourceError

### Source Isolation
- ✅ One failure never stops the scan
- ✅ Errors logged with structured context
- ✅ Scan metrics distinguish: fetched, accepted, failed, zero-yield

### Existing Functionality
- Run provenance: PASS
- SQLite thread-safety: PASS
- GUI startup: PASS

## Final Status

**PROVIDER_HARDENING_COMPLETE**