"""Storage tests for provider/discovery run telemetry and the application
lifecycle (M3) — temp/in-memory SQLite only.

All assertions are content-free or metadata-only; no titles/bodies are stored
for provider runs or discovery runs, and applications are the only
content-bearing table here (job ids + notes + timestamps).
"""

from __future__ import annotations

import pytest

from job_agent import db
from job_agent.application import ALL_STAGES, Application


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _insert_job(conn, jid: str = "remotive:1") -> None:
    conn.execute(
        """
        INSERT INTO jobs (id, title, company, url, source, source_type, location, status)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (jid, "AI Engineer", "Gamma", "https://x/1", "remotive", "structured_data", "Europe", "active"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Provider runs
# ---------------------------------------------------------------------------


def test_record_and_read_latest_provider_run(conn) -> None:
    rid = db.record_provider_run(
        conn,
        source_id="remote_remoteok",
        provider="remoteok",
        status="ok",
        requests=1,
        hits=3,
        candidate_jobs=3,
        latency_ms=420,
    )
    assert rid > 0
    runs = db.latest_provider_runs(conn, limit=5)
    assert len(runs) == 1
    row = runs[0]
    assert row["source_id"] == "remote_remoteok"
    assert row["provider"] == "remoteok"
    assert row["status"] == "ok"
    assert row["errors"] == []
    assert db.latest_provider_run_for_source(conn, "remote_remoteok")["hits"] == 3


def test_latest_provider_run_newest_first_per_source(conn) -> None:
    db.record_provider_run(conn, source_id="s1", provider="remoteok", status="ok", requests=1, hits=1, candidate_jobs=1)
    db.record_provider_run(conn, source_id="s1", provider="remoteok", status="ok", requests=1, hits=5, candidate_jobs=5)
    db.record_provider_run(
        conn,
        source_id="s2",
        provider="remotive",
        status="failed",
        requests=1,
        hits=0,
        candidate_jobs=0,
        errors=["boom"],
    )
    assert db.latest_provider_run_for_source(conn, "s1")["hits"] == 5  # newest
    assert db.provider_failure_count(conn) == 1
    runs_by_id = db.latest_provider_runs(conn, limit=0)
    assert runs_by_id == []


def test_provider_run_never_stores_content(conn) -> None:
    db.record_provider_run(
        conn,
        source_id="remote_remoteok",
        provider="remoteok",
        status="failed",
        requests=1,
        hits=0,
        candidate_jobs=0,
        errors=["fetch 500 for https://remoteok.com/api"],
    )
    row = db.latest_provider_run_for_source(conn, "remote_remoteok")
    # errors are bounded and content-free by convention; only metadata counts.
    assert row["status"] == "failed"
    assert db.provider_failure_count(conn) == 1


# ---------------------------------------------------------------------------
# Discovery run aggregates
# ---------------------------------------------------------------------------


def test_record_and_read_discovery_run(conn) -> None:
    db.record_discovery_run(
        conn,
        run_id="testrun",
        planned_queries=10,
        candidates_found=12,
        jobs_persisted=5,
        jobs_from_providers=3,
        jobs_from_search=2,
        provider_failures=1,
        fetch_errors=1,
        duration_ms=1500,
    )
    run = db.latest_discovery_run(conn)
    assert run is not None
    assert run["jobs_from_providers"] == 3
    assert run["jobs_from_search"] == 2
    assert run["provider_failures"] == 1
    assert run["duration_ms"] == 1500  # content-free aggregate


def test_no_discovery_run_yet(conn) -> None:
    assert db.latest_discovery_run(conn) is None


# ---------------------------------------------------------------------------
# Application lifecycle storage
# ---------------------------------------------------------------------------


def _app(job_id: str, stage: str) -> Application:
    base = Application(job_id=job_id)
    chain = {
        "APPLIED": ("APPLIED",),
        "RESPONDED": ("APPLIED", "RESPONDED"),
        "INTERVIEW": ("APPLIED", "RESPONDED", "INTERVIEW"),
        "OFFER": ("APPLIED", "RESPONDED", "INTERVIEW", "OFFER"),
        "HIRED": ("APPLIED", "RESPONDED", "INTERVIEW", "OFFER", "HIRED"),
        "REJECTED": ("APPLIED", "REJECTED"),
    }
    for step in chain.get(stage, ()):
        base = base.enter(step)
    assert base.stage == stage
    return base


def test_save_and_get_application_upsert(conn) -> None:
    _insert_job(conn, "remotive:1")
    db.save_application(conn, _app("remotive:1", "APPLIED"))
    first = db.get_application(conn, "remotive:1")
    assert first is not None
    assert first["stage"] == "APPLIED"
    assert first["applied_at"] is not None

    # Upsert: advancing the same job replaces the record, one row remains.
    db.save_application(conn, _app("remotive:1", "INTERVIEW"))
    updated = db.get_application(conn, "remotive:1")
    assert updated["stage"] == "INTERVIEW"
    rows = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    assert rows == 1


def test_get_application_missing(conn) -> None:
    assert db.get_application(conn, "nope") is None


def test_application_stage_counts(conn) -> None:
    _insert_job(conn, "remotive:1")
    _insert_job(conn, "remotive:2")
    _insert_job(conn, "remotive:3")
    db.save_application(conn, _app("remotive:1", "APPLIED"))
    db.save_application(conn, _app("remotive:2", "INTERVIEW"))
    db.save_application(conn, _app("remotive:3", "APPLIED"))
    counts = db.application_stage_counts(conn)
    assert counts["APPLIED"] == 2
    assert counts["INTERVIEW"] == 1
    assert counts["HIRED"] == 0
    assert all(counts[stage] >= 0 for stage in ALL_STAGES)


def test_save_application_with_metadata_roundtrip(conn) -> None:
    _insert_job(conn, "remotive:1")
    app = _app("remotive:1", "INTERVIEW").with_fields(
        notes="phone screen went well",
        interview_stage="second_round",
        interview_date="2026-03-01T10:00:00Z",
        follow_up_at="2026-03-15T00:00:00Z",
    )
    db.save_application(conn, app)
    row = db.get_application(conn, "remotive:1")
    assert row["notes"] == "phone screen went well"
    assert row["interview_stage"] == "second_round"
    assert row["interview_date"].startswith("2026-03-01T10:00:00")  # ISO, tz-aware
    assert row["follow_up_at"].startswith("2026-03-15")


def test_list_applications_joins_job_and_ordered(conn) -> None:
    _insert_job(conn, "remotive:1")
    _insert_job(conn, "remotive:2")
    db.save_application(conn, _app("remotive:2", "APPLIED"))
    db.save_application(conn, _app("remotive:1", "INTERVIEW"))
    rows = db.list_applications(conn)
    assert len(rows) == 2
    # Newer updated_at first (both just written; order is by job_id tiebreak).
    assert {r["job_id"] for r in rows} == {"remotive:1", "remotive:2"}
    first = rows[0]
    assert first["title"] == "AI Engineer"
    assert "e_total" not in first  # internal join column never leaks


def test_list_applications_stage_filter(conn) -> None:
    _insert_job(conn, "remotive:1")
    _insert_job(conn, "remotive:2")
    db.save_application(conn, _app("remotive:1", "APPLIED"))
    db.save_application(conn, _app("remotive:2", "INTERVIEW"))
    rows = db.list_applications(conn, stage="INTERVIEW")
    assert [r["job_id"] for r in rows] == ["remotive:2"]


def test_application_job_exists_only_for_real_jobs(conn) -> None:
    _insert_job(conn, "remotive:1")
    assert db.application_job_exists(conn, "remotive:1") is True
    assert db.application_job_exists(conn, "remotive:999") is False


def test_legacy_application_status_column_left_untouched(conn) -> None:
    _insert_job(conn, "remotive:1")
    db.save_application(conn, _app("remotive:1", "APPLIED"))
    row = conn.execute("SELECT status FROM applications WHERE job_id='remotive:1'").fetchone()
    assert row["status"] is None  # legacy column remains NULL (predates stages)
