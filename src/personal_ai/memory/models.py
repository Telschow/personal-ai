"""Memory domain model.

Memory is durable information the AI has learned or been instructed to
retain. It is deliberately distinct from:

* the **corpus** — external/source material (document store);
* the **execution** — what the AI is currently doing (orchestration);
* **evidence** — material that supports one specific execution;
* **artifacts** — outputs produced by an execution.

Every memory carries provenance (where it came from) and a scope (who/what it
applies to), and it is *data*, never policy: a memory can inform context but
can never grant permissions or change approval requirements.

The lifecycle is ``active -> archived`` (retire from active retrieval) or
``active -> deleted`` (logical delete that keeps provenance). A physical
``purge`` exists for privacy-sensitive memories.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def new_memory_id() -> str:
    """Generate a fresh, stable memory identifier.

    Deliberately non-privacy-bearing so it can be used as a durable
    correlation key, an event reference, and a log field without leaking what
    the memory is about.
    """
    return "mem-" + uuid.uuid4().hex


class MemoryKind(Enum):
    """The kinds of durable memory the system may retain."""

    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    PROJECT_CONTEXT = "project_context"
    ENTITY = "entity"
    SUMMARY = "summary"
    INSTRUCTION = "instruction"


class MemoryScope(Enum):
    """Who/what a memory applies to. Retrieval must enforce this."""

    GLOBAL = "global"
    AGENT = "agent"
    PROJECT = "project"
    EXECUTION = "execution"


class MemoryStatus(Enum):
    """Lifecycle of a memory record.

    ``active`` memories participate in retrieval; ``archived`` memories are
    retired from active retrieval but keep their record and provenance; a
    logical ``deleted`` memory keeps provenance but never surfaces in normal
    retrieval. ``purge`` physically removes the record for privacy-sensitive
    content.
    """

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemorySourceType(Enum):
    """Provenance: where a durable memory came from."""

    USER = "user"
    EXECUTION = "execution"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    IMPORTED = "imported"
    SYSTEM = "system"


class MemoryEventType:
    """Canonical safe memory event names (stored in the memory store).

    These are separate from orchestration events because memories are not
    correlated with an execution. Payloads may carry identifiers, kinds, and
    scopes only — never private memory text.
    """

    CREATED = "memory.created"
    UPDATED = "memory.updated"
    ARCHIVED = "memory.archived"
    DELETED = "memory.deleted"
    PURGED = "memory.purged"
    ACCESSED = "memory.accessed"


class MemoryValidationError(ValueError):
    """Raised when a memory record violates the domain contract."""


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, raising :class:`MemoryValidationError`."""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise MemoryValidationError(f"invalid ISO timestamp: {value!r}") from exc


def _coerce(enum_type: type[Enum], value: object, label: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise MemoryValidationError(
                f"invalid {label}: {value!r} (expected one of "
                f"{[member.value for member in enum_type]})"
            ) from exc
    raise MemoryValidationError(f"invalid {label}: {value!r}")


@dataclass(frozen=True, slots=True)
class Memory:
    """One durable memory record.

    ``confidence`` 0.0-1.0 expresses how strongly the system believes the
    memory is correct; ``importance`` 0.0-1.0 expresses how useful the memory
    is likely to be for future context selection. Both are operational
    metadata, not rigorous probabilities.

    ``scope_id`` is required for non-global scopes (an agent id, a project
    name, or an execution id). A non-global scope without a ``scope_id`` is
    invalid.
    """

    memory_id: str
    kind: MemoryKind
    content: str
    summary: str = ""
    source_type: MemorySourceType = MemorySourceType.USER
    source_id: str = ""
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str | None = None
    confidence: float = 0.5
    importance: float = 0.5
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        """Normalize enum fields so ``to_dict`` round-trips back to ``Memory``.

        ``to_dict`` emits string values (the JSON contract); reconstructing a
        ``Memory`` from that dict must coerce them back to enum members.
        """
        object.__setattr__(self, "kind", _coerce(MemoryKind, self.kind, "kind"))
        object.__setattr__(
            self,
            "source_type",
            _coerce(MemorySourceType, self.source_type, "source_type"),
        )
        object.__setattr__(self, "scope", _coerce(MemoryScope, self.scope, "scope"))
        object.__setattr__(self, "status", _coerce(MemoryStatus, self.status, "status"))

    def to_dict(self) -> dict[str, object]:
        """Canonical, JSON-compatible representation (the API/UI contract)."""
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "content": self.content,
            "summary": self.summary,
            "confidence": self.confidence,
            "importance": self.importance,
            "status": self.status.value,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "expires_at": self.expires_at,
        }


def validate_memory(memory: Memory) -> None:
    """Validate a memory record against the domain contract."""
    if not memory.content.strip():
        raise MemoryValidationError("memory content must not be empty")
    for enum_type, value, label in (
        (MemoryKind, memory.kind, "kind"),
        (MemoryScope, memory.scope, "scope"),
        (MemoryStatus, memory.status, "status"),
        (MemorySourceType, memory.source_type, "source_type"),
    ):
        if not isinstance(value, enum_type):
            raise MemoryValidationError(f"invalid {label}: {value!r}")
    if not (0.0 <= memory.confidence <= 1.0):
        raise MemoryValidationError("confidence must be in [0.0, 1.0]")
    if not (0.0 <= memory.importance <= 1.0):
        raise MemoryValidationError("importance must be in [0.0, 1.0]")
    if memory.scope is not MemoryScope.GLOBAL and not memory.scope_id:
        raise MemoryValidationError(f"scope {memory.scope.value!r} requires a scope_id")
    if memory.scope is MemoryScope.GLOBAL and memory.scope_id:
        raise MemoryValidationError("global memories must not carry a scope_id")
    for label, value in (
        ("created_at", memory.created_at),
        ("updated_at", memory.updated_at),
    ):
        if value:
            parse_iso(value)
    for label, value in (
        ("last_accessed_at", memory.last_accessed_at),
        ("expires_at", memory.expires_at),
    ):
        if value:
            parse_iso(value)


@dataclass(frozen=True, slots=True)
class MemoryDraft:
    """Input for creating a memory.

    ``memory_id`` is optional; a fresh stable id is generated when absent.
    Creation is explicit: a draft always names a ``source_type`` so the memory
    is traceable to its origin.
    """

    kind: MemoryKind | str
    content: str
    summary: str = ""
    source_type: MemorySourceType | str = MemorySourceType.USER
    source_id: str = ""
    scope: MemoryScope | str = MemoryScope.GLOBAL
    scope_id: str | None = None
    confidence: float = 0.5
    importance: float = 0.5
    expires_at: str | None = None
    memory_id: str | None = None

    def to_memory(self, created_at: str) -> Memory:
        """Build and validate a :class:`Memory` from this draft."""
        memory = Memory(
            memory_id=self.memory_id or new_memory_id(),
            kind=_coerce(MemoryKind, self.kind, "kind"),  # type: ignore[arg-type]
            content=self.content.strip(),
            summary=self.summary,
            source_type=_coerce(MemorySourceType, self.source_type, "source_type"),  # type: ignore[arg-type]
            source_id=self.source_id,
            scope=_coerce(MemoryScope, self.scope, "scope"),  # type: ignore[arg-type]
            scope_id=self.scope_id,
            confidence=float(self.confidence),
            importance=float(self.importance),
            created_at=created_at,
            updated_at=created_at,
            expires_at=self.expires_at,
        )
        validate_memory(memory)
        return memory
