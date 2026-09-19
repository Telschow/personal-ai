"""Evidence / trust model for career intelligence.

Every claim the fit layer surfaces must be attributable to an
:class:`CareerEvidence` with an explicit verification level. There is no
auto-promotion: automation output lands at ``candidate``/``inferred``,
profile-declared facts are ``verified``, and parent knowledge-base records
are ``documented``. Evidence is data, never instruction.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VerificationLevel(StrEnum):
    """Trust ladder for a career evidence claim.

    Ordered least → most trusted. Higher levels rank earlier in retrieval
    and are eligible for stronger narrative claims.
    """

    UNKNOWN = "unknown"
    INFERRED = "inferred"  # produced by automation, never promoted
    CANDIDATE = "candidate"  # raw retrieval hit, unconfirmed by anyone
    DOCUMENTED = "documented"  # persisted personal-ai memory/chunk (sourced)
    USER_CONFIRMED = "user_confirmed"  # user-acknowledged derived claim
    VERIFIED = "verified"  # stated by the user in profile/career file


LEVEL_ORDER: dict[VerificationLevel, int] = {
    VerificationLevel.UNKNOWN: 0,
    VerificationLevel.INFERRED: 1,
    VerificationLevel.CANDIDATE: 2,
    VerificationLevel.DOCUMENTED: 3,
    VerificationLevel.USER_CONFIRMED: 4,
    VerificationLevel.VERIFIED: 5,
}


def level_index(level: VerificationLevel | str) -> int:
    return LEVEL_ORDER.get(VerificationLevel(level), 0)


# Capability facets used across evidence, requirements and strengths.
CATEGORY_KEYS = (
    "product",
    "technical",
    "leadership",
    "program",
    "ai",
    "agentic_ai",
    "systems",
    "engineering",
    "domain",
    "communication",
    "innovation",
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "product": (
        "product",
        "product owner",
        "product manager",
        "roadmap",
        "backlog",
        "stakeholder",
        "go-to-market",
        "product strategy",
        "vision",
    ),
    "technical": (
        "engineer",
        "engineering",
        "architecture",
        "algorithm",
        "technical",
        "c++",
        "python",
        "software",
        "embedded",
        "control",
        "validation",
        "simulation",
        "math",
    ),
    "leadership": (
        "lead",
        "leader",
        "leadership",
        "head of",
        "director",
        "manager",
        "management",
        "team lead",
        "team leadership",
        "ownership",
        "department",
        "chief",
    ),
    "program": (
        "program",
        "project management",
        "agile",
        "scrum",
        "coordination",
        "cross-functional",
        "process",
    ),
    "ai": (
        "ai",
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "autonomous",
        "autopilot",
        "computer vision",
        "llm",
        "robotics",
        "neural",
        "perception",
    ),
    "agentic_ai": (
        "agentic",
        "ai agent",
        "agents",
        "autonomous agent",
        "agentic ai",
        "tool use",
    ),
    "systems": (
        "systems",
        "system",
        "requirements engineering",
        "functional safety",
        "iso 26262",
        "sysml",
        "architecture",
        "holistic",
        "integration",
    ),
    "engineering": (
        "development",
        "software development",
        "embedded software",
        "coding",
        "swe",
        "programming",
    ),
    "domain": (
        "automotive",
        "mobility",
        "autonomous driving",
        "adas",
        "vehicle",
        "ev",
        "electric vehicle",
        "industrial",
        "energy",
        "robotics",
        "aerospace",
    ),
    "communication": (
        "communication",
        "stakeholder management",
        "presentation",
        "negotiation",
        "english",
        "german",
        "spanish",
    ),
    "innovation": (
        "innovation",
        "research",
        "renewable",
        "defense",
        "defence",
        "space",
        "deep tech",
        "new technology",
        "r&d",
    ),
}


def classify_categories(text: str | None) -> tuple[str, ...]:
    """Return the sorted capability facets implied by ``text`` keywords.

    Deterministic, case-insensitive substring matching against a fixed
    keyword table. No model involvement; never promotes by itself.
    """
    haystack = f" {text or ''} ".casefold()
    found: set[str] = set()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(f" {kw} " in f" {haystack} " or kw in haystack for kw in keywords):
            found.add(category)
    return tuple(sorted(found))


def _clean_words(text: str | None) -> tuple[str, ...]:
    words = sorted({w for w in re.findall(r"[a-zäöüßñ]{3,}", (text or "").casefold()) if len(w) > 2})
    return tuple(words)


def evidence_id(claim: str, source: str) -> str:
    """Stable identity for a claim from a given source (idempotent across runs)."""
    return hashlib.sha256(f"{claim}\x00{source}".encode()).hexdigest()[:16]


class CareerEvidence(BaseModel):
    """One attributable claim about the user, with provenance and trust level."""

    evidence_id: str
    claim: str
    level: VerificationLevel
    source: str
    source_type: str | None = None
    categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    document_id: str | None = None
    chunk_id: str | None = None
    memory_id: str | None = None
    memory_kind: str | None = None
    observed_at: str | None = None
    normalized_fact: str | None = None
    authority: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _mk(
    *,
    claim: str,
    source: str,
    level: VerificationLevel,
    categories: set[str] | None = None,
    keywords: list[str] | None = None,
    confidence: float = 0.5,
    **extra: Any,
) -> CareerEvidence:
    return CareerEvidence(
        evidence_id=evidence_id(claim, source),
        claim=claim,
        level=level,
        source=source,
        categories=list(categories or set()),
        keywords=list(keywords or []),
        confidence=confidence,
        **extra,
    )


def build_profile_evidence(profile_yaml: dict, career) -> list[CareerEvidence]:
    """Derive VERIFIED evidence from the user-maintained profile file.

    The career profile's *explicit* fields are also emitted as claims so the
    fit narrative can reference them; auto-derived profile fields are never
    emitted (no auto-promotion). No I/O, no network, deterministic.
    """
    out: list[CareerEvidence] = []
    source = "profile"

    for exp in profile_yaml.get("experience", []) or []:
        company = str(exp.get("company", "")).strip()
        title = str(exp.get("title", "")).strip()
        dates = str(exp.get("dates", "")).strip()
        location = str(exp.get("location", "")).strip()
        if not company or not title:
            continue
        suffix = f" ({dates})" if dates else ""
        claim = f"Worked as {title} at {company}{suffix}".rstrip()
        facts = " ".join(exp.get("facts", []) or [])
        keywords: list[str] = list(dict.fromkeys([*_clean_words(title), *_clean_words(company), *_clean_words(facts)]))
        out.append(
            _mk(
                claim=claim,
                source=source,
                level=VerificationLevel.VERIFIED,
                categories=set(classify_categories(f"{title} {facts}")),
                keywords=keywords,
                confidence=0.95,
                raw={"company": company, "title": title, "location": location},
            )
        )

    for skill in profile_yaml.get("skills", []) or []:
        skill = str(skill).strip()
        if not skill:
            continue
        out.append(
            _mk(
                claim=f"Lists the skill: {skill}",
                source=source,
                level=VerificationLevel.VERIFIED,
                categories=set(classify_categories(skill)),
                keywords=list(_clean_words(skill)),
                confidence=0.9,
            )
        )

    if career is not None and not career.inferred_fields.intersection(
        {"target_role_families", "target_seniority", "leadership_direction"}
    ):
        out.append(
            _mk(
                claim=(f"Targets {', '.join(career.target_role_families)} at seniority {career.target_seniority}"),
                source="career_profile",
                level=VerificationLevel.VERIFIED,
                categories={"product", "leadership"},
                keywords=list(_clean_words(" ".join(career.target_role_families))),
                confidence=0.9,
            )
        )

    return out


def rank_evidence(items: list[CareerEvidence], *, limit: int = 40) -> list[CareerEvidence]:
    """Deterministic ranking by trust, confidence, then recency, then id.

    Deduplicates on ``evidence_id`` keeping the first (highest trust)
    occurrence. Caps at ``limit``.
    """
    dedup: dict[str, CareerEvidence] = {}
    for item in items:
        if item.evidence_id in dedup:
            continue
        dedup[item.evidence_id] = item
    ranked = sorted(
        dedup.values(),
        key=lambda e: (
            level_index(e.level),
            e.confidence,
            e.observed_at or "",
            e.evidence_id,
        ),
        reverse=True,
    )
    return ranked[:limit]
