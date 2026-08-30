"""Minimal task executor.

For Phase 39 the executor does exactly one task at a time: loads the task,
resolves its agent and model, executes it through the policy engine, captures
a structured :class:`AgentResult`, emits events, and persists state.

There is no unrestricted autonomous loop. Work is performed by a pluggable
``work`` function (dependency injection) so the executor stays testable
offline and the demonstration can wire real corpus tool calls.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.models import Agent
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.registry import AgentRegistry
from personal_ai.agents.routing import ModelCapability, ModelRequest, ModelRouter
from personal_ai.execution.events import (
    EventType,
    OrchestrationEvent,
)
from personal_ai.execution.models import AgentResult, Task, TaskStatus
from personal_ai.execution.storage import OrchestrationStore


class ExecutionError(Exception):
    """Base class for executor failures."""


class UnassignedAgentError(ExecutionError):
    """Raised when a task has no assigned agent."""


class TaskExecutionFailedError(ExecutionError):
    """Raised when the work callable raises a non-retryable error."""


class RetryableExecutionError(ExecutionError):
    """Raised when the work callable failed but a bounded retry is reasonable."""


AgentWork = Callable[[Agent, Task, PolicyEngine, object], AgentResult]


class TaskExecutor:
    """Executes one task deterministically, with policy enforcement and events."""

    def __init__(
        self,
        store: OrchestrationStore,
        agents: AgentRegistry,
        policy: PolicyEngine,
        router: ModelRouter,
        work: AgentWork,
        emit: Callable[[OrchestrationEvent], None] | None = None,
        event_sequence: Callable[[], int] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._agents = agents
        self._policy = policy
        self._router = router
        self._work = work
        self._emit = emit or (lambda event: store.append_event(event))
        self._seq = event_sequence or _Counter()
        self._now = now or (lambda: "1970-01-01T00:00:00+00:00")

    def execute(self, task: Task) -> Task:
        """Run one task to completion (or a terminal failure/block state)."""
        if task.assigned_agent is None:
            raise UnassignedAgentError(f"Task {task.task_id} has no assigned agent")

        agent = self._agents.get(task.assigned_agent)
        model = self._select_model(agent, task)

        task = self._to_running(task)
        task = Task(
            task_id=task.task_id,
            plan_id=task.plan_id,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            dependencies=task.dependencies,
            assigned_agent=task.assigned_agent,
            selected_model=model,
            skill=task.skill,
            tools=task.tools,
            policy=task.policy,
            inputs=task.inputs,
            outputs=task.outputs,
            artifacts=task.artifacts,
            evidence=task.evidence,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            created_at=task.created_at,
            updated_at=self._now(),
            completed_at=task.completed_at,
            error=task.error,
            approval_state=task.approval_state,
        )
        self._save_task(task)
        self._event(
            EventType.TASK_STARTED,
            task,
            agent_id=agent.id,
            payload={"model": model, "agent": agent.id},
        )

        try:
            result = self._work(agent, task, self._policy, self)
            task = self._apply_result(task, result)
        except Exception as exc:  # noqa: BLE001 - structured task failure
            task = self._handle_failure(task, agent, exc)
        self._save_task(task)
        return task

    # ---- internals ----
    def _select_model(self, agent: Agent, task: Task) -> str | None:
        if agent.model is not None:
            return agent.model
        capability = ModelCapability.RESEARCH
        if task.skill and "verif" in task.skill:
            capability = ModelCapability.VERIFICATION
        elif task.skill and ("coding" in task.skill or "test" in task.skill):
            capability = ModelCapability.CODING
        selection = self._router.resolve(
            ModelRequest(
                capability=capability, complexity="normal", requires_tools=True
            )
        )
        return selection.model

    def _to_running(self, task: Task) -> Task:
        """Return a copy of ``task`` that is legally in the RUNNING state."""
        status = task.status
        if status is TaskStatus.PENDING or status is TaskStatus.FAILED:
            task = task.with_status(TaskStatus.READY)
        return task.with_status(TaskStatus.RUNNING)

    def _apply_result(self, task: Task, result: AgentResult) -> Task:
        updated = self._now()
        if result.status == "completed":
            task = Task(
                task_id=task.task_id,
                plan_id=task.plan_id,
                title=task.title,
                description=task.description,
                status=TaskStatus.VERIFYING,
                priority=task.priority,
                dependencies=task.dependencies,
                assigned_agent=task.assigned_agent,
                selected_model=task.selected_model,
                skill=task.skill,
                tools=task.tools,
                policy=task.policy,
                inputs=task.inputs,
                outputs=dict(result.metrics),
                artifacts=task.artifacts + result.artifacts,
                evidence=task.evidence + result.evidence,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                created_at=task.created_at,
                updated_at=updated,
                completed_at=None,
                error=None,
                approval_state=task.approval_state,
            )
            for artifact in result.artifacts:
                self._store.save_artifact(artifact)
                self._event(
                    EventType.ARTIFACT_CREATED,
                    task,
                    payload={
                        "artifact_id": artifact.artifact_id,
                        "artifact_type": artifact.type,
                        "hash": artifact.content_hash[:16],
                    },
                )
            self._event(
                EventType.TASK_COMPLETED,
                task,
                payload={
                    "summary_len": len(result.summary),
                    "next": result.next_action,
                },
            )
            return task
        if result.status == "needs_approval":
            task = Task(
                task_id=task.task_id,
                plan_id=task.plan_id,
                title=task.title,
                description=task.description,
                status=TaskStatus.NEEDS_APPROVAL,
                priority=task.priority,
                dependencies=task.dependencies,
                assigned_agent=task.assigned_agent,
                selected_model=task.selected_model,
                skill=task.skill,
                tools=task.tools,
                policy=task.policy,
                inputs=task.inputs,
                outputs=task.outputs,
                artifacts=task.artifacts,
                evidence=task.evidence,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                created_at=task.created_at,
                updated_at=updated,
                completed_at=None,
                error=result.error,
                approval_state="pending",
            )
            metadata = dict(result.metrics or {})
            self._store.save_approval_request(
                task.plan_id,
                task.task_id,
                str(metadata.get("permission", "unknown")),
                tool=str(
                    metadata.get("tool") or (task.tools[0] if task.tools else None)
                ),
                risk=str(metadata.get("risk", "unknown")),
                reason=str(metadata.get("reason") or result.error),
                requested_at=updated,
            )
            self._event(EventType.APPROVAL_REQUESTED, task)
            self._event(
                EventType.APPROVAL_GATE_REQUIRED,
                task,
                payload={
                    "permission": metadata.get("permission"),
                    "risk": metadata.get("risk"),
                },
            )
            self._event(EventType.TASK_WAITING_APPROVAL, task)
            return task
        # generic failure
        return self._mark_failed(
            task, result.error or "agent returned a non-success status"
        )

    def _handle_failure(self, task: Task, agent: Agent, exc: Exception) -> Task:
        retryable = isinstance(exc, RetryableExecutionError)
        if retryable and task.retry_count < task.max_retries:
            retried = Task(
                task_id=task.task_id,
                plan_id=task.plan_id,
                title=task.title,
                description=task.description,
                status=TaskStatus.WAITING,
                priority=task.priority,
                dependencies=task.dependencies,
                assigned_agent=task.assigned_agent,
                selected_model=task.selected_model,
                skill=task.skill,
                tools=task.tools,
                policy=task.policy,
                inputs=task.inputs,
                outputs=task.outputs,
                artifacts=task.artifacts,
                evidence=task.evidence,
                retry_count=task.retry_count + 1,
                max_retries=task.max_retries,
                created_at=task.created_at,
                updated_at=self._now(),
                completed_at=None,
                error=f"{type(exc).__name__}: {exc}",
                approval_state=task.approval_state,
            )
            self._event(
                EventType.TASK_RETRYING,
                retried,
                payload={"retry": retried.retry_count, "error": type(exc).__name__},
            )
            return retried
        self._event(
            EventType.TASK_FAILED,
            task,
            payload={"error": type(exc).__name__},
        )
        return self._mark_failed(task, f"{type(exc).__name__}: {exc}")

    def _mark_failed(self, task: Task, error: str) -> Task:
        return Task(
            task_id=task.task_id,
            plan_id=task.plan_id,
            title=task.title,
            description=task.description,
            status=TaskStatus.FAILED,
            priority=task.priority,
            dependencies=task.dependencies,
            assigned_agent=task.assigned_agent,
            selected_model=task.selected_model,
            skill=task.skill,
            tools=task.tools,
            policy=task.policy,
            inputs=task.inputs,
            outputs=task.outputs,
            artifacts=task.artifacts,
            evidence=task.evidence,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            created_at=task.created_at,
            updated_at=self._now(),
            completed_at=self._now(),
            error=error,
            approval_state=task.approval_state,
        )

    def _save_task(self, task: Task) -> None:
        self._store.save_task(task)

    def emit_tool_event(
        self,
        event_type: str,
        task: Task,
        *,
        tool: str | None = None,
        agent: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Emit a tool-level orchestration event (called/completed/denied)."""
        self._event(event_type, task, agent_id=agent, payload=payload, tool=tool)

    def _event(
        self,
        event_type: str,
        task: Task,
        *,
        agent_id: str | None = None,
        tool: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        seq = self._seq()
        event = OrchestrationEvent(
            id=f"{event_type}:{seq}:{task.task_id}",
            seq=seq,
            event_type=event_type,
            plan_id=task.plan_id,
            task_id=task.task_id,
            agent_id=agent_id or task.assigned_agent,
            tool=tool,
            timestamp=self._now(),
            status=task.status.value,
            payload=payload or {},
        )
        self._emit(event)


class _Counter:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> int:
        self._value += 1
        return self._value
