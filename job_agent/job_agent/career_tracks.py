"""Configurable career tracks and a deterministic track classifier.

Tracks replace the earlier ad-hoc query clusters. They are data, not code:
each track declares the search terms, title/domain signals, and source
categories used for discovery, and the classifier is a pure deterministic
function (no LLM, no network).

The 12 defaults reflect the candidate's stated direction (product/program,
ADAS/AI/robotics/deep-tech/defence) while remaining conservative: an unknown
posting classifies as ``unknown`` rather than being forced into a track.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

_WS = re.compile(r"\s+")


class CareerTrack(BaseModel):
    """A single, operator-configurable career direction."""

    track_id: str
    name: str
    description: str = ""
    search_terms: list[str] = Field(default_factory=list)
    title_patterns: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    seniority_terms: list[str] = Field(default_factory=list)
    location_preferences: list[str] = Field(default_factory=list)
    source_categories: list[str] = Field(default_factory=list)
    category_affinities: list[str] = Field(default_factory=list)
    priority: int = 50
    enabled: bool = True


class CareerTracksSettings(BaseModel):
    """Track configuration; empty ``tracks`` falls back to :data:`DEFAULT_TRACKS`."""

    tracks: list[CareerTrack] = Field(default_factory=list)

    @property
    def resolved(self) -> list[CareerTrack]:
        return self.tracks or list(DEFAULT_TRACKS)


@dataclass(frozen=True)
class TrackMatch:
    track_id: str
    score: float
    confidence: float
    matched_terms: tuple[str, ...]


def _track(
    track_id: str,
    name: str,
    *,
    search_terms: list[str],
    title_patterns: list[str],
    domain_terms: list[str],
    negative_terms: list[str] | None = None,
    source_categories: list[str] | None = None,
    category_affinities: list[str] | None = None,
    priority: int = 50,
) -> CareerTrack:
    return CareerTrack(
        track_id=track_id,
        name=name,
        search_terms=search_terms,
        title_patterns=title_patterns,
        domain_terms=domain_terms,
        negative_terms=negative_terms or [],
        source_categories=source_categories or ["search_engine", "ats", "structured_data"],
        category_affinities=category_affinities or [],
        priority=priority,
    )


DEFAULT_TRACKS: tuple[CareerTrack, ...] = (
    _track(
        "product_management",
        "Technical Product Management",
        search_terms=["Technical Product Manager", "Product Owner", "Principal Product Manager"],
        title_patterns=["product manager", "product owner", "product lead", "group product"],
        domain_terms=["roadmap", "product strategy", "product vision", "backlog", "go-to-market"],
        negative_terms=["product marketing", "sales"],
        category_affinities=["startup", "general", "tech_general", "engineering", "remote", "ai", "ats"],
        priority=95,
    ),
    _track(
        "program_management",
        "Technical Program Management",
        search_terms=["Technical Program Manager", "Program Manager", "Staff Technical Program Manager"],
        title_patterns=["program manager", "technical program", "project manager", "program lead"],
        domain_terms=["program management", "cross-functional", "delivery", "milestones", "stakeholder"],
        negative_terms=["construction", "event manager"],
        category_affinities=["startup", "general", "tech_general", "engineering", "remote", "ai", "ats"],
        priority=88,
    ),
    _track(
        "engineering_leadership",
        "Engineering Leadership",
        search_terms=["Engineering Manager", "Head of Engineering", "Director of Engineering"],
        title_patterns=[
            "engineering manager",
            "head of engineering",
            "director of engineering",
            "vp engineering",
            "head of software",
            "tech lead",
            "technical lead",
        ],
        domain_terms=["people management", "engineering leadership", "team leadership", "hiring"],
        category_affinities=["startup", "general", "tech_general", "engineering", "remote", "ats"],
        priority=80,
    ),
    _track(
        "autonomous_driving",
        "Autonomous Driving / ADAS",
        search_terms=["ADAS", "Autonomous Driving", "Autonomous Systems Engineer"],
        title_patterns=["autonomous driving", "adas", "autonomy", "perception", "sensor fusion"],
        domain_terms=["autonomous driving", "adas", "lidar", "radar", "sensor fusion", "perception", "self-driving"],
        category_affinities=["ai", "robotics", "engineering", "general", "tech_general", "startup", "remote"],
        priority=92,
    ),
    _track(
        "ai_ml",
        "AI / Machine Learning",
        search_terms=["Machine Learning Engineer", "AI Engineer", "Applied AI Lead"],
        title_patterns=["machine learning", "ml engineer", "ai engineer", "applied ai", "data scientist"],
        domain_terms=["machine learning", "deep learning", "llm", "generative ai", "computer vision", "nlp"],
        negative_terms=["data entry"],
        category_affinities=["ai", "remote", "startup", "general", "tech_general", "engineering", "robotics"],
        priority=90,
    ),
    _track(
        "robotics",
        "Robotics / Autonomy",
        search_terms=["Robotics Engineer", "Robotics Software Engineer", "Autonomy Engineer"],
        title_patterns=["robotics", "robot", "manipulation", "motion planning"],
        domain_terms=["robotics", "ros", "manipulation", "motion planning", "slam", "humanoid"],
        category_affinities=["robotics", "ai", "engineering", "remote", "startup", "general", "tech_general"],
        priority=82,
    ),
    _track(
        "deeptech",
        "Deep Tech",
        search_terms=["Deep Tech", "Quantum Computing", "Photonics Engineer"],
        title_patterns=["quantum", "photonics", "semiconductor", "fusion", "deep tech"],
        domain_terms=["quantum", "photonics", "semiconductor", "fusion", "advanced materials", "deep tech"],
        category_affinities=["engineering", "general", "tech_general", "startup", "ai", "energy_cleantech", "defence"],
        priority=70,
    ),
    _track(
        "defence_aerospace",
        "Defence & Aerospace",
        search_terms=["Defence Systems", "Aerospace Engineer", "Space Systems"],
        title_patterns=["defence", "defense", "aerospace", "space systems", "avionics"],
        domain_terms=["defence", "defense", "aerospace", "space", "avionics", "satellite", "security clearance"],
        category_affinities=["defence", "engineering", "general", "tech_general", "startup", "ai", "energy_cleantech"],
        priority=74,
    ),
    _track(
        "mobility",
        "Mobility & Automotive",
        search_terms=["Automotive", "Mobility", "Electric Vehicle"],
        title_patterns=["automotive", "mobility", "electric vehicle", "charging", "vehicle"],
        domain_terms=["automotive", "mobility", "electric vehicle", "ev", "charging", "oem"],
        category_affinities=["engineering", "general", "tech_general", "startup", "energy_cleantech", "ai"],
        priority=72,
    ),
    _track(
        "energy_cleantech",
        "Energy & Climate Tech",
        search_terms=["Energy", "Climate Tech", "Battery Systems"],
        title_patterns=["energy", "climate", "battery", "grid", "solar"],
        domain_terms=["renewable", "energy", "climate", "battery", "grid", "decarbonization", "sustainability"],
        category_affinities=["energy_cleantech", "engineering", "general", "tech_general", "startup", "remote"],
        priority=62,
    ),
    _track(
        "data_platform",
        "Data Platform & Products",
        search_terms=["Data Platform", "Data Product Manager", "Analytics Lead"],
        title_patterns=["data platform", "data product", "analytics", "data engineering"],
        domain_terms=["data platform", "data pipeline", "analytics", "data governance", "warehouse"],
        category_affinities=["general", "tech_general", "startup", "remote", "ai", "engineering"],
        priority=66,
    ),
    _track(
        "innovation_strategy",
        "Innovation & Technology Strategy",
        search_terms=["Innovation Manager", "Technology Strategy", "Digital Transformation"],
        title_patterns=["innovation", "technology strategy", "digital transformation", "corporate strategy"],
        domain_terms=["innovation", "technology strategy", "transformation", "venture", "r&d strategy"],
        category_affinities=[
            "general",
            "tech_general",
            "startup",
            "finance",
            "energy_cleantech",
            "engineering",
            "aggregator",
        ],
        priority=58,
    ),
)

DEFAULT_TRACK_IDS: tuple[str, ...] = tuple(t.track_id for t in DEFAULT_TRACKS)


def _norm(text: str | None) -> str:
    return _WS.sub(" ", (text or "").casefold()).strip()


def match_tracks(
    title: str,
    description: str = "",
    *,
    tracks: list[CareerTrack] | None = None,
    limit: int = 3,
) -> list[TrackMatch]:
    """Rank tracks for a posting (deterministic; ties broken by priority then id)."""
    catalogue = tracks if tracks is not None else list(DEFAULT_TRACKS)
    title_n = _norm(title)
    text_n = _norm(f"{title} {description[:4000]}")

    matches: list[TrackMatch] = []
    for track in catalogue:
        if not track.enabled:
            continue
        if any(_norm(neg) in text_n for neg in track.negative_terms):
            continue
        score = 0.0
        matched: list[str] = []
        for pattern in track.title_patterns:
            if _norm(pattern) in title_n:
                score += 1.0
                matched.append(pattern)
        for term in track.domain_terms:
            if _norm(term) in text_n:
                score += 0.4
                matched.append(term)
        if score <= 0:
            continue
        confidence = min(1.0, score / 2.0)
        matches.append(
            TrackMatch(
                track_id=track.track_id,
                score=round(score, 3),
                confidence=round(confidence, 3),
                matched_terms=tuple(dict.fromkeys(matched)),
            )
        )

    priority = {t.track_id: t.priority for t in catalogue}
    matches.sort(key=lambda m: (-m.score, -priority.get(m.track_id, 0), m.track_id))
    return matches[:limit]


def classify_track(
    title: str,
    description: str = "",
    *,
    tracks: list[CareerTrack] | None = None,
) -> TrackMatch | None:
    """Return the single best track match, or ``None`` for an unknown posting."""
    matches = match_tracks(title, description, tracks=tracks, limit=1)
    return matches[0] if matches else None


class SeniorityBand(StrEnum):
    """Named seniority band derived from the numeric requirements level."""

    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    LEAD = "lead"
    EXEC = "executive"
    UNKNOWN = "unknown"


_BAND_BY_LEVEL: dict[int, SeniorityBand] = {
    1: SeniorityBand.ENTRY,
    2: SeniorityBand.MID,
    3: SeniorityBand.SENIOR,
    4: SeniorityBand.STAFF,
    5: SeniorityBand.LEAD,
    6: SeniorityBand.EXEC,
}

_SENIORITY_ALIASES: dict[str, SeniorityBand] = {
    "junior": SeniorityBand.ENTRY,
    "entry": SeniorityBand.ENTRY,
    "intern": SeniorityBand.ENTRY,
    "working student": SeniorityBand.ENTRY,
    "mid": SeniorityBand.MID,
    "intermediate": SeniorityBand.MID,
    "senior": SeniorityBand.SENIOR,
    "staff": SeniorityBand.STAFF,
    "principal": SeniorityBand.STAFF,
    "lead": SeniorityBand.LEAD,
    "head": SeniorityBand.LEAD,
    "director": SeniorityBand.EXEC,
    "vp": SeniorityBand.EXEC,
    "chief": SeniorityBand.EXEC,
}


def normalize_seniority(value: int | str | None) -> SeniorityBand:
    """Map a numeric level (1–6) or a label onto a named band."""
    if value is None:
        return SeniorityBand.UNKNOWN
    if isinstance(value, int):
        return _BAND_BY_LEVEL.get(value, SeniorityBand.UNKNOWN)
    text = _norm(value)
    if not text:
        return SeniorityBand.UNKNOWN
    for alias, band in _SENIORITY_ALIASES.items():
        if alias in text:
            return band
    return SeniorityBand.UNKNOWN


__all__ = [
    "CareerTrack",
    "CareerTracksSettings",
    "DEFAULT_TRACK_IDS",
    "DEFAULT_TRACKS",
    "SeniorityBand",
    "TrackMatch",
    "classify_track",
    "match_tracks",
    "normalize_seniority",
]
