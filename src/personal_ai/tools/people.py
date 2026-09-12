"""Policy-gated read-only people/identity tools for the chat agent.

Exposes the Phase 50 agent-layer people tools (``search_people`` and
``get_person``) to the interactive chat registry under the new ``people.read``
permission. Like every document/memory chat surface, authorization lives
entirely in the agents layer, so these chat handlers never touch the people
store directly: each handler delegates to
:class:`~personal_ai.agents.policy.PolicyEngine.execute` impersonating the
researcher agent — the only agent the chat path may impersonate — making the
enforcement point identical to the execution runtime and every decision
observable through the same audit trail.

Safety invariants:

* Read-only: ``people.read`` is a read permission granted with no approval
  requirement; nothing here grants, denies, or requires approval, and the
  tools never write. ``PROPOSE_MEMORY``-style write routes stay out of scope.
* Differentiated access is preserved: only the researcher reaches the store;
  curator/engineer policies still deny ``people.read``, so chat can never
  impersonate a higher-privilege identity to read people data.
* No write surface: the handlers bind only the read people tools and their
  read dependency (the person store).
"""

from __future__ import annotations

from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools


class PolicyGatedPeople:
    """Two read-only, policy-gated handlers for the chat people tools.

    Holding both handlers lets :func:`build_policy_gated_people_handler`
    construct a single :class:`PolicyEngine` and audit trail shared by the
    ``search_people`` and ``get_person`` tools. The engine binds only the
    Phase 50 people tools, so a build without a person store exposes no people
    tool.
    """

    def __init__(self, person_store: object) -> None:
        tools = build_default_agent_tools(person_store=person_store)
        policy = PolicyEngine(
            tools,
            build_default_agent_registry(),
            build_default_skill_registry(),
        )
        self._policy = policy

    def search_people(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "search_people", arguments)

    def get_person(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "get_person", arguments)


def build_policy_gated_people_handler(person_store: object) -> PolicyGatedPeople:
    """Return chat handlers that run the people tools through policy.

    ``RESEARCHER`` is the only agent the chat path may impersonate, and the
    read-only ``people.read`` permission is granted with no approval
    requirement, so every chat call observes a recorded ALLOWED decision (or a
    denial) through the same code path the execution runtime uses. No people
    data is ever accessed outside the policy engine.
    """
    return PolicyGatedPeople(person_store)
