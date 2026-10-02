#!/usr/bin/env python3
"""Stage A Discovery - Small validation run to measure yield."""

import os
import sys
from pathlib import Path

# Set up environment for job_agent
os.environ.setdefault("JOB_AGENT_DATABASE_PATH", "output/jobs.sqlite3")
# Ollama runs on the host while these scripts run inside the container, so the
# container reaches it via the host gateway. Outside Docker, plain loopback is
# correct. Both are overridable with JOB_AGENT_LLM_BASE_URL.
os.environ.setdefault(
    "JOB_AGENT_LLM_BASE_URL",
    "http://host.docker.internal:11434" if Path("/.dockerenv").exists() else "http://127.0.0.1:11434",
)
os.environ.setdefault("JOB_AGENT_LLM_MODEL", "qwen3.5:9b")

# Add job_agent to path
sys.path.insert(0, str(__file__))

import time
from datetime import UTC, datetime

from job_agent import db as job_db
from job_agent.config import load_config
from job_agent.discovery_search import run_planned_discovery
from job_agent.pipeline import ingest_global_jobs, run_lifecycle
from job_agent.scoring import scoring_policy_from_config


def main():
    print("=== STAGE A DISCOVERY ===")
    print("Starting small validation run (25 queries max)...\n")

    # Load config and override for Stage A
    cfg = load_config()
    cfg.career.discovery.max_queries_total = 25
    cfg.career.discovery.max_queries_per_track = 5
    cfg.career.discovery.max_queries_per_source = 2
    cfg.career.discovery.max_sources_per_track = 4
    cfg.career.pacing.interval_by_class = {"low": 1.0, "medium": 2.0, "high": 4.0}

    print(f"Config: max_queries_total={cfg.career.discovery.max_queries_total}")
    print(f"Config: max_queries_per_track={cfg.career.discovery.max_queries_per_track}")
    print(f"Config: max_queries_per_source={cfg.career.discovery.max_queries_per_source}")
    print(f"Config: max_sources_per_track={cfg.career.discovery.max_sources_per_track}")

    # Load profile
    from pathlib import Path

    import yaml

    profile_path = Path(cfg.profile_path)
    if not profile_path.is_absolute():
        profile_path = Path(__file__).parent / cfg.profile_path
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    policy = scoring_policy_from_config(cfg.model_dump())

    # Run discovery
    run_started_at = datetime.now(UTC).isoformat(timespec="seconds")
    start = time.monotonic()

    plan, jobs, provenance, errors, pacing_report = run_planned_discovery(
        cfg,
        max_pages=cfg.career.discovery.max_results_per_query * 10,
        max_results=cfg.career.discovery.max_results_per_query,
        limit_total=cfg.career.discovery.max_queries_total,
        limit_per_track=cfg.career.discovery.max_queries_per_track,
    )

    print(f"\nPlanned queries: {len(plan.queries())}")
    print(f"Candidates found: {len(jobs)}")
    print(f"Provenance entries: {len(provenance)}")
    print(f"Fetch errors: {len(errors)}")

    # Ingest jobs
    db_path = cfg.database_path
    conn = job_db.connect(db_path)

    result = ingest_global_jobs(
        conn,
        jobs,
        profile,
        policy,
        provenance=provenance,
        run_started_at=run_started_at,
    )

    run_lifecycle(conn, cfg, result.jobs_seen)

    # Record provider runs
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
        provider_failures=len([p for p in pacing_report.providers if p["status"] == "failed"]),
        fetch_errors=len(errors),
        duration_ms=int((time.monotonic() - start) * 1000),
    )

    conn.commit()

    # Print summary
    print("\n=== STAGE A RESULTS ===")
    print(f"Planned queries: {len(plan.queries())}")
    print(f"Candidates found: {len(jobs)}")
    print(f"Jobs persisted: {jobs_persisted}")
    print(f"  From providers: {jobs_from_providers}")
    print(f"  From search: {jobs_from_search}")
    print(f"Duplicates: {result.total_duplicates}")
    print(f"Fetch errors: {len(errors)}")
    print(f"Duration: {int((time.monotonic() - start) * 1000)}ms")
    print("\nPacing Totals:")
    for k, v in pacing_report.totals.items():
        print(f"  {k}: {v}")

    print("\nPer-Source Pacing:")
    for src in pacing_report.per_source:
        print(
            f"  {src.source_id}: planned={src.planned_queries}, attempted={src.attempted_queries}, "
            f"successful={src.successful_queries}, rate_limited={src.rate_limited_queries}, "
            f"failed={src.failed_queries}, paused={src.paused_skipped_queries}, "
            f"hits={src.hits_returned}, pages={src.candidate_pages}, parsed={src.jobs_parsed}"
        )

    # Query effectiveness
    print("\nQuery Plan Summary:")
    summary = plan.summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # Query-by-query breakdown
    print("\nQuery Audit (first 10):")
    for item in plan.audit_items()[:10]:
        print(
            f"  {item['query']} | track={item['track_id']} | source={item['source_id']} | loc={item['location_term']} | reason={item['reason']}"
        )

    conn.close()

    # Run fit analysis on new jobs
    # print("\n=== Running fit analysis... ===")
    # from job_agent.cli import run_fit
    # run_fit(db_path, limit=50)


if __name__ == "__main__":
    main()
