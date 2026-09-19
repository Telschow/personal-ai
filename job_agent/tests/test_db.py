"""Storage tests: migrations, upsert/refresh, ranking, lifecycle, dedup.

All tests use an in-memory or temp SQLite file — no network, no real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from job_agent import db
from job_agent.canonical import canonical_key
from job_agent.models import Job, Score
from job_agent.salary import normalize_salary

UTC = UTC


def _job(
    jid: str,
    title: str = "Product Manager AI",
    company: str = "Acme GmbH",
    location: str = "Munich",
    salary_min_eur: float | None = None,
    salary_max_eur: float | None = None,
) -> Job:
    return Job(
        id=jid,
        title=title,
        company=company,
        url=f"https://acme.jobs/{jid}",
        source="test",
        source_type="ats_board",
        location=location,
        normalized_location=location,
        remote_mode="onsite",
        canonical_key=canonical_key(company, title, location),
        salary_min_eur=salary_min_eur,
        salary_max_eur=salary_max_eur,
        status="active",
    )


def _score(total: float = 70.0, decision: str = "review") -> Score:
    return Score(total=total, decision=decision, reasons=["x"], gaps=[], confidence=0.9)


def test_migrations_apply(tmp_path):
    dbfile = tmp_path / "t.db"
    conn = db.connect(str(dbfile))
    assert db.migrations.current_version(conn) == db.migrations.latest_version()
    tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "jobs" in tables and "evaluations" in tables
    assert "scan_runs" in tables and "job_decisions" in tables and "preferences" in tables
    conn.close()


def test_upsert_and_refresh(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    j = _job("1")
    db.upsert_job(conn, j)
    conn.commit()
    stored = db.get_job(conn, "1")
    assert stored is not None and stored.status == "active"
    assert stored.canonical_key == j.canonical_key

    # refresh increments last_seen, keeps field values
    db.upsert_job(conn, _job("1", company="Acme GmbH"))
    conn.commit()
    again = db.get_job(conn, "1")
    assert again is not None and again.id == "1"
    assert again.status == "active"
    conn.close()


def test_evaluate_and_store_ranks(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    a = _job("a", title="Senior AI Product Lead", salary_max_eur=160000)
    b = _job("b", title="Junior PM", salary_max_eur=80000)
    db.evaluate_and_store(conn, a, _score(90, "strong"), "v1", "p1")
    db.evaluate_and_store(conn, b, _score(30, "reject"), "v1", "p1")
    conn.commit()
    rows = db.list_matched_jobs(conn)
    assert len(rows) == 1  # reject excludes
    job, total, decision, reasons = rows[0]
    assert job.id == "a"
    assert total == 90.0
    conn.close()


def test_lifecycle_stale_and_closed(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    now = datetime.now(UTC)

    old = _job("old")
    old.last_checked = (now - timedelta(days=60)).isoformat()
    db.upsert_job(conn, old)
    conn.execute(
        "UPDATE jobs SET last_checked=?, missing_scans=0 WHERE id='old'", ((now - timedelta(days=60)).isoformat(),)
    )

    gone = _job("gone")
    db.upsert_job(conn, gone)
    conn.execute(
        "UPDATE jobs SET last_checked=?, missing_scans=0 WHERE id='gone'", ((now - timedelta(days=2)).isoformat(),)
    )

    current = _job("current")
    db.upsert_job(conn, current)
    conn.execute("UPDATE jobs SET last_checked=?, missing_scans=0 WHERE id='current'", (now.isoformat(),))
    conn.commit()

    # current is seen this run; old & gone are not.
    # First pass: gone gets missing_scans 1, old is stale from 60d.
    counts = db.apply_lifecycle(conn, seen_ids={"current"}, freshness_days=30, close_after_missing_scans=2)
    assert counts["stale"] >= 1

    # Second pass without seeing them: both gone (2) and old (2) close.
    counts2 = db.apply_lifecycle(conn, seen_ids={"current"}, freshness_days=30, close_after_missing_scans=2)
    assert counts2["closed"] == 2

    conn.commit()
    states = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM jobs").fetchall()}
    assert states["gone"] == "closed"
    assert states["current"] == "active"
    assert states["old"] == "closed"
    conn.close()


def test_dedup_merges_canonical_keys(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    a = _job("a", company="Acme", location="Munich")
    b = _job("b", company="Acme GmbH", location="München")  # same canonical key
    db.upsert_job(conn, a)
    db.upsert_job(conn, b)
    conn.commit()
    dup = db.find_duplicate_of(conn, b)
    assert dup is not None and dup.id == "a"

    counts = db.deduplicate(conn)
    assert counts["merged"] >= 1
    conn.commit()
    states = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM jobs").fetchall()}
    assert states["a"] == "active"
    assert states["b"] == "duplicate"
    conn.close()


def test_dedup_does_not_merge_after_close(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    a = _job("a", company="Acme")
    b = _job("b", company="Acme GmbH")  # same key as a
    db.upsert_job(conn, a)
    db.upsert_job(conn, b)
    conn.execute("UPDATE jobs SET status='closed' WHERE id='a'")
    conn.commit()
    dup = db.find_duplicate_of(conn, b)
    assert dup is None  # filtered (a is closed) → re-open candidate
    conn.close()


def test_salary_normalization_roundtrip(tmp_path):
    s = normalize_salary(100000, 130000, "USD")
    assert s.min_eur is not None and s.max_eur is not None

    conn = db.connect(str(tmp_path / "t.db"))
    j = _job("s1", title="PM USD")
    j.salary_min = 100000
    j.salary_max = 130000
    j.salary_currency = "USD"
    j.salary_min_eur = s.min_eur
    j.salary_max_eur = s.max_eur
    j.salary_converted = True
    db.upsert_job(conn, j)
    conn.commit()
    out = db.get_job(conn, "s1")
    assert out is not None
    assert out.salary_converted is True
    assert out.salary_min_eur == s.min_eur
    conn.close()


def test_stats_counts_and_scan_runs(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.evaluate_and_store(conn, _job("x"), _score(80, "strong"), "v1", "p1")
    db.record_scan_run(conn, source="test", status="completed", candidate_count=1, duration_ms=123)
    conn.commit()
    counts = db.stats_counts(conn)
    assert counts["total_jobs"] >= 1
    assert counts["total_evaluations"] >= 1
    assert db.average_source_duration_ms(conn) > 0
    conn.close()


def test_decisions_and_preferences(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.upsert_job(conn, _job("d1"))
    conn.commit()
    db.record_decision(conn, "d1", "approve", reason="strong fit", decided_by="test")
    assert db.recent_decisions(conn) and db.recent_decisions(conn)[0]["action"] == "approve"

    db.set_preference(conn, "remote_ok", "true", origin="user")
    assert db.get_preference(conn, "remote_ok")["value"] == "true"
    conn.close()
