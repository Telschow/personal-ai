# SOURCES.md — Source taxonomy and configuration

## Taxonomy

Every source carries a `kind` from `SourceKind`:

| Kind               | Meaning                                                        |
|--------------------|----------------------------------------------------------------|
| `api`              | Structured JSON API (ATS boards).                              |
| `rss_json`         | RSS/Atom XML or JSON jobs feed.                                |
| `mcp`              | Local MCP server exposing job postings (future).               |
| `structured_page`  | HTML page with parseable structured data (JSON-LD `JobPosting`). |
| `ats_board`        | Public ATS career board (JSON-LD on board pages works as a fallback). |
| `browser_automation` | Browser-automation only (restricted; not implemented).        |
| `restricted`       | Deliberately not accessed (login-walled or terms-restricted).  |

## Adapters (Slice 1)

- **Greenhouse** — `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  (`token` is the public board company, e.g. `gitlab`).
- **Lever** — `https://api.lever.co/v0/postings/{site}?mode=json`.
- **Ashby** — public posting endpoint (published + optional compensation).
- **SmartRecruiters** — `{company}.api.smartrecruiters.com` postings API.
- **Workable** — public endpoint
  `https://{subdomain}.workable.com/api/v3/accounts/{subdomain}/jobs`; a
  401/403 means the board requires authentication and raises `SourceError`.
- **Direct page** — fetches company domains and parses `JobPosting` JSON-LD
  from job URLs discovered via search/sitemap; per-page failures are isolated.
- **RSS/JSON feed** — any RSS/Atom XML or JSON jobs feed (`RssJsonAdapter`).

## Configuration

```yaml
sources:
  greenhouse:
    - token: gitlab               # public board company name
  lever:
    - site: exampleco
  ashby:
    - board: exampleco
  smartrecruiters:
    - company: exampleco
  workable:
    - subdomain: exampleco
  rss_json:
    - name: MyFeed
      url: https://example.com/jobs.xml
  sitemap:
    - url: https://example.com/sitemap.xml
  direct_company_domains:
    - careers.example.com
```

Placeholder values (`example-*`, `your-*`, `sample-*`) are detected and
skipped by discovery so an untouched example config never hits the network.

## Catalog-driven discovery (multi-track)

`sources_catalog.yaml` is the typed source registry for the discovery
expander. Every entry carries `source_type`, `discovery_method` (search-engine
`site:` + JSON-LD parse, or direct ATS API), `query_host` (the `site:` host),
`rate_limit_class`, and an `enabled`/`disabled` flag with a reason.

Provenance:

- 12 entries are direct `emredurukn/awesome-job-boards` README members
  (wellfound, weworkremotely, remoteok, hnjobs, remote.co, builtin,
  efinancialcareers, eu-startups, workatastartup, himalayas,
  berlinstartupjobs, climatebase).
- Additional curated/verified entries: munichstartupjobs, arbeitsagentur,
  ai-jobs.net, energyjobline, ingenieur.de (Germany/Munich/EU focused) and
  ATS platforms (Greenhouse/Lever/Ashby/…) used as `site:` seeds for career
  pages.
- Disabled on purpose (ToS/anti-bot/aggregator): LinkedIn, Indeed, StepStone,
  XING, Glassdoor, Dice, FlexJobs; Bundeswehr (manual public-sector flow).

Behavior notes (from the 2026-09-18 validation slice):

- `discover plan` is pure/deterministic: 120 queries = 5 tracks × 3 terms × 3
  locations (≤24 queries/track, ≤8 sources/track), every query carrying its
  location (Munich-first) and quoted track term.
- Under **default budgets the live surface is the first catalog-ordered ATS
  sources only** (greenhouse/lever/ashby); the 25+ non-ATS community boards are
  reachable only after per-track source caps are raised or source rotation is
  added (deferred — see ROADMAP v0.4).
- Yeilds live jobs via `run_planned_discovery` → `ingest_global_jobs`; the
  career pipeline (`career/`) is untouched and remains the scoring authority.

## Discovery

`discovery.build_sources(cfg, sources_filter=...)` is a wire-only function: it
maps `Config.sources` to concrete `Source` instances and applies the CLI
`--source NAME` filter. `list_configured_sources` returns a redacted inventory
(tokens truncated to the first six characters) for `sources list`.

Search-engine discovery (`WebSearchDiscovery`) composes deterministic queries
from `search.target_roles` × `search.global_locations` plus remote/AI/A-fingerprint
terms, rate-limits requests, and pushes candidate page URLs through
`fetch_candidate_jobs`, which only accepts pages that normalize into a real
job.

## Error model

- `SourceError` — a source-level failure (timeout, HTTP error). Never aborts
  the whole scan; the source is logged as `source_failed` and the scan
  continues with the remaining sources.
- Restricted/knowingly-unreachable sources raise `SourceError("...restricted")`
  so the operator can distinguish "blocked-by-design" from "broken".
- Empty responses are valid (source says no jobs right now).

## Adding a source

A new adapter is a `Source` subclass in `sources.py` that maps a config entry
to a fetch -> `list[Job]` + its `kind`. Wire it in `discovery.build_sources`,
add an example to `config.example.yaml`, and add a hermetic test that stubs
HTTP responses via `httpx.MockTransport`. Adapters never touch the database,
config, or policy.