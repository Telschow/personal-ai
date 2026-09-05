"""Policy-gated durable-memory chat tool for the agent runtime.

Memory *reads* (``search_memory``, automatic chat recall) are untrusted
contextual data and are already wired from the existing ``MemoryService``.
Memory *writes* are the sensitive direction: a model must never be able to
silently persist claims about the user. This module exposes two write paths,
both of which run only through the :class:`~personal_ai.agents.policy.
PolicyEngine` under the curator agent:

* the **explicit** path (``build_policy_gated_memory_proposal_handler``): the
  user explicitly approves every ``propose_memory`` write;
* the **automatic** path (``build_automatic_memory_curator``): the
  deterministic :class:`~personal_ai.memory.policy.MemoryPolicy` decides
  whether a candidate may be accepted, deferred, rejected, or escalated for
  human approval — model metadata is untrusted input, and every write still
  passes the policy engine's ``memory.write`` approval gate.

The curator's ``memory.write`` permission is approval-gated, never
auto-allowed. The automatic path grants the gate only for candidates the
deterministic policy already accepted; nothing here modifies policy, and
authorization lives entirely in the agents layer so every decision is
recorded on the same policy audit trail.

Only policy-accepted candidates reach :class:`MemoryService`; nothing in this
module infers, auto-records, or bypasses the gate.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_ai.agents.defs import CURATOR, build_default_agent_registry
from personal_ai.agents.models import Permission
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import (
    PROPOSE_MEMORY,
    AgentToolRegistry,
    _memory_candidate_handler,
    _memory_write_handler,
)
from personal_ai.memory.models import MemoryCandidate
from personal_ai.memory.policy import (
    MemoryDecision,
    MemoryPolicy,
    MemoryPolicyDecision,
)


def build_policy_gated_memory_proposal_handler(
    memory_service: object,
    approver: object | None = None,
) -> Callable[[dict[str, object]], object]:
    """Return a chat handler that runs ``propose_memory`` only through policy.

    ``approver`` is the user-facing approval callback ``(agent_id, tool_name,
    permission_value) -> bool`` consulted by the policy engine when the
    curator requests ``memory.write`` (which is approval-required in the
    curator policy). With no approver the write is never granted, so a
    handler created this way always raises
    :class:`~personal_ai.agents.policy.ApprovalRequiredError` — the safe
    default. The handler closes over a minimal tool registry containing only
    ``propose_memory``, so no read tool or corpus surface is ever in scope
    for a write decision.
    """
    tools = AgentToolRegistry()
    tools.register(PROPOSE_MEMORY, _memory_write_handler(memory_service))
    policy = PolicyEngine(
        tools,
        build_default_agent_registry(),
        build_default_skill_registry(),
        approver=approver,
    )

    def handle(arguments: dict[str, object]) -> object:
        return policy.execute(CURATOR, "propose_memory", arguments)

    return handle


class AutomaticMemoryCurator:
    """Deterministic-policy memory curation that still honors the write gate.

    ``curate`` classifies a candidate with the deterministic
    :class:`~personal_ai.memory.policy.MemoryPolicy`, then:

    * ``accept`` — the write is executed via ``PolicyEngine.execute(curator,
      "propose_memory", ...)`` with the automatic approver (which re-verifies
      the policy decision for exactly this candidate);
    * ``require_approval`` — executed the same way when an ``interactive
      approver`` is supplied (the existing user-facing approval path);
    * ``reject`` / ``defer`` — never reaches the policy engine; a safe
      summary (identifiers only, never content) is returned.
    """

    def __init__(
        self,
        memory_service: object,
        policy: MemoryPolicy,
        *,
        interactive_approver: Callable[[str, str, str], bool] | None = None,
        auto_approver: Callable[[str, str, str], bool] | None = None,
    ) -> None:
        self._service = memory_service
        self._policy = policy
        self._interactive_approver = interactive_approver
        self._auto_approver = auto_approver

    def evaluate(self, candidate: MemoryCandidate) -> MemoryPolicyDecision:
        return self._policy.evaluate(candidate)

    def curate(self, candidate: MemoryCandidate) -> dict[str, object]:
        """Classify a candidate, writing only policy-accepted outcomes."""
        decision = self._policy.evaluate(candidate)
        if decision.decision is MemoryDecision.ACCEPT:
            approver = self._auto_approver or self._approver_for(candidate)
            return self._write(candidate, decision, approver)
        if (
            decision.decision is MemoryDecision.REQUIRE_APPROVAL
            and self._interactive_approver is not None
        ):
            return self._write(candidate, decision, self._interactive_approver)
        return dict(decision.summary(), applied=False)

    def _write(
        self,
        candidate: MemoryCandidate,
        decision: MemoryPolicyDecision,
        approver: Callable[[str, str, str], bool],
    ) -> dict[str, object]:
        tools = AgentToolRegistry()
        tools.register(PROPOSE_MEMORY, _memory_candidate_handler(self._service))
        engine = PolicyEngine(
            tools,
            build_default_agent_registry(),
            build_default_skill_registry(),
            approver=approver,
        )
        result = engine.execute(CURATOR, "propose_memory", candidate.to_dict())
        if isinstance(result, dict):
            return {**decision.summary(), "applied": True, **result}
        return dict(decision.summary(), applied=True, result=result)

    def _approver_for(
        self, candidate: MemoryCandidate
    ) -> Callable[[str, str, str], bool]:
        """Grant ``memory.write`` for exactly this policy-accepted candidate.

        The gate re-evaluates the candidate against the deterministic policy
        on every call, so an accepted candidate can never write if the policy
        no longer accepts it (deterministic inputs make this a pure guard).
        """

        def approve(agent_id: str, tool_name: str, permission_value: str) -> bool:
            return (
                agent_id == CURATOR.id
                and tool_name == PROPOSE_MEMORY.name
                and permission_value == Permission.MEMORY_WRITE.value
                and self._policy.evaluate(candidate).decision is MemoryDecision.ACCEPT
            )

        return approve


def build_automatic_memory_curator(
    memory_service: object,
    *,
    policy: MemoryPolicy | None = None,
    interactive_approver: Callable[[str, str, str], bool] | None = None,
    auto_approver: Callable[[str, str, str], bool] | None = None,
) -> AutomaticMemoryCurator:
    """Build the automatic memory curator over a memory service.

    ``policy`` defaults to the deterministic :class:`MemoryPolicy`
    (recommended — the policy is deliberately replaceable for tests and
    future tuning, not per requirement to bypass it). ``interactive_approver``
    is an optional human-approval callback used only for candidates the policy
    escalates (``require_approval``). ``auto_approver`` is the write gate for
    automated acceptances; by default it re-verifies the policy decision, and
    overriding it is intended for tests that prove a denied gate blocks the
    write.
    """
    return AutomaticMemoryCurator(
        memory_service,
        policy or MemoryPolicy(),
        interactive_approver=interactive_approver,
        auto_approver=auto_approver,
    )
