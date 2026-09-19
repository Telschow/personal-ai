"""Query-plan tests — hermetic, deterministic, no network."""

from __future__ import annotations

import textwrap

from job_agent.catalog import load_catalog
from job_agent.config import default_config
from job_agent.query_plan import build_query_plan, focal_locations, planned_queries

MINI = textwrap.dedent(
    """
    provenance:
      url: https://github.com/emredurukn/awesome-job-boards
      retrieved: "2026-09-18"
    sources:
      - source_id: ats_board_a
        name: Board A
        source_type: ats
        enabled: true
        priority: 90
        query_host: "boards-a.example"
      - source_id: ats_board_b
        name: Board B
        source_type: ats
        enabled: true
        priority: 80
        query_host: "boards-b.example"
      - source_id: agg_blocked
        name: Blocked
        source_type: search_engine
        enabled: false
        priority: 95
        reason: "bot-hostile"
      - source_id: api_one
        name: API One
        source_type: structured_data
        enabled: true
        priority: 70
        discovery_method: ats_api
    """
)


def _catalog(tmp_path):
    path = tmp_path / "sources_catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    return load_catalog(path)


def test_plan_deterministic(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 4
    cat = _catalog(tmp_path)
    a = build_query_plan(cfg, catalog=cat)
    b = build_query_plan(cfg, catalog=cat)
    assert a.queries() == b.queries()
    assert a.summary() == b.summary()


def test_plan_respects_budgets(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 12
    cfg.career.discovery.max_queries_per_track = 4
    cat = _catalog(tmp_path)
    plan = build_query_plan(cfg, catalog=cat, limit_total=12, limit_per_track=4)
    summary = plan.summary()
    assert summary["query_count"] <= 12
    assert all(v <= 4 for v in summary["queries_by_track"].values())


def test_plan_location_rotation(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 200
    cfg.career.discovery.max_queries_per_track = 200
    cfg.career.discovery.max_sources_per_track = 8
    cat = _catalog(tmp_path)
    plan = build_query_plan(cfg, catalog=cat)
    queries = plan.queries()
    assert queries
    assert "Munich" in queries[0]
    assert summary_locations(plan) == ["Munich", "Germany", "Remote Europe"]
    for item in plan.items:
        assert item.location_term in ("Munich", "Germany", "Remote Europe")


def test_plan_provenance_metadata(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 10
    cfg.career.discovery.max_queries_per_track = 4
    cat = _catalog(tmp_path)
    plan = build_query_plan(cfg, catalog=cat)
    items = plan.items
    assert items
    first = items[0]
    prov = first.provenance
    assert prov["source"] == "ats_board_a"
    assert prov["track"] == first.track_id
    assert prov["discovery_method"]
    assert prov["location_term"] in ("Munich", "Germany", "Remote Europe")


def test_plan_dedup(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 100
    cfg.career.discovery.max_queries_per_track = 100
    cfg.career.discovery.max_sources_per_track = 100
    cat = _catalog(tmp_path)
    plan = build_query_plan(cfg, catalog=cat)
    queries = plan.queries()
    assert len(queries) == len(set(queries))


def test_plan_caps_sources_per_track(tmp_path):
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 100
    cfg.career.discovery.max_queries_per_track = 100
    cat = _catalog(tmp_path)
    plan = build_query_plan(cfg, catalog=cat, limit_sources=1)
    sources = {item.source_id for item in plan.items}
    assert sources == {"ats_board_a"}


def test_planned_queries_falls_back_when_catalog_disabled(tmp_path):
    cfg = default_config()
    cfg.sources.use_catalog = False
    cfg.search.target_roles = ["Product Manager"]
    cfg.search.global_locations = ["Munich"]
    cfg.search.result_query_cap = 10
    queries = planned_queries(cfg)
    assert queries
    from job_agent.discovery_search import build_global_queries

    assert queries == build_global_queries(cfg)[: len(queries)] or queries == build_global_queries(cfg)


def test_focal_locations():
    locs = focal_locations("Munich")
    assert locs[0] == "Munich"
    assert "Germany" in locs
    assert "Remote Europe" in locs
    assert focal_locations("Munich") == focal_locations("Munich")


def summary_locations(plan):
    ordered: list[str] = []
    for item in plan.items:
        if item.location_term not in ordered:
            ordered.append(item.location_term)
    return ordered
