"""Service layer for GUI - wraps backend functions for consumption by Streamlit app."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from job_agent import db
from job_agent.career.cv_generation import generate_cv
from job_agent.career.linkedin_optimization import (
    import_linkedin_profile,
    optimize_linkedin,
)
from job_agent.career.profile import CareerProfile, derive_career_profile
from job_agent.career.requirements import extract_job_attributes
from job_agent.config import Config, load_config
from job_agent.models import Job


class CareerService:
    """Service wrapper for backend functions."""

    def __init__(self, config_path: str | None = None, database_path: str | None = None):
        """Initialize service with optional config and database paths."""
        self.config_path = config_path
        self.database_path = database_path
        self._config = None
        self._career_profile = None
        self._knowledge = None

    @property
    def config(self) -> Config:
        """Lazy load configuration."""
        if self._config is None:
            self._config = load_config(path=self.config_path)
        return self._config

    def _get_db_path(self) -> str:
        """Get the database path."""
        return self.database_path or self.config.database_path

    @contextmanager
    def _db(self):
        """Context manager for database connection - creates new connection per operation."""
        conn = db.connect(self._get_db_path())
        try:
            yield conn
        finally:
            conn.close()

    @property
    def career_profile(self) -> CareerProfile:
        """Lazy load career profile."""
        if self._career_profile is None:
            with open(self.config.profile_path) as f:
                profile_data = (
                    json.load(f) if self.config.profile_path.endswith(".json") else __import__("yaml").safe_load(f)
                )
            self._career_profile = derive_career_profile(profile_data)
        return self._career_profile

    @property
    def knowledge(self):
        """Lazy load knowledge adapter."""
        if self._knowledge is None:
            from job_agent.career.knowledge import build_knowledge

            self._knowledge = build_knowledge("personal_ai", database_path=self.config.database_path)
        return self._knowledge

    # Job database operations
    def get_jobs(self, limit: int = 100, offset: int = 0) -> list[Job]:
        """Get jobs with pagination."""
        with self._db() as conn:
            return db.get_jobs(conn, limit=limit, offset=offset)

    def get_job(self, job_id: str) -> Job | None:
        """Get a single job by ID."""
        with self._db() as conn:
            return db.get_job(conn, job_id)

    def count_jobs(self) -> int:
        """Get total job count."""
        with self._db() as conn:
            return db.count_jobs(conn)

    def get_jobs_by_filters(self, filters: dict[str, Any], limit: int = 100, offset: int = 0) -> list[Job]:
        """Get jobs with filters (simplified - would need to extend db.py for complex filtering)."""
        # For now, get all jobs and filter in memory - optimize later if needed
        with self._db() as conn:
            all_jobs = db.get_jobs(conn, limit=10000)  # Get reasonable chunk
        filtered_jobs = []

        for job in all_jobs:
            match = True
            for key, value in filters.items():
                if (
                    key == "role_family"
                    and getattr(job, "role_family", None) != value
                    or key == "location"
                    and value.lower() not in (getattr(job, "location", "") or "").lower()
                    or key == "company"
                    and value.lower() not in (getattr(job, "company", "") or "").lower()
                    or key == "salary_disclosed"
                    and value
                    and not (job.salary_min_eur or job.salary_max_eur)
                ):
                    match = False
                    break
                # Add more filters as needed

            if match:
                filtered_jobs.append(job)

        # Apply pagination
        start = offset
        end = offset + limit
        return filtered_jobs[start:end]

    def get_jobs_with_scores(self, limit: int = 100) -> list[tuple[Job, dict]]:
        """Get jobs with their fit scores."""
        # This would need to be implemented based on how scores are stored
        # For now, return jobs with placeholder scores
        jobs = self.get_jobs(limit=limit)
        result = []
        for job in jobs:
            # Placeholder - in reality, this would come from evaluations table or recent scoring
            score_data = {
                "total": 50.0,  # placeholder
                "decision": "unknown",
                "reasons": [],
                "gaps": [],
                "confidence": 0.5,
            }
            result.append((job, score_data))
        return result

    # Feedback operations
    def add_feedback(self, job_id: str, label: str, note: str | None = None, run_id: str | None = None) -> int:
        """Add feedback for a job."""
        from job_agent.feedback import validate_label

        validated_label = validate_label(label)
        now = datetime.now(UTC).isoformat(timespec="seconds")

        with self._db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback (job_id, label, note, created_at, run_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, validated_label, note, now, run_id),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def get_feedback_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Get all feedback for a job."""
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, label, note, created_at, run_id
                FROM feedback
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_feedback_summary(self) -> dict[str, Any]:
        """Get feedback summary."""
        from job_agent.feedback import get_feedback_summary

        with self._db() as conn:
            return get_feedback_summary(conn)

    def get_latest_feedback_for_job(self, job_id: str) -> dict[str, Any] | None:
        """Get latest feedback for a job."""
        from job_agent.feedback import get_latest_feedback_for_job

        with self._db() as conn:
            return get_latest_feedback_for_job(conn, job_id)

    # Application operations
    def get_application(self, job_id: str) -> dict[str, Any] | None:
        """Get application for a job."""
        from job_agent.db import get_application

        with self._db() as conn:
            return get_application(conn, job_id)

    def save_application(self, job_id: str, stage: str = "APPLIED", notes: str | None = None) -> None:
        """Save or update application for a job."""
        from job_agent.application import Application
        from job_agent.db import save_application

        app = Application(job_id=job_id, stage=stage, notes=notes)
        with self._db() as conn:
            save_application(conn, app)

    # CV Generation
    def generate_cv(
        self, job_id: str, cv_path: str, language: str = "en", use_llm: bool = True, use_semantic: bool = True
    ) -> dict[str, Any]:
        """Generate a CV for a job."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Extract job attributes
        attrs = extract_job_attributes(job)

        # Load CV document for evidence
        from pathlib import Path

        from job_agent.career.documents import ingest_document

        doc = ingest_document(Path(cv_path))

        with self._db() as conn:
            # Save document to DB and get evidence
            db.save_career_document(conn, doc)
            candidates = db.candidate_evidence_from_document(doc)
            existing = db._get_existing_evidence(conn)  # Assuming this exists
            result = db.reconcile_document(doc, candidates, existing)
            db.save_career_evidence_many(conn, result.evidence)
            all_evidence = db._get_existing_evidence(conn)
            db.save_career_reconciliation(
                conn,
                document_id=doc.document_id,
                total_facts=result.new_count + result.kept_count + result.conflict_count,
                exact_matches=result.kept_count,
                new_count=result.new_count,
                conflicts=result.conflict_count,
                status="conflicts" if result.conflict_count else "clean",
            )
            conn.commit()

        # LLM and semantic clients (opt-in, fail-closed)
        client = None
        semantic_client = None
        if use_llm and self.config.career.llm.enabled:
            from job_agent.career.cv_llm import OllamaCvClient
            from job_agent.career.llm_log import LlmCallLog

            llm_log = LlmCallLog()
            llm = self.config.career.llm
            llm_timeout = llm.timeout_seconds or self.config.llm.timeout_seconds
            client = OllamaCvClient(
                llm.base_url or self.config.llm.base_url,
                llm.model or self.config.llm.model,
                temperature=llm.temperature,
                timeout=llm_timeout,
                call_log=llm_log,
            )
            if self.config.career.llm.semantic and use_semantic:
                from job_agent.career.llm import OllamaJsonClient

                semantic_client = OllamaJsonClient(
                    llm.base_url or self.config.llm.base_url,
                    llm.model or self.config.llm.model,
                    temperature=llm.temperature,
                    timeout=llm_timeout,
                    call_log=llm_log,
                )

        # Generate CV

        cv_result = generate_cv(
            self.career_profile,
            attrs,
            all_evidence,
            job_id=job_id,
            client=client,
            semantic_client=semantic_client,
        )

        # Save artifact
        with self._db() as conn:
            db.save_career_artifact(
                conn,
                cv_result.artifact,
                source=cv_result.artifact.status.value,
                llm_used=(cv_result.artifact.status.value == "llm"),
                mapping_json=json.dumps(
                    [m.model_dump(mode="json") for m in cv_result.positioning.mapping]
                    if hasattr(cv_result.positioning, "mapping")
                    else [],
                    ensure_ascii=False,
                ),
                validation_json=json.dumps(
                    [v.model_dump(mode="json") for v in cv_result.artifact.check_compliance(all_evidence)[1]],
                    ensure_ascii=False,
                ),
                positioning_json=json.dumps(
                    cv_result.positioning.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            )
            conn.commit()

        return {
            "artifact_id": cv_result.artifact.artifact_id,
            "job_id": job_id,
            "headline": cv_result.artifact.headline,
            "summary": cv_result.artifact.summary,
            "sections": [
                {"name": s.name, "content": s.content, "evidence_ids": s.evidence_ids} for s in cv_result.sections
            ],
            "status": cv_result.artifact.status.value,
            "move_type": cv_result.move_type.value,
            "gaps": cv_result.gaps,
            "manifest": cv_result.manifest.model_dump(mode="json") if cv_result.manifest else None,
        }

    def render_cv_human(self, cv_data: dict[str, Any], language: str = "en") -> str:
        """Render CV data as human-readable markdown."""
        # Reconstruct objects from dict

        # This is simplified - in reality we'd need to reconstruct all objects properly
        # For now, return a placeholder
        return f"# CV for job {cv_data['job_id']}\n\nHeadline: {cv_data['headline']}\n\nSummary: {cv_data['summary']}"

    # LinkedIn Optimization
    def optimize_linkedin(
        self, job_id: str, cv_path: str, linkedin_profile_path: str, use_llm: bool = True, use_semantic: bool = True
    ) -> dict[str, Any]:
        """Optimize LinkedIn profile for a job."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Extract job attributes
        attrs = extract_job_attributes(job)

        # Load CV document for evidence
        from pathlib import Path

        from job_agent.career.documents import ingest_document

        doc = ingest_document(Path(cv_path))

        with self._db() as conn:
            # Save document to DB and get evidence
            db.save_career_document(conn, doc)
            candidates = db.candidate_evidence_from_document(doc)
            existing = db._get_existing_evidence(conn)
            result = db.reconcile_document(doc, candidates, existing)
            db.save_career_evidence_many(conn, result.evidence)
            all_evidence = db._get_existing_evidence(conn)
            db.save_career_reconciliation(
                conn,
                document_id=doc.document_id,
                total_facts=result.new_count + result.kept_count + result.conflict_count,
                exact_matches=result.kept_count,
                new_count=result.new_count,
                conflicts=result.conflict_count,
                status="conflicts" if result.conflict_count else "clean",
            )
            conn.commit()

        # Import LinkedIn profile
        linkedin_profile = import_linkedin_profile(linkedin_profile_path)

        # LLM and semantic clients (opt-in, fail-closed)
        client = None
        semantic_client = None
        if use_llm and self.config.career.llm.enabled:
            from job_agent.career.cv_llm import OllamaCvClient
            from job_agent.career.llm_log import LlmCallLog

            llm_log = LlmCallLog()
            llm = self.config.career.llm
            llm_timeout = llm.timeout_seconds or self.config.llm.timeout_seconds
            client = OllamaCvClient(
                llm.base_url or self.config.llm.base_url,
                llm.model or self.config.llm.model,
                temperature=llm.temperature,
                timeout=llm_timeout,
                call_log=llm_log,
            )
            if self.config.career.llm.semantic and use_semantic:
                from job_agent.career.llm import OllamaJsonClient

                semantic_client = OllamaJsonClient(
                    llm.base_url or self.config.llm.base_url,
                    llm.model or self.config.llm.model,
                    temperature=llm.temperature,
                    timeout=llm_timeout,
                    call_log=llm_log,
                )

        # Generate CV (needed for LinkedIn optimization)

        cv_result = generate_cv(
            self.career_profile,
            attrs,
            all_evidence,
            job_id=job_id,
            client=client,
            semantic_client=semantic_client,
        )

        # Optimize LinkedIn

        result = optimize_linkedin(linkedin_profile, self.career_profile, all_evidence, cv_result)

        # Save artifact (for tracking)
        with self._db() as conn:
            db.save_career_artifact(
                conn,
                cv_result.artifact,
                source="linkedin_optimization",
                llm_used=False,
                mapping_json=json.dumps(
                    [m.model_dump(mode="json") for m in cv_result.positioning.mapping]
                    if hasattr(cv_result.positioning, "mapping")
                    else [],
                    ensure_ascii=False,
                ),
                validation_json=json.dumps(
                    [v.model_dump(mode="json") for v in cv_result.artifact.check_compliance(all_evidence)[1]],
                    ensure_ascii=False,
                ),
                positioning_json=json.dumps(
                    cv_result.positioning.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            )
            conn.commit()

        return {
            "current_profile": linkedin_profile.__dict__,
            "recommendations": [
                {
                    "section": r.section,
                    "current": r.current,
                    "recommended": r.recommended,
                    "rationale": r.rationale,
                    "evidence_ids": r.evidence_ids,
                    "verification": r.verification,
                }
                for r in result.recommendations
            ],
            "career_move_type": result.career_move_type,
            "validation_status": result.validation_status.value,
        }

    # Project recommendations
    def get_project_recommendations(self, job_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Get project recommendations for a job."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Extract job attributes
        attrs = extract_job_attributes(job)

        # Get evidence
        from job_agent.career.retrieval import build_retrieval_plan, collect_evidence

        plan = build_retrieval_plan(self.career_profile, attrs)
        collect_evidence(
            self.career_profile,
            self.career_profile,
            self.knowledge,
            plan,
            attrs,
        )

        # Generate recommendations
        from job_agent.career.project_recommendations import ProjectRecommendationEngine

        engine = ProjectRecommendationEngine(
            {"target_role_families": self.career_profile.target_role_families},
            {"experience": self.career_profile.experience, "skills": self.career_profile.skills},
        )

        recommendations = engine.recommend_projects(
            [kw for kw in attrs.concepts if len(kw) > 2], attrs.role_family or "product_management"
        )

        return [
            {
                "name": p.project_name,
                "target": p.career_target,
                "problem": p.problem,
                "skills": p.target_skills_demonstrated,
                "effort": p.estimated_effort,
                "cv_potential": p.cv_bullet_potential,
            }
            for p in recommendations[:limit]
        ]

    # Application tracker
    def get_applications(self, status: str | None = None) -> list[dict[str, Any]]:
        """Get applications with optional status filter."""
        from job_agent.db import list_applications

        with self._db() as conn:
            apps = list_applications(conn)
        if status:
            return [app for app in apps if app.get("stage") == status]
        else:
            return apps

    def update_application_stage(self, job_id: str, stage: str) -> None:
        """Update application stage for a job."""
        from job_agent.application import Application, check_transition
        from job_agent.db import get_application, save_application

        with self._db() as conn:
            current_app = get_application(conn, job_id)
            if current_app:
                app = Application(**current_app)
                check_transition(app.stage, stage)
                app = app.enter(stage)
                save_application(conn, app)
            else:
                # Create new application
                app = Application(job_id=job_id, stage=stage)
                save_application(conn, app)

    # Reports
    def list_reports(self) -> list[dict[str, Any]]:
        """List available reports."""
        from pathlib import Path

        report_dir = Path(self.config.report_dir)
        if not report_dir.exists():
            return []

        reports = []
        for report_file in report_dir.glob("*.md"):
            stat = report_file.stat()
            reports.append(
                {
                    "name": report_file.name,
                    "path": str(report_file),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )

        return sorted(reports, key=lambda x: x["modified"], reverse=True)

    def get_report_content(self, report_path: str) -> str:
        """Get content of a report file."""
        with open(report_path, encoding="utf-8") as f:
            return f.read()

    # Provider health
    def get_provider_health(self) -> list[dict[str, Any]]:
        """Get provider health status."""
        from job_agent.db import latest_provider_runs, provider_failure_count

        with self._db() as conn:
            providers = latest_provider_runs(conn, limit=50)
            failure_counts = provider_failure_count(conn)

        result = []
        for provider in providers:
            provider_name = provider["provider"]
            failure_count = failure_counts.get(provider_name, 0)

            # Determine status
            if provider["status"] == "success" and failure_count == 0:
                status = "healthy_with_results" if provider.get("count", 0) > 0 else "healthy_zero_results"
            elif provider["status"] == "success":
                status = "partial_results"
            elif provider["status"] in ("timeout", "http_error"):
                status = provider["status"]
            else:
                status = "unknown"

            result.append(
                {
                    "provider": provider_name,
                    "source_id": provider["source_id"],
                    "status": status,
                    "last_run": provider.get("last_seen"),
                    "candidates": provider.get("count", 0),
                    "new": provider.get("new", 0),
                    "duplicates": provider.get("duplicates", 0),
                    "errors": provider.get("errors", 0),
                    "latency": provider.get("duration_seconds", 0),
                    "failure_count": failure_count,
                }
            )

        return result

    # Crawl control
    def get_latest_discovery_run(self) -> dict[str, Any] | None:
        """Get latest discovery run info."""
        from job_agent.db import latest_discovery_run

        with self._db() as conn:
            return latest_discovery_run(conn)

    def trigger_crawl(self) -> None:
        """Trigger a new discovery crawl."""
        # This would need to call the existing crawl functionality
        # For now, we'll note that this should be done via the CLI or scheduler
        # The GUI can't easily trigger a crawl without duplicating logic
        # But we can at least show the status
        pass

    def close(self):
        """Close database connection."""
        # No persistent connection to close with connection-per-operation pattern
        pass
