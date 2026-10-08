"""Kanban-oriented read projection over the orchestration store.

This is a *read model* / projection intended as the future UI contract. It
derives a board for one execution from durable state (tasks, events, artifacts,
evidence, approvals) without duplicating data or letting a UI touch SQLite
directly. The backend event stream remains the source of truth; this is a thin,
cheap, deterministic view over it.

Columns are intentionally conservative and map 1:1 onto real task states so the
UI never invents state:

* ``backlog``        — planned / pending tasks
* ``ready``          — runnable, waiting for a slot
* ``running``        — currently executing
* ``waiting_approval`` — paused on a human approval gate
* ``verifying``      — finished work being verified
* ``done``           — verified/completed
* ``failed``         — failed or rejected/cancelled
"""

from __future__ import annotations

from personal_ai.agents.registry import AgentRegistry
from personal_ai.execution.events import EventType
from personal_ai.execution.models import Task, TaskStatus
from personal_ai.execution.storage import OrchestrationStore

_COLUMN_BY_STATUS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "backlog",
    TaskStatus.PLANNED: "backlog",
    TaskStatus.READY: "ready",
    TaskStatus.RUNNING: "running",
    TaskStatus.NEEDS_APPROVAL: "waiting_approval",
    TaskStatus.WAITING: "waiting_approval",
    TaskStatus.BLOCKED: "waiting_approval",
    TaskStatus.VERIFYING: "verifying",
    TaskStatus.COMPLETED: "done",
    TaskStatus.FAILED: "failed",
    TaskStatus.CANCELLED: "failed",
}

COLUMNS: tuple[str, ...] = (
    "backlog",
    "ready",
    "running",
    "waiting_approval",
    "verifying",
    "done",
    "failed",
)


class ExecutionBoard:
    """Build a Kanban projection of one execution from durable state."""

    def __init__(
        self, store: OrchestrationStore, agents: AgentRegistry | None = None
    ) -> None:
        self._store = store
        self._agents = agents

    def board(self, execution_id: str) -> dict[str, object]:
        plan = self._store.get_plan(execution_id)
        if plan is None:
            return {
                "execution_id": execution_id,
                "objective": "",
                "status": "unknown",
                "columns": {col: [] for col in COLUMNS},
            }
        tasks = self._store.tasks_for_plan(execution_id)
        events = self._store.events_for_plan(execution_id)
        approvals = {
            (a["task_id"], a["permission"]): a
            for a in self._store.approval_requests(execution_id)
        }
        last_event_by_task: dict[str, str] = {}
        start_at_by_task: dict[str, str] = {}
        for ev in events:
            if ev.task_id:
                last_event_by_task[ev.task_id] = ev.event_type
                if (
                    ev.event_type == EventType.TASK_STARTED
                    and ev.task_id not in start_at_by_task
                ):
                    start_at_by_task[ev.task_id] = ev.timestamp

        columns: dict[str, list[dict[str, object]]] = {col: [] for col in COLUMNS}
        for task in tasks:
            card = self._card(task, approvals, last_event_by_task, start_at_by_task)
            columns[_column_for(task.status)].append(card)

        for column in columns.values():
            column.sort(key=lambda c: c["task_id"])

        return {
            "execution_id": execution_id,
            "objective": plan.objective,
            "status": plan.status.value,
            "risk": plan.risk,
            "columns": columns,
        }

    def _card(
        self,
        task: Task,
        approvals: dict[tuple[str, str], dict[str, object]],
        last_event_by_task: dict[str, str],
        start_at_by_task: dict[str, str],
    ) -> dict[str, object]:
        agent_name = ""
        if self._agents is not None and task.assigned_agent:
            try:
                agent_name = self._agents.get(task.assigned_agent).role
            except Exception:  # noqa: BLE001 - agent may be unknown
                agent_name = task.assigned_agent

        approval_required = any(tid == task.task_id for tid, _perm in approvals)
        approval_status = "none"
        for (_tid, _perm), req in approvals.items():
            if _tid == task.task_id:
                approval_status = str(req.get("status", "pending"))
                break

        return {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "agent_id": task.assigned_agent,
            "agent_name": agent_name,
            "skill": task.skill,
            "model": task.selected_model,
            "dependencies": list(task.dependencies),
            "risk": task.policy,
            "approval_required": approval_required,
            "approval_status": approval_status,
            "retry_count": task.retry_count,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": start_at_by_task.get(task.task_id) or None,
            "completed_at": task.completed_at,
            "artifact_count": len(self._store.artifacts_for_task(task.task_id)),
            "evidence_count": len(self._store.evidence_for_task(task.task_id)),
            "last_event": last_event_by_task.get(task.task_id),
            "error": task.error,
        }


def _column_for(status: TaskStatus) -> str:
    return _COLUMN_BY_STATUS.get(status, "backlog")
