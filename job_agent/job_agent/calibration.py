"""Feedback calibration diagnostics."""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import get_jobs
from .feedback import get_feedback_summary, get_latest_feedback_for_job


def precision_at_k(conn: sqlite3.Connection, k: int = 10) -> float:
    """Calculate precision@K based on feedback."""
    # Get top K jobs by score
    jobs = get_jobs(conn, limit=k)
    if not jobs:
        return 0.0

    relevant_count = 0
    for job in jobs:
        feedback = get_latest_feedback_for_job(conn, job.id)
        if feedback and feedback["label"] in ("strong_interest", "interested"):
            relevant_count += 1

    return relevant_count / len(jobs) if jobs else 0.0


def false_positive_rate(conn: sqlite3.Connection, k: int = 10) -> float:
    """Calculate false positive rate in top K jobs."""
    jobs = get_jobs(conn, limit=k)
    if not jobs:
        return 0.0

    negative_count = 0
    for job in jobs:
        feedback = get_latest_feedback_for_job(conn, job.id)
        if feedback and feedback["label"] in (
            "not_interested",
            "wrong_role",
            "wrong_seniority",
            "wrong_location",
            "wrong_compensation",
            "wrong_domain",
            "irrelevant",
        ):
            negative_count += 1

    return negative_count / len(jobs) if jobs else 0.0


def false_negative_rate(conn: sqlite3.Connection) -> float:
    """Calculate false negative rate (positive feedback on non-ranked jobs)."""
    # This requires tracking which jobs are in top ranks vs not
    # For simplicity, check if jobs with positive feedback are in top 100
    jobs = get_jobs(conn, limit=100)
    ranked_job_ids = {j.id for j in jobs}

    # Count positive feedback jobs not in top 100
    cursor = conn.execute("SELECT job_id FROM feedback WHERE label IN ('strong_interest', 'interested')")
    feedback_jobs = [r[0] for r in cursor.fetchall()]

    if not feedback_jobs:
        return 0.0

    missed = sum(1 for jid in feedback_jobs if jid not in ranked_job_ids)
    return missed / len(feedback_jobs)


def score_feedback_correlation(conn: sqlite3.Connection) -> dict[str, Any]:
    """Analyze correlation between scores and feedback."""
    jobs = get_jobs(conn, limit=500)

    scores_with_feedback = []
    for job in jobs:
        feedback = get_latest_feedback_for_job(conn, job.id)
        if not feedback:
            continue

        score = conn.execute(
            "SELECT total FROM evaluations WHERE job_id = ?",
            (job.id,),
        ).fetchone()

        if score:
            scores_with_feedback.append(
                {
                    "job_id": job.id,
                    "score": score[0],
                    "label": feedback["label"],
                }
            )

    if not scores_with_feedback:
        return {"error": "No scored jobs with feedback"}

    # Average score by label
    label_scores = {}
    for item in scores_with_feedback:
        label = item["label"]
        if label not in label_scores:
            label_scores[label] = []
        label_scores[label].append(item["score"])

    avg_scores = {label: sum(scores) / len(scores) for label, scores in label_scores.items()}

    return {
        "total_with_feedback": len(scores_with_feedback),
        "average_scores_by_label": avg_scores,
        "label_counts": {label: len(scores) for label, scores in label_scores.items()},
    }


def generate_calibration_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """Generate comprehensive calibration report."""
    summary = get_feedback_summary(conn)

    return {
        "feedback_summary": summary,
        "precision_at_5": precision_at_k(conn, 5),
        "precision_at_10": precision_at_k(conn, 10),
        "precision_at_20": precision_at_k(conn, 20),
        "false_positive_rate_10": false_positive_rate(conn, 10),
        "false_negative_rate": false_negative_rate(conn),
        "score_feedback_correlation": score_feedback_correlation(conn),
    }
