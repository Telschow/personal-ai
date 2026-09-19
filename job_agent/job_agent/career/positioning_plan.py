"""Structured, deterministic positioning plan for a job-specific CV proposal.

The plan is advisory and evidence-referenced. It reorganises, selects and
summarises the user's own evidence — it never adds professional facts. Every
list carries the evidence ids it stands on; GAP and negative-evidence concepts
are surfaced explicitly so nothing unsupported is ever claimed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .achievements import Achievement, derive_achievements
from .evidence import CareerEvidence
from .mapping import CoverageLevel, RequirementMap
from .profile import CareerProfile
from .retrieval import CONCEPT_LABELS


class PositioningPlan(BaseModel):
    headline: str
    themes: list[str] = Field(default_factory=list)
    emphasize_experience: list[str] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    de_emphasize: list[str] = Field(default_factory=list)
    transition_narrative: str | None = None
    risks: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def _supported(mapping: RequirementMap) -> bool:
    return mapping.coverage in (CoverageLevel.STRONG, CoverageLevel.PARTIAL, CoverageLevel.TRANSFERABLE)


def build_positioning_plan(
    career: CareerProfile,
    mapping: list[RequirementMap],
    evidence: list[CareerEvidence],
) -> PositioningPlan:
    """Deterministic positioning plan from evidence and coverage mappings."""
    strong = [m for m in mapping if m.coverage == CoverageLevel.STRONG]
    partial = [m for m in mapping if m.coverage == CoverageLevel.PARTIAL]
    transfer = [m for m in mapping if m.coverage == CoverageLevel.TRANSFERABLE]
    gaps = [m for m in mapping if m.coverage in (CoverageLevel.GAP, CoverageLevel.UNKNOWN)]
    negatives = [m for m in mapping if m.negative_evidence]

    strong_ids = [eid for m in strong for eid in m.evidence_ids]
    all_supported_ids = [eid for m in mapping if _supported(m) for eid in m.evidence_ids]

    headline = _build_headline(career, strong)
    ev_by_id = {e.evidence_id: e for e in evidence}

    emphasized: list[str] = []
    seen: set[str] = set()
    for eid in strong_ids:
        ev = ev_by_id.get(eid)
        if ev is None or eid in seen:
            continue
        seen.add(eid)
        emphasized.append(truncate(ev.claim))
    if not emphasized:
        for m in partial[:3]:
            for eid in m.evidence_ids:
                ev = ev_by_id.get(eid)
                if ev is None or eid in seen:
                    continue
                seen.add(eid)
                emphasized.append(truncate(ev.claim))

    themes = [CONCEPT_LABELS.get(m.capability, m.capability.replace("_", " ")) for m in (strong + transfer)[:5]]

    skills: list[str] = []
    for ev in evidence:
        if ev.source in ("profile", "career_profile") and "skill" in ev.claim.casefold():
            skills.append(ev.claim.replace("Lists the skill: ", "").strip())
        if len(skills) >= 8:
            break

    de_emphasize = [f"{m.requirement}: negative evidence (do not claim)" for m in negatives]
    de_emphasize.extend(f"{m.requirement}: no evidence (gap, unverifiable to claim)" for m in gaps)

    return PositioningPlan(
        headline=headline,
        themes=list(dict.fromkeys(themes)),
        emphasize_experience=emphasized[:6],
        achievements=derive_achievements(evidence, limit=6),
        skills=skills,
        de_emphasize=de_emphasize[:6],
        transition_narrative=_transition_narrative(career),
        risks=[m.requirement for m in negatives] + [m.requirement for m in gaps[:2]],
        gaps=[m.requirement for m in gaps[:4]],
        evidence_refs=list(dict.fromkeys(all_supported_ids)),
    )


def truncate(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _build_headline(career: CareerProfile, strong: list[RequirementMap]) -> str:
    if career.target_role_families:
        family = ", ".join(sorted(career.target_role_families))[:40]
        base = f"{family} profile"
    else:
        base = "Product / engineering leadership profile"
    if strong:
        top = CONCEPT_LABELS.get(strong[0].capability, strong[0].capability.replace("_", " "))
        return f"{base} with documented {top} experience"
    return base


def _transition_narrative(career: CareerProfile) -> str | None:
    current = career.current_role_family
    targets = sorted(career.target_role_families)
    if not current or not targets or current in targets:
        return None
    target = ", ".join(targets)
    return (
        f"Current {current} experience provides a foundation for the target "
        f"{target} direction; emphasise transferable delivery and leadership "
        "evidence in the application."
    )


__all__ = ["PositioningPlan", "build_positioning_plan", "truncate"]
