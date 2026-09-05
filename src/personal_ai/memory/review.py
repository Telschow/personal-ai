"""Exception-only human review of escalated memory candidates (Phase 18).

Normal curation remains fully automatic: ordinary high-confidence,
non-sensitive, durable facts are accepted by the deterministic
:class:`~personal_ai.memory.policy.MemoryPolicy` and written through the same
policy-gated curator. Only the *exception* subset — candidates the policy
escalates (``require_approval``) or that reconciliation flags as ambiguous
(``conflict``) — is parked in the review queue for a human to adjudicate
(:mod:`personal_ai.memory.curation`). This module implements that decision
surface.

Invariants (all prior phases unchanged):

* **the single write path is reused** — ``approve`` re-runs the exact
  deterministic policy, then routes the candidate through
  :class:`~personal_ai.tools.memory.AutomaticMemoryCurator` (→ ``PolicyEngine
  .execute(CURATOR, "propose_memory", ...)`` → the ``memory.write`` approval
  gate → :meth:`MemoryService.apply_candidate` with deterministic
  reconciliation). The human approver grants the gate only for the specific
  pending review row being decided. There is no second writer.
* **no durable memory is ever written on ``reject``** — rejection only
  transitions the review row (a new ``memory_events`` audit record is not
  created because nothing was stored); the decision is otherwise auditable via
  the row itself.
* **no write happens without a pending row** — decisions are guarded so a
  double approval or a stale row cannot produce a memory.
* **approval of a ``conflict`` stores the candidate alongside, never over**
  the existing related memory: the human explicitly adjudicated the
  contradiction, but the existing record is not silently overwritten or
  superseded. The candidate is persisted through the same service primitives
  the canonical create path uses, with its provenance attached.
* **a candidate the policy no longer accepts at decision time** (defer/reject)
  is not written; the row transitions to ``expired`` with a count-only reason.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.defs import CURATOR
from personal_ai.agents.models import Permission
from personal_ai.memory.curation import (
    STATUS_REVIEW_APPROVED,
    STATUS_REVIEW_EXPIRED,
    STATUS_REVIEW_PENDING,
    STATUS_REVIEW_REJECTED,
    CurationStore,
)
from personal_ai.memory.models import (
    AssertionStatus,
    MemoryCandidate,
    MemoryEvidenceRef,
    now_iso,
)
from personal_ai.memory.policy import MemoryDecision, MemoryPolicy
from personal_ai.memory.service import MemoryService


class MemoryReviewService:
    """Approve/reject exception-queue items without a second write route."""

    def __init__(
        self,
        curation_store: CurationStore,
        memory_service: MemoryService,
        *,
        policy: MemoryPolicy | None = None,
    ) -> None:
        self._store = curation_store
        self._service = memory_service
        self._policy = policy or MemoryPolicy()

    # ---- read surface (aggregate-only by default) --------------------------

    def pending_counts(self) -> dict[str, object]:
        """Aggregate-only pending review counts (never statements)."""
        return self._store.review_counts()

    def list_pending(
        self, *, category: str | None = None, limit: int = 200
    ) -> tuple[dict[str, object], ...]:
        """Pending review rows (used only by the explicit ``--show`` surface)."""
        return self._store.list_pending_review(category=category, limit=limit)

    # ---- decisions ---------------------------------------------------------

    def approve(self, review_id: int, *, note: str = "") -> dict[str, object]:
        """Approve one pending review item; writes through the curated path.

        The candidate is reconstructed from the stored row, re-evaluated by
        the deterministic policy, and routed through the same policy-gated
        curator that automatic curation uses. The interactive approval gate is
        granted only while the review row is still pending.
        """
        row = self._store.get_review(review_id)
        if row is None:
            return {"outcome": "not_found", "review_id": review_id}
        if str(row["status"]) != STATUS_REVIEW_PENDING:
            return {
                "outcome": "not_pending",
                "review_id": review_id,
                "status": str(row["status"]),
            }
        candidate = self._candidate_from_row(row)
        if candidate is None:
            self._store.set_review_status(
                review_id,
                STATUS_REVIEW_EXPIRED,
                reviewed_at=now_iso(),
                note="invalid stored candidate",
            )
            return {"outcome": "expired", "review_id": review_id}

        decision = self._policy.evaluate(candidate)
        if decision.decision in (MemoryDecision.DEFER, MemoryDecision.REJECT):
            self._store.set_review_status(
                review_id,
                STATUS_REVIEW_EXPIRED,
                reviewed_at=now_iso(),
                note=f"policy now {decision.reason}",
            )
            return {
                "outcome": "expired",
                "review_id": review_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
            }

        curator = self._curator_for(review_id)
        result = curator.curate(candidate)
        return self._resolve_row(review_id, candidate, result, row, note)

    def reject(self, review_id: int, *, note: str = "") -> dict[str, object]:
        """Reject one pending review item; writes no durable memory."""
        row = self._store.get_review(review_id)
        if row is None:
            return {"outcome": "not_found", "review_id": review_id}
        if str(row["status"]) != STATUS_REVIEW_PENDING:
            return {
                "outcome": "not_pending",
                "review_id": review_id,
                "status": str(row["status"]),
            }
        self._store.set_review_status(
            review_id,
            STATUS_REVIEW_REJECTED,
            reviewed_at=now_iso(),
            note=note,
        )
        return {"outcome": "rejected", "review_id": review_id}

    # ---- internals ---------------------------------------------------------

    def _resolve_row(
        self,
        review_id: int,
        candidate: MemoryCandidate,
        result: dict[str, object],
        row: dict[str, object],
        note: str,
    ) -> dict[str, object]:
        write_status = str(result.get("status", ""))
        if result.get("applied") and write_status in ("created", "updated"):
            accepted = self._store.set_review_status(
                review_id,
                STATUS_REVIEW_APPROVED,
                reviewed_at=now_iso(),
                note=note,
            )
            return {
                "outcome": "approved" if accepted else "not_pending",
                "review_id": review_id,
                "write_status": write_status,
                "memory_id": result.get("memory_id"),
                "superseded_id": result.get("superseded_id"),
                "evidence_added": int(result.get("evidence_added", 0) or 0),
                "category": row["category"],
            }
        if write_status == "conflict":
            memory = self._store_conflict_approved(candidate)
            accepted = self._store.set_review_status(
                review_id,
                STATUS_REVIEW_APPROVED,
                reviewed_at=now_iso(),
                note=note or "approved conflict stored alongside",
            )
            return {
                "outcome": "approved" if accepted else "not_pending",
                "review_id": review_id,
                "write_status": "created",
                "memory_id": memory.memory_id,
                "evidence_added": len(candidate.evidence),
                "category": row["category"],
                "note": "conflict approved: stored alongside the existing record",
            }
        self._store.set_review_status(
            review_id,
            STATUS_REVIEW_EXPIRED,
            reviewed_at=now_iso(),
            note=f"write not applied ({write_status or 'unknown'})",
        )
        return {
            "outcome": "expired",
            "review_id": review_id,
            "write_status": write_status,
        }

    def _curator_for(self, review_id: int) -> object:
        # Imported lazily to avoid a module-level import cycle through the
        # tools layer (tools.memory -> agents -> memory), matched to the lazy
        # import used by curation's default curator.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        return AutomaticMemoryCurator(
            self._service,
            self._policy,
            interactive_approver=self._approver_for(review_id),
        )

    def _approver_for(
        self, review_id: int
    ) -> Callable[[str, str, str], bool]:
        """Grant ``memory.write`` only for this specific pending review item.

        The gate re-checks the review row is still ``pending`` so a decision
        that raced with another action cannot authorize a write.
        """

        def approve(agent_id: str, tool_name: str, permission: str) -> bool:
            row = self._store.get_review(review_id)
            return (
                row is not None
                and str(row["status"]) == STATUS_REVIEW_PENDING
                and agent_id == CURATOR.id
                and tool_name == "propose_memory"
                and permission == Permission.MEMORY_WRITE.value
            )

        return approve

    def _candidate_from_row(
        self, row: dict[str, object]
    ) -> MemoryCandidate | None:
        raw = row.get("candidate_json")
        if isinstance(raw, dict) and raw:
            try:
                return MemoryCandidate.from_dict(raw)
            except Exception:  # noqa: BLE001 - untrusted stored JSON
                return None
        try:
            evidence = tuple(
                MemoryEvidenceRef(**ref)  # type: ignore[arg-type]
                for ref in row.get("evidence_json", [])  # type: ignore[union-attr]
                if isinstance(ref, dict)
            )
            return MemoryCandidate(
                statement=str(row["statement"]),
                kind=row["kind"],  # type: ignore[arg-type]
                confidence=float(row["confidence"]),
                utility=float(row["importance"]),
                temporal_scope=row["temporal_scope"],  # type: ignore[arg-type]
                assertion_status=AssertionStatus.ASSERTED,
                evidence=evidence,
            )
        except Exception:  # noqa: BLE001 - malformed stored row
            return None

    def _store_conflict_approved(self, candidate: MemoryCandidate) -> object:
        """Persist an explicitly approved conflicting candidate alongside.

        The candidate already passed the deterministic policy when it was
        queued (reconciliation blocked it, not policy). The human's approval
        directs storage of the new fact *without* overwriting the existing
        record — no silent supersession. Uses the same service primitives the
        service's own create path composes (never raw SQL, never a new
        memory-insertion function).
        """
        memory = self._service.create_candidate_memory(candidate)
        self._service.add_evidence(memory.memory_id, candidate.evidence)
        return memory


__all__: tuple[str, ...] = (
    "MemoryReviewService",
)