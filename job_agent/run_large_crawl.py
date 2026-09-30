#!/usr/bin/env python3
"""Large-scale job discovery crawl with staged execution."""

import os
import sys
from pathlib import Path

# Set up environment for job_agent
os.environ.setdefault("JOB_AGENT_DATABASE_PATH", "output/jobs.sqlite3")
os.environ.setdefault("JOB_AGENT_LLM_BASE_URL", "http://host.docker.internal:11434")
os.environ.setdefault("JOB_AGENT_LLM_MODEL", "qwen3.5:9b")

# Add job_agent to path
sys.path.insert(0, str(Path(__file__).parent))

import time
from datetime import UTC, datetime

import yaml

from job_agent import db as job_db
from job_agent.config import load_config
from job_agent.discovery_search import run_planned_discovery
from job_agent.pipeline import ingest_global_jobs, run_lifecycle
from job_agent.scoring import scoring_policy_from_config


def main():
    print("=== STAGE B - LARGE-SCALE CRAWL ===")
    print("Running discovery to target >1,000 unique postings...\n")

    # Load config
    cfg = load_config()

    # Stage B configuration
    print("Stage B Configuration:")
    print(f"  max_queries_total: {cfg.career.discovery.max_queries_total}")
    print(f"  max_queries_per_track: {cfg.career.discovery.max_queries_per_track}")
    print(f"  max_queries_per_source: {cfg.career.discovery.max_queries_per_source}")
    print(f"  max_sources_per_track: {cfg.career.discovery.max_sources_per_track}")
    print(f"  max_results_per_query: {cfg.career.discovery.max_results_per_query}")
    print(f"  max_global_pages: {cfg.search.max_global_pages}")

    # Load profile and policy
    profile_path = cfg.profile_path
    if not os.path.isabs(profile_path):
        profile_path = os.path.join(os.path.dirname(__file__), profile_path)

    with open(profile_path) as f:
        profile = yaml.safe_load(f)
    policy = scoring_policy_from_config(cfg.model_dump())

    print(f"\nProfile loaded from: {profile_path}")
    print(f"Scoring policy version: {policy.version}")

    # Run discovery
    run_started_at = datetime.now(UTC).isoformat(timespec="seconds")
    start = time.monotonic()

    print(f"\nStarting discovery at {run_started_at}")
    print("This may take several minutes depending on rate limits and network performance...")

    plan, jobs, provenance, errors, pacing_report = run_planned_discovery(
        cfg,
        max_pages=cfg.search.max_global_pages,
        max_results=cfg.career.discovery.max_results_per_query,
        limit_total=cfg.career.discovery.max_queries_total,
        limit_per_track=cfg.career.discovery.max_queries_per_track,
        limit_per_source=cfg.career.discovery.max_queries_per_source,
        limit_sources=cfg.career.discovery.max_sources_per_track,
    )

    elapsed = time.monotonic() - start

    print("\n=== DISCOVERY PHASE COMPLETED ===")
    print(f"Planned queries: {len(plan.queries())}")
    print(f"Candidates found: {len(jobs)}")
    print(f"Provenance entries: {len(provenance)}")
    print(f"Fetch errors: {len(errors)}")
    print(f"Duration: {elapsed:.1f}s ({elapsed / 60:.1f} minutes)")

    print("\nQuery Plan Summary:")
    summary = plan.summary()
    for k, v in summary.items():
        if k != "budgets":
            print(f"  {k}: {v}")

    print("\nPer-Source Pacing:")
    for src in pacing_report.per_source:
        status_icon = {"ok": "✓", "failed": "✗", "zero_yield": "⚠"}.get(src.status, "?")
        print(
            f"  {status_icon} {src.source_id}: {src.planned_queries} planned, "
            f"{src.attempted_queries} attempted, {src.successful_queries} successful, "
            f"{src.failed_queries} failed, {src.rate_limited_queries} rate-limited, "
            f"{src.hits_returned} hits, {src.candidate_pages} pages, {src.jobs_parsed} jobs"
        )

    # Prepare database connection
    db_path = cfg.database_path
    conn = job_db.connect(db_path)

    print(f"\nIngesting {len(jobs)} jobs into database...")
    ingest_start = time.monotonic()

    result = ingest_global_jobs(
        conn,
        jobs,
        profile,
        policy,
        provenance=provenance,
        run_started_at=run_started_at,
    )

    ingest_elapsed = time.monotonic() - ingest_start
    print(f"Ingestion completed in {ingest_elapsed:.1f}s")

    # Run lifecycle processing
    print("\nApplying lifecycle rules...")
    lifecycle_start = time.monotonic()
    run_lifecycle(conn, cfg, result.jobs_seen)
    lifecycle_elapsed = time.monotonic() - lifecycle_start
    print(f"Lifecycle processing completed in {lifecycle_elapsed:.1f}s")

    # Record provider runs and discovery run
    for pstat in pacing_report.providers:
        job_db.record_provider_run(
            conn,
            source_id=pstat["source_id"],
            provider=pstat["provider"],
            status=pstat["status"],
            requests=pstat["requests"],
            hits=pstat["hits"],
            candidate_jobs=pstat["candidate_jobs"],
            duplicates=pstat["duplicates"],
            errors=list(pstat["errors"]),
            latency_ms=pstat["latency_ms"],
        )

    jobs_persisted = sum(result.persisted_by_source.values())
    jobs_from_providers = sum(
        result.persisted_by_source.get(str(p.get("source_id")), 0) for p in pacing_report.providers
    )
    jobs_from_search = jobs_persisted - jobs_from_providers

    job_db.record_discovery_run(
        conn,
        planned_queries=len(plan.queries()),
        candidates_found=len(jobs),
        jobs_persisted=jobs_persisted,
        jobs_from_providers=jobs_from_providers,
        jobs_from_search=jobs_from_search,
        provider_failures=len([p for p in pacing_report.providers if p.get("status") == "failed"]),
        fetch_errors=len(errors),
        duration_ms=int((time.monotonic() - start) * 1000),
    )

    conn.commit()

    # Print summary
    print("\n=== FINAL SUMMARY ===")
    total_jobs = conn.execute("SELECT COUNT(*) as total FROM jobs").fetchone()[0]
    total_evaluations = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    high_fit_count = conn.execute("SELECT COUNT(*) FROM evaluations WHERE total >= 75").fetchone()[0]

    print(f"Total jobs in database: {total_jobs}")
    print(f"Jobs with evaluations: {total_evaluations}")
    print(f"High-fit jobs (≥75): {high_fit_count}")
    print(f"Duration: {int((time.monotonic() - start) * 1000)}ms ({int((time.monotonic() - start) / 60)} minutes)")

    print("\nPer-Source Yields:")
    for p in pacing_report.providers:
        if p["candidate_jobs"] > 0:
            yield_pct = p["hits"] / p["candidate_jobs"] * 100 if p["candidate_jobs"] > 0 else 0
            print(f"  {p['source_id']}: {p['candidate_jobs']} candidates -> {p['hits']} hits ({yield_pct:.0f}%)")

    print("\n=== ANALYSIS ===")
    print(
        f"Search efficiency: {jobs_persisted}/{len(plan.queries())} = {jobs_persisted / len(plan.queries()):.1f} jobs per query"
    )
    print(f"Provider sources: {jobs_from_providers}, Search engines: {jobs_from_search}")

    # Check if we met the target
    if total_jobs >= 1000:
        print(f"\n✓ TARGET MET: {total_jobs} jobs persisted (exceeds 1,000)")
    else:
        print(f"\n⚠️  BELOW TARGET: {total_jobs} jobs persisted (short of 1,000)")

    recent_jobs = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM jobs WHERE date_posted >= date('now', '-30 days') AND date_posted IS NOT NULL"
    ).fetchone()[0]
    print(f"Recent high-fit jobs (30 days, ≥75): {high_fit_count} (context: {recent_jobs} total recent)")

    conn.close()

    # Save a copy of the config for this run
    output_dir = Path("output/reports")
    output_dir.mkdir(exist_ok=True)

    from datetime import datetime as dt

    today = dt.now().strftime("%Y-%m-%d")

    # Create crawl summary
    summary_data = {
        "date": dt.now().isoformat(),
        "stage": "Stage B - Large Crawl",
        "planned_queries": len(plan.queries()),
        "candidates_found": len(jobs),
        "jobs_persisted": jobs_persisted,
        "jobs_from_providers": jobs_from_providers,
        "jobs_from_search": jobs_from_search,
        "duplicates": result.total_duplicates,
        "provider_failures": len([p for p in pacing_report.providers if p.get("status") == "failed"]),
        "fetch_errors": len(errors),
        "duration_ms": int((time.monotonic() - start) * 1000),
        "final_total_jobs": total_jobs,
        "final_total_evaluations": total_evaluations,
        "high_fit_jobs": high_fit_count,
        "recent_jobs": recent_jobs,
        "success_rate": jobs_persisted / len(plan.queries()) if len(plan.queries()) > 0 else 0,
    }

    import json

    with open(output_dir / f"crawl-summary-{today}.json", "w") as f:
        json.dump(summary_data, f, indent=2)

    print(f"\nSummary saved to: output/reports/crawl-summary-{today}.json")

    return total_jobs


if __name__ == "__main__":
    try:
        final_count = main()
        sys.exit(0 if final_count >= 1000 else 1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
