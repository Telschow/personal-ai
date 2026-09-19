"""M2 discovery yield + five-level dedup accounting tests — hermetic.

Covers the per-source queried-vs-yielded observability (hits_returned /
duplicate_on_page / duplicate_at_url / candidate_pages / jobs_parsed), the
ingest-level split between duplicate_at_db and previous_runs, the workspace
dedup rollup, and the merged DiscoveryYieldReport composition.
"""

from __future__ import annotations

import textwrap

from job_agent import db
from job_agent.catalog import load_catalog
from job_agent.config import default_config
from job_agent.discovery_search import (
    DiscoveryPacingReport,
    RateLimitPacer,
    SearchHit,
    SourceRunStats,
    run_planned_discovery,
)
from job_agent.models import Job
from job_agent.observability import discovery_yield_report
from job_agent.pipeline import ScanResult, ingest_global_jobs, run_lifecycle
from job_agent.query_plan import build_query_plan
from job_agent.scoring import ScoringPolicy

MINI = textwrap.dedent(
    """
    provenance:
      url: https://github.com/emredurukn/awesome-job-boards
      retrieved: "2026-09-18"
    sources:
      - source_id: ats_board_a
        name: Board A
        source_type: ats
        category: ats
        enabled: true
        priority: 90
        query_host: "boards-a.example"
      - source_id: agg_ai
        name: AI Boards
        source_type: search_engine
        category: ai
        enabled: true
        priority: 85
        query_host: "ai-boards.example"
      - source_id: agg_general
        name: General
        source_type: search_engine
        category: general
        enabled: true
        priority: 80
        query_host: "general.example"
      - source_id: agg_robotics
        name: Robotics Boards
        source_type: search_engine
        category: robotics
        enabled: true
        priority: 75
        query_host: "robotics.example"
      - source_id: de_startups
        name: German Startups
        source_type: search_engine
        category: startup
        country: DE
        enabled: true
        priority: 70
        query_host: "de-startups.example"
      - source_id: agg_blocked
        name: Blocked
        source_type: search_engine
        category: general
        enabled: false
        priority: 95
        reason: "bot-hostile"
    """
)

PROFILE = {"skills": ["Product Management", "AI"], "leadership_required": True}

POLICY = ScoringPolicy(
    salary_min=80000,
    salary_target=120000,
    similarity_weight=0.25,
    ai_weight=0.20,
    compensation_weight=0.15,
    location_weight=0.10,
    leadership_weight=0.20,
    purpose_weight=0.05,
    wlb_weight=0.05,
    industries=("AI",),
    negative_keywords=("junior",),
    target_roles=("Product Manager",),
)


def _default_config(tmp_path):
    cfg = default_config()
    cfg.sources.catalog_path = str(tmp_path / "catalog.yaml")
    cfg.profile_path = str(tmp_path / "profile.yaml")
    cfg.database_path = str(tmp_path / "jobs.sqlite3")
    (tmp_path / "catalog.yaml").write_text(MINI, encoding="utf-8")
    return cfg


class _FakeEngine:
    def __init__(self, hits_by_query: dict[str, list[dict]]):
        self.hits_by_query = {
            query: [
                SearchHit(url=hit["url"], title=hit.get("title", ""), snippet=hit.get("snippet", "")) for hit in hits
            ]
            for query, hits in hits_by_query.items()
        }
        self.queries: list[str] = []

    def search_one(self, query: str):
        self.queries.append(query)
        return list(self.hits_by_query.get(query, []))


def _url_fetch(urls, max_pages=200):
    jobs = []
    errors = []
    for u in urls:
        jobs.append(Job(id=u.split("/")[-1], url=u, title="T", company="C", source="fake"))
    return jobs, errors


def _small_plan_items(cfg):
    cfg.career.discovery.max_queries_total = 8
    plan = build_query_plan(cfg)
    return plan


def _run_silent(cfg, engine, fetch):
    pacer = RateLimitPacer(cfg.career.pacing, now=lambda: 0.0, sleep=lambda _s: None)
    return run_planned_discovery(cfg, engine=engine, fetch=fetch, pacing=pacer)


# ---------------------------------------------------------------------------
# Yield observability feeds (M2.1)
# ---------------------------------------------------------------------------


def test_run_loop_fills_yield_fields(tmp_path):
    cfg = _default_config(tmp_path)
    plan = _small_plan_items(cfg)
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)
    _plan, jobs, provenance, errors, report = _run_silent(cfg, engine, _url_fetch)

    assert errors == []
    assert len(jobs) == len(plan.items)
    # Every planned query returned exactly one distinct hit URL.
    assert report.totals["hits_returned"] == len(plan.items)
    assert report.totals["candidate_pages"] == len(plan.items)
    assert report.totals["jobs_parsed"] == len(plan.items)
    assert report.totals["duplicate_on_page"] == 0
    assert report.totals["duplicate_at_url"] == 0
    # Per-source: each source's jobs_parsed equals the jobs attributed to it.
    provenance_src_counts: dict[str, int] = {}
    for p in provenance:
        provenance_src_counts[p.source_id] = provenance_src_counts.get(p.source_id, 0) + 1
    for s in report.per_source:
        assert s.jobs_parsed == provenance_src_counts.get(s.source_id, 0)


def test_duplicate_on_page_within_one_query(tmp_path):
    cfg = _default_config(tmp_path)
    plan = _small_plan_items(cfg)
    q = plan.items[0].query
    src = plan.items[0].source_id
    # The same posting URL is returned twice for a single query.
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    hits[q] = [
        {"title": "hit", "url": "https://job.example/dup"},
        {"title": "hit", "url": "https://job.example/dup"},
    ]
    engine = _FakeEngine(hits)
    _plan, _jobs, _prov, _errors, report = _run_silent(cfg, engine, _url_fetch)

    row = next(s for s in report.per_source if s.source_id == src)
    assert row.duplicate_on_page == 1
    assert report.totals["duplicate_on_page"] == 1
    # The duplicate URL still surfaces only one candidate page / one job.
    assert report.totals["candidate_pages"] == len(plan.items)
    assert report.totals["jobs_parsed"] == len(plan.items)


def test_duplicate_at_url_cross_query_collapse(tmp_path):
    cfg = _default_config(tmp_path)
    plan = _small_plan_items(cfg)
    item_a, item_b = plan.items[0], plan.items[1]
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    # The second query (different source) surfaces the SAME URL item_a claimed.
    hits[item_b.query] = [{"title": "hit", "url": hits[item_a.query][0]["url"]}]
    engine = _FakeEngine(hits)
    _plan, _jobs, _prov, _errors, report = _run_silent(cfg, engine, _url_fetch)

    # The URL is claimed by the first-seen source only.
    row_b = next(s for s in report.per_source if s.source_id == item_b.source_id)
    assert row_b.duplicate_at_url == 1
    assert report.totals["duplicate_at_url"] == 1
    assert report.totals["candidate_pages"] == len(plan.items) - 1  # one candidate dropped via url-level dedup
    job_urls = {j.url for j in _jobs}
    assert len(job_urls) == len(plan.items) - 1


def test_catalog_unknown_jobs_counted(tmp_path):
    cfg = _default_config(tmp_path)
    plan = _small_plan_items(cfg)
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)

    def _fetch_with_stray(urls, max_pages=200):
        jobs, errors = _url_fetch(urls, max_pages=max_pages)
        # A page that parses to something not attributable to any plan query.
        jobs.append(Job(id="stray", url="https://stray.example/1", title="T", company="C", source="fake"))
        return jobs, errors

    _plan, jobs, provenance, errors, report = _run_silent(cfg, engine, _fetch_with_stray)
    assert report.catalog_unknown_jobs == 1
    assert any(p.source_id == "catalog_unknown" for p in provenance)


# ---------------------------------------------------------------------------
# Ingest-level dedup attribution (M2.2): duplicate_at_db vs previous_runs
# ---------------------------------------------------------------------------


def _raw_job(jid: str, url: str, company: str = "Acme") -> Job:
    return Job(
        id=jid,
        title="Product Manager AI",
        company=company,
        url=url,
        source="fake",
        location="Munich",
    )


def test_ingest_duplicate_attribution(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    result = ingest_global_jobs(
        conn,
        [_raw_job("1", "https://acme.example/1", company="Acme GmbH")],
        PROFILE,
        POLICY,
        provenance=None,
    )
    assert result.total_duplicates == 0
    assert result.persisted_by_source.get("unknown", 0) == 1

    # Same canonical key (company "Acme" == "Acme GmbH") → duplicate_at_db.
    result2 = ingest_global_jobs(
        conn,
        [_raw_job("2", "https://acme.example/2", company="Acme")],
        PROFILE,
        POLICY,
        provenance=None,
    )
    assert result2.total_duplicates == 1
    assert result2.duplicate_at_db_by_source.get("unknown", 0) == 1
    assert result2.previous_runs_by_source == {}
    conn.close()


def test_ingest_ng_previous_runs_attribution(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    run_started_at = "2030-01-01T00:00:00+00:00"

    # Seed a row discovered before the run boundary.
    seed = [_raw_job("1", "https://acme.example/1", company="Acme GmbH")]
    ingest_global_jobs(conn, seed, PROFILE, POLICY, provenance=None)

    result = ingest_global_jobs(
        conn,
        [_raw_job("2", "https://acme.example/2", company="Acme")],
        PROFILE,
        POLICY,
        provenance=None,
        run_started_at=run_started_at,
    )
    assert result.previous_runs_by_source.get("unknown", 0) == 1
    assert result.duplicate_at_db_by_source == {}


def test_ingest_duplicate_at_db_within_run_start_cut(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    # run_started_at far in the past → the first row ALSO predates the run.
    result = ingest_global_jobs(
        conn,
        [_raw_job("1", "https://acme.example/1", company="Acme GmbH")],
        PROFILE,
        POLICY,
        provenance=None,
        run_started_at="2026-09-17T10:00:00+00:00",
    )
    assert result.persisted_by_source.get("unknown", 0) == 1
    # Second identical canonical row created within the run window → at_db.
    result2 = ingest_global_jobs(
        conn,
        [_raw_job("2", "https://acme.example/2", company="Acme")],
        PROFILE,
        POLICY,
        provenance=None,
        run_started_at="2026-09-17T10:00:00+00:00",
    )
    assert result2.duplicate_at_db_by_source.get("unknown", 0) == 1
    assert result2.previous_runs_by_source == {}


def test_ingest_duplicate_attribution_by_source(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    from job_agent.pipeline import Provenance

    jobs = [
        _raw_job("1", "https://acme.example/1", company="Acme GmbH"),
        _raw_job("2", "https://acme.example/2", company="Acme"),
        _raw_job("3", "https://other.example/3", company="Other Corp"),
    ]
    provenance = [
        Provenance(source_id="ats_board_a", query="pm munich"),
        Provenance(source_id="ats_board_a", query="pm munich"),
        Provenance(source_id="agg_general", query="pm germany"),
    ]
    result = ingest_global_jobs(conn, jobs, PROFILE, POLICY, provenance=provenance)
    assert result.total_duplicates == 1
    assert result.persisted_by_source == {"ats_board_a": 1, "agg_general": 1}
    assert result.duplicate_at_db_by_source == {"ats_board_a": 1}


# ---------------------------------------------------------------------------
# Workspace dedup rollup (duplicate_workspace)
# ---------------------------------------------------------------------------


def test_run_lifecycle_reports_workspace_dedup(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    ingest_global_jobs(
        conn,
        [_raw_job("1", "https://acme.example/1", company="Acme GmbH")],
        PROFILE,
        POLICY,
        provenance=None,
    )
    ingest_global_jobs(
        conn,
        [_raw_job("2", "https://acme.example/2", company="Acme")],
        PROFILE,
        POLICY,
        provenance=None,
    )
    cfg = _default_config(tmp_path)
    lifecycle = run_lifecycle(conn, cfg, seen_ids=set())
    assert lifecycle["dedup_groups"] >= 1
    assert lifecycle["dedup_merged"] >= 1
    conn.close()


# ---------------------------------------------------------------------------
# Merged DiscoveryYieldReport composition (M2.3)
# ---------------------------------------------------------------------------


def _distinct_fetch(urls, max_pages=200):
    jobs = []
    for i, u in enumerate(urls):
        jobs.append(Job(id=f"c{i}", title=f"Product {i}", company=f"Company {i}", url=u, source="fake"))
    return jobs, []


def test_yield_report_merges_sources_and_dedup(tmp_path):
    cfg = _default_config(tmp_path)
    plan = _small_plan_items(cfg)
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    # Make one source yield nothing: its queries surface no URLs at all.
    dry_source = plan.items[-1].source_id
    hits = {
        item.query: [] if item.source_id == dry_source else hits[item.query]
        for item in plan.items
        if item.query in hits
    }
    engine = _FakeEngine(hits)
    _plan, jobs, _prov, _errors, pacing_report = _run_silent(cfg, engine, _distinct_fetch)
    conn = db.connect(str(tmp_path / "t.db"))
    result = ingest_global_jobs(conn, jobs, PROFILE, POLICY, provenance=_prov)
    lifecycle = run_lifecycle(conn, cfg, seen_ids=result.jobs_seen)

    catalog = load_catalog(tmp_path / "catalog.yaml")
    report = discovery_yield_report(
        pacing_report=pacing_report,
        scan_result=result,
        lifecycle=lifecycle,
        qualifying_sources={e.source_id for e in catalog.enabled()},
    )
    assert dry_source in report.zero_yield_sources
    assert report.totals["sources_zero_yield"] == 1
    planned_ids = {item.source_id for item in plan.items}
    enabled_ids = {e.source_id for e in catalog.enabled()}
    assert set(report.never_queried_sources) == enabled_ids - planned_ids
    assert report.totals["jobs_persisted"] == len(jobs)
    assert report.totals["candidate_pages"] == len(jobs)
    payload = report.to_dict()
    assert isinstance(payload["totals"], dict)
    assert payload["dedup_workspace"] == lifecycle.get("dedup_workspace", payload["dedup_workspace"])
    conn.close()


def test_yield_report_shape_is_aggregate_only(tmp_path):
    report = discovery_yield_report(
        pacing_report=DiscoveryPacingReport(
            per_source=(
                SourceRunStats(
                    source_id="s1",
                    rate_limit_class="medium",
                    planned_queries=2,
                    attempted_queries=2,
                    successful_queries=2,
                    rate_limited_queries=0,
                    failed_queries=0,
                    paused_skipped_queries=0,
                    hits_returned=2,
                    duplicate_on_page=0,
                    duplicate_at_url=0,
                    candidate_pages=2,
                    jobs_parsed=2,
                ),
            )
        ),
        scan_result=ScanResult(persisted_by_source={"s1": 2}),
        lifecycle={"dedup_groups": 3, "dedup_merged": 4},
        qualifying_sources={"s1", "s2"},
    ).to_dict()
    assert report["totals"]["jobs_persisted"] == 2
    assert report["totals"]["duplicate_workspace_groups"] == 3
    assert report["totals"]["duplicate_workspace_merged"] == 4
    assert report["never_queried_sources"] == ["s2"]
    assert report["zero_yield_sources"] == []
    # Aggregate-only: identifiers and counts, never job content.
    for key in ("title", "url", "description", "query"):
        assert key not in report
