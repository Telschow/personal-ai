"""Generate sample career search report for demonstration."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

from job_agent import db
from job_agent.models import Job
from job_agent.career.report_generator import CareerSearchReport


def create_sample_report():
    """Create sample report with mock data."""
    print("Generating sample career search report...")
    
    # Create in-memory database with sample jobs
    conn = db.connect(':memory:')
    
    # Sample jobs
    sample_jobs = [
        {
            "id": "job1",
            "title": "Senior Technical Product Manager",
            "company": "BMW Group",
            "url": "https://example.com/job1",
            "source": "greenhouse",
            "location": "Munich, Germany",
            "role_family": "product_management",
            "role_classification_confidence": 0.95,
            "location_city": "Munich",
            "location_country": "Germany",
            "location_scope": "munich",
            "location_score": 1.0,
            "salary_min_eur": 110000,
            "salary_max_eur": 140000,
            "compensation_status": "disclosed",
        },
        {
            "id": "job2",
            "title": "Product Manager AI",
            "company": "Conti",
            "url": "https://example.com/job2",
            "source": "ashby",
            "location": "Munich, Germany",
            "role_family": "product_management",
            "role_classification_confidence": 0.92,
            "location_city": "Munich",
            "location_country": "Germany",
            "location_scope": "munich",
            "location_score": 1.0,
            "salary_min_eur": 100000,
            "salary_max_eur": 130000,
            "compensation_status": "disclosed",
        },
        {
            "id": "job3",
            "title": "Solutions Architect",
            "company": "TechCorp",
            "url": "https://example.com/job3",
            "source": "smartrecruiters",
            "location": "Remote",
            "role_family": "solutions_architecture",
            "role_classification_confidence": 0.88,
            "location_scope": "remote_eu",
            "location_score": 0.3,
            "salary_min_eur": 120000,
            "salary_max_eur": 150000,
            "compensation_status": "estimated",
        },
        {
            "id": "job4",
            "title": "Head of Product - Autonomous Systems",
            "company": "InnovateAI",
            "url": "https://example.com/job4",
            "source": "greenhouse",
            "location": "Berlin, Germany",
            "role_family": "product_leadership",
            "role_classification_confidence": 0.94,
            "location_city": "Berlin",
            "location_country": "Germany",
            "location_scope": "germany",
            "location_score": 0.7,
            "salary_min_eur": 130000,
            "salary_max_eur": 170000,
            "compensation_status": "disclosed",
        },
        {
            "id": "job5",
            "title": "Program Manager - Robotics",
            "company": "AutoTech",
            "url": "https://example.com/job5",
            "source": "lever",
            "location": "Remote, Worldwide",
            "role_family": "program_leadership",
            "role_classification_confidence": 0.85,
            "location_scope": "international",
            "location_score": 0.2,
            "salary_min_eur": None,
            "salary_max_eur": None,
            "compensation_status": "not_disclosed",
        },
    ]
    
    # Insert sample jobs
    for job_data in sample_jobs:
        job = Job.model_validate({
            **job_data,
            "apply_url": None,
            "source_type": "ats_board",
            "canonical_url": job_data["url"],
            "country": "Germany",
            "normalized_location": job_data["location"],
            "remote_mode": "onsite" if job_data["location_scope"] == "munich" else "remote",
            "employment_type": "full-time",
            "date_posted": None,
            "description": f"Sample {job_data['title']} position",
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_period": None,
            "salary_source": None,
            "salary_confidence": 0.0,
            "salary_converted": False,
            "canonical_key": f"key_{job_data['id']}",
            "status": "active",
            "run_id": "sample_run",
            "discovery_source": "test",
            "company_radar_id": None,
            "provider_native_id": None,
            "role_classification_reason": "sample classification",
            "location_reason": "sample location",
        })
        
        # Insert into db
        from job_agent.db import upsert_job
        upsert_job(conn, job)
    
    print(f"✓ Inserted {len(sample_jobs)} sample jobs")
    
    # Generate report
    report_gen = CareerSearchReport(conn, output_dir="output/reports")
    report = report_gen.generate_report("sample_run")
    
    output_path = report_gen.write_report(report)
    print(f"✓ Report written to {output_path}")
    
    # Print summary
    print("\n=== Report Summary ===")
    print(f"Total jobs: {report['corpus_stats']['total_jobs']}")
    print(f"Role families: {report['corpus_stats']['by_role_family']}")
    print(f"Location scopes: {report['corpus_stats']['by_location_scope']}")
    print(f"Salary disclosure rate: {report['corpus_stats']['salary_disclosure_rate']:.1%}")
    
    print("\n=== Top 5 Jobs ===")
    for i, job in enumerate(report['top_shortlists']['top_20'][:5], 1):
        print(f"{i}. {job['title']} at {job['company']}")
        print(f"   Location: {job['location']} | Salary: {job['salary']}")
    
    return output_path


if __name__ == "__main__":
    create_sample_report()
