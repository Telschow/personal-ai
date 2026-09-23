"""Career search report generation."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .profile import derive_career_profile
from ..config import load_config
from ..models import Job
from ..normalizer import normalize_job


class CareerSearchReport:
    """Generate comprehensive career search reports."""

    def __init__(self, conn: sqlite3.Connection, output_dir: str = "output"):
        self.conn = conn
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self, run_id: str | None = None) -> dict[str, Any]:
        """Generate complete career search report."""
        jobs = self._fetch_jobs(run_id)
        
        report = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "corpus_stats": self._corpus_stats(jobs),
            "top_shortlists": self._top_shortlists(jobs),
            "shortlists_by_category": self._shortlists_by_category(jobs),
            "skill_analysis": self._skill_analysis(jobs),
            "company_clusters": self._company_clusters(jobs),
        }
        
        return report

    def _fetch_jobs(self, run_id: str | None) -> list[Job]:
        """Fetch jobs from database."""
        where_clause = f" WHERE run_id = '{run_id}'" if run_id else " WHERE status IN ('active', 'likely_active', 'unknown')"
        
        rows = self.conn.execute(
            f"SELECT * FROM jobs{where_clause} ORDER BY last_seen DESC LIMIT 1000"
        ).fetchall()
        
        jobs = []
        for row in rows:
            # Parse job from row
            job_data = {
                "id": row["id"],
                "title": row["title"],
                "company": row["company"],
                "url": row["url"],
                "apply_url": row["apply_url"],
                "source": row["source"],
                "source_type": row["source_type"],
                "canonical_url": row["canonical_url"],
                "location": row["location"],
                "country": row["country"],
                "normalized_location": row["normalized_location"],
                "remote_mode": row["remote_mode"] or "unknown",
                "employment_type": row["employment_type"],
                "date_posted": row["date_posted"],
                "description": row["description"] or "",
                "salary_min": row["salary_min"],
                "salary_max": row["salary_max"],
                "salary_currency": row["salary_currency"],
                "salary_period": row["salary_period"],
                "salary_source": row["salary_source"],
                "salary_confidence": row["salary_confidence"] or 0.0,
                "compensation_status": row["compensation_status"] or "unknown",
                "salary_min_eur": row["salary_min_eur"],
                "salary_max_eur": row["salary_max_eur"],
                "salary_converted": bool(row["salary_converted"]),
                "canonical_key": row["canonical_key"],
                "status": row["status"],
                "run_id": row["run_id"],
                "discovery_source": row["discovery_source"],
                "company_radar_id": row["company_radar_id"],
                "provider_native_id": row["provider_native_id"],
                "role_family": row["role_family"],
                "role_classification_confidence": row["role_classification_confidence"] or 0.0,
                "role_classification_reason": row["role_classification_reason"],
                "location_city": row["location_city"],
                "location_country": row["location_country"],
                "location_scope": row["location_scope"],
                "location_score": row["location_score"] or 0.0,
                "location_reason": row["location_reason"],
            }
            jobs.append(Job.model_validate(job_data))
        
        return jobs

    def _corpus_stats(self, jobs: list[Job]) -> dict[str, Any]:
        """Calculate corpus statistics."""
        total = len(jobs)
        by_role = {}
        by_location = {}
        by_source = {}
        disclosed_salary = 0
        
        for job in jobs:
            role = job.role_family or "unknown"
            by_role[role] = by_role.get(role, 0) + 1
            
            scope = job.location_scope or "unknown"
            by_location[scope] = by_location.get(scope, 0) + 1
            
            source = job.source or "unknown"
            by_source[source] = by_source.get(source, 0) + 1
            
            if job.compensation_status in ("disclosed", "estimated"):
                disclosed_salary += 1
        
        return {
            "total_jobs": total,
            "by_role_family": by_role,
            "by_location_scope": by_location,
            "by_source": by_source,
            "salary_disclosed": disclosed_salary,
            "salary_disclosure_rate": disclosed_salary / total if total > 0 else 0,
        }

    def _top_shortlists(self, jobs: list[Job]) -> dict[str, list[dict[str, Any]]]:
        """Generate top shortlists."""
        # Score jobs by role classification confidence and location fit
        scored = []
        for job in jobs:
            score = job.role_classification_confidence * 0.7 + job.location_score * 0.3
            scored.append((job, score))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        
        top_20 = [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location or "Unknown",
                "role_family": job.role_family or "unknown",
                "location_scope": job.location_scope,
                "location_score": job.location_score,
                "role_confidence": job.role_classification_confidence,
                "salary": f"{job.salary_min_eur or '?'}-{job.salary_max_eur or '?'}€" if job.salary_min_eur else "Not disclosed",
                "url": job.url,
                "source": job.source,
            }
            for job, _ in scored[:20]
        ]
        
        return {"top_20": top_20}

    def _shortlists_by_category(self, jobs: list[Job]) -> dict[str, list[dict[str, Any]]]:
        """Generate category-specific shortlists."""
        # Munich-first
        munich = [j for j in jobs if j.location_scope == "munich"]
        munich.sort(key=lambda j: (j.role_classification_confidence, j.location_score), reverse=True)
        
        # AI/autonomous systems
        ai_related = [j for j in jobs if j.role_archetypes and any("AI" in str(a) or "autonomous" in str(a).lower() for a in j.role_archetypes)]
        ai_related.sort(key=lambda j: j.role_classification_confidence, reverse=True)
        
        # High career acceleration
        career_accel = [j for j in jobs if j.role_family and "product" in j.role_family.lower()]
        career_accel.sort(key=lambda j: j.role_classification_confidence, reverse=True)
        
        return {
            "munich": self._format_jobs(munich[:20]),
            "ai_autonomous": self._format_jobs(ai_related[:20]),
            "career_acceleration": self._format_jobs(career_accel[:20]),
        }

    def _format_jobs(self, jobs: list[Job]) -> list[dict[str, Any]]:
        """Format jobs for display."""
        return [
            {
                "company": job.company,
                "title": job.title,
                "location": job.location or "Unknown",
                "role_family": job.role_family or "unknown",
                "location_scope": job.location_scope,
                "location_score": job.location_score,
                "role_confidence": job.role_classification_confidence,
                "url": job.url,
            }
            for job in jobs
        ]

    def _skill_analysis(self, jobs: list[Job]) -> dict[str, Any]:
        """Analyze skill requirements from job descriptions."""
        # Simplified analysis - in practice would parse job descriptions
        return {
            "most_common_roles": {},
            "skill_gaps_identified": []
        }

    def _company_clusters(self, jobs: list[Job]) -> dict[str, Any]:
        """Cluster jobs by company."""
        companies: dict[str, int] = {}
        for job in jobs:
            companies[job.company] = companies.get(job.company, 0) + 1
        
        sorted_companies = sorted(companies.items(), key=lambda x: x[1], reverse=True)
        return {
            "top_companies": [
                {"company": company, "count": count}
                for company, count in sorted_companies[:20]
            ]
        }

    def write_report(self, report: dict[str, Any]) -> str:
        """Write report to markdown file."""
        output_path = self.output_dir / f"career_report_{report['run_id'] or 'latest'}.md"
        
        lines = [
            "# Career Search Report",
            f"Generated: {report['generated_at']}",
            f"Run ID: {report['run_id'] or 'N/A'}",
            "",
            "## Executive Summary",
            "",
            f"Total jobs analyzed: {report['corpus_stats']['total_jobs']}",
            f"Salary disclosure rate: {report['corpus_stats']['salary_disclosure_rate']:.1%}",
            "",
            "## Corpus Overview",
            "",
            f"By role family: {report['corpus_stats']['by_role_family']}",
            f"By location: {report['corpus_stats']['by_location_scope']}",
            "",
            "## Top 20 Jobs",
            "",
        ]
        
        for i, job in enumerate(report['top_shortlists']['top_20'], 1):
            lines.append(f"{i}. **{job['title']}** at {job['company']}")
            lines.append(f"   Location: {job['location']} | Role: {job['role_family']}")
            lines.append(f"   Salary: {job['salary']} | Score: {job['role_confidence']:.2f}")
            lines.append(f"   URL: {job['url']}")
            lines.append("")
        
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return str(output_path)
