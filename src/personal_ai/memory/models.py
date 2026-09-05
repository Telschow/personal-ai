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

The lifecycle is ``candidate -> active`` for policy-approved instances,
``active -> superseded`` when a newer record replaces one, ``active ->
archived`` (retire from active retrieval), or ``active -> deleted`` (logical
delete that keeps provenance). A physical ``purge`` exists for
privacy-sensitive memories.
"""

from __future__ import annotations

import math
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
    """The kinds of durable memory the system may retain.

    The first seven kinds predate the curation pipeline and remain stable (a
    JSON contract). The later kinds were added to classify personal/identity
    knowledge produced by corpus curation.
    """

    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    PROJECT_CONTEXT = "project_context"
    ENTITY = "entity"
    SUMMARY = "summary"
    INSTRUCTION = "instruction"
    IDENTITY = "identity"
    BIOGRAPHY = "biography"
    RELATIONSHIP = "relationship"
    GOAL = "goal"
    HABIT = "habit"
    ROUTINE = "routine"
    INTEREST = "interest"
    SKILL = "skill"
    WORK = "work"
    EDUCATION = "education"
    LOCATION_CONTEXT = "location_context"
    IMPORTANT_EVENT = "important_event"
    LONG_TERM_CONTEXT = "long_term_context"
    COMMUNICATION_PREFERENCE = "communication_preference"
    PERSONAL_FACT = "personal_fact"


class MemoryScope(Enum):
    """Who/what a memory applies to. Retrieval must enforce this."""

    GLOBAL = "global"
    AGENT = "agent"
    PROJECT = "project"
    EXECUTION = "execution"


class MemoryStatus(Enum):
    """Lifecycle of a memory record.

    ``candidate`` memories are proposed but not yet accepted; ``active``
    memories participate in retrieval; ``superseded`` memories were replaced
    by a newer record (kept for provenance but never surfaced in normal
    retrieval); ``archived`` memories are retired from active retrieval but
    keep their record and provenance; a logical ``deleted`` memory keeps
    provenance but never surfaces in normal retrieval. ``purge`` physically
    removes the record for privacy-sensitive content.
    """

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
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
    CORPUS = "corpus"


class TemporalScope(Enum):
    """How a memory's content relates to the present.

    ``current`` facts still hold today; ``historical`` facts describe the
    past; ``recurring`` facts recur over time (habits, periodic events);
    ``unknown`` is the safe default.
    """

    CURRENT = "current"
    HISTORICAL = "historical"
    RECURRING = "recurring"
    UNKNOWN = "unknown"


class AssertionStatus(Enum):
    """How a candidate statement was asserted.

    ``asserted`` is a plain statement of fact; ``hypothetical`` and
    ``uncertain`` express doubt; ``quoted`` is reported speech the system
    cannot confirm.
    """

    ASSERTED = "asserted"
    HYPOTHETICAL = "hypothetical"
    QUOTED = "quoted"
    UNCERTAIN = "uncertain"


class MemoryEventType:
    """Canonical safe memory event names (stored in the memory store).

    These are separate from orchestration events because memories are not
    correlated with an execution. Payloads may carry identifiers, kinds, and
    scopes only — never private memory text.
    """

    CREATED = "memory.created"
    UPDATED = "memory.updated"
    SUPERSEDED = "memory.superseded"
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
    temporal_scope: TemporalScope = TemporalScope.UNKNOWN
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
        object.__setattr__(
            self,
            "temporal_scope",
            _coerce(TemporalScope, self.temporal_scope, "temporal_scope"),
        )

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
            "temporal_scope": self.temporal_scope.value,
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
        (TemporalScope, memory.temporal_scope, "temporal_scope"),
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
    temporal_scope: TemporalScope | str = TemporalScope.UNKNOWN

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
            temporal_scope=_coerce(  # type: ignore[arg-type]
                TemporalScope, self.temporal_scope, "temporal_scope"
            ),
            created_at=created_at,
            updated_at=created_at,
            expires_at=self.expires_at,
        )
        validate_memory(memory)
        return memory


_EVIDENCE_LIMIT = 5
_STATEMENT_MAX_CHARS = 512


@dataclass(frozen=True, slots=True)
class MemoryEvidenceRef:
    """One provenance reference supporting a memory.

    Pointers carry identifiers and timestamps only — never content. The triple
    ``(source_type, source_id, source_document_id)`` is the stable identity of
    an evidence item so re-adding the same evidence is idempotent.
    """

    source_type: str
    source_id: str
    source_document_id: str | None = None
    source_timestamp: str | None = None

    def validate(self) -> None:
        if not self.source_type.strip():
            raise MemoryValidationError("evidence source_type must not be empty")
        if not self.source_id.strip():
            raise MemoryValidationError("evidence source_id must not be empty")
        if self.source_timestamp is not None:
            parse_iso(self.source_timestamp)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_document_id": self.source_document_id,
            "source_timestamp": self.source_timestamp,
        }


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A proposed memory before policy and reconciliation run.

    Candidates are model-generated metadata and are therefore treated as
    *untrusted input*: every field is validated (bounded statement, bounded
    evidence, finite numerics in [0.0, 1.0]) and the final write decision is
    made by the deterministic memory policy, never by the model.
    """

    statement: str
    kind: MemoryKind | str
    confidence: float
    durability: float = 0.5
    relevance: float = 0.5
    specificity: float = 0.5
    recurrence: int = 1
    utility: float = 0.5
    temporal_scope: TemporalScope | str = TemporalScope.UNKNOWN
    assertion_status: AssertionStatus | str = AssertionStatus.ASSERTED
    summary: str = ""
    evidence: tuple[MemoryEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", self.statement.strip())
        object.__setattr__(self, "kind", _coerce(MemoryKind, self.kind, "kind"))
        object.__setattr__(
            self,
            "temporal_scope",
            _coerce(TemporalScope, self.temporal_scope, "temporal_scope"),
        )
        object.__setattr__(
            self,
            "assertion_status",
            _coerce(AssertionStatus, self.assertion_status, "assertion_status"),
        )
        object.__setattr__(
            self,
            "evidence",
            tuple(self.evidence),
        )
        self.validate()

    def validate(self) -> None:
        statement = self.statement.strip()
        if not statement:
            raise MemoryValidationError("candidate statement must not be empty")
        if len(statement) > _STATEMENT_MAX_CHARS:
            raise MemoryValidationError(
                f"candidate statement exceeds {_STATEMENT_MAX_CHARS} characters"
            )
        if len(self.evidence) > _EVIDENCE_LIMIT:
            raise MemoryValidationError(
                f"candidate carries more than {_EVIDENCE_LIMIT} evidence references"
            )
        for label, value in (
            ("confidence", self.confidence),
            ("durability", self.durability),
            ("relevance", self.relevance),
            ("specificity", self.specificity),
            ("utility", self.utility),
        ):
            _validate_score(value, label)
        if not isinstance(self.recurrence, int) or isinstance(self.recurrence, bool):
            raise MemoryValidationError("candidate recurrence must be a positive int")
        if self.recurrence < 1:
            raise MemoryValidationError("candidate recurrence must be >= 1")
        seen: set[tuple[object, ...]] = set()
        for ref in self.evidence:
            if not isinstance(ref, MemoryEvidenceRef):
                raise MemoryValidationError(
                    f"evidence entries must be MemoryEvidenceRef, got {type(ref).__name__}"
                )
            ref.validate()
            key = (ref.source_type, ref.source_id, ref.source_document_id)
            if key in seen:
                raise MemoryValidationError("duplicate evidence reference in candidate")
            seen.add(key)
        for label, value in (
            ("kind", self.kind),
            ("temporal_scope", self.temporal_scope),
            ("assertion_status", self.assertion_status),
        ):
            if not isinstance(value, Enum):
                raise MemoryValidationError(f"invalid {label}: {value!r}")

    def to_dict(self) -> dict[str, object]:
        """Canonical, JSON-compatible representation."""
        return {
            "statement": self.statement.strip(),
            "kind": self.kind.value,
            "confidence": self.confidence,
            "durability": self.durability,
            "relevance": self.relevance,
            "specificity": self.specificity,
            "recurrence": self.recurrence,
            "utility": self.utility,
            "temporal_scope": self.temporal_scope.value,
            "assertion_status": self.assertion_status.value,
            "summary": self.summary,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> MemoryCandidate:
        """Rebuild a validated candidate from its canonical dict.

        This is the inverse of :meth:`to_dict` and is used to round-trip a
        candidate through the ``propose_memory`` tool boundary, where the
        policy-approved candidate is reconstructed and re-validated before any
        write.
        """
        evidence = tuple(
            MemoryEvidenceRef(**ref)  # type: ignore[arg-type]
            for ref in data.get("evidence", [])  # type: ignore[union-attr]
        )
        return cls(
            statement=str(data["statement"]),
            kind=data["kind"],  # type: ignore[arg-type]
            confidence=float(data["confidence"]),
            durability=float(data.get("durability", 0.5)),
            relevance=float(data.get("relevance", 0.5)),
            specificity=float(data.get("specificity", 0.5)),
            recurrence=int(data.get("recurrence", 1)),
            utility=float(data.get("utility", 0.5)),
            temporal_scope=data.get("temporal_scope", TemporalScope.UNKNOWN),  # type: ignore[arg-type]
            assertion_status=data.get("assertion_status", AssertionStatus.ASSERTED),  # type: ignore[arg-type]
            summary=str(data.get("summary", "")),
            evidence=evidence,
        )


def _validate_score(value: float, label: str) -> None:
    if isinstance(value, bool):
        raise MemoryValidationError(f"candidate {label} must be a number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError(f"candidate {label} must be a number") from exc
    if not math.isfinite(numeric):
        raise MemoryValidationError(f"candidate {label} must be finite")
    if not (0.0 <= numeric <= 1.0):
        raise MemoryValidationError(f"candidate {label} must be in [0.0, 1.0]")
