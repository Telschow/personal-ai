"""Structured job requirements: a semantic view over a raw posting.

``extract_job_attributes`` is deterministic and uses only stored normalized
job fields (title, description, location, remote_mode). It never calls a
model. The raw JD text is deliberately reduced to a small typed surface so
that no downstream step (retrieval plan, fit, LLM prompt) ever copies raw
JD text around as instructions.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..models import Job
from ..scoring import ai_relevance, leadership_score, normalize_text

MAX_CONCEPTS = 12

# capability concept -> matched keyword stubs
CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "ai_systems": (
        "machine learning",
        "computer vision",
        "neural",
        "llm",
        "agents",
        "perception",
        "autonomous",
        "autopilot",
        "ai agent",
        "agentic",
        "foundation model",
    ),
    "product_strategy": (
        "product strategy",
        "product vision",
        "roadmap",
        "go-to-market",
        "market analysis",
        "product roadmap",
    ),
    "stakeholder_management": (
        "stakeholder",
        "cross-functional",
        "leadership engagement",
        "senior stakeholders",
        "alignment",
    ),
    "team_leadership": (
        "team leadership",
        "mentor",
        "lead a team",
        "team lead",
        "leading ",
        "manage engineers",
        "leadership of",
    ),
    "program_management": (
        "program management",
        "project management",
        "agile",
        "scrum",
        "release planning",
        "milestones",
        "delivery",
    ),
    "systems_engineering": (
        "systems engineering",
        "system architecture",
        "requirements engineering",
        "sysml",
        "functional safety",
        "iso 26262",
        "validation",
        "integration",
    ),
    "safety_critical": ("functional safety", "safety", "iso 26262", "safe", "certification", "safety case"),
    "autonomous_driving": (
        "autonomous driving",
        "adas",
        "automated driving",
        "valet parking",
        "maneuver",
        "vehicle automation",
    ),
    "backend_engineering": (
        "backend",
        "backend development",
        "rest",
        "rest api",
        "api",
        "microservices",
        "microservice",
        "cloud",
        "service",
        "kubernetes",
        "docker",
        "database",
        "sql",
        "kafka",
        "grpc",
        "server-side",
    ),
    "embedded_engineering": ("embedded", "firmware", "c++", "cargo", "on-device", "real-time", "sensor"),
    "data_engineering": (
        "data pipelines",
        "data pipeline",
        "data engineering",
        "spark",
        "databases",
        "database",
        "etl",
        "data warehouse",
        "warehouse",
        "airflow",
        "data lakes",
        "streaming",
    ),
    "ai_tooling": ("ai tools", "copilot", "ai tooling", "agentic tooling", "llm tooling"),
    "user_research": ("user research", "user interviews", "usability", "customer insights"),
    "gtm": ("sales", "business development", "partnerships", "revenue", "go-to-market strategy"),
    "regulation": ("gdpr", "compliance", "regulation", "regulatory", "cybersecurity", "security clearance"),
}

_CONCEPT_MAP: tuple[tuple[str, tuple[str, ...]], ...] = tuple(CONCEPT_TERMS.items())

_SENIORITY_KEYWORDS: list[tuple[int, tuple[str, ...]]] = [
    (1, ("junior", "entry level", "working student", "graduate", "intern")),
    (2, ("mid-level", "experienced professional", "2+ years")),
    (3, ("senior", "expert", "5+ years", "extensive experience")),
    (4, ("lead", "staff", "principal", "tech lead", "techlead", "product owner")),
    (5, ("manager", "head of", "engineering manager", "team lead")),
    (6, ("director", "vp", "vice president", "chief", "cto", "head of")),
]

_INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "automotive": ("automotive", "vehicle", "oem", "adas", "autonomous driving"),
    "mobility": ("mobility", "transport", "fleet", "shuttle"),
    "robotics": ("robotics", "robot", "drone"),
    "ai": ("artificial intelligence", "machine learning", "ai ", "llm"),
    "energy": ("energy", "grid", "renewable", "solar", "wind"),
    "defense": ("defence", "defense", "munich security", "security clearance", "military"),
    "health": ("health", "medical", "clinical", "pharma"),
    "space": ("space", "satellite", "aerospace"),
    "industrial": ("industrial", "manufacturing", "factory", "automation"),
    "financial": ("bank", "fintech", "finance", "payment"),
}

_LANGUAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "German": ("german", "deutsch", "deutschkenntnisse"),
    "English": ("english", "fluent english", "englisch"),
    "Spanish": ("spanish", "español"),
}

_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "startup": ("startup", "seed", "early stage", "funded", "series a", "series b"),
    "scale-up": ("scale-up", "scale up", "series c", "growing"),
    "corporate": ("corporate", "enterprise", "group", "global company", "conglomerate"),
}

_CLEARANCE_TERMS = (
    "security clearance",
    "sichereits",
    "verlängerung der sicherheits",
    "sicherheitsprüfung",
    "mit sicherheitsfreigabe",
    "clearance",
)

_PEOPLE_MANAGEMENT_TERMS = (
    "manage a team",
    "managing a team",
    "leadership of a team",
    "lead a team",
    "people management",
    "team of",
    "hiring",
    "führen eines teams",
)


class StructuredJobAttributes(BaseModel):
    """Typed, bounded semantic view of a posting used by fit reasoning."""

    role_family: str = "unknown"
    track: str = "unknown"
    seniority: int = 0
    leadership_scope: int = 0
    people_management: bool = False
    ai_relevance: int = 0
    agentic_ai_relevance: int = 0
    technical_depth: int = 0
    product_scope: int = 0
    organizational_scope: int = 0
    innovation_signal: int = 0
    industry: str | None = None
    domain: str | None = None
    company_stage: str | None = None
    location_mode: str = "unknown"
    languages_required: list[str] = Field(default_factory=list)
    security_clearance: bool = False
    concepts: list[str] = Field(default_factory=list)


def _family_and_track(title: str) -> tuple[str, str]:
    t = title.casefold()
    if any(k in t for k in ("head of", "director of", "vp of", "chief", "cto")):
        if any(k in t for k in ("engineering", "technology", "tech")):
            return "technical_leadership", "technical-led"
        return "product_leadership", "product-led"
    if any(k in t for k in ("product owner", "product manager", "product lead", "group product")):
        return "product_management", "product-led"
    if any(k in t for k in ("program manager", "program director")):
        return "program_leadership", "program-led"
    if any(k in t for k in ("engineering manager", "tech lead", "technical lead", "head of software")):
        return "technical_leadership", "technical-led"
    if any(k in t for k in ("machine learning", "ml engineer", "data scientist", "ai engineer")):
        return "ml_engineering", "technical-led"
    if any(k in t for k in ("software", "engineer", "developer")):
        return "engineering", "technical-led"
    return "unknown", "unknown"


def _seniority(text: str, title: str) -> int:
    combined = f"{title} {text}".casefold()
    for level, terms in sorted(_SENIORITY_KEYWORDS, reverse=True):
        if any(t in combined for t in terms):
            return level
    return 3  # neutral default for unmarked postings


def _requirements_concepts(text: str) -> list[str]:
    found: list[str] = []
    for concept, terms in CONCEPT_TERMS.items():
        if any(t in text for t in terms):
            found.append(concept)
    return found[:MAX_CONCEPTS]


def _product_scope(text: str) -> int:
    terms = ("roadmap", "product strategy", "backlog", "stakeholder", "vision", "go-to-market", "release")
    hits = sum(1 for t in terms if t in text)
    return min(10, 2 + hits)


def _technical_depth(text: str) -> int:
    terms = (
        "architecture",
        "algorithm",
        "system design",
        "c++",
        "python",
        "simulation",
        "real-time",
        "embedded",
        "control",
        "sensor",
        "validation",
        "integration",
    )
    hits = sum(1 for t in terms if t in text)
    return min(10, 2 + hits)


def _organizational_scope(text: str) -> int:
    terms = ("global", "enterprise", "org-wide", "cross-functional", "multiple teams", "worldwide", "platform")
    hits = sum(1 for t in terms if t in text)
    return min(10, hits)


def _innovation_signal(text: str) -> int:
    terms = ("innovation", "research", "deep tech", "state of the art", "novel", "r&d", "new technology")
    return min(10, sum(1 for t in terms if t in text))


def extract_job_attributes(job: Job) -> StructuredJobAttributes:
    """Build the typed requirement surface for a stored job. Pure and local."""
    title = job.title or ""
    desc = (job.description or "")[:8000]
    text = normalize_text(f"{title} {desc} {job.location or ''}")
    plain = re.sub(r"\s+", " ", (f"{title} {desc} {job.location or ''}").casefold())

    family, track = _family_and_track(title or desc)

    leadership = leadership_score(job)
    agentic = 0
    for term in ("agentic", "ai agent", "agents", "llm", "autonomous"):
        if term in plain:
            agentic = max(agentic, 1)
    agentic_score = min(10, agentic * 4 + (8 if any(t in plain for t in ("agentic", "ai agent")) else 0))

    languages: list[str] = []
    for lang, terms in _LANGUAGE_KEYWORDS.items():
        if any(t in plain for t in terms):
            languages.append(lang)

    industry = None
    domain = None
    for candidate, terms in _INDUSTRY_KEYWORDS.items():
        if any(t in plain for t in terms):
            industry = candidate
            domain = candidate
            break

    stage = next((s for s, terms in _STAGE_KEYWORDS.items() if any(t in plain for t in terms)), None)

    return StructuredJobAttributes(
        role_family=family,
        track=track,
        seniority=_seniority(text, title),
        leadership_scope=min(10, round(leadership * 10)),
        people_management=any(t in plain for t in _PEOPLE_MANAGEMENT_TERMS) or "lead a team" in plain,
        ai_relevance=min(10, round(ai_relevance(job) * 10)),
        agentic_ai_relevance=agentic_score,
        technical_depth=_technical_depth(plain),
        product_scope=_product_scope(plain),
        organizational_scope=_organizational_scope(plain),
        innovation_signal=_innovation_signal(plain),
        industry=industry,
        domain=domain,
        company_stage=stage,
        location_mode=job.remote_mode if job.remote_mode in ("remote", "hybrid", "onsite") else "unknown",
        languages_required=languages,
        security_clearance=any(t in plain for t in _CLEARANCE_TERMS),
        concepts=_requirements_concepts(plain),
    )


__all__ = [
    "MAX_CONCEPTS",
    "StructuredJobAttributes",
    "extract_job_attributes",
]
