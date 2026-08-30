"""Task / plan / artifact / evidence models for orchestration.

These are first-class typed objects. Plans are distinct from tasks; tasks
carry an explicit lifecycle state machine; artifacts and evidence reference
large or private content by id/hash rather than inlining it. Agent results
are structured (never only an arbitrary string).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def new_execution_id() -> str:
    """Generate a fresh, stable execution identifier.

    This is decoupled from the objective text on purpose: execution identity
    must be stable and non-privacy-bearing so it can be used as a durable
    correlation key, a file/db key, and a log field without leaking what the
    execution is about.
    """
    return "exec-" + uuid.uuid4().hex


class TaskStatus(Enum):
    """Explicit, typed lifecycle states for a task."""

    PENDING = "pending"
    PLANNED = "planned"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    NEEDS_APPROVAL = "needs_approval"


class PlanStatus(Enum):
    """Lifecycle states for a plan."""

    PENDING = "pending"
    PLANNED = "planned"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    NEEDS_APPROVAL = "needs_approval"


# Allowed forward transitions for a plan. Terminal and gated states are
# explicit: a plan can never silently return to work once completed, and a
# rejected execution can only be reopened by an explicit retry.
_PLAN_TRANSITIONS: dict[PlanStatus, set[PlanStatus]] = {
    PlanStatus.PENDING: {
        PlanStatus.PLANNED,
        PlanStatus.RUNNING,
        PlanStatus.BLOCKED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.PLANNED: {
        PlanStatus.RUNNING,
        PlanStatus.BLOCKED,
        PlanStatus.CANCELLED,
        PlanStatus.PENDING,
    },
    PlanStatus.RUNNING: {
        PlanStatus.RUNNING,  # idempotent resume
        PlanStatus.VERIFYING,
        PlanStatus.COMPLETED,
        PlanStatus.FAILED,
        PlanStatus.BLOCKED,
        PlanStatus.CANCELLED,
        PlanStatus.NEEDS_APPROVAL,
    },
    PlanStatus.VERIFYING: {
        PlanStatus.COMPLETED,
        PlanStatus.FAILED,
        PlanStatus.RUNNING,
        PlanStatus.BLOCKED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.NEEDS_APPROVAL: {
        PlanStatus.RUNNING,
        PlanStatus.BLOCKED,
        PlanStatus.CANCELLED,
        PlanStatus.FAILED,
    },
    PlanStatus.BLOCKED: {
        PlanStatus.RUNNING,
        PlanStatus.BLOCKED,  # idempotent pause
        PlanStatus.CANCELLED,
        PlanStatus.FAILED,
    },
    PlanStatus.COMPLETED: set(),
    PlanStatus.FAILED: {PlanStatus.RUNNING},  # explicit retry only
    PlanStatus.CANCELLED: set(),
}


class PlanTransitionError(Exception):
    """Raised when a plan status transition is not permitted by the state machine."""


def allowed_plan_transitions(status: PlanStatus) -> frozenset[PlanStatus]:
    return frozenset(_PLAN_TRANSITIONS[status])


def plan_transition_from(status: PlanStatus, status_target: PlanStatus) -> None:
    if status_target not in _PLAN_TRANSITIONS[status]:
        raise PlanTransitionError(
            f"Invalid plan transition: {status.value} -> {status_target.value}"
        )


# Allowed forward transitions for a task.
_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.PLANNED,
        TaskStatus.READY,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
    },
    TaskStatus.PLANNED: {
        TaskStatus.READY,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
        TaskStatus.PENDING,
    },
    TaskStatus.READY: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
        TaskStatus.WAITING,
    },
    TaskStatus.RUNNING: {
        TaskStatus.WAITING,
        TaskStatus.VERIFYING,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.NEEDS_APPROVAL,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING: {
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.NEEDS_APPROVAL: {
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: set(),
    TaskStatus.CANCELLED: set(),
}


class TaskTransitionError(Exception):
    """Raised when a task status transition is not permitted by the state machine."""


def allowed_transitions(status: TaskStatus) -> frozenset[TaskStatus]:
    return frozenset(_TASK_TRANSITIONS[status])


def transition_from(status: TaskStatus, status_target: TaskStatus) -> None:
    if status_target not in _TASK_TRANSITIONS[status]:
        raise TaskTransitionError(
            f"Invalid task transition: {status.value} -> {status_target.value}"
        )


@dataclass(frozen=True, slots=True)
class Task:
    """A unit of work assigned to an agent.

    ``dependencies`` lists other task ids that must complete first. Large or
    private outputs live in ``artifacts``/``evidence`` as references, never as
    inlined payloads.
    """

    task_id: str
    plan_id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    assigned_agent: str | None = None
    selected_model: str | None = None
    skill: str | None = None
    tools: tuple[str, ...] = ()
    policy: str = "default"
    inputs: dict[str, object] = field(default_factory=dict)
    outputs: dict[str, object] = field(default_factory=dict)
    artifacts: tuple[Any, ...] = ()  # Artifact instances
    evidence: tuple[Any, ...] = ()  # Evidence instances
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    approval_state: str = "not_required"

    def with_status(self, status: TaskStatus) -> Task:
        transition_from(self.status, status)
        updated = self.updated_at
        completed = self.completed_at
        return Task(
            task_id=self.task_id,
            plan_id=self.plan_id,
            title=self.title,
            description=self.description,
            status=status,
            priority=self.priority,
            dependencies=self.dependencies,
            assigned_agent=self.assigned_agent,
            selected_model=self.selected_model,
            skill=self.skill,
            tools=self.tools,
            policy=self.policy,
            inputs=self.inputs,
            outputs=self.outputs,
            artifacts=self.artifacts,
            evidence=self.evidence,
            retry_count=self.retry_count,
            max_retries=self.max_retries,
            created_at=self.created_at,
            updated_at=updated,
            completed_at=completed,
            error=self.error,
            approval_state=self.approval_state,
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """A durable, inspectable decomposition of an objective."""

    plan_id: str
    objective: str
    status: PlanStatus = PlanStatus.PENDING
    task_ids: tuple[str, ...] = ()
    risk: str = "low"
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    final_outcome: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def with_status(self, status: PlanStatus) -> Plan:
        plan_transition_from(self.status, status)
        return Plan(
            plan_id=self.plan_id,
            objective=self.objective,
            status=status,
            task_ids=self.task_ids,
            risk=self.risk,
            assumptions=self.assumptions,
            constraints=self.constraints,
            final_outcome=self.final_outcome,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    """A typed output produced by a task.

    ``content_hash`` and ``reference`` identify the content; the payload
    itself is stored externally (e.g. on disk) and referenced, keeping the
    orchestration state small.
    """

    artifact_id: str
    type: str
    title: str
    producing_task_id: str
    content_hash: str
    reference: str
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: str = ""


def compute_artifact_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class Evidence:
    """A provenance-preserving reference to a corpus passage.

    Reuses the existing retrieval identifiers (document_id, source_type,
    source) rather than creating a second provenance system. ``excerpt`` is a
    short bounded reference for display, not a full private dump.
    """

    evidence_id: str
    source_type: str | None
    source: str | None
    document_id: str | None
    chunk_id: str | None = None
    relevance: float = 0.0
    excerpt: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Structured result returned by an agent/executor for one task."""

    status: str
    summary: str
    artifacts: tuple[Artifact, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
    next_action: str | None = None
    error: str | None = None
