"""Private AI memory layer.

Phase 40: durable local memory that sits *beside* execution under the
control plane. Memory is not the corpus, not execution state, not evidence,
and not policy. Everything here is local + deterministic + SQLite-backed; no
vector database and no model/network dependency.

Public surface:

* :mod:`models` — the memory domain model, validation, and JSON contract.
* :mod:`store` — SQLite persistence (memories + safe memory events).
* :mod:`retriever` — deterministic lexical, scope-aware retrieval.
* :mod:`service` — the :class:`MemoryService` boundary (no SQL in agents).
* :mod:`context` — explicit untrusted :class:`MemoryContext` adapter.
* :mod:`chat` — bounded, deterministic automatic chat recall
  (:class:`ChatMemory`), the application-side complement to the
  policy-gated ``search_memory`` agent tool.

The :class:`ControlPlane` (in :mod:`personal_ai.execution`) exposes memory
operations through this package; agents never touch SQLite directly.
"""

from personal_ai.memory.chat import (
    ChatMemory,
    ChatMemoryResult,
    derive_chat_scopes,
)
from personal_ai.memory.context import (
    MemoryContext,
    render_untrusted_memory_context,
)
from personal_ai.memory.models import (
    Memory,
    MemoryDraft,
    MemoryEventType,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryValidationError,
    new_memory_id,
    now_iso,
    parse_iso,
    validate_memory,
)
from personal_ai.memory.retriever import (
    MemoryHit,
    MemoryRetriever,
    MemorySearchError,
    ScopeFilter,
    tokenize,
)
from personal_ai.memory.service import (
    MemoryNotConfiguredError,
    MemoryService,
)
from personal_ai.memory.store import MemoryNotFoundError, MemoryStore, open_memory_store

__all__ = [
    "ChatMemory",
    "ChatMemoryResult",
    "Memory",
    "MemoryContext",
    "MemoryDraft",
    "MemoryEventType",
    "MemoryHit",
    "MemoryKind",
    "MemoryNotConfiguredError",
    "MemoryNotFoundError",
    "MemoryRetriever",
    "MemoryScope",
    "MemorySearchError",
    "MemoryService",
    "MemorySourceType",
    "MemoryStatus",
    "MemoryStore",
    "MemoryValidationError",
    "ScopeFilter",
    "derive_chat_scopes",
    "new_memory_id",
    "now_iso",
    "open_memory_store",
    "parse_iso",
    "render_untrusted_memory_context",
    "tokenize",
    "validate_memory",
]
