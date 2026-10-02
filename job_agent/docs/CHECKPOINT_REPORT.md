# CHECKPOINT REPORT — Provider-Only Crawl + Fit Model Validation

> **Historical validation report.**
> Metrics below describe one local `jobs.sqlite3` database at one point in time.
> They are a point-in-time record, not a current status claim.
>
> The individual search target behind that run — career tracks, sector
> preferences, compensation floor, and employer list — has been removed. What is
> kept is the engineering diagnosis: which failure modes the fit model and the
> discovery pipeline showed, and which of them are inherent to the code rather
> than to any one operator's preferences.

**Date**: 2026-09-21  
**Database**: `job_agent/output/jobs.sqlite3`  
**Git commit**: 123aaeb (feat(job-agent): operationalize career application workflow)

---

## CURRENT BASELINE

| Metric | Value |
|--------|------:|
| Total jobs in DB | 146 |
| Active jobs | 90 |
| Closed/duplicate | 56 |
| Jobs with evaluations | 145 |
| Jobs with career_fit | 4 |
| Recent jobs (2026-08-21 → 2026-09-20) | 32 active |
| Recent high-fit (≥0.75) | **1** |
| Current high-fit threshold | **≥0.75** (75/100) |

### Jobs by Source
| Source | Count | Method |
|--------|------:|--------|
| RemoteOK | 99 | Provider (API) |
| Remotive | 20 | Provider (API) |
| Direct/Search | 26 | Web search fallback |
| Test | 1 | — |

### Fit Score Distribution (all 145 evaluated)
| Bucket | Count |
|--------|------:|
| <0.40 | 107 |
| 0.40–0.49 | 4 |
| 0.50–0.59 | 12 |
| 0.60–0.69 | 8 |
| 0.70–0.79 | 13 |
| 0.80+ | 1 |

### Recent (30d) Fit Score Distribution
| Bucket | Count |
|--------|------:|
| <0.40 | 23 |
| 0.40–0.49 | 1 |
| 0.50–0.59 | 1 |
| 0.60–0.69 | 3 |
| 0.70–0.79 | 4 |

### Decisions (all)
| Decision | Count |
|----------|------:|
| reject | 111 |
| review | 31 |
| strong | 3 |

---

## PROVIDER BENCHMARK

| Provider | Type | Requests | Jobs | Unique | Recent | Parse % | Latency | Status |
|----------|------|---------:|-----:|-------:|-------:|--------:|--------:|--------|
| RemoteOK | API | 1 | 99 | 99 | ~30 | 100% | 1.3s | OK |
| Remotive | API | 1 | 20 | 20 | ~2 | 100% | 0.8s | OK |
| Greenhouse | ATS API | 1 | 0 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| Lever | ATS API | 1 | 0 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| Ashby | ATS API | 1 | 0 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| SmartRecruiters | ATS API | 1 | 0 | 0 | 0 | 0% | 0.2s | OK (zero yield) |

**Key Finding**: RemoteOK yields ~60 jobs/request; search engines yield ~1.8 jobs/query with severe throttling.

---

## PROVIDER-ONLY BENCHMARK (Fixed Config)

Run with `provider_only=true`, `skip_search_engines=true`, using real ATS slugs from `config.yaml`.

| Provider | Type | Requests | Jobs | Unique | Recent | Parse % | Latency | Status |
|----------|------|----------:|-----:|-------:|-------:|--------:|--------:|--------|
| RemoteOK | API | 1 | 99 | 99 | ~30 | 100% | 1.3s | OK |
| Remotive | API | 1 | 20 | 20 | ~2 | 100% | 0.8s | OK |
| Greenhouse (three example boards) | ATS API | 3 | 0 | 0 | 0 | 0% | — | OK (zero yield) |
| Lever | ATS API | 0 | — | — | — | — | — | Not configured |
| Ashby (one example board) | ATS API | 1 | 20 | 20 | ~5 | 100% | 1.0s | OK |
| SmartRecruiters (one example board) | ATS API | 1 | 0 | 0 | 0 | 0% | — | OK (zero yield) |

**Totals**: 56 jobs persisted (43 from providers, 13 from search fallback), 33 fetch errors.

**Key Finding**: Greenhouse boards still zero jobs (likely no open roles matching keywords). The Ashby example board yields 20 jobs. RemoteOK and Remotive remain primary volume sources.

---

## COMPENSATION AUDIT

Current salary handling observed in that run:
- The database contained **no parsed salary on any row** (all NULL).
- A compensation floor was configured for that run.
- Scoring used the **lower bound** of a published range, so a wide range
  starting well below the floor scored as a miss.
- Missing salary scored 0.55 and stayed reviewable — it never hard-rejected.
- The configured floor was by a wide margin the most frequent rejection reason,
  and it rejected mostly on **absent or wide-range** salary rather than on
  salary that was actually disclosed and genuinely low.

That last point is the transferable finding: when a hard compensation floor is
enabled and salary disclosure is sparse, the floor mostly measures *disclosure
rate* rather than *role quality*.

Proposed counterfactual scenarios:
1. **Unknown-neutral**: treat missing salary as neutral (0.5) instead of reviewable (0.55) — reduces false negatives.
2. **Range-interval**: use midpoint of published range for scoring, not lower bound.
3. **Soft floor**: make the floor a penalty weight rather than hard exclusion.
4. **Unconfigured default**: ship with no floor at all, so the floor is opt-in
   rather than something every operator inherits by default.

Next step: unit tests for compensation logic and scenario analysis. Scenarios 1-3
are only reachable when an operator configures a floor, which is why the shipped
default is now unconfigured.

---

## FOCUSED TESTS (Planned)

- Role archetype classification (`role_archetypes.py`)
- Company-to-ATS resolution (`config.yaml` → provider client)
- Compensation range handling (lower bound vs midpoint, unknown-neutral)
- Location preference separation (preferred city vs national vs remote)

These will be added to `job_agent/tests/` and run in CI.

---

## STAGE A — Provider-Only Crawl (Not Yet Run)

**Configuration**: `provider_only=true`, `skip_search_engines=true`, 25 queries max, 5 per track, 2 per source

**Expected Outcomes**:
- 200-400 candidate URLs
- 80-150 jobs parsed
- 60-100 unique jobs persisted
- Measurement of jobs/query, parse rate, recent %, fit distribution

---

## CAREER COVERAGE

That run measured job counts per track against the operator's configured target
tracks. Those specific tracks — and the per-track counts, which describe one
person's search rather than the code — are removed here.

The transferable finding is structural and still holds:

**Some configured tracks receive near-zero provider coverage.** A track is only
as discoverable as the sources indexed for it. Where coverage is near zero the
cause is nearly always upstream of scoring: no configured source carries that
sector, or the search fallback returns too few results per query. Adding tracks
to configuration without adding a source for them produces a scoring system that
can never rank anything for those tracks.

Coverage per configured track is worth measuring as a standing check, before
concluding that the fit model is at fault.

---

## FIT DIAGNOSIS

### High-Fit Threshold
- **Current threshold**: ≥0.75 (75/100) — defined in `scoring.py:336`
- **NOT 0.60** as referenced in some documentation (that appears to be the "review" threshold)
- Only **3 jobs total** ≥0.75, **1 recent** (in 30d window)

### Why So Few High-Fit Jobs?

#### Category A — Discovery Failure (Right Roles Not Entering Dataset)
- **Some configured tracks**: near-zero provider coverage
- **ATS providers broken**: Greenhouse, Lever, Ashby return 404 (need real tokens/boards)
- **Search engines throttled**: DDGS returns ~1.8 jobs/query with heavy rate limiting
- **Senior roles**: Search queries don't explicitly target "Staff/Principal/Director/VP" levels

#### Category B — Scoring Model Failure (Right Roles Enter But Score Low)
- **A configured compensation floor was the dominant rejection reason**, overwhelmingly against postings that published no salary or a wide range
- Many postings don't publish salary → score 0.55 (reviewable, never high-fit)
- Salary scoring uses the **lower bound** of a range, so a range spanning a wide band scored as its minimum
- Leadership score triggers on "product owner"/"program manager" but not on technical lead without people mgmt
- Location scoring penalizes postings outside the configured preferred city and country heavily (0.10 weight but tiered)

#### Category C — Genuine Low Fit
- Generic software engineering roles (no product/program/AI/autonomy)
- Junior/entry-level roles (excluded by negative keywords)
- Pure sales/marketing roles

### Representative Rejection Reasons
```
"hard exclusion: compensation below configured floor"   ← dominant rejection reason
"strong domain relevance", "leadership responsibility appears relevant"  ← on rejected jobs
"no published compensation (configured target not met)"  ← on review jobs
```

The pattern worth noting: jobs rejected on compensation were frequently jobs
the fit model *otherwise* scored well.

### Key Insight
> The problem was **primarily Category A (discovery)** plus **Category B (compensation scoring)**.
> The dataset lacked the target roles, and where relevant roles did appear, the
> configured compensation floor removed them.
>
> Neither half of that is inherent to a fresh checkout today: there is no
> built-in floor to inherit, and the track/industry definitions are
> configuration rather than source.

---

## GUI VERIFICATION

| Feature | Status |
|---------|--------|
| Fit sorting (high→low) | ✅ Implemented in API (`sort=fit_score_desc`) |
| Fit sorting (low→high) | ✅ |
| Date sorting | ✅ |
| Company sorting | ✅ |
| NULL fit handling | ✅ (DESC NULLS LAST / ASC NULLS FIRST) |
| Pagination preserves sort | ✅ Server-side |
| Filters (status, track, location, min_fit, source, analyzed) | ✅ |
| User status persistence | ✅ Survives re-discovery |

---

## QUALITY CHECKS

| Check | Status |
|-------|--------|
| pytest | ✅ 3059 passed |
| ruff check | ✅ |
| ruff format | ✅ |
| mypy | Not configured |

---

## DECISION

**STOP — Provider bottleneck confirmed**

### Evidence
1. **Only 2/11 providers functional** (RemoteOK, Remotive)
2. **ATS providers need credentials/fixes** (Greenhouse, Lever, Ashby return 404)
3. **Search engines severely throttled** (~1.8 jobs/query)
4. **Some configured tracks have zero provider coverage**

### Specific Bottlenecks
| Bottleneck | Impact | Fix Required |
|------------|--------|--------------|
| Greenhouse/Lever/Ashby 404 | Zero enterprise ATS jobs | Real tokens/board names in config |
| No provider covers a configured track | Zero coverage for that track | Add a provider implementation or a search fallback |
| Salary floor hard exclusion | Removed a large share of matches, mostly on undisclosed or wide-range salary | Floor is configurable and **unconfigured by default**; when enabled, prefer a soft penalty or an explicit "salary unknown" tier |
| Search engine throttling | Cannot scale discovery | Fix ATS providers first |

---

## NEXT ACTION (Bounded)

**Fix the ATS provider endpoints** — this is the highest-leverage single action.

### Immediate Steps
1. **Obtain real ATS tokens** for the companies you want to track. No target
   companies ship in the source: `company_radar.yaml` is gitignored and holds the
   operator's own employer list.
2. **Get real Lever sites** for target companies
3. **Get real Ashby board names** for target companies
4. **Test each ATS provider** with `provider_only=true` mode
5. **Measure yield** per provider before scaling

### Config Change Required
```yaml
# job_agent/config.yaml — replace example tokens
sources:
  greenhouse:
    - token: "nimbusmotors"  # real token
  lever:
    - site: "nimbus"  # real site
  ashby:
    - board: "nimbus"  # real board
```

### Validation Test
Run provider-only benchmark:
```bash
cd job_agent && uv run python -m job_agent.cli discover run \
  --max-queries 50 --max-queries-per-track 10 \
  --max-sources-per-track 5 --raw-limit 200 \
  --provider-only --skip-search-engines
```

**Success criterion**: ≥50 jobs/request from at least 3 providers, with ≥20% recent jobs.

---

## APPENDIX: High-Fit Threshold Clarification

| Reference | Threshold | Location |
|-----------|-----------|----------|
| `scoring.py:336` | **≥75** (0.75) | Code — `total >= 75` → "strong" |
| `scoring.py:334` | <50 or hard → "reject" | Code |
| `scoring.py:338` | 50–74 → "review" | Code |
| `DISCOVERY_SCALE_REPORT.md` | ≥0.75 | Correct |
| `STAGED_CRAWL_DESIGN.md` | ≥0.60 | **INCORRECT** — this is "review" threshold |
| `PROVIDER_DISCOVERY_STRATEGY.md` | ≥0.60 | **INCORRECT** |

**Canonical definition**: High-fit = **strong decision = score ≥ 75 (0.75)**.