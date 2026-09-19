"""Source catalog tests — hermetic, no network."""

from __future__ import annotations

import textwrap

from job_agent.catalog import (
    CatalogSourceType,
    DiscoveryMethod,
    SourceCatalog,
    SourceCatalogEntry,
    load_catalog,
    query_hosts,
    select_sources,
)

MINI = textwrap.dedent(
    """
    provenance:
      url: https://github.com/emredurukn/awesome-job-boards
      retrieved: "2026-09-18"
    sources:
      - source_id: ats_goofy
        name: Goofy Board
        source_type: ats
        enabled: true
        priority: 90
        query_host: "boards.goofy.example"
      - source_id: search_example
        name: Example Search
        source_type: search_engine
        enabled: true
        priority: 70
      - source_id: agg_disabled
        name: Disabled Aggregator
        source_type: search_engine
        enabled: false
        priority: 95
        reason: "bot-hostile"
      - source_id: api_one
        name: API One
        source_type: structured_data
        enabled: true
        priority: 75
        discovery_method: ats_api
    """
)


def test_load_catalog_from_text(tmp_path):
    path = tmp_path / "sources_catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    catalog = load_catalog(path)
    assert catalog.counts()["total"] == 4
    assert catalog.counts()["enabled"] == 3
    assert catalog.counts()["disabled"] == 1


def test_load_catalog_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_catalog(tmp_path / "nope.yaml")


def test_entries_carry_types_and_methods(tmp_path):
    path = tmp_path / "sources_catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    catalog = load_catalog(path)
    goofy = catalog.by_id("ats_goofy")
    assert goofy is not None
    assert goofy.source_type is CatalogSourceType.ATS
    assert goofy.query_host == "boards.goofy.example"
    disabled = catalog.by_id("agg_disabled")
    assert disabled is not None and not disabled.enabled and disabled.reason


def test_query_hosts_direct_only(tmp_path):
    path = tmp_path / "sources_catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    catalog = load_catalog(path)
    hosts = query_hosts(catalog.sources)
    assert "boards.goofy.example" in hosts
    assert len(hosts) == 1


def test_select_sources_omits_disabled_and_orders(tmp_path):
    from job_agent.career_tracks import DEFAULT_TRACKS

    path = tmp_path / "sources_catalog.yaml"
    path.write_text(MINI, encoding="utf-8")
    catalog = load_catalog(path)
    track = DEFAULT_TRACKS[0]
    selected = select_sources(track, catalog, limit=10)
    ids = [s.source_id for s in selected]
    assert "agg_disabled" not in ids
    assert ids[0] == "ats_goofy"  # priority desc
    assert ids.index("api_one") < ids.index("search_example")


def test_select_sources_respects_track_categories():
    from job_agent.career_tracks import CareerTrack

    track = CareerTrack(
        track_id="only_ats",
        name="Only ATS",
        search_terms=[],
        title_patterns=[],
        domain_terms=[],
        source_categories=["ats"],
        priority=50,
    )
    catalog = SourceCatalog(
        sources=[
            SourceCatalogEntry(source_id="a", name="A", source_type=CatalogSourceType.ATS, enabled=True, priority=1),
            SourceCatalogEntry(
                source_id="s", name="S", source_type=CatalogSourceType.SEARCH_ENGINE, enabled=True, priority=99
            ),
        ],
    )
    selected = select_sources(track, catalog)
    assert [s.source_id for s in selected] == ["a"]


def test_builtin_catalog_is_populated():
    from job_agent.config import PROJECT_ROOT

    catalog = load_catalog(PROJECT_ROOT / "sources_catalog.yaml")
    counts = catalog.counts()
    assert counts["total"] >= 30
    assert counts["enabled"] >= 20
    assert counts["disabled"] >= 5
    assert catalog.by_id("ats_greenhouse") is not None
    linkedin = catalog.by_id("search_linkedin")
    assert linkedin is not None
    assert not linkedin.enabled
    assert linkedin.reason
    assert catalog.by_id("linkedin") is None


def test_discovery_method_enum():
    assert DiscoveryMethod("search_engine") == DiscoveryMethod.SEARCH_ENGINE
    assert DiscoveryMethod("ats_api") == DiscoveryMethod.ATS_API
