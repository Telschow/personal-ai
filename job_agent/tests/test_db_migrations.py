"""Migration integrity: existing (v7-era) data survives reaching the latest
schema, and the new provider/discovery/application tables land clean."""

from __future__ import annotations

import sqlite3

from job_agent import db, migrations


def _pre_migration_schema(conn: sqlite3.Connection) -> None:
    """Reproduce the tables that existed before migrations v8..v10 (v7)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id                      TEXT PRIMARY KEY,
            title                   TEXT NOT NULL,
            company                 TEXT NOT NULL,
            url                     TEXT NOT NULL DEFAULT '',
            apply_url               TEXT,
            source                  TEXT,
            source_type             TEXT,
            location                TEXT,
            country                 TEXT,
            normalized_location     TEXT,
            remote_mode             TEXT,
            employment_type         TEXT,
            date_posted             TEXT,
            description             TEXT,
            salary_min              REAL,
            salary_max              REAL,
            salary_currency         TEXT,
            salary_min_eur          REAL,
            salary_max_eur          REAL,
            salary_converted        INTEGER NOT NULL DEFAULT 0,
            raw_json                TEXT,
            canonical_key           TEXT,
            status                  TEXT NOT NULL DEFAULT 'active',
            discovered_at           TEXT,
            last_seen               TEXT DEFAULT CURRENT_TIMESTAMP,
            last_checked            TEXT,
            missing_scans           INTEGER NOT NULL DEFAULT 0,
            canonical_url           TEXT,
            source_count            INTEGER NOT NULL DEFAULT 0,
            user_status             TEXT NOT NULL DEFAULT 'NEW',
            user_status_updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            job_id TEXT PRIMARY KEY,
            total REAL,
            decision TEXT,
            reasons TEXT,
            gaps TEXT,
            confidence REAL,
            scoring_version TEXT,
            profile_version TEXT,
            evaluated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications (
            job_id TEXT PRIMARY KEY,
            status TEXT,
            cv_path TEXT,
            letter_path TEXT,
            submitted_at TEXT,
            notes TEXT
        );
        """
    )
    conn.execute("PRAGMA user_version = 7")
    conn.commit()


def test_migration_preserves_existing_rows(tmp_path) -> None:
    path = str(tmp_path / "legacy.sqlite3")
    raw = sqlite3.connect(path)
    _pre_migration_schema(raw)
    raw.execute(
        "INSERT INTO jobs (id, title, company, source) VALUES (?, ?, ?, ?)",
        ("legacy:1", "Senior Engineer", "Legacy Corp", "jsonld"),
    )
    raw.commit()

    assert migrations.current_version(raw) == 7
    migrations.migrate(raw)
    assert migrations.current_version(raw) == migrations.latest_version()

    row = raw.execute("SELECT id, title, company FROM jobs WHERE id = 'legacy:1'").fetchone()
    assert row == ("legacy:1", "Senior Engineer", "Legacy Corp")

    for table in ("provider_runs", "discovery_runs", "applications"):
        count = raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0
    raw.close()


def test_new_tables_empty_on_fresh_db(tmp_path) -> None:
    conn = db.connect(str(tmp_path / "fresh.sqlite3"))
    assert migrations.current_version(conn) == migrations.latest_version()
    for table in ("provider_runs", "discovery_runs", "applications"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_migration_is_idempotent(tmp_path) -> None:
    conn = db.connect(str(tmp_path / "idem.sqlite3"))
    applied = migrations.current_version(conn)
    conn.execute("INSERT INTO jobs (id, title, company, url) VALUES ('x', 'y', 'z', 'https://x')")
    conn.commit()
    assert migrations.migrate(conn) == applied  # no-op, no data loss
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    conn.close()


def test_application_and_run_inserts_roundtrip_after_migration(tmp_path) -> None:
    from job_agent.application import Application

    path = str(tmp_path / "roundtrip.sqlite3")
    raw = sqlite3.connect(path)
    _pre_migration_schema(raw)
    raw.close()

    conn = db.connect(path)
    db.upsert_job(
        conn,
        __import__("job_agent.models", fromlist=["Job"]).Job(
            id="rt:1",
            title="ML Engineer",
            company="Gamma",
            url="https://x/1",
            source="remotive",
            source_type="structured_data",
            location="Europe",
        ),
    )
    db.record_provider_run(
        conn,
        source_id="remote_remotive",
        provider="remotive",
        status="ok",
        requests=1,
        hits=1,
        candidate_jobs=1,
    )
    db.record_discovery_run(
        conn,
        run_id="migration-test",
        planned_queries=1,
        candidates_found=1,
        jobs_persisted=1,
        jobs_from_providers=1,
        jobs_from_search=0,
        provider_failures=0,
        fetch_errors=0,
        duration_ms=10,
    )
    application = Application(job_id="rt:1").enter("APPLIED")
    db.save_application(conn, application)
    conn.commit()

    assert db.latest_provider_runs(conn, limit=5)[0]["status"] == "ok"
    assert db.latest_discovery_run(conn)["jobs_persisted"] == 1
    assert db.get_application(conn, "rt:1")["stage"] == "APPLIED"
    conn.close()
