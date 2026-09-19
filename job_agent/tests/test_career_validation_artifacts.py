"""Validation + artifact status: anti-fabrication guards, deterministic."""

from pathlib import Path

from job_agent.career.artifacts import ArtifactStatus, Bullet, CVArtifact
from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id
from job_agent.career.validation import validate_artifact_claims, validate_claim

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _ev(claim: str, level: str = "documented", categories=None, source="doc-a") -> CareerEvidence:
    return CareerEvidence(
        evidence_id=evidence_id(claim, source),
        claim=claim,
        level=VerificationLevel(level),
        source=source,
        categories=categories or [],
        keywords=[],
        confidence=0.9,
    )


def _evidence():
    return [
        _ev(
            "Worked as Product Owner - Autonomous Driving at BMW Group (2024-present)",
            "verified",
            ["product", "domain", "ai", "leadership"],
        ),
        _ev("Led the design of an Automated Valet Parking feature", "documented", ["product", "domain", "ai"]),
        _ev("Reduced reporting time by 30 percent", "verified", ["program"]),
        _ev("Managed a team of 5 engineers at TUMCREATE (2015-2018)", "documented", ["leadership"]),
    ]


def test_claim_backed_by_evidence_is_supported():
    ev = _evidence()
    v = validate_claim("Product Owner for Autonomous Driving at BMW Group", ev)
    assert v.problem is None
    assert v.matched_evidence_ids


def test_fabricated_metric_is_rejected():
    ev = _evidence()
    v = validate_claim("Increased quarterly revenue by 400 percent", ev)
    assert v.problem == "numeric_unmatched"
    assert v.unmatched_numbers == ["400"]


def test_invented_claim_has_no_evidence():
    ev = _evidence()
    v = validate_claim("Led a data engineering team for a bank", ev)
    assert v.problem == "no_evidence"
    assert v.matched_evidence_ids == []


def test_artifact_validated_when_every_bullet_is_backed():
    ev = _evidence()
    art = CVArtifact(
        artifact_id="art-1",
        job_id="j-1",
        bullets=[
            Bullet(text="Product Owner for Autonomous Driving at BMW Group", evidence_id=ev[0].evidence_id),
            Bullet(text="Led design of Automated Valet Parking", evidence_id=ev[1].evidence_id),
        ],
    )
    status, verdicts = art.check_compliance(ev)
    assert status == ArtifactStatus.VALIDATED
    assert all(v.problem is None for v in verdicts)


def test_artifact_requires_review_on_unsupported_bullet():
    ev = _evidence()
    art = CVArtifact(
        artifact_id="art-2",
        job_id="j-1",
        bullets=[
            Bullet(text="Product Owner for Autonomous Driving at BMW Group", evidence_id=ev[0].evidence_id),
            Bullet(text="Drove company revenue growth to 10 million euros", evidence_id=None),
        ],
    )
    status, verdicts = art.check_compliance(ev)
    assert status == ArtifactStatus.REQUIRES_REVIEW
    assert any(v.problem == "numeric_unmatched" for v in verdicts)


def test_artifact_never_auto_approves():
    ev = _evidence()
    art = CVArtifact(artifact_id="art-3", job_id="j-1")
    status, _ = art.check_compliance(ev)
    assert status in (ArtifactStatus.VALIDATED, ArtifactStatus.REQUIRES_REVIEW)
    assert status != ArtifactStatus.APPROVED


def test_artifact_note_marks_proposal():
    art = CVArtifact(artifact_id="art-4", job_id="j-1")
    assert "NOT APPROVED" in art.note


def test_validate_artifact_claims_one_verdict_per_line():
    ev = _evidence()
    claims = ["Product Owner for Autonomous Driving at BMW Group", "Scaled revenue by 900 percent"]
    verdicts = validate_artifact_claims(claims, ev)
    assert len(verdicts) == 2
    assert verdicts[1].problem == "numeric_unmatched"


def test_missing_or_dangling_evidence_id_requires_review():
    ev = _evidence()
    art = CVArtifact(
        artifact_id="art-5",
        job_id="j-1",
        bullets=[Bullet(text="Product Owner for Autonomous Driving at BMW Group", evidence_id="nonexistent-id")],
    )
    status, _ = art.check_compliance(ev)
    assert status == ArtifactStatus.REQUIRES_REVIEW
