from job_agent.models import Job
from job_agent.scoring import (
    ScoringPolicy,
    contains_negative_keyword,
    score,
    scoring_policy_from_config,
)


def policy():
    return ScoringPolicy(
        salary_min=120_000,
        salary_target=150_000,
        similarity_weight=0.25,
        ai_weight=0.20,
        compensation_weight=0.15,
        location_weight=0.10,
        leadership_weight=0.20,
        purpose_weight=0.05,
        wlb_weight=0.05,
        industries=("AI", "Defence"),
        negative_keywords=("junior",),
        target_roles=("Product Manager", "Technical Program Manager"),
    )


def profile():
    return {"skills": ["Product Management", "AI", "Product Strategy", "Leadership"]}


def test_strong_job():
    j = Job(
        id="1",
        title="Senior Product Manager AI",
        company="X",
        url="https://x",
        source="test",
        location="Munich",
        salary_max_eur=145_000,
        description="Lead AI product strategy and cross-functional teams.",
    )
    s = score(j, profile(), policy())
    assert s.total > 70 and s.decision == "strong"


def test_junior_rejected():
    j = Job(
        id="2",
        title="Junior Product Manager",
        company="X",
        url="https://x",
        source="test",
        location="Munich",
        description="AI",
    )
    s = score(j, profile(), policy())
    assert s.decision == "reject" and s.hard_fail


def test_below_floor_rejected():
    j = Job(
        id="5",
        title="Product Manager AI",
        company="Y",
        url="https://y",
        source="test",
        location="Munich",
        salary_max_eur=90_000,
        description="Lead AI product strategy.",
    )
    s = score(j, profile(), policy())
    assert s.decision == "reject"
    assert "compensation" in " ".join(s.reasons + s.gaps)


def test_unknown_salary_is_reviewable():
    j = Job(
        id="3",
        title="Product Lead AI",
        company="Y",
        url="https://y",
        source="test",
        location="Berlin",
        description="Lead AI product strategy.",
    )
    s = score(j, profile(), policy())
    assert s.decision in {"review", "strong"}
    assert any("no published compensation" in g for g in s.gaps)


def test_defence_not_penalized():
    j = Job(
        id="4",
        title="Product Lead Autonomous Systems",
        company="DefenceCo",
        url="https://d",
        source="test",
        location="Berlin",
        salary_max_eur=135_000,
        description="Lead autonomous systems and AI product strategy for defence applications.",
    )
    s = score(j, profile(), policy())
    assert s.decision != "reject" and s.total > 65


def test_above_target_gets_compensation_reason():
    j = Job(
        id="6",
        title="Product Manager AI",
        company="Z",
        url="https://z",
        source="test",
        location="Munich",
        salary_min_eur=160_000,
        description="Lead AI product strategy.",
    )
    s = score(j, profile(), policy())
    assert any("≥ target" in r for r in s.reasons)


def test_negative_keyword_is_word_boundary_not_substring():
    assert contains_negative_keyword("Product Manager (f/m/d) with internship focus", "intern") is False
    assert contains_negative_keyword("international product manager", "intern") is False
    assert contains_negative_keyword("NPI manager - international product lifecycle", "intern") is False
    assert contains_negative_keyword("Junior Product Manager", "junior")
    assert contains_negative_keyword("Software Engineering Intern", "intern") is True
    assert contains_negative_keyword("working student, product management", "working student") is True


def test_international_job_not_hard_rejected_for_intern():
    j = Job(
        id="int-1",
        title="Product Manager (f/m/d)",
        company="Munich Electrification",
        url="https://x",
        source="jsonld",
        location="Munich",
        description="As an international product manager you coordinate our global product line.",
        salary_max_eur=150_000,
    )
    p = ScoringPolicy(
        salary_min=120_000,
        salary_target=150_000,
        similarity_weight=0.25,
        ai_weight=0.20,
        compensation_weight=0.15,
        location_weight=0.10,
        leadership_weight=0.20,
        purpose_weight=0.05,
        wlb_weight=0.05,
        industries=("AI", "Defence"),
        negative_keywords=("intern", "junior"),
        target_roles=("Product Manager", "Technical Program Manager"),
    )
    s = score(j, profile(), p)
    assert s.decision != "reject"
    assert not any("intern" in r for r in s.reasons)


def test_internship_job_rejected_for_intern():
    j = Job(
        id="int-2",
        title="Product Management Intern",
        company="X",
        url="https://x",
        source="test",
        location="Munich",
        description="Six-month internship for students.",
    )
    p = ScoringPolicy(
        salary_min=120_000,
        salary_target=150_000,
        similarity_weight=0.25,
        ai_weight=0.20,
        compensation_weight=0.15,
        location_weight=0.10,
        leadership_weight=0.20,
        purpose_weight=0.05,
        wlb_weight=0.05,
        industries=("AI", "Defence"),
        negative_keywords=("intern", "junior"),
        target_roles=("Product Manager", "Technical Program Manager"),
    )
    s = score(j, profile(), p)
    assert s.decision == "reject" and s.hard_fail


def test_config_policy_mapping():
    cfg = {
        "jobs": {"salary": {"minimum_eur": 120000, "target_eur": 150000}},
        "ranking": {"similarity_weight": 0.3, "ai_relevance_weight": 0.2},
        "search": {"target_roles": ["Product Manager"], "keywords_negative": ["junior"]},
        "industries_preferred": ["AI"],
    }
    p = scoring_policy_from_config(cfg)
    assert p.version  # deterministic version string
    assert p.salary_min == 120_000 and p.salary_target == 150_000
    assert p.version == scoring_policy_from_config(cfg).version


def test_confidence_and_breakdown_present():
    j = Job(
        id="7",
        title="Product Manager AI",
        company="Z",
        url="https://z",
        source="test",
        location="Munich",
        salary_max_eur=145_000,
        description="Lead AI product strategy.",
    )
    s = score(j, profile(), policy())
    assert 0 < s.confidence <= 1
    assert set(s.breakdown) >= {
        "similarity",
        "ai_relevance",
        "compensation",
        "location",
        "leadership",
        "purpose",
        "wlb",
    }
