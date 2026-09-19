"""Deterministic fit intelligence: current fit vs. career upside.

The deterministic layer is authoritative: :func:`analyze_fit` computes
scores, strengths, gaps, transferable skills, and risks *without* any model.
The optional LLM narrative (see :mod:`.llm`) is consultative only and can
never alter these numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..models import Job
from .evidence import CareerEvidence, VerificationLevel, level_index
from .mapping import adjacent_evidence
from .positioning import build_positioning
from .profile import CareerProfile
from .requirements import StructuredJobAttributes
from .retrieval import CONCEPT_LABELS, concept_coverage

if TYPE_CHECKING:
    pass


class FitWeights(BaseModel):
    """Default requirement weights. Callers may inject an override."""

    role_family: float = 0.22
    ai_relevance: float = 0.18
    leadership: float = 0.18
    technical_depth: float = 0.14
    product_scope: float = 0.10
    seniority: float = 0.08
    domain: float = 0.10

    @property
    def total(self) -> float:
        return sum(self.model_dump().values())


class FitScore(BaseModel):
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)


class EvidenceInsight(BaseModel):
    dimension: str
    score: float
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Gap(BaseModel):
    dimension: str
    capability: str
    note: str = ""


class TransferableSkill(BaseModel):
    requirement: str
    evidence_ids: list[str] = Field(default_factory=list)
    level: str = VerificationLevel.UNKNOWN.value
    note: str = ""


class FitAssessment(BaseModel):
    job_id: str
    job_title: str
    company: str
    generated_at: str
    current_fit: FitScore
    career_upside: FitScore
    evidence_coverage: float
    strengths: list[EvidenceInsight] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    transferable_skills: list[TransferableSkill] = Field(default_factory=list)
    positioning: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    narrative: Any = Field(default=None)


# Capability categories used by fit come from the mapping module's adjacency
# table (single source of truth); transferable evidence must equal the
# deterministic mapping layer so the two consumer views can never disagree.


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _adjacent(family: str) -> frozenset[str]:
    from .profile import _ADJACENT

    return _ADJACENT.get(family, frozenset())


def role_family_match(attrs: StructuredJobAttributes, career: CareerProfile) -> float:
    family = attrs.role_family
    if not family or family == "unknown":
        return 0.5
    if family in career.target_role_families:
        return 1.0
    if family in _ADJACENT_for(career):
        return 0.7
    if family == career.current_role_family:
        return 0.5
    return 0.2


def _ADJACENT_for(career: CareerProfile) -> frozenset[str]:
    adjacent: set[str] = set()
    for target in career.target_role_families:
        adjacent.update(_adjacent(target))
    return frozenset(adjacent)


def seniority_match(attrs: StructuredJobAttributes, career: CareerProfile) -> float:
    target = career.target_seniority
    if target <= 0:
        return 0.5
    if attrs.seniority == target:
        return 1.0
    if attrs.seniority == target - 1:
        return 0.7
    if attrs.seniority == target + 1:
        return 0.85
    return 0.35


def ai_match(attrs: StructuredJobAttributes, career: CareerProfile) -> float:
    attr = attrs.ai_relevance
    if attr <= 0:
        return 0.6  # AI-neutral posting: neither penalize nor reward
    closeness = 1.0 - abs(attr - career.ai_exposure) / 10
    return max(0.2, closeness)


def leadership_match(attrs: StructuredJobAttributes, career: CareerProfile) -> float:
    scope = attrs.leadership_scope / 10
    direction_ok = (
        attrs.track == "unknown"
        or (attrs.track.startswith("product") and career.leadership_direction == "product")
        or (attrs.track.startswith("technical") and career.leadership_direction == "technical")
        or (attrs.track.startswith("program") and career.leadership_direction == "program")
        or career.leadership_direction == "program"
    )
    base = 0.7 if direction_ok else 0.4
    return _clamp01(base * (0.4 + 0.6 * scope))


def capability_match(requested: int, owned: int) -> float:
    if requested <= 0:
        return 0.6
    closeness = 1.0 - abs(requested - owned) / 10
    return max(0.2, closeness)


def domain_match(attrs: StructuredJobAttributes, career: CareerProfile) -> float:
    if not attrs.domain:
        return 0.6
    for domain in career.industry_domains:
        if domain in attrs.domain or attrs.domain in domain:
            return 1.0
    return 0.35


def _trajectory(attrs: StructuredJobAttributes, career: CareerProfile) -> tuple[float, dict[str, float]]:
    role_advance = (
        1.0
        if attrs.role_family in career.target_role_families
        else (0.7 if attrs.role_family in _ADJACENT_for(career) else 0.4)
    )
    target = max(1, career.target_seniority)
    if attrs.seniority >= target:
        seniority_advance = 1.0
    elif attrs.seniority == target - 1:
        seniority_advance = 0.6
    else:
        seniority_advance = 0.3
    leadership_advance = min(1.0, attrs.leadership_scope / 10)
    ai_advance = min(1.0, attrs.ai_relevance / 10)
    breakdown = {
        "role_advance": round(role_advance, 3),
        "seniority_advance": round(seniority_advance, 3),
        "leadership_advance": round(leadership_advance, 3),
        "ai_advance": round(ai_advance, 3),
    }
    progress = 0.35 * role_advance + 0.30 * seniority_advance + 0.20 * leadership_advance + 0.15 * ai_advance
    return progress, breakdown


def _evidence_totals(evidence: list[CareerEvidence]) -> tuple[list[str], list[str]]:
    ids = [e.evidence_id for e in evidence]
    sources = sorted({e.source for e in evidence})
    return ids, sources


def analyze_fit(
    job: Job,
    career: CareerProfile,
    attrs: StructuredJobAttributes,
    evidence: list[CareerEvidence],
    *,
    weights: FitWeights | None = None,
    narrative: Any = None,
) -> FitAssessment:
    """Deterministic fit assessment; never mutates state."""
    w = weights or FitWeights()
    total_w = max(w.total, 0.001)

    matches: dict[str, float] = {
        "role_family": role_family_match(attrs, career),
        "ai_relevance": ai_match(attrs, career),
        "leadership": leadership_match(attrs, career),
        "technical_depth": capability_match(attrs.technical_depth, career.technical_depth),
        "product_scope": capability_match(attrs.product_scope, career.product_depth),
        "seniority": seniority_match(attrs, career),
        "domain": domain_match(attrs, career),
    }

    current = sum(m * getattr(w, dim) for dim, m in matches.items()) / total_w
    coverage_map = concept_coverage(evidence, attrs.concepts)
    concepts_total = max(1, len(attrs.concepts))
    covered = sum(1 for ids in coverage_map.values() if ids)
    coverage = covered / concepts_total if attrs.concepts else 0.5
    if attrs.concepts and not evidence:
        coverage = 0.0

    progress, trajectory_breakdown = _trajectory(attrs, career)
    credibility = 0.5 + 0.5 * coverage
    upside = _clamp01(progress * credibility)

    strengths: list[EvidenceInsight] = []
    for concept, evs in coverage_map.items():
        if not evs:
            continue
        best = max(level_index(e.level) / 5 for e in evs)
        strengths.append(
            EvidenceInsight(
                dimension=CONCEPT_LABELS.get(concept, concept.replace("_", " ")),
                score=round(0.4 + 0.6 * best, 3),
                evidence_ids=[e.evidence_id for e in evs],
                note=f"{len(evs)} supporting source(s)",
            )
        )
    for dim, m in matches.items():
        if m >= 0.65:
            strengths.append(
                EvidenceInsight(dimension=dim, score=round(m, 3), evidence_ids=[], note="structural requirement match")
            )

    transferable: list[TransferableSkill] = []
    transferable_concepts: set[str] = set()
    used_coverage = {x.evidence_id for evs in coverage_map.values() for x in evs}
    for concept in attrs.concepts:
        if coverage_map.get(concept):
            continue
        adjacent_evs = adjacent_evidence(evidence, concept, excluded_ids=used_coverage)
        if adjacent_evs:
            transferable.append(
                TransferableSkill(
                    requirement=CONCEPT_LABELS.get(concept, concept.replace("_", " ")),
                    evidence_ids=[e.evidence_id for e in adjacent_evs],
                    level=max((e.level.value for e in adjacent_evs), key=lambda v: level_index(v)),
                    note="adjacent capability evidence",
                )
            )
            transferable_concepts.add(concept)

    gaps: list[Gap] = []
    for concept, evs in coverage_map.items():
        if not evs and concept not in transferable_concepts:
            gaps.append(
                Gap(
                    dimension=CONCEPT_LABELS.get(concept, concept.replace("_", " ")),
                    capability=concept,
                    note="no documented evidence for this capability",
                )
            )
    for dim, m in matches.items():
        if m < 0.4:
            gaps.append(Gap(dimension=dim, capability=dim, note=f"low requirement match ({m:.2f})"))

    risks: list[str] = []
    if job.salary_min_eur is not None and job.salary_min_eur < 120_000:
        risks.append("published bottom of range is below the €120k salary floor")
    if attrs.security_clearance and not any(e.source == "profile" for e in evidence):
        risks.append("security clearance required; no clearance evidence found")
    if attrs.people_management and not coverage_map.get("team_leadership"):
        risks.append("people-management requirement without supporting team-leadership evidence")
    if attrs.seniority and career.target_seniority and attrs.seniority >= career.target_seniority + 2:
        risks.append("posting seniority is 2+ levels above the current target")

    positioning = _build_positioning(attrs, career, strengths, coverage_map, evidence)

    evidence_ids, knowledge_sources = _evidence_totals(evidence)
    return FitAssessment(
        job_id=job.id,
        job_title=job.title,
        company=job.company,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        current_fit=FitScore(score=round(_clamp01(current), 3), breakdown={k: round(v, 3) for k, v in matches.items()}),
        career_upside=FitScore(score=round(upside, 3), breakdown=trajectory_breakdown),
        evidence_coverage=round(coverage, 3),
        strengths=_dedupe_insights(strengths),
        gaps=gaps[:8],
        transferable_skills=transferable[:8],
        positioning=positioning,
        risks=risks[:6],
        evidence_ids_used=evidence_ids,
        knowledge_sources=knowledge_sources,
        narrative=narrative,
    )


def _build_positioning(attrs, career, strengths, coverage_map, evidence):
    return build_positioning(attrs, career, strengths, coverage_map, evidence)


def _dedupe_insights(items: list[EvidenceInsight]) -> list[EvidenceInsight]:
    seen: set[str] = set()
    out: list[EvidenceInsight] = []
    for item in items:
        key = item.dimension
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:12]
