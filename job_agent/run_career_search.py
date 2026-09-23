"""Run complete career search workflow."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from job_agent import db
from job_agent.config import load_config
from job_agent.discovery import build_sources
from job_agent.scoring import scoring_policy_from_config
from job_agent.pipeline import run_sources
from job_agent.career.report_generator import CareerSearchReport
from job_agent.career.profile import derive_career_profile
import yaml


def main():
    print("=== Personal AI Career Search System ===")
    print()
    
    # Load configuration
    config = load_config("config.yaml")
    print(f"✓ Configuration loaded")
    
    # Load career profile
    with open("profile/profile.yaml", "r") as f:
        profile_data = yaml.safe_load(f)
    career_profile = derive_career_profile(profile_data)
    print(f"✓ Career profile loaded: {career_profile.name}")
    print(f"  Target roles: {career_profile.target_role_families}")
    
    # Setup database
    db_path = "career_search.db"
    conn = db.connect(db_path)
    print(f"✓ Database connected")
    
    # Build sources
    sources = build_sources(config)
    print(f"✓ {len(sources)} sources configured")
    
    # Run crawl
    run_id = uuid.uuid4().hex
    print(f"\n=== Running crawl (run_id: {run_id[:8]}...) ===")
    policy = scoring_policy_from_config(config.model_dump())
    
    total_fetched = 0
    total_persisted = 0
    
    for src in sources[:3]:  # Limit to first 3 for demo
        print(f"Scanning {src.name}...", end=" ")
        try:
            result = run_sources(conn, [src], {}, policy, run_id=run_id)
            total_fetched += result.total_fetched
            total_persisted += max(0, result.total_fetched - result.total_duplicates)
            print(f"{result.total_fetched} candidates, {max(0, result.total_fetched - result.total_duplicates)} new")
        except Exception as e:
            print(f"Error: {e}")
    
    print(f"\nTotal: {total_fetched} candidates, {total_persisted} new jobs")
    
    # Generate report
    print("\n=== Generating report ===")
    report_gen = CareerSearchReport(conn, output_dir="output/reports")
    report = report_gen.generate_report(run_id)
    
    output_path = report_gen.write_report(report)
    print(f"✓ Report written to: {output_path}")
    
    # Print summary
    print("\n=== Summary ===")
    print(f"Jobs in corpus: {report['corpus_stats']['total_jobs']}")
    print(f"Top role families: {list(report['corpus_stats']['by_role_family'].keys())[:5]}")
    
    # Save run metadata
    meta_path = Path("output/runs") / f"run_{run_id}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    meta_path.write_text(json.dumps({
        "run_id": run_id,
        "total_fetched": total_fetched,
        "total_persisted": total_persisted,
        "sources_count": len(sources),
        "timestamp": uuid.uuid1().int,
    }, indent=2))
    
    print("\n✓ Career search complete")
    print(f"Run ID: {run_id}")
    print(f"Output: output/")


if __name__ == "__main__":
    main()
