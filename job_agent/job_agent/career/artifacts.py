"""CV artifact model and deterministic status logic.

A :class:`CVArtifact` is a *proposal* — the tailored variant of the user's
own experience. It carries the evidence ids each bullet stands on, so its
status can be decided mechanically:

- every bullet has a stored evidence id  -> VALIDATED
- any bullet lacks evidence or is mathematically unverifiable
  -> REQUIRES_REVIEW (never silently approved)
- APPROVED / REJECTED are human decisions applied on top and are terminal.

No inference, no model calls here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .validation import ClaimValidation, validate_claim


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REQUIRES_REVIEW = "requires_review"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Bullet:
    text: str
    evidence_id: str | None = None


class CVArtifact(BaseModel):
    artifact_id: str
    job_id: str
    status: ArtifactStatus = ArtifactStatus.DRAFT
    headline: str = ""
    summary: str = ""
    bullets: list[Bullet] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    note: str = "PROPOSAL - NOT APPROVED"

    def check_compliance(
        self,
        evidence: list,
    ) -> tuple[ArtifactStatus, list[ClaimValidation]]:
        """Mechanically re-check every bullet against evidence."""
        verdicts = [validate_claim(b.text, evidence) for b in self.bullets]
        problems = [v for v in verdicts if v.problem]
        if problems:
            return ArtifactStatus.REQUIRES_REVIEW, verdicts
        # Every bullet must actually reference a stored evidence id.
        known = {e.evidence_id for e in evidence}
        if any(b.evidence_id and b.evidence_id not in known for b in self.bullets):
            return ArtifactStatus.REQUIRES_REVIEW, verdicts
        return ArtifactStatus.VALIDATED, verdicts


__all__ = ["ArtifactStatus", "Bullet", "CVArtifact"]
