"""Human feedback model and persistence layer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from .logging_setup import get_logger

log = get_logger("feedback")

# Valid feedback labels
VALID_LABELS: frozenset[str] = frozenset({
    "strong_interest",
    "interested",
    "maybe",
    "not_interested",
    "wrong_role",
    "wrong_seniority",
    "wrong_location",
    "wrong_compensation",
    "wrong_domain",
    "duplicate",
    "irrelevant",
})

# Labels that indicate positive relevance
POSITIVE_LABELS: frozenset[str] = frozenset({"strong_interest", "interested"})

# Labels that indicate negative relevance
NEGATIVE_LABELS: frozenset[str] = frozenset({
    "not_interested",
    "wrong_role",
    "wrong_seniority",
    "wrong_location",
    "wrong_compensation",
    "wrong_domain",
    "irrelevant",
})

# Neutral labels
NEUTRAL_LABELS: frozenset[str] = frozenset({"maybe", "duplicate"})


def is_relevant(label: str) -> bool:
    """Determine if a feedback label counts as 'relevant' for precision@K."""
    return label in POSITIVE_LABELS


def is_negative(label: str) -> bool:
    """Determine if a feedback label is a negative signal."""
    return label in NEGATIVE_LABELS


def is_positive(label: str) -> bool:
    """Determine if a feedback label is a positive signal."""
    return label in POSITIVE_LABELS


def validate_label(label: str) -> str:
    """Validate and normalize a feedback label."""
    normalized = label.strip().lower().replace(" ", "_")
    if normalized not in VALID_LABELS:
        raise ValueError(
            f"Invalid feedback label: '{label}'. Valid labels: {', '.join(sorted(VALID_LABELS))}"
        )
    return normalized


def add_feedback(
    conn: sqlite3.Connection,
    job_id: str,
    label: str,
    note: str | None = None,
    run_id: str | None = None,
) -> int:
    """Add a human feedback record. Never overwrites historical feedback."""
    validated_label = validate_label(label)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    
    cur = conn.execute(
        """
        INSERT INTO feedback (job_id, label, note, created_at, run_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (job_id, validated_label, note, now, run_id),
    )
    conn.commit()
    feedback_id = int(cur.lastrowid or 0)
    log_event(log, "feedback_added", job_id=job_id, label=validated_label, feedback_id=feedback_id)
    return feedback_id


def get_feedback_for_job(
    conn: sqlite3.Connection, job_id: str
) -> list[dict[str, Any]]:
    """Get all feedback records for a job, ordered by creation time."""
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


def get_latest_feedback_for_job(
    conn: sqlite3.Connection, job_id: str
) -> dict[str, Any] | None:
    """Get the most recent feedback record for a job."""
    row = conn.execute(
        """
        SELECT id, job_id, label, note, created_at, run_id
        FROM feedback
        WHERE job_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    return dict(row) if row else None


def get_all_feedback(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[dict[str, Any]]:
    """Get all feedback records, ordered by creation time descending."""
    sql = """
        SELECT id, job_id, label, note, created_at, run_id
        FROM feedback
        ORDER BY created_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_feedback_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Get a summary of all feedback."""
    total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    unique_jobs = conn.execute(
        "SELECT COUNT(DISTINCT job_id) FROM feedback"
    ).fetchone()[0]
    
    label_counts = conn.execute(
        "SELECT label, COUNT(*) as cnt FROM feedback GROUP BY label ORDER BY cnt DESC"
    ).fetchall()
    
    return {
        "total_records": total,
        "unique_jobs": unique_jobs,
        "label_distribution": {r[0]: r[1] for r in label_counts},
    }


def get_labeled_job_ids(conn: sqlite3.Connection) -> set[str]:
    """Get all job IDs that have feedback."""
    rows = conn.execute(
        "SELECT DISTINCT job_id FROM feedback"
    ).fetchall()
    return {r[0] for r in rows}


def has_feedback(conn: sqlite3.Connection, job_id: str) -> bool:
    """Check if a job has any feedback."""
    row = conn.execute(
        "SELECT 1 FROM feedback WHERE job_id = ? LIMIT 1",
        (job_id,),
    ).fetchone()
    return row is not None


def log_event(log, event: str, **kwargs: Any) -> None:
    """Log a feedback event."""
    from .logging_setup import log_event as _log_event
    _log_event(log, event, **kwargs)