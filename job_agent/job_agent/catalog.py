"""Versioned job-source catalog.

The catalog is a checked-in YAML registry describing *where* jobs can be
discovered and *how* (discovery method), together with provenance so a future
maintainer knows where the list came from. It is data, not code: no network,
no scraping, no browser automation happens here.

Selection is deterministic: a career track names the source categories it
wants, and :func:`select_sources` returns the enabled entries in priority
order. Disabled entries are retained with a reason so the registry documents
what is deliberately out of scope.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .career_tracks import CareerTrack


class CatalogSourceType(StrEnum):
    ATS = "ats"
    STRUCTURED_DATA = "structured_data"
    SEARCH_ENGINE = "search_engine"
    FEED = "feed"
    DIRECT = "direct"
    BROWSER = "browser"
    RESTRICTED = "restricted"


class DiscoveryMethod(StrEnum):
    ATS_API = "ats_api"
    JSONLD = "jsonld"
    RSS_JSON = "rss_json"
    SEARCH_ENGINE = "search_engine"
    BROWSER = "browser"
    RESTRICTED = "restricted"


class CatalogProvenance(BaseModel):
    repository: str = ""
    url: str = ""
    license: str = ""
    retrieved: str = ""
    note: str = ""


class SourceCatalogEntry(BaseModel):
    source_id: str
    name: str
    url: str = ""
    category: str = "general"
    country: str = "global"
    region: str = ""
    language: str = ""
    source_type: CatalogSourceType = CatalogSourceType.SEARCH_ENGINE
    discovery_method: DiscoveryMethod = DiscoveryMethod.SEARCH_ENGINE
    supports_location_filter: bool = False
    supports_keyword_filter: bool = True
    supports_remote_filter: bool = False
    supports_salary_filter: bool = False
    supports_pagination: bool = False
    supports_structured_data: bool = False
    supports_ats_links: bool = False
    rate_limit_class: str = "medium"
    enabled: bool = True
    priority: int = 50
    reason: str = ""
    query_host: str = ""
    upstream: str = ""


class SourceCatalog(BaseModel):
    version: str = "1"
    provenance: CatalogProvenance = Field(default_factory=CatalogProvenance)
    sources: list[SourceCatalogEntry] = Field(default_factory=list)

    def by_id(self, source_id: str) -> SourceCatalogEntry | None:
        for entry in self.sources:
            if entry.source_id == source_id:
                return entry
        return None

    def enabled(self) -> list[SourceCatalogEntry]:
        return [s for s in self.sources if s.enabled]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"total": len(self.sources), "enabled": 0, "disabled": 0}
        for entry in self.sources:
            out["enabled" if entry.enabled else "disabled"] += 1
            out[entry.source_type.value] = out.get(entry.source_type.value, 0) + 1
        return out


def load_catalog(path: str | Path) -> SourceCatalog:
    """Load and validate the catalog YAML at ``path``."""
    catalog_path = Path(path)
    if not catalog_path.exists():
        raise FileNotFoundError(f"source catalog not found: {catalog_path}")
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid source catalog structure in {catalog_path}")
    return SourceCatalog.model_validate(data)


def select_sources(
    track: CareerTrack,
    catalog: SourceCatalog,
    *,
    limit: int | None = None,
) -> list[SourceCatalogEntry]:
    """Return enabled catalog entries for a track, deterministically ordered.

    A track with no ``source_categories`` matches every enabled entry; a track
    with non-empty ``category_affinities`` additionally requires the entry's
    catalog ``category`` to be among them (so AI boards reach AI tracks and
    German boards reach their categories before global ATS platforms dominate
    every track). Ties are broken by ``priority`` (desc), then ``source_id``
    (asc), so the result is stable across runs and platforms.
    """
    wanted = set(track.source_categories)
    affinities = set(track.category_affinities)
    candidates: list[SourceCatalogEntry] = []
    for s in catalog.sources:
        if not s.enabled:
            continue
        if wanted and s.source_type.value not in wanted:
            continue
        if affinities and s.category not in affinities and s.category != "general":
            continue
        candidates.append(s)
    candidates.sort(key=lambda s: (-s.priority, s.source_id))
    if limit is not None and limit >= 0:
        return candidates[:limit]
    return candidates


def query_hosts(entries: list[SourceCatalogEntry]) -> list[str]:
    """Distinct ``site:`` hosts for search-engine query generation."""
    hosts: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.query_host and entry.query_host not in seen:
            seen.add(entry.query_host)
            hosts.append(entry.query_host)
    return hosts


__all__ = [
    "CatalogProvenance",
    "CatalogSourceType",
    "DiscoveryMethod",
    "SourceCatalog",
    "SourceCatalogEntry",
    "load_catalog",
    "query_hosts",
    "select_sources",
]
