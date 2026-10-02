# Project State — Personal Job Agent

## Status

**OPERATIONAL MVP — JOB-AGENT RUNNING IN DOCKER WITH WORKING GUI**

The system is a complete, local-first career-intelligence loop: catalog-driven
discovery → typed normalization → deterministic scoring → SQLite persistence →
evidence-based career-fit analysis → tailored-application proposals (proposal-only,
never auto-submitted) → local web UI → career application tracking. Native
provider feeds (RemoteOK/Remotive) augment the DDGS search track. Every slice is
validated by hermetic tests and by live, measurement-only benchmark runs
documented below.

**STOP ENGINEERING AFTER THIS CHECKPOINT** — future work belongs to a new,
explicitly-scoped phase, not to silent additions.

## Profile policy

No personal policy ships in the source; these are local config decisions:

- Compensation floor / target unset by default (`0/0`): no hard exclusion and a
  neutral compensation score. Unknown salary = reviewable.
- No sector is preferred by default.
- Preferred-city weighting is a score, never a filter, and is a score of `0.0`
  priority when no city is configured.

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
    providers.py        # native provider feeds (RemoteOK/Remotive) + health
    application.py      # career application state machine (typed stages)
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
| M3.2 | Provider track (RemoteOK/Remotive) + career application lifecycle + gateway dashboard/telemetry + GUI application panel + provider-run persistence (migrations v8–v10) | ✅ implemented (518 suite) |
| final | Bounded broad-discovery benchmark (see below) + docs + test/lint/format verification | ✅ done (this checkpoint) |

## Phase 40/4 slice (M3.2) — provider track + career workflow

- **Native providers** (`providers.py`): RemoteOK + Remotive direct-feed
  clients behind the `ProviderResult` contract; `ProviderStatus`
  (ok / zero_yield / failed); read-only `ProviderHealth`. Catalog entries
  `remote_remoteok` / `remote_remotive` declare `provider:` + `url:`. Unknown/
  missing provider ⇒ search-fallback (never a hard error). See
  `docs/PROVIDERS.md` (contract, health, fallback, testing, comparison vs the
  Career-Ops Node reference study).
- **Salary/date parsing** hardened: European thousands (`€65.000` → 65000.0),
  description normalization, `_parse_payload(source_id, entry, ...)` signature
  so provider entries can vary their feed per source.
- **Applications** (`application.py`): typed stage machine
  (NOT_APPLIED → APPLIED → RESPONDED → INTERVIEW → OFFER → HIRED; terminal
  REJECTED/WITHDRAWN), `enter()` transitions + `with_fields()` validation
  (interview_stage enum, ISO follow_up/interview dates), timestamps stamped on
  the right transitions. Persisted via `save_application` UPSERT; `OFFER` is a
  legal target from INTERVIEW (was missing from `NON_TERMINAL_STAGES`).
- **Gateway** (`src/personal_ai/server.py`): `/api/job-agent/dashboard`
  (application stage counts, last discovery run, provider-run health),
  `/api/job-agent/applications` (list w/ stage filter; per-job GET 404-if-none,
  POST upsert with transition validation → 400, PATCH metadata/notes),
  job detail carries `application`, discover returns `provider_runs` +
  `jobs_from_providers/search` and persists runs (+ commit). Dry-run uses an
  in-memory DB and writes nothing. `_apply_application` closure bug fixed
  (local `app` shadow) via rename to `application`.
- **GUI** (`src/personal_ai/static/`): pipeline stat cards (In Interview/Offer),
  last-run + provider-health bar, discovery limit + dry-run controls, and an
  Application Tracking panel (stage + notes) in the job modal.
- **Docker**: job-agent installed editable into the gateway venv after
  `uv sync` (`--extra discovery`); runtime data (sources_catalog.yaml,
  profile/, templates/) kept at `/app/job_agent`; `JOB_AGENT_DB=/data/job-agent.db`
  on the shared `/data` volume. `.dockerignore` excludes `job_agent/output`
  and dev artifacts.

## Final checkpoint benchmark (measurement-only)

- Date: 2026-09-19. Method: temp catalog (17 benchmark sources, unchanged
  priorities/affinities), scratch SQLite DB under `/tmp`, deterministic
  pipeline only (zero LLM/Ollama calls). Live DDGS with pacing/rate-limits
  enabled. **No `job_agent` code was modified for the benchmark.**
- Groups (sources): ATS (greenhouse, lever, ashby) · AI boards (aijobs.net,
  aijobs.ai, mljobs.io, machinelearningjobs, deeplearningjobs) ·
  Regional-Germany (munich_startup, berlin_startup, arbeitsagentur,
  ingenieur) ·
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
| **Regional-Germany** | 30 | 76 | 72 | 1 | 1 | 1 |
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

- `pytest -q` (job_agent suite): **518 passed**. `ruff check .`, `ruff format
  --check .` clean (line-length 120, select E/F/W/I/UP/B/SIM). Root suite
  `uv run pytest` (Personal AI gateway + shared server tests): **3059 passed**;
  root `ruff check .` clean; `ruff format --check src tests` clean.
- Production DB `output/jobs.sqlite3` untouched by benchmarks (scratch DB
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