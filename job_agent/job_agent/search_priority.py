from __future__ import annotations

from typing import Any

from .career_direction import CareerDirection
from .location import LocationTier, RemoteMode
from .models import Job
from .role_archetypes import RoleArchetype


class SearchPriority:
    """Search priority calculation for location-first prioritization."""

    job_id: str
    priority: float
    reason: str

    def __init__(self, job_id: str, priority: float, reason: str):
        self.job_id = job_id
        self.priority = priority
        self.reason = reason


def calculate_search_priority(
    job: Job,
    location_tier: LocationTier,
    remote_mode: RemoteMode,
    role_archetypes: set[RoleArchetype],
    career_direction: CareerDirection,
    career_profile: dict[str, Any],
) -> SearchPriority:
    """Calculate search priority with location-first prioritization."""
    # Implementation will go here
    # For now, return a placeholder priority
    return SearchPriority(
        job_id=job.id,
        priority=0.5,
        reason="Placeholder priority",
    )
