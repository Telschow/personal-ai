"""Discovery diagnostics (deterministic, aggregate-only).

Answers "what do I actually have?" without dumping job content: coverage by
career track, location tier, seniority band, and — from the provenance table —
by discovery source and method. Pure composition over the store boundary and
the deterministic classifiers; no SQL, no network, no model calls.
"""

from __future__ import annotations

from dataclasses import field

from pydantic import BaseModel

from . import db
from .career.requirements import extract_job_attributes
from .career_tracks import classify_track, normalize_seniority
from .location import parse_location


class DiscoveryDiagnostics(BaseModel):
    """Aggregate-only overview of the stored discovery result."""

    jobs_sampled: int
    jobs_total: int
    jobs_by_career_track: dict[str, int] = field(default_factory=dict)
    jobs_by_location_tier: dict[str, int] = field(default_factory=dict)
    jobs_by_region: dict[str, int] = field(default_factory=dict)
    jobs_by_seniority: dict[str, int] = field(default_factory=dict)
    jobs_by_canonical_source: dict[str, int] = field(default_factory=dict)
    records_by_discovery_method: dict[str, int] = field(default_factory=dict)
    canonical_url_stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def discovery_diagnostics(
    conn, *, sample_limit: int = 1000, unknown_track_label: str = "unknown"
) -> DiscoveryDiagnostics:
    """Compute deterministic, aggregate-only discovery diagnostics.

    ``sample_limit`` bounds the analysis window (jobs are visited in stable id
    order). Every classifier used here is pure and deterministic.
    """
    total = db.stats_counts(conn).get("total_jobs", 0)
    jobs = db.jobs_for_analysis(conn, limit=sample_limit)

    by_track: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_region: dict[str, int] = {}
    by_seniority: dict[str, int] = {}

    for job in jobs:
        track = classify_track(job.title, job.description)
        track_id = track.track_id if track is not None else unknown_track_label
        by_track[track_id] = by_track.get(track_id, 0) + 1

        parse = parse_location(job.location, remote_mode=job.remote_mode)
        tier = parse.tier.value
        by_tier[tier] = by_tier.get(tier, 0) + 1
        region = parse.region
        by_region[region] = by_region.get(region, 0) + 1

        attrs = extract_job_attributes(job)
        band = normalize_seniority(attrs.seniority).value
        by_seniority[band] = by_seniority.get(band, 0) + 1

    by_canonical_source: dict[str, int] = {}
    for row in db.source_job_counts(conn, limit=100):
        by_canonical_source[row["source_id"]] = int(row["jobs_count"])

    return DiscoveryDiagnostics(
        jobs_sampled=len(jobs),
        jobs_total=int(total),
        jobs_by_career_track=by_track,
        jobs_by_location_tier=by_tier,
        jobs_by_region=by_region,
        jobs_by_seniority=by_seniority,
        jobs_by_canonical_source=by_canonical_source,
        records_by_discovery_method=db.discovery_method_counts(conn),
        canonical_url_stats=db.canonical_url_stats(conn),
    )


class PerSourceYield(BaseModel):
    """Merged queried-vs-yielded view for one source in a discovery run."""

    source_id: str
    planned_queries: int
    attempted_queries: int
    successful_queries: int
    rate_limited_queries: int
    failed_queries: int
    paused_skipped_queries: int
    hits_returned: int
    duplicate_on_page: int
    duplicate_at_url: int
    candidate_pages: int
    jobs_parsed: int
    jobs_persisted: int
    duplicate_at_db: int
    previous_runs: int
    zero_yield: bool


class DiscoveryYieldReport(BaseModel):
    """Aggregate-only per-source yield + five-level dedup accounting for a run.

    Levels (M2 mapping): ``duplicate_at_url`` — distinct queries/sources in the
    same run collapsing to one hit URL; ``duplicate_on_page`` — the same posting
    URL repeated within a single query's results page; ``duplicate_at_db`` —
    canonical-key matches at ingest against rows first seen earlier in THIS run;
    ``duplicate_workspace`` — fragmented same-key rows merged by lifecycle
    dedup; ``previous_runs`` — ingest matches against rows that predate the run
    (re-discovery). Content-free: identifiers and counts only.

    ``providers`` carries aggregate-only per-provider run results (M3) and
    ``provider_totals`` folds their counters, including how many provider
    candidates actually persisted (attributed from the ingest result).
    """

    per_source: list[PerSourceYield] = field(default_factory=list)
    zero_yield_sources: list[str] = field(default_factory=list)
    never_queried_sources: list[str] = field(default_factory=list)
    catalog_unknown_jobs: int = 0
    totals: dict[str, int] = field(default_factory=dict)
    dedup_workspace: dict[str, int] = field(default_factory=dict)
    providers: list[dict[str, object]] = field(default_factory=list)
    provider_totals: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def discovery_yield_report(
    *,
    pacing_report,
    scan_result,
    lifecycle: dict[str, int],
    qualifying_sources: set[str],
) -> DiscoveryYieldReport:
    """Compose a per-source queried-vs-yielded report with five-level dedup.

    ``qualifying_sources`` = catalog source ids that were *eligible* for the run
    (enabled + passing plan filters); sources eligible but never scheduled are
    reported in ``never_queried_sources`` (they answer "which sources never got a
    query?"). ``scan_result`` carries ingest-level attribution (persisted /
    duplicate_at_db / previous_runs per provenance source).
    """
    by_source: dict[str, dict[str, int]] = {}
    for s in pacing_report.per_source:
        row = {
            "planned_queries": s.planned_queries,
            "attempted_queries": s.attempted_queries,
            "successful_queries": s.successful_queries,
            "rate_limited_queries": s.rate_limited_queries,
            "failed_queries": s.failed_queries,
            "paused_skipped_queries": s.paused_skipped_queries,
            "hits_returned": s.hits_returned,
            "duplicate_on_page": s.duplicate_on_page,
            "duplicate_at_url": s.duplicate_at_url,
            "candidate_pages": s.candidate_pages,
            "jobs_parsed": s.jobs_parsed,
            "jobs_persisted": 0,
            "duplicate_at_db": 0,
            "previous_runs": 0,
        }
        by_source[s.source_id] = row
    for src, count in scan_result.persisted_by_source.items():
        row = by_source.setdefault(src, _empty_yield_row())
        row["jobs_persisted"] += count
    for src, count in scan_result.duplicate_at_db_by_source.items():
        row = by_source.setdefault(src, _empty_yield_row())
        row["duplicate_at_db"] += count
    for src, count in scan_result.previous_runs_by_source.items():
        row = by_source.setdefault(src, _empty_yield_row())
        row["previous_runs"] += count

    per_source: list[PerSourceYield] = []
    zero_yield: list[str] = []
    for source_id, row in by_source.items():
        is_zero = row["attempted_queries"] > 0 and row["jobs_parsed"] == 0
        if is_zero:
            zero_yield.append(source_id)
        per_source.append(
            PerSourceYield(
                source_id=source_id,
                **row,
                zero_yield=is_zero,
            )
        )
    per_source.sort(key=lambda p: p.source_id)

    planned_ids = {p.source_id for p in pacing_report.per_source}
    never_queried = sorted(id_ for id_ in qualifying_sources if id_ not in planned_ids)

    totals = {
        "sources_queried": len([p for p in per_source if p.attempted_queries > 0]),
        "sources_zero_yield": len(zero_yield),
        "sources_never_queried": len(never_queried),
        "hits_returned": sum(p.hits_returned for p in per_source),
        "duplicate_on_page": sum(p.duplicate_on_page for p in per_source),
        "duplicate_at_url": sum(p.duplicate_at_url for p in per_source),
        "candidate_pages": sum(p.candidate_pages for p in per_source),
        "jobs_parsed": sum(p.jobs_parsed for p in per_source),
        "jobs_persisted": sum(p.jobs_persisted for p in per_source),
        "duplicate_at_db": sum(p.duplicate_at_db for p in per_source),
        "duplicate_workspace_groups": lifecycle.get("dedup_groups", 0),
        "duplicate_workspace_merged": lifecycle.get("dedup_merged", 0),
        "previous_runs": sum(p.previous_runs for p in per_source),
        "catalog_unknown_jobs": pacing_report.catalog_unknown_jobs,
    }

    providers = [dict(p) for p in getattr(pacing_report, "providers", ())]
    provider_source_ids = {str(p.get("source_id")) for p in providers}
    provider_totals: dict[str, object] = {
        "sources": len(providers),
        "ok": sum(1 for p in providers if p.get("status") == "ok"),
        "zero_yield": sum(1 for p in providers if p.get("status") == "zero_yield"),
        "failed": sum(1 for p in providers if p.get("status") == "failed"),
        "requests": sum(int(p.get("requests") or 0) for p in providers),
        "hits": sum(int(p.get("hits") or 0) for p in providers),
        "candidates": sum(int(p.get("candidate_jobs") or 0) for p in providers),
        "jobs_persisted": sum(
            row["jobs_persisted"] for source_id, row in by_source.items() if source_id in provider_source_ids
        ),
    }

    return DiscoveryYieldReport(
        per_source=per_source,
        zero_yield_sources=zero_yield,
        never_queried_sources=never_queried,
        catalog_unknown_jobs=pacing_report.catalog_unknown_jobs,
        totals=totals,
        dedup_workspace={
            "groups": lifecycle.get("dedup_groups", 0),
            "merged": lifecycle.get("dedup_merged", 0),
        },
        providers=providers,
        provider_totals=provider_totals,
    )


def _empty_yield_row() -> dict[str, int]:
    return {
        "planned_queries": 0,
        "attempted_queries": 0,
        "successful_queries": 0,
        "rate_limited_queries": 0,
        "failed_queries": 0,
        "paused_skipped_queries": 0,
        "hits_returned": 0,
        "duplicate_on_page": 0,
        "duplicate_at_url": 0,
        "candidate_pages": 0,
        "jobs_parsed": 0,
        "jobs_persisted": 0,
        "duplicate_at_db": 0,
        "previous_runs": 0,
    }


__all__ = [
    "DiscoveryDiagnostics",
    "DiscoveryYieldReport",
    "PerSourceYield",
    "discovery_diagnostics",
    "discovery_yield_report",
]
