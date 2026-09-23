"""Regression tests for feedback calibration."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from job_agent import db
from job_agent.feedback import add_feedback, get_feedback_for_job, get_feedback_summary, has_feedback
from job_agent.migrations import migrate
from job_agent.calibration import precision_at_k, false_positive_rate, false_negative_rate, score_feedback_correlation


def test_feedback_persistence() -> None:
    """Test that feedback is persisted correctly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = db.connect(db_path)
        try:
            migrate(conn)
            
            # Add a job
            job_id = "test-job-1"
            conn.execute(
                "INSERT INTO jobs (id, title, company, url) VALUES (?, ?, ?, ?)",
                (job_id, "Test Job", "Test Company", "http://example.com")
            )
            conn.commit()
            
            # Add feedback
            feedback_id = add_feedback(conn, job_id, "strong_interest", note="Great match")
            assert feedback_id > 0
            
            # Verify feedback persisted
            assert has_feedback(conn, job_id)
            feedback = get_feedback_for_job(conn, job_id)
            assert len(feedback) == 1
            assert feedback[0]["label"] == "strong_interest"
            assert feedback[0]["note"] == "Great match"
            
            # Verify summary
            summary = get_feedback_summary(conn)
            assert summary["total_records"] == 1
            assert summary["unique_jobs"] == 1
            assert summary["label_distribution"]["strong_interest"] == 1
            
        finally:
            conn.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_unknown_vs_poor_match() -> None:
    """Test that unknown classifications are handled differently from poor matches."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = db.connect(db_path)
        try:
            migrate(conn)
            
            # Add jobs with different role classifications
            job_unknown = "job-unknown"
            job_poor = "job-poor"
            
            conn.execute(
                "INSERT INTO jobs (id, title, company, url, role_family) VALUES (?, ?, ?, ?, ?)",
                (job_unknown, "Unknown Role", "Company A", "http://example.com", None)
            )
            conn.execute(
                "INSERT INTO jobs (id, title, company, url, role_family) VALUES (?, ?, ?, ?, ?)",
                (job_poor, "Poor Match", "Company B", "http://example.com", "Sales")
            )
            conn.commit()
            
            # Add feedback
            add_feedback(conn, job_unknown, "maybe")
            add_feedback(conn, job_poor, "not_interested")
            
            # Verify both are recorded but with different labels
            feedback_unknown = get_feedback_for_job(conn, job_unknown)
            feedback_poor = get_feedback_for_job(conn, job_poor)
            
            assert feedback_unknown[0]["label"] == "maybe"
            assert feedback_poor[0]["label"] == "not_interested"
            
        finally:
            conn.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_career_pivot_classification() -> None:
    """Test career pivot classification with feedback."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = db.connect(db_path)
        try:
            migrate(conn)
            
            # Add a career pivot job
            job_id = "pivot-job"
            conn.execute(
                "INSERT INTO jobs (id, title, company, url, role_family) VALUES (?, ?, ?, ?, ?)",
                (job_id, "Technical Product Manager", "Company", "http://example.com", "Technical Product Management")
            )
            conn.commit()
            
            # Add feedback indicating it's a good pivot
            add_feedback(conn, job_id, "interested", note="Good adjacent move")
            
            feedback = get_feedback_for_job(conn, job_id)
            assert feedback[0]["label"] == "interested"
            
        finally:
            conn.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_precision_at_k() -> None:
    """Test precision@K calculation."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = db.connect(db_path)
        try:
            migrate(conn)
            
            # Add jobs and evaluations
            for i in range(10):
                job_id = f"job-{i}"
                conn.execute(
                    "INSERT INTO jobs (id, title, company, url, source) VALUES (?, ?, ?, ?, ?)",
                    (job_id, f"Job {i}", f"Company {i}", "http://example.com", "test")
                )
                conn.execute(
                    "INSERT INTO evaluations (job_id, total) VALUES (?, ?)",
                    (job_id, 100 - i * 5)  # Decreasing scores
                )
            
            conn.commit()
            
            # Add feedback for top jobs
            add_feedback(conn, "job-0", "strong_interest")
            add_feedback(conn, "job-1", "interested")
            add_feedback(conn, "job-2", "not_interested")
            add_feedback(conn, "job-3", "strong_interest")
            
            precision_5 = precision_at_k(conn, 5)
            # Top 5 jobs: job-0, job-1, job-2, job-3, job-4
            # Relevant: job-0, job-1, job-3 = 3/5 = 0.6
            assert 0.5 <= precision_5 <= 0.7
            
        finally:
            conn.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_false_positive_rate() -> None:
    """Test false positive rate calculation."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        conn = db.connect(db_path)
        try:
            migrate(conn)
            
            # Add jobs
            for i in range(5):
                job_id = f"job-{i}"
                conn.execute(
                    "INSERT INTO jobs (id, title, company, url, source) VALUES (?, ?, ?, ?, ?)",
                    (job_id, f"Job {i}", f"Company {i}", "http://example.com", "test")
                )
                conn.execute(
                    "INSERT INTO evaluations (job_id, total) VALUES (?, ?)",
                    (job_id, 100 - i * 10)
                )
            
            conn.commit()
            
            # Add negative feedback to top job
            add_feedback(conn, "job-0", "not_interested")
            
            fpr = false_positive_rate(conn, 5)
            # Top 5 jobs, 1 negative = 0.2
            assert fpr == 0.2
            
        finally:
            conn.close()
    finally:
        Path(db_path).unlink(missing_ok=True)
