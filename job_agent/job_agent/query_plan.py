"""Deterministic, budgeted, source-rotating discovery query planning.

Replaces the earlier ad-hoc query clusters with a track- and source-aware
plan: each career track selects its catalog sources (by source type and
category affinity) and the planner expands them into queries using a bounded
*round-major rotation*.

Rotation
--------
A "pair" is a ``(track, source)`` combination with an ordered list of cells
(one cell per ``search_term x location_term``). The plan is emitted in
rounds: round ``r`` appends exactly one query per non-exhausted pair (the
pair's ``r``-th cell), iterating tracks in priority order and sources in
priority order. All budgets are hard caps:

* ``max_queries_total`` — the global cap (never exceeded),
* ``max_queries_per_track`` — per-track cap,
* ``max_queries_per_source`` — cap on how many queries one source may
  contribute *within a single track* (a high-priority ATS platform can never
  swallow a track's whole budget, while the round-major rotation + the
  per-track/global caps bound its total share),
* ``max_sources_per_track`` — how many eligible sources a track may use.

The rotation guarantees that every enabled track's highest-priority sources
receive a first query before any source receives a second one, and that a
source is never starved by an earlier track block. Priorities still order the
candidates; the caps bound the allocation.

Eligibility and Munich-weighting
--------------------------------
A source is eligible for a track when it is enabled, its ``source_type`` is
among the track's ``source_categories`` (if any), and its catalog ``category``
is among the track's ``category_affinities`` (if any; the ``general`` category
always matches conservative breadth). Sources from Germany / DACH are queried
with Munich + Germany locations only (locality scoping). Munich is *ordering*,
never a filter: the preferred city is always the first location term.
Everything here is pure and deterministic — the same configuration always
yields the same query list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .career_tracks import CareerTrack
from .catalog import SourceCatalog, SourceCatalogEntry, load_catalog
from .config import Config
from .location import normalize_location

_DEFAULT_FALLBACK_LOCATIONS = ("Munich", "Germany", "Remote Europe")

_RATE_LIMIT_CLASSES = ("low", "medium", "high")


@dataclass(frozen=True)
class QueryPlanItem:
    query: str
    track_id: str
    source_id: str
    source_type: str
    discovery_method: str
    location_term: str
    reason: str = "source_priority"
    source_priority: int = 0
    rate_limit_class: str = "medium"
    source_country: str = "global"

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "track": self.track_id,
            "source": self.source_id,
            "source_type": self.source_type,
            "discovery_method": self.discovery_method,
            "location_term": self.location_term,
        }

    def audit(self) -> dict[str, object]:
        return {
            "query": self.query,
            "track_id": self.track_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "discovery_method": self.discovery_method,
            "location_term": self.location_term,
            "reason": self.reason,
            "source_priority": self.source_priority,
            "rate_limit_class": self.rate_limit_class,
            "source_country": self.source_country,
        }


@dataclass(frozen=True)
class QueryPlan:
    items: tuple[QueryPlanItem, ...] = ()
    budgets: dict[str, int] = field(default_factory=dict)
    locations: tuple[str, ...] = ()

    def queries(self) -> list[str]:
        return [item.query for item in self.items]

    def audit_items(self) -> list[dict[str, object]]:
        return [item.audit() for item in self.items]

    def summary(self) -> dict[str, object]:
        per_track: dict[str, int] = {}
        per_source: dict[str, int] = {}
        per_reason: dict[str, int] = {}
        for item in self.items:
            per_track[item.track_id] = per_track.get(item.track_id, 0) + 1
            per_source[item.source_id] = per_source.get(item.source_id, 0) + 1
            per_reason[item.reason] = per_reason.get(item.reason, 0) + 1
        return {
            "query_count": len(self.items),
            "tracks_covered": sorted(per_track),
            "sources_covered": len(per_source),
            "queries_by_track": per_track,
            "queries_by_source": per_source,
            "queries_by_reason": per_reason,
            "locations": list(self.locations),
            "budgets": dict(self.budgets),
        }


def focal_locations(preferred_city: str, *, national: bool = True, remote: bool = True) -> list[str]:
    """Ordered discovery location terms — preferred city first, then region."""
    terms: list[str] = []
    city = (preferred_city or "").strip()
    if city:
        terms.append(city)
        norm = normalize_location(city)
        if norm and norm not in (t.casefold() for t in terms):
            terms.append(norm)
    if national and "germany" not in " ".join(t.casefold() for t in terms):
        terms.append("Germany")
    if remote and "remote" not in " ".join(t.casefold() for t in terms):
        terms.append("Remote Europe")
    if not terms:
        return list(_DEFAULT_FALLBACK_LOCATIONS)
    return terms


def _is_dach(source: SourceCatalogEntry) -> bool:
    return source.country == "DE" or source.region == "DACH" or (source.language or "").lower() == "de"


def source_locations(source: SourceCatalogEntry, base_locations: list[str] | tuple[str, ...]) -> list[str]:
    """Location terms for a source.

    German / DACH sources are locality-scoped to the Munich + Germany terms
    (when the base contains them); everything else keeps the full base set.
    Returns the base set unchanged when scoping would produce nothing.
    """
    if not _is_dach(source):
        return list(base_locations)
    scoped = [loc for loc in base_locations if loc.casefold() in ("munich", "germany")]
    return scoped or list(base_locations)


def _eligibility_reason(track, source: SourceCatalogEntry) -> str:
    if track.category_affinities and source.category in track.category_affinities:
        return "category_affinity"
    if track.source_categories and source.source_type.value in track.source_categories:
        return "source_categories"
    return "source_priority"


def _reason_for(track, source: SourceCatalogEntry, cell_loc: str, base_locations: list[str], round_index: int) -> str:
    if round_index > 0:
        return "source_rotation"
    if _is_dach(source):
        return "german_locality"
    if base_locations and cell_loc == base_locations[0]:
        return "munich_priority"
    return _eligibility_reason(track, source)


def build_query_plan(
    cfg: Config,
    *,
    catalog: SourceCatalog | None = None,
    limit_total: int | None = None,
    limit_per_track: int | None = None,
    limit_sources: int | None = None,
    limit_per_source: int | None = None,
) -> QueryPlan:
    """Build the deterministic, budgeted, rotating discovery plan for a config."""
    budgets = {
        "max_queries_total": limit_total or cfg.career.discovery.max_queries_total,
        "max_queries_per_track": limit_per_track or cfg.career.discovery.max_queries_per_track,
        "max_queries_per_source": limit_per_source or cfg.career.discovery.max_queries_per_source,
        "max_sources_per_track": limit_sources or cfg.career.discovery.max_sources_per_track,
    }
    if catalog is None:
        catalog = _catalog_for(cfg)

    base_locations = focal_locations(
        cfg.career.location.preferred_city,
        national=cfg.career.location.national,
        remote=cfg.career.location.remote,
    )
    tracks = [t for t in sorted(cfg.career.tracks.resolved, key=lambda t: (-t.priority, t.track_id)) if t.enabled]

    max_total = budgets["max_queries_total"]
    max_track = budgets["max_queries_per_track"]
    max_source = budgets["max_queries_per_source"]
    max_sources_track = budgets["max_sources_per_track"]

    # (track, source, cells) ordered by track priority, then source priority.
    pairs: list[tuple[CareerTrack, SourceCatalogEntry, list[tuple[str, str]]]] = []
    for track in tracks:
        if max_sources_track <= 0:
            break
        sources = select_sources_bounded(track, catalog, max_sources_track)
        for source in sources:
            locations = source_locations(source, base_locations)
            cells = [(term, loc) for term in track.search_terms for loc in locations]
            if cells:
                pairs.append((track, source, cells))

    items: list[QueryPlanItem] = []
    seen_queries: set[str] = set()
    used_by_track: dict[str, int] = {}
    used_by_pair: dict[tuple[str, str], int] = {}
    emitted = 0

    round_index = 0
    while emitted < max_total:
        progressed = False
        for track, source, cells in pairs:
            if emitted >= max_total:
                break
            if used_by_track.get(track.track_id, 0) >= max_track:
                continue
            if used_by_pair.get((track.track_id, source.source_id), 0) >= max_source:
                continue
            if round_index >= len(cells):
                continue
            term, loc = cells[round_index]
            host = f"site:{source.query_host} " if source.query_host else ""
            query = f'{host}"{term}" {loc} jobs'.strip()
            if query in seen_queries:
                continue
            seen_queries.add(query)
            items.append(
                QueryPlanItem(
                    query=query,
                    track_id=track.track_id,
                    source_id=source.source_id,
                    source_type=source.source_type.value,
                    discovery_method=source.discovery_method.value,
                    location_term=loc,
                    reason=_reason_for(track, source, loc, base_locations, round_index),
                    source_priority=source.priority,
                    rate_limit_class=source.rate_limit_class
                    if source.rate_limit_class in _RATE_LIMIT_CLASSES
                    else "medium",
                    source_country=source.country or "global",
                )
            )
            used_by_track[track.track_id] = used_by_track.get(track.track_id, 0) + 1
            used_by_pair[(track.track_id, source.source_id)] = (
                used_by_pair.get((track.track_id, source.source_id), 0) + 1
            )
            emitted += 1
            progressed = True
        round_index += 1
        if not progressed:
            break

    return QueryPlan(items=tuple(items), budgets=budgets, locations=tuple(base_locations))


def select_sources_bounded(track, catalog: SourceCatalog, limit: int):
    from .catalog import select_sources

    return select_sources(track, catalog, limit=limit)


def planned_queries(cfg: Config) -> list[str]:
    """Queries used by the scan/global-discovery path.

    When the catalog is enabled and loadable the plan governs; otherwise it
    falls back to the legacy ad-hoc generation so an unconfigured install
    still works.
    """
    if not cfg.sources.use_catalog:
        from .discovery_search import build_global_queries

        return build_global_queries(cfg)
    try:
        catalog = load_catalog(cfg.catalog_path_resolved())
    except (FileNotFoundError, ValueError):
        from .discovery_search import build_global_queries

        return build_global_queries(cfg)
    return build_query_plan(cfg, catalog=catalog).queries()


def _catalog_for(cfg: Config) -> SourceCatalog:
    return load_catalog(cfg.catalog_path_resolved())


# Keep the module import-light for tests that build plans with an explicit catalog.
__all__ = [
    "QueryPlan",
    "QueryPlanItem",
    "build_query_plan",
    "focal_locations",
    "planned_queries",
    "source_locations",
]
