"""Structured job requirements: deterministic typed surface, no model calls."""

from job_agent.career.requirements import (
    MAX_CONCEPTS,
    extract_job_attributes,
)
from job_agent.models import Job


def _job(title, description, location="Munich, Germany", remote_mode="onsite"):
    return Job(
        id="001",
        source="test",
        source_type="test",
        title=title,
        company="ACME",
        url="https://example.com",
        location=location,
        description=description,
        remote_mode=remote_mode,
    )


def test_product_management_attrs():
    job = _job(
        "Product Owner - Autonomous Driving",
        "Own the roadmap, lead the backlog with stakeholders and ML teams "
        "for an autonomous driving product. English, German.",
    )
    attrs = extract_job_attributes(job)
    assert attrs.role_family == "product_management"
    assert attrs.track == "product-led"
    assert attrs.ai_relevance >= 5
    assert "German" in attrs.languages_required
    assert "English" in attrs.languages_required
    assert attrs.seniority >= 3


def test_engineering_attrs():
    job = _job(
        "Senior Software Engineer - Embedded C++",
        "Design embedded systems, write C++, real-time control, simulation.",
        remote_mode="remote",
    )
    attrs = extract_job_attributes(job)
    assert attrs.role_family == "engineering"
    assert attrs.technical_depth >= 4
    assert attrs.location_mode == "remote"


def test_leadership_title():
    job = _job("Head of Engineering", "Lead a team, manage engineers, people management.")
    attrs = extract_job_attributes(job)
    assert attrs.role_family == "technical_leadership"
    assert attrs.people_management is True
    assert attrs.leadership_scope >= 5


def test_seniority_ranked():
    low = extract_job_attributes(_job("Junior Developer", "intern graduate entry level"))
    high = extract_job_attributes(_job("VP Product", "director vp vice president"))
    assert low.seniority < high.seniority


def test_security_clearance_detected():
    attrs = extract_job_attributes(_job("Engineer", "security clearance required"))
    assert attrs.security_clearance is True


def test_concepts_bounded_and_typed():
    attrs = extract_job_attributes(
        _job("ML Engineer", "machine learning computer vision neural llm agents perception robots")
    )
    assert 0 < len(attrs.concepts) <= MAX_CONCEPTS
    assert all(isinstance(c, str) for c in attrs.concepts)


def test_industry_detected():
    attrs = extract_job_attributes(_job("Engineer", "automotive oem vehicle adas"))
    assert attrs.industry == "automotive"


def test_agentic_ai_signal():
    attrs = extract_job_attributes(_job("AI Engineer", "agentic ai foundation model agents"))
    assert attrs.agentic_ai_relevance >= 7


def test_stage_detected():
    attrs = extract_job_attributes(_job("PM", "early stage startup seed funded"))
    assert attrs.company_stage == "startup"


def test_empty_description_safe():
    attrs = extract_job_attributes(_job("", ""))
    assert attrs.role_family == "unknown"
    assert attrs.ai_relevance >= 0
    assert isinstance(attrs.languages_required, list)
