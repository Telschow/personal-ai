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
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

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


# Compatibility aliases for the stable evidence contract
EvidenceVerificationLevel = Literal["verified", "directly_supported", "inferred", "unknown"]


class CareerEvidenceType(StrEnum):
    """Explicit evidence categorization for career intelligence."""
    SKILL = "skill"
    TECHNOLOGY = "technology"
    PROJECT = "project"
    ACHIEVEMENT = "achievement"
    RESPONSIBILITY = "responsibility"
    LEADERSHIP = "leadership"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    DOMAIN_EXPERIENCE = "domain_experience"
    INDUSTRY_EXPERIENCE = "industry_experience"
    LANGUAGE = "language"
    CAREER_EXPERIENCE = "career_experience"


class GapSupportLevel(StrEnum):
    """Evidence support level for a job requirement."""
    STRONGLY_SUPPORTED = "strongly_supported"
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CareerMoveClassification(StrEnum):
    """Classification of a job relative to career trajectory."""
    DIRECT_MATCH = "direct_match"
    ADJACENT_MATCH = "adjacent_match"
    PIVOT = "pivot"
    STRETCH = "stretch"


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
    evidence_type: CareerEvidenceType | None = None
    source_location: str | None = None  # e.g., "document:page-3", "memory:work", "profile:experience"
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
    gap_support: GapSupportLevel | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _infer_evidence_type(claim: str, categories: set[str]) -> CareerEvidenceType | None:
    """Infer evidence type from claim text and categories."""
    claim_l = claim.casefold()

    # Check categories first (more reliable)
    if "leadership" in categories:
        return CareerEvidenceType.LEADERSHIP
    if "product" in categories:
        return CareerEvidenceType.PROJECT
    if "technical" in categories:
        return CareerEvidenceType.TECHNOLOGY
    if "ai" in categories or "agentic_ai" in categories:
        return CareerEvidenceType.TECHNOLOGY
    if "systems" in categories or "engineering" in categories:
        return CareerEvidenceType.TECHNOLOGY
    if "domain" in categories:
        return CareerEvidenceType.DOMAIN_EXPERIENCE
    if "communication" in categories:
        return CareerEvidenceType.LANGUAGE
    if "innovation" in categories:
        return CareerEvidenceType.DOMAIN_EXPERIENCE
    if "program" in categories:
        return CareerEvidenceType.RESPONSIBILITY

    # Fallback: infer from claim text (more specific patterns)
    if "lists the skill:" in claim_l:
        return CareerEvidenceType.SKILL
    if "language proficiency:" in claim_l:
        return CareerEvidenceType.LANGUAGE
    if "graduated with" in claim_l or " degree " in claim_l or (" msc " in claim_l or claim_l.startswith("msc ")) or (" bsc " in claim_l or claim_l.startswith("bsc ")) or (" ba " in claim_l or claim_l.startswith("ba ")) or (" ma " in claim_l or claim_l.startswith("ma ")) or (" phd " in claim_l or claim_l.startswith("phd ")) or (" bachelor " in claim_l or claim_l.startswith("bachelor ")) or (" master " in claim_l or claim_l.startswith("master ")):
        return CareerEvidenceType.EDUCATION
    if "certification" in claim_l or "certified" in claim_l or "credential" in claim_l or "license" in claim_l:
        return CareerEvidenceType.CERTIFICATION
    if any(k in claim_l for k in ("led a team", "managed a team", "spearheaded", "directed", "head of", "director of", "lead cross-functional", "managed cross-functional")):
        return CareerEvidenceType.LEADERSHIP
    if any(k in claim_l for k in ("built", "developed", "launched", "delivered", "implemented", "project")):
        return CareerEvidenceType.PROJECT
    if any(k in claim_l for k in ("achieved", "increased", "reduced", "improved", "saved", "grew", "scaled")):
        return CareerEvidenceType.ACHIEVEMENT
    if any(k in claim_l for k in ("responsible for", "owned", "accountable", "duty", "task")):
        return CareerEvidenceType.RESPONSIBILITY
    if any(k in claim_l for k in ("python", "c++", "java", "kubernetes", "docker", "aws", "gcp", "azure", "sql", "kafka", "spark", "tensorflow", "pytorch", "ros2", "opencv", "microservices", "ci/cd", "git", "linux")):
        return CareerEvidenceType.TECHNOLOGY
    if any(k in claim_l for k in ("automotive", "mobility", "autonomous driving", "adas", "robotics", "defense", "energy", "health", "finance", "space", "industrial")):
        return CareerEvidenceType.DOMAIN_EXPERIENCE
    if any(k in claim_l for k in ("german", "english", "spanish", "french", "language")):
        return CareerEvidenceType.LANGUAGE
    if "worked as" in claim_l or "experience" in claim_l or "role" in claim_l or "position" in claim_l:
        return CareerEvidenceType.CAREER_EXPERIENCE
    return None


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
    cats = categories or set()
    # If evidence_type is explicitly provided in extra, don't infer
    ev_type = extra.pop("evidence_type", None) if "evidence_type" in extra else _infer_evidence_type(claim, cats or set())
    return CareerEvidence(
        evidence_id=evidence_id(claim, source),
        claim=claim,
        level=level,
        source=source,
        evidence_type=ev_type,
        categories=list(cats or set()),
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
    now = datetime.now(UTC).isoformat(timespec="seconds")

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
                source_location=f"profile:experience:{company.lower().replace(' ', '_')}",
                observed_at=now,
                raw={"company": company, "title": title, "location": location, "dates": dates, "facts": facts},
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
                source_location="profile:skills",
                observed_at=now,
                evidence_type=CareerEvidenceType.SKILL,
            )
        )

    # Education - explicitly set as EDUCATION type
    for edu in profile_yaml.get("education", []) or []:
        if not isinstance(edu, dict):
            continue
        school = str(edu.get("school", "")).strip()
        degree = str(edu.get("degree", "")).strip()
        years = str(edu.get("years", "")).strip()
        if not school or not degree:
            continue
        claim = f"Graduated with {degree} from {school}"
        if years:
            claim += f" ({years})"
        out.append(
            _mk(
                claim=claim,
                source=source,
                level=VerificationLevel.VERIFIED,
                categories={"education"},
                keywords=list(_clean_words(f"{degree} {school}")),
                confidence=0.95,
                source_location="profile:education",
                observed_at=now,
                evidence_type=CareerEvidenceType.EDUCATION,
            )
        )

    # Languages
    for lang in profile_yaml.get("languages", []) or []:
        if isinstance(lang, dict):
            # YAML format: {"German": "native/bilingual"}
            lang_name = next(iter(lang.keys())) if lang else ""
            lang_level = next(iter(lang.values())) if lang else ""
            claim_text = f"{lang_name}: {lang_level}" if lang_level else lang_name
        else:
            lang_name = str(lang).strip()
            claim_text = lang_name
        if not claim_text:
            continue
        out.append(
            _mk(
                claim=f"Language proficiency: {claim_text}",
                source=source,
                level=VerificationLevel.VERIFIED,
                categories={"communication"},
                keywords=[lang_name.lower()],
                confidence=0.9,
                evidence_type=CareerEvidenceType.LANGUAGE,
                source_location="profile:languages",
                observed_at=now,
            )
        )

    # Target role families (career direction)
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
                source_location="career_profile:targets",
                observed_at=now,
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
