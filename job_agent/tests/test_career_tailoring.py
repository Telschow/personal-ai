"""Tailoring orchestration: WYSIWYG deterministic + fail-closed LLM path."""

from pathlib import Path

import yaml

from job_agent.career.artifacts import ArtifactStatus
from job_agent.career.cv_llm import CvProposal
from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id
from job_agent.career.profile import derive_career_profile
from job_agent.career.tailoring import TailorResult, compact_for_prompt, tailor

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _profiled_career():
    with open(_PROFILE_PATH) as fh:
        return derive_career_profile(yaml.safe_load(fh))


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


class _FakeCvClient:
    def __init__(self, proposal: CvProposal | None):
        self._proposal = proposal
        self.called = False

    def generate_proposal(self, *, job, evidence_items, evidence_ids):
        self.called = True
        return self._proposal


def test_tailor_deterministic_artifact_is_fully_evidence_backed():
    career = _profiled_career()
    evidence = _evidence()
    from job_agent.career.requirements import extract_job_attributes
    from job_agent.models import Job

    job = Job(
        id="p:1",
        title="AI Product Manager",
        company="ACME",
        url="https://example.test/jobs/1",
        description="autonomous driving product leadership",
        source="test",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    result = tailor(career, attrs, evidence, job_id="p:1")
    assert isinstance(result, TailorResult)
    assert result.source == "deterministic"
    assert result.status == ArtifactStatus.VALIDATED
    assert result.artifact.note == "PROPOSAL - NOT APPROVED"
    for bullet in result.artifact.bullets:
        assert bullet.evidence_id in {e.evidence_id for e in evidence}


def test_tailor_llm_proposal_is_revalidated():
    career = _profiled_career()
    evidence = _evidence()
    from job_agent.career.requirements import extract_job_attributes
    from job_agent.models import Job

    job = Job(
        id="p:2",
        title="AI PM",
        company="ACME",
        url="https://example.test/jobs/2",
        description="autonomous driving",
        source="test",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    proposal = CvProposal(
        headline="Automotive AI delivery lead",
        summary="Owned autonomous driving projects end-to-end.",
        bullets=[
            {"text": "Product Owner for Autonomous Driving at BMW Group", "evidence_id": evidence[0].evidence_id},
        ],
    )
    client = _FakeCvClient(proposal)
    result = tailor(career, attrs, evidence, job_id="p:2", client=client)
    assert client.called
    assert result.source == "llm"
    assert result.status == ArtifactStatus.VALIDATED
    assert result.artifact.headline == "Automotive AI delivery lead"


def test_tailor_llm_failure_falls_back_to_deterministic():
    career = _profiled_career()
    evidence = _evidence()
    from job_agent.career.requirements import extract_job_attributes
    from job_agent.models import Job

    job = Job(
        id="p:3",
        title="AI PM",
        company="ACME",
        url="https://example.test/jobs/3",
        description="autonomous driving",
        source="test",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    client = _FakeCvClient(None)
    result = tailor(career, attrs, evidence, job_id="p:3", client=client)
    assert client.called
    assert result.source == "deterministic"
    assert result.status == ArtifactStatus.VALIDATED


def test_tailor_never_returns_unvalidated_proposal():
    career = _profiled_career()
    evidence = _evidence()
    from job_agent.career.requirements import extract_job_attributes
    from job_agent.models import Job

    job = Job(
        id="p:4",
        title="AI PM",
        company="ACME",
        url="https://example.test/jobs/4",
        description="autonomous driving",
        source="test",
        source_type="board",
    )
    attrs = extract_job_attributes(job)
    bad = CvProposal(
        headline="h",
        summary="s",
        bullets=[{"text": "Single-handedly generated 10 million in revenue", "evidence_id": "zzz"}],
    )
    client = _FakeCvClient(bad)
    result = tailor(career, attrs, evidence, job_id="p:4", client=client)
    assert client.called
    # the fabricated, unbacked proposal must NOT reach the user
    assert "10 million" not in result.artifact.headline
    assert "10 million" not in result.artifact.summary
    assert all("revenue" not in b.text for b in result.artifact.bullets)


def test_compact_for_prompt_is_bounded_and_id_only():
    evidence = _evidence() + [_ev(f"Extra claim number {i}", "inferred") for i in range(20)]
    items = compact_for_prompt(evidence, limit=5)
    assert len(items) <= 5
    for item in items:
        assert set(item) == {"evidence_id", "claim", "level", "source_type"}
