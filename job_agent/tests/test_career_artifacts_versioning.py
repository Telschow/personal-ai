"""Artifact versioning: V5 append-only guarantees.

Tests migration V4→V5 preserves existing data, append-only saves create
versioned artifacts, list/get/latest semantics are correct, and legacy
rows (missing version column) default to version 1.
"""

from __future__ import annotations

import json
import sqlite3

from job_agent import db, migrations
from job_agent.career.artifacts import Bullet, CVArtifact
from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id
from job_agent.career.mapping import CoverageLevel, RequirementMap


def _conn(tmp_path) -> sqlite3.Connection:
    conn = db.connect(str(tmp_path / "v5.db"))
    assert migrations.current_version(conn) >= 5
    return conn


def _ev(claim: str, eid: str | None = None) -> CareerEvidence:
    return CareerEvidence(
        evidence_id=eid or evidence_id(claim, "doc-a"),
        claim=claim,
        level=VerificationLevel.VERIFIED,
        source="doc-a",
        source_type="cv_document",
        categories=["product", "domain"],
        keywords=[],
        confidence=0.9,
    )


def _art(conn, job_id: str = "p:1", artifact_id: str = "tailor:p:1") -> CVArtifact:
    ev_a = _ev("Worked as Product Owner - Autonomous Driving at BMW Group (2024-present)", "ev-a")
    ev_b = _ev("Led design of Automated Valet Parking feature", "ev-b")
    db.save_career_evidence(conn, ev_a)
    db.save_career_evidence(conn, ev_b)
    return CVArtifact(
        artifact_id=artifact_id,
        job_id=job_id,
        headline="Automotive AI delivery lead",
        summary="Owned autonomous driving projects.",
        bullets=[
            Bullet(text="Product Owner for Autonomous Driving at BMW Group", evidence_id="ev-a"),
            Bullet(text="Led design of Automated Valet Parking feature", evidence_id="ev-b"),
        ],
        evidence_ids=["ev-a", "ev-b"],
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_v4_to_v5_preserves_data(tmp_path):
    """Migrating an existing V4 database retains rows and columns."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Apply only migrations 1..4 (simulating a pre-registration v4 database)
    for fn in migrations._MIGRATIONS[:4]:
        conn.execute("BEGIN")
        try:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {migrations._MIGRATIONS.index(fn) + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    assert migrations.current_version(conn) == 4
    # insert a v4-era artifact the old way (plain per-job row, no version col)
    conn.execute(
        "INSERT INTO career_artifacts (artifact_id,job_id,status,headline,summary,"
        "bullets_json,evidence_ids_json,note,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tailor:p:9", "p:9", "draft", "h", "s", "[]", "[]", "", "llm", "2025-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    # now open through our normal path → migrates to the current schema version
    conn = db.connect(path)
    assert migrations.current_version(conn) == 7
    row = conn.execute("SELECT * FROM career_artifacts WHERE job_id='p:9'").fetchone()
    assert row is not None
    assert row["version"] == 1  # legacy row defaults to version 1
    assert row["llm_used"] == 0
    assert row["mapping_json"] is None
    assert row["validation_json"] is None
    conn.close()


def test_v5_adds_version_column(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    db.save_career_artifact(conn, art)
    row = conn.execute("SELECT version FROM career_artifacts WHERE job_id='p:1'").fetchone()
    assert row is not None
    assert row["version"] == 1
    conn.close()


def test_v5_unique_index_enforces_job_version(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    db.save_career_artifact(conn, art)
    # Inserting another version with same (job_id, version) violates UNIQUE
    try:
        conn.execute(
            "INSERT INTO career_artifacts (artifact_id,job_id,version,status,headline,"
            "summary,bullets_json,evidence_ids_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("dup", "p:1", 1, "draft", "", "", "[]", "[]", "2025-01-01T00:00:00"),
        )
        conn.commit()
        raise AssertionError("Expected IntegrityError")
    except sqlite3.IntegrityError:
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Append-only save
# ---------------------------------------------------------------------------


def test_double_tailor_creates_two_versions(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    v1 = db.save_career_artifact(conn, art, source="llm")
    v2 = db.save_career_artifact(conn, art, source="deterministic")
    assert v1 == 1
    assert v2 == 2
    all_arts = db.list_career_artifacts(conn, "p:1")
    assert len(all_arts) == 2
    assert all_arts[0]["version"] == 1
    assert all_arts[1]["version"] == 2
    conn.close()


def test_save_returns_version_number(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    v = db.save_career_artifact(conn, art)
    assert v == 1
    stored = db.get_career_artifact(conn, "p:1")
    assert stored is not None and stored["version"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# get / list / latest
# ---------------------------------------------------------------------------


def test_get_latest_returns_highest_version(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn), source="llm")
    db.save_career_artifact(conn, _art(conn), source="deterministic")
    latest = db.get_career_artifact(conn, "p:1")
    assert latest is not None
    assert latest["version"] == 2
    assert latest["source"] == "deterministic"
    conn.close()


def test_get_specific_version(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn), source="llm")
    db.save_career_artifact(conn, _art(conn), source="deterministic")
    v1 = db.get_career_artifact(conn, "p:1", version=1)
    v2 = db.get_career_artifact(conn, "p:1", version=2)
    assert v1 is not None and v1["source"] == "llm"
    assert v2 is not None and v2["source"] == "deterministic"
    conn.close()


def test_get_nonexistent_returns_none(tmp_path):
    conn = _conn(tmp_path)
    assert db.get_career_artifact(conn, "no-such-job") is None
    assert db.get_career_artifact(conn, "no-such-job", version=1) is None
    conn.close()


def test_list_ascending_order(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn), source="llm")
    db.save_career_artifact(conn, _art(conn), source="deterministic")
    rows = db.list_career_artifacts(conn, "p:1")
    assert [r["version"] for r in rows] == [1, 2]
    conn.close()


def test_list_version_filter(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn), source="llm")
    db.save_career_artifact(conn, _art(conn), source="deterministic")
    rows = db.list_career_artifacts(conn, "p:1", version_from=2)
    assert len(rows) == 1 and rows[0]["version"] == 2
    rows = db.list_career_artifacts(conn, "p:1", version_to=1)
    assert len(rows) == 1 and rows[0]["version"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


def test_snapshot_with_mapping_and_validation(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    mapping = [
        RequirementMap(
            requirement="Deliver autonomous driving products",
            capability="autonomous_driving",
            coverage=CoverageLevel.STRONG,
            confidence=0.9,
            evidence_ids=["ev-a"],
            layer="deterministic",
            reasoning="Direct match",
        )
    ]
    mapping_json = json.dumps([m.model_dump(mode="json") for m in mapping])
    v = db.save_career_artifact(conn, art, source="llm", llm_used=True, mapping_json=mapping_json)
    stored = db.get_career_artifact(conn, "p:1", version=v)
    assert stored is not None
    assert stored["llm_used"] == 1
    assert stored["mapping"] is not None
    assert stored["mapping"][0]["requirement"] == "Deliver autonomous driving products"
    conn.close()


def test_artifact_evidence_links_per_version(tmp_path):
    conn = _conn(tmp_path)
    art = _art(conn)
    db.save_career_artifact(conn, art, source="llm")
    db.save_career_artifact(conn, art, source="deterministic")
    v1_stored = db.get_career_artifact(conn, "p:1", version=1)
    v2_stored = db.get_career_artifact(conn, "p:1", version=2)
    for sid in [v1_stored["artifact_id"], v2_stored["artifact_id"]]:
        links = conn.execute("SELECT evidence_id FROM career_artifact_evidence WHERE artifact_id=?", (sid,)).fetchall()
        assert len(links) == 2
    conn.close()


# ---------------------------------------------------------------------------
# require_approval
# ---------------------------------------------------------------------------


def test_require_approval_marks_latest(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn))
    db.save_career_artifact(conn, _art(conn))
    db.artifact_require_approval(conn, "p:1")
    latest = db.get_career_artifact(conn, "p:1")
    assert latest is not None and latest["status"] == "requires_review"
    v1 = db.get_career_artifact(conn, "p:1", version=1)
    assert v1 is not None and v1["status"] == "draft"
    conn.close()


def test_require_approval_marks_specific_version(tmp_path):
    conn = _conn(tmp_path)
    db.save_career_artifact(conn, _art(conn))
    db.save_career_artifact(conn, _art(conn))
    db.artifact_require_approval(conn, "p:1", version=1)
    v1 = db.get_career_artifact(conn, "p:1", version=1)
    assert v1 is not None and v1["status"] == "requires_review"
    latest = db.get_career_artifact(conn, "p:1")
    assert latest is not None and latest["version"] == 2 and latest["status"] == "draft"
    conn.close()
