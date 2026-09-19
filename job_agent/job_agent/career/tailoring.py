"""Tailoring orchestration: deterministic plan + optional LLM proposal.

Composes documents → reconciliation → mapping → positioning → (optionally) an
LLM proposal → a validated :class:`CVArtifact`. Both paths are proposal-only:

- the deterministic path builds a fully evidence-backed artifact from the
  positioning plan (every bullet carries its stored evidence id),
- the LLM path additionally rewrites the user's own evidence into concise CV
  lines and is re-checked by :mod:`career.validation` before use.

Neither path can approve, write, or submit anything. The artifact is always
labelled ``PROPOSAL - NOT APPROVED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .artifacts import (
    ArtifactStatus,
    Bullet,
    CVArtifact,
)
from .cv_llm import CvGenerationClient, CvProposal
from .evidence import CareerEvidence
from .mapping import RequirementMap, map_requirements
from .positioning_plan import PositioningPlan, build_positioning_plan
from .profile import CareerProfile
from .requirements import StructuredJobAttributes
from .semantic_mapping import (
    SemanticMappingClient,
    SemanticMapResult,
    refine_mapping,
)
from .validation import ClaimValidation


@dataclass(frozen=True)
class TailorResult:
    artifact: CVArtifact
    plan: PositioningPlan
    status: ArtifactStatus
    verdicts: list[ClaimValidation]
    source: str  # "llm" | "deterministic"
    mapping: list[RequirementMap] = field(default_factory=list)
    semantic: SemanticMapResult | None = None


def build_tailoring_plan(
    career: CareerProfile,
    attrs: StructuredJobAttributes,
    evidence: list[CareerEvidence],
    *,
    semantic_client: SemanticMappingClient | None = None,
) -> tuple[list[RequirementMap], PositioningPlan, SemanticMapResult | None]:
    mapping = map_requirements(attrs, career, evidence)
    semantic = refine_mapping(mapping, evidence, client=semantic_client)
    plan = build_positioning_plan(career, semantic.mapping, evidence)
    return semantic.mapping, plan, semantic


def _artifact_from_plan(
    plan: PositioningPlan,
    evidence: list[CareerEvidence],
    job_id: str,
) -> CVArtifact:
    by_id = {e.evidence_id: e for e in evidence}
    bullets: list[Bullet] = []
    seen: set[str] = set()
    for eid in plan.evidence_refs:
        ev = by_id.get(eid)
        if ev is None or eid in seen:
            continue
        seen.add(eid)
        bullets.append(Bullet(text=ev.claim.rstrip(".")[:240], evidence_id=eid))
        if len(bullets) >= 6:
            break
    headline = plan.headline or "career profile"
    return CVArtifact(
        artifact_id=f"tailor:{job_id}:{len(plan.evidence_refs)}:{len(plan.themes)}",
        job_id=job_id,
        headline=headline,
        summary="; ".join(plan.themes[:3]) if plan.themes else plan.transition_narrative or "",
        bullets=bullets,
        evidence_ids=list(plan.evidence_refs),
    )


def _artifact_from_proposal(proposal: CvProposal, job_id: str) -> CVArtifact:
    return CVArtifact(
        artifact_id=f"tailor:{job_id}:llm",
        job_id=job_id,
        headline=proposal.headline,
        summary=proposal.summary,
        bullets=[Bullet(text=b.text, evidence_id=b.evidence_id) for b in proposal.bullets],
        evidence_ids=[b.evidence_id for b in proposal.bullets],
    )


def tailor(
    career: CareerProfile,
    attrs: StructuredJobAttributes,
    evidence: list[CareerEvidence],
    *,
    job_id: str,
    client: CvGenerationClient | None = None,
    semantic_client: SemanticMappingClient | None = None,
) -> TailorResult:
    """Produce a proposal-only tailored CV artifact.

    With a client, an LLM proposal is attempted first and always re-checked.
    On any LLM failure, or without a client, the deterministic evidence-backed
    artifact is produced instead (never an unvalidated proposal).
    """
    mapping, plan, semantic = build_tailoring_plan(career, attrs, evidence, semantic_client=semantic_client)

    if client is not None:
        try:
            proposal = client.generate_proposal(
                job=attrs.model_dump(),
                evidence_items=compact_for_prompt(evidence),
                evidence_ids=[e.evidence_id for e in evidence],
            )
        except Exception:  # noqa: BLE001 - a failed proposal must not kill tailoring
            proposal = None
        if proposal is not None:
            artifact = _artifact_from_proposal(proposal, job_id)
            status, verdicts = artifact.check_compliance(evidence)
            # An LLM proposal that fails mechanical validation is *discarded*,
            # never surfaced (REQUIRES_REVIEW only exists for deterministic
            # human-first artifacts).
            if status is ArtifactStatus.VALIDATED:
                return TailorResult(
                    artifact=artifact.model_copy(update={"status": status}),
                    plan=plan,
                    status=status,
                    verdicts=verdicts,
                    source="llm",
                    mapping=mapping,
                    semantic=semantic,
                )

    artifact = _artifact_from_plan(plan, evidence, job_id)
    status, verdicts = artifact.check_compliance(evidence)
    return TailorResult(
        artifact=artifact.model_copy(update={"status": status}),
        plan=plan,
        status=status,
        verdicts=verdicts,
        source="deterministic",
        mapping=mapping,
        semantic=semantic,
    )


def compact_for_prompt(evidence: list[CareerEvidence], *, limit: int = 12) -> list[dict[str, Any]]:
    """Bounded, id-only prompt projection of the evidence window."""
    items = sorted(evidence, key=lambda e: (e.level.value, len(e.claim)), reverse=True)
    return [
        {
            "evidence_id": e.evidence_id,
            "claim": e.claim[:600],
            "level": e.level.value,
            "source_type": e.source_type,
        }
        for e in items[:limit]
    ]


__all__ = ["TailorResult", "build_tailoring_plan", "compact_for_prompt", "tailor"]
