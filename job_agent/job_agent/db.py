"""SQLite storage layer.

All SQL lives behind this module (store boundary). The agent layer never
touches SQLite directly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import migrations
from .logging_setup import get_logger
from .models import Job, Score

log = get_logger("db")

UTC = UTC

RETRYABLE_STATUSES = {"active", "stale"}
CLOSED_STATUSES = {"closed", "duplicate"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    """Open (creating parent dirs) and migrate the database."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")  # transient writer lock -> wait, don't fail
    migrations.migrate(conn)
    return conn


def upsert_job(conn: sqlite3.Connection, job: Job) -> None:
    """Insert a normalized job, or refresh it if it already exists.

    ``job`` must already carry its ``canonical_key``, ``source_type``,
    ``normalized_location``, ``remote_mode`` and EUR-normalized salary fields
    (see :mod:`job_agent.normalizer`).
    """
    cols = (
        "id,title,company,url,apply_url,source,source_type,location,country,normalized_location,"
        "remote_mode,employment_type,date_posted,description,salary_min,salary_max,salary_currency,"
        "salary_min_eur,salary_max_eur,salary_converted,raw_json,canonical_key,status,discovered_at,"
        "last_seen,last_checked,missing_scans,canonical_url,source_count,user_status,user_status_updated_at"
    )
    placeholders = ",".join(["?"] * 31)
    now = _now()
    sql = f"""
        INSERT OR IGNORE INTO jobs ({cols}) VALUES ({placeholders})
    """
    conn.execute(
        sql,
        (
            job.id,
            job.title,
            job.company,
            job.url,
            job.apply_url,
            job.source,
            job.source_type,
            job.location,
            job.country,
            job.normalized_location,
            job.remote_mode,
            job.employment_type,
            job.date_posted.isoformat() if job.date_posted else None,
            job.description,
            job.salary_min,
            job.salary_max,
            job.salary_currency,
            job.salary_min_eur,
            job.salary_max_eur,
            1 if job.salary_converted else 0,
            job.raw_json if isinstance(job.raw_json, str) else json.dumps(job.raw, ensure_ascii=False),
            job.canonical_key,
            "active",
            now,
            now,
            now,
            0,
            job.canonical_url,
            0,
            job.user_status if job.user_status else "NEW",
            job.user_status_updated_at,
        ),
    )
    # Refresh last_seen/last_checked whenever the job is seen again.
    # IMPORTANT: Do NOT update user_status or user_status_updated_at here - those are user-owned.
    conn.execute(
        """
        UPDATE jobs SET last_seen=?, last_checked=?, missing_scans=0
        WHERE id=? AND status NOT IN ('closed','duplicate')
        """,
        (now, now, job.id),
    )


def record_evaluation(
    conn: sqlite3.Connection,
    job_id: str,
    score: Score,
    scoring_version: str,
    profile_version: str,
    explanation: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO evaluations
            (job_id, total, decision, reasons_json, gaps_json, llm_explanation,
             confidence, scoring_version, profile_version, evaluated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            score.total,
            score.decision,
            json.dumps(score.reasons),
            json.dumps(score.gaps),
            explanation,
            score.confidence,
            scoring_version,
            profile_version,
            _now(),
        ),
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def update_user_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    user_status: str,
) -> bool:
    """Update the user-facing status of a job.

    Returns True if the job was found and updated, False otherwise.
    """
    now = _now()
    cursor = conn.execute(
        """
        UPDATE jobs SET user_status=?, user_status_updated_at=?
        WHERE id=?
        """,
        (user_status, now, job_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    source: str | None = None,
    user_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if source:
        where.append("source = ?")
        params.append(source)
    if user_status:
        where.append("user_status = ?")
        params.append(user_status)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs{where_sql} ORDER BY last_seen DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [j for j in (_job_from_row(r) for r in rows) if j is not None]


def jobs_for_analysis(
    conn: sqlite3.Connection,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> list[Job]:
    """Deterministic, bounded job snapshot (stable ``id`` order) for analysis.

    Aggregators and diagnostics must never depend on timestamps; ``id`` order
    is stable across runs on the same database.
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [j for j in (_job_from_row(r) for r in rows) if j is not None]


def _job_from_row(row: sqlite3.Row | None) -> Job | None:
    if row is None:
        return None
    # sqlite3.Row doesn't have .get(), so convert to dict for safe access
    row_dict = dict(row)
    return Job(
        id=row["id"],
        title=row["title"] or "",
        company=row["company"] or "",
        url=row["url"] or "",
        apply_url=row["apply_url"],
        source=row["source"],
        source_type=row["source_type"],
        canonical_url=row["canonical_url"],
        location=row["location"] or "",
        country=row["country"],
        normalized_location=row["normalized_location"],
        remote_mode=row["remote_mode"] or "unknown",
        employment_type=row["employment_type"],
        date_posted=_parse_dt(row["date_posted"]),
        description=row["description"] or "",
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        salary_min_eur=row["salary_min_eur"],
        salary_max_eur=row["salary_max_eur"],
        salary_converted=bool(row["salary_converted"]),
        canonical_key=row["canonical_key"],
        status=row["status"],
        closed_at=row["closed_at"],
        discovered_at=row["discovered_at"],
        last_seen=row["last_seen"],
        last_checked=row["last_checked"],
        missing_scans=row["missing_scans"],
        raw={},
        user_status=row_dict.get("user_status", "NEW"),
        user_status_updated_at=row_dict.get("user_status_updated_at"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate_and_store(
    conn: sqlite3.Connection,
    job: Job,
    score: Score,
    scoring_version: str,
    profile_version: str,
) -> None:
    """Persist job + evaluation atomically for a freshly fetched job."""
    upsert_job(conn, job)
    record_evaluation(conn, job.id, score, scoring_version, profile_version)


def list_matched_jobs(
    conn: sqlite3.Connection,
    *,
    status: str = "active",
    limit: int = 100,
) -> list[tuple[Job, float, str, list[str]]]:
    """Return (job, total, decision, reasons) for ranked shortlist."""
    rows = conn.execute(
        """
        SELECT j.*, e.total AS e_total, e.decision AS e_decision, e.reasons_json AS e_reasons
        FROM jobs j JOIN evaluations e ON e.job_id = j.id
        WHERE j.status = ? AND e.decision <> 'reject'
        ORDER BY e.total DESC LIMIT ?
        """,
        (status, limit),
    ).fetchall()
    out: list[tuple[Job, float, str, list[str]]] = []
    for r in rows:
        job = _job_from_row(r)
        if job is None:
            continue
        reasons = json.loads(r["e_reasons"] or "[]")
        out.append((job, float(r["e_total"]), r["e_decision"] or "review", reasons))
    return out


def get_user_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Get counts of jobs per user_status for dashboard."""
    rows = conn.execute(
        """
        SELECT user_status, COUNT(*) as count
        FROM jobs
        GROUP BY user_status
        """
    ).fetchall()
    return {row["user_status"]: row["count"] for row in rows}


def get_jobs_by_user_status(
    conn: sqlite3.Connection,
    user_status: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    """Get jobs filtered by user_status with score join for sorting."""
    rows = conn.execute(
        """
        SELECT j.*, e.total AS e_total, e.decision AS e_decision
        FROM jobs j
        LEFT JOIN evaluations e ON e.job_id = j.id
        WHERE j.user_status = ?
        ORDER BY e.total DESC NULLS LAST, j.last_seen DESC
        LIMIT ? OFFSET ?
        """,
        (user_status, limit, offset),
    ).fetchall()
    return [j for j in (_job_from_row(r) for r in rows) if j is not None]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def apply_lifecycle(
    conn: sqlite3.Connection, seen_ids: set[str], freshness_days: int, close_after_missing_scans: int
) -> dict[str, int]:
    """Mark jobs stale/closed based on the current scan.

    - Jobs seen in this scan already had missing_scans reset to 0 by upsert.
    - Jobs not seen but previously active get missing_scans incremented.
    - Jobs whose last_seen is older than ``freshness_days`` become ``stale``.
    - Jobs with missing_scans >= close_after_missing_scans become ``closed``.
    Returns a counts dict for reporting.
    """
    counts = {"active": 0, "stale": 0, "closed": 0, "duplicate": 0, "seen": len(seen_ids)}

    # Identify jobs that exist and are still lifecyclable.
    rows = conn.execute(
        "SELECT id, last_checked, missing_scans FROM jobs WHERE status IN ('active','stale')"
    ).fetchall()
    cutoff = datetime.now(UTC) - timedelta(days=freshness_days)

    now = _now()
    for r in rows:
        jid = r["id"]
        if jid in seen_ids:
            continue
        last_checked = _parse_dt(r["last_checked"] or "")
        is_stale = last_checked is not None and last_checked < cutoff
        new_missing = (r["missing_scans"] or 0) + 1
        if new_missing >= close_after_missing_scans:
            conn.execute(
                "UPDATE jobs SET status='closed', closed_at=?, missing_scans=? WHERE id=?",
                (now, new_missing, jid),
            )
            counts["closed"] += 1
        elif is_stale:
            conn.execute("UPDATE jobs SET status='stale', missing_scans=? WHERE id=?", (new_missing, jid))
            counts["stale"] += 1
        else:
            conn.execute("UPDATE jobs SET missing_scans=? WHERE id=?", (new_missing, jid))
            counts["active"] += 1
    return counts


def active_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='active'").fetchone()["n"]


def stats_counts(conn: sqlite3.Connection) -> dict[str, int | str]:
    """Aggregate row counts for the stats command."""
    out: dict[str, int | str] = {}
    for status in ("active", "stale", "closed", "duplicate"):
        out[status] = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status=?", (status,)).fetchone()["n"]
    out["total_jobs"] = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    out["total_evaluations"] = conn.execute("SELECT COUNT(*) AS n FROM evaluations").fetchone()["n"]
    out["total_legend"] = "jobs_by_status:" + ",".join(
        f"{k}={v}" for k, v in out.items() if k not in ("total_jobs", "total_evaluations", "total_legend")
    )
    return out


def best_url(conn: sqlite3.Connection, job_id: str) -> str:
    row = conn.execute("SELECT url, apply_url FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return ""
    return row["apply_url"] or row["url"] or ""


def find_duplicate_of(conn: sqlite3.Connection, job: Job) -> Job | None:
    """Return the existing canonical job for a newly fetched ``job``, if any.

    A duplicate exists when another job in the DB shares the canonical_key and
    neither is closed/duplicate. When no identity key matches, the canonical
    URL is used as a secondary signal (the same posting page surfaced by a
    different aggregator), so source multiplicity never creates duplicates.
    """
    row = None
    if job.canonical_key:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE canonical_key = ? AND id <> ?
              AND status NOT IN ('closed','duplicate')
            ORDER BY first_seen ASC
            LIMIT 1
            """,
            (job.canonical_key, job.id),
        ).fetchone()
    if row is None and job.canonical_url:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE canonical_url = ? AND canonical_url IS NOT NULL AND id <> ?
              AND status NOT IN ('closed','duplicate')
            ORDER BY first_seen ASC
            LIMIT 1
            """,
            (job.canonical_url, job.id),
        ).fetchone()
    return _job_from_row(row)


def deduplicate(conn: sqlite3.Connection) -> dict[str, int]:
    """Merge rows sharing a canonical_key into the earliest-served item.

    The first-seen row is retained with merged source attribution; later rows
    are marked ``duplicate`` with a pointer to the canonical id stored in their
    raw_json metadata. Returns counts.
    """
    counts = {"groups": 0, "merged": 0}
    dup_by = conn.execute(
        """
        SELECT canonical_key, COUNT(*) AS n
        FROM jobs
        WHERE canonical_key IS NOT NULL AND status NOT IN ('closed','duplicate')
        GROUP BY canonical_key HAVING n > 1
        """
    ).fetchall()
    for row in dup_by:
        key = row["canonical_key"]
        if not key:
            continue
        counts["groups"] += 1
        members = conn.execute(
            "SELECT * FROM jobs WHERE canonical_key=? AND status NOT IN ('closed','duplicate') ORDER BY first_seen ASC",
            (key,),
        ).fetchall()
        if len(members) < 2:
            continue
        canonical_id = members[0]["id"]
        canonical_sources: list[str] = [members[0]["source"] or ""]
        for m in members[1:]:
            if m["source"] and m["source"] not in canonical_sources:
                canonical_sources.append(m["source"])
            # Merge best URL onto canonical row.
            if (m["apply_url"] or m["url"]) and not (members[0]["apply_url"] or members[0]["url"]):
                conn.execute(
                    "UPDATE jobs SET url=?, apply_url=?, raw_json=? WHERE id=?",
                    (
                        m["url"],
                        m["apply_url"],
                        json.dumps(
                            {**(json.loads(members[0]["raw_json"] or "{}")), "merged_sources": canonical_sources}
                        ),
                        canonical_id,
                    ),
                )
            conn.execute(
                "UPDATE jobs SET status='duplicate', raw_json=? WHERE id=?",
                (json.dumps({"merged_into": canonical_id}), m["id"]),
            )
            counts["merged"] += 1
        conn.execute(
            "UPDATE jobs SET raw_json=? WHERE id=?",
            (
                json.dumps({**(json.loads(members[0]["raw_json"] or "{}")), "merged_sources": canonical_sources}),
                canonical_id,
            ),
        )
    return counts


# ---------------------------------------------------------------------------
# Discovery provenance
# ---------------------------------------------------------------------------


def record_job_source(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    source_id: str,
    source_name: str | None = None,
    source_url: str | None = None,
    discovery_method: str | None = None,
    query: str | None = None,
    canonical_url: str | None = None,
) -> None:
    """Attribute a job to a discovery source (idempotent per (job, source, url)).

    Stores identifiers and method metadata only — never job/personal content.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO job_sources
            (job_id, source_id, source_name, source_url, discovery_method, query, canonical_url, discovered_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (job_id, source_id, source_name, source_url, discovery_method, query, canonical_url, _now()),
    )
    conn.execute(
        """
        UPDATE jobs SET source_count = (
            SELECT COUNT(DISTINCT source_id) FROM job_sources WHERE job_id = ?
        ) WHERE id = ?
        """,
        (job_id, job_id),
    )


def job_sources_for(conn: sqlite3.Connection, job_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Content-free provenance rows for a job (deterministic by key + id)."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    rows = conn.execute(
        """
        SELECT source_id, source_name, source_url, discovery_method, query, discovered_at
        FROM job_sources WHERE job_id = ?
        ORDER BY source_id, id LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def source_job_counts(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    """Aggregate count of distinct jobs per discovery source (aggregate-only)."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    rows = conn.execute(
        """
        SELECT source_id, COUNT(DISTINCT job_id) AS jobs_count,
               COUNT(*) AS records
        FROM job_sources
        GROUP BY source_id
        ORDER BY jobs_count DESC, source_id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def discovery_method_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Aggregate counts of records by discovery method."""
    out: dict[str, int] = {}
    for row in conn.execute(
        "SELECT discovery_method, COUNT(*) AS n FROM job_sources WHERE discovery_method IS NOT NULL GROUP BY discovery_method"
    ):
        out[row["discovery_method"]] = int(row["n"])
    return out


def canonical_url_stats(conn: sqlite3.Connection) -> dict[str, int]:
    with_url = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE canonical_url IS NOT NULL AND canonical_url <> ''"
    ).fetchone()["n"]
    return {
        "with_canonical_url": int(with_url),
        "total_jobs": conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"],
    }


def record_scan_run(
    conn: sqlite3.Connection,
    *,
    source: str,
    status: str,
    candidate_count: int,
    fetched_count: int | None = None,
    parsed_count: int | None = None,
    errors: list[str] | None = None,
    duration_ms: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO scan_runs (started_at, completed_at, status, source,
                               candidate_count, fetched_count, parsed_count,
                               errors, duration_ms)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            _now(),
            _now(),
            status,
            source,
            candidate_count,
            fetched_count,
            parsed_count,
            json.dumps(errors or []),
            duration_ms,
        ),
    )
    return int(cur.lastrowid or 0)


def average_source_duration_ms(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT AVG(duration_ms) AS avg_ms FROM scan_runs WHERE duration_ms IS NOT NULL").fetchone()
    return float(row["avg_ms"] or 0.0)


# ---------------------------------------------------------------------------
# Decisions & preferences (read/write)
# ---------------------------------------------------------------------------


def record_decision(
    conn: sqlite3.Connection,
    job_id: str,
    action: str,
    *,
    reason: str | None = None,
    decided_by: str = "cli",
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO job_decisions (job_id, action, reason, decided_by, metadata_json)
        VALUES (?,?,?,?,?)
        """,
        (job_id, action, reason, decided_by, json.dumps(metadata or {})),
    )


def recent_decisions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM job_decisions ORDER BY decided_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_preference(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM preferences WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def set_preference(
    conn: sqlite3.Connection,
    key: str,
    value: str,
    *,
    origin: str = "inferred",
    evidence: str | None = None,
    weight: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO preferences (key, value, origin, evidence, weight, updated_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, origin=excluded.origin,
            evidence=excluded.evidence, weight=excluded.weight, updated_at=excluded.updated_at
        """,
        (key, value, origin, evidence, weight, _now()),
    )


def list_preferences(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM preferences ORDER BY origin, key").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Career fit results (Slice 2, aggregate-only storage)
# ---------------------------------------------------------------------------


def save_career_fit(conn: sqlite3.Connection, job_id: str, assessment: Any) -> None:
    """Persist (or overwrite) the aggregate career-fit assessment for a job.

    Only aggregates and evidence *references* are stored — never raw JD text
    or evidence content. Idempotent: re-running fit overwrites the row.
    """
    narrative = assessment.narrative
    conn.execute(
        """
        INSERT OR REPLACE INTO career_fit
            (job_id, current_fit, career_upside, evidence_coverage,
             breakdown_json, strengths_json, gaps_json, transferable_json,
             positioning_json, risks_json, evidence_refs_json,
             knowledge_sources_json, narrative_json, llm_used, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            assessment.current_fit.score,
            assessment.career_upside.score,
            assessment.evidence_coverage,
            json.dumps(assessment.current_fit.breakdown),
            json.dumps([s.model_dump() for s in assessment.strengths]),
            json.dumps([g.model_dump() for g in assessment.gaps]),
            json.dumps([t.model_dump() for t in assessment.transferable_skills]),
            json.dumps(assessment.positioning),
            json.dumps(assessment.risks),
            json.dumps(assessment.evidence_ids_used),
            json.dumps(assessment.knowledge_sources),
            assessment.narrative.model_dump_json() if narrative is not None else None,
            1 if narrative is not None else 0,
            _now(),
        ),
    )


def get_career_fit(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM career_fit WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Career documents, evidence, reconciliation, artifacts (Slice 3)
# ---------------------------------------------------------------------------


def save_career_document(conn: sqlite3.Connection, document: Any) -> bool:
    """Persist an ingested career document; idempotent on ``document_id``.

    Returns True when a row was inserted, False when the identical document
    already existed (re-ingestion is a no-op).
    """
    sections = [
        {"section": s.section, "heading": s.heading, "text": s.text, "position": s.position, "ref": s.ref}
        for s in document.sections
    ]
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO career_documents
            (document_id, filename, source_path, mime_type, content_hash,
             size_bytes, sections_json, ingested_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            document.document_id,
            document.filename,
            document.source_path,
            document.mime_type,
            document.content_hash,
            document.size_bytes,
            json.dumps(sections, ensure_ascii=False),
            document.ingested_at,
        ),
    )
    return bool(cur.rowcount)


def get_career_document(conn: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM career_documents WHERE document_id=?", (document_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["sections"] = json.loads(row["sections_json"] or "[]")
    return out


def has_career_document(conn: sqlite3.Connection, document_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM career_documents WHERE document_id=?", (document_id,)).fetchone()
    return row is not None


def list_career_documents(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM career_documents ORDER BY ingested_at DESC, document_id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def save_career_evidence(conn: sqlite3.Connection, evidence: Any) -> bool:
    """Persist evidence; idempotent on ``evidence_id`` (INSERT OR IGNORE)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO career_evidence
            (evidence_id, claim, level, source, source_type, categories_json,
             keywords_json, confidence, normalized_fact, authority, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evidence.evidence_id,
            evidence.claim,
            evidence.level.value if hasattr(evidence.level, "value") else str(evidence.level),
            evidence.source,
            evidence.source_type,
            json.dumps(evidence.categories),
            json.dumps(evidence.keywords),
            evidence.confidence,
            evidence.normalized_fact,
            evidence.authority,
            _now(),
        ),
    )
    return bool(cur.rowcount)


def save_career_evidence_many(conn: sqlite3.Connection, evidence_items: list[Any]) -> int:
    """Persist a batch idempotently; returns the number of new rows."""
    added = 0
    for item in evidence_items:
        if save_career_evidence(conn, item):
            added += 1
    return added


def get_career_evidence(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
    offset: int = 0,
    source: str | None = None,
) -> list[dict[str, Any]]:
    where, params = "", list[Any]()
    if source:
        where = " WHERE source=?"
        params.append(source)
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM career_evidence{where} ORDER BY created_at DESC, evidence_id LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def career_evidence_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM career_evidence").fetchone()["n"]


def save_career_reconciliation(
    conn: sqlite3.Connection,
    *,
    document_id: str,
    total_facts: int,
    exact_matches: int,
    new_count: int,
    conflicts: int,
    status: str,
) -> None:
    """Checkpoint reconciliation counts (never claim content in the DB)."""
    conn.execute(
        """
        INSERT INTO career_reconciliation
            (document_id, total_facts, exact_matches, new_count, conflicts,
             has_conflicts, status, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (document_id, total_facts, exact_matches, new_count, conflicts, 1 if conflicts else 0, status, _now()),
    )


def list_career_reconciliations(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM career_reconciliation ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _next_artifact_version(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM career_artifacts WHERE job_id=?",
        (job_id,),
    ).fetchone()
    return int(row["v"]) + 1


def _insert_artifact(
    conn: sqlite3.Connection,
    artifact: Any,
    *,
    version: int,
    source: str,
    llm_used: bool,
    mapping_json: str | None,
    validation_json: str | None,
    positioning_json: str | None,
) -> None:
    base = artifact.artifact_id or "tailor"
    artifact_id = f"{base}:v{version}"
    conn.execute(
        """
        INSERT INTO career_artifacts
            (artifact_id, job_id, version, status, headline, summary, bullets_json,
             evidence_ids_json, note, source, llm_used, mapping_json,
             validation_json, positioning_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            artifact_id,
            artifact.job_id,
            version,
            artifact.status.value if hasattr(artifact.status, "value") else str(artifact.status),
            artifact.headline,
            artifact.summary,
            json.dumps([{"text": b.text, "evidence_id": b.evidence_id} for b in artifact.bullets]),
            json.dumps(artifact.evidence_ids),
            artifact.note,
            source,
            1 if llm_used else 0,
            mapping_json,
            validation_json,
            positioning_json,
            artifact.created_at,
        ),
    )
    conn.execute("DELETE FROM career_artifact_evidence WHERE artifact_id=?", (artifact_id,))
    for eid in sorted(set(artifact.evidence_ids or [])):
        conn.execute(
            "INSERT OR IGNORE INTO career_artifact_evidence (artifact_id, evidence_id) VALUES (?, ?)",
            (artifact_id, eid),
        )


def save_career_artifact(
    conn: sqlite3.Connection,
    artifact: Any,
    *,
    source: str = "deterministic",
    llm_used: bool = False,
    version: int | None = None,
    mapping_json: str | None = None,
    validation_json: str | None = None,
    positioning_json: str | None = None,
) -> int:
    """Persist an APPEND-ONLY proposal artifact version for a job.

    Each run creates a new row scoped by ``(job_id, version)`` (v5). The stored
    ``artifact_id`` is the proposal's content signature suffixed with ``:vN``
    so it stays unique across runs; ``career_artifact_evidence`` link rows are
    kept consistent with the artifact's declared evidence ids. Returns the
    version that was written.

    Version assignment is collision-safe: if two writers race on the same
    ``MAX(version)+1`` the UNIQUE index rejects one and both are retried with
    the next free version.
    """
    for _ in range(8):  # bounded; the unique index (job_id, version) is the authority
        next_version = _next_artifact_version(conn, artifact.job_id) if version is None else version
        try:
            _insert_artifact(
                conn,
                artifact,
                version=next_version,
                source=source,
                llm_used=llm_used,
                mapping_json=mapping_json,
                validation_json=validation_json,
                positioning_json=positioning_json,
            )
            return next_version
        except sqlite3.IntegrityError as exc:
            if version is not None:
                raise  # an explicit pinned version colliding is a caller error
            if "job_id" in str(exc) and "version" in str(exc):
                continue  # concurrent writer took this version; retry with the next
            raise
    raise RuntimeError(f"could not allocate an artifact version for job {artifact.job_id}")


def get_career_artifact(
    conn: sqlite3.Connection,
    job_id: str,
    version: int | None = None,
) -> dict[str, Any] | None:
    """Fetch a proposal artifact for a job (latest version by default)."""
    if version is None:
        row = conn.execute(
            "SELECT * FROM career_artifacts WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM career_artifacts WHERE job_id=? AND version=?",
            (job_id, version),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["bullets"] = json.loads(row["bullets_json"] or "[]")
    out["evidence_ids"] = json.loads(row["evidence_ids_json"] or "[]")
    out["mapping"] = json.loads(row["mapping_json"]) if row["mapping_json"] else None
    out["validation"] = json.loads(row["validation_json"]) if row["validation_json"] else None
    out["positioning"] = json.loads(row["positioning_json"]) if row["positioning_json"] else None
    return out


def list_career_artifacts(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    version_from: int | None = None,
    version_to: int | None = None,
) -> list[dict[str, Any]]:
    """All proposal artifact versions for a job, ascending."""
    clauses = ["job_id=?"]
    params: list[Any] = [job_id]
    if version_from is not None:
        clauses.append("version>=?")
        params.append(version_from)
    if version_to is not None:
        clauses.append("version<=?")
        params.append(version_to)
    rows = conn.execute(
        f"SELECT artifact_id, job_id, version, status, source, llm_used, created_at "
        f"FROM career_artifacts WHERE {' AND '.join(clauses)} ORDER BY version",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def artifact_require_approval(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    version: int | None = None,
) -> None:
    """Human gate: mark the latest (or a specific) artifact of a job
    REQUIRES_REVIEW.

    This is the only transition the CLI exposes for artefacts (the human
    explicitly opens review); approval/rejection is a later decision surface.
    """
    if version is None:
        conn.execute(
            "UPDATE career_artifacts SET status='requires_review' WHERE job_id=? "
            "AND version=(SELECT COALESCE(MAX(version),0) FROM career_artifacts WHERE job_id=?)",
            (job_id, job_id),
        )
    else:
        conn.execute(
            "UPDATE career_artifacts SET status='requires_review' WHERE job_id=? AND version=?",
            (job_id, version),
        )
