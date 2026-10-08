"""The orchestrator: ties planner, executor, and verifier into a workflow.

For Phase 39/39B this is the minimal deterministic researcher -> verifier
workflow. Given an objective it creates a plan, builds its tasks, runs the
researcher task (which gathers evidence through the policy engine), verifies
the result deterministically, runs the reviewer task, and records plan/event
state.

The orchestrator owns the plan/task lifecycle and emits all orchestration
events; it does not contain recursive autonomous loops.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.registry import AgentRegistry
from personal_ai.agents.routing import ModelRouter
from personal_ai.execution.approval import ApprovalContext
from personal_ai.execution.events import EventType, OrchestrationEvent
from personal_ai.execution.executor import TaskExecutor
from personal_ai.execution.models import (
    Plan,
    PlanStatus,
    Task,
    TaskStatus,
    TaskTransitionError,
    new_execution_id,
    plan_transition_from,
)
from personal_ai.execution.planner import Planner
from personal_ai.execution.storage import OrchestrationStore
from personal_ai.execution.verifier import DeterministicVerifier
from personal_ai.execution.workflows import default_work_dispatch


class OrchestrationError(Exception):
    """Base class for orchestrator failures."""


class PlanNotCompleteError(OrchestrationError):
    """Raised when an operation requires a plan that is not yet complete."""


class Orchestrator:
    """Owns plan lifecycle and drives the researcher -> verifier workflow."""

    def __init__(
        self,
        store: OrchestrationStore,
        planner: Planner,
        policy: PolicyEngine,
        router: ModelRouter,
        agents: AgentRegistry,
        emit: Callable[[OrchestrationEvent], None] | None = None,
        now: Callable[[], str] | None = None,
        verifier: DeterministicVerifier | None = None,
        approval_context: ApprovalContext | None = None,
        work_dispatch: Callable | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._policy = policy
        self._router = router
        self._agents = agents
        self._emit = emit or (lambda event: store.append_event(event))
        self._now = now or (lambda: "1970-01-01T00:00:00+00:00")
        self._verifier = verifier or DeterministicVerifier()
        # The event counter starts from durable state so resumed executions
        # (e.g. after a restart) never re-use sequence numbers: cursor-based
        # event reads stay strictly monotonic across the whole history.
        self._seq = _Counter(start=store.max_event_seq())
        self._approval = approval_context
        self._executor = TaskExecutor(
            store=store,
            agents=agents,
            policy=policy,
            router=router,
            work=work_dispatch or default_work_dispatch(self._verifier),
            emit=self._emit,
            event_sequence=self._seq,
            now=self._now,
        )

    def create_research_plan(
        self, objective: str, execution_id: str | None = None
    ) -> Plan:
        execution_id = execution_id or new_execution_id()
        created = self._now()
        plan = self._planner.deterministic_research_plan(
            execution_id, objective, created
        )
        self._store.save_plan(plan)
        for task in self._planner.build_tasks(plan, created):
            self._store.save_task(task)
            self._event(
                EventType.TASK_CREATED,
                plan_id=execution_id,
                task_id=task.task_id,
                agent_id=task.assigned_agent,
                status=task.status.value,
                payload={"agent": task.assigned_agent, "skill": task.skill},
            )
        self._event(
            EventType.PLAN_CREATED,
            plan_id=execution_id,
            status=plan.status.value,
            payload={"tasks": list(plan.task_ids)},
        )
        self._event(
            EventType.EXECUTION_CREATED,
            plan_id=execution_id,
            status="created",
            payload={"objective_len": len(objective)},
        )
        return plan

    def get_execution(self, execution_id: str) -> Plan | None:
        """Return the durable execution (plan) state for ``execution_id``."""
        return self._store.get_plan(execution_id)

    def resume_execution(self, execution_id: str) -> Plan:
        """Run/continue an execution, idempotently and resumably.

        This is the control-plane continuation hook: it advances a plan that
        is PENDING/PLANNED/RUNNING/WAITING_APPROVAL through its researcher ->
        verifier workflow, skipping work that is already done, and pausing on
        any task that still requires approval.
        """
        plan = self._store.get_plan(execution_id)
        if plan is None:
            raise PlanNotCompleteError(f"Unknown execution: {execution_id}")
        if plan.status in (
            PlanStatus.COMPLETED,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
        ):
            return plan

        resumed = plan.status in (
            PlanStatus.RUNNING,
            PlanStatus.BLOCKED,
            PlanStatus.NEEDS_APPROVAL,
        )
        plan = plan.with_status(PlanStatus.RUNNING)
        self._store.save_plan(plan)
        self._event(
            EventType.EXECUTION_RESUMED if resumed else EventType.EXECUTION_STARTED,
            plan_id=execution_id,
            status=plan.status.value,
        )

        tasks = self._store.tasks_for_plan(execution_id)
        research = _find_task(tasks, _RESEARCH_SKILLS)
        if research is None:
            raise PlanNotCompleteError("No research task found in plan")
        if research.status not in (
            TaskStatus.VERIFYING,
            TaskStatus.COMPLETED,
        ):
            done = self._execute_task(research)
            research = done
            if done.status is TaskStatus.NEEDS_APPROVAL:
                return _paused_plan(self, plan, done.error or "approval required")
            if done.status.value != "verifying":
                return _fail_plan(self, plan, done.error or done.status.value)

        self._event(
            EventType.VERIFICATION_STARTED,
            plan_id=execution_id,
            status=research.status.value,
        )

        verify_task = _find_task(tasks, "verification")
        verify_done = None
        if verify_task is not None and verify_task.status not in (
            TaskStatus.VERIFYING,
            TaskStatus.COMPLETED,
        ):
            verify_task = _with_review_inputs(verify_task, research)
            self._store.save_task(verify_task)
            verify_done = self._execute_task(verify_task)
            if verify_done.status is TaskStatus.NEEDS_APPROVAL:
                return _paused_plan(
                    self, plan, verify_done.error or "approval required"
                )
            if verify_done.status.value == "failed":
                return _fail_plan(
                    self, plan, verify_done.error or "verification failed"
                )

        _complete_unverified(self, research, verify_done)

        outcome = research.outputs.get("summary", "")
        completed = Plan(
            plan_id=execution_id,
            objective=plan.objective,
            status=PlanStatus.COMPLETED,
            task_ids=plan.task_ids,
            risk=plan.risk,
            assumptions=plan.assumptions,
            constraints=plan.constraints,
            final_outcome=str(outcome),
            created_at=plan.created_at,
            updated_at=self._now(),
        )
        self._store.save_plan(completed)
        self._event(
            EventType.PLAN_COMPLETED,
            plan_id=execution_id,
            status=PlanStatus.COMPLETED.value,
            payload={"evidence_count": len(research.evidence)},
        )
        self._event(
            EventType.EXECUTION_COMPLETED,
            plan_id=execution_id,
            status="completed",
            payload={"evidence_count": len(research.evidence)},
        )
        return completed

    def _execute_task(self, task: Task) -> Task:
        """Execute one task with a scoped approval context (cleared on exit)."""
        if self._approval is not None:
            self._approval.set_current(task.plan_id, task.task_id)
        try:
            done = self._executor.execute(task)
        finally:
            if self._approval is not None:
                self._approval.clear()
        self._store.save_task(done)
        return done

    def approve(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        *,
        approver: str = "user",
    ) -> dict[str, str]:
        """Approve a specific task+permission for an execution.

        Validates that the task belongs to the execution and that a matching
        pending approval request exists. Approval is task- and permission-
        scoped; it authorizes exactly the gated operation.
        """
        request = self._request_for(execution_id, task_id, permission)
        if not request:
            raise PlanNotCompleteError(
                f"No pending approval for {execution_id}/{task_id}/{permission}"
            )
        self._store.decide_approval(
            execution_id, task_id, permission, "approved", approver=approver
        )
        task = self._store.get_task(task_id)
        if task is not None and task.status is TaskStatus.NEEDS_APPROVAL:
            self._store.save_task(task.with_status(TaskStatus.READY))
        self._event(
            EventType.APPROVAL_GRANTED,
            plan_id=execution_id,
            task_id=task_id,
            payload={"permission": permission, "agent": approver},
        )
        self._event(
            EventType.TASK_APPROVED,
            plan_id=execution_id,
            task_id=task_id,
            payload={"permission": permission},
        )
        return {
            "execution_id": execution_id,
            "task_id": task_id,
            "permission": permission,
        }

    def reject(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        *,
        approver: str = "user",
    ) -> dict[str, str]:
        """Reject a pending approval; the gated task is failed (never approved)."""
        request = self._request_for(execution_id, task_id, permission)
        if not request:
            raise PlanNotCompleteError(
                f"No pending approval for {execution_id}/{task_id}/{permission}"
            )
        self._store.decide_approval(
            execution_id, task_id, permission, "rejected", approver=approver
        )
        task = self._store.get_task(task_id)
        if task is not None and task.status is TaskStatus.NEEDS_APPROVAL:
            self._store.save_task(_mark_rejected(task, self._now()))
        self._event(
            EventType.APPROVAL_DENIED,
            plan_id=execution_id,
            task_id=task_id,
            payload={"permission": permission, "agent": approver},
        )
        self._event(
            EventType.TASK_REJECTED,
            plan_id=execution_id,
            task_id=task_id,
            payload={"permission": permission},
        )
        # A rejected approval forbids the gated work outright: the execution
        # must not silently continue (or requeue) that work, so the plan is
        # driven to a terminal failed state.
        plan = self._store.get_plan(execution_id)
        if plan is not None and plan.status is not PlanStatus.FAILED:
            _fail_plan(
                self,
                plan,
                f"approval rejected for {task_id} [{permission}]",
            )
        return {
            "execution_id": execution_id,
            "task_id": task_id,
            "permission": permission,
        }

    def pause_execution(self, execution_id: str) -> Plan:
        plan = self._store.get_plan(execution_id)
        if plan is None:
            raise PlanNotCompleteError(f"Unknown execution: {execution_id}")
        if plan.status in (
            PlanStatus.COMPLETED,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
        ):
            return plan
        paused = plan.with_status(PlanStatus.BLOCKED)
        self._store.save_plan(paused)
        return paused

    def cancel_execution(self, execution_id: str) -> Plan:
        plan = self._store.get_plan(execution_id)
        if plan is None:
            raise PlanNotCompleteError(f"Unknown execution: {execution_id}")
        if plan.status in (
            PlanStatus.COMPLETED,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
        ):
            return plan
        cancelled = plan.with_status(PlanStatus.CANCELLED)
        self._store.save_plan(cancelled)
        for task in self._store.tasks_for_plan(execution_id):
            if task.status not in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                try:
                    self._store.save_task(task.with_status(TaskStatus.CANCELLED))
                except TaskTransitionError:
                    pass
        self._event(
            EventType.EXECUTION_CANCELLED,
            plan_id=execution_id,
            status=PlanStatus.CANCELLED.value,
            payload={"reason": "cancelled"},
        )
        return cancelled

    def retry_task(self, execution_id: str, task_id: str) -> Task:
        """Explicitly recover a failed task: ``FAILED -> READY`` and reopen the plan.

        Only a task that durably failed and belongs to this execution can be
        retried. A *rejected* approval is never undone by a retry: the durable
        ``rejected`` record keeps the policy approver from granting, so a
        retried task that hits the same gate pauses for approval again instead
        of silently running.
        """
        plan = self._store.get_plan(execution_id)
        if plan is None or task_id not in plan.task_ids:
            raise PlanNotCompleteError(f"Unknown execution: {execution_id}")
        task = self._store.get_task(task_id)
        if task is None:
            raise PlanNotCompleteError(f"Unknown task: {task_id}")
        if task.status is not TaskStatus.FAILED:
            raise PlanNotCompleteError(
                f"Task {task_id} is not failed (status={task.status.value})"
            )

        now = self._now()
        if plan.status is not PlanStatus.RUNNING:
            plan_transition_from(plan.status, PlanStatus.RUNNING)
            reopened = Plan(
                plan_id=plan.plan_id,
                objective=plan.objective,
                status=PlanStatus.RUNNING,
                task_ids=plan.task_ids,
                risk=plan.risk,
                assumptions=plan.assumptions,
                constraints=plan.constraints,
                final_outcome=None,
                created_at=plan.created_at,
                updated_at=now,
            )
            self._store.save_plan(reopened)
            self._event(
                EventType.EXECUTION_RESUMED,
                plan_id=execution_id,
                status=reopened.status.value,
                payload={"reason": f"retry {task_id}"},
            )

        retried = Task(
            task_id=task.task_id,
            plan_id=task.plan_id,
            title=task.title,
            description=task.description,
            status=TaskStatus.READY,
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
            updated_at=now,
            completed_at=None,
            error=None,
            approval_state=task.approval_state,
        )
        self._store.save_task(retried)
        self._event(
            EventType.TASK_READY,
            plan_id=execution_id,
            task_id=task_id,
            status=TaskStatus.READY.value,
            payload={"reason": "retry"},
        )
        return retried

    def _request_for(
        self, execution_id: str, task_id: str, permission: str
    ) -> dict[str, object] | None:
        plan = self._store.get_plan(execution_id)
        if plan is None or task_id not in plan.task_ids:
            return None
        for req in self._store.approval_requests(execution_id):
            if req.get("task_id") == task_id and req.get("permission") == permission:
                if req.get("status") == "pending":
                    return req
                raise PlanNotCompleteError(
                    f"Approval for {task_id}/{permission} is already decided"
                )
        return None

    def run_research_plan(self, plan_id: str) -> Plan:
        """Legacy one-shot entry point for the researcher -> verifier workflow.

        This is intentionally a thin compatibility alias for the canonical
        :meth:`resume_execution` path. There is exactly one execution engine:
        approval-gated tasks pause the plan durably here exactly as they do on
        the control-plane path, and ``approve()``/``reject()``/``resume()``
        work identically regardless of which entry point started the
        execution. Keeping a single engine avoids two subtly different
        approval semantics.
        """
        return self.resume_execution(plan_id)

    def _event(
        self,
        event_type: str,
        *,
        plan_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        seq = self._seq()
        event = OrchestrationEvent(
            id=f"{event_type}:{seq}:{plan_id or ''}:{task_id or ''}",
            seq=seq,
            event_type=event_type,
            plan_id=plan_id or "",
            task_id=task_id,
            agent_id=agent_id,
            timestamp=self._now(),
            status=status,
            payload=payload or {},
        )
        self._emit(event)


def _find_task(tasks, skills):
    """Find the first task whose skill is one of ``skills`` (str or tuple)."""
    names = (skills,) if isinstance(skills, str) else tuple(skills)
    for task in tasks:
        if task.skill in names:
            return task
    return None


_RESEARCH_SKILLS = ("corpus-research", "memory-research", "workout-research")


def _with_review_inputs(verify_task, research_task):
    inputs = dict(verify_task.inputs)
    inputs["evidence"] = [
        {
            "evidence_id": e.evidence_id,
            "source_type": e.source_type,
            "source": e.source,
            "document_id": e.document_id,
            "excerpt": e.excerpt,
        }
        for e in research_task.evidence
    ]
    inputs["summary"] = research_task.outputs.get("summary", "")
    return Task(
        task_id=verify_task.task_id,
        plan_id=verify_task.plan_id,
        title=verify_task.title,
        description=verify_task.description,
        status=verify_task.status,
        priority=verify_task.priority,
        dependencies=verify_task.dependencies,
        assigned_agent=verify_task.assigned_agent,
        selected_model=verify_task.selected_model,
        skill=verify_task.skill,
        tools=verify_task.tools,
        policy=verify_task.policy,
        inputs=inputs,
        outputs=verify_task.outputs,
        artifacts=verify_task.artifacts,
        evidence=verify_task.evidence,
        retry_count=verify_task.retry_count,
        max_retries=verify_task.max_retries,
        created_at=verify_task.created_at,
        updated_at=verify_task.updated_at,
        completed_at=verify_task.completed_at,
        error=verify_task.error,
        approval_state=verify_task.approval_state,
    )


def _fail_plan(orchestrator: Orchestrator, plan: Plan, reason: str) -> Plan:
    plan_transition_from(plan.status, PlanStatus.FAILED)
    failed = Plan(
        plan_id=plan.plan_id,
        objective=plan.objective,
        status=PlanStatus.FAILED,
        task_ids=plan.task_ids,
        risk=plan.risk,
        assumptions=plan.assumptions,
        constraints=plan.constraints,
        final_outcome=None,
        created_at=plan.created_at,
        updated_at=orchestrator._now(),
    )
    orchestrator._store.save_plan(failed)
    orchestrator._event(
        EventType.PLAN_FAILED,
        plan_id=plan.plan_id,
        status=PlanStatus.FAILED.value,
        payload={"reason": reason},
    )
    orchestrator._event(
        EventType.EXECUTION_FAILED,
        plan_id=plan.plan_id,
        status=PlanStatus.FAILED.value,
        payload={"reason": reason},
    )
    return failed


def _slug(text: str) -> str:
    keep = "".join(ch for ch in text.lower() if ch.isalnum() or ch in "-_")
    return f"plan-{keep[:24] or 'objective'}"


def _paused_plan(orchestrator: Orchestrator, plan: Plan, reason: str) -> Plan:
    """Transition a plan to a waiting-for-approval state and record the gate."""
    plan_transition_from(plan.status, PlanStatus.NEEDS_APPROVAL)
    paused = Plan(
        plan_id=plan.plan_id,
        objective=plan.objective,
        status=PlanStatus.NEEDS_APPROVAL,
        task_ids=plan.task_ids,
        risk=plan.risk,
        assumptions=plan.assumptions,
        constraints=plan.constraints,
        final_outcome=None,
        created_at=plan.created_at,
        updated_at=orchestrator._now(),
    )
    orchestrator._store.save_plan(paused)
    orchestrator._event(
        EventType.EXECUTION_PAUSED,
        plan_id=plan.plan_id,
        status=PlanStatus.NEEDS_APPROVAL.value,
        payload={"reason": reason},
    )
    return paused


def _mark_rejected(task: Task, now: str) -> Task:
    """Mark a task as failed because its approval was rejected."""
    from dataclasses import replace

    if task.status is TaskStatus.NEEDS_APPROVAL:
        task = task.with_status(TaskStatus.READY)
    return replace(
        task,
        status=TaskStatus.FAILED,
        error="approval rejected",
        updated_at=now,
        completed_at=now,
    )


def _complete_unverified(
    orchestrator: Orchestrator,
    research: Task | None,
    verify_task: Task | None,
) -> None:
    """Close the loop on tasks whose work finished and was verified.

    The executor leaves finished work in VERIFYING; once the orchestrator has
    driven verification to a pass, the VERIFYING -> COMPLETED transition marks
    the task as durably done so projections (board, result counts) see a
    terminal state and a correct ``completed_at``.
    """
    from dataclasses import replace

    now = orchestrator._now()
    for task in (research, verify_task):
        if task is not None and task.status is TaskStatus.VERIFYING:
            completed = task.with_status(TaskStatus.COMPLETED)
            orchestrator._store.save_task(replace(completed, completed_at=now))


class _Counter:
    def __init__(self, start: int = 0) -> None:
        self._value = start

    def __call__(self) -> int:
        self._value += 1
        return self._value
