"""Discovery pacing, rate-limit handling, and paced-run tests — hermetic."""

from __future__ import annotations

import textwrap

import pytest

from job_agent.catalog import (
    load_catalog,
)
from job_agent.config import default_config
from job_agent.discovery_search import (
    DiscoveryPacingReport,
    RateLimitExceeded,
    RateLimitPacer,
    SearchHit,
    run_planned_discovery,
)
from job_agent.query_plan import build_query_plan

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


@pytest.fixture
def mini_catalog(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    return load_catalog(path)


# ---------------------------------------------------------------------------
# Planner rotation + affinity eligibility
# ---------------------------------------------------------------------------


def test_rotation_covers_distinct_sources_before_second_round(mini_catalog):
    """Round-major rotation: every pair emits its preferred-city cell first."""
    cfg = default_config()
    cfg.career.location.preferred_city = "Munich"
    cfg.career.discovery.max_queries_total = 200
    cfg.career.discovery.max_queries_per_track = 200
    plan = build_query_plan(cfg, catalog=mini_catalog)
    pairs = {(item.track_id, item.source_id) for item in plan.items}
    assert len(pairs) >= 4
    first_round = plan.items[: len(pairs)]
    assert all(item.location_term == "Munich" for item in first_round)
    seen_sources: set[str] = set()
    seen_locations: set[str] = set()
    for item in plan.items:
        seen_sources.add(item.source_id)
        seen_locations.add(item.location_term)
    assert seen_locations == {"Munich", "Germany", "Remote Europe"}
    assert "agg_blocked" not in seen_sources
    assert {"ats_board_a", "agg_ai", "agg_general", "agg_robotics", "de_startups"} <= seen_sources


def test_eligibility_filters_by_category_affinity(mini_catalog):
    """Technical tracks without an ATS affinity never query ATS boards."""
    cfg = default_config()
    plan = build_query_plan(cfg, catalog=mini_catalog)
    ad = [item for item in plan.items if item.track_id == "autonomous_driving"]
    assert ad, "expected autonomous_driving tracks in the plan"
    assert not any(item.source_id.startswith("ats_") for item in ad)
    pm = [item for item in plan.items if item.track_id == "product_management"]
    assert any(item.source_id == "ats_board_a" for item in pm)


def test_reasons_are_attributed(mini_catalog):
    cfg = default_config()
    cfg.career.location.preferred_city = "Munich"
    cfg.career.discovery.max_queries_total = 200
    cfg.career.discovery.max_queries_per_track = 200
    plan = build_query_plan(cfg, catalog=mini_catalog)
    reasons = {item.reason for item in plan.items}
    assert "source_rotation" in reasons
    assert "german_locality" in reasons
    assert {"preferred_city_priority", "category_affinity"} & reasons, f"unexpected reasons {reasons}"
    for item in plan.items:
        assert item.reason in {
            "source_rotation",
            "german_locality",
            "preferred_city_priority",
            "category_affinity",
            "source_categories",
            "source_priority",
        }


def test_audit_items_shape(mini_catalog):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 4
    plan = build_query_plan(cfg, catalog=mini_catalog)
    items = plan.audit_items()
    assert items
    first = items[0]
    assert {"query", "track_id", "source_id", "source_type", "discovery_method", "location_term", "reason"}.issubset(
        first
    )
    assert "source_priority" in first
    assert "rate_limit_class" in first
    assert "source_country" in first


def test_budget_validation_rejects_bad_pacing():
    cfg = default_config()
    cfg.career.pacing.interval_by_class = {"low": 0.5, "medium": 1.0}
    with pytest.raises(ValueError):
        cfg.model_validate(cfg.model_dump(mode="json"))


def test_budget_validation_rejects_nonpositive_discovery():
    cfg = default_config()
    cfg.career.discovery.max_results_per_query = 0
    with pytest.raises(ValueError):
        cfg.model_validate(cfg.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# RateLimitPacer
# ---------------------------------------------------------------------------


@pytest.fixture
def pacing():
    cfg = default_config()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return cfg.career.pacing, sleeps, fake_sleep


def test_pacer_intervals_respect_rate_limit_class(pacing):
    cfg_pacing, sleeps, fake_sleep = pacing
    pacer = RateLimitPacer(cfg_pacing, now=lambda: 0.0, sleep=fake_sleep)
    assert pacer.interval_for("low") == 0.5
    assert pacer.interval_for("high") == 2.0
    pacer.wait_before("s1", "high")
    pacer.wait_before("s1", "low")
    assert sleeps == [2.0, 0.5]


def test_pacer_rate_limit_backoff_and_pause(pacing):
    cfg_pacing, sleeps, fake_sleep = pacing
    now = [0.0]
    pacer = RateLimitPacer(cfg_pacing, now=lambda: now[0], sleep=fake_sleep)
    assert pacer.should_attempt("s1")
    # Simulate two failures with a success in between: no pause on isolated failures.
    pacer.note_failure("s1", rate_limited=True)
    assert sleeps == [2.0]
    pacer.note_success("s1")
    sleeps.clear()
    pacer.note_failure("s1", rate_limited=True)
    pacer.note_failure("s1", rate_limited=True)
    pacer.note_failure("s1", rate_limited=True)
    assert pacer.should_attempt("s1") is False  # paused_skipped incremented
    assert not pacer.should_attempt("s1")
    stats = pacer.report({"s1": "high"})
    row = next((r for r in stats.per_source if r.source_id == "s1"), None)
    assert row is not None
    assert row.attempted_queries == 5  # 1 rate-limited + 1 success + 3 rate-limited
    assert row.successful_queries == 1
    assert row.rate_limited_queries == 4
    assert row.paused_skipped_queries == 2
    assert row.planned_queries == 0  # never set in this dedicated test


def test_pacer_single_success_keeps_source_alive(pacing):
    cfg_pacing, sleeps, fake_sleep = pacing
    pacer = RateLimitPacer(cfg_pacing, now=lambda: 0.0, sleep=fake_sleep)
    assert pacer.should_attempt("s")
    pacer.note_failure("s", rate_limited=False)
    assert pacer.should_attempt("s")
    pacer.note_success("s")
    assert pacer.should_attempt("s")
    stats = pacer.report({"s": "medium"})
    assert stats.per_source[0].successful_queries == 1
    assert stats.per_source[0].failed_queries == 1


def test_pacer_repeated_failures_pause_source(pacing):
    cfg_pacing, sleeps, fake_sleep = pacing
    cfg_pacing.max_consecutive_failures = 2
    now = [0.0]
    pacer = RateLimitPacer(cfg_pacing, now=lambda: now[0], sleep=fake_sleep)
    pacer.note_failure("s", rate_limited=False)
    assert pacer.should_attempt("s")
    pacer.note_failure("s", rate_limited=False)
    assert pacer.should_attempt("s") is False
    report = pacer.report({"s": "low"})
    assert report.totals["failed"] == 2
    assert report.totals["paused_skipped"] == 1


def test_pacer_does_not_retry_storm(pacing):
    cfg_pacing, sleeps, fake_sleep = pacing
    cfg_pacing.max_retries_per_query = 1
    cfg_pacing.max_consecutive_failures = 5
    pacer = RateLimitPacer(cfg_pacing, now=lambda: 0.0, sleep=fake_sleep)
    # The pacer never retries a single query itself; the caller applies
    # max_retries_per_query. Consecutive rate-limit failures eventually pause.
    pacer.note_failure("s", rate_limited=True)
    assert pacer.should_attempt("s")  # 1 failure < max_consecutive_failures
    assert sum(1 for _ in sleeps if _ > 0) == 1
    assert sleeps[0] == cfg_pacing.backoff_base_s


# ---------------------------------------------------------------------------
# Paced run end-to-end (hermetic)
# ---------------------------------------------------------------------------


def _default_config(tmp_path):
    cfg = default_config()
    cfg.sources.catalog_path = str(tmp_path / "catalog.yaml")
    cfg.profile_path = str(tmp_path / "profile.yaml")
    cfg.database_path = str(tmp_path / "jobs.sqlite3")
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
        self.raise_exc: Exception | None = None

    def search_one(self, query: str):
        self.queries.append(query)
        if self.raise_exc is not None:
            exc = self.raise_exc
            self.raise_exc = None
            raise exc
        return list(self.hits_by_query.get(query, []))


def _fake_fetch(urls, max_pages=200):
    jobs = []
    errors = []
    for u in urls:
        jobs.append(type("FakeJob", (), {"url": u, "title": "T", "company": "C", "id": u})())
    return jobs, errors


def test_paced_run_end_to_end_preserves_source_identity(mini_catalog, tmp_path):
    cfg = _default_config(tmp_path)
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 6

    # Deterministic hits keyed by query so every planned query yields one URL.
    plan = build_query_plan(cfg, catalog=mini_catalog)
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)

    plan2, jobs, provenance, errors, report = run_planned_discovery(
        cfg,
        engine=engine,
        fetch=_fake_fetch,
    )
    assert errors == []
    assert len(plan2.items) == len(plan.items)
    assert len(jobs) == len(plan.items)
    assert engine.queries == plan2.queries()  # exact plan order, no reordering
    assert len(provenance) == len(jobs)
    # Provenance forwards source identity for the very first job.
    assert provenance[0].source_id == plan2.items[0].source_id
    assert provenance[0].query == plan2.items[0].query
    assert isinstance(report, DiscoveryPacingReport)
    totals = report.to_dict()["totals"]
    assert totals["attempted"] == len(plan.items)
    assert totals["successful"] == len(plan.items)
    assert totals["rate_limited"] == 0
    assert totals["failed"] == 0


def test_paced_run_rate_limit_skips_then_continues(mini_catalog, tmp_path):
    cfg = _default_config(tmp_path)
    cfg.career.location.preferred_city = "Munich"
    cfg.career.discovery.max_queries_total = 200
    cfg.career.discovery.max_queries_per_track = 200

    plan = build_query_plan(cfg, catalog=mini_catalog)
    first_source = plan.items[0].source_id
    assert plan.items[0].location_term == "Munich"
    later_by_source = sum(1 for item in plan.items if item.source_id == first_source) - 1
    assert later_by_source >= 1  # the paused source recurs in the plan

    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)
    engine.raise_exc = RateLimitExceeded("excessive load generating behavior detected")
    sleeps: list[float] = []
    pacer = RateLimitPacer(cfg.career.pacing, now=lambda: 0.0, sleep=sleeps.append)
    cfg.career.pacing.max_consecutive_failures = 1  # first rate-limit pauses that source

    plan2, _jobs, _prov, errors, report = run_planned_discovery(
        cfg,
        engine=engine,
        fetch=_fake_fetch,
        pacing=pacer,
    )
    assert errors == []
    # The rate-limited source is paused for the rest of the run; every OTHER
    # source still gets its full plan cells (isolation: no stall).
    totals = report.to_dict()["totals"]
    other_attempted = totals["attempted"] - totals["rate_limited"]
    assert totals["rate_limited"] == 1
    assert totals["successful"] == other_attempted
    assert totals["failed"] == 0
    assert totals["paused_skipped"] == later_by_source
    assert totals["attempted"] + totals["paused_skipped"] == len(plan.items)
    assert len(engine.queries) == totals["attempted"]
    assert sleeps  # backoff absorbed once before continuing with other sources


def test_paced_run_generic_failure_isolated(mini_catalog, tmp_path):
    cfg = _default_config(tmp_path)
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 6

    plan = build_query_plan(cfg, catalog=mini_catalog)
    hits = {item.query: [{"title": "hit", "url": f"https://job.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)
    engine.raise_exc = RuntimeError("boom")

    plan2, _jobs, _prov, errors, report = run_planned_discovery(
        cfg,
        engine=engine,
        fetch=_fake_fetch,
    )
    assert errors == []
    assert len(engine.queries) == len(plan.items)
    totals = report.to_dict()["totals"]
    assert totals["failed"] == 1  # only the first query raised
    failed_source = plan.items[0].source_id
    row = next(r for r in report.per_source if r.source_id == failed_source)
    assert row.failed_queries == 1
    assert row.paused_skipped_queries == 0  # a single failure does not pause


def test_paced_run_reports_per_source_planned(mini_catalog, tmp_path):
    cfg = _default_config(tmp_path)
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 6

    plan = build_query_plan(cfg, catalog=mini_catalog)
    hits = {item.query: [{"title": "hit", "url": f"https://j.example/{idx}"}] for idx, item in enumerate(plan.items)}
    engine = _FakeEngine(hits)
    plan2, _jobs, _prov, _errors, report = run_planned_discovery(
        cfg,
        engine=engine,
        fetch=_fake_fetch,
    )
    by_source = {r.source_id: r.to_dict() for r in report.per_source}
    assert len(by_source) == len({item.source_id for item in plan2.items})
    for row in report.per_source:
        assert row.planned_queries >= 1
        assert row.attempted_queries == row.planned_queries


def test_rate_limit_exceeded_subclass_message():
    with pytest.raises(RateLimitExceeded):
        raise RateLimitExceeded("excessive load generating behavior detected")
