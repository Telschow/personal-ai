"""Tests for per-run isolation (Phase 2)."""

from __future__ import annotations

import uuid

from job_agent import db


def test_unique_run_id():
    run_id1 = uuid.uuid4().hex
    run_id2 = uuid.uuid4().hex
    assert run_id1 != run_id2


def test_record_discovery_run_isolation():
    conn = db.connect(":memory:")
    run_id1 = uuid.uuid4().hex
    db.record_discovery_run(
        conn,
        run_id=run_id1,
        planned_queries=0,
        candidates_found=5,
        jobs_persisted=3,
        jobs_from_providers=3,
        jobs_from_search=0,
        provider_failures=0,
        fetch_errors=0,
        duration_ms=100,
    )
    row = conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (run_id1,)).fetchone()
    assert row is not None
    assert row["jobs_from_providers"] == 3
    assert row["jobs_from_search"] == 0

    run_id2 = uuid.uuid4().hex
    db.record_discovery_run(
        conn,
        run_id=run_id2,
        planned_queries=0,
        candidates_found=2,
        jobs_persisted=1,
        jobs_from_providers=0,
        jobs_from_search=1,
        provider_failures=0,
        fetch_errors=0,
        duration_ms=200,
    )
    row2 = conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (run_id2,)).fetchone()
    assert row2 is not None
    assert row2["jobs_from_providers"] == 0
    assert row2["jobs_from_search"] == 1

    # ensure historical runs unaffected
    row1_again = conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (run_id1,)).fetchone()
    assert row1_again["jobs_from_search"] == 0


def test_provider_only_zero_search():
    conn = db.connect(":memory:")
    # simulate historical search run
    old_run = "oldrun"
    db.record_discovery_run(
        conn,
        run_id=old_run,
        planned_queries=0,
        candidates_found=2,
        jobs_persisted=2,
        jobs_from_providers=0,
        jobs_from_search=2,
        provider_failures=0,
        fetch_errors=0,
        duration_ms=10,
    )
    # new provider-only run
    new_run = uuid.uuid4().hex
    db.record_discovery_run(
        conn,
        run_id=new_run,
        planned_queries=0,
        candidates_found=3,
        jobs_persisted=3,
        jobs_from_providers=3,
        jobs_from_search=0,
        provider_failures=0,
        fetch_errors=0,
        duration_ms=20,
    )
    row = conn.execute("SELECT jobs_from_search FROM discovery_runs WHERE run_id=?", (new_run,)).fetchone()
    assert row is not None
    assert row["jobs_from_search"] == 0
