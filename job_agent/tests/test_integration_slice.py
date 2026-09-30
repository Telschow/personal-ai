"""Integration tests for role and location classification."""

from __future__ import annotations

from job_agent.career_direction import CareerDirection
from job_agent.models import Job
from job_agent.normalizer import normalize_job
from job_agent.role_archetypes import RoleArchetype


def test_full_normalization_and_classification():
    # Test a technical product manager in Munich
    job = Job(
        id="test1",
        title="Senior Technical Product Manager",
        description="Leading product strategy for autonomous driving systems in Munich",
        company="Example Corp",
        url="https://example.com/jobs/test1",
        location="Munich, Germany",
        source="test",
        source_type="test",
    )

    normalized = normalize_job(job, source_type="ats_board")

    # Check role classification
    assert RoleArchetype.TECHNICAL_PRODUCT in normalized.role_archetypes
    assert RoleArchetype.AUTONOMOUS_SYSTEMS in normalized.role_archetypes
    assert normalized.role_family == "Product Management"
    assert normalized.role_classification_confidence >= 0.9
    assert "technical product manager" in normalized.role_classification_reason
    assert normalized.career_direction == CareerDirection.DIRECT_MATCH

    # Check location classification
    assert normalized.location_city == "Munich"
    assert normalized.location_country == "Germany"
    assert normalized.location_scope == "munich"
    assert normalized.location_score == 1.0
    assert "Munich city detected" in normalized.location_reason

    # Check normalization fields
    assert normalized.remote_mode == "onsite"
    assert normalized.normalized_location.lower() == "munich germany"
    assert normalized.canonical_key is not None
    assert normalized.canonical_url is not None


def test_remote_job():
    # Test a remote AI engineer
    job = Job(
        id="test2",
        title="Remote AI Engineer",
        description="Working remotely on machine learning projects",
        company="Example Corp",
        url="https://example.com/jobs/test2",
        location="Remote",
        source="test",
        source_type="test",
    )

    normalized = normalize_job(job, source_type="ats_board")

    # Check role classification
    assert RoleArchetype.AI_PRODUCT in normalized.role_archetypes
    assert normalized.role_family == "AI Product"
    assert normalized.role_classification_confidence >= 0.9
    assert "AI engineer detected" in normalized.role_classification_reason
    assert normalized.career_direction == CareerDirection.DIRECT_MATCH

    # Check location classification
    assert normalized.location_scope == "remote_eu"
    assert normalized.location_score == 0.3
    assert "Remote detected" in normalized.location_reason

    # Check normalization fields
    assert normalized.remote_mode == "remote"


def test_international_job():
    # Test an international role
    job = Job(
        id="test3",
        title="Global Solutions Architect",
        description="Designing solutions for clients worldwide",
        company="Example Corp",
        url="https://example.com/jobs/test3",
        location="Worldwide",
        source="test",
        source_type="test",
    )

    normalized = normalize_job(job, source_type="ats_board")

    # Check role classification
    assert RoleArchetype.SOLUTIONS_ARCHITECTURE in normalized.role_archetypes
    assert normalized.role_family == "Solutions Architecture"
    assert normalized.role_classification_confidence >= 0.9
    assert "solutions architect detected" in normalized.role_classification_reason
    assert normalized.career_direction == CareerDirection.ADJACENT_MATCH

    # Check location classification
    assert normalized.location_scope == "international"
    assert normalized.location_score == 0.2
    assert "International detected" in normalized.location_reason


def test_unknown_location_and_role():
    # Test a job with unknown location and role
    job = Job(
        id="test4",
        title="Some Manager",
        description="Managing things",
        company="Example Corp",
        url="https://example.com/jobs/test4",
        location="",
        source="test",
        source_type="test",
    )

    normalized = normalize_job(job, source_type="ats_board")

    # Check role classification
    assert RoleArchetype.UNKNOWN in normalized.role_archetypes
    assert normalized.role_family == "Unknown"
    assert normalized.role_classification_confidence == 0.5
    assert "no matching keywords or track affinity" in normalized.role_classification_reason
    assert normalized.career_direction == CareerDirection.UNKNOWN

    # Check location classification
    assert normalized.location_scope == "unknown"
    assert normalized.location_score == 0.0
    assert "No location information found" in normalized.location_reason
