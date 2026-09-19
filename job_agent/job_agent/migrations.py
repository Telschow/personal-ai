"""Lightweight SQLite migrations using PRAGMA user_version.

No Alembic, no external ORM. Each migration is a callable taking a connection
and running DDL/DML in a transaction. ``migrate`` applies all migrations newer
than the stored user_version, committing per migration.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from .logging_setup import get_logger, log_event

log = get_logger("migrations")

Migration = Callable[[sqlite3.Connection], None]

_MIGRATIONS: list[Migration] = []


def _exec_many(conn: sqlite3.Connection, script: str) -> None:
    """Run a DDL/DML script statement-by-statement so the surrounding
    explicit BEGIN...COMMIT in :func:`migrate` stays authoritative.

    (``executescript`` would implicitly commit the open transaction.)
    """
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def migration(order: int) -> Callable[[Migration], Migration]:
    """Register a migration to run when user_version < ``order``."""

    def _wrap(fn: Migration) -> Migration:
        assert order == len(_MIGRATIONS) + 1, "migrations must be strictly sequential starting at 1"
        _MIGRATIONS.append(fn)
        return fn

    return _wrap


@migration(1)
def _v1_initial(conn: sqlite3.Connection) -> None:
    """Baseline schema (matches the original hand-created tables)."""
    _exec_many(
        conn,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            company         TEXT NOT NULL,
            url             TEXT NOT NULL,
            apply_url       TEXT,
            source          TEXT,
            location        TEXT,
            country         TEXT,
            employment_type TEXT,
            date_posted     TEXT,
            description     TEXT,
            salary_min      REAL,
            salary_max      REAL,
            salary_currency TEXT,
            raw_json        TEXT,
            first_seen      TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen       TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            job_id       TEXT PRIMARY KEY,
            total        REAL,
            decision     TEXT,
            reasons_json TEXT,
            gaps_json    TEXT,
            evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        CREATE TABLE IF NOT EXISTS applications (
            job_id       TEXT PRIMARY KEY,
            status       TEXT,
            cv_path      TEXT,
            letter_path  TEXT,
            submitted_at TEXT,
            notes        TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        """,
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


@migration(2)
def _v2_foundation(conn: sqlite3.Connection) -> None:
    """Slice-1 foundation: lifecycle, canonical dedup, salary normalization,
    source taxonomy, decision & preference tables, scan runs.

    Guards every ALTER with a column-existence check so a freshly migrated
    database created directly from the complete (v2) schema is a no-op.
    """
    if not _column_exists(conn, "jobs", "canonical_key"):
        _exec_many(
            conn,
            """
            ALTER TABLE jobs ADD COLUMN canonical_key TEXT;
            ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
            ALTER TABLE jobs ADD COLUMN closed_at TEXT;
            ALTER TABLE jobs ADD COLUMN source_type TEXT;
            ALTER TABLE jobs ADD COLUMN discovered_at TEXT;
            ALTER TABLE jobs ADD COLUMN last_checked TEXT;
            ALTER TABLE jobs ADD COLUMN missing_scans INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE jobs ADD COLUMN application_deadline TEXT;
            ALTER TABLE jobs ADD COLUMN normalized_location TEXT;
            ALTER TABLE jobs ADD COLUMN remote_mode TEXT;
            ALTER TABLE jobs ADD COLUMN salary_min_eur REAL;
            ALTER TABLE jobs ADD COLUMN salary_max_eur REAL;
            ALTER TABLE jobs ADD COLUMN salary_converted INTEGER NOT NULL DEFAULT 0;
            """,
        )
    if not _column_exists(conn, "evaluations", "llm_explanation"):
        _exec_many(
            conn,
            """
            ALTER TABLE evaluations ADD COLUMN llm_explanation TEXT;
            ALTER TABLE evaluations ADD COLUMN confidence REAL;
            ALTER TABLE evaluations ADD COLUMN scoring_version TEXT;
            ALTER TABLE evaluations ADD COLUMN profile_version TEXT;
            """,
        )
    if not _column_exists(conn, "applications", "updated_at"):
        _exec_many(
            conn,
            """
            ALTER TABLE applications ADD COLUMN updated_at TEXT;
            """,
        )

    _exec_many(
        conn,
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at     TEXT,
            completed_at   TEXT,
            status         TEXT,
            source         TEXT,
            candidate_count INTEGER,
            fetched_count  INTEGER,
            parsed_count   INTEGER,
            errors         TEXT,
            duration_ms    INTEGER
        );
        CREATE TABLE IF NOT EXISTS job_decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT NOT NULL,
            action      TEXT NOT NULL,
            reason      TEXT,
            decided_by  TEXT,
            decided_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        CREATE TABLE IF NOT EXISTS preferences (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            origin      TEXT,
            evidence    TEXT,
            weight      REAL,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_canonical_key ON jobs(canonical_key);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_status ON scan_runs(status);
        """,
    )


@migration(3)
def _v3_career_fit(conn: sqlite3.Connection) -> None:
    """Slice-2 career fit results (aggregate-only, keyed per job)."""
    _exec_many(
        conn,
        """
        CREATE TABLE IF NOT EXISTS career_fit (
            job_id              TEXT PRIMARY KEY,
            current_fit         REAL NOT NULL,
            career_upside       REAL NOT NULL,
            evidence_coverage   REAL NOT NULL,
            breakdown_json      TEXT NOT NULL,
            strengths_json      TEXT NOT NULL,
            gaps_json           TEXT NOT NULL,
            transferable_json   TEXT NOT NULL,
            positioning_json    TEXT NOT NULL,
            risks_json          TEXT NOT NULL,
            evidence_refs_json  TEXT NOT NULL,
            knowledge_sources_json TEXT NOT NULL,
            narrative_json      TEXT,
            llm_used            INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        """,
    )


@migration(4)
def _v4_career_documents(conn: sqlite3.Connection) -> None:
    """Slice-3 CV/document foundation: documents, evidence, reconciliation
    checkpoints, artifacts (proposal-only).

    Evidence rows reference the user's own claim text (needed to re-validate
    proposals deterministically); reconciliation checkpoints store counts and
    statuses only. Artifacts are proposals and are always overwritten, never
    merged.
    """
    _exec_many(
        conn,
        """
        CREATE TABLE IF NOT EXISTS career_documents (
            document_id    TEXT PRIMARY KEY,
            filename       TEXT NOT NULL,
            source_path    TEXT NOT NULL,
            mime_type      TEXT NOT NULL,
            content_hash   TEXT NOT NULL,
            size_bytes     INTEGER NOT NULL,
            sections_json  TEXT NOT NULL,
            ingested_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS career_evidence (
            evidence_id     TEXT PRIMARY KEY,
            claim           TEXT NOT NULL,
            level           TEXT NOT NULL,
            source          TEXT NOT NULL,
            source_type     TEXT,
            categories_json TEXT NOT NULL,
            keywords_json   TEXT NOT NULL,
            confidence      REAL NOT NULL,
            normalized_fact TEXT,
            authority       TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_career_evidence_source ON career_evidence(source);
        CREATE TABLE IF NOT EXISTS career_reconciliation (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id   TEXT NOT NULL,
            total_facts   INTEGER NOT NULL,
            exact_matches INTEGER NOT NULL,
            new_count     INTEGER NOT NULL,
            conflicts     INTEGER NOT NULL,
            has_conflicts INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES career_documents(document_id)
        );
        CREATE TABLE IF NOT EXISTS career_artifacts (
            artifact_id      TEXT PRIMARY KEY,
            job_id           TEXT NOT NULL,
            status           TEXT NOT NULL,
            headline         TEXT NOT NULL,
            summary          TEXT NOT NULL,
            bullets_json     TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL,
            note             TEXT NOT NULL,
            source           TEXT NOT NULL,
            created_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_career_artifacts_job ON career_artifacts(job_id);
        CREATE TABLE IF NOT EXISTS career_artifact_evidence (
            artifact_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            PRIMARY KEY (artifact_id, evidence_id),
            FOREIGN KEY(artifact_id) REFERENCES career_artifacts(artifact_id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_id) REFERENCES career_evidence(evidence_id)
        );
        """,
    )


@migration(5)
def _v5_artifact_versioning(conn: sqlite3.Connection) -> None:
    """Slice-3.5: append-only artifact immutability.

    Every ``tailor()`` run now appends a NEW ``career_artifacts`` row scoped to
    ``(job_id, version)`` instead of overwriting the previous proposal. Existing
    v4 rows are preserved as ``version = 1``. Snapshot columns (mapping /
    validation / positioning) are added so a stored artifact audits itself
    without a second read. The unique ``(job_id, version)`` index replaces the
    plain per-job index (which is dropped).
    """
    if not _column_exists(conn, "career_artifacts", "version"):
        conn.execute("ALTER TABLE career_artifacts ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    if not _column_exists(conn, "career_artifacts", "llm_used"):
        conn.execute("ALTER TABLE career_artifacts ADD COLUMN llm_used INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "career_artifacts", "mapping_json"):
        conn.execute("ALTER TABLE career_artifacts ADD COLUMN mapping_json TEXT")
    if not _column_exists(conn, "career_artifacts", "validation_json"):
        conn.execute("ALTER TABLE career_artifacts ADD COLUMN validation_json TEXT")
    if not _column_exists(conn, "career_artifacts", "positioning_json"):
        conn.execute("ALTER TABLE career_artifacts ADD COLUMN positioning_json TEXT")
    _exec_many(
        conn,
        """
        DROP INDEX IF EXISTS idx_career_artifacts_job;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_career_artifacts_job_version
            ON career_artifacts(job_id, version);
        """,
    )


@migration(6)
def _v6_discovery_provenance(conn: sqlite3.Connection) -> None:
    """Slice-5 discovery provenance: per-job source attribution.

    Aggregators / search engines may surface the same posting through several
    sources. ``job_sources`` records every (source, canonical_url) pairing seen
    for a job — ids and methods only, never personal content — so diagnostics
    can answer "which discovery channel surfaced this job" without dumping
    job text. ``jobs.canonical_url`` acts as a secondary identity signal and
    ``jobs.source_count`` caches the number of distinct sources.
    """
    if not _column_exists(conn, "jobs", "canonical_url"):
        conn.execute("ALTER TABLE jobs ADD COLUMN canonical_url TEXT")
    if not _column_exists(conn, "jobs", "source_count"):
        conn.execute("ALTER TABLE jobs ADD COLUMN source_count INTEGER NOT NULL DEFAULT 0")
    _exec_many(
        conn,
        """
        CREATE TABLE IF NOT EXISTS job_sources (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            source_id       TEXT NOT NULL,
            source_name     TEXT,
            source_url      TEXT,
            discovery_method TEXT,
            query           TEXT,
            canonical_url   TEXT,
            discovered_at   TEXT,
            UNIQUE(job_id, source_id, canonical_url),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_job_sources_job ON job_sources(job_id);
        CREATE INDEX IF NOT EXISTS idx_job_sources_source ON job_sources(source_id);
        """,
    )


@migration(7)
def _v7_user_job_status(conn: sqlite3.Connection) -> None:
    """M3 MVP: user-facing job status (NEW/SAVED/REJECTED/APPLIED).

    Distinct from the system lifecycle status (active/stale/closed/duplicate).
    This tracks the user's personal decision on each job.
    """
    if not _column_exists(conn, "jobs", "user_status"):
        _exec_many(
            conn,
            """
            ALTER TABLE jobs ADD COLUMN user_status TEXT NOT NULL DEFAULT 'NEW';
            """,
        )
    if not _column_exists(conn, "jobs", "user_status_updated_at"):
        _exec_many(
            conn,
            """
            ALTER TABLE jobs ADD COLUMN user_status_updated_at TEXT;
            """,
        )
    _exec_many(
        conn,
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_status);
        """,
    )


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns the new user_version."""
    version = current_version(conn)
    applied = 0

    # Run with an explicit transaction (autocommit off) so a failed
    # migration rolls back atomically instead of leaving DDL committed.
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit for manual BEGIN/COMMIT
    try:
        for i, fn in enumerate(_MIGRATIONS, start=1):
            if i <= version:
                continue
            conn.execute("BEGIN")
            try:
                fn(conn)
                conn.execute(f"PRAGMA user_version = {i}")
                conn.execute("COMMIT")
                applied += 1
                log_event(log, "migration_applied", version=i)
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = previous_isolation
    if applied:
        log_event(log, "migrations_complete", from_version=version, to_version=current_version(conn))
    return current_version(conn)


def latest_version() -> int:
    return len(_MIGRATIONS)
