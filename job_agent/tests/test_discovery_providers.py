"""Provider-backed discovery integration tests (M3) — hermetic.

``run_planned_discovery`` partitions provider-backed catalog sources out of
the paced search loop and fetches them directly; transport is faked by
monkeypatching the provider's default HTTP callable, so no network is used.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from job_agent.catalog import load_catalog
from job_agent.config import default_config
from job_agent.discovery_search import DiscoveryPacingReport, RateLimitPacer, SearchHit, run_planned_discovery
from job_agent.providers import ProviderStatus, RemotiveProvider, provider_health_for


def _no_sleep_pacer(cfg):
    return RateLimitPacer(cfg.career.pacing, now=lambda: 0.0, sleep=lambda _s: None)


CATALOG = textwrap.dedent(
    """
    provenance:
      url: https://example.com/sources.yaml
      retrieved: "2026-09-18"
    sources:
      - source_id: remote_remotive
        name: Remotive
        source_type: search_engine
        category: general
        enabled: true
        priority: 88
        url: "remotive.example"
        query_host: "remotive.example"
        provider: remotive
      - source_id: agg_generic
        name: Generic Search
        source_type: search_engine
        category: general
        enabled: true
        priority: 80
        query_host: "generic.example"
    """
)

REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "id": 1,
            "url": "https://remotive.com/remote-jobs/1",
            "title": "AI Engineer (Remote)",
            "company_name": "Gamma",
            "category": "software",
            "job_type": "full_time",
            "publication_date": "2026-01-20T10:00:00Z",
            "candidate_required_location": "Europe",
            "salary": "80k",
            "tags": ["python"],
            "description": "<p>Build AI systems.</p>",
        },
        {
            "id": 2,
            "url": "https://remotive.com/remote-jobs/2",
            "title": "ML Engineer (Remote)",
            "company_name": "Omega",
            "category": "software",
            "job_type": "full_time",
            "candidate_required_location": "Worldwide",
            "tags": ["ml"],
            "description": "No description",
        },
    ]
}


class _FakeEngine:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search_one(self, query: str):
        self.queries.append(query)
        return [SearchHit(url="https://generic.example/job/1", title="Generic hit")]


def _fake_fetch(urls, max_pages=200):
    return [], []


def _one_job_fetch(urls, max_pages=200):
    jobs = [
        type("FakeJob", (), {"url": u, "title": "Generic", "company": "C", "id": u, "source": "search"})() for u in urls
    ]
    return jobs, []


@pytest.fixture
def cfg(tmp_path):
    cfg = default_config()
    cat = tmp_path / "catalog.yaml"
    cat.write_text(CATALOG, encoding="utf-8")
    cfg.sources.catalog_path = str(cat)
    cfg.career.discovery.max_queries_total = 50
    cfg.career.discovery.max_queries_per_track = 50
    cfg.career.discovery.max_queries_per_source = 50
    return cfg


def _fake_transport(monkeypatch, payload: object, status: int = 200):
    def http(url):
        return (status, json.dumps(payload).encode("utf-8"), {})

    monkeypatch.setattr("job_agent.providers._default_http", http)


def _unfiltered(monkeypatch):
    """Disable the per-source search filter so canned feeds yield their jobs."""
    monkeypatch.setattr("job_agent.discovery_search._first_term_for_source", lambda plan, sid: None)


def test_provider_source_fetched_directly_not_queried(cfg, monkeypatch) -> None:
    _fake_transport(monkeypatch, REMOTIVE_PAYLOAD)
    _unfiltered(monkeypatch)
    engine = _FakeEngine()
    plan, jobs, provenance, errors, report = run_planned_discovery(
        cfg, engine=engine, fetch=_fake_fetch, pacing=_no_sleep_pacer(cfg), max_results=25
    )
    assert errors == []
    # Provider-backed source id is planned but never querying the engine.
    assert any(item.source_id == "remote_remotive" for item in plan.items)
    assert not any("remotive.example" in q for q in engine.queries)
    # Generic search source still went through the paced engine.
    assert any("generic.example" in q for q in engine.queries)

    provider_ids = {p["source_id"] for p in report.providers}
    assert provider_ids == {"remote_remotive"}
    provider_stats = report.providers[0]
    assert provider_stats["provider"] == "remotive"
    assert provider_stats["status"] == "ok"
    assert provider_stats["candidate_jobs"] == 2

    provider_jobs = [p for p in provenance if p.discovery_method == "provider"]
    assert len(provider_jobs) == 2
    assert all(p.source_id == "remote_remotive" for p in provider_jobs)
    assert all(p.source_url == "remotive.example" for p in provider_jobs)
    assert len(provenance) == len(jobs)


def test_provider_source_jobs_merge_with_search_jobs(cfg, monkeypatch) -> None:
    _fake_transport(monkeypatch, REMOTIVE_PAYLOAD)
    _unfiltered(monkeypatch)
    engine = _FakeEngine()
    plan, jobs, provenance, _errors, report = run_planned_discovery(
        cfg, engine=engine, fetch=_one_job_fetch, pacing=_no_sleep_pacer(cfg), max_results=25
    )
    job_sources = {j.source for j in jobs}
    assert "remotive" in job_sources
    assert any(j.source != "remotive" for j in jobs)  # generic provenance too
    # Provider jobs keep a stable, provenance-able id.
    ids = [j.id for j in jobs if j.source == "remotive"]
    assert ids == ["remotive:1", "remotive:2"]


def test_provider_failure_isolated_run_continues(cfg, monkeypatch) -> None:
    _fake_transport(monkeypatch, {"oops": True}, status=500)
    engine = _FakeEngine()
    plan, jobs, provenance, _errors, report = run_planned_discovery(
        cfg, engine=engine, fetch=_one_job_fetch, pacing=_no_sleep_pacer(cfg), max_results=25
    )
    failures = [p for p in report.providers if p["status"] == "failed"]
    assert len(failures) == 1
    assert failures[0]["source_id"] == "remote_remotive"
    assert failures[0]["provider"] == "remotive"
    # Generic source still discovered jobs through the search engine; the
    # provider failure didn't abort the run and produced no provider rows.
    assert engine.queries
    assert jobs
    assert not any(p.discovery_method == "provider" for p in provenance)
    assert any(p.discovery_method == "search_engine" for p in provenance)


def test_provider_zero_yield_reported_not_abort(cfg, monkeypatch) -> None:
    _fake_transport(monkeypatch, {"jobs": []})
    plan, _jobs, provenance, _errors, report = run_planned_discovery(
        cfg, engine=_FakeEngine(), fetch=_fake_fetch, pacing=_no_sleep_pacer(cfg), max_results=25
    )
    assert any(p["status"] == "zero_yield" for p in report.providers)
    assert not any(p.discovery_method == "provider" for p in provenance)
    assert isinstance(report, DiscoveryPacingReport)


def test_provider_limit_scales_with_planned_budget(cfg, monkeypatch) -> None:
    seen: dict[str, int] = {}

    def http(url):
        seen[url] = seen.get(url, 0) + 1
        return (200, json.dumps({"jobs": []}).encode("utf-8"), {})

    monkeypatch.setattr("job_agent.providers._default_http", http)
    cfg.career.discovery.max_queries_per_source = 40
    plan, _jobs, _prov, _err, report = run_planned_discovery(
        cfg, engine=_FakeEngine(), fetch=_fake_fetch, pacing=_no_sleep_pacer(cfg), max_results=8
    )
    assert len(seen) == 1  # exactly one bounded fetch for the provider source
    assert not any(p["status"] == "failed" for p in report.providers)
    (url,) = seen
    assert url.startswith(RemotiveProvider.feed_url)


def test_provider_health_matches_latest_run(cfg, monkeypatch) -> None:
    catalog = load_catalog(cfg.sources.catalog_path)
    entry = next(s for s in catalog.sources if s.source_id == "remote_remotive")
    from job_agent import providers

    assert provider_health_for(entry, None) == providers.ProviderHealth.NOT_TESTED
    # Health never disables: a run with zero yield maps to ZERO_YIELD only.
    _fake_transport(monkeypatch, {"jobs": []})
    run_planned_discovery(cfg, engine=_FakeEngine(), fetch=_fake_fetch, pacing=_no_sleep_pacer(cfg), max_results=8)
    # provider_health_for still needs the persisted row; simulate it directly.
    assert provider_health_for(entry, {"status": "ok", "hits": 0}) == providers.ProviderHealth.ZERO_YIELD
    assert provider_health_for(entry, {"status": "ok", "hits": 3}) == providers.ProviderHealth.HEALTHY


def test_provider_status_enum_values_stable() -> None:
    assert ProviderStatus.OK.value == "ok"
    assert ProviderStatus.ZERO_YIELD.value == "zero_yield"
    assert ProviderStatus.FAILED.value == "failed"
