"""Deterministic career direction classification.

Classifies each job as:

- DIRECT_MATCH: Fundamentally similar responsibilities and evidence
- ADJACENT_MATCH: Uses substantial existing capabilities but extends into another domain or responsibility
- STRETCH_MATCH: Credible path, but important responsibilities/skills are missing
- CAREER_PIVOT: Potentially interesting direction requiring materially new capabilities

Stores the evidence for the classification.
"""

from __future__ import annotations

from typing import Any

from .models import Job
from .career_direction import CareerDirection
from .role_archetypes import RoleArchetype


class CareerDirectionAssessment:
    """Result of career direction classification."""
    
    job_id: str
    direction: CareerDirection
    supporting_evidence: list[str]
    missing_evidence: list[str]
    
    def __init__(
        self,
        job_id: str,
        direction: CareerDirection,
        supporting_evidence: list[str] | None = None,
        missing_evidence: list[str] | None = None,
    ):
        self.job_id = job_id
        self.direction = direction
        self.supporting_evidence = supporting_evidence or []
        self.missing_evidence = missing_evidence or []


def determine_career_direction(
    job: Job,
    role_archetypes: set[RoleArchetype],
    career_profile: dict[str, Any],
) -> CareerDirectionAssessment:
    """Determine the career direction for a job based on role archetypes and career profile."""
    # Implementation will go here
    # For now, return a placeholder assessment
    return CareerDirectionAssessment(
        job_id=job.id,
        direction=CareerDirection.UNKNOWN,
        supporting_evidence=[],
        missing_evidence=[],
    )