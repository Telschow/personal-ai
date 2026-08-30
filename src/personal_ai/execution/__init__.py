"""Orchestration runtime: plans, tasks, events, artifacts, and the workflow.

Public surface:

* :mod:`models` — Plan / Task / Artifact / Evidence / AgentResult and the
  task lifecycle state machine.
* :mod:`events` — structured, audit-safe orchestration event stream.
* :mod:`planner` — validated task-graph planning (deterministic + proposal).
* :mod:`executor` — policy-gated, single-task execution with bounded retries.
* :mod:`verifier` — deterministic pass/fail verification contract.
* :mod:`workflows` — the built-in researcher / reviewer work functions.
* :mod:`orchestrator` — owns plan lifecycle, drives researcher -> verifier.
* :mod:`storage` — SQLite-backed durable store.
* :mod:`approval` — task-/permission-scoped durable approval context.
* :mod:`board` — Kanban read projection over durable execution state.
* :mod:`control_plane` — the single service boundary for clients (CLI/API).
"""

from personal_ai.execution.approval import ApprovalContext
from personal_ai.execution.board import ExecutionBoard
from personal_ai.execution.control_plane import (
    ControlPlane,
    ExecutionNotFoundError,
)
from personal_ai.execution.events import EventType, OrchestrationEvent
from personal_ai.execution.executor import TaskExecutor
from personal_ai.execution.models import (
    AgentResult,
    Artifact,
    Evidence,
    Plan,
    PlanStatus,
    PlanTransitionError,
    Task,
    TaskStatus,
    TaskTransitionError,
    allowed_plan_transitions,
    allowed_transitions,
    compute_artifact_hash,
    plan_transition_from,
    transition_from,
)
from personal_ai.execution.orchestrator import Orchestrator
from personal_ai.execution.planner import (
    InvalidPlanError,
    Planner,
    PlanningError,
    TaskSpec,
)
from personal_ai.execution.storage import (
    OrchestrationStore,
    open_orchestration_store,
)
from personal_ai.execution.verifier import (
    DeterministicVerifier,
    VerificationError,
    VerificationResult,
)
from personal_ai.execution.workflows import default_work_dispatch

__all__ = [
    "AgentResult",
    "ApprovalContext",
    "Artifact",
    "ControlPlane",
    "DeterministicVerifier",
    "EventType",
    "Evidence",
    "ExecutionBoard",
    "ExecutionNotFoundError",
    "InvalidPlanError",
    "OrchestrationEvent",
    "OrchestrationStore",
    "Orchestrator",
    "Plan",
    "PlanStatus",
    "PlanTransitionError",
    "Planner",
    "PlanningError",
    "Task",
    "TaskExecutor",
    "TaskSpec",
    "TaskStatus",
    "TaskTransitionError",
    "VerificationError",
    "VerificationResult",
    "allowed_plan_transitions",
    "allowed_transitions",
    "compute_artifact_hash",
    "default_work_dispatch",
    "open_orchestration_store",
    "plan_transition_from",
    "transition_from",
]
