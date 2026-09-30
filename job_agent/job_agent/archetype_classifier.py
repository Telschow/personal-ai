"""Deterministic role archetype classification.

Classifies jobs into multiple role archetypes based on title, description, and existing track/category evidence.
No LLM dependency - purely keyword/rule based.
"""

from __future__ import annotations

import re

from .models import Job
from .role_archetypes import RoleArchetype

# Keywords for each archetype
ARCHETYPE_KEYWORDS = {
    RoleArchetype.TECHNICAL_PRODUCT: [
        "technical product manager",
        "product manager",
        "product owner",
        "product lead",
        "group product manager",
        "principal product manager",
        "product engineer",
        "technical pm",
        "tpms",
    ],
    RoleArchetype.PRODUCT_LEADERSHIP: [
        "head of product",
        "director of product",
        "vp product",
        "chief product officer",
        "product director",
        "product leadership",
        "lead product",
        "product lead",
    ],
    RoleArchetype.TECHNICAL_PROGRAM: [
        "technical program manager",
        "program manager",
        "staff technical program manager",
        "senior program manager",
        "program lead",
        "technical program lead",
        "tpm",
    ],
    RoleArchetype.SYSTEMS_INTEGRATION: [
        "systems integration",
        "integration engineer",
        "systems integrator",
        "integration lead",
        "system integration",
        "platform integration",
        "technical integration",
    ],
    RoleArchetype.PLATFORM_PRODUCT: [
        "platform product manager",
        "platform product",
        "platform engineer",
        "platform team",
        "internal platform",
        "developer platform",
        "platform lead",
    ],
    RoleArchetype.AI_PRODUCT: [
        "ai product manager",
        "ml product manager",
        "machine learning product",
        "ai product lead",
        "genai product",
        "llm product",
        "artificial intelligence product",
    ],
    RoleArchetype.AUTONOMOUS_SYSTEMS: [
        "autonomous systems",
        "autonomous driving",
        "adas",
        "autonomy engineer",
        "self driving",
        "autonomous vehicle",
        "autonomous systems engineer",
    ],
    RoleArchetype.ROBOTICS: [
        "robotics engineer",
        "robotics software",
        "robotics product",
        "ros engineer",
        "motion planning",
        "manipulation",
        "slam",
        "humanoid",
    ],
    RoleArchetype.DEEPTECH: [
        "deeptech",
        "deep tech",
        "quantum",
        "photonics",
        "semiconductor",
        "fusion",
        "advanced materials",
        "quantum computing",
    ],
    RoleArchetype.DEFENCE_AEROSPACE: [
        "defence",
        "defense",
        "aerospace",
        "space systems",
        "satellite",
        "avionics",
        "military systems",
        "mission systems",
        "unmanned systems",
    ],
    RoleArchetype.MOBILITY: [
        "automotive",
        "mobility",
        "electric vehicle",
        "ev ",
        "charging",
        "vehicle platform",
        "oem",
        "connected car",
    ],
    RoleArchetype.TECHNOLOGY_STRATEGY: [
        "technology strategy",
        "tech strategy",
        "technical strategy",
        "cto office",
        "chief technology",
        "technology strategist",
    ],
    RoleArchetype.PRODUCT_STRATEGY: [
        "product strategy",
        "product strategist",
        "product vision",
        "roadmap",
        "go-to-market",
        "gtm",
    ],
    RoleArchetype.ENGINEERING_PROGRAM: [
        "engineering manager",
        "lead engineer",
        "technical lead",
        "staff engineer",
        "principal engineer",
        "engineering lead",
    ],
    RoleArchetype.TECHNICAL_PARTNERSHIPS: [
        "technical partnerships",
        "strategic partnerships",
        "partner engineering",
        "partner manager",
        "alliances",
        "ecosystem",
    ],
    RoleArchetype.SOLUTIONS_ARCHITECTURE: [
        "solutions architect",
        "solution architect",
        "solutions architecture",
        "sales engineer",
        "pre-sales",
        "customer engineering",
    ],
    RoleArchetype.CUSTOMER_ENGINEERING: [
        "customer engineer",
        "customer engineering",
        "field engineer",
        "deployment engineer",
        "implementation engineer",
        "customer success engineer",
    ],
    RoleArchetype.INNOVATION: [
        "innovation manager",
        "innovation lead",
        "digital transformation",
        "transformation manager",
        "venture",
        "r&d strategy",
    ],
}


# Track/category affinities for additional evidence
TRACK_ARCHETYPE_MAP = {
    "product_management": [
        RoleArchetype.TECHNICAL_PRODUCT,
        RoleArchetype.PRODUCT_LEADERSHIP,
        RoleArchetype.PRODUCT_STRATEGY,
        RoleArchetype.PLATFORM_PRODUCT,
    ],
    "program_management": [
        RoleArchetype.TECHNICAL_PROGRAM,
        RoleArchetype.ENGINEERING_PROGRAM,
    ],
    "engineering_leadership": [
        RoleArchetype.ENGINEERING_PROGRAM,
        RoleArchetype.SYSTEMS_LEADERSHIP,
    ],
    "autonomous_driving": [
        RoleArchetype.AUTONOMOUS_SYSTEMS,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "ai_ml": [
        RoleArchetype.AI_PRODUCT,
        RoleArchetype.TECHNICAL_PRODUCT,
    ],
    "robotics": [
        RoleArchetype.ROBOTICS,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "deeptech": [
        RoleArchetype.DEEPTECH,
        RoleArchetype.TECHNOLOGY_STRATEGY,
    ],
    "defence_aerospace": [
        RoleArchetype.DEFENCE_AEROSPACE,
        RoleArchetype.SYSTEMS_INTEGRATION,
    ],
    "mobility": [
        RoleArchetype.MOBILITY,
        RoleArchetype.PLATFORM_PRODUCT,
    ],
    "energy_cleantech": [
        RoleArchetype.DEEPTECH,
        RoleArchetype.TECHNOLOGY_STRATEGY,
    ],
    "data_platform": [
        RoleArchetype.PLATFORM_PRODUCT,
        RoleArchetype.TECHNICAL_PRODUCT,
    ],
    "innovation_strategy": [
        RoleArchetype.INNOVATION,
        RoleArchetype.TECHNOLOGY_STRATEGY,
        RoleArchetype.PRODUCT_STRATEGY,
    ],
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def classify_role_archetypes(
    job: Job,
    track: str | None = None,
) -> set[RoleArchetype]:
    """Classify a job into role archetypes based on title, description, and track.

    Returns a set of matching archetypes (multi-label).
    """
    archetypes: set[RoleArchetype] = set()

    title = _normalize_text(job.title or "")
    description = _normalize_text(job.description or "")
    combined = f"{title} {description}"

    # 1. Keyword matching on title + description
    for archetype, keywords in ARCHETYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                archetypes.add(archetype)
                break

    # 2. Track-based affinity
    if track and track in TRACK_ARCHETYPE_MAP:
        archetypes.update(TRACK_ARCHETYPE_MAP[track])

    # 3. Fallback: if no archetypes found, mark as UNKNOWN
    if not archetypes:
        archetypes.add(RoleArchetype.UNKNOWN)

    return archetypes
