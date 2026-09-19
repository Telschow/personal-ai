# Project State — Personal Job Agent

## Status

**WORKING MVP — READY FOR REAL-WORLD USAGE**

The system is a complete, local-first career-intelligence loop: catalog-driven
discovery → typed normalization → deterministic scoring → SQLite persistence →
evidence-based career-fit analysis → tailored-application proposals (proposal-only,
never auto-submitted) → local web UI. Every slice is validated by hermetic tests
and by live, measurement-only benchmark runs documented below.

**STOP ENGINEERING AFTER THIS CHECKPOINT** — future work belongs to a new,
explicitly-scoped phase, not to silent additions.

## Current profile policy

- Salary floor **€120k** (hard exclusion when published below), target **€150k**;
  unknown salary = reviewable. Defence treated as a normal industry.
- Munich-priority weighting is a score, never a filter; remote ≠ Munich.

## Project structure

```
job_agent/
  job_agent/            # application package (the production code)
    career/             # StructuredJobAttributes, evidence, mapping, fit,
                        #   objection/pitch, tailoring, artifacts, documents
    sources/            # ATS/feed/sitemap/direct adapters (taxonomy)
    catalog.py          # typed source catalog (sources_catalog.yaml)
    career_tracks.py    # track taxonomy + classification
    config.py           # typed pydantic Config (env overrides)
    db.py               # SQLite schema + migrations (PRAGMA user_version)
    discovery_search.py # planned discovery loop + DDGS search engine
    location.py         # tiered location parsing
    normalizer.py       # job canonicalization
    pipeline.py         # ingest + lifecycle (stale/closed/dedup)
    query_plan.py       # deterministic, budgeted multi-track plan
    scoring.py          # deterministic ScoringPolicy
    cli.py              # job-agent CLI
  tests/                # hermetic pytest suite
  sources_catalog.yaml  # 42 sources, 34 enabled
  profile/profile.yaml  # user profile
  config.example.yaml   # documented configuration
  docs/                 # architecture, security, roadmap, care, sources
```

## Engineering history

| Version | Slice | Status |
|---------|-------|--------|
| v0.1 | Foundation: config validation, SQLite + migrations, canonical dedup, lifecycle, salary policy, ATS taxonomy/adapters, deterministic scoring | ✅ implemented |
| v0.2 | Typed pydantic Config + env, salary multi-currency EUR normalization, source taxonomy + adapters (Greenhouse/Lever/Ashby/SmartRecruiters/Workable/RSS/JSON/sitemap/direct), logger, docs | ✅ implemented + live-validated |
| v0.3 | `sources_catalog.yaml`, `location.py` tiers, `career_tracks.py`, `build_query_plan` (120-query plan), `run_planned_discovery` + `cli discover`, live validation | ✅ implemented + validated |
| v0.4 = M2 | Discovery observability: per-source queried-vs-yielded, five-level dedup attribution, workspace dedup, `DiscoveryYieldReport` | ✅ implemented (475 tests) |
| M3 | Local Job-Agent MVP: `/api/job-agent/*` endpoints in the Personal AI gateway, user status persistence, minimal HTML/JS UI, 8 smoke tests | ✅ implemented |
| M3.1 | Web UI integrations: discovery execution from UI, status actions, agent analysis in job detail modal | ✅ implemented |
| final | Bounded broad-discovery benchmark (see below) + docs + test/lint/format verification | ✅ done (this checkpoint) |

## Final checkpoint benchmark (measurement-only)

- Date: 2026-09-19. Method: temp catalog (17 benchmark sources, unchanged
  priorities/affinities), scratch SQLite DB under `/tmp`, deterministic
  pipeline only (zero LLM/Ollama calls). Live DDGS with pacing/rate-limits
  enabled. **No `job_agent` code was modified for the benchmark.**
- Groups (sources): ATS (greenhouse, lever, ashby) · AI boards (aijobs.net,
  aijobs.ai, mljobs.io, machinelearningjobs, deeplearningjobs) ·
  Munich-Germany (munich_startup, berlin_startup, arbeitsagentur, ingenieur) ·
  Remote (weworkremotely, remoteok, remotive, himalayas, remote.co).
- Tracks: product_management, program_management, autonomous_driving, ai_ml,
  robotics, engineering_leadership, defence_aerospace, mobility, deeptech.
- Parameters: limit_total=144, limit_per_track=20, limit_sources=20,
  max_pages=120, rate_limit_s=0.2, pacing intervals 0.5/1.0/2.0s.

### Results

| Group | queries exec | hits | candidates | parsed | persisted | useful |
|-------|-------------:|-----:|-----------:|-------:|----------:|-------:|
| **ATS** | 18 | 103 | 92 | 9 | 9 | **3** |
| **AI boards** | 34 | 93 | 82 | 0 | 0 | 0 |
| **Munich-Germany** | 30 | 76 | 72 | 1 | 1 | 1 |
| **Remote** | 35 | 119 | 116 | 11 | 11 | 5 |
| **Total** | **117/144** | **391** | **362** | **21** | **21** | **9** |

Funnel: 144 planned → 117 executed → 391 hits → 362 candidate URLs → 21 parsed
→ 21 persisted → 21 analyzed → 9 potentially-useful (all ≥ 0.60 fit, decision
not reject, non-entry seniority). Fit distribution: ≥0.80 = 2, 0.60–0.79 = 13,
0.40–0.59 = 6.

### ATS vs Non-ATS

- ATS: 18 queries → 9 persisted → 3 useful (Lever 3; Ashby 5 parsed but
  rejected by policy; Greenhouse zero — general-application pages carry no
  JSON-LD, "no_jsonld_jobposting").
- Non-ATS: 99 queries → 12 persisted → **6 useful** (remoteok 5,
  berlin_startup 1). Additional value ≈ +2× useful yield at +5.5× query budget.
- **Conclusion: `BROAD DISCOVERY SHOWS MATERIAL ADDITIONAL VALUE`.** Non-ATS
  sources roughly double the useful candidate count vs ATS-only.

### Zero-yield analysis (12/17 sources)

| Reason | Sources |
|--------|---------|
| `no_jsonld_jobposting` | aijobs.net, deeplearningjobs, mljobs.io, greenhouse, weworkremotely, arbeitsagentur, ingenieur |
| `fetch_errors_no_parse` | aijobs.ai, himalayas, remote.co |
| `aggregator_results_no_job_urls` | machinelearningjobs |
| `query_failed` | munich_startup |

Dominant cause: aggregator/portal pages that rank high on DDGS but render list
pages without `JobPosting` JSON-LD (a fetcher-parser limitation, not a scoring
limitation). DDGS throttling capped execution at 117/144 queries (~19% skipped).

### Verification (this checkpoint)

- `pytest -q` (job_agent suite): **436 passed**. `ruff check .`, `ruff format
  --check .`, `mypy` clean. See `docs/ROADMAP.md` for per-version counts.
- Production DB `output/jobs.sqlite3` untouched by the benchmark (scratch DB
  only).

## Known limitations (deferred — do not fix silently)

- Aggregator/portal pages (AI boards, German portals, weworkremotely) are not
  parsed because they lack `JobPosting` JSON-LD; a structured-data/API adapter
  is future work.
- DDGS throttling is the main run yield cap; per-source search-engine
  alternates and retry backoff tuning are future work.
- Production default catalog reaches only the first 3–17 sources per run
  depending on budget; deterministic source rotation across runs is future
  work.
- Salary normalization covers EUR today; GBP/CHF/SEK/DKK/NOK/USD verification
  pending before enabling.
- Discovery budget tuning target: 5–10 queries/source; current limited-source
  plans leave under-budgeted sources.

## Future work (not started)

- v0.6 career intelligence (structured requirement extraction, company intel)
- v0.7 application factory (CV tailoring proven on live JDs, cover letters)
- v0.8 application execution (Playwright; never auto-submit)
- v1.0 Personal-AI integration / RAG (memory writes, preference learning)