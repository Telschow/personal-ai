"""Policy-gated read-only fetch tools for the chat agent.

Phase 32 exposes the Phase 31 agent-layer fetch tools (``get_document`` and
``get_memory``) to the interactive chat registry. Like every document/memory
chat surface, authorization lives entirely in the agents layer, so these chat
handlers never touch the stores/services directly. Each handler delegates to
:class:`~personal_ai.agents.policy.PolicyEngine.execute` impersonating the
researcher agent — the only agent the chat path may impersonate — making the
enforcement point identical to the execution runtime and every decision
observable through the same audit trail, exactly like
:mod:`~personal_ai.tools.corpus`, :mod:`~personal_ai.tools.workouts`, and
:mod:`~personal_ai.tools.personal_context`.

Safety invariants:

* Read-only: ``get_document`` (``corpus.search``) and ``get_memory``
  (``memory.read``) are the existing read permissions; nothing here grants,
  denies, or requires approval.
* Phase 31 differentiated access is preserved: the researcher is the only
  identity reachable from chat, and the same policy gates apply as in the
  execution runtime (curator/engineers cannot fetch documents through the
  policy path; only researcher/corpus agents reach the services).
* No write surface: the handlers bind only the read tools and their read
  dependencies; ``propose_memory`` and every automatic curation path stay out
  of scope.
"""

from __future__ import annotations

from personal_ai.agents.defs import (
    RESEARCHER,
    build_default_agent_registry,
)
from personal_ai.agents.policy import PolicyEngine
from personal_ai.agents.skills import build_default_skill_registry
from personal_ai.agents.tools import build_default_agent_tools


class PolicyGatedGetter:
    """Two read-only, policy-gated handlers for the chat fetch tools.

    Holding both handlers lets :func:`build_policy_gated_get_handler`
    construct a single :class:`PolicyEngine` and audit trail shared by the
    ``get_document`` and ``get_memory`` tools. The engine binds only the
    Phase 31 fetch tools (GET_DOCUMENT requires a document store AND a chunk
    store; GET_MEMORY requires a memory service), so a build without a
    dependency simply does not expose the corresponding fetch tool.
    """

    def __init__(
        self,
        document_store: object | None,
        chunk_store: object | None,
        memory_service: object | None,
    ) -> None:
        tools = build_default_agent_tools(
            document_store=document_store,
            chunk_store=chunk_store,
            memory_service=memory_service,
        )
        policy = PolicyEngine(
            tools,
            build_default_agent_registry(),
            build_default_skill_registry(),
        )
        self._policy = policy

    def get_document(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "get_document", arguments)

    def get_memory(self, arguments: dict[str, object]) -> object:
        return self._policy.execute(RESEARCHER, "get_memory", arguments)


def build_policy_gated_get_handler(
    document_store: object | None = None,
    chunk_store: object | None = None,
    memory_service: object | None = None,
) -> PolicyGatedGetter:
    """Return chat handlers that run ``get_document``/``get_memory`` through policy.

    ``RESEARCHER`` is the only agent the chat path may impersonate, and the
    read-only ``corpus.search`` / ``memory.read`` permissions are granted with
    no approval requirement, so every chat call observes a recorded ALLOWED
    decision (or a denial) through the same code path the execution runtime
    uses. No document or memory data is ever accessed outside the policy
    engine.
    """
    return PolicyGatedGetter(document_store, chunk_store, memory_service)
