"""Requirement → capability → evidence mapping with explicit coverage.

Deterministic. For every job requirement concept we produce a
:class:`RequirementMap` carrying:

- the coverage level (STRONG / PARTIAL / TRANSFERABLE / GAP / UNKNOWN),
- a confidence estimate,
- the concrete evidence ids that support it (empty only for GAP/UNKNOWN),
- and an explicit negative flag when the evidence *contradicts* the
  requirement (NEGATIVE EVIDENCE is never confused with NO EVIDENCE).

The mapping is advisory input to tailoring and positioning; it never invents
facts. GAP (no evidence at all) is deliberately distinguished from
TRANSFERABLE (no direct evidence but adjacent capability evidence exists).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from .evidence import CareerEvidence, VerificationLevel, level_index
from .profile import CareerProfile
from .requirements import CONCEPT_TERMS, StructuredJobAttributes
from .retrieval import CONCEPT_LABELS

# Fixed adjacency table: concept -> capability categories that can count as
# transferable evidence when direct evidence is missing.
_ADJACENT_CATEGORIES: dict[str, frozenset[str]] = {
    "ai_systems": frozenset({"ai", "technical", "systems"}),
    "product_strategy": frozenset({"product", "communication"}),
    "stakeholder_management": frozenset({"communication", "product", "leadership"}),
    "team_leadership": frozenset({"leadership", "communication"}),
    "program_management": frozenset({"program", "product"}),
    "systems_engineering": frozenset({"systems", "engineering", "technical"}),
    "safety_critical": frozenset({"systems", "engineering"}),
    "autonomous_driving": frozenset({"domain", "ai", "systems"}),
    "backend_engineering": frozenset({"technical", "engineering"}),
    "embedded_engineering": frozenset({"technical", "engineering"}),
    "data_engineering": frozenset({"technical", "ai"}),
    "ai_tooling": frozenset({"ai", "agentic_ai", "technical"}),
    "user_research": frozenset({"product", "communication"}),
    "gtm": frozenset({"communication", "product"}),
    "regulation": frozenset({"systems", "communication"}),
}

_NEGATIVE_TERMS = (
    "no experience",
    "not familiar",
    "never worked",
    "no knowledge",
    "does not have",
    "lack of",
    "haven't",
)

# Concepts whose *adjacent* evidence must additionally contain a concept
# synonym in the claim text. A generic "Development Engineer" role is not
# backend/data/embedded engineering evidence; without a direct term hint the
# concept stays GAP instead of being inflated to TRANSFERABLE.
_ADJACENT_REQUIRES_DIRECT_HINT: frozenset[str] = frozenset(
    {"backend_engineering", "embedded_engineering", "data_engineering"}
)

# TRANSFERABLE is adjacency, never a direct capability claim: its confidence
# is deliberately capped so adjacent evidence cannot reach direct-evidence
# confidence levels.
TRANSFERABLE_CONFIDENCE_CAP = 0.7


class CoverageLevel(StrEnum):
    STRONG = "STRONG"
    PARTIAL = "PARTIAL"
    TRANSFERABLE = "TRANSFERABLE"
    GAP = "GAP"
    UNKNOWN = "UNKNOWN"


class RequirementMap(BaseModel):
    """One requirement → capability mapping with attributable coverage.

    ``layer`` records whether the mapping was produced by the deterministic
    floor (``deterministic``) or upgraded by the semantic layer
    (``semantic``); ``reasoning`` carries the human-grounded justification
    for the semantic refinement.
    """

    requirement: str
    capability: str
    coverage: CoverageLevel
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    negative_evidence: bool = False
    note: str = ""
    layer: str = "deterministic"
    reasoning: str = ""


def _concept_haystack(ev: CareerEvidence) -> str:
    return " ".join(s for s in (ev.claim, *(ev.keywords or []), *(ev.categories or [])) if s)


def _has_concept_hint(ev: CareerEvidence, concept: str) -> bool:
    """True when the evidence claim/keywords carry a concept synonym.

    Category labels are *not* enough for the narrow concepts in
    ``_ADJACENT_REQUIRES_DIRECT_HINT``: a generic "engineer" category must not
    count as backend-engineering adjacency unless the claim itself mentions a
    backend concept (REST, microservices, SQL, …).
    """
    text = f"{ev.claim} {' '.join(ev.keywords or [])}".casefold()
    return any(term in text for term in CONCEPT_TERMS.get(concept, (concept.replace("_", " "),)))


def adjacent_evidence(
    evidence: list[CareerEvidence],
    concept: str,
    excluded_ids: set[str],
) -> list[CareerEvidence]:
    """Deterministic adjacency for a concept.

    Shared by :mod:`career.mapping` and :mod:`career.fit` so the two consumer
    views can never disagree about which evidence is adjacent to a concept.
    """
    wanted = _ADJACENT_CATEGORIES.get(concept, frozenset())
    if not wanted:
        return []
    result = [
        e
        for e in evidence
        if e.evidence_id not in excluded_ids
        and wanted.intersection(e.categories)
        and not _is_negative(e, concept)
        and (concept not in _ADJACENT_REQUIRES_DIRECT_HINT or _has_concept_hint(e, concept))
    ]
    return result[:4]


def _is_negative(ev: CareerEvidence, concept: str) -> bool:
    """Concept-aware negative-evidence detection.

    Generic self-negating phrases ("no experience", "not experienced", ...)
    plus per-concept negations such as "no <data engineering> experience".
    A bare keyword overlap alone (`_direct_hits`) is not evidence of skill;
    an explicit negation of a concept term turns the overlap into evidence of
    a real gap.
    """
    text = _concept_haystack(ev).casefold()
    if any(t in text for t in _NEGATIVE_TERMS):
        return True
    for term in CONCEPT_TERMS.get(concept, (concept.replace("_", " "),)):
        pattern = re.compile(
            rf"(?:no|zero|without)\s+{re.escape(term.casefold())}\s+"
            r"(?:experience|background|exposure|skill|knowledge|track.?record)",
            re.IGNORECASE,
        )
        if pattern.search(text):
            return True
    return False


def _direct_hits(
    evidence: list[CareerEvidence],
    concept: str,
) -> list[CareerEvidence]:
    terms = CONCEPT_TERMS.get(concept, (concept.replace("_", " "),))
    hits: list[CareerEvidence] = []
    for ev in evidence:
        hay = _concept_haystack(ev).casefold()
        if any(t in hay for t in terms):
            hits.append(ev)
    return hits


def _best_level(hits: list[CareerEvidence]) -> VerificationLevel:
    return max((h.level for h in hits), key=level_index)


def _confidence_for(level: VerificationLevel, n: int, negative: bool) -> float:
    if negative:
        return 0.2
    base = {
        VerificationLevel.VERIFIED: 0.95,
        VerificationLevel.USER_CONFIRMED: 0.9,
        VerificationLevel.DOCUMENTED: 0.85,
        VerificationLevel.CANDIDATE: 0.6,
        VerificationLevel.INFERRED: 0.5,
        VerificationLevel.UNKNOWN: 0.1,
    }[level]
    return round(min(1.0, base + 0.03 * (n - 1)), 3)


def map_requirements(
    attrs: StructuredJobAttributes,
    career: CareerProfile,
    evidence: list[CareerEvidence] | None = None,
) -> list[RequirementMap]:
    """Build the requirement→capability map for a job's bounded concept set.

    Deterministic and stable: same inputs ⇒ same ordering and coverage. The
    role-family structural requirement maps against the career's target role
    family (no evidence needed: that is a profile fact, not a fabricated
    capability claim).
    """
    evidence = evidence or []
    if not attrs.concepts:
        return []

    maps: list[RequirementMap] = []
    for concept in attrs.concepts:
        label = CONCEPT_LABELS.get(concept, concept.replace("_", " "))
        direct = _direct_hits(evidence, concept)

        # Negative evidence is decisive and checked before keyword overlap: a
        # claim that literally says "no data engineering experience" must never
        # be read as a STRONG data-engineering capability.
        negatives = [e for e in direct if _is_negative(e, concept)]
        if negatives:
            maps.append(
                RequirementMap(
                    requirement=label,
                    capability=concept,
                    coverage=CoverageLevel.GAP,
                    confidence=0.2,
                    evidence_ids=sorted(e.evidence_id for e in negatives),
                    negative_evidence=True,
                    note="explicit negative evidence: do not claim this capability",
                )
            )
            continue

        non_negative = [e for e in direct if not _is_negative(e, concept)]
        if non_negative:
            level = _best_level(non_negative)
            coverage = (
                CoverageLevel.STRONG
                if level_index(level) >= level_index(VerificationLevel.DOCUMENTED)
                else CoverageLevel.PARTIAL
            )
            maps.append(
                RequirementMap(
                    requirement=label,
                    capability=concept,
                    coverage=coverage,
                    confidence=_confidence_for(level, len(non_negative), False),
                    evidence_ids=[e.evidence_id for e in non_negative],
                    negative_evidence=False,
                    note=f"{len(non_negative)} direct evidence item(s); best {level.value}",
                )
            )
            continue

        # No direct evidence: look for transferable signals from adjacent
        # capability categories, then distinguish TRANSFERABLE from GAP.
        used = {e.evidence_id for e in direct}
        adjacent = adjacent_evidence(evidence, concept, excluded_ids=used)
        if adjacent:
            level = _best_level(adjacent)
            maps.append(
                RequirementMap(
                    requirement=label,
                    capability=concept,
                    coverage=CoverageLevel.TRANSFERABLE,
                    confidence=min(TRANSFERABLE_CONFIDENCE_CAP, _confidence_for(level, len(adjacent), False)),
                    evidence_ids=[e.evidence_id for e in adjacent],
                    note=f"{len(adjacent)} adjacent-capability evidence item(s); best {level.value}",
                )
            )
        else:
            coverage = CoverageLevel.UNKNOWN if _profile_is_sparse(career) else CoverageLevel.GAP
            maps.append(
                RequirementMap(
                    requirement=label,
                    capability=concept,
                    coverage=coverage,
                    confidence=0.1 if coverage is CoverageLevel.UNKNOWN else 0.3,
                    evidence_ids=[],
                    note=(
                        "no evidence and no usable profile base facts"
                        if coverage is CoverageLevel.UNKNOWN
                        else "no evidence found for this requirement"
                    ),
                )
            )
    return maps


def _profile_is_sparse(career: CareerProfile) -> bool:
    """An empty/near-empty profile forces UNKNOWN rather than GAP for
    un-evidenced concepts: with no base facts we cannot tell a real gap from
    missing data. With any profile content (name/experience/education) the
    absence of evidence is a genuine GAP."""
    return not career.name and not career.education and "target_role_families" in career.inferred_fields


__all__ = [
    "CoverageLevel",
    "RequirementMap",
    "TRANSFERABLE_CONFIDENCE_CAP",
    "adjacent_evidence",
    "map_requirements",
]
