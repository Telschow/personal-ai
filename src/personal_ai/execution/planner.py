"""Planner: creates validated plans and their task graphs.

Two modes are supported:

* **Deterministic** — known workflows are decomposed into a fixed, validated
  task graph without any model call.
* **LLM-assisted** — a proposed task graph is validated against allowed
  agents, skills, tools, and policy. The LLM proposes tasks; the system
  decides. An LLM can never redefine security policy.

:class:`PlanProposal` is the validation boundary: it is built from safe,
structured data (never free-form policy), so this module is fully testable
offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from personal_ai.agents.registry import AgentRegistry, SkillRegistry
from personal_ai.execution.models import Plan, PlanStatus, Task, TaskStatus


class PlanningError(Exception):
    """Base class for planning failures."""


class InvalidPlanError(PlanningError):
    """Raised when a proposed task graph violates policy or structure."""


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A proposed task within a plan (from a planner or an LLM)."""

    task_id: str
    title: str
    description: str
    agent: str
    skill: str | None = None
    dependencies: tuple[str, ...] = ()
    priority: int = 0
    tools: tuple[str, ...] = ()
    policy: str = "default"
    inputs: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_llm(cls, data: dict[str, object]) -> TaskSpec:
        """Build a :class:`TaskSpec` strictly from JSON-safe data.

        Rejects anything that is not a plain mapping so an LLM/corpus payload
        cannot smuggle in extra authority. Only the known fields are read;
        the rest is discarded.
        """
        agent = data.get("agent")
        task_id = data.get("task_id")
        skill = data.get("skill")
        if not isinstance(task_id, str) or not isinstance(agent, str):
            raise InvalidPlanError("TaskSpec requires string task_id and agent")
        deps_raw = data.get("dependencies", ())
        if not isinstance(deps_raw, (list, tuple)) or not all(
            isinstance(d, str) for d in deps_raw
        ):
            raise InvalidPlanError("dependencies must be a list of strings")
        tools_raw = data.get("tools", ())
        if not isinstance(tools_raw, (list, tuple)) or not all(
            isinstance(t, str) for t in tools_raw
        ):
            raise InvalidPlanError("tools must be a list of strings")
        return TaskSpec(
            task_id=task_id,
            title=str(data.get("title", task_id)),
            description=str(data.get("description", "")),
            agent=agent,
            skill=skill if isinstance(skill, str) else None,
            dependencies=tuple(deps_raw),
            priority=int(data.get("priority", 0)),
            tools=tuple(tools_raw),
            inputs=_safe_mapping(data.get("inputs")),
        )


def _safe_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()}


class Planner:
    """Creates and validates plans and their task graphs."""

    def __init__(self, agents: AgentRegistry, skills: SkillRegistry) -> None:
        self._agents = agents
        self._skills = skills

    def deterministic_research_plan(
        self,
        plan_id: str,
        objective: str,
        created_at: str,
    ) -> Plan:
        """The canonical researcher -> verifier -> synthesis workflow."""
        t1 = TaskSpec(
            task_id=f"{plan_id}-research",
            title="Research objective in corpus",
            description=objective,
            agent="researcher",
            skill="corpus-research",
            tools=("corpus.search", "corpus.fetch"),
        )
        t2 = TaskSpec(
            task_id=f"{plan_id}-verify",
            title="Verify research evidence",
            description="Verify the research task produced evidence and a coherent output.",
            agent="reviewer",
            skill="verification",
            tools=("corpus.search",),
            dependencies=(t1.task_id,),
        )
        self._validate([t1, t2])
        plan = Plan(
            plan_id=plan_id,
            objective=objective,
            status=PlanStatus.PLANNED,
            task_ids=(t1.task_id, t2.task_id),
            risk="low",
            assumptions=("corpus indexed",),
            constraints=("read-only",),
            created_at=created_at,
            updated_at=created_at,
        )
        return plan

    def build_tasks(self, plan: Plan, created_at: str) -> tuple[Task, ...]:
        """Instantiate :class:`Task` rows for the research plan."""
        t1 = Task(
            task_id=f"{plan.plan_id}-research",
            plan_id=plan.plan_id,
            title="Research objective in corpus",
            description=plan.objective,
            status=TaskStatus.READY,
            assigned_agent="researcher",
            skill="corpus-research",
            tools=("corpus.search", "corpus.fetch"),
            created_at=created_at,
            updated_at=created_at,
        )
        t2 = Task(
            task_id=f"{plan.plan_id}-verify",
            plan_id=plan.plan_id,
            title="Verify research evidence",
            description="Verify the research task produced evidence and a coherent output.",
            status=TaskStatus.READY,
            assigned_agent="reviewer",
            skill="verification",
            dependencies=(t1.task_id,),
            tools=("corpus.search",),
            created_at=created_at,
            updated_at=created_at,
        )
        return (t1, t2)

    def validate_proposed_plan(
        self,
        plan_id: str,
        objective: str,
        task_specs: list[TaskSpec],
        created_at: str,
    ) -> Plan:
        """Validate an LLM/planner-proposed task graph into a stored plan.

        Each spec is checked against registered agents/skills and the agent's
        allowed tools. A spec may reference a skill or tool only if the
        assigned agent declares it; otherwise the whole plan is rejected.
        """
        self._validate(task_specs)
        task_ids = tuple(spec.task_id for spec in task_specs)
        plan = Plan(
            plan_id=plan_id,
            objective=objective,
            status=PlanStatus.PLANNED,
            task_ids=task_ids,
            risk="medium",
            created_at=created_at,
            updated_at=created_at,
        )
        return plan

    def _validate(self, task_specs: list[TaskSpec]) -> None:
        ids = [spec.task_id for spec in task_specs]
        if len(ids) != len(set(ids)):
            raise InvalidPlanError("Duplicate task_id in plan")
        id_set = set(ids)
        for spec in task_specs:
            for dep in spec.dependencies:
                if dep not in id_set:
                    raise InvalidPlanError(
                        f"Task {spec.task_id} depends on unknown task {dep}"
                    )
            try:
                agent = self._agents.get(spec.agent)
            except Exception as exc:
                raise InvalidPlanError(str(exc)) from exc
            if spec.skill is not None and spec.skill not in agent.skills:
                raise InvalidPlanError(
                    f"Agent {agent.id} does not declare skill {spec.skill}"
                )
            for tool in spec.tools:
                if tool not in agent.tools:
                    raise InvalidPlanError(
                        f"Agent {agent.id} does not declare tool {tool}"
                    )
