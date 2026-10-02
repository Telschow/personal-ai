"""Fit analysis: deterministic, evidence-attributed, safety notes."""

from pathlib import Path

import yaml

from job_agent.career import build_profile_evidence
from job_agent.career.evidence import (
    CareerEvidence,
    VerificationLevel,
    rank_evidence,
)
from job_agent.career.fit import (
    FitWeights,
    analyze_fit,
    capability_match,
    domain_match,
    leadership_match,
    role_family_match,
    seniority_match,
)
from job_agent.career.profile import derive_career_profile
from job_agent.career.requirements import extract_job_attributes
from job_agent.models import Job

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _profile() -> dict:
    with open(_PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


PROFILE = _profile()
CAREER = derive_career_profile(PROFILE)


def _job(description, title="Product Owner - Autonomous Driving", salary_min=None):
    return Job(
        id="j1",
        source="test",
        source_type="test",
        title=title,
        company="ACME",
        url="u",
        description=description,
        salary_min_eur=salary_min,
    )


def _evidence(extra=None, limit=100):
    ev = build_profile_evidence(PROFILE, CAREER)
    for e in extra or []:
        ev.append(e)
    return rank_evidence(ev, limit=limit)


def test_strong_fit_drives_high_score():
    attrs = extract_job_attributes(
        _job("product strategy roadmap stakeholder machine learning autonomous driving german english")
    )
    a = analyze_fit(_job(""), CAREER, attrs, _evidence())
    assert a.current_fit.score >= 0.6


def test_weak_fit_scores_low():
    attrs = extract_job_attributes(
        _job(
            "Backend Engineer - APIs, Python, AWS cloud services",
            title="Junior Backend Developer",
        )
    )
    a = analyze_fit(_job(""), CAREER, attrs, _evidence())
    assert a.current_fit.score < 0.5


def test_deterministic():
    attrs = extract_job_attributes(_job("product strategy roadmap stakeholder machine learning"))
    a1 = analyze_fit(_job(""), CAREER, attrs, _evidence())
    a2 = analyze_fit(_job(""), CAREER, attrs, _evidence())
    assert a1.current_fit.score == a2.current_fit.score
    assert a1.evidence_ids_used == a2.evidence_ids_used


def test_coverage_requires_evidence():
    attrs = extract_job_attributes(_job("machine learning computer vision autonomous driving product strategy"))
    a_empty = analyze_fit(_job(""), CAREER, attrs, [])
    assert a_empty.evidence_coverage == 0.0
    a_full = analyze_fit(_job(""), CAREER, attrs, _evidence())
    assert a_full.evidence_coverage > 0.0


def test_upside_bounded_and_tied_to_coverage():
    attrs = extract_job_attributes(
        _job("product strategy roadmap stakeholder machine learning autonomous driving german english")
    )
    a = analyze_fit(_job(""), CAREER, attrs, _evidence())
    assert 0.0 <= a.career_upside.score <= 1.0
    # Deterministic scores never depend on the number of evidence items.
    low = analyze_fit(_job(""), CAREER, attrs, _evidence(limit=5))
    assert low.current_fit.score == a.current_fit.score


def test_gaps_for_missing_concepts():
    attrs = extract_job_attributes(_job("backend engineering, APIs, microservices, cloud"))
    a = analyze_fit(_job(""), CAREER, attrs, _evidence())
    gap_dims = [g.dimension for g in a.gaps]
    assert "backend engineering" in gap_dims


def test_salary_floor_risk():
    attrs = extract_job_attributes(_job("product strategy roadmap"))
    a = analyze_fit(
        _job("", salary_min=80000),
        CAREER,
        attrs,
        _evidence(),
        salary_floor_eur=120000,
    )
    assert any(r for r in a.risks if "salary floor" in r)


def test_no_salary_floor_risk_when_unconfigured():
    """With no configured floor, the assessment must not invent one."""
    attrs = extract_job_attributes(_job("product strategy roadmap"))
    a = analyze_fit(_job("", salary_min=80000), CAREER, attrs, _evidence())
    assert not any("salary floor" in r for r in a.risks)


def test_people_management_risk():
    attrs = extract_job_attributes(_job("manage a team, lead a team"))
    # Evidence that covers product work but NOT team leadership avoids the
    # safety note being suppressed by the real profile's leadership skills.
    only_product = [
        CareerEvidence(
            evidence_id="e1",
            claim="product roadmap experience",
            level=VerificationLevel.VERIFIED,
            source="profile",
            categories=["product"],
            keywords=["product", "roadmap"],
        )
    ]
    a = analyze_fit(_job(""), CAREER, attrs, only_product)
    assert any(r for r in a.risks if "people-management" in r)


def test_evidence_coverage_document_level_modulates_credibility_only():
    # A DOCUMENTED memory still counts as coverage but never changes matches.
    doc = CareerEvidence(
        evidence_id="d1",
        claim="user leads a team of 5 people",
        level=VerificationLevel.DOCUMENTED,
        source="personal_ai_memory",
        categories=["leadership"],
        keywords=["lead", "team"],
    )
    attrs = extract_job_attributes(_job("product strategy roadmap stakeholder machine learning"))
    base = analyze_fit(_job(""), CAREER, attrs, _evidence())
    with_doc = analyze_fit(_job(""), CAREER, attrs, _evidence([doc]))
    assert base.current_fit.score == with_doc.current_fit.score
    assert any(s.evidence_ids for s in with_doc.strengths)


def test_match_functions():
    assert role_family_match(extract_job_attributes(_job("x", title="Product Owner")), CAREER) == 1.0
    assert 0 <= seniority_match(extract_job_attributes(_job("x")), CAREER) <= 1
    assert 0 <= capability_match(4, 7) <= 1
    assert domain_match(extract_job_attributes(_job("automotive")), CAREER) >= 0.6
    assert 0 <= leadership_match(extract_job_attributes(_job("x")), CAREER) <= 1


def test_default_weights_sum_to_one():
    assert abs(FitWeights().total - 1.0) < 1e-6


def test_fit_bounds_all_ranges():
    a = analyze_fit(
        _job(""),
        CAREER,
        extract_job_attributes(_job("anything")),
        [],
    )
    assert 0.0 <= a.current_fit.score <= 1.0
    assert isinstance(a.knowledge_sources, list)
    assert isinstance(a.evidence_ids_used, list)
    assert a.generated_at
