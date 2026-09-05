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
* :mod:`corpus` — deterministic corpus-layer candidate extraction.
* :mod:`conversations` — deterministic conversation-layer candidate
  extraction (ChatGPT/Gemini exports).
* :mod:`proposals` — bounded LLM-assisted candidate proposals; the LLM is a
  proposal generator only and every write still passes the deterministic
  policy-gated curator.
* :mod:`curation` — durable, resumable full-corpus curation; every candidate
  still flows through the same policy-gated write path, and checkpoints and
  reports are content-free (review queue excluded).
* :mod:`review` — exception-only human review of escalated candidates
  (``require_approval``/``conflict``); approval reuses the same policy-gated
  write path, rejection writes nothing.
* :mod:`orchestration` — bounded, resumable corpus-wide curation
  (``curate-all``) sequencing every source adapter through the same gate.

The :class:`ControlPlane` (in :mod:`personal_ai.execution`) exposes memory
operations through this package; agents never touch SQLite directly.
"""

from personal_ai.memory.adapters import (
    ACTIVITY_EXTRACTOR_VERSION,
    CHROME_HISTORY_EVENT_SOURCE,
    DOCUMENT_EXTRACTOR_VERSION,
    DOCUMENT_PROPOSAL_PROMPT,
    DOCUMENT_PROPOSAL_SCHEMA,
    GENERIC_DOCUMENT_SOURCE,
    WORKOUT_EXTRACTOR_VERSION,
    DocumentCurationAdapter,
    DocumentProposal,
    DocumentProposalBatch,
    DocumentProposalExtractor,
    EventCurationAdapter,
    WorkoutCurationAdapter,
    to_document_candidate,
)
from personal_ai.memory.chat import (
    ChatMemory,
    ChatMemoryResult,
    derive_chat_scopes,
)
from personal_ai.memory.context import (
    MemoryContext,
    render_untrusted_memory_context,
)
from personal_ai.memory.conversations import (
    CONVERSATION_SOURCE_TYPES,
    ConversationMemoryExtractor,
    ConversationMemoryIngestor,
    ConversationMemoryReport,
    ConversationSourceError,
)
from personal_ai.memory.corpus import (
    ACTIVITY,
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_RECORDS,
    EMAIL,
    FINANCIAL,
    WORKOUTS,
    CorpusIngestionReport,
    CorpusMemoryIngestor,
    CorpusSourceError,
    DomainStats,
    OutcomeTally,
    extract_activity_patterns,
    extract_workout_routine,
)
from personal_ai.memory.curation import (
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_UNIT_TIMEOUT_SECONDS,
    ConversationCurationAdapter,
    CurationConfig,
    CurationConfigError,
    CurationError,
    CurationExtractionMode,
    CurationReport,
    CurationResumeError,
    CurationSourceError,
    CurationStore,
    DryRunTally,
    MemoryCurationRunner,
    RunCounters,
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
from personal_ai.memory.orchestration import (
    DEFAULT_CORPUS_SOURCES,
    DEFAULT_ORCHESTRATION_LIMIT,
    DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS,
    CorpusCurationConfig,
    CorpusCurationOrchestrator,
    CorpusCurationReport,
)
from personal_ai.memory.proposals import (
    LLMConversationMemoryIngestor,
    LLMConversationMemoryReport,
    LLMMemoryProposalExtractor,
    MalformedMemoryProposalError,
    MemoryProposal,
    MemoryProposalBatch,
    MemoryProposalError,
    parse_memory_proposal_batch,
    to_memory_candidate,
)
from personal_ai.memory.retriever import (
    MemoryHit,
    MemoryRetriever,
    MemorySearchError,
    ScopeFilter,
    tokenize,
)
from personal_ai.memory.review import MemoryReviewService
from personal_ai.memory.service import (
    MemoryNotConfiguredError,
    MemoryService,
)
from personal_ai.memory.store import MemoryNotFoundError, MemoryStore, open_memory_store

__all__ = [
    "ACTIVITY",
    "ACTIVITY_EXTRACTOR_VERSION",
    "CHROME_HISTORY_EVENT_SOURCE",
    "CONVERSATION_SOURCE_TYPES",
    "DEFAULT_CORPUS_SOURCES",
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_ORCHESTRATION_LIMIT",
    "DEFAULT_ORCHESTRATION_MAX_MODEL_CALLS",
    "DEFAULT_UNIT_TIMEOUT_SECONDS",
    "DOCUMENT_EXTRACTOR_VERSION",
    "DOCUMENT_PROPOSAL_PROMPT",
    "DOCUMENT_PROPOSAL_SCHEMA",
    "EMAIL",
    "FINANCIAL",
    "GENERIC_DOCUMENT_SOURCE",
    "WORKOUTS",
    "WORKOUT_EXTRACTOR_VERSION",
    "ChatMemory",
    "ChatMemoryResult",
    "ConversationCurationAdapter",
    "ConversationMemoryExtractor",
    "ConversationMemoryIngestor",
    "ConversationMemoryReport",
    "ConversationSourceError",
    "CorpusCurationConfig",
    "CorpusCurationOrchestrator",
    "CorpusCurationReport",
    "CorpusIngestionReport",
    "CorpusMemoryIngestor",
    "CorpusSourceError",
    "CurationConfig",
    "CurationConfigError",
    "CurationError",
    "CurationExtractionMode",
    "CurationReport",
    "CurationResumeError",
    "CurationSourceError",
    "CurationStore",
    "DocumentCurationAdapter",
    "DocumentProposal",
    "DocumentProposalBatch",
    "DocumentProposalExtractor",
    "DomainStats",
    "DryRunTally",
    "EventCurationAdapter",
    "LLMConversationMemoryIngestor",
    "LLMConversationMemoryReport",
    "LLMMemoryProposalExtractor",
    "MalformedMemoryProposalError",
    "Memory",
    "MemoryContext",
    "MemoryCurationRunner",
    "MemoryDraft",
    "MemoryEventType",
    "MemoryHit",
    "MemoryKind",
    "MemoryNotConfiguredError",
    "MemoryNotFoundError",
    "MemoryProposal",
    "MemoryProposalBatch",
    "MemoryProposalError",
    "MemoryRetriever",
    "MemoryReviewService",
    "MemoryScope",
    "MemorySearchError",
    "MemoryService",
    "MemorySourceType",
    "MemoryStatus",
    "MemoryStore",
    "MemoryValidationError",
    "OutcomeTally",
    "RunCounters",
    "ScopeFilter",
    "WorkoutCurationAdapter",
    "derive_chat_scopes",
    "extract_activity_patterns",
    "extract_workout_routine",
    "new_memory_id",
    "now_iso",
    "open_memory_store",
    "parse_iso",
    "parse_memory_proposal_batch",
    "render_untrusted_memory_context",
    "to_document_candidate",
    "to_memory_candidate",
    "tokenize",
    "validate_memory",
]
