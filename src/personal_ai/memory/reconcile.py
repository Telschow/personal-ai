"""Deterministic reconciliation of a candidate against existing memories.

Before a policy-accepted candidate is written, it is reconciled against the
active global memories already stored so that the same fact is never stored
twice:

* an exact duplicate (normalized statement, same kind) gains **evidence**
  instead of a second record;
* a closely related fact backed by clearly stronger, newer evidence
  **supersedes** the older record (the old record is kept for provenance but
  marked ``superseded``);
* a closely related fact without a clear winner is a **conflict** — it must
  not be silently overwritten and is surfaced for human review;
* otherwise a new **created** record.

Reconciliation is pure and lexical (normalized token overlap): no vector
search, no embeddings, no LLM judgment. It reads through the memory service
and never writes directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from personal_ai.memory.models import MemoryCandidate, MemoryStatus

RELATED_OVERLAP_MIN = 0.5
SUPERSEDE_CONFIDENCE_GAP = 0.2

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


class ReconcileAction(Enum):
    """The reconciled outcome for a candidate."""

    CREATE = "create"
    ADD_EVIDENCE = "add_evidence"
    SUPERSEDE = "supersede"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    """The deterministic plan for a candidate before any write happens."""

    action: ReconcileAction
    target_id: str | None = None
    reason: str = ""


class MemoryServiceLike(Protocol):
    """The read surface the reconciler needs (satisfied by MemoryService)."""

    def list(
        self, status: MemoryStatus | str | None = None
    ) -> object: ...  # pragma: no cover

    def evidence_for(self, memory_id: str) -> object: ...  # pragma: no cover


class MemoryReconciler:
    """Plans how a candidate should be written, deterministically."""

    def __init__(self, memory_service: MemoryServiceLike) -> None:
        self._service = memory_service

    def plan(self, candidate: MemoryCandidate) -> ReconciliationPlan:
        """Return the write plan for a candidate without persisting anything."""
        related = self._related(candidate)
        if not related:
            return ReconciliationPlan(ReconcileAction.CREATE, reason="no_existing_fact")

        best = max(
            related,
            key=lambda memory: (memory.updated_at or "", memory.memory_id),
        )
        if normalize_text(best.content) == normalize_text(candidate.statement):
            if not candidate.evidence:
                return ReconciliationPlan(
                    ReconcileAction.CONFLICT,
                    target_id=best.memory_id,
                    reason="duplicate_without_new_evidence",
                )
            return ReconciliationPlan(
                ReconcileAction.ADD_EVIDENCE,
                target_id=best.memory_id,
                reason="exact_duplicate",
            )

        overlap = token_overlap(
            normalize_text(best.content), normalize_text(candidate.statement)
        )
        if overlap < RELATED_OVERLAP_MIN:
            return ReconciliationPlan(ReconcileAction.CREATE, reason="no_existing_fact")

        candidate_newer = (
            candidate.confidence >= best.confidence + SUPERSEDE_CONFIDENCE_GAP
        )
        evidence_is_newer = self._newest_source_time(
            candidate
        ) >= self._newest_source_time(best)
        if candidate_newer and evidence_is_newer:
            return ReconciliationPlan(
                ReconcileAction.SUPERSEDE,
                target_id=best.memory_id,
                reason="stronger_and_newer_evidence",
            )
        return ReconciliationPlan(
            ReconcileAction.CONFLICT,
            target_id=best.memory_id,
            reason="ambiguous_related_fact",
        )

    def _related(self, candidate: MemoryCandidate) -> tuple[object, ...]:
        """Active, global, same-kind memories whose text overlaps candidate."""
        out: list[object] = []
        for memory in self._service.list(MemoryStatus.ACTIVE):  # type: ignore[union-attr]
            if getattr(memory, "scope", None).value != "global":
                continue
            if getattr(memory, "kind", None).value != candidate.kind.value:
                continue
            if memory.status.value != "active":
                continue
            overlap = token_overlap(
                normalize_text(memory.content), normalize_text(candidate.statement)
            )
            if overlap >= RELATED_OVERLAP_MIN:
                out.append(memory)
        return tuple(out)

    def _newest_source_time(self, memory_or_candidate: object) -> float:
        """Latest evidence timestamp (seconds since epoch); 0.0 when absent."""
        if hasattr(memory_or_candidate, "evidence"):
            refs = memory_or_candidate.evidence
            stamps = [ref.source_timestamp for ref in refs if ref.source_timestamp]
        else:
            rows = self._service.evidence_for(memory_or_candidate.memory_id)  # type: ignore[union-attr]
            stamps = [
                row["source_timestamp"] for row in rows if row["source_timestamp"]
            ]
        best = 0.0
        for stamp in stamps:
            try:
                parsed = datetime.fromisoformat(stamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                best = max(best, parsed.timestamp())
            except ValueError:
                continue
        return best


def normalize_text(text: str) -> str:
    """Lowercase, alphanumeric-only normalized form for equality matching."""
    return " ".join(_WORD_RE.findall(text.lower()))


def token_overlap(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    shared = len(a_tokens & b_tokens)
    return shared / min(len(a_tokens), len(b_tokens))
