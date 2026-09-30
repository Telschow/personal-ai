"""Global search-engine discovery with rate-limit-aware pacing.

Search engines are a *discovery* layer only: candidate URLs are thereafter
fetched and parsed as structured ``JobPosting`` pages before they can enter
the scoring pipeline. A query failure is isolated and never aborts a run.

Pacing (Phase 51+): the catalog assigns each source a ``rate_limit_class``
(low/medium/high). The :class:`RateLimitPacer` sleeps the class's configured
interval before each query, applies exponential backoff after a rate-limit
response, and pauses a pervasively rate-limited source for the rest of the
run (``max_consecutive_failures``) while other sources continue. A failed
query is never retried more than ``max_retries_per_query`` (0 or 1) — retry
storms are not acceptable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import providers as providers_mod
from .catalog import load_catalog
from .logging_setup import get_logger, log_event
from .models import Job
from .pipeline import Provenance
from .sources import DirectPageSource

if TYPE_CHECKING:
    from .providers import DiscoveryProvider

log = get_logger("discovery")


def _first_term_for_source(plan, source_id: str) -> str | None:
    """First quoted search term a plan assigns to a source (filter hint).

    Providers use the term as an optional relevance filter ("filter, never
    volume"): the feed is bounded regardless and the term only narrows which
    items a source's bounded candidates include. Returns None when the plan
    assigns no quoted term to the source.
    """
    for item in plan.items:
        if item.source_id != source_id:
            continue
        import re

        match = re.search(r'"([^"]+)"', item.query or "")
        if match:
            return match.group(1)
    return None


class RateLimitExceeded(Exception):
    """Raised when the search engine signals throttling for a query."""


_MAX_CONNECT = 1  # engines are opened per query; no reusable session in this layer


def _looks_throttled(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text for token in ("excessive load", "rate limit", "too many", "throttl", "429", "h2 connection")
    )


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    source_query: str = ""


class WebSearchDiscovery:
    def __init__(self, max_results: int = 50, rate_limit_s: float = 0.2) -> None:
        self.max_results = max_results
        self.rate_limit_s = rate_limit_s

    def _ddgs_text(self, query: str, max_results: int):
        try:  # pragma: no cover - depends on optional dependency
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError("Install '.[discovery]' to enable search-engine discovery") from exc
        with DDGS() as ddgs:
            try:
                return ddgs.text(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                if _looks_throttled(exc):
                    raise RateLimitExceeded(str(exc)[:200]) from exc
                raise

    def search_one(self, query: str, max_results: int | None = None) -> list[SearchHit]:
        """Run a single query and return hits (raises :class:`RateLimitExceeded`)."""
        aggregated = self._ddgs_text(query, max_results or self.max_results) or []
        hits: list[SearchHit] = []
        for h in aggregated:
            u = h.get("href") or h.get("url")
            if u:
                hits.append(
                    SearchHit(
                        title=h.get("title", ""),
                        url=u,
                        snippet=h.get("body", "") or "",
                        source_query=query,
                    )
                )
        return hits

    def search(self, queries) -> list[SearchHit]:
        """Legacy bulk path — isolate failures, fixed per-query sleep."""
        hits: list[SearchHit] = []
        for q in queries:
            try:
                hits.extend(self.search_one(q))
            except Exception:  # noqa: BLE001
                log_event(log, "query_failed", query=q)
            time.sleep(self.rate_limit_s)
        return hits

    def candidate_urls(self, queries: list[str]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for h in self.search(queries):
            if h.url not in seen:
                seen.add(h.url)
                urls.append(h.url)
        return urls


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


@dataclass
class _SourcePaceState:
    planned_queries: int = 0
    attempted: int = 0
    successful: int = 0
    rate_limited: int = 0
    failed: int = 0
    paused_skipped: int = 0
    consecutive_failures: int = 0
    backoff_s: float = 0.0
    pause_reason: str | None = None
    # Yield observability (M2): per-source queried-vs-yielded accounting.
    hits_returned: int = 0
    duplicate_on_page: int = 0
    duplicate_at_url: int = 0
    candidate_pages: int = 0
    jobs_parsed: int = 0


@dataclass(frozen=True)
class SourceRunStats:
    """Per-source pacing + yield/outcome counters for one run (aggregate-only).

    Pacing fields are filled by :class:`RateLimitPacer`; the yield fields
    (``hits_returned`` / ``duplicate_on_page`` / ``duplicate_at_url`` /
    ``candidate_pages`` / ``jobs_parsed``) are fed from the discovery run loop
    so a single per-source row answers "queried vs yielded".
    """

    source_id: str
    rate_limit_class: str
    planned_queries: int
    attempted_queries: int
    successful_queries: int
    rate_limited_queries: int
    failed_queries: int
    paused_skipped_queries: int
    hits_returned: int = 0
    duplicate_on_page: int = 0
    duplicate_at_url: int = 0
    candidate_pages: int = 0
    jobs_parsed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "rate_limit_class": self.rate_limit_class,
            "planned_queries": self.planned_queries,
            "attempted_queries": self.attempted_queries,
            "successful_queries": self.successful_queries,
            "rate_limited_queries": self.rate_limited_queries,
            "failed_queries": self.failed_queries,
            "paused_skipped_queries": self.paused_skipped_queries,
            "hits_returned": self.hits_returned,
            "duplicate_on_page": self.duplicate_on_page,
            "duplicate_at_url": self.duplicate_at_url,
            "candidate_pages": self.candidate_pages,
            "jobs_parsed": self.jobs_parsed,
        }


@dataclass(frozen=True)
class ProviderRunStats:
    """Aggregate-only provider run for reports/checkpoints (never content)."""

    source_id: str
    provider: str
    status: str
    requests: int = 0
    hits: int = 0
    candidate_jobs: int = 0
    duplicates: int = 0
    errors: tuple[str, ...] = ()
    latency_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "status": self.status,
            "requests": self.requests,
            "hits": self.hits,
            "candidate_jobs": self.candidate_jobs,
            "duplicates": self.duplicates,
            "errors": list(self.errors),
            "latency_ms": self.latency_ms,
        }


class RateLimitPacer:
    """Deterministic, class-aware pacing for a discovery run.

    ``sleep``/``now`` are injectable for hermetic tests; production uses wall
    time. A rate-limited query triggers an immediate bounded backoff (single
    sleep), after ``max_consecutive_failures`` the source is paused for the
    rest of the run with a recorded reason — other sources continue. ``provider``
    backed sources are fetched directly and not subject to pacing.
    """

    def __init__(
        self, pacing, *, now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep
    ) -> None:  # noqa: ANN001
        self.pacing = pacing
        self._now = now
        self._sleep = sleep
        self._states: dict[str, _SourcePaceState] = {}
        self._slept_s = 0.0
        self.catalog_unknown_jobs = 0

    def _state(self, source_id: str) -> _SourcePaceState:
        state = self._states.get(source_id)
        if state is None:
            state = _SourcePaceState()
            self._states[source_id] = state
        return state

    def interval_for(self, rate_limit_class: str) -> float:
        try:
            return float(self.pacing.interval_by_class.get(rate_limit_class, 1.0))
        except (TypeError, ValueError):
            return 1.0

    def wait_before(self, source_id: str, rate_limit_class: str) -> float:
        """Sleep the class interval before a query; returns the seconds slept."""
        seconds = self.interval_for(rate_limit_class)
        self._sleep(seconds)
        self._slept_s += seconds
        return seconds

    def should_attempt(self, source_id: str) -> bool:
        state = self._state(source_id)
        if state.pause_reason is not None:
            state.paused_skipped += 1
            return False
        return True

    def note_success(self, source_id: str) -> None:
        state = self._state(source_id)
        state.attempted += 1
        state.successful += 1
        state.consecutive_failures = 0
        state.backoff_s = 0.0

    def note_failure(self, source_id: str, *, rate_limited: bool = False) -> None:
        state = self._state(source_id)
        state.attempted += 1
        if rate_limited:
            state.rate_limited += 1
        else:
            state.failed += 1
        state.consecutive_failures += 1
        state.backoff_s = min(
            self.pacing.backoff_max_s,
            (state.backoff_s * 2) if state.backoff_s > 0 else self.pacing.backoff_base_s,
        )
        if rate_limited:
            # Absorb the throttle once before continuing with the next source.
            self._sleep(state.backoff_s)
            self._slept_s += state.backoff_s
        if state.consecutive_failures >= self.pacing.max_consecutive_failures:
            state.pause_reason = "rate_limit" if rate_limited else "repeated_failures"

    def set_planned(self, source_id: str, count: int) -> None:
        self._state(source_id).planned_queries = count

    # -- yield observability feeds (M2) -------------------------------------
    # These are counting-only: they never affect pacing decisions.

    def note_hits(self, source_id: str, count: int) -> None:
        self._state(source_id).hits_returned += count

    def note_on_page_duplicate(self, source_id: str, count: int) -> None:
        self._state(source_id).duplicate_on_page += count

    def note_url_duplicate(self, source_id: str) -> None:
        self._state(source_id).duplicate_at_url += 1

    def note_candidate_pages(self, source_id: str, count: int) -> None:
        self._state(source_id).candidate_pages += count

    def note_jobs_parsed(self, source_id: str, count: int) -> None:
        self._state(source_id).jobs_parsed += count

    def note_catalog_unknown_jobs(self, count: int) -> None:
        self.catalog_unknown_jobs += count

    def report(
        self,
        rate_limit_class_for: dict[str, str],
        providers: tuple[dict[str, object], ...] = (),
    ) -> DiscoveryPacingReport:
        ordered = sorted(self._states.items(), key=lambda kv: kv[0])
        rows = []
        for source_id, state in ordered:
            rows.append(
                SourceRunStats(
                    source_id=source_id,
                    rate_limit_class=rate_limit_class_for.get(source_id, "medium"),
                    planned_queries=state.planned_queries,
                    attempted_queries=state.attempted,
                    successful_queries=state.successful,
                    rate_limited_queries=state.rate_limited,
                    failed_queries=state.failed,
                    paused_skipped_queries=state.paused_skipped,
                    hits_returned=state.hits_returned,
                    duplicate_on_page=state.duplicate_on_page,
                    duplicate_at_url=state.duplicate_at_url,
                    candidate_pages=state.candidate_pages,
                    jobs_parsed=state.jobs_parsed,
                )
            )
        return DiscoveryPacingReport(
            per_source=tuple(rows),
            cost_seconds=self._slept_s,
            catalog_unknown_jobs=self.catalog_unknown_jobs,
            providers=providers,
        )


@dataclass(frozen=True)
class DiscoveryPacingReport:
    """Bounded per-source + aggregate pacing + yield outcomes for a run.

    ``catalog_unknown_jobs`` counts jobs emitted by fetch whose provenance could
    not be attributed to a plan query (surface-level attribution gap).
    ``providers`` holds aggregate-only per-provider run results (content-free).
    """

    per_source: tuple[SourceRunStats, ...] = ()
    cost_seconds: float = 0.0
    catalog_unknown_jobs: int = 0
    providers: tuple[dict[str, object], ...] = ()

    @property
    def totals(self) -> dict[str, int]:
        return {
            "planned": sum(s.planned_queries for s in self.per_source),
            "attempted": sum(s.attempted_queries for s in self.per_source),
            "successful": sum(s.successful_queries for s in self.per_source),
            "rate_limited": sum(s.rate_limited_queries for s in self.per_source),
            "failed": sum(s.failed_queries for s in self.per_source),
            "paused_skipped": sum(s.paused_skipped_queries for s in self.per_source),
            "hits_returned": sum(s.hits_returned for s in self.per_source),
            "duplicate_on_page": sum(s.duplicate_on_page for s in self.per_source),
            "duplicate_at_url": sum(s.duplicate_at_url for s in self.per_source),
            "candidate_pages": sum(s.candidate_pages for s in self.per_source),
            "jobs_parsed": sum(s.jobs_parsed for s in self.per_source),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "per_source": [s.to_dict() for s in self.per_source],
            "totals": {**self.totals, "cost_seconds": round(self.cost_seconds, 3)},
            "catalog_unknown_jobs": self.catalog_unknown_jobs,
            "providers": [dict(p) for p in self.providers],
        }


def build_global_queries(cfg) -> list[str]:
    """Generate bounded, deduplicated discovery queries from config."""
    search = cfg.search
    roles = search.target_roles
    locations = search.global_locations
    query_cap = int(search.result_query_cap or 120)

    clusters = [
        "AI machine learning artificial intelligence",
        "deep tech robotics autonomous systems",
        "technical product strategy platform",
        "technical program program management engineering leadership",
        "innovation technology strategy",
    ]
    queries: list[str] = []
    for role in roles:
        for loc in locations:
            queries.append(f'"{role}" "{loc}" jobs')
        for cluster in clusters:
            queries.append(f'"{role}" {cluster} jobs')

    for role in roles[:12]:
        queries.append(f'"{role}" remote Europe salary')
        queries.append(f'"{role}" Germany English jobs')
        queries.append(f'"{role}" international relocation jobs')

    ats_patterns = [
        "site:boards.greenhouse.io",
        "site:jobs.lever.co",
        "site:ashbyhq.com",
        "site:careers.smartrecruiters.com",
    ]
    for role in roles[:10]:
        for pattern in ats_patterns:
            queries.append(f'{pattern} "{role}" AI OR robotics OR deep tech')

    dedup: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if q not in seen:
            seen.add(q)
            dedup.append(q)
        if len(dedup) >= query_cap:
            break
    return dedup


def fetch_candidate_jobs(urls: list[str], max_pages: int = 200) -> tuple[list[Job], list[str]]:
    """Fetch candidate URLs and keep only pages that parse as JobPosting.

    Returns (jobs, errors). Page failures are isolated and reported; no URL
    that fails to produce structured job data ever enters the pipeline.
    """
    jobs: list[Job] = []
    errors: list[str] = []
    for url in urls[:max_pages]:
        try:
            parsed = DirectPageSource(url).fetch()
            jobs.extend(parsed)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
    return jobs, errors


def run_planned_discovery(
    cfg,
    *,
    max_pages: int = 200,
    max_results: int = 25,
    rate_limit_s: float = 0.2,
    limit_total: int | None = None,
    limit_per_track: int | None = None,
    limit_per_source: int | None = None,
    limit_sources: int | None = None,
    pacing: RateLimitPacer | None = None,
    engine: WebSearchDiscovery | None = None,
    fetch=None,
    provider_only: bool = False,
    skip_search_engines: bool = False,
):
    """Run the catalog-driven discovery plan end to end.

    Returns ``(plan, jobs, provenance, errors, pacing)`` where ``provenance``
    mirrors ``jobs`` and records the catalog source + query that surfaced each
    posting (content-free), and ``pacing`` is a :class:`DiscoveryPacingReport`
    of per-source planned/attempted/successful/rate-limited/failed/paused
    counters. Queries are executed in plan order with class-aware pacing; a
    pervasively rate-limited source is paused for the run while others
    continue. ``engine``/``fetch``/``pacing`` are injectable for hermetic tests.
    """
    from .query_plan import build_query_plan

    plan = build_query_plan(
        cfg,
        limit_total=limit_total,
        limit_per_track=limit_per_track,
        limit_per_source=limit_per_source,
        limit_sources=limit_sources,
    )

    # Provider-backed sources: catalog entries that declare a native provider
    # (e.g. remoteok/remotive) are fetched directly from their feed; the
    # generic search-engine path remains the fallback for everything else.

    provider_by_source: dict[str, DiscoveryProvider] = {}
    catalog: object | None = None
    catalog_loaded = False
    try:
        catalog = load_catalog(cfg.catalog_path_resolved())
        catalog_loaded = True
        for entry in catalog.sources:
            resolved = providers_mod.provider_for(entry)
            if resolved is not None:
                provider_by_source[entry.source_id] = resolved
    except Exception:  # noqa: BLE001 — missing/unparseable catalog => search fallback only
        catalog_loaded = False

    # Provider-only mode: filter plan to only include functional providers
    if provider_only or skip_search_engines:
        filtered_items = [item for item in plan.items if item.source_id in provider_by_source]
        plan = type(plan)(items=tuple(filtered_items), budgets=plan.budgets, locations=plan.locations)

    item_by_query = {item.query: item for item in plan.items}

    if pacing is None:
        pacing = RateLimitPacer(cfg.career.pacing)
    eng = engine or WebSearchDiscovery(max_results=max_results, rate_limit_s=rate_limit_s)

    class_for: dict[str, str] = {}
    per_source_planned: dict[str, int] = {}
    for item in plan.items:
        class_for[item.source_id] = item.rate_limit_class
        per_source_planned[item.source_id] = per_source_planned.get(item.source_id, 0) + 1
    for source_id, count in per_source_planned.items():
        pacing.set_planned(source_id, count)

    # Initialize result containers
    jobs: list[Job] = []
    errors: list[str] = []
    provenance: list[Provenance] = []
    url_query: dict[str, str] = {}
    url_source: dict[str, str] = {}
    candidate_by_source: dict[str, int] = {}
    catalog_unknown = 0
    jobs_by_source: dict[str, int] = {}
    provider_stats: list[dict[str, object]] = []

    # Continue with search-engine discovery (only if not in provider-only mode)
    if not (provider_only or skip_search_engines):
        for item in plan.items:
            if item.source_id in provider_by_source:
                continue  # provider-backed sources are fetched directly, not queried
            pacing.wait_before(item.source_id, item.rate_limit_class)
            if not pacing.should_attempt(item.source_id):
                continue
            max_tries = 1 + pacing.pacing.max_retries_per_query
            hits: list[SearchHit] = []
            for _ in range(max_tries):
                try:
                    hits = eng.search_one(item.query) if hasattr(eng, "search_one") else eng.search([item.query])
                except RateLimitExceeded:
                    pacing.note_failure(item.source_id, rate_limited=True)
                    break
                except Exception:  # noqa: BLE001 - isolated per query
                    pacing.note_failure(item.source_id, rate_limited=False)
                    break
                else:
                    pacing.note_success(item.source_id)
                    break
            # Yield accounting for this query's raw hits (URLs only, no fetching).
            q_urls: list[str] = []
            q_duplicate_on_page = 0
            q_seen: set[str] = set()
            for h in hits:
                if not h.url:
                    continue
                if h.url in q_seen:
                    # Same posting URL repeated within one query's results page.
                    q_duplicate_on_page += 1
                    continue
                q_seen.add(h.url)
                q_urls.append(h.url)
            successful = any(h.url for h in hits)
            if successful:
                pacing.note_hits(item.source_id, len(q_urls))
                pacing.note_on_page_duplicate(item.source_id, q_duplicate_on_page)
            for url in q_urls:
                if url in url_query:
                    # Distinct query/source in the same run collapsing to one URL.
                    pacing.note_url_duplicate(item.source_id)
                    continue
                url_query[url] = hits[0].source_query or item.query
                url_source[url] = item.source_id
                candidate_by_source[item.source_id] = candidate_by_source.get(item.source_id, 0) + 1
        for source_id, count in candidate_by_source.items():
            pacing.note_candidate_pages(source_id, count)

        fetcher = fetch or fetch_candidate_jobs
        search_jobs, search_errors = fetcher(list(url_query.keys()), max_pages=max_pages)
        jobs.extend(search_jobs)
        errors.extend(search_errors)

        for job in search_jobs:
            query = url_query.get(job.url)
            plan_item = item_by_query.get(query) if query else None
            if plan_item is not None:
                provenance.append(
                    Provenance(
                        source_id=plan_item.source_id,
                        source_name=plan_item.source_id,
                        discovery_method=plan_item.discovery_method,
                        query=plan_item.query,
                    )
                )
                jobs_by_source[plan_item.source_id] = jobs_by_source.get(plan_item.source_id, 0) + 1
            else:
                catalog_unknown += 1
                provenance.append(
                    Provenance(source_id="catalog_unknown", discovery_method="search_engine", query=query)
                )
        for source_id, count in jobs_by_source.items():
            pacing.note_jobs_parsed(source_id, count)
        pacing.note_catalog_unknown_jobs(catalog_unknown)

    # Provider-backed sources are fetched once each (bounded by their planned
    # share of the discovery budget). A provider failure is isolated and only
    # reported — it never aborts the run and never affects scoring.
    if provider_by_source:
        entry_by_id = {entry.source_id: entry for entry in (catalog.sources if catalog_loaded else [])}
        for source_id in sorted(provider_by_source):
            provider = provider_by_source[source_id]
            limit = min(
                max(1, per_source_planned.get(source_id, 1)) * max_results,
                providers_mod._MAX_PROVIDER_LIMIT,
            )
            try:
                result = provider.fetch(
                    source_id=source_id, limit=limit, search=_first_term_for_source(plan, source_id)
                )
            except Exception as exc:  # noqa: BLE001 — per-provider isolation
                result = providers_mod.ProviderResult(
                    source_id=source_id,
                    provider=provider.name,
                    jobs=(),
                    status=providers_mod.ProviderStatus.FAILED,
                    errors=(f"{provider.name}: {exc}"[:200],),
                )
            entry = entry_by_id.get(source_id)
            for job in result.jobs:
                jobs.append(job)
                provenance.append(
                    Provenance(
                        source_id=source_id,
                        source_name=source_id,
                        source_url=entry.url if entry else None,
                        discovery_method="provider",
                    )
                )
            provider_stats.append(result.to_run_stats().to_dict())
            if result.status == providers_mod.ProviderStatus.OK:
                pacing.note_jobs_parsed(source_id, len(result.jobs))

    return plan, jobs, provenance, errors, pacing.report(class_for, providers=tuple(provider_stats))
