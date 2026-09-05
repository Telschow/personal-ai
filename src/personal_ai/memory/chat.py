"""Automatic, bounded conversational memory recall.

Phase 42: a small application adapter that lets the conversational/chat path
use durable memory as *context* without making memory authoritative. It is
distinct from the explicit, policy-gated ``search_memory`` agent tool
(Phase 41):

* Automatic recall runs in trusted application code before a chat model call.
  It is bounded, deterministic, explicitly scoped, write-free, and requires no
  model call to build the query.
* ``search_memory`` remains the authorized agent capability for explicit,
  deeper discovery during an execution.

Both share the single retrieval source of truth:

    MemoryService -> MemoryRetriever -> MemoryStore

This module never touches SQLite, never writes, and never consults policy.
Memory stays untrusted reference data: it can inform an answer but can never
instruct the model, change permissions, grant approval, or steer model or
agent selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from personal_ai.memory.context import (
    MemoryContext,
    render_untrusted_memory_context,
)
from personal_ai.memory.models import MemoryScope
from personal_ai.memory.retriever import ScopeFilter, tokenize
from personal_ai.ollama_client import ChatMessage

if TYPE_CHECKING:
    from personal_ai.memory.service import MemoryService

# The retrieval query is derived from the current user request only. It is
# deliberately bounded so conversation history is never dumped into search.
MAX_QUERY_CHARS = 200

# Conservative default result size so memory never overwhelms model context.
DEFAULT_MEMORY_LIMIT = 3


def _bounded_query(user_message: str) -> str:
    """Return a small lexical query derived from the current user request."""
    normalized = " ".join(user_message.split())
    return normalized[:MAX_QUERY_CHARS]


def derive_chat_scopes(
    *,
    execution_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[ScopeFilter, ...]:
    """Derive trusted retrieval scopes for automatic chat recall.

    Scopes come only from trusted application/execution context, never from
    model or memory input. Global memories are always eligible (the retriever
    treats an empty scope set as "global only"); execution- and agent-scoped
    memories are only visible when the matching trusted id is supplied.
    Project scope is deliberately unsupported, mirroring Phase 41 semantics.
    """
    scopes: list[ScopeFilter] = []
    if execution_id is not None:
        scopes.append(ScopeFilter(MemoryScope.EXECUTION, execution_id))
    if agent_id is not None:
        scopes.append(ScopeFilter(MemoryScope.AGENT, agent_id))
    return tuple(scopes)


def _last_user_text(messages: Sequence[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


@dataclass(frozen=True, slots=True)
class ChatMemoryResult:
    """Everything the chat layer needs from one automatic recall.

    ``provenance`` carries identifiers and rank/score only — never memory
    content, the retrieval query, or hidden prompt internals.
    """

    context: MemoryContext
    messages: list[ChatMessage]

    @property
    def provenance(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "memory_id": hit.memory.memory_id,
                "kind": hit.memory.kind.value,
                "scope": hit.memory.scope.value,
                "scope_id": hit.memory.scope_id,
                "rank": hit.rank,
                "score": round(hit.score, 3),
            }
            for hit in self.context.memories
        )


class ChatMemory:
    """Application layer: bounded, deterministic automatic chat recall.

    Constructed by the application/CLI (which owns persistence), then handed
    to the chat layer. The chat layer never opens a database and never talks
    to :class:`MemoryStore` directly — this adapter and the
    :class:`MemoryService` behind it stay between them.

    Recall is strictly read-only: it never writes, never records access, never
    creates events, never creates approvals, and never consults policy.
    """

    def __init__(
        self,
        service: MemoryService,
        *,
        limit: int | None = None,
        min_relevance: float | None = None,
    ) -> None:
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        if min_relevance is not None and not 0.0 <= min_relevance <= 1.0:
            msg = f"min_relevance must be in [0.0, 1.0], got {min_relevance}"
            raise ValueError(msg)
        self._service = service
        self._limit = limit if limit is not None else DEFAULT_MEMORY_LIMIT
        self._min_relevance = min_relevance

    @property
    def service(self) -> MemoryService:
        return self._service

    @property
    def limit(self) -> int:
        return self._limit

    def recall(
        self,
        user_message: str,
        *,
        execution_id: str | None = None,
        agent_id: str | None = None,
    ) -> MemoryContext:
        """Retrieve bounded, relevant, in-scope memories for a chat request.

        The query is a bounded lexical slice of the current user message; the
        deterministic retriever orders results. Memories may only be active,
        unexpired, and in scope — archived, deleted, and expired memories are
        never resurrected, and nothing here records access.

        When lexical recall returns nothing for a tokenized query, a bounded
        salient-context fallback returns the most important active in-scope
        memories (relevance 0.0). This keeps explicitly stored, high-importance
        facts reachable from questions that share no tokens with their content
        — for example an identity memory ("preferred name") answering
        "Who am I?". The fallback is bounded (:attr:`limit`), deterministic,
        and never invents content: it reuses the existing empty-query ranking
        (importance/confidence/recency) and stays untrusted reference data.
        """
        query = _bounded_query(user_message)
        scopes = derive_chat_scopes(
            execution_id=execution_id,
            agent_id=agent_id,
        )
        if not query or not tokenize(query):
            return MemoryContext(query=query, memories=(), scopes=scopes)
        hits = self._service.search(query, scopes, limit=self._limit)
        if not hits:
            hits = self._service.search("", scopes, limit=self._limit)
        if self._min_relevance is not None:
            hits = tuple(h for h in hits if h.relevance >= self._min_relevance)
        return MemoryContext(query=query, memories=hits, scopes=scopes)

    def build_context_messages(
        self,
        messages: Sequence[ChatMessage],
        *,
        execution_id: str | None = None,
        agent_id: str | None = None,
    ) -> ChatMemoryResult:
        """Combine automatic recall with untrusted message construction.

        Ordering enforces the instruction hierarchy: trusted system policy
        and the user's own request always precede the memory block, which is
        appended last and explicitly labeled as untrusted reference data.
        The input sequence is not mutated and model/tool routing is untouched.
        """
        user_text = _last_user_text(messages)
        context = self.recall(
            user_text,
            execution_id=execution_id,
            agent_id=agent_id,
        )
        block = render_untrusted_memory_context(context)
        if context.memories:
            extra: list[ChatMessage] = [ChatMessage(role="user", content=block)]
        else:
            extra = []
        return ChatMemoryResult(context=context, messages=[*messages, *extra])
