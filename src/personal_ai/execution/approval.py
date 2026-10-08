"""Task-scoped, durable approval context for the execution runtime.

Approval in the control plane is *task-scoped and permission-scoped*: a single
``approve(execution_id, task_id, permission)`` authorizes only that one tool
permission for that one gated task. It never broadens the agent's policy and
never grants unrelated permissions — the same tool on a different task, or a
different permission on the same task, still requires its own approval.

The :class:`ApprovalContext` is wired into a :class:`PolicyEngine` approver so
the engine's existing, untouched permission gate is satisfied without changing
the foundational ``agents/policy.py``. Because the approver reads the persisted
``orchestration_approvals`` table, approvals survive process restarts and are
resumable.
"""

from __future__ import annotations

from personal_ai.execution.storage import OrchestrationStore


class ApprovalContext:
    """Carries the "currently executing" execution/task to a policy approver.

    The orchestrator sets ``current`` immediately before executing a task so
    the approver knows which task (and therefore which persisted approval
    record) is being evaluated.
    """

    def __init__(self, store: OrchestrationStore) -> None:
        self._store = store
        self._current: tuple[str, str] | None = None

    def set_current(self, execution_id: str, task_id: str) -> None:
        self._current = (execution_id, task_id)

    def clear(self) -> None:
        self._current = None

    def grant(self, agent_id: str, tool: str, permission: str) -> bool:
        """Return True only if the *current* task+permission is approved."""
        if self._current is None:
            return False
        execution_id, task_id = self._current
        return (
            self._store.approval_status(execution_id, task_id, permission) == "approved"
        )

    def make_approver(self):
        """Return a callable matching the PolicyEngine ``approver`` signature."""
        return self.grant
