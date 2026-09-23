"""Application tracking system."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from job_agent import db


class ApplicationStatus(StrEnum):
    """Application status values."""
    
    DISCOVERED = "discovered"
    REVIEWED = "reviewed"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"


class ApplicationTracker:
    """Track job applications."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_table(self):
        """Create applications table if it doesn't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                job_id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                source TEXT NOT NULL,
                application_date TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                cv_variant TEXT,
                linkedin_variant TEXT,
                portfolio_project TEXT,
                cover_letter TEXT,
                notes TEXT,
                next_action TEXT,
                next_action_date TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        """)
        self.conn.commit()

    def add_application(self, job_id: str, company: str, role: str, source: str, notes: str = "") -> None:
        """Add a new application."""
        now = datetime.now().isoformat()
        self.conn.execute("""
            INSERT OR REPLACE INTO applications 
            (job_id, company, role, source, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (job_id, company, role, source, ApplicationStatus.DISCOVERED, notes, now))
        self.conn.commit()

    def update_status(self, job_id: str, status: ApplicationStatus, notes: str = "", next_action: str = "", next_action_date: str = "") -> None:
        """Update application status."""
        now = datetime.now().isoformat()
        self.conn.execute("""
            UPDATE applications SET
                status = ?,
                notes = COALESCE(?, notes),
                next_action = COALESCE(?, next_action),
                next_action_date = COALESCE(?, next_action_date),
                updated_at = ?
            WHERE job_id = ?
        """, (status, notes or None, next_action or None, next_action_date or None, now, job_id))
        self.conn.commit()

    def add_next_action(self, job_id: str, next_action: str, next_action_date: str) -> None:
        """Add next action for application."""
        now = datetime.now().isoformat()
        self.conn.execute("""
            UPDATE applications SET
                next_action = ?,
                next_action_date = ?,
                updated_at = ?
            WHERE job_id = ?
        """, (next_action, next_action_date, now, job_id))
        self.conn.commit()

    def list_applications(self, status: ApplicationStatus | None = None) -> list[dict[str, Any]]:
        """List applications with optional status filter."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM applications WHERE status = ? ORDER BY updated_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM applications ORDER BY updated_at DESC"
            ).fetchall()
        
        return [dict(row) for row in rows]

    def get_application(self, job_id: str) -> dict[str, Any] | None:
        """Get specific application."""
        row = self.conn.execute(
            "SELECT * FROM applications WHERE job_id = ?",
            (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_applications_by_status(self, statuses: list[ApplicationStatus]) -> list[dict[str, Any]]:
        """Get applications by multiple statuses."""
        placeholders = ",".join(["?" for _ in statuses])
        rows = self.conn.execute(
            f"SELECT * FROM applications WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            statuses
        ).fetchall()
        return [dict(row) for row in rows]

    def get_applications_with_next_action(self) -> list[dict[str, Any]]:
        """Get applications with pending next actions."""
        rows = self.conn.execute(
            "SELECT * FROM applications WHERE next_action IS NOT NULL AND next_action_date IS NOT NULL ORDER BY next_action_date ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_dossiers(self) -> list[dict[str, Any]]:
        """Get complete dossiers for all applications."""
        # Join with jobs to get full dossiers
        rows = self.conn.execute("""
            SELECT 
                a.*,
                j.title,
                j.company,
                j.location,
                j.url,
                j.role_family,
                j.location_scope,
                j.role_classification_confidence,
                j.location_score
            FROM applications a
            JOIN jobs j ON a.job_id = j.id
            ORDER BY a.updated_at DESC
        """).fetchall()
        
        return [dict(row) for row in rows]


def get_application_cli_commands():
    """Get application CLI commands."""
    return [
        {
            "command": "job-agent applications list",
            "description": "List all applications",
        },
        {
            "command": "job-agent applications list --status applied",
            "description": "List applied applications",
        },
        {
            "command": "job-agent applications list --status shortlisted",
            "description": "List shortlisted applications",
        },
        {
            "command": "job-agent applications add <job_id>",
            "description": "Add job to applications",
        },
        {
            "command": "job-agent applications update <job_id> --status <status>",
            "description": "Update application status",
        },
        {
            "command": "job-agent applications show <job_id>",
            "description": "Show application dossier",
        },
        {
            "command": "job-agent applications next-actions",
            "description": "Show applications with next actions",
        },
    ]
