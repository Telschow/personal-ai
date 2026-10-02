"""Comprehensive career search report with all dimensions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime

from job_agent.models import Job
from job_agent.career.report_generator import CareerSearchReport
from job_agent.career.cv_tailoring import CVTailoringEngine, create_cv_variants_for_jobs
from job_agent.career.project_recommendations import ProjectRecommendationEngine
from job_agent.career.profile import derive_career_profile
import yaml


class ComprehensiveCareerReport:
    """Generate comprehensive career search report with all dimensions."""

    def __init__(self, conn: sqlite3.Connection, output_dir: str = "output"):
        self.conn = conn
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_full_report(self, run_id: str | None = None) -> dict:
        """Generate full career report with all dimensions."""
        
        print("Generating comprehensive career report...")
        
        # Load career profile
        with open("profile/profile.yaml", "r") as f:
            profile_data = yaml.safe_load(f)
        career_profile = derive_career_profile(profile_data)
        
        # Fetch jobs
        jobs = self._fetch_jobs(run_id)
        print(f"✓ Fetched {len(jobs)} jobs")
        
        # Generate basic report
        report_gen = CareerSearchReport(self.conn, str(self.output_dir / "reports"))
        basic_report = report_gen.generate_report(run_id)
        
        # Generate CV variants for top jobs
        print("Generating CV variants...")
        master_cv = {
            'experience': profile_data.get('experience', []),
            'skills': profile_data.get('skills', [])
        }
        cv_variants = create_cv_variants_for_jobs(jobs, career_profile, master_cv, top_n=5)
        
        # Generate project recommendations
        print("Generating project recommendations...")
        project_engine = ProjectRecommendationEngine(
            {'target_role_families': career_profile.target_role_families},
            master_cv
        )
        
        # Find common keywords from top jobs
        from collections import Counter
        all_keywords = []
        for job in jobs[:10]:
            keywords = job.title.lower().split() + (job.description or '').lower().split()
            all_keywords.extend([k for k in keywords if len(k) > 3])
        
        common_keywords = [kw for kw, _ in Counter(all_keywords).most_common(20)]
        projects = project_engine.recommend_projects(common_keywords, "product_management")
        
        # Compile full report
        full_report = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "run_id": run_id,
                "career_profile": {
                    "name": career_profile.name,
                    "current_role": career_profile.current_role_family,
                    "target_roles": career_profile.target_role_families,
                }
            },
            "corpus": basic_report,
            "shortlists": {
                "top_20": basic_report['top_shortlists']['top_20'][:20],
                "munich": basic_report['shortlists_by_category']['munich'],
                "ai_autonomous": basic_report['shortlists_by_category']['ai_autonomous'],
                "career_acceleration": basic_report['shortlists_by_category']['career_acceleration'],
            },
            "cv_variants": [
                {
                    "job_id": v.job_id,
                    "job_title": v.job_title,
                    "company": v.company,
                    "key_message": v.key_message,
                    "skills_emphasis": v.skills_emphasis[:5],
                    "gaps": v.gaps[:3],
                    "rationale": v.rationale,
                }
                for v in cv_variants
            ],
            "project_recommendations": [
                {
                    "name": p.project_name,
                    "target": p.career_target,
                    "problem": p.problem,
                    "skills": p.target_skills_demonstrated[:5],
                    "effort": p.estimated_effort,
                    "cv_potential": p.cv_bullet_potential,
                }
                for p in projects
            ]
        }
        
        # Write report
        self._write_markdown_report(full_report)
        
        return full_report

    def _fetch_jobs(self, run_id: str | None) -> list[Job]:
        """Fetch jobs from database."""
        where_clause = f" WHERE run_id = '{run_id}'" if run_id else ""
        
        rows = self.conn.execute(
            f"SELECT * FROM jobs{where_clause} ORDER BY last_seen DESC LIMIT 100"
        ).fetchall()
        
        jobs = []
        for row in rows:
            # Minimal job reconstruction
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

    def _write_markdown_report(self, report: dict):
        """Write comprehensive markdown report."""
        output_path = self.output_dir / "reports" / "comprehensive_career_report.md"
        
        lines = [
            "# Comprehensive Career Search Report",
            "",
            f"**Generated:** {report['metadata']['generated_at']}",
            f"**Run ID:** {report['metadata']['run_id'] or 'N/A'}",
            "",
            f"**Candidate:** {report['metadata']['career_profile']['name']}",
            f"**Current Role:** {report['metadata']['career_profile']['current_role']}",
            f"**Target Roles:** {', '.join(report['metadata']['career_profile']['target_roles'])}",
            "",
            "## Executive Summary",
            "",
            f"**Total Jobs Analyzed:** {report['corpus']['corpus_stats']['total_jobs']}",
            f"**Salary Disclosure Rate:** {report['corpus']['corpus_stats']['salary_disclosure_rate']:.1%}",
            "",
            "## Top Opportunities",
            "",
        ]
        
        for i, job in enumerate(report['shortlists']['top_20'][:10], 1):
            lines.append(f"{i}. **{job['title']}** at {job['company']}")
            lines.append(f"   Location: {job['location']} | Salary: {job['salary']}")
            lines.append(f"   Role: {job['role_family']} | Score: {job['role_confidence']:.2f}")
            lines.append(f"   URL: {job['url']}")
            lines.append("")
        
        lines.extend([
            "## Preferred City Opportunities",
            "",
        ])
        
        for job in report['shortlists']['preferred_city'][:10]:
            lines.append(f"- **{job['title']}** at {job['company']}")
        
        lines.extend([
            "",
            "## AI/Autonomous Systems Opportunities",
            "",
        ])
        
        for job in report['shortlists']['ai_autonomous'][:10]:
            lines.append(f"- **{job['title']}** at {job['company']}")
        
        lines.extend([
            "",
            "## CV Tailoring Recommendations",
            "",
        ])
        
        for variant in report['cv_variants']:
            lines.append(f"### {variant['job_title']} at {variant['company']}")
            lines.append(f"**Key Message:** {variant['key_message']}")
            lines.append(f"**Skills to Emphasize:** {', '.join(variant['skills_emphasis'])}")
            lines.append(f"**Gaps:** {', '.join(variant['gaps']) if variant['gaps'] else 'None identified'}")
            lines.append("")
        
        lines.extend([
            "## Project Recommendations",
            "",
        ])
        
        for proj in report['project_recommendations']:
            lines.append(f"### {proj['name']}")
            lines.append(f"**Target:** {proj['target']}")
            lines.append(f"**Problem:** {proj['problem']}")
            lines.append(f"**Effort:** {proj['effort']}")
            lines.append(f"**CV Potential:** {proj['cv_potential']}")
            lines.append("")
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ Full report written to {output_path}")


def generate_career_report(db_path: str, output_dir: str = "output"):
    """Generate full career report from database."""
    conn = sqlite3.connect(db_path)
    report_gen = ComprehensiveCareerReport(conn, output_dir)
    report = report_gen.generate_full_report()
    conn.close()
    return report
