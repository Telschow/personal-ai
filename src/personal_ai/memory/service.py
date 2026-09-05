"""The memory service boundary.

Agents and clients must never touch SQLite directly. This service is the only
route into the memory store, exactly as :class:`ControlPlane` is the only
route into the orchestration store. Persistence details stay here.

Memory *creation* is explicit and controlled: there is no automatic
conversation memorization and no autonomous unrestricted memory writing. A
memory is created because a caller (the future agent/CLI/API) explicitly
provides a :class:`MemoryDraft` naming a provenance. Agent-*proposed* memories
are a documented future path that must pass a policy boundary before this
service is invoked — this service itself never consults or mutates policy.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from personal_ai.memory.models import (
    Memory,
    MemoryCandidate,
    MemoryDraft,
    MemoryEventType,
    MemoryEvidenceRef,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryValidationError,
    now_iso,
)
from personal_ai.memory.reconcile import MemoryReconciler, ReconcileAction
from personal_ai.memory.retriever import (
    MemoryHit,
    MemoryRetriever,
    ScopeFilter,
)
from personal_ai.memory.store import MemoryNotFoundError, MemoryStore


class MemoryNotConfiguredError(Exception):
    """Raised when an operation needs a memory service that is not wired up."""


class MemoryConflictError(Exception):
    """Raised when reconciliation finds an ambiguous related fact.

    The candidate must not be silently written; the caller surfaces the
    conflict for human review instead.
    """

    def __init__(self, memory_id: str | None, reason: str) -> None:
        super().__init__(reason)
        self.memory_id = memory_id
        self.reason = reason


class MemoryService:
    """Owns memory persistence, lifecycle, retrieval, and its safe event log."""

    def __init__(
        self,
        store: MemoryStore,
        retriever: MemoryRetriever | None = None,
        *,
        now: Callable[[], str] | None = None,
        reconciler: MemoryReconciler | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever or MemoryRetriever(store, now=now)
        self._reconciler = reconciler or MemoryReconciler(self)
        self._now = now or now_iso

    # ---- creation ----
    def create(self, draft: MemoryDraft) -> Memory:
        """Persist a validated memory from an explicit draft."""
        created = self._now()
        memory = draft.to_memory(created)
        self._store.save(memory)
        self._store.append_event(
            MemoryEventType.CREATED,
            memory.memory_id,
            _safe_payload(memory),
        )
        return memory

    def create_user_memory(
        self,
        content: str,
        kind: object = "preference",
        *,
        summary: str = "",
        scope: object = "global",
        scope_id: str | None = None,
        source_id: str = "",
        confidence: float = 0.5,
        importance: float = 0.5,
        expires_at: str | None = None,
    ) -> Memory:
        """Convenience for an explicitly user-provided memory."""
        return self.create(
            MemoryDraft(
                kind=kind,  # type: ignore[arg-type]
                content=content,
                summary=summary,
                source_type="user",
                source_id=source_id,
                scope=scope,  # type: ignore[arg-type]
                scope_id=scope_id,
                confidence=confidence,
                importance=importance,
                expires_at=expires_at,
            )
        )

    # ---- reads ----
    def get(self, memory_id: str) -> Memory:
        memory = self._store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError(f"Unknown memory: {memory_id}")
        return memory

    def list(self, status: MemoryStatus | str | None = None) -> tuple[Memory, ...]:
        """List memories; defaults to active, newest first."""
        if status is None or status == "active":
            return self._store.list(MemoryStatus.ACTIVE)
        return self._store.list(
            status if isinstance(status, MemoryStatus) else MemoryStatus(status)
        )

    def search(
        self,
        query: str = "",
        scopes: Sequence[ScopeFilter] = (),
        *,
        limit: int = 10,
        include_expired: bool = False,
        now: str | None = None,
    ) -> tuple[MemoryHit, ...]:
        """Deterministic, scope-aware, active-and-unexpired-only retrieval."""
        return self._retriever.search(
            query=query,
            scopes=tuple(scopes),
            limit=limit,
            include_expired=include_expired,
            now=now,
        )

    def events(self, memory_id: str) -> tuple[dict[str, object], ...]:
        """Safe audit events for one memory (identifiers only, no content)."""
        self.get(memory_id)
        return self._store.memory_events(memory_id)

    # ---- provenance ----
    def add_evidence(
        self, memory_id: str, evidence: tuple[MemoryEvidenceRef, ...]
    ) -> int:
        """Attach provenance references idempotently; returns rows inserted."""
        self.get(memory_id)
        if not all(isinstance(ref, MemoryEvidenceRef) for ref in evidence):
            raise MemoryValidationError(
                "evidence entries must be MemoryEvidenceRef instances"
            )
        return self._store.add_evidence(memory_id, evidence)

    def evidence_for(self, memory_id: str) -> tuple[dict[str, object], ...]:
        """Provenance references for one memory (identifiers, never content)."""
        self.get(memory_id)
        return self._store.evidence_for(memory_id)

    # ---- reconciliation ----
    def apply_candidate(self, candidate: MemoryCandidate) -> dict[str, object]:
        """Persist a validated candidate per the deterministic reconciliation.

        The candidate must already have passed the automatic memory policy; the
        service itself never consults policy (that separation keeps policy
        authoritative and auditable).
        """
        plan = self._reconciler.plan(candidate)
        if plan.action is ReconcileAction.ADD_EVIDENCE:
            added = self.add_evidence(plan.target_id or "", candidate.evidence)
            return {
                "status": "updated",
                "memory_id": plan.target_id,
                "kind": candidate.kind.value,
                "evidence_added": added,
            }
        if plan.action is ReconcileAction.SUPERSEDE:
            memory = self.create_candidate_memory(candidate)
            superseded = self.supersede(plan.target_id or "", memory.memory_id)
            added = self.add_evidence(memory.memory_id, candidate.evidence)
            return {
                "status": "created",
                "memory_id": memory.memory_id,
                "superseded_id": superseded.memory_id,
                "kind": candidate.kind.value,
                "evidence_added": added,
            }
        if plan.action is ReconcileAction.CONFLICT:
            raise MemoryConflictError(plan.target_id, plan.reason)
        memory = self.create_candidate_memory(candidate)
        added = self.add_evidence(memory.memory_id, candidate.evidence)
        return {
            "status": "created",
            "memory_id": memory.memory_id,
            "kind": candidate.kind.value,
            "evidence_added": added,
        }

    def create_candidate_memory(self, candidate: MemoryCandidate) -> Memory:
        """Create an active memory from a validated candidate.

        Provenance lives in the evidence rows attached afterwards; the record
        itself is sourced as ``corpus`` so it is distinguishable from
        explicitly user-entered memories.
        """
        return self.create(
            MemoryDraft(
                kind=candidate.kind,
                content=candidate.statement.strip(),
                summary=candidate.summary,
                source_type=MemorySourceType.CORPUS,
                source_id="",
                scope=MemoryScope.GLOBAL,
                scope_id=None,
                confidence=candidate.confidence,
                importance=candidate.utility,
                expires_at=None,
                temporal_scope=candidate.temporal_scope,
            )
        )

    # ---- lifecycle ----
    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        summary: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        expires_at: str | None = None,
    ) -> Memory:
        """Update mutable fields of a memory (scope and provenance are fixed)."""
        current = self.get(memory_id)
        updated = Memory(
            memory_id=current.memory_id,
            kind=current.kind,
            content=content if content is not None else current.content,
            summary=summary if summary is not None else current.summary,
            source_type=current.source_type,
            source_id=current.source_id,
            scope=current.scope,
            scope_id=current.scope_id,
            confidence=current.confidence if confidence is None else float(confidence),
            importance=current.importance if importance is None else float(importance),
            status=current.status,
            temporal_scope=current.temporal_scope,
            created_at=current.created_at,
            updated_at=self._now(),
            last_accessed_at=current.last_accessed_at,
            expires_at=current.expires_at if expires_at is None else expires_at,
        )
        self._store.save(updated)
        self._store.append_event(
            MemoryEventType.UPDATED,
            memory_id,
            _safe_payload(updated),
        )
        return updated

    def archive(self, memory_id: str) -> Memory:
        """Retire a memory from active retrieval without destroying it."""
        return self._set_status(
            memory_id, MemoryStatus.ARCHIVED, MemoryEventType.ARCHIVED
        )

    def supersede(self, old_id: str, new_id: str) -> Memory:
        """Mark an active memory as replaced by a newer record.

        The old record keeps its content and provenance for auditability but
        is removed from active retrieval; only active memories may be
        superseded.
        """
        old = self.get(old_id)
        self.get(new_id)
        if old.status is not MemoryStatus.ACTIVE:
            raise MemoryValidationError(
                f"cannot supersede {old.status.value!r} memory {old_id}"
            )
        superseded = Memory(
            memory_id=old.memory_id,
            kind=old.kind,
            content=old.content,
            summary=old.summary,
            source_type=old.source_type,
            source_id=old.source_id,
            scope=old.scope,
            scope_id=old.scope_id,
            confidence=old.confidence,
            importance=old.importance,
            status=MemoryStatus.SUPERSEDED,
            temporal_scope=old.temporal_scope,
            created_at=old.created_at,
            updated_at=self._now(),
            last_accessed_at=old.last_accessed_at,
            expires_at=old.expires_at,
        )
        self._store.save(superseded)
        self._store.append_event(
            MemoryEventType.SUPERSEDED,
            old_id,
            {
                "memory_id": old_id,
                "replaced_by": new_id,
                "kind": old.kind.value,
                "scope": old.scope.value,
                "status": MemoryStatus.SUPERSEDED.value,
            },
        )
        return superseded

    def delete(self, memory_id: str) -> Memory:
        """Logically delete a memory (record and provenance are kept)."""
        return self._set_status(
            memory_id, MemoryStatus.DELETED, MemoryEventType.DELETED
        )

    def purge(self, memory_id: str) -> None:
        """Physically remove a memory record (privacy-sensitive deletion).

        The safe event log entry for the purge remains, but the record and its
        content are gone and cannot be retrieved or restored.
        """
        memory = self.get(memory_id)
        self._store.append_event(
            MemoryEventType.PURGED, memory_id, _safe_payload(memory)
        )
        self._store.purge(memory_id)

    def record_access(self, memory_id: str) -> None:
        """Explicitly stamp ``last_accessed_at`` (never called by search)."""
        self.get(memory_id)
        self._store.record_access(memory_id)
        self._store.append_event(
            MemoryEventType.ACCESSED,
            memory_id,
            {"memory_id": memory_id, "status": "accessed"},
        )

    def counts(self) -> dict[str, int]:
        return self._store.counts()

    def statistics(self) -> dict[str, object]:
        """Aggregate, content-free memory statistics for observability."""
        return self._store.statistics()

    def _set_status(
        self, memory_id: str, status: MemoryStatus, event_type: str
    ) -> Memory:
        current = self.get(memory_id)
        memory = Memory(
            memory_id=current.memory_id,
            kind=current.kind,
            content=current.content,
            summary=current.summary,
            source_type=current.source_type,
            source_id=current.source_id,
            scope=current.scope,
            scope_id=current.scope_id,
            confidence=current.confidence,
            importance=current.importance,
            status=status,
            temporal_scope=current.temporal_scope,
            created_at=current.created_at,
            updated_at=self._now(),
            last_accessed_at=current.last_accessed_at,
            expires_at=current.expires_at,
        )
        self._store.save(memory)
        self._store.append_event(event_type, memory_id, _safe_payload(memory))
        return memory


def _safe_payload(memory: Memory) -> dict[str, object]:
    """Identifiers only — kind/scope/status, never private memory content."""
    return {
        "memory_id": memory.memory_id,
        "kind": memory.kind.value,
        "scope": memory.scope.value,
        "scope_id": memory.scope_id,
        "status": memory.status.value,
    }
