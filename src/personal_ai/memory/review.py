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
  double approval or a stale row cannot produce a memory. The ``memory.write``
  gate is bound to the specific pending review row for *every* policy outcome
  (``require_approval`` via the interactive approver and ``accept``-classified
  conflicts via the automatic approver), so no approval path re-arms the gate
  on policy alone.
* **decisions are serialized per service instance** — a ``threading.RLock``
  means concurrent threads cannot both pass the pending gate and both write;
  the guarded pending->terminal UPDATE additionally makes a second decider on
  any row observe ``not_pending``.
* **approval of a ``conflict`` stores the candidate alongside, never over**
  the existing related memory: the human explicitly adjudicated the
  contradiction, but the existing record is not silently overwritten or
  superseded. The candidate is persisted through the same service primitives
  the canonical create path uses, with its provenance attached.
* **a candidate the policy no longer accepts at decision time** (defer/reject)
  is not written; the row transitions to ``expired`` with a count-only reason.

Phase 27 adds two durable guarantees that harden the human adjudication
boundary without changing any of the above security/correctness semantics:

* **atomic adjudication (one transaction)** — because ``CurationStore`` and
  ``MemoryStore`` share a single SQLite connection, the whole decision
  (pending-row validation, deterministic policy re-evaluation, the
  policy-gated memory write, reconciliation, the audit event, and the pending
  -> terminal review transition) is executed inside one explicit
  ``BEGIN IMMEDIATE`` ... ``COMMIT`` transaction. The stores' internal
  auto-commits are transparently deferred while the transaction is open (see
  :class:`_AdjudicationConnection`), so the unit commits all-or-nothing: a
  crash before ``COMMIT`` rolls everything back, never leaving
  ``approved``/no-memory or pending/duplicate-memory durable states.
* **durable adjudication audit trail** — every terminal decision appends
  exactly one content-free event (identifiers + aggregate metadata + a
  SHA-256 statement digest) to ``memory_review_audit``; a repeated decision is
  ``not_pending`` and writes no further event.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Callable
from contextlib import contextmanager

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


class MemoryReviewTransactionError(RuntimeError):
    """Raised when an adjudication transaction cannot be committed cleanly."""


def _statement_hash(statement: str) -> str:
    """Deterministic cryptographic digest of a reviewed statement.

    Used in the audit log in place of the statement text so review history
    keeps its integrity while never storing personal content.
    """
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


class _AdjudicationConnection:
    """A transparent ``sqlite3.Connection`` stand-in that defers commits.

    ``CurationStore`` and ``MemoryStore`` each call ``self._connection.commit()``
    after every write. When an adjudication decision runs, those intermediate
    commits would otherwise break the atomicity of the single shared
    transaction. This proxy passes everything through to the real connection
    except ``commit()``/``rollback()``: while an adjudication transaction is
    open (:attr:`_in_txn`), store-internal commits are deferred so all DML
    accumulates in the one explicit transaction controlled by
    :func:`_adjudication_transaction`. Outside the transaction behaviour is
    byte-for-byte identical to the raw connection.
    """

    def __init__(self, raw: sqlite3.Connection) -> None:
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_in_txn", False)

    def __getattr__(self, name: str):
        return getattr(self._raw, name)

    def commit(self) -> None:
        if not self._in_txn:
            self._raw.commit()

    def rollback(self) -> None:
        if not self._in_txn:
            self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    @property
    def raw(self) -> sqlite3.Connection:
        return self._raw

    @property
    def in_transaction(self) -> bool:
        return self._in_txn


@contextmanager
def _adjudication_transaction(connection: _AdjudicationConnection):
    """Execute a body inside one atomic adjudication transaction.

    Uses ``BEGIN IMMEDIATE`` so the connection holds the write lock from the
    start (cross-process serialization). Store-internal commits are deferred
    by the proxy while the transaction is open. On success the single
    ``COMMIT`` makes the memory write, audit event, and review transition
    durable together; on failure ``ROLLBACK`` undoes all of them. A crashed
    process between the writes and the ``COMMIT`` is recovered by SQLite's
    journal/WAL at next open (all rolled back). If ``COMMIT`` itself fails we
    attempt a rollback and surface :class:`MemoryReviewTransactionError` so the
    caller never reports a decision as committed when it may not be.
    """
    connection._in_txn = True
    connection.raw.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        try:
            connection.raw.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        try:
            connection.raw.execute("COMMIT")
        except BaseException as exc:
            try:
                connection.raw.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise MemoryReviewTransactionError(
                "adjudication commit failed; no decision reported as committed"
            ) from exc
    finally:
        connection._in_txn = False


class MemoryReviewService:
    """Approve/reject exception-queue items without a second write route."""

    def __init__(
        self,
        curation_store: CurationStore,
        memory_service: MemoryService,
        *,
        policy: MemoryPolicy | None = None,
        connection: _AdjudicationConnection | None = None,
        actor: str = "human",
    ) -> None:
        self._store = curation_store
        self._service = memory_service
        self._policy = policy or MemoryPolicy()
        self._connection = connection
        self._actor = actor
        # Serializes approve/reject decisions within one service instance so
        # concurrent threads deciding the same row cannot both pass the
        # pending gate and both write. Cross-instance/cross-process races are
        # additionally guarded by the guarded pending->terminal UPDATE (a
        # second decider observes ``not_pending``) and by the reconciler's
        # idempotent same-statement merge inside ``apply_candidate``.
        self._lock = threading.RLock()

    # ---- read surface (aggregate-only by default) --------------------------

    def pending_counts(self) -> dict[str, object]:
        """Aggregate-only pending review counts (never statements)."""
        return self._store.review_counts()

    def list_pending(
        self, *, category: str | None = None, limit: int = 200
    ) -> tuple[dict[str, object], ...]:
        """Pending review rows (used only by the explicit ``--show`` surface)."""
        return self._store.list_pending_review(category=category, limit=limit)

    # ---- audit observability (Phase 28/30) -----------------------------------

    def audit(
        self,
        *,
        review_id: int | None = None,
        limit: int = 200,
        recent: bool = True,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Privacy-safe recent audit events.

        Returns operational metadata only (``review_id``, ``action``,
        ``outcome``, ``actor``, ``policy_category``, ``memory_id``,
        ``created_at``). The internal row ``id`` and the ``statement_hash``
        digest are deliberately omitted so this surface cannot become a
        data-exfiltration channel.

        Optional time-window filtering:
        - ``since``: inclusive lower bound on ``created_at`` (ISO-8601, normalized to UTC)
        - ``until``: inclusive upper bound on ``created_at`` (ISO-8601, normalized to UTC)
        """
        rows = self._store.review_audit(
            review_id=review_id, limit=limit, recent=recent, since=since, until=until
        )
        return tuple(
            {
                "review_id": row["review_id"],
                "action": row["action"],
                "outcome": row["outcome"],
                "actor": row["actor"],
                "policy_category": row.get("policy_category", ""),
                "memory_id": row.get("memory_id"),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def audit_counts(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, object]:
        """Aggregate-only audit counts (never statements or evidence).

        Optional time-window filtering:
        - ``since``: inclusive lower bound on ``created_at`` (ISO-8601, normalized to UTC)
        - ``until``: inclusive upper bound on ``created_at`` (ISO-8601, normalized to UTC)
        """
        return self._store.review_audit_counts(since=since, until=until)

    # ---- decisions ---------------------------------------------------------

    def approve(
        self, review_id: int, *, note: str = "", actor: str | None = None
    ) -> dict[str, object]:
        """Approve one pending review item; writes through the curated path.

        The candidate is reconstructed from the stored row, re-evaluated by
        the deterministic policy, and routed through the same policy-gated
        curator that automatic curation uses. The interactive approval gate is
        granted only while the review row is still pending. The whole decision
        is serialized per service instance so two threads cannot both
        authorize a write for the same row.

        When a shared connection proxy was supplied, the memory write, audit
        event, and review transition all commit in one atomic transaction, so
        the returned outcome always reflects the durable committed state.
        """
        with self._lock:
            return self._guard(self._approve_impl, review_id, note=note, actor=actor)

    def _guard(
        self, fn: Callable[..., dict[str, object]], *args, **kwargs
    ) -> dict[str, object]:
        """Run a decision inside the adjudication transaction (if available).

        Catches transaction/commit failures and returns a conservative error
        outcome so the caller never reports a decision as committed when the
        durable state may be unchanged.
        """
        if self._connection is None:
            return fn(*args, **kwargs)
        try:
            with _adjudication_transaction(self._connection):
                return fn(*args, **kwargs)
        except MemoryReviewTransactionError as exc:
            return {
                "outcome": "error",
                "reason": str(exc),
            }

    def _approve_impl(
        self, review_id: int, *, note: str = "", actor: str | None = None
    ) -> dict[str, object]:
        row = self._store.get_review(review_id)
        if row is None:
            return {
                "outcome": "not_found",
                "review_id": review_id,
                "audit_recorded": False,
            }
        if str(row["status"]) != STATUS_REVIEW_PENDING:
            return {
                "outcome": "not_pending",
                "review_id": review_id,
                "status": str(row["status"]),
                "audit_recorded": False,
            }
        candidate = self._candidate_from_row(row)
        if candidate is None:
            self._store.set_review_status(
                review_id,
                STATUS_REVIEW_EXPIRED,
                reviewed_at=now_iso(),
                note="invalid stored candidate",
            )
            self._store.append_review_audit(
                review_id=review_id,
                action="approve",
                outcome="expired",
                actor=actor or self._actor,
                policy_category=str(row.get("category", "")),
                memory_id=None,
                statement_hash=_statement_hash(str(row["statement"])),
                created_at=now_iso(),
            )
            return {
                "outcome": "expired",
                "review_id": review_id,
                "audit_recorded": True,
            }

        decision = self._policy.evaluate(candidate)
        if decision.decision in (MemoryDecision.DEFER, MemoryDecision.REJECT):
            self._store.set_review_status(
                review_id,
                STATUS_REVIEW_EXPIRED,
                reviewed_at=now_iso(),
                note=f"policy now {decision.reason}",
            )
            self._store.append_review_audit(
                review_id=review_id,
                action="approve",
                outcome="expired",
                actor=actor or self._actor,
                policy_category=decision.decision.value,
                memory_id=None,
                statement_hash=_statement_hash(str(row["statement"])),
                created_at=now_iso(),
            )
            return {
                "outcome": "expired",
                "review_id": review_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "audit_recorded": True,
            }

        curator = self._curator_for(review_id)
        result = curator.curate(candidate)
        return self._resolve_row(review_id, candidate, result, row, note, actor)

    def reject(
        self, review_id: int, *, note: str = "", actor: str | None = None
    ) -> dict[str, object]:
        """Reject one pending review item; writes no durable memory.

        Rejection is terminal: the guarded pending->rejected transition means
        a repeated decision on the same row observes ``not_pending`` (a
        harmless no-op that never writes). Decisions are serialized per
        service instance just like approval, and the terminal event commits
        atomically with the review transition.
        """
        with self._lock:
            if self._connection is None:
                return self._reject_impl(review_id, note=note, actor=actor)
            try:
                with _adjudication_transaction(self._connection):
                    return self._reject_impl(review_id, note=note, actor=actor)
            except MemoryReviewTransactionError as exc:
                return {"outcome": "error", "reason": str(exc)}

    def _reject_impl(
        self, review_id: int, *, note: str = "", actor: str | None = None
    ) -> dict[str, object]:
        row = self._store.get_review(review_id)
        if row is None:
            return {
                "outcome": "not_found",
                "review_id": review_id,
                "audit_recorded": False,
            }
        if str(row["status"]) != STATUS_REVIEW_PENDING:
            return {
                "outcome": "not_pending",
                "review_id": review_id,
                "status": str(row["status"]),
                "audit_recorded": False,
            }
        transitioned = self._store.set_review_status(
            review_id,
            STATUS_REVIEW_REJECTED,
            reviewed_at=now_iso(),
            note=note,
        )
        if transitioned:
            self._store.append_review_audit(
                review_id=review_id,
                action="reject",
                outcome="rejected",
                actor=actor or self._actor,
                policy_category=str(row.get("category", "")),
                memory_id=None,
                statement_hash=_statement_hash(str(row["statement"])),
                created_at=now_iso(),
            )
        return {
            "outcome": "rejected",
            "review_id": review_id,
            "audit_recorded": transitioned,
        }

    # ---- internals ---------------------------------------------------------

    def _resolve_row(
        self,
        review_id: int,
        candidate: MemoryCandidate,
        result: dict[str, object],
        row: dict[str, object],
        note: str,
        actor: str | None = None,
    ) -> dict[str, object]:
        write_status = str(result.get("status", ""))
        if result.get("applied") and write_status in ("created", "updated"):
            accepted = self._store.set_review_status(
                review_id,
                STATUS_REVIEW_APPROVED,
                reviewed_at=now_iso(),
                note=note,
            )
            if accepted:
                self._store.append_review_audit(
                    review_id=review_id,
                    action="approve",
                    outcome="approved",
                    actor=actor or self._actor,
                    policy_category=str(
                        result.get("decision", "") or row.get("category", "")
                    ),
                    memory_id=result.get("memory_id"),
                    statement_hash=_statement_hash(str(row["statement"])),
                    created_at=now_iso(),
                )
            return {
                "outcome": "approved" if accepted else "not_pending",
                "review_id": review_id,
                "write_status": write_status,
                "memory_id": result.get("memory_id"),
                "superseded_id": result.get("superseded_id"),
                "evidence_added": int(result.get("evidence_added", 0) or 0),
                "category": row["category"],
                "audit_recorded": accepted,
            }
        if write_status == "conflict":
            memory = self._store_conflict_approved(candidate)
            accepted = self._store.set_review_status(
                review_id,
                STATUS_REVIEW_APPROVED,
                reviewed_at=now_iso(),
                note=note or "approved conflict stored alongside",
            )
            if accepted:
                self._store.append_review_audit(
                    review_id=review_id,
                    action="approve",
                    outcome="approved",
                    actor=actor or self._actor,
                    policy_category=str(row.get("category", "")),
                    memory_id=memory.memory_id,
                    statement_hash=_statement_hash(str(row["statement"])),
                    created_at=now_iso(),
                )
            return {
                "outcome": "approved" if accepted else "not_pending",
                "review_id": review_id,
                "write_status": "created",
                "memory_id": memory.memory_id,
                "evidence_added": len(candidate.evidence),
                "category": row["category"],
                "note": "conflict approved: stored alongside the existing record",
                "audit_recorded": accepted,
            }
        transitioned = self._store.set_review_status(
            review_id,
            STATUS_REVIEW_EXPIRED,
            reviewed_at=now_iso(),
            note=f"write not applied ({write_status or 'unknown'})",
        )
        if transitioned:
            self._store.append_review_audit(
                review_id=review_id,
                action="approve",
                outcome="expired",
                actor=actor or self._actor,
                policy_category=str(row.get("category", "")),
                memory_id=result.get("memory_id"),
                statement_hash=_statement_hash(str(row["statement"])),
                created_at=now_iso(),
            )
        return {
            "outcome": "expired",
            "review_id": review_id,
            "write_status": write_status,
            "audit_recorded": transitioned,
        }

    def _curator_for(self, review_id: int) -> object:
        # Imported lazily to avoid a module-level import cycle through the
        # tools layer (tools.memory -> agents -> memory), matched to the lazy
        # import used by curation's default curator.
        from personal_ai.tools.memory import AutomaticMemoryCurator

        approver = self._approver_for(review_id)
        return AutomaticMemoryCurator(
            self._service,
            self._policy,
            interactive_approver=approver,
            # ACCEPT-classified candidates (typically ``conflict`` rows, which
            # policy allows but reconciliation blocked) take the automatic
            # gate; binding it to the same review-row check keeps approval
            # explicit for every policy outcome.
            auto_approver=approver,
        )

    def _approver_for(self, review_id: int) -> Callable[[str, str, str], bool]:
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

    def _candidate_from_row(self, row: dict[str, object]) -> MemoryCandidate | None:
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


__all__: tuple[str, ...] = ("MemoryReviewService",)
