"""Query-plan tests — hermetic, deterministic, no network."""

from __future__ import annotations

import textwrap

from job_agent.catalog import load_catalog
from job_agent.config import default_config
from job_agent.query_plan import (
    build_query_plan,
    focal_locations,
    planned_queries,
    source_locations,
)

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
    cfg.career.location.preferred_city = "Munich"
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


def test_plan_without_preferred_city_has_no_default_city(tmp_path):
    """A fresh config has no city preference; discovery must not invent one."""
    cfg = default_config()
    cfg.career.discovery.max_queries_total = 200
    cfg.career.discovery.max_queries_per_track = 200
    cfg.career.discovery.max_sources_per_track = 8
    plan = build_query_plan(cfg, catalog=_catalog(tmp_path))
    assert "Munich" not in summary_locations(plan)
    assert summary_locations(plan) == ["Germany", "Remote Europe"]


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


def test_focal_locations_without_a_preferred_city():
    """No configured city means no invented one; scope terms still apply."""
    locs = focal_locations("")
    assert "Munich" not in locs
    assert locs == ["Germany", "Remote Europe"]


def test_focal_locations_fall_back_when_every_scope_flag_is_off():
    """With nothing configured at all, the fallback is geography-free."""
    assert focal_locations("", national=False, remote=False) == ["Remote Europe"]


def test_dach_sources_drop_broad_remote_terms():
    """A regional source gets concrete place terms only."""
    from job_agent.catalog import SourceCatalogEntry

    regional = SourceCatalogEntry(
        source_id="de_board",
        name="Regional Board",
        url="https://example.test",
        category="general",
        country="DE",
        region="DACH",
        source_type="ats",
    )
    base = focal_locations("Munich")
    assert source_locations(regional, base) == ["Munich", "Germany"]
    # With no city configured the national term survives, remote does not.
    assert source_locations(regional, focal_locations("")) == ["Germany"]


def test_non_dach_source_keeps_the_full_base_set():
    from job_agent.catalog import SourceCatalogEntry

    global_source = SourceCatalogEntry(
        source_id="global_board",
        name="Global Board",
        url="https://example.test",
        category="general",
        country="US",
        source_type="ats",
    )
    base = focal_locations("Munich")
    assert source_locations(global_source, base) == base


def summary_locations(plan):
    ordered: list[str] = []
    for item in plan.items:
        if item.location_term not in ordered:
            ordered.append(item.location_term)
    return ordered
