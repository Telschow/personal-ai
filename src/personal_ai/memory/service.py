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
    MemoryDraft,
    MemoryEventType,
    MemoryStatus,
    now_iso,
)
from personal_ai.memory.retriever import (
    MemoryHit,
    MemoryRetriever,
    ScopeFilter,
)
from personal_ai.memory.store import MemoryNotFoundError, MemoryStore


class MemoryNotConfiguredError(Exception):
    """Raised when an operation needs a memory service that is not wired up."""


class MemoryService:
    """Owns memory persistence, lifecycle, retrieval, and its safe event log."""

    def __init__(
        self,
        store: MemoryStore,
        retriever: MemoryRetriever | None = None,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever or MemoryRetriever(store, now=now)
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
