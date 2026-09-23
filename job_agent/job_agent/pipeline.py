"""Scan pipeline.

Orchestrates fetch → normalize → dedup → score → persist for a list of
sources, with per-source isolation, structured logging, progress callbacks,
and a scan-run audit row per source.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import db
from .config import Config
from .logging_setup import get_logger, log_event
from .models import Job, JobMatch, Score, SourceStats
from .normalizer import normalize_job
from .scoring import ScoringPolicy
from .sources import Source

log = get_logger("pipeline")

ProgressCallback = Callable[[str, int, int, float], None]
""" (source_name, jobs_fetched, total_so_far, elapsed_seconds) """


@dataclass(frozen=True)
class Provenance:
    """Content-free attribution of a job to a discovery source."""

    source_id: str
    source_name: str | None = None
    source_url: str | None = None
    discovery_method: str | None = None
    query: str | None = None


def _record_provenance(
    conn: sqlite3.Connection, job_id: str, provenance: Provenance, canonical_url: str | None = None
) -> None:
    db.record_job_source(
        conn,
        job_id,
        source_id=provenance.source_id,
        source_name=provenance.source_name,
        source_url=provenance.source_url,
        discovery_method=provenance.discovery_method,
        query=provenance.query,
        canonical_url=canonical_url,
    )


def _source_provenance(src: Source) -> Provenance:
    return Provenance(
        source_id=src.name,
        source_name=src.name,
        source_url=getattr(src, "base_url", None),
        discovery_method=src.kind.value,
    )


@dataclass
class ScanResult:
    matches: list[JobMatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stats: list[SourceStats] = field(default_factory=list)
    jobs_seen: set[str] = field(default_factory=set)
    total_fetched: int = 0
    total_duplicates: int = 0
    # M2 ingest-level dedup attribution (per provenance source_id).
    # persisted_by_source: first-seen-in this-run rows; duplicate_at_db:
    # canonical_key matches against rows created earlier in THIS run;
    # previous_runs: matches against rows that predate the run (re-discovery).
    persisted_by_source: dict[str, int] = field(default_factory=dict)
    duplicate_at_db_by_source: dict[str, int] = field(default_factory=dict)
    previous_runs_by_source: dict[str, int] = field(default_factory=dict)


def _profile_version(profile: dict) -> str:
    """Deterministic snapshot of the profile dict (for evaluation explainability)."""
    import json as _json

    blob = _json.dumps(profile, sort_keys=True, ensure_ascii=False)
    import hashlib

    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def run_sources(
    conn: sqlite3.Connection,
    sources: Iterable[Source],
    profile: dict,
    policy: ScoringPolicy,
    run_id: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> ScanResult:
    result = ScanResult()
    profile_version = _profile_version(profile)
    total_so_far = 0

    for src in sources:
        start = time.monotonic()
        stats = SourceStats(source=src.name)
        try:
            raw_jobs = src.fetch()
            stats.fetched = len(raw_jobs)
            result.total_fetched += len(raw_jobs)
            log_event(log, "source_fetched", source=src.name, count=len(raw_jobs))
        except Exception as exc:  # noqa: BLE001 - source isolation
            stats.errors.append(str(exc))
            result.errors.append(f"{src.name}: {exc}")
            log_event(log, "source_failed", source=src.name, error=str(exc)[:200])
        else:
            for job in raw_jobs:
                norm = normalize_job(job, source_type=src.kind.value)
                if run_id:
                    norm.run_id = run_id
                existing = db.find_duplicate_of(conn, norm)
                if existing is not None:
                    result.total_duplicates += 1
                    stats.duplicates += 1
                    # Keep canonical row's staleness fresh even when this scan
                    # only surfaced the duplicate URL.
                    db.upsert_job(conn, norm)  # refreshes last_seen
                    _record_provenance(conn, existing.id, _source_provenance(src), norm.canonical_url)
                    continue
                sc = score_job(norm, profile, policy)
                db.evaluate_and_store(conn, norm, sc, policy.version, profile_version)
                _record_provenance(conn, norm.id, _source_provenance(src), norm.canonical_url)
                result.jobs_seen.add(norm.id)
                total_so_far += 1
                if sc.decision != "reject":
                    result.matches.append(JobMatch(job=norm, score=sc))
            stats.parsed = stats.fetched - stats.duplicates

        elapsed = time.monotonic() - start
        db.record_scan_run(
            conn,
            source=src.name,
            status="completed" if not stats.errors else "failed",
            candidate_count=stats.fetched,
            fetched_count=stats.fetched,
            parsed_count=stats.parsed,
            errors=stats.errors,
            duration_ms=int(elapsed * 1000),
        )
        result.stats.append(stats)
        if on_progress:
            on_progress(src.name, stats.fetched, total_so_far, elapsed)

    conn.commit()
    return result


def score_job(job: Job, profile: dict, policy: ScoringPolicy) -> Score:
    from .scoring import score

    return score(job, profile, policy)


def ingest_global_jobs(
    conn: sqlite3.Connection,
    jobs: list[Job],
    profile: dict,
    policy: ScoringPolicy,
    *,
    provenance: list[Provenance] | None = None,
    run_started_at: str | None = None,
    run_id: str | None = None,
) -> ScanResult:
    """Persist jobs discovered by the global search layer with the same
    normalize/dedup/evaluate path, but without per-source scan-run rows.

    ``provenance`` mirrors ``jobs`` 1:1 and records which catalog source /
    discovery query surfaced each posting (content-free).

    ``run_started_at`` (ISO) is the run boundary used to split ``total_duplicates``
    into ``duplicate_at_db`` (canonical-key cells first seen earlier in THIS run)
    versus ``previous_runs`` (re-discovery of a row persisted before the run).
    When omitted, every duplicate is attributed to ``duplicate_at_db``.
    """
    result = ScanResult()
    profile_version = _profile_version(profile)
    for index, job in enumerate(jobs):
        norm = normalize_job(job, source_type="structured_page")
        if run_id:
            norm.run_id = run_id
        source_id = provenance[index].source_id if provenance and index < len(provenance) else None
        existing = db.find_duplicate_of(conn, norm)
        if existing is not None:
            result.total_duplicates += 1
            if run_started_at and existing.discovered_at and existing.discovered_at < run_started_at:
                result.previous_runs_by_source[source_id or "unknown"] = (
                    result.previous_runs_by_source.get(source_id or "unknown", 0) + 1
                )
            else:
                result.duplicate_at_db_by_source[source_id or "unknown"] = (
                    result.duplicate_at_db_by_source.get(source_id or "unknown", 0) + 1
                )
            db.upsert_job(conn, norm)
            if provenance and index < len(provenance):
                _record_provenance(conn, existing.id, provenance[index], norm.canonical_url)
            continue
        sc = score_job(norm, profile, policy)
        db.evaluate_and_store(conn, norm, sc, policy.version, profile_version)
        if provenance and index < len(provenance):
            _record_provenance(conn, norm.id, provenance[index], norm.canonical_url)
        result.jobs_seen.add(norm.id)
        result.persisted_by_source[source_id or "unknown"] = (
            result.persisted_by_source.get(source_id or "unknown", 0) + 1
        )
        if sc.decision != "reject":
            result.matches.append(JobMatch(job=norm, score=sc))
    conn.commit()
    return result


def run_lifecycle(conn: sqlite3.Connection, config: Config, seen_ids: set[str]) -> dict[str, int]:
    """Apply staleness/closure rules and dedup, then commit.

    Returns lifecycle counters including the workspace-level dedup merge
    counts (``dedup_groups`` / ``dedup_merged``) so operators can see how many
    fragmented same-key rows were folded under ``duplicate_workspace``.
    """
    lifecycle = db.apply_lifecycle(
        conn,
        seen_ids,
        freshness_days=config.jobs.freshness_days,
        close_after_missing_scans=config.jobs.close_after_missing_scans,
    )
    dedup = db.deduplicate(conn)
    conn.commit()
    log_event(log, "lifecycle_applied", **{**lifecycle})
    log_event(log, "dedup_applied", groups=dedup["groups"], merged=dedup["merged"])
    return {**lifecycle, "dedup_groups": dedup["groups"], "dedup_merged": dedup["merged"]}


__all__ = ["Provenance", "ScanResult", "run_sources", "run_lifecycle", "ingest_global_jobs", "score_job"]
