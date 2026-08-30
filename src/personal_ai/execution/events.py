"""Structured orchestration event stream.

Events are the durable audit history that powers the future Kanban UI and
answers *"what happened, when, why"*. They are timestamped, correlated with
plan/task/agent ids, and by default carry only safe summaries/counts/hashes /
references — never full private content, prompts, or tool outputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


class EventType:
    """Canonical orchestration event type names."""

    PLAN_CREATED = "plan.created"
    TASK_CREATED = "task.created"
    TASK_READY = "task.ready"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_DENIED = "tool.denied"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"
    ARTIFACT_CREATED = "artifact.created"
    TASK_BLOCKED = "task.blocked"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    TASK_FAILED = "task.failed"
    TASK_RETRYING = "task.retrying"
    TASK_COMPLETED = "task.completed"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    PLAN_COMPLETED = "plan.completed"
    PLAN_FAILED = "plan.failed"
    # --- execution-level / control-plane events ---
    EXECUTION_CREATED = "execution.created"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_RESUMED = "execution.resumed"
    EXECUTION_PAUSED = "execution.paused"
    EXECUTION_CANCELLED = "execution.cancelled"
    TASK_WAITING_APPROVAL = "task.waiting_approval"
    TASK_APPROVED = "task.approved"
    TASK_REJECTED = "task.rejected"
    APPROVAL_GATE_REQUIRED = "approval.gate_required"


def compute_event_id(
    event_type: str,
    plan_id: str,
    task_id: str | None,
    sequence: int,
) -> str:
    """Stable deterministic identity for one event record."""
    material = f"{sequence}\x00{event_type}\x00{plan_id}\x00{task_id or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OrchestrationEvent:
    """One structured, audit-safe orchestration event.

    ``payload`` is a small mapping of safe values (summaries, counts, ids,
    hashes, references). It must never contain full private content.
    """

    id: str
    seq: int
    event_type: str
    plan_id: str
    task_id: str | None
    agent_id: str | None = None
    tool: str | None = None
    timestamp: str = ""
    status: str | None = None
    payload: dict[str, object] = field(default_factory=dict)

    @property
    def execution_id(self) -> str:
        """The execution this event belongs to (aliases ``plan_id``)."""
        return self.plan_id
