"""Slice-3 storage: documents, evidence, reconciliation, artifacts — hermetic."""

from job_agent import db
from job_agent.career.artifacts import Bullet, CVArtifact
from job_agent.career.documents import CareerDocument, ingest_document
from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "slice3.db"))
    assert db.migrations.current_version(conn) >= 4
    return conn


def _document(tmp_path) -> CareerDocument:
    path = tmp_path / "cv.txt"
    path.write_text("Experience\nProduct Owner - Autonomous Driving at Nimbus Motors, 2024-present\n", encoding="utf-8")
    return ingest_document(path)


def _evidence() -> CareerEvidence:
    claim = "Worked as Product Owner - Autonomous Driving at Nimbus Motors (2024-present)"
    return CareerEvidence(
        evidence_id=evidence_id(claim, "doc-a"),
        claim=claim,
        level=VerificationLevel.VERIFIED,
        source="doc-a",
        source_type="cv_document",
        categories=["product", "domain", "ai"],
        keywords=["autonomous driving", "nimbus"],
        confidence=0.9,
        normalized_fact="werkte als product owner - autonomous driving bij nimbus motors",
        authority="cv_document",
    )


def test_document_save_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    doc = _document(tmp_path)
    assert db.save_career_document(conn, doc) is True
    assert db.save_career_document(conn, doc) is False  # identical re-ingestion
    assert db.career_evidence_count(conn) == 0
    rows = db.list_career_documents(conn)
    assert len(rows) == 1
    assert rows[0]["content_hash"] == doc.content_hash
    conn.close()


def test_document_get_and_missing(tmp_path):
    conn = _conn(tmp_path)
    doc = _document(tmp_path)
    assert db.has_career_document(conn, doc.document_id) is False
    db.save_career_document(conn, doc)
    assert db.has_career_document(conn, doc.document_id) is True
    stored = db.get_career_document(conn, doc.document_id)
    assert stored is not None
    assert "Experience" in stored["sections"][0]["text"]
    conn.close()


def test_evidence_save_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    ev = _evidence()
    assert db.save_career_evidence(conn, ev) is True
    assert db.save_career_evidence(conn, ev) is False
    assert db.career_evidence_count(conn) == 1
    rows = db.get_career_evidence(conn)
    assert rows[0]["evidence_id"] == ev.evidence_id
    assert rows[0]["level"] == "verified"
    conn.close()


def test_evidence_batch_and_source_filter(tmp_path):
    conn = _conn(tmp_path)
    ev1 = _evidence()
    ev2 = CareerEvidence(
        evidence_id=evidence_id("Led design of Automated Valet Parking", "doc-b"),
        claim="Led design of Automated Valet Parking",
        level=VerificationLevel.DOCUMENTED,
        source="doc-b",
        categories=["product"],
        keywords=[],
        confidence=0.8,
    )
    added = db.save_career_evidence_many(conn, [ev1, ev2, ev2])
    assert added == 2
    assert len(db.get_career_evidence(conn, source="doc-a")) == 1
    conn.close()


def test_reconciliation_checkpoint_aggregate_only(tmp_path):
    conn = _conn(tmp_path)
    doc = _document(tmp_path)
    db.save_career_document(conn, doc)
    db.save_career_reconciliation(
        conn,
        document_id=doc.document_id,
        total_facts=10,
        exact_matches=7,
        new_count=2,
        conflicts=1,
        status="conflicts",
    )
    rows = db.list_career_reconciliations(conn)
    assert len(rows) == 1
    assert rows[0]["conflicts"] == 1
    assert rows[0]["has_conflicts"] == 1
    # no claim content is ever stored
    assert all("claim" not in row for row in rows)
    conn.close()


def test_artifact_save_overwrite_and_evidence_links(tmp_path):
    conn = _conn(tmp_path)
    ev = _evidence()
    db.save_career_evidence(conn, ev)
    art = CVArtifact(
        artifact_id="tailor:p:1:llm",
        job_id="p:1",
        headline="Automotive AI delivery lead",
        summary="Owned autonomous driving projects.",
        bullets=[Bullet(text="Product Owner for Autonomous Driving at Nimbus Motors", evidence_id=ev.evidence_id)],
        evidence_ids=[ev.evidence_id],
    )
    v1 = db.save_career_artifact(conn, art, source="llm")
    assert v1 == 1
    stored_v1 = db.get_career_artifact(conn, "p:1", version=1)
    assert stored_v1 is not None
    assert stored_v1["source"] == "llm"
    assert stored_v1["bullets"][0]["evidence_id"] == ev.evidence_id
    link = conn.execute(
        "SELECT evidence_id FROM career_artifact_evidence WHERE artifact_id=?",
        (stored_v1["artifact_id"],),
    ).fetchone()
    assert link is not None and link["evidence_id"] == ev.evidence_id

    # append-only: a second run creates v2, v1 keeps its own evidence links
    v2 = db.save_career_artifact(conn, art, source="deterministic")
    assert v2 == 2
    stored_v2 = db.get_career_artifact(conn, "p:1")
    assert stored_v2 is not None and stored_v2["version"] == 2
    assert stored_v2["source"] == "deterministic"
    link_v1 = conn.execute(
        "SELECT evidence_id FROM career_artifact_evidence WHERE artifact_id=?",
        (stored_v1["artifact_id"],),
    ).fetchone()
    link_v2 = conn.execute(
        "SELECT evidence_id FROM career_artifact_evidence WHERE artifact_id=?",
        (stored_v2["artifact_id"],),
    ).fetchone()
    assert link_v1 is not None and link_v2 is not None
    assert link_v2["evidence_id"] == ev.evidence_id
    versions = db.list_career_artifacts(conn, "p:1")
    assert [r["version"] for r in versions] == [1, 2]
    conn.close()


def test_artifact_require_approval_marks_review(tmp_path):
    conn = _conn(tmp_path)
    art = CVArtifact(artifact_id="tailor:p:2", job_id="p:2")
    db.save_career_artifact(conn, art)
    db.artifact_require_approval(conn, "p:2")
    stored = db.get_career_artifact(conn, "p:2")
    assert stored is not None and stored["status"] == "requires_review"
    conn.close()
