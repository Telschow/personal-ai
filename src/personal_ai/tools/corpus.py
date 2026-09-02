"""Policy-gated document / knowledge search for the chat agent.

The chat tool registry (:class:`~personal_ai.tools.registry.ToolRegistry`)
has no permission system of its own — authorization lives entirely in the
agents layer. So the ``search_documents`` and ``search_knowledge`` chat tools
never touch the document/retrieval services directly. Their handlers delegate
to :class:`~personal_ai.agents.policy.PolicyEngine.execute` with the
researcher agent, making the enforcement point identical to the execution
runtime and every decision observable through the same audit trail, exactly
like the workout and personal-context handlers.
"""

from __future__ import annotations

from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools


class PolicyGatedCorpus:
    """Two read-only, policy-gated handlers for the chat document tools.

    Holding both handlers lets :func:`build_policy_gated_corpus_handler`
    construct a single :class:`PolicyEngine` and audit trail shared by the
    narrow ``search_documents`` and the broad ``search_knowledge`` tools.
    """

    def __init__(self, retrieval_service: object, chunk_store: object) -> None:
        tools = build_default_agent_tools(
            retrieval_service=retrieval_service,
            chunk_store=chunk_store,
        )
        policy = PolicyEngine(
            tools,
            build_default_agent_registry(),
            build_default_skill_registry(),
        )
        self._policy = policy
        self._chunk_store = chunk_store
        self._retrieval_service = retrieval_service

    def search_documents(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "search_documents", arguments)

    def search_knowledge(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "search_knowledge", arguments)


def build_policy_gated_corpus_handler(
    retrieval_service: object,
    chunk_store: object,
) -> PolicyGatedCorpus:
    """Return chat handlers that run document retrieval only through policy.

    ``RESEARCHER`` is the only agent the chat path may impersonate, and the
    read-only ``corpus.search`` permission is granted with no approval
    requirement, so every chat call observes a recorded ALLOWED decision (or
    a denial) through the same code path the execution runtime uses. No
    document data is ever accessed outside the policy engine.
    """
    return PolicyGatedCorpus(retrieval_service, chunk_store)
