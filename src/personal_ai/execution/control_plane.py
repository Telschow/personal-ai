"""Application-level control-plane service.

This is the single, thin service boundary between the execution runtime and
every client (the CLI today; a future HTTP API and Kanban/Open WebUI tomorrow).
It keeps business logic out of the CLI and prevents any future UI from touching
SQLite/store internals directly.

Responsibilities:

* build/own the runtime components (policy engine is wired with a durable,
  task-/permission-scoped :class:`ApprovalContext` approver);
* expose the execution lifecycle: create, run/resume, pause, cancel, approve,
  reject;
* expose read projections: execution, tasks, events (cursor), board, artifacts,
  evidence, result.

Everything stays local/SQLite-backed; nothing here requires network or Ollama.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.defs import build_default_agent_registry
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.routing import (
    ModelCapability,
    ModelRouter,
    OllamaProvider,
)
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    SEARCH_MEMORY,
    SEARCH_WORKOUTS,
    _memory_search_handler,
    _workout_search_handler,
    build_default_agent_tools,
)
from personal_ai.execution.approval import ApprovalContext
from personal_ai.execution.board import ExecutionBoard
from personal_ai.execution.events import OrchestrationEvent
from personal_ai.execution.models import Plan
from personal_ai.execution.orchestrator import Orchestrator
from personal_ai.execution.planner import Planner
from personal_ai.execution.storage import OrchestrationStore
from personal_ai.execution.verifier import DeterministicVerifier
from personal_ai.memory.models import Memory, MemoryDraft, MemoryStatus
from personal_ai.memory.retriever import MemoryHit, ScopeFilter
from personal_ai.memory.service import (
    MemoryNotConfiguredError,
    MemoryService,
)


class ExecutionNotFoundError(Exception):
    """Raised when an operation references an unknown execution id."""


class ControlPlane:
    """Local control-plane service over the execution runtime.

    The service boundary for *both* execution orchestration and durable
    memory: clients (CLI today, a future HTTP API and Kanban/Open WebUI
    tomorrow) interact only with this class. ``memory`` is optional — when
    absent the execution runtime is fully usable and every ``memory_*``
    operation raises :class:`MemoryNotConfiguredError`.
    """

    def __init__(
        self,
        store: OrchestrationStore,
        *,
        agents: object | None = None,
        skills: object | None = None,
        tools: object | None = None,
        policy: PolicyEngine | None = None,
        router: ModelRouter | None = None,
        planner: Planner | None = None,
        verifier: DeterministicVerifier | None = None,
        work_dispatch: Callable | None = None,
        now: Callable[[], str] | None = None,
        memory: MemoryService | None = None,
        workout: object | None = None,
    ) -> None:
        self._store = store
        self._memory = memory
        self._workout = workout
        actual_agents = agents if agents is not None else build_default_agent_registry()
        actual_skills = skills if skills is not None else build_default_skill_registry()
        actual_tools = tools if tools is not None else build_default_agent_tools()
        if memory is not None and "search_memory" not in actual_tools:
            actual_tools.register(SEARCH_MEMORY, _memory_search_handler(memory))
        if workout is not None and "search_workouts" not in actual_tools:
            actual_tools.register(SEARCH_WORKOUTS, _workout_search_handler(workout))

        self._approval = ApprovalContext(store)
        actual_policy = policy or PolicyEngine(
            actual_tools,
            actual_agents,
            actual_skills,
            approver=self._approval.make_approver(),
        )
        actual_router = router or _default_router()
        actual_planner = planner or Planner(actual_agents, actual_skills)

        self._agents = actual_agents
        self._orchestrator = Orchestrator(
            store,
            planner=actual_planner,
            policy=actual_policy,
            router=actual_router,
            agents=actual_agents,
            verifier=verifier,
            approval_context=self._approval,
            work_dispatch=work_dispatch,
            now=now,
        )
        self._board = ExecutionBoard(store, actual_agents)

    # ---- lifecycle ----
    def create_research_execution(self, objective: str) -> Plan:
        return self._orchestrator.create_research_plan(objective)

    def create_execution(self, objective: str, execution_id: str | None = None) -> Plan:
        return self._orchestrator.create_research_plan(
            objective, execution_id=execution_id
        )

    def get_execution(self, execution_id: str) -> Plan:
        plan = self._store.get_plan(execution_id)
        if plan is None:
            raise ExecutionNotFoundError(execution_id)
        return plan

    def list_executions(self) -> tuple[str, ...]:
        return self._store.list_plan_ids()

    def run_execution(self, execution_id: str) -> Plan:
        return self.resume_execution(execution_id)

    def resume_execution(self, execution_id: str) -> Plan:
        return self._orchestrator.resume_execution(execution_id)

    def pause_execution(self, execution_id: str) -> Plan:
        return self._orchestrator.pause_execution(execution_id)

    def cancel_execution(self, execution_id: str) -> Plan:
        return self._orchestrator.cancel_execution(execution_id)

    def retry_task(self, execution_id: str, task_id: str) -> object:
        """Explicitly retry a failed task (recovery); rejects never re-grant."""
        return self._orchestrator.retry_task(execution_id, task_id)

    # ---- approvals ----
    def approve(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        *,
        approver: str = "user",
    ) -> dict[str, str]:
        return self._orchestrator.approve(
            execution_id, task_id, permission, approver=approver
        )

    def reject(
        self,
        execution_id: str,
        task_id: str,
        permission: str,
        *,
        approver: str = "user",
    ) -> dict[str, str]:
        return self._orchestrator.reject(
            execution_id, task_id, permission, approver=approver
        )

    def approvals(self, execution_id: str) -> tuple[dict[str, object], ...]:
        return self._store.approval_requests(execution_id)

    def tasks(self, execution_id: str) -> tuple[object, ...]:
        return self._store.tasks_for_plan(execution_id)

    def events(self, execution_id: str) -> tuple[OrchestrationEvent, ...]:
        return self._store.events_for_plan(execution_id)

    def events_after(
        self, execution_id: str, after_seq: int
    ) -> tuple[OrchestrationEvent, ...]:
        return self._store.events_after_seq(execution_id, after_seq)

    def board(self, execution_id: str) -> dict[str, object]:
        return self._board.board(execution_id)

    def artifacts(self, task_id: str) -> tuple[object, ...]:
        return self._store.artifacts_for_task(task_id)

    def evidence(self, task_id: str) -> tuple[object, ...]:
        return self._store.evidence_for_task(task_id)

    def result(self, execution_id: str) -> dict[str, object]:
        plan = self.get_execution(execution_id)
        tasks = self._store.tasks_for_plan(execution_id)
        completed = [t for t in tasks if t.status.value == "completed"]
        return {
            "execution_id": execution_id,
            "status": plan.status.value,
            "objective": plan.objective,
            "final_outcome": plan.final_outcome,
            "risk": plan.risk,
            "task_count": len(tasks),
            "completed_tasks": len(completed),
            "artifact_count": sum(
                len(self._store.artifacts_for_task(t.task_id)) for t in tasks
            ),
            "evidence_count": sum(
                len(self._store.evidence_for_task(t.task_id)) for t in tasks
            ),
        }

    # ---- memory (optional service; execution works without it) ----
    @property
    def memory_service(self) -> MemoryService:
        return self._require_memory()

    @property
    def workout_service(self) -> object:
        """The optional read-only workout query service (None when unwired)."""
        return self._workout

    def memory_create(self, draft: MemoryDraft) -> Memory:
        return self._require_memory().create(draft)

    def memory_create_user(
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
        return self._require_memory().create_user_memory(
            content,
            kind=kind,
            summary=summary,
            scope=scope,
            scope_id=scope_id,
            source_id=source_id,
            confidence=confidence,
            importance=importance,
            expires_at=expires_at,
        )

    def memory_get(self, memory_id: str) -> Memory:
        return self._require_memory().get(memory_id)

    def memory_update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        summary: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        expires_at: str | None = None,
    ) -> Memory:
        return self._require_memory().update(
            memory_id,
            content=content,
            summary=summary,
            confidence=confidence,
            importance=importance,
            expires_at=expires_at,
        )

    def memory_search(
        self,
        query: str,
        scopes: tuple[ScopeFilter, ...] = (),
        *,
        limit: int = 10,
        include_expired: bool = False,
    ) -> tuple[MemoryHit, ...]:
        """Deterministic, scope-aware retrieval; global scope by default."""
        return self._require_memory().search(
            query,
            scopes=scopes,
            limit=limit,
            include_expired=include_expired,
        )

    def memory_list(
        self, status: MemoryStatus | str | None = None
    ) -> tuple[Memory, ...]:
        return self._require_memory().list(status)

    def memory_archive(self, memory_id: str) -> Memory:
        return self._require_memory().archive(memory_id)

    def memory_delete(self, memory_id: str) -> Memory:
        """Logical deletion: provenance is kept, content never returns."""
        return self._require_memory().delete(memory_id)

    def memory_purge(self, memory_id: str) -> None:
        """Physical deletion for privacy-sensitive memories."""
        self._require_memory().purge(memory_id)

    def memory_events(self, memory_id: str) -> tuple[dict[str, object], ...]:
        return self._require_memory().events(memory_id)

    def _require_memory(self) -> MemoryService:
        if self._memory is None:
            raise MemoryNotConfiguredError(
                "ControlPlane was built without a MemoryService"
            )
        return self._memory


def _default_router() -> ModelRouter:
    return ModelRouter(
        fallback_model="ollama:qwen3.5:9b",
        providers={
            "ollama": OllamaProvider(
                chat_model="qwen3.5:9b",
                capability_models={
                    ModelCapability.RESEARCH: "qwen3.5:9b",
                    ModelCapability.VERIFICATION: "qwen3.5:9b",
                    ModelCapability.CODING: "qwen3.5:9b",
                },
            )
        },
    )
