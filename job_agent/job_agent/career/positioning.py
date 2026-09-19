"""Deterministic positioning recommendations for a job fit.

Rules are fixed and evidence-grounded; nothing is claimed that the evidence
window does not support. Positioning is advisory only — never a submission
or application step.
"""

from __future__ import annotations

from .evidence import VerificationLevel, level_index
from .profile import CareerProfile
from .requirements import StructuredJobAttributes
from .retrieval import CONCEPT_LABELS

POSITIONING_HONESTY_PREFIX = "Only claim what the evidence supports"
POSITIONING_HONESTY_SUFFIX = "if you communicate about this role"


def build_positioning(
    attrs: StructuredJobAttributes,
    career: CareerProfile,
    strengths: list,
    coverage: dict[str, list],
    evidence: list,
) -> list[str]:
    """Return a small, deterministic set of honest positioning suggestions."""
    out: list[str] = []
    by_concept = {k: set(e.evidence_id for e in v) for k, v in coverage.items()}

    strong_concepts = [
        CONCEPT_LABELS.get(c, c.replace("_", " "))
        for c, ids in by_concept.items()
        if ids and any(level_index(e.level) >= level_index(VerificationLevel.DOCUMENTED) for e in coverage.get(c, []))
    ]
    if strong_concepts:
        out.append("Lead with documented evidence in: " + ", ".join(sorted(set(strong_concepts))[:4]) + ".")

    if attrs.agentic_ai_relevance >= 7 and career.agentic_ai_exposure < 6:
        out.append(
            "The role emphasizes agentic AI; lead with concrete system-wide "
            "AI experience, not stand-alone demos, unless documented."
        )

    if attrs.security_clearance and not any(e.source in ("profile", "career_profile") for e in evidence):
        out.append("The role requires security clearance; verify your clearance status before discussing this role.")

    if attrs.languages_required and career.languages:
        missing = [
            lang
            for lang in attrs.languages_required
            if not any(career_l.startswith(lang) or lang in career_l for career_l in career.languages)
        ]
        if missing:
            out.append(
                f"Language requirement ({', '.join(missing)}) is not documented "
                "in the profile; confirm before applying."
            )

    if attrs.location_mode == "onsite" and not career.willingness_to_relocate:
        out.append("Role is on-site and relocation willingness is not confirmed.")

    if strengths:
        out.append(
            f"{POSITIONING_HONESTY_PREFIX} (top strength: {strengths[0].dimension}), {POSITIONING_HONESTY_SUFFIX}."
        )
    return out
