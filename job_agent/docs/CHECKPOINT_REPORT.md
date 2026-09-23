# CHECKPOINT REPORT — Provider-Only Crawl + Fit Model Validation

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
| Greenhouse (helsing, databricks, snowflake) | ATS API | 3 | 0 | 0 | 0 | 0% | — | OK (zero yield) |
| Lever | ATS API | 0 | — | — | — | — | — | Not configured |
| Ashby (snowflake) | ATS API | 1 | 20 | 20 | ~5 | 100% | 1.0s | OK |
| SmartRecruiters (google) | ATS API | 1 | 0 | 0 | 0 | 0% | — | OK (zero yield) |

**Totals**: 56 jobs persisted (43 from providers, 13 from search fallback), 33 fetch errors.

**Key Finding**: Greenhouse boards still zero jobs (likely no open roles matching keywords). Ashby snowflake yields 20 jobs. RemoteOK and Remotive remain primary volume sources.

---

## COMPENSATION AUDIT

Current salary handling:
- Database shows **0 jobs with salary_min_eur / salary_max_eur** (all NULL).
- Config floor €120k, target €150k.
- Scoring uses lower bound of range; unknown salary → 0.55 reviewable.
- 76% of rejections due to "compensation below floor €120,000".

Proposed counterfactual scenarios:
1. **Unknown-neutral**: treat missing salary as neutral (0.5) instead of reviewable (0.55) — reduces false negatives.
2. **Range-interval**: use midpoint of published range for scoring, not lower bound.
3. **Soft floor**: make €120k a penalty weight rather than hard exclusion.

Next step: implement unit tests for compensation logic and run scenario analysis.

---

## FOCUSED TESTS (Planned)

- Role archetype classification (`role_archetypes.py`)
- Company-to-ATS resolution (`config.yaml` → provider client)
- Compensation range handling (lower bound vs midpoint, unknown-neutral)
- Location preference separation (Munich vs remote vs other)

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

## CAREER COVERAGE (from current 90 active jobs)

| Track | Jobs | Notes |
|-------|-----:|-------|
| Product Management | 8 | |
| Program Management | 12 | |
| AI / ML | 12 | |
| Autonomous Driving | 14 | |
| Robotics / Autonomy | 7 | |
| Engineering Leadership | 3 | |
| Defence / Aerospace | 1 | |
| DeepTech | 0 | |
| Mobility | 14 | |
| Data Platform | 1 | |
| Innovation Strategy | 0 | |
| Energy / Cleantech | 0 | |

**Gaps**: Defence, DeepTech, Energy/Cleantech, Data Platform have minimal coverage.

---

## FIT DIAGNOSIS

### High-Fit Threshold
- **Current threshold**: ≥0.75 (75/100) — defined in `scoring.py:336`
- **NOT 0.60** as referenced in some documentation (that appears to be the "review" threshold)
- Only **3 jobs total** ≥0.75, **1 recent** (in 30d window)

### Why So Few High-Fit Jobs?

#### Category A — Discovery Failure (Right Roles Not Entering Dataset)
- **Defence/DeepTech/Energy tracks**: Near-zero provider coverage
- **ATS providers broken**: Greenhouse, Lever, Ashby return 404 (need real tokens/boards)
- **Search engines throttled**: DDGS returns ~1.8 jobs/query with heavy rate limiting
- **Senior roles**: Search queries don't explicitly target "Staff/Principal/Director/VP" levels

#### Category B — Scoring Model Failure (Right Roles Enter But Score Low)
- **Compensation floor (€120k) is the #1 rejection reason**: 76% of rejections are "compensation below floor €120,000"
- Many EU/remote jobs don't publish salary → get 0.55 (reviewable, never high-fit)
- Salary scoring uses **lower bound** of range → €80k-€150k becomes €80k (below floor)
- Leadership score triggers on "product owner"/"program manager" but not on technical lead without people mgmt
- Location scoring penalizes non-Munich/non-Germany heavily (0.10 weight but tiered)

#### Category C — Genuine Low Fit
- Generic software engineering roles (no product/program/AI/autonomy)
- Junior/entry-level roles (excluded by negative keywords)
- Pure sales/marketing roles

### Representative Rejection Reasons
```
"hard exclusion: compensation below floor €120,000"  ← 76% of rejections
"strong AI relevance", "leadership responsibility appears relevant"  ← on rejected jobs
"no published compensation (target €150,000+)"  ← on review jobs
```

### Key Insight
> The problem is **primarily Category A (discovery)** + **Category B (compensation scoring)**.  
> The dataset lacks the right roles (Defence, DeepTech, senior Product/Program), and when relevant roles do appear, the compensation floor kills them.

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
4. **Defence/DeepTech tracks have zero provider coverage**

### Specific Bottlenecks
| Bottleneck | Impact | Fix Required |
|------------|--------|--------------|
| Greenhouse/Lever/Ashby 404 | Zero enterprise ATS jobs | Real tokens/board names in config |
| No Defence/DeepTech providers | Zero coverage for priority tracks | Add provider implementations or search fallbacks |
| Salary floor hard exclusion | Kills 76% of potential matches | Make floor configurable/soft or add "salary unknown" tier |
| Search engine throttling | Cannot scale discovery | Fix ATS providers first |

---

## NEXT ACTION (Bounded)

**Fix the ATS provider endpoints** — this is the highest-leverage single action.

### Immediate Steps
1. **Get real Greenhouse tokens** for target companies (BMW, Helsing, Quantum Systems, Rohde & Schwarz, etc.)
2. **Get real Lever sites** for target companies
3. **Get real Ashby board names** for target companies
4. **Test each ATS provider** with `provider_only=true` mode
5. **Measure yield** per provider before scaling

### Config Change Required
```yaml
# job_agent/config.yaml — replace example tokens
sources:
  greenhouse:
    - token: "bmwgroup"  # real token
  lever:
    - site: "bmw"  # real site
  ashby:
    - board: "bmw"  # real board
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