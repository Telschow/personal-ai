# Roadmap

## v0.2 – foundation hardening (Slice 1, implemented)
- Typed pydantic `Config` with env overrides; `auto_submit`/`auto_publish`
  hard-blocked by validation; example placeholders never reach the network.
- SQLite schema under `PRAGMA user_version` migrations (v1..v2); idempotent
  `upsert_job`; canonical dedup; time-based lifecycle (stale → closed).
- Salary policy: **no built-in floor or target**. While unconfigured
  (`0/0`), published compensation scores neutrally and never hard-rejects;
  unknown stays reviewable. Configuring both values enables the floor/target
  gate. Multi-currency normalization to EUR (incl. German `70.000` forms).
- Source taxonomy + adapters: Greenhouse, Lever, Ashby, SmartRecruiters,
  Workable, RSS/JSON, sitemap, direct `JobPosting` pages; per-source failure
  isolation; token-redacted `sources list`.
- Deterministic `ScoringPolicy` scoring with documented baseline, confidence,
  breakdown, and hard-fail path. No LLM.
- Structured logging; 64 hermetic tests; ruff + format + mypy clean.
- Live validations: GitLab public Greenhouse board (224 jobs → 222 accepted,
  dedup 2 groups, lifecycle applied).

## v0.3 – catalog-driven multi-track discovery (implemented + validated)
- `sources_catalog.yaml`: typed registry (source_type, discovery_method,
  query_host, rate_limit_class, enabled/disabled reasons), provenance =
  `emredurukn/awesome-job-boards` for 12 entries + curated additions +
  explicitly disabled bot-hostile aggregators (LinkedIn/Indeed/StepStone/…).
- `location.py` tiered/scoped parsing (city core / metro / region / country /
  DACH-EU / remote-DE / remote-EU / international / unknown); the preferred
  city is a weight, never a filter, and remote is not the same tier.
- `career_tracks.py` taxonomy (product/program/AI-ML/ADAS/mobility/defence/…) +
  `query_plan.build_query_plan` — pure, deterministic 120-query plan
  (5 tracks × 3 terms × 3 locations, ≤24 queries/track, ≤8 sources/track).
- `run_planned_discovery` + `cli discover plan|run`, provenance
  (`job_sources`), diagnostics (`discovery_diagnostics`), docs v6
  (`jobs.canonical_url`, `jobs.source_count`). 436 hermetic tests.
- **Validation slice 2026-09-18** (measurement-only, `job_agent` code
  untouched): 27 unique live jobs across 3 bounded passes, 27/27 analyzed,
  final==deterministic coverage (STRONG 121/GAP 25/TRANSFERABLE 18),
  25/25 proposals accepted, 0 monotonic violations, 27 LLM calls (mean 15.4 s,
  0 retries); Munich-tier jobs 7/27 (26%); effective live surface = Lever +
  Ashby (Greenhouse 404s via stale index); DDGS hard-throttles at ~120
  sustained queries (the main yield cap). Full report: `/tmp/opencode/benchmark`.
- Known gaps (deferred): default budgets reach only the first 3 catalog
  sources; ingest duplicate counters under-report post-merge dedup; probes pass
  needs a healthy DDGS window to score company pages.

## v0.4 – discovery observability (M2) + local Job-Agent MVP (M3) ✅
- **M2 discovery observability** (2026-09-18): per-source queried-vs-yielded
  accounting (`hits_returned`, `duplicate_on_page`, `duplicate_at_url`,
  `candidate_pages`, `jobs_parsed`), five-level dedup attribution
  (`duplicate_at_db` vs `previous_runs` split by `run_started_at` boundary),
  workspace dedup (`duplicate_workspace_groups`/`merged`), merged
  `DiscoveryYieldReport` with `zero_yield_sources` and `never_queried_sources`.
  475 tests pass, ruff/mypy clean.
- **M3 local Job-Agent MVP** (2026-09-18): `/api/job-agent/*` endpoints mounted
  in Personal AI gateway (`health`, `jobs` list/filter/detail, `jobs/{id}/status`,
  `discover` POST), user-facing status (`NEW/SAVED/REJECTED/APPLIED`) with
  durable persistence surviving restarts, minimal HTML/JS UI
  (`src/personal_ai/static/index.html` + `app.js`), startup via
  `python -m personal_ai.server --job-db <path>`. 8 smoke tests verified:
  startup, discovery, jobs list, job detail, status change, restart persistence,
  second discovery (dedup + status preservation), failure tolerance.

## v0.5 – source expansion + broad-discovery validation 🏁
- **FINAL CHECKPOINT BENCHMARK 2026-09-19** (measurement-only; zero `job_agent`
  code changes): end-to-end broad-discovery run across **17 sources in 4
  groups** (ATS 3 / AI boards 5 / Munich-Germany 4 / Remote 5), 9 career
  tracks, 144 planned queries, **117 executed** (live DDGS,
  pacing/rate-limits enabled), 391 hits, 362 candidate URLs, **21 jobs parsed
  and persisted**, **21/21 analyzed** (deterministic pipeline, zero LLM calls),
  **9 potentially-useful candidates**. Full data:
  `/tmp/opencode/final_bench/{final_report.json, final_analyze.json}`.
  - ATS (18 queries): 9 jobs persisted, **3 useful** — Lever only
    (Greenhouse "general application" pages carry no JSON-LD).
  - **Non-ATS (99 queries): 12 jobs persisted, 6 useful** — remoteok 5,
    Berlin-startups 1; remotive parsed 2 (both low-fit). **Material added
    value beyond the 3-source ATS surface.**
  - Zero-yield 12/17 sources: mostly `no_jsonld_jobposting` (AI boards,
    Arbeitsagentur, ingenieur, weworkremotely, greenhouse), `fetch_errors`
    (remote.co, himalayas, aijobs.ai), `aggregator_results_no_job_urls`
    (machinelearningjobs), `query_failed` (munich_startup).
  - **Conclusion: `BROAD DISCOVERY SHOWS MATERIAL ADDITIONAL VALUE`.** The
    production default already enables this; boost non-ATS quota in config.
  - Known limitation (unchanged): DDGS throttling remains the main yield cap
    (117/144 executed).
- Add Teamtailor, Recruitee, Personio and additional ATS adapters.
- Add sitemap/robots-aware discovery and per-domain crawl budgets.
- Add company-domain fingerprinting and automatic ATS identification.
- Include non-ATS catalog sources in the default plan (per-source caps +
  deterministic source rotation so every enabled source is queried over runs).
- Honor `rate_limit_class` in the search engine; report skipped-query counts.
- Add country-aware salary normalization (GBP, CHF, SEK, DKK, NOK, USD —
  EUR table exists; verify rates before enabling GBP/CHF/USD in scoring).
- LLM-assisted interpretation of deterministically scored shortlists
  (summary/explanation) — baseline always shown, LLM never changes the score.

## v0.6 – career intelligence
- Separate `career_profile` and `job_evidence` schemas.
- Extract requirements into structured fields: seniority, education,
  must-have skills, nice-to-have skills, travel, management scope, remote
  policy.
- Company intelligence: growth, funding, leadership, product, layoffs,
  location, reputation and likely WLB signals.

## v0.7 – application factory
- Master CV in DOCX and structured achievement bank.
- Job-specific CV tailoring with claim-level provenance.
- Cover letters in German/English/Spanish.
- Screening-question draft generation.
- Human approval UI (never automatic).

## v0.8 – application execution
- Playwright browser automation for compatible ATS flows.
- Never auto-submit; explicit per-application approval remains mandatory.
- Application status tracking and follow-up reminders.

## v1.0 – integration with personal AI / RAG
- Read/write via the parent `personal_ai` memory store.
- Learn preferences from viewed/rejected/applied jobs.
- Detect recurring patterns in successful applications.
- Weekly career intelligence report.