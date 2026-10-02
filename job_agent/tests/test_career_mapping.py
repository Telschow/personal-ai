"""Requirement→capability mapping: coverage levels, confidence, GAP vs
TRANSFERABLE, NEGATIVE vs NO EVIDENCE, evidence ids everywhere."""

from pathlib import Path

import yaml

from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id
from job_agent.career.mapping import CoverageLevel, map_requirements
from job_agent.career.profile import derive_career_profile
from job_agent.career.requirements import extract_job_attributes
from job_agent.models import Job

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _profile():
    with open(_PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


def _job(description: str, title: str = "AI Product Manager") -> Job:
    return Job(
        id="m:1",
        title=title,
        company="ACME",
        url="https://example.test/jobs/1",
        description=description,
        source="test",
        source_type="board",
    )


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


def _attrs(description: str, title: str = "AI Product Manager"):
    career = derive_career_profile(_profile())
    job = _job(description, title)
    return extract_job_attributes(job), derive_career_profile(_profile()) or career


def test_mapping_returns_one_row_per_concept():
    attrs, career = _attrs("autonomous driving, machine learning, stakeholder management")
    maps = map_requirements(attrs, career, [])
    assert len(maps) == len(attrs.concepts)
    assert len(maps) > 0


def test_mapping_empty_concepts_empty():
    career = derive_career_profile(_profile())
    job = _job("some role here")
    attrs = extract_job_attributes(job)
    # concepts are derived from keywords; a totally neutral posting can be empty
    if not attrs.concepts:
        assert map_requirements(attrs, career, []) == []


def test_mapping_direct_evidence_is_strong():
    attrs, career = _attrs("autonomous driving product owner")
    ev = _ev(
        "Worked as Product Owner - Autonomous Driving at Nimbus Motors (2024-present)",
        "verified",
        categories=["product", "domain", "ai", "leadership"],
    )
    maps = map_requirements(attrs, career, [ev])
    dm = [m for m in maps if m.capability == "autonomous_driving" and m.evidence_ids]
    assert dm
    assert dm[0].coverage == CoverageLevel.STRONG
    assert ev.evidence_id in dm[0].evidence_ids
    assert not dm[0].negative_evidence


def test_mapping_candidate_evidence_is_partial():
    attrs, career = _attrs("machine learning systems")
    ev = _ev("Learned machine learning in university", "inferred", categories=["ai"])
    maps = map_requirements(attrs, career, [ev])
    dm = [m for m in maps if m.capability == "ai_systems" and m.evidence_ids]
    assert dm
    assert dm[0].coverage == CoverageLevel.PARTIAL


def test_mapping_negative_evidence_is_gap_not_empty():
    attrs, career = _attrs("data engineering")
    ev = _ev("No experience with data engineering at all", "documented", categories=["technical"])
    maps = map_requirements(attrs, career, [ev])
    dm = [m for m in maps if m.capability == "data_engineering"]
    assert dm
    assert dm[0].coverage == CoverageLevel.GAP
    assert dm[0].negative_evidence is True
    assert ev.evidence_id in dm[0].evidence_ids


def test_mapping_transferable_distinct_from_gap():
    attrs, career = _attrs("team leadership and backend engineering for AI product")
    adjacent = _ev(
        "Led cross-functional stakeholder workshops", "documented", categories=["communication", "leadership"]
    )
    maps = map_requirements(attrs, career, [adjacent])
    tl = [m for m in maps if m.capability == "team_leadership" and m.evidence_ids]
    assert tl and tl[0].coverage == CoverageLevel.TRANSFERABLE
    # backend_engineering shares no adjacent category with the evidence and has
    # no direct evidence → GAP, and the two outcomes are visibly different.
    gaps = [m for m in maps if m.coverage == CoverageLevel.GAP]
    assert any(m.capability == "backend_engineering" for m in gaps)
    trans = [m for m in maps if m.coverage == CoverageLevel.TRANSFERABLE]
    assert trans


def test_mapping_every_mapping_carries_evidence_ids_or_is_gap():
    attrs, career = _attrs("autonomous driving, backend engineering, data engineering")
    ev = _ev(
        "Product Owner for Automated Valet Parking at Nimbus Motors",
        "verified",
        categories=["product", "domain", "ai", "leadership"],
    )
    maps = map_requirements(attrs, career, [ev])
    for m in maps:
        if m.coverage in (CoverageLevel.GAP, CoverageLevel.UNKNOWN):
            assert m.evidence_ids == []
        else:
            assert m.evidence_ids


def test_mapping_unknown_when_profile_sparse():
    career = derive_career_profile({})  # empty profile → sparse
    attrs, _ = _attrs("machine learning for robotics", "ML Engineer")
    maps = map_requirements(attrs, career, [])
    assert maps
    assert all(m.coverage == CoverageLevel.UNKNOWN for m in maps)


def test_mapping_deterministic_ordering():
    attrs, career = _attrs("autonomous driving, machine learning, agile")
    ev = _ev("Worked on agile autonomous driving", "verified", categories=["product", "domain", "ai", "program"])
    a = map_requirements(attrs, career, [ev])
    b = map_requirements(attrs, career, [ev])
    assert [(m.requirement, m.coverage, tuple(m.evidence_ids)) for m in a] == [
        (m.requirement, m.coverage, tuple(m.evidence_ids)) for m in b
    ]


def test_mapping_confidence_bounded():
    career = derive_career_profile(_profile())
    attrs, _ = _attrs("autonomous driving")
    ev = _ev(
        "Worked as Product Owner - Autonomous Driving at Nimbus Motors (2024-present)",
        "verified",
        categories=["product", "domain", "ai"],
    )
    maps = map_requirements(attrs, career, [ev])
    for m in maps:
        assert 0.0 <= m.confidence <= 1.0
