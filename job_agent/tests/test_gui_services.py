"""Tests for GUI service layer."""

from __future__ import annotations

from job_agent.gui.services import CareerService


def test_service_initialization():
    """Test that CareerService initializes correctly."""
    service = CareerService(config_path="config.yaml")
    assert service is not None
    assert service.config is not None
    # Verify service does NOT retain a persistent connection (thread-safe architecture)
    assert not hasattr(service, "_conn") or service._conn is None


def test_service_job_count():
    """Test that service can count jobs."""
    service = CareerService(config_path="config.yaml")
    count = service.count_jobs()
    assert count > 0
    print(f"Job count: {count}")


def test_service_get_jobs():
    """Test that service can get jobs."""
    service = CareerService(config_path="config.yaml")
    jobs = service.get_jobs(limit=5)
    assert len(jobs) <= 5
    for job in jobs:
        assert hasattr(job, "id")
        assert hasattr(job, "title")
        assert hasattr(job, "company")


def test_service_feedback_summary():
    """Test that service can get feedback summary."""
    service = CareerService(config_path="config.yaml")
    summary = service.get_feedback_summary()
    assert "total_records" in summary
    assert "unique_jobs" in summary
    assert "label_distribution" in summary


def test_service_get_job():
    """Test that service can get a single job."""
    service = CareerService(config_path="config.yaml")

    # Get a real job ID from the database
    jobs = service.get_jobs(limit=1)
    if jobs:
        job_id = jobs[0].id
        job = service.get_job(job_id)
        assert job is not None
        assert job.id == job_id


def test_service_career_profile():
    """Test that service can load career profile."""
    service = CareerService(config_path="config.yaml")
    profile = service.career_profile
    assert profile is not None
    assert hasattr(profile, "target_role_families")
    assert hasattr(profile, "current_role_family")


if __name__ == "__main__":
    test_service_initialization()
    test_service_job_count()
    test_service_get_jobs()
    test_service_feedback_summary()
    test_service_get_job()
    test_service_career_profile()
    print("All GUI service tests passed!")
