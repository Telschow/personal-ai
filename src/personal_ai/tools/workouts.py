"""Policy-gated workout search for the chat agent.

The chat tool registry (:class:`~personal_ai.tools.registry.ToolRegistry`)
has no permission system of its own — authorization lives entirely in the
agents layer. So the ``search_workouts`` chat tool never touches the workout
service directly. Its handler delegates to
:class:`~personal_ai.agents.policy.PolicyEngine.execute` with the researcher
agent, making the enforcement point identical to the execution runtime and
every decision observable through the same audit trail.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools


def build_policy_gated_workout_handler(
    workout_service: object,
) -> Callable[[dict[str, object]], object]:
    """Return a chat handler that runs ``search_workouts`` only through policy.

    The handler closes over a :class:`PolicyEngine` wired with the researcher's
    read-only ``workout.read`` grant. ``RESEARCHER`` is the only agent the
    chat path may impersonate, and ``workout.read`` is granted with no
    approval requirement, so every chat call observes a recorded ALLOWED
    decision (or a denial) through the same code path the execution runtime
    uses. No workout data is ever accessed outside the policy engine.
    """
    tools = build_default_agent_tools(workout_service=workout_service)
    policy = PolicyEngine(
        tools,
        build_default_agent_registry(),
        build_default_skill_registry(),
    )

    def handle(arguments: dict[str, object]) -> object:
        return policy.execute(RESEARCHER, "search_workouts", arguments)

    return handle
