"""Evidence model: hierarchy, provenance, deduplication, no auto-promotion."""

from pathlib import Path

import yaml

from job_agent.career.evidence import (
    CareerEvidence,
    VerificationLevel,
    build_profile_evidence,
    classify_categories,
    evidence_id,
    level_index,
    rank_evidence,
)
from job_agent.career.profile import derive_career_profile

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "profile.yaml"


def _profile():
    with open(_PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


def _ev(claim: str, level: str, source: str = "test", eid: str | None = None) -> CareerEvidence:
    return CareerEvidence(
        evidence_id=evidence_id(claim, source) if eid is None else eid,
        claim=claim,
        level=VerificationLevel(level),
        source=source,
        categories=[],
        keywords=[],
        confidence=0.5,
    )


def test_level_index_ordering():
    assert level_index(VerificationLevel.INFERRED) < level_index(VerificationLevel.DOCUMENTED)
    assert level_index(VerificationLevel.DOCUMENTED) < level_index(VerificationLevel.VERIFIED)


def test_evidence_id_stable():
    assert evidence_id("foo", "bar") == evidence_id("foo", "bar")
    assert evidence_id("foo", "bar") != evidence_id("foo", "baz")


def test_profile_evidence_all_verified():
    career = derive_career_profile(_profile())
    ev = build_profile_evidence(_profile(), career)
    assert len(ev) > 10
    assert all(e.level == VerificationLevel.VERIFIED for e in ev)


def test_profile_evidence_covers_skills_and_experience():
    career = derive_career_profile(_profile())
    ev = build_profile_evidence(_profile(), career)
    claims = [e.claim for e in ev]
    assert any("Lists the skill" in c for c in claims)
    assert any("Worked as" in c for c in claims)


def test_profile_no_inferred_promotion():
    career = derive_career_profile(_profile())
    ev = build_profile_evidence(_profile(), career)
    categories = {cat for e in ev for cat in e.categories}
    assert "ai" in categories or "technical" in categories
    assert not any(e.level == VerificationLevel.INFERRED for e in ev)


def test_rank_evidence_preferred_order():
    items = [_ev("inferred", "inferred"), _ev("verified", "verified"), _ev("documented", "documented")]
    ranked = rank_evidence(items)
    assert [e.claim for e in ranked] == ["verified", "documented", "inferred"]


def test_rank_evidence_dedup_keeps_first():
    items = [_ev("dup claim", "verified", "test", "same"), _ev("dup claim", "inferred", "test", "same")]
    ranked = rank_evidence(items)
    assert len(ranked) == 1
    assert ranked[0].claim == "dup claim"
    assert ranked[0].level == VerificationLevel.VERIFIED


def test_rank_evidence_limit():
    items = [_ev(f"claim-{i}", "verified") for i in range(5)]
    assert len(rank_evidence(items, limit=3)) == 3


def test_classify_categories_product():
    cats = classify_categories("product roadmap stakeholder")
    assert "product" in cats


def test_classify_categories_ai():
    cats = classify_categories("machine learning autonomous agent")
    assert "ai" in cats
    assert "agentic_ai" in cats


def test_classify_categories_none_empty():
    assert classify_categories(None) == ()
    assert classify_categories("") == ()
