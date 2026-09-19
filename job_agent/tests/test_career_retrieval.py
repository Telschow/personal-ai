"""Retrieval plan: templates over concepts, never raw JD text; bounded I/O."""

from pathlib import Path

import yaml

from job_agent.career import extract_job_attributes
from job_agent.career.evidence import VerificationLevel
from job_agent.career.knowledge import CareerEvidence, NullCareerKnowledge
from job_agent.career.profile import derive_career_profile
from job_agent.career.retrieval import (
    MAX_QUERIES,
    build_retrieval_plan,
    collect_evidence,
    compact_evidence,
    concept_coverage,
)
from job_agent.models import Job

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _profile() -> dict:
    with open(_PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


def _career():
    return derive_career_profile(_profile())


def _job(description, title="Product Owner - Autonomous Driving"):
    return Job(
        id="j1",
        source="test",
        source_type="test",
        title=title,
        company="ACME",
        url="u",
        description=description,
    )


def _attrs(description):
    return extract_job_attributes(_job(description))


def test_plan_uses_templates_not_raw_jd():
    career = _career()
    attrs = _attrs("machine learning computer vision agents parking AI systems")
    plan = build_retrieval_plan(career, attrs)
    assert plan
    assert all(len(q) <= 120 for q in plan)
    assert len(plan) <= MAX_QUERIES
    for q in plan:
        # The query must be a generic template, never raw JD text.
        assert "machine learning" not in q.lower() or "skills in" in q
    assert "concept" not in plan[0]  # templates must be concretized


def test_plan_bounded_and_deterministic():
    career = _career()
    attrs = _attrs("ai agent foundation model, ml, computer vision, llm, perception, robotics")
    p1 = build_retrieval_plan(career, attrs)
    p2 = build_retrieval_plan(career, attrs)
    assert p1 == p2
    assert len(p1) <= MAX_QUERIES


def test_concept_coverage_matches_keywords():
    ev = [
        CareerEvidence(
            evidence_id="e1",
            claim="worked on autonomous driving perception",
            level=VerificationLevel.VERIFIED,
            source="profile",
            categories=["ai", "domain"],
            keywords=["autonomous", "perception"],
        )
    ]
    mapping = concept_coverage(ev, ["autonomous_driving", "ai_systems", "product_strategy"])
    assert mapping["autonomous_driving"]
    assert mapping["ai_systems"]
    assert not mapping["product_strategy"]


def test_concept_coverage_capped_and_deduped():
    ev = [
        CareerEvidence(
            evidence_id=f"e{i}",
            claim="machine learning experience",
            level=VerificationLevel.DOCUMENTED,
            source=f"s{i}",
            categories=["ai"],
            keywords=[],
        )
        for i in range(10)
    ]
    mapping = concept_coverage(ev, ["ai_systems"])
    assert len(mapping["ai_systems"]) <= 4


def test_collect_evidence_null_knowledge_no_crash():
    career = _career()
    attrs = _attrs("product strategy roadmap stakeholders")
    plan = build_retrieval_plan(career, attrs)
    ev = collect_evidence(
        _profile(),
        career,
        NullCareerKnowledge(),
        plan,
        attrs,
    )
    assert ev
    assert all(isinstance(e, CareerEvidence) for e in ev)


def test_compact_evidence_bounded():
    career = _career()
    attrs = _attrs("product strategy roadmap stakeholders")
    plan = build_retrieval_plan(career, attrs)
    ev = collect_evidence(
        _profile(),
        career,
        NullCareerKnowledge(),
        plan,
        attrs,
    )
    items, ids = compact_evidence(ev, max_chars=400)
    assert items
    assert ids
    total = sum(len(i["claim"]) for i in items)
    assert total <= 400 + 30  # truncated-tail slack
    assert all("evidence_id" in i for i in items)
    assert all("claim" in i for i in items)


def test_compact_evidence_never_contains_secrets_fields():
    items, _ = compact_evidence(
        [
            CareerEvidence(
                evidence_id="e1",
                claim="plain claim",
                level=VerificationLevel.VERIFIED,
                source="profile",
                raw={"password": "hunter2"},
            )
        ],
    )
    # raw is not propagated into the compact payload at all
    assert all("raw" not in i for i in items)
