"""Tests for full CV generation and LinkedIn optimization."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from job_agent.career.cv_generation import GeneratedCV, generate_cv, render_cv_human
from job_agent.career.evidence import CareerEvidence, VerificationLevel, CareerEvidenceType, evidence_id
from job_agent.career.linkedin_optimization import LinkedInProfile, optimize_linkedin
from job_agent.career.profile import derive_career_profile
from job_agent.career.requirements import extract_job_attributes
from job_agent.models import Job
from job_agent.career.knowledge import NullCareerKnowledge


def _profile():
    with open(Path(__file__).resolve().parent.parent / "profile" / "profile.yaml") as fh:
        return yaml.safe_load(fh)


def _career():
    return derive_career_profile(_profile())


def _evidence():
    return [
        CareerEvidence(
            evidence_id=evidence_id("Worked as Product Owner - Autonomous Driving at BMW Group (2024-present)", "profile"),
            claim="Worked as Product Owner - Autonomous Driving at BMW Group (2024-present)",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.CAREER_EXPERIENCE,
            categories=["product", "domain", "ai", "leadership"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Led the design of an Automated Valet Parking feature", "profile"),
            claim="Led the design of an Automated Valet Parking feature",
            level=VerificationLevel.DOCUMENTED,
            source="profile",
            evidence_type=CareerEvidenceType.PROJECT,
            categories=["product", "domain", "ai"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Reduced reporting time by 30 percent", "profile"),
            claim="Reduced reporting time by 30 percent",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.ACHIEVEMENT,
            categories=["program"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Managed a team of 5 engineers at TUMCREATE (2015-2018)", "profile"),
            claim="Managed a team of 5 engineers at TUMCREATE (2015-2018)",
            level=VerificationLevel.DOCUMENTED,
            source="profile",
            evidence_type=CareerEvidenceType.LEADERSHIP,
            categories=["leadership"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("MSc Automotive and Combustion Engine Technology from Technical University of Munich (2018-2021)", "profile"),
            claim="MSc Automotive and Combustion Engine Technology from Technical University of Munich (2018-2021)",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.EDUCATION,
            categories=["education"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Lists the skill: Python", "profile"),
            claim="Lists the skill: Python",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.SKILL,
            categories=["technical"],
            keywords=["python"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Lists the skill: Product Management", "profile"),
            claim="Lists the skill: Product Management",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.SKILL,
            categories=["product"],
            keywords=["product management"],
        ),
        CareerEvidence(
            evidence_id=evidence_id("Lists the skill: Autonomous Driving", "profile"),
            claim="Lists the skill: Autonomous Driving",
            level=VerificationLevel.VERIFIED,
            source="profile",
            evidence_type=CareerEvidenceType.SKILL,
            categories=["domain"],
            keywords=["autonomous driving"],
        ),
    ]


def _job():
    return Job(
        id="p:1",
        title="AI Product Manager",
        company="ACME",
        url="https://example.test/jobs/1",
        description="autonomous driving product leadership",
        source="test",
        source_type="board",
    )


def test_generate_cv_creates_all_sections():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    assert isinstance(cv, GeneratedCV)
    assert cv.artifact.job_id == "p:1"
    assert cv.artifact.headline
    assert cv.artifact.summary
    assert len(cv.sections) >= 4  # At least summary, skills, experience, education

    section_names = {s.name for s in cv.sections}
    assert "Professional Summary" in section_names
    assert "Core Competencies" in section_names
    assert "Professional Experience" in section_names
    assert "Education" in section_names


def test_generate_cv_all_bullets_have_evidence():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    for bullet in cv.artifact.bullets:
        assert bullet.evidence_id is not None
        # Evidence ID should exist in our evidence
        assert any(e.evidence_id == bullet.evidence_id for e in evidence)


def test_generate_cv_manifest_includes_all_bullets():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    assert cv.manifest is not None
    assert len(cv.manifest.provenance) == len(cv.artifact.bullets)

    for prov in cv.manifest.provenance:
        assert prov.evidence_id
        assert prov.verification in ["verified", "documented", "inferred", "unknown", "candidate", "user_confirmed"]
        assert prov.sources


def test_render_cv_human_english():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")
    output = render_cv_human(cv, language="en")

    assert "AI Product Manager" in output or "Product Owner" in output
    assert "Professional Summary" in output
    assert "Core Competencies" in output
    assert "Professional Experience" in output
    assert "Education" in output
    assert "Evidence Manifest" in output


def test_render_cv_human_german():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")
    output = render_cv_human(cv, language="de")

    assert "Berufliches Profil" in output
    assert "Kernkompetenzen" in output
    assert "Berufserfahrung" in output
    assert "Ausbildung" in output


def test_optimize_linkedin_returns_recommendations():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    linkedin_profile = LinkedInProfile(
        headline="Product Owner at BMW",
        about="Experienced product owner",
        current_role="Product Owner",
        experience=[
            {"company": "BMW Group", "title": "Product Owner - Autonomous Driving", "facts": ["Led AVP project"]}
        ],
        education=[],
        skills=["Product Management", "Python"],
        certifications=[],
        projects=[],
        languages=["German", "English"],
        location="Munich",
        custom_sections={},
        profile_url="",
        last_updated="",
        source="yaml",
    )

    result = optimize_linkedin(linkedin_profile, career, evidence, cv)

    assert len(result.recommendations) >= 3
    sections = {r.section for r in result.recommendations}
    assert "headline" in sections
    assert "about" in sections
    assert "skills" in sections

    for rec in result.recommendations:
        assert rec.section
        assert rec.recommended
        assert rec.rationale
        assert rec.verification in ["verified", "documented", "candidate", "inferred", "unknown", "UNSUPPORTED"]


def test_optimize_linkedin_separates_current_vs_recommended():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    linkedin_profile = LinkedInProfile(
        headline="Product Owner at BMW",
        about="Experienced product owner",
        current_role="Product Owner",
        experience=[
            {"company": "BMW Group", "title": "Product Owner - Autonomous Driving", "facts": ["Led AVP project"]}
        ],
        education=[],
        skills=["Product Management", "Python"],
        certifications=[],
        projects=[],
        languages=["German", "English"],
        location="Munich",
        custom_sections={},
        profile_url="",
        last_updated="",
        source="yaml",
    )

    result = optimize_linkedin(linkedin_profile, career, evidence, cv)

    # Current profile should be unchanged
    assert result.current_profile.headline == "Product Owner at BMW"
    assert result.current_profile.about == "Experienced product owner"

    # Recommendations should have different values
    headline_rec = next(r for r in result.recommendations if r.section == "headline")
    assert headline_rec.current == "Product Owner at BMW"
    assert headline_rec.recommended != "Product Owner at BMW"


def test_generate_cv_with_move_type_classification():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    assert cv.move_type in [
        "direct_match",
        "adjacent_match",
        "pivot",
        "stretch",
    ]


def test_generate_cv_evidence_prioritization():
    career = _career()
    evidence = _evidence()
    attrs = extract_job_attributes(_job())

    cv = generate_cv(career, attrs, evidence, job_id="p:1")

    # All evidence IDs in bullets should be from our evidence
    for sec in cv.sections:
        for eid in sec.evidence_ids:
            assert any(e.evidence_id == eid for e in evidence)

    # Total unique evidence IDs should not exceed available
    all_ids = set()
    for sec in cv.sections:
        all_ids.update(sec.evidence_ids)
    assert len(all_ids) <= len(evidence)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])