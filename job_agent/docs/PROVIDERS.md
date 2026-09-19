# Job-Agent Providers (Phase 40/4 — native provider track)

The discovery pipeline combines two complementary tracks inside a single
`run_planned_discovery` cycle:

1. **Native providers** — direct, structured API clients over a source's own
   JSON/Atom feed (RemoteOK, Remotive). Deterministic parsing, no scraping.
2. **Search fallback** — the existing DDGS/web-search track used for every
   source without an entry in the provider registry.

Both tracks return the same `Job` model; provenance records
`discovery_method: provider` vs `search` so telemetry stays honest.

This project does **not** copy the Career-Ops (Node) approach of browser
automation + an "agent inbox" of human-run automations. The reasons are
documented in the comparison table at the bottom.

---

## Provider contract

Every provider is a small class with a narrow public surface:

```python
class RemoteOKProvider:                      # job_agent/providers.py
    source_id: str = "remote_remoteok"       # catalog source key
    name: str = "remoteok"
    def parse_feed(...)                    # opaque pagination/parse details
    def fetch(self, *, source_id, entry, search, limit) -> ProviderResult
```

`fetch` returns a frozen `ProviderResult`:

```python
ProviderResult(
    source_id,
    provider,
    jobs,  # parsed, normalized Job objects
    status,  # ProviderStatus
    requests,
    hits,
    duplicates,
    errors,  # telemetry (fetch/salary/date details)
    latency_ms,
)
```

`ProviderStatus` is one of:

- `ok` — HTTP 200 and at least one job parsed.
- `zero_yield` — HTTP 200 but nothing usable (rate-limit page, empty feed).
- `failed` — transport/provider exception surfaced through `errors`.

A `MalformedProviderPayload` (unparseable but well-formed HTTP) is a
subclass of `ProviderError`, so it always reaches `fetch`'s error handling —
it produces a `failed` result and **never raises out of the pipeline**.

## Registry, fallback and health

- `providers.py::provider_for(entry)` maps a catalog entry (`provider:` field
  plus `url:`) to a provider instance. A missing/unknown provider key yields
  `None`, and the source falls back to the search track.
- `ProviderHealth` (HEALTHY / ZERO_YIELD / FAILED / DISABLED / NOT_TESTED) is
  a **read-only diagnostic** derived from stored provider runs; it never
  auto-disables a source.
- `DiscoveryPacingReport.providers` carries one dict per provider source:
  `source_id, provider, status, requests, hits, candidate_jobs, duplicates,
  errors, latency_ms`. Provider runs are persisted to `provider_runs` /
  `discovery_runs` (migrations v8/v9) so the GUI dashboard, `/v1` health and
  `report` surfaces can show provider health aggregated over time.

## Adding a provider

1. Write a provider class in `job_agent/providers.py` implementing `fetch`
   with the `ProviderResult` contract (parse into `Job`, track hits/
   duplicates/errors, keep bounded page counts).
2. Register it in the provider registry and add/reuse a catalog entry under
   `sources_catalog.yaml` with `provider: <name>` and a `url:`.
3. Add hermetic tests: `tests/test_providers.py` uses a monkeypatched
   transport with canned fixtures (never the real API).
4. `tests/test_discovery_providers.py` proves the provider branch of
   `run_planned_discovery` (partition, pacing, telemetry, FAILED isolation,
   fallback when no provider).

Constraints from the architecture:

- A provider may **only register jobs** — it never writes to SQLite, never
  decides scoring, never pokes the agent.
- Every provider call is **bounded** (`limit` argument) and isolated — one
  provider failing must not abort the source or the run.
- No browser automation; no provider may ship executable model output.

## Testing

`tests/test_providers.py` (14 tests) covers RemoteOK/Remotive fixtures,
salary/date parsing (`€65.000` → 65000.0), European-format salaries,
description normalization, malformed-payload → FAILED behavior, and no-eval
`ProviderError`.

`tests/test_discovery_providers.py` (7 tests) covers the full discovery
cycle with an injected no-sleep `RateLimitPacer`: provider jobs flow to the
pipeline, search fallback for provider-less sources works, per-provider
failures become FAILED results, and pacing telemetry carries provider runs.

## Comparison: native providers vs Career-Ops (Node)

| Concern | personal-ai job-agent (this project) | Career-Ops (reference study) |
| --- | --- | --- |
| Primary feeds | Native JSON/Atom APIs (RemoteOK, Remotive) | Browser-automated ATS scraping + gig-economy boards |
| Effort per source | Small Python class, no browser | Playwright scripts per portal + "agent inbox" |
| Background runtime | One DB connection, one process | Node daemons/automations, browser profiles |
| Failure behavior | FAILED result isolated per source | Per-portal scripts + liveness checks |
| Human loop | Application tracking via `/api/job-agent/applications` | separate agent-inbox, follow-up/cadence JS |
| Language/AI | Local, Ollama-optional (scoring uses local model) | OpenAI/Gemini-heavy tailors |
| Deployment | Docker, single gateway | Docker + many lanes |

The Career-Ops reference (studied, `/tmp/opencode/career-ops`, HEAD
`efd4a8b`) demonstrated that deep ATS automation requires per-portal browser
scripts and a shared inbox with human adjudication — exactly the scale of
complexity this slice deliberately does **not** take on. Each ATS/portal
integration remains future work (see README road-map).