"""Work functions for the orchestrator's built-in agents.

Work functions are the "what does a role actually do" implementations. They
run through the policy engine, emit tool/verification events, and return a
structured :class:`AgentResult`. They are injected into the executor (not
hard-coded), so tests can substitute fake work functions offline.
"""

from __future__ import annotations

from typing import Protocol

from personal_ai.agents.models import Agent
from personal_ai.agents.policy import (
    ApprovalRequiredError,
    PolicyDenialError,
    PolicyEngine,
)
from personal_ai.execution.events import EventType
from personal_ai.execution.models import AgentResult, Artifact, Evidence, Task
from personal_ai.execution.verifier import DeterministicVerifier


class ExecutorContext(Protocol):
    """The subset of the executor the work functions may call."""

    def emit_tool_event(
        self,
        event_type: str,
        task: Task,
        *,
        tool: str | None = None,
        agent: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None: ...


def research_work(
    agent: Agent,
    task: Task,
    policy: PolicyEngine,
    ctx: ExecutorContext,
) -> AgentResult:
    """Researcher: query the corpus through the policy engine and gather evidence."""
    query = task.description or task.title
    outcome, result, meta = _run_tool(
        policy, agent, task, "corpus.search", {"query": query}
    )
    if outcome == "denied":
        ctx.emit_tool_event(
            EventType.TOOL_DENIED,
            task,
            tool="corpus.search",
            agent=agent.id,
            payload={"reason": str(meta.get("reason"))},
        )
        return AgentResult(
            status="failed",
            summary="Research blocked by policy.",
            metrics={"reason": str(meta.get("reason"))},
            error=str(meta.get("reason")),
        )
    if outcome == "approval":
        ctx.emit_tool_event(
            EventType.TOOL_APPROVAL_REQUIRED,
            task,
            tool="corpus.search",
            agent=agent.id,
            payload={"permission": meta.get("permission")},
        )
        return AgentResult(
            status="needs_approval",
            summary="Research paused pending approval.",
            metrics={
                "permission": meta.get("permission"),
                "tool": "corpus.search",
                "risk": meta.get("risk", "low"),
                "reason": str(meta.get("reason")),
            },
            error=str(meta.get("reason")),
        )

    search_results = result
    evidence: list[Evidence] = []
    if isinstance(search_results, list):
        for idx, item in enumerate(search_results):
            if not isinstance(item, dict):
                continue
            evidence.append(
                Evidence(
                    evidence_id=f"{task.task_id}-ev-{idx}",
                    source_type=item.get("source_type"),
                    source=item.get("source"),
                    document_id=item.get("document_id"),
                    relevance=float(item.get("score", 0.0) or 0.0),
                    excerpt=_clip(str(item.get("text", ""))),
                )
            )

    summary = _summarize(task, evidence)
    artifact = Artifact(
        artifact_id=f"{task.task_id}-research-summary",
        type="research",
        title="Research summary",
        producing_task_id=task.task_id,
        content_hash=compute_short_hash(summary),
        reference=f"task://{task.task_id}",
        metadata={"evidence_count": len(evidence)},
    )
    ctx.emit_tool_event(
        EventType.TOOL_COMPLETED,
        task,
        tool="corpus.search",
        agent=agent.id,
        payload={"results": len(evidence)},
    )
    return AgentResult(
        status="completed",
        summary=summary,
        artifacts=(artifact,),
        evidence=tuple(evidence),
        metrics={"summary": summary, "evidence_count": len(evidence)},
    )


def memory_research_work(
    agent: Agent,
    task: Task,
    policy: PolicyEngine,
    ctx: ExecutorContext,
) -> AgentResult:
    """Researcher: search durable memory through the policy engine (read-only).

    The tool is invoked with the trusted execution context (plan id and
    agent id), so scope derivation is deterministic and never attacker-tuned.
    Retrieved memories become evidence; no memory is created or mutated and
    emitted events carry only a count — never content or the query.
    """
    query = task.description or task.title
    outcome, result, meta = _run_tool(
        policy,
        agent,
        task,
        "search_memory",
        {
            "query": query,
            "execution_id": task.plan_id,
            "agent_id": task.assigned_agent,
        },
    )
    if outcome == "denied":
        ctx.emit_tool_event(
            EventType.TOOL_DENIED,
            task,
            tool="search_memory",
            agent=agent.id,
            payload={"reason": str(meta.get("reason"))},
        )
        return AgentResult(
            status="failed",
            summary="Memory retrieval blocked by policy.",
            metrics={"reason": str(meta.get("reason"))},
            error=str(meta.get("reason")),
        )
    if outcome == "approval":
        ctx.emit_tool_event(
            EventType.TOOL_APPROVAL_REQUIRED,
            task,
            tool="search_memory",
            agent=agent.id,
            payload={"permission": meta.get("permission")},
        )
        return AgentResult(
            status="needs_approval",
            summary="Memory retrieval paused pending approval.",
            metrics={
                "permission": meta.get("permission"),
                "tool": "search_memory",
                "risk": meta.get("risk", "low"),
                "reason": str(meta.get("reason")),
            },
            error=str(meta.get("reason")),
        )

    memories: list[dict[str, object]] = []
    if isinstance(result, dict):
        raw = result.get("memories")
        if isinstance(raw, list):
            memories = [item for item in raw if isinstance(item, dict)]

    evidence: list[Evidence] = [
        Evidence(
            evidence_id=f"{task.task_id}-mem-{idx}",
            source_type="memory",
            source=f"memory://{item.get('memory_id', '')}",
            document_id=item.get("memory_id"),
            relevance=float(item.get("score", 0.0) or 0.0),
            excerpt=_clip(str(item.get("content", ""))),
        )
        for idx, item in enumerate(memories)
    ]

    summary = _summarize_memory(task, evidence)
    artifact = Artifact(
        artifact_id=f"{task.task_id}-memory-summary",
        type="memory",
        title="Memory retrieval summary",
        producing_task_id=task.task_id,
        content_hash=compute_short_hash(summary),
        reference=f"task://{task.task_id}",
        metadata={"memory_count": len(evidence)},
    )
    ctx.emit_tool_event(
        EventType.TOOL_COMPLETED,
        task,
        tool="search_memory",
        agent=agent.id,
        payload={"tool": "search_memory", "count": len(evidence)},
    )
    return AgentResult(
        status="completed",
        summary=summary,
        artifacts=(artifact,),
        evidence=tuple(evidence),
        metrics={"summary": summary, "memory_count": len(evidence)},
    )


def workout_research_work(
    agent: Agent,
    task: Task,
    policy: PolicyEngine,
    ctx: ExecutorContext,
) -> AgentResult:
    """Researcher: query workout activity through the policy engine (read-only).

    The tool is invoked with the trusted execution context; matched workouts
    become evidence. Nothing is created or mutated and emitted events carry
    only a count — never exercise names, dates, or the query.
    """
    query = task.description or task.title
    outcome, result, meta = _run_tool(
        policy,
        agent,
        task,
        "search_workouts",
        {"query": query},
    )
    if outcome == "denied":
        ctx.emit_tool_event(
            EventType.TOOL_DENIED,
            task,
            tool="search_workouts",
            agent=agent.id,
            payload={"reason": str(meta.get("reason"))},
        )
        return AgentResult(
            status="failed",
            summary="Workout search blocked by policy.",
            metrics={"reason": str(meta.get("reason"))},
            error=str(meta.get("reason")),
        )
    if outcome == "approval":
        # search_workouts is a read-only, always-ALLOWED tool; kept for
        # symmetry with the corpus/memory researchers if gating ever changes.
        ctx.emit_tool_event(
            EventType.TOOL_APPROVAL_REQUIRED,
            task,
            tool="search_workouts",
            agent=agent.id,
            payload={"permission": meta.get("permission")},
        )
        return AgentResult(
            status="needs_approval",
            summary="Workout search paused pending approval.",
            metrics={
                "permission": meta.get("permission"),
                "tool": "search_workouts",
                "risk": meta.get("risk", "low"),
                "reason": str(meta.get("reason")),
            },
            error=str(meta.get("reason")),
        )

    workouts: list[dict[str, object]] = []
    if isinstance(result, dict):
        raw = result.get("workouts")
        if isinstance(raw, list):
            workouts = [item for item in raw if isinstance(item, dict)]

    evidence: list[Evidence] = [
        Evidence(
            evidence_id=f"{task.task_id}-wkt-{idx}",
            source_type="workout",
            source=f"workout://{item.get('workout_id', '')}",
            document_id=item.get("workout_id"),
            relevance=0.0,
            excerpt=_clip(_workout_excerpt(item)),
        )
        for idx, item in enumerate(workouts)
    ]

    summary = _summarize_workout(task, evidence)
    artifact = Artifact(
        artifact_id=f"{task.task_id}-workout-summary",
        type="workout",
        title="Workout research summary",
        producing_task_id=task.task_id,
        content_hash=compute_short_hash(summary),
        reference=f"task://{task.task_id}",
        metadata={"workout_count": len(evidence)},
    )
    ctx.emit_tool_event(
        EventType.TOOL_COMPLETED,
        task,
        tool="search_workouts",
        agent=agent.id,
        payload={"tool": "search_workouts", "count": len(evidence)},
    )
    return AgentResult(
        status="completed",
        summary=summary,
        artifacts=(artifact,),
        evidence=tuple(evidence),
        metrics={"summary": summary, "workout_count": len(evidence)},
    )


def reviewer_work(
    agent: Agent,
    task: Task,
    policy: PolicyEngine,
    ctx: ExecutorContext,
    verifier: DeterministicVerifier | None = None,
) -> AgentResult:
    """Reviewer: run the deterministic verification contract on the result."""
    verifier = verifier or DeterministicVerifier()
    research_evidence = task.inputs.get("evidence", ())
    research_summary = task.inputs.get("summary", "")

    probe = Task(
        task_id=task.task_id,
        plan_id=task.plan_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assigned_agent=task.assigned_agent,
        skill="corpus-research",
        outputs={"summary": research_summary},
        evidence=_as_evidence_tuple(research_evidence),
    )
    result = verifier.verify(probe)
    if result.passed:
        return AgentResult(
            status="completed",
            summary="Verification passed.",
            metrics={"verdict": "pass", "reasons": list(result.reasons)},
            next_action="finalize",
        )
    ctx.emit_tool_event(
        EventType.VERIFICATION_FAILED,
        task,
        agent=agent.id,
        payload={"reasons": list(result.reasons)},
    )
    return AgentResult(
        status="failed",
        summary="Verification failed.",
        metrics={"verdict": "fail", "reasons": list(result.reasons)},
        error="; ".join(result.reasons),
    )


def default_work_dispatch(verifier: DeterministicVerifier | None = None):
    """Build a dispatcher that routes a task to its role's work function."""
    verifier = verifier or DeterministicVerifier()

    def dispatch(
        agent: Agent, task: Task, policy: PolicyEngine, ctx: ExecutorContext
    ) -> AgentResult:
        if task.skill == "corpus-research":
            return research_work(agent, task, policy, ctx)
        if task.skill == "memory-research":
            return memory_research_work(agent, task, policy, ctx)
        if task.skill == "workout-research":
            return workout_research_work(agent, task, policy, ctx)
        if task.skill == "verification":
            return reviewer_work(agent, task, policy, ctx, verifier)
        raise UnknownSkillError(f"No work function for skill {task.skill}")

    return dispatch


class UnknownSkillError(Exception):
    """Raised by the dispatcher when a task references an unsupported skill."""


def compute_short_hash(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _as_evidence_tuple(value: object) -> tuple[Evidence, ...]:
    if isinstance(value, Evidence):
        return (value,)
    if isinstance(value, (list, tuple)):
        out: list[Evidence] = []
        for item in value:
            if isinstance(item, Evidence):
                out.append(item)
            elif isinstance(item, dict):
                out.append(
                    Evidence(
                        evidence_id=str(item.get("evidence_id", "")),
                        source_type=item.get("source_type"),
                        source=item.get("source"),
                        document_id=item.get("document_id"),
                        excerpt=str(item.get("excerpt", "")),
                    )
                )
        return tuple(out)
    return ()


def _run_tool(
    policy: PolicyEngine,
    agent: Agent,
    task: Task,
    tool: str,
    arguments: dict[str, object],
    risk: str = "low",
) -> tuple[str, object | None, dict[str, object]]:
    """Run one tool through the policy engine, mapping policy outcomes.

    Returns ``(outcome, result, meta)`` where outcome is one of
    ``"ok"`` / ``"denied"`` / ``"approval"``. ``meta`` carries safe, structured
    details (permission, tool, risk, reason) so the executor can record a
    durable approval request without exposing arguments or content.
    """
    try:
        result = policy.execute(agent, tool, arguments)
    except PolicyDenialError as exc:
        return ("denied", None, {"tool": tool, "reason": str(exc)})
    except ApprovalRequiredError as exc:
        permission = policy.check_tool(agent, tool).permission
        return (
            "approval",
            None,
            {
                "tool": tool,
                "permission": permission.value if permission is not None else "unknown",
                "risk": risk,
                "reason": str(exc),
            },
        )
    return ("ok", result, {"tool": tool})


def _clip(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _summarize(task: Task, evidence: list[Evidence]) -> str:
    objective = task.description or task.title
    if evidence:
        return f"Objective: {objective} ({len(evidence)} item(s) reviewed)."
    return f"Objective: {objective}. No corpus evidence found."


def _summarize_memory(task: Task, evidence: list[Evidence]) -> str:
    objective = task.description or task.title
    if evidence:
        return f"Objective: {objective} ({len(evidence)} memory/memories retrieved)."
    return f"Objective: {objective}. No relevant memories found."


def _workout_excerpt(item: dict[str, object]) -> str:
    """Compact, deterministic excerpt of one workout search hit."""
    matched = ", ".join(str(e) for e in item.get("matched_exercises") or [])
    excerpt = (
        f"Workout {item.get('workout_id')} on {item.get('started_at')}: "
        f"{item.get('exercise_count')} exercises, {item.get('set_count')} sets, "
        f"{item.get('total_volume_kg')} kg total volume."
    )
    if matched:
        excerpt += f" Matching movements: {matched}."
    return excerpt


def _summarize_workout(task: Task, evidence: list[Evidence]) -> str:
    objective = task.description or task.title
    if evidence:
        return f"Objective: {objective} ({len(evidence)} workout(s) found)."
    return f"Objective: {objective}. No workout data found."
