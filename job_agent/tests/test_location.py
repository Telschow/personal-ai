"""Tests for location classification."""

from __future__ import annotations

from job_agent.location_classifier import classify_location
from job_agent.models import Job


def test_munich_location():
    job = Job(
        id="test1",
        title="Software Engineer",
        description="Working in Munich",
        company="Example Inc",
        url="https://example.com/jobs/1",
        location="Munich, Germany",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert city == "Munich"
    assert country == "Germany"
    assert scope == "munich"
    assert score == 1.0
    assert "Munich city detected" in reason


def test_munich_region_location():
    job = Job(
        id="test2",
        title="Software Engineer",
        description="Working in Munich region",
        company="Example Inc",
        url="https://example.com/jobs/2",
        location="Munich region, Germany",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert city == "Munich"
    assert country == "Germany"
    assert scope == "munich"
    assert score == 1.0
    assert "Munich city detected" in reason


def test_germany_location():
    job = Job(
        id="test3",
        title="Software Engineer",
        description="Working in Germany",
        company="Example Inc",
        url="https://example.com/jobs/3",
        location="Germany",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert country == "Germany"
    assert scope == "germany"
    assert score == 0.6
    assert "Germany detected" in reason


def test_remote_germany_location():
    job = Job(
        id="test4",
        title="Software Engineer",
        description="Remote work in Germany",
        company="Example Inc",
        url="https://example.com/jobs/4",
        location="Remote Germany",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert country == "Germany"
    assert scope == "germany"
    assert score == 0.6
    assert "Germany detected" in reason


def test_eu_location():
    job = Job(
        id="test5",
        title="Software Engineer",
        description="Working in Europe",
        company="Example Inc",
        url="https://example.com/jobs/5",
        location="Europe",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert scope == "eu"
    assert score == 0.4
    assert "EU detected" in reason


def test_remote_eu_location():
    job = Job(
        id="test6",
        title="Software Engineer",
        description="Remote work in Europe",
        company="Example Inc",
        url="https://example.com/jobs/6",
        location="Remote Europe",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert scope == "eu"
    assert score == 0.4
    assert "EU detected" in reason


def test_international_location():
    job = Job(
        id="test7",
        title="Software Engineer",
        description="Working worldwide",
        company="Example Inc",
        url="https://example.com/jobs/7",
        location="Worldwide",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert scope == "international"
    assert score == 0.2
    assert "International detected" in reason


def test_unknown_location():
    job = Job(
        id="test8",
        title="Software Engineer",
        description="Working in unknown location",
        company="Example Inc",
        url="https://example.com/jobs/8",
        location="",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert scope == "unknown"
    assert score == 0.0
    assert "No location information found" in reason


def test_hybrid_location():
    job = Job(
        id="test9",
        title="Software Engineer",
        description="Hybrid work in Munich",
        company="Example Inc",
        url="https://example.com/jobs/9",
        location="Munich, Germany (hybrid)",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert city == "Munich"
    assert country == "Germany"
    assert scope == "munich"
    assert score == 1.0
    assert "Munich city detected" in reason


def test_remote_location():
    job = Job(
        id="test10",
        title="Software Engineer",
        description="Remote work",
        company="Example Inc",
        url="https://example.com/jobs/10",
        location="Remote",
        source="test",
        source_type="test",
    )
    city, country, scope, score, reason = classify_location(job)
    assert scope == "remote_eu"
    assert score == 0.3
    assert "Remote detected" in reason


def _job(location: str, description: str = "") -> Job:
    return Job(
        id="t",
        title="Software Engineer",
        description=description,
        company="Example Inc",
        url="https://example.com/jobs/t",
        location=location,
        source="test",
        source_type="test",
    )


def test_unknown_city_onsite_does_not_score_as_munich():
    """An unrecognised on-site city must not inherit the top location score.

    The mode fallback used to return scope `munich` with score 1.0, so a job in
    New York or Tokyo ranked as the most preferred location in the corpus.
    """
    for location in ("New York, NY", "Tokyo, Japan", "Austin, TX"):
        city, country, scope, score, reason = classify_location(_job(location, "On-site five days a week."))
        assert scope == "onsite_unspecified", location
        assert score < 1.0, location
        assert city == "Unknown", location
        assert country == "Unknown", location
        assert "city unspecified" in reason, location


def test_unknown_city_hybrid_does_not_score_as_munich_region():
    scope, score = classify_location(_job("Porto, Portugal", "Hybrid, 2 days in office"))[2:4]
    assert scope == "hybrid_unspecified"
    assert score < 0.8


def test_short_keyword_needs_word_boundary():
    """`eu` must not match inside an unrelated German word."""
    _, _, scope, score, reason = classify_location(_job("Zürich, Switzerland", "Team für Steuer und Finanzen."))
    assert scope == "unknown"
    assert score == 0.0
    assert reason == "No location information found"


def test_word_boundary_still_matches_standalone_keyword():
    _, _, scope, _, _ = classify_location(_job("Zürich, Switzerland", "Office is in the EU."))
    assert scope == "eu"


def test_every_mode_fallback_scope_is_declared():
    """No fallback may invent a scope that the tables do not define."""
    from job_agent.location_classifier import LOCATION_SCOPES, LOCATION_SCORE_WEIGHTS

    assert set(LOCATION_SCORE_WEIGHTS) == set(LOCATION_SCOPES)
    for location, description in (
        ("New York, NY", "On-site"),
        ("Porto, Portugal", "Hybrid"),
        ("Remote", "Work from home"),
    ):
        scope = classify_location(_job(location, description))[2]
        assert scope in LOCATION_SCOPES
