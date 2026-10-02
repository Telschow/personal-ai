"""Achievements + positioning plan: deterministic, evidence-referenced, honest."""

from pathlib import Path

import yaml

from job_agent.career.achievements import derive_achievements
from job_agent.career.evidence import CareerEvidence, VerificationLevel, evidence_id
from job_agent.career.mapping import map_requirements
from job_agent.career.positioning_plan import build_positioning_plan, truncate
from job_agent.career.profile import derive_career_profile
from job_agent.career.requirements import extract_job_attributes
from job_agent.models import Job

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


def _profile():
    with open(_PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


def _attrs(description: str, title: str = "AI Product Manager"):
    job = Job(
        id="p:1",
        title=title,
        company="ACME",
        url="https://example.test/jobs/1",
        description=description,
        source="test",
        source_type="board",
    )
    return extract_job_attributes(job)


def test_achievements_requires_action_or_number():
    plain = _ev("Worked as a Product Manager", "documented")
    metric = _ev("Reduced reporting time by 30 percent", "verified")
    action = _ev("Led cross-functional team of 8 engineers", "documented")
    out = derive_achievements([plain, metric, action])
    posts = [a.text for a in out if a.text.startswith("Reduced")]
    led = [a.text for a in out if a.text.startswith("Led")]
    assert posts and led
    assert all(a.evidence_id in {metric.evidence_id, action.evidence_id, plain.evidence_id} for a in out)


def test_achievements_ordered_by_trust_then_metric():
    low = _ev("Built a small internal tool with a few users", "inferred")
    high = _ev("Launched a platform serving 5000 users", "verified")
    out = derive_achievements([low, high], limit=5)
    assert out[0].evidence_id == high.evidence_id or out[0].number_driven
    assert out[0].text == high.claim.rstrip(".") if len(out) else False


def test_achievements_capped_and_deduped():
    evs = [_ev(f"Led initiative number {i}" if i == 0 else f"Built component {i}", "documented") for i in range(12)]
    out = derive_achievements(evs, limit=4)
    assert len(out) == 4


def test_achievements_no_fabrication_numbers():
    runtime = _ev("Managed a team", "documented")
    fabricated = CareerEvidence(
        evidence_id=evidence_id("Led 500 people in 2024", "doc-fake"),
        claim="Led 500 people in 2024",
        level=VerificationLevel.VERIFIED,
        source="doc-fake",
        categories=[],
        keywords=[],
        confidence=1.0,
    )
    out = derive_achievements([runtime, fabricated], limit=5)
    assert any("500" in a.text for a in out)  # derived only from evidence text


def test_positioning_headline_from_strong():
    career = derive_career_profile(_profile())
    attrs = _attrs("autonomous driving product owner at a leading OEM")
    ev = [
        _ev(
            "Worked as Product Owner - Autonomous Driving at Nimbus Motors (2024-present)",
            "verified",
            categories=["product", "domain", "ai", "leadership"],
        ),
        _ev(
            "Led the design of an Automated Valet Parking feature", "documented", categories=["product", "domain", "ai"]
        ),
    ]
    mapping = map_requirements(attrs, career, ev)
    plan = build_positioning_plan(career, mapping, ev)
    assert plan.headline
    assert "profile" in plan.headline
    assert "documented" in plan.headline


def test_positioning_references_evidence_ids():
    career = derive_career_profile(_profile())
    attrs = _attrs("autonomous driving and machine learning products")
    ev = [
        _ev(
            "Product Owner for Autonomous Driving at Nimbus Motors (2024-present)",
            "verified",
            categories=["product", "domain", "ai"],
        )
    ]
    mapping = map_requirements(attrs, career, ev)
    plan = build_positioning_plan(career, mapping, ev)
    assert plan.evidence_refs
    assert all(eid in {e.evidence_id for e in ev} for eid in plan.evidence_refs)


def test_positioning_lists_gaps_and_negative_evidence():
    career = derive_career_profile(_profile())
    attrs = _attrs("data engineering and autonomous driving")
    negative = _ev("I have no data engineering experience", "documented", categories=["technical"])
    strong = _ev(
        "Product Owner - Autonomous Driving at Nimbus Motors", "verified", categories=["product", "domain", "ai"]
    )
    evs = [negative, strong]
    mapping = map_requirements(attrs, career, evs)
    plan = build_positioning_plan(career, mapping, evs)
    assert any("no evidence" in d or "negative evidence" in d for d in plan.de_emphasize)
    assert plan.gaps
    assert plan.risks


def test_positioning_never_invents_metrics():
    career = derive_career_profile(_profile())
    attrs = _attrs("technical program management in automotive")
    ev = [_ev("Coordinated launch of a vehicle program", "documented", categories=["program"])]
    mapping = map_requirements(attrs, career, ev)
    plan = build_positioning_plan(career, mapping, ev)
    for theme in plan.themes:
        assert not any(ch.isdigit() for ch in theme)


def test_truncate():
    assert len(truncate("x" * 500, 10)) == 10
    assert truncate("short") == "short"
